import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from uuid import UUID

EXECUTION_SPEC_SCHEMA_VERSION = "s2-poc-v2"
_IDENTITY_PATTERN = re.compile(r"labpilot-poc-[0-9a-f]{64}-[0-9a-f]{32}")


class PhysicalObservationKind(StrEnum):
    ACTIVE_MATCH = "active_match"
    TERMINAL_MATCH = "terminal_match"
    IDENTITY_CONFLICT = "identity_conflict"
    UNKNOWN = "unknown"


class PositiveCreationEvidence(StrEnum):
    FIRST_DISPATCH_PROVEN = "first_dispatch_proven"
    NEVER_ACCEPTED_PROVEN = "never_accepted_proven"


class ControlOutcomeKind(StrEnum):
    CREATED = "created"
    EXISTING_MATCH = "existing_match"
    TERMINAL_MATCH = "terminal_match"
    CONTROL_ACCEPTED = "control_accepted"
    IDENTITY_CONFLICT = "identity_conflict"
    DEFINITE_NON_ACCEPTANCE = "definite_non_acceptance"
    CONTROL_REJECTED = "control_rejected"
    CONTROL_UNKNOWN = "control_unknown"


@dataclass(frozen=True, slots=True)
class FrozenLaunchSpec:
    schema_version: str
    runtime_path: str
    argv: tuple[str, ...]
    working_directory: str
    environment: tuple[tuple[str, str], ...]
    credential_slots: tuple[str, ...]
    launch_profile_version: str
    start_marker_path: str
    sleep_seconds: float
    exit_code: int
    child_sleep_seconds: float
    print_env_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PhysicalObservation:
    kind: PhysicalObservationKind
    execution_identity: str
    expected_spec_hash: str
    observed_spec_hash: str | None = None
    pid: int | None = None
    invocation_id: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    exit_code: int | None = None
    result: str | None = None
    evidence_source: str | None = None


@dataclass(frozen=True, slots=True)
class ControlOutcome:
    kind: ControlOutcomeKind
    execution_identity: str
    expected_spec_hash: str
    detail_code: str


def canonical_execution_spec(spec: FrozenLaunchSpec) -> bytes:
    if spec.schema_version != EXECUTION_SPEC_SCHEMA_VERSION:
        raise ValueError("unsupported_execution_spec_schema")
    if not spec.runtime_path or not Path(spec.runtime_path).is_absolute():
        raise ValueError("runtime_path_must_be_absolute")
    environment_keys = [key for key, _ in spec.environment]
    if len(environment_keys) != len(set(environment_keys)):
        raise ValueError("duplicate_environment_key")

    value = asdict(spec)
    value["environment"] = sorted(spec.environment)
    value["credential_slots"] = sorted(spec.credential_slots)
    value["print_env_keys"] = sorted(spec.print_env_keys)
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def execution_spec_hash(spec: FrozenLaunchSpec) -> str:
    return hashlib.sha256(canonical_execution_spec(spec)).hexdigest()


def supervisor_execution_identity(installation_namespace: str, attempt_id: UUID) -> str:
    namespace_hash = hashlib.sha256(installation_namespace.encode("utf-8")).hexdigest()
    return f"labpilot-poc-{namespace_hash}-{attempt_id.hex}"


def systemd_unit_name(execution_identity: str) -> str:
    if _IDENTITY_PATTERN.fullmatch(execution_identity) is None:
        raise ValueError("invalid_supervisor_execution_identity")
    return f"{execution_identity}.service"


def validate_request_hash(spec: FrozenLaunchSpec, asserted_hash: str) -> None:
    if execution_spec_hash(spec) != asserted_hash:
        raise ValueError("execution_spec_hash_mismatch")
