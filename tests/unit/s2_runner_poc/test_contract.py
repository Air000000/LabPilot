from dataclasses import fields
from pathlib import Path
from uuid import UUID

import pytest
from tests.poc.s2_runner.contract import (
    EXECUTION_SPEC_SCHEMA_VERSION,
    FrozenLaunchSpec,
    canonical_execution_spec,
    execution_spec_hash,
    supervisor_execution_identity,
    systemd_unit_name,
    validate_request_hash,
)

ATTEMPT_ID = UUID("12345678-1234-5678-1234-567812345678")
OTHER_ATTEMPT_ID = UUID("12345678-1234-5678-1234-567812345679")


def _spec(**changes: object) -> FrozenLaunchSpec:
    values: dict[str, object] = {
        "schema_version": EXECUTION_SPEC_SCHEMA_VERSION,
        "runtime_path": "/usr/bin/python3.12",
        "argv": ("-m", "tests.poc.s2_runner.probe"),
        "working_directory": "/opt/labpilot",
        "environment": (("BETA", "2"), ("ALPHA", "1")),
        "credential_slots": ("registry", "dataset"),
        "execution_user": "labpilot-train",
        "start_marker_path": "/var/lib/labpilot/poc/start.jsonl",
        "sleep_seconds": 2.5,
        "exit_code": 17,
        "child_sleep_seconds": 1.0,
        "print_env_keys": ("BETA", "ALPHA"),
        "collect_after_exit": False,
    }
    values.update(changes)
    return FrozenLaunchSpec(**values)  # type: ignore[arg-type]


def test_same_namespace_and_attempt_produce_same_full_execution_identity() -> None:
    first = supervisor_execution_identity("installation-a", ATTEMPT_ID)
    second = supervisor_execution_identity("installation-a", ATTEMPT_ID)

    assert first == second
    assert first == (
        "labpilot-poc-f50c0e431c9af3143deff24d338a9ed8a100b90a31ab4de50ca06eb84268380c-"
        "12345678123456781234567812345678"
    )


def test_different_namespace_changes_execution_identity() -> None:
    assert supervisor_execution_identity(
        "installation-a", ATTEMPT_ID
    ) != supervisor_execution_identity("installation-b", ATTEMPT_ID)


def test_different_attempt_changes_execution_identity() -> None:
    assert supervisor_execution_identity(
        "installation-a", ATTEMPT_ID
    ) != supervisor_execution_identity("installation-a", OTHER_ATTEMPT_ID)


def test_execution_identity_contains_full_uuid_without_truncation() -> None:
    identity = supervisor_execution_identity("installation-a", ATTEMPT_ID)

    assert identity.endswith(ATTEMPT_ID.hex)
    assert len(identity.removeprefix("labpilot-poc-").split("-")) == 2
    assert len(identity.split("-")[-2]) == 64
    assert len(identity.split("-")[-1]) == 32
    assert systemd_unit_name(identity) == f"{identity}.service"


def test_semantically_same_spec_hashes_identically_after_set_order_normalization() -> None:
    first = _spec()
    second = _spec(
        environment=(("ALPHA", "1"), ("BETA", "2")),
        credential_slots=("dataset", "registry"),
        print_env_keys=("ALPHA", "BETA"),
    )

    assert canonical_execution_spec(first) == canonical_execution_spec(second)
    assert execution_spec_hash(first) == execution_spec_hash(second)


@pytest.mark.parametrize(
    ("field_name", "changed_value"),
    [
        ("sleep_seconds", 3.0),
        ("exit_code", 18),
        ("execution_user", "other-user"),
        ("start_marker_path", "/var/lib/labpilot/poc/other.jsonl"),
        ("collect_after_exit", True),
    ],
)
def test_behavior_change_changes_hash(field_name: str, changed_value: object) -> None:
    assert execution_spec_hash(_spec()) != execution_spec_hash(_spec(**{field_name: changed_value}))


def test_runtime_path_change_changes_hash() -> None:
    assert execution_spec_hash(_spec()) != execution_spec_hash(
        _spec(runtime_path="/opt/python/bin/python3.12")
    )


def test_unsupported_schema_version_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported_execution_spec_schema"):
        canonical_execution_spec(_spec(schema_version="s2-poc-v2"))


def test_hash_mismatch_is_rejected_locally() -> None:
    with pytest.raises(ValueError, match="execution_spec_hash_mismatch"):
        validate_request_hash(_spec(), "0" * 64)


def test_duplicate_environment_keys_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate_environment_key"):
        canonical_execution_spec(_spec(environment=(("ALPHA", "1"), ("ALPHA", "2"))))


def test_runtime_path_must_be_absolute_and_non_empty() -> None:
    for runtime_path in ("", "python3.12", str(Path("relative/python"))):
        with pytest.raises(ValueError, match="runtime_path_must_be_absolute"):
            canonical_execution_spec(_spec(runtime_path=runtime_path))


def test_secret_value_field_is_not_part_of_frozen_spec_surface() -> None:
    forbidden = {"secret", "token", "password", "api_key", "authorization"}

    assert not forbidden.intersection(field.name.casefold() for field in fields(FrozenLaunchSpec))
