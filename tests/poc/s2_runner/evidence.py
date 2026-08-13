import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

_FORBIDDEN_DETAIL_KEYS = {"secret", "token", "password", "api_key", "authorization"}
_MAX_RECORD_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    scenario_id: str
    phase: str
    execution_identity: str | None
    execution_spec_hash: str | None
    expected: str
    observed: str
    detail: dict[str, object]


class EvidenceLog:
    def __init__(self, path: Path) -> None:
        self._path = path

    def append(self, record: EvidenceRecord) -> None:
        _reject_secret_shaped_keys(record.detail)
        encoded = (
            json.dumps(asdict(record), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + "\n"
        ).encode("utf-8")
        if len(encoded) > _MAX_RECORD_BYTES:
            raise ValueError("evidence_record_too_large")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self._path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            if os.write(descriptor, encoded) != len(encoded):
                raise OSError("short_evidence_write")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def read_all(self) -> tuple[EvidenceRecord, ...]:
        if not self._path.exists():
            return ()
        records: list[EvidenceRecord] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if not isinstance(value, dict) or not isinstance(value.get("detail"), dict):
                raise ValueError("invalid_evidence_record")
            detail = {str(key): item for key, item in value["detail"].items()}
            _reject_secret_shaped_keys(detail)
            records.append(
                EvidenceRecord(
                    scenario_id=str(value["scenario_id"]),
                    phase=str(value["phase"]),
                    execution_identity=_optional_string(value.get("execution_identity")),
                    execution_spec_hash=_optional_string(value.get("execution_spec_hash")),
                    expected=str(value["expected"]),
                    observed=str(value["observed"]),
                    detail=detail,
                )
            )
        return tuple(records)


def _reject_secret_shaped_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in _FORBIDDEN_DETAIL_KEYS:
                raise ValueError("secret_shaped_evidence_key")
            _reject_secret_shaped_keys(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _reject_secret_shaped_keys(item)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None
