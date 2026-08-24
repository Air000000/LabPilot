from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, TypeVar

FactT = TypeVar("FactT")

EVIDENCE_FILES = {
    "scenario.json",
    "db-before.json",
    "db-after.json",
    "runner-info.json",
    "runner-service-effective.json",
    "observed-environment.json",
    "physical-observations.jsonl",
    "runtime-events.jsonl",
    "systemd-exact-unit.json",
    "journal-exact-unit.jsonl",
    "workload-test-oracle.json",
    "worker-failure.json",
    "verdict.json",
}
_JSONL_FILES = {
    "physical-observations.jsonl",
    "runtime-events.jsonl",
    "journal-exact-unit.jsonl",
}
_SECRET_KEYS = (
    "secret",
    "password",
    "token",
    "credential",
    "authorization",
    "apikey",
    "privatekey",
    "databaseurl",
)
_CREDENTIAL_URL = re.compile(r"^[a-z][a-z0-9+.-]*://[^/@\s:]+:[^/@\s]+@", re.I)
_SCENARIO = re.compile(r"^F(?:0[1-9]|1[0-9]|2[0-2])$", re.ASCII)
_TESTED_SHA = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_REASON_CODE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$", re.ASCII)


class BoundedPollTimeout(TimeoutError):
    def __init__(self, last_observed: object) -> None:
        super().__init__(f"bounded predicate deadline reached; last={last_observed!r}")
        self.last_observed = last_observed


class EvidenceBoundsError(ValueError):
    pass


class EvidenceSecretError(ValueError):
    pass


class OracleViolation(AssertionError):
    pass


@dataclass(frozen=True, slots=True)
class ScenarioVerdict:
    schema_version: int
    scenario: str
    tested_sha: str
    verdict: Literal["PASS", "FAIL", "STOPPED"]
    reason_code: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("scenario verdict schema_version must be integer 1")
        if type(self.scenario) is not str or _SCENARIO.fullmatch(self.scenario) is None:
            raise ValueError("scenario verdict scenario must be F01 through F22")
        if type(self.tested_sha) is not str or _TESTED_SHA.fullmatch(self.tested_sha) is None:
            raise ValueError("scenario verdict tested_sha must be 40 lowercase hex")
        if type(self.verdict) is not str or self.verdict not in {"PASS", "FAIL", "STOPPED"}:
            raise ValueError("scenario verdict must be PASS, FAIL, or STOPPED")
        if (
            type(self.reason_code) is not str
            or len(self.reason_code.encode("ascii", "ignore")) != len(self.reason_code)
            or len(self.reason_code) > 80
            or _REASON_CODE.fullmatch(self.reason_code) is None
        ):
            raise ValueError("scenario verdict reason_code must be bounded lower_snake_case")


@dataclass(frozen=True, slots=True)
class EvidenceLimits:
    max_file_bytes: int = 64 * 1024
    max_records: int = 200
    max_database_rows: int = 200
    max_environment_items: int = 64


def require_capacity_at_most_four(statuses: Iterable[str]) -> None:
    physical = {"launch_pending", "running", "unknown", "identity_conflict"}
    count = sum(status in physical for status in statuses)
    if count > 4:
        raise OracleViolation(f"physical-intent capacity exceeded: {count}")


def require_fifo_dispatch(candidates: Iterable[str], dispatched: Iterable[str]) -> None:
    candidate_order = tuple(candidates)
    dispatch_order = tuple(dispatched)
    if dispatch_order != candidate_order[: len(dispatch_order)]:
        raise OracleViolation("durable FIFO dispatch bypassed an older candidate")


def require_single_start(workload_oracle: Mapping[str, object]) -> None:
    if workload_oracle.get("start_count") != 1:
        raise OracleViolation("workload start_count must be exactly 1")


def require_unknown_slot(attempt: Mapping[str, object]) -> None:
    if not (
        attempt.get("status") == "unknown"
        and attempt.get("terminal_at") is None
        and attempt.get("retry_count") == 0
        and attempt.get("launch_count") == 1
    ):
        raise OracleViolation("UNKNOWN must remain nonterminal and consume its original slot")


def require_identity_conflict(attempt: Mapping[str, object]) -> None:
    if not (
        attempt.get("status") == "identity_conflict"
        and attempt.get("terminal_at") is None
        and attempt.get("stop_count") == 0
        and attempt.get("retry_count") == 0
        and attempt.get("launch_count") == 1
    ):
        raise OracleViolation(
            "IDENTITY_CONFLICT must remain distinct, nonterminal, and non-destructive"
        )


def require_natural_terminal_preserved(
    before_cancel: Mapping[str, object], after_cancel: Mapping[str, object]
) -> None:
    fields = ("terminal_kind", "exit_code", "termination_signal")
    if tuple(before_cancel.get(field) for field in fields) != tuple(
        after_cancel.get(field) for field in fields
    ):
        raise OracleViolation("cancel overwrote natural terminal truth")


