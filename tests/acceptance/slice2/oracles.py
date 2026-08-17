from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

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


class BoundedPollTimeout(TimeoutError):
    def __init__(self, last_observed: object) -> None:
        super().__init__(f"bounded predicate deadline reached; last={last_observed!r}")
        self.last_observed = last_observed


class EvidenceBoundsError(ValueError):
    pass


class EvidenceSecretError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EvidenceLimits:
    max_file_bytes: int = 64 * 1024
    max_records: int = 200
    max_database_rows: int = 200
    max_environment_items: int = 64


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
        self._sealed: set[str] = set()

    def write_json(self, name: str, value: object) -> Path:
        self._require_name(name, jsonl=False)
        if name in self._sealed:
            raise RuntimeError(f"{name} is sealed")
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

    def seal_verdict(self, verdict: Mapping[str, object]) -> str:
        value = dict(verdict)
        value["sealed"] = True
        value["administrative_cleanup_is_domain_evidence"] = False
        path = self.write_json("verdict.json", value)
        self._sealed.add("verdict.json")
        return hashlib.sha256(path.read_bytes()).hexdigest()

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
