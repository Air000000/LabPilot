from pathlib import Path
from uuid import UUID

import pytest
from tests.poc.s2_runner.contract import supervisor_execution_identity
from tests.poc.s2_runner.durable_state import (
    DispatchPhase,
    DurableIntent,
    DurableIntentStore,
)

ATTEMPT_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def _intent() -> DurableIntent:
    namespace = "native-host-a"
    return DurableIntent(
        attempt_id=ATTEMPT_ID,
        installation_namespace=namespace,
        execution_identity=supervisor_execution_identity(namespace, ATTEMPT_ID),
        execution_spec_hash="a" * 64,
        dispatch_phase=DispatchPhase.FIRST_DISPATCH_AVAILABLE,
    )


def test_create_round_trips_first_dispatch_intent(tmp_path: Path) -> None:
    store = DurableIntentStore(tmp_path)

    store.create(_intent())

    assert store.load(ATTEMPT_ID) == _intent()


def test_mark_dispatch_may_have_occurred_is_monotonic(tmp_path: Path) -> None:
    store = DurableIntentStore(tmp_path)
    store.create(_intent())

    changed = store.mark_dispatch_may_have_occurred(ATTEMPT_ID)
    unchanged = store.mark_dispatch_may_have_occurred(ATTEMPT_ID)

    assert changed.dispatch_phase is DispatchPhase.DISPATCH_MAY_HAVE_OCCURRED
    assert unchanged == changed
    assert store.load(ATTEMPT_ID) == changed


def test_missing_state_does_not_create_non_acceptance_evidence(tmp_path: Path) -> None:
    store = DurableIntentStore(tmp_path)

    with pytest.raises(FileNotFoundError):
        store.load(ATTEMPT_ID)
    with pytest.raises(FileNotFoundError):
        store.mark_dispatch_may_have_occurred(ATTEMPT_ID)

    assert tuple(tmp_path.glob("*")) == ()


def test_replace_failure_does_not_report_new_phase_as_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = DurableIntentStore(tmp_path)
    store.create(_intent())

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("injected_replace_failure")

    monkeypatch.setattr("tests.poc.s2_runner.durable_state.os.replace", fail_replace)

    with pytest.raises(OSError, match="injected_replace_failure"):
        store.mark_dispatch_may_have_occurred(ATTEMPT_ID)

    assert store.load(ATTEMPT_ID).dispatch_phase is DispatchPhase.FIRST_DISPATCH_AVAILABLE