def require_startup_barrier_order(
    events: Iterable[str], *, expected_attempts: Iterable[str]
) -> None:
    ordered = tuple(events)
    try:
        barrier = ordered.index("barrier_complete")
    except ValueError as error:
        raise OracleViolation("startup barrier completion is missing") from error
    if any(event.startswith("dispatch:") for event in ordered[:barrier]):
        raise OracleViolation("dispatch occurred before startup barrier completion")
    for attempt_id in expected_attempts:
        required = (f"reconcile:{attempt_id}", f"audit:{attempt_id}")
        if any(event not in ordered[:barrier] for event in required):
            raise OracleViolation("startup barrier omitted reconciliation or audit")
        if ordered.index(required[0]) > ordered.index(required[1]):
            raise OracleViolation("startup barrier audit preceded reconciliation")


def require_runtime_max_termination(properties: Mapping[str, object]) -> None:
    runtime = properties.get("RuntimeMaxUSec")
    if (
        properties.get("Result") != "timeout"
        or type(runtime) is not str
        or not runtime.isdecimal()
        or int(runtime) <= 0
    ):
        raise OracleViolation("real RuntimeMaxSec termination evidence is missing")


def require_observe_before_uow_crash_order(events: Iterable[str]) -> None:
    ordered = tuple(events)
    required = (
        "observe_returned",
        "crash_before_uow_commit",
        "restart",
        "observe_returned",
        "uow_commit",
    )
    cursor = 0
    for event in required:
        try:
            cursor = ordered.index(event, cursor) + 1
        except ValueError as error:
            raise OracleViolation("observe-before-UoW crash ordering is invalid") from error


def poll_until(
    observe: Callable[[], FactT],
    predicate: Callable[[FactT], bool],
    *,
    timeout_seconds: float,
    interval_seconds: float = 0.1,
    monotonic: Callable[[], float] = time.monotonic,
    backoff: Callable[[float], None] = time.sleep,
) -> FactT:
    if timeout_seconds <= 0 or interval_seconds <= 0:
        raise ValueError("poll bounds must be positive")
    deadline = monotonic() + timeout_seconds
    while True:
        fact = observe()
        if predicate(fact):
            return fact
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise BoundedPollTimeout(fact)
        backoff(min(interval_seconds, remaining))


class EvidenceWriter:
    def __init__(self, root: Path, *, limits: EvidenceLimits | None = None) -> None:
        self.root = root
        self.limits = limits or EvidenceLimits()
        self.root.mkdir(parents=True, exist_ok=True)

    def write_json(self, name: str, value: object) -> Path:
        self._require_name(name, jsonl=False)
        if name == "verdict.json":
            raise ValueError("verdict.json must be created through seal_verdict")
        _reject_secrets(value)
        if name in {"db-before.json", "db-after.json"}:
            rows = value.get("rows") if isinstance(value, Mapping) else None
            if not isinstance(rows, list) or len(rows) > self.limits.max_database_rows:
                raise EvidenceBoundsError("database row limit exceeded")
        if name == "observed-environment.json":
            environment = value.get("environment") if isinstance(value, Mapping) else None
            if (
                isinstance(environment, Mapping)
                and len(environment) > self.limits.max_environment_items
            ):
                raise EvidenceBoundsError("environment item limit exceeded")
        self._write(name, _json_bytes(value))
        return self.root / name

    def write_jsonl(self, name: str, values: Iterable[object]) -> Path:
        self._require_name(name, jsonl=True)
        records: list[bytes] = []
        for value in values:
            if len(records) >= self.limits.max_records:
                raise EvidenceBoundsError("record limit exceeded")
            _reject_secrets(value)
            records.append(_json_bytes(value))
        self._write(name, b"".join(record + b"\n" for record in records))
        return self.root / name

    def seal_verdict(self, verdict: ScenarioVerdict) -> str:
        if type(verdict) is not ScenarioVerdict:
            raise TypeError("seal_verdict requires ScenarioVerdict")
        value = asdict(verdict)
        value["sealed"] = True
        value["administrative_cleanup_is_domain_evidence"] = False
        encoded = _json_bytes(value)
        self._publish_no_replace("verdict.json", encoded)
        return hashlib.sha256(encoded).hexdigest()

    def _require_name(self, name: str, *, jsonl: bool) -> None:
        if name not in EVIDENCE_FILES or (name in _JSONL_FILES) is not jsonl:
            raise ValueError("evidence filename is not authorized")

    def _write(self, name: str, encoded: bytes) -> None:
        if len(encoded) > self.limits.max_file_bytes:
            raise EvidenceBoundsError("file byte limit exceeded")
        descriptor, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=self.root)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.root / name)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            Path(temporary).unlink(missing_ok=True)
            raise

    def _publish_no_replace(self, name: str, encoded: bytes) -> None:
        if len(encoded) > self.limits.max_file_bytes:
            raise EvidenceBoundsError("file byte limit exceeded")
        descriptor, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=self.root)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, self.root / name, follow_symlinks=False)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        finally:
            Path(temporary).unlink(missing_ok=True)


def _json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError) as error:
        raise EvidenceBoundsError("evidence is not bounded canonical JSON") from error


def _reject_secrets(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = "".join(character for character in str(key).lower() if character.isalnum())
            if any(secret in normalized for secret in _SECRET_KEYS):
                raise EvidenceSecretError("secret-shaped evidence key rejected")
            _reject_secrets(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _reject_secrets(item)
    elif isinstance(value, str) and (
        value.lower().startswith("bearer ") or _CREDENTIAL_URL.match(value)
    ):
        raise EvidenceSecretError("secret-shaped evidence value rejected")
