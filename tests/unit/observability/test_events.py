from datetime import UTC, datetime

import pytest

from labpilot.common.canonical_json import CanonicalizationError
from labpilot.observability.events import RuntimeEventEnvelope, append_runtime_event
from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow
from labpilot.observability.payloads import prepare_runtime_event_payload


class _Session:
    def __init__(self) -> None:
        self.added: list[object] = []

    async def scalar(self, statement: object) -> datetime:
        del statement
        return datetime(2026, 8, 17, tzinfo=UTC)

    def add(self, row: object) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        return None


class _Uow:
    def __init__(self) -> None:
        self.session = _Session()


@pytest.fixture
def fixed_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    async def allocate(*args: object, **kwargs: object) -> int:
        del args, kwargs
        return 1

    monkeypatch.setattr("labpilot.observability.events._allocate_sequence", allocate)


@pytest.mark.asyncio
async def test_append_runtime_event_can_skip_outbox(fixed_sequence: None) -> None:
    del fixed_sequence
    uow = _Uow()

    event = await append_runtime_event(
        uow,  # type: ignore[arg-type]
        RuntimeEventEnvelope.for_test("audit-only"),
        enqueue_outbox=False,
    )

    assert isinstance(event, RuntimeEventRow)
    assert [type(row) for row in uow.session.added] == [RuntimeEventRow]


@pytest.mark.asyncio
async def test_append_runtime_event_keeps_default_outbox(fixed_sequence: None) -> None:
    del fixed_sequence
    uow = _Uow()

    event = await append_runtime_event(
        uow,  # type: ignore[arg-type]
        RuntimeEventEnvelope.for_test("published"),
    )

    assert [type(row) for row in uow.session.added] == [
        RuntimeEventRow,
        OutboxRecordRow,
    ]
    assert uow.session.added[1].event_id == event.event_id  # type: ignore[attr-defined]


def test_attempt_reconciliation_payload_is_bounded_and_registered() -> None:
    payload = {
        "attempt_id": "33333333-3333-4333-8333-333333333333",
        "X": "labpilot-v1-x",
        "H": "a" * 64,
        "coordinator_start_id": "44444444-4444-4444-8444-444444444444",
        "pre_reconciliation_status": "launch_pending",
        "physical_observation_kind": "active_match",
        "post_reconciliation_status": "running",
        "physical_boot_id": "boot-1",
        "supervisor_invocation_id": "invocation-1",
    }

    prepared = prepare_runtime_event_payload(
        event_type="attempt.reconciled_after_restart",
        event_schema_version=1,
        payload=payload,
    )

    assert prepared.persisted_json() == payload
    with pytest.raises(CanonicalizationError, match="unexpected fields"):
        prepare_runtime_event_payload(
            event_type="attempt.reconciled_after_restart",
            event_schema_version=1,
            payload={**payload, "raw_journal": "forbidden"},
        )
