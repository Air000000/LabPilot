import json
import os
import tempfile
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from uuid import UUID


class DispatchPhase(StrEnum):
    FIRST_DISPATCH_AVAILABLE = "first_dispatch_available"
    DISPATCH_MAY_HAVE_OCCURRED = "dispatch_may_have_occurred"


@dataclass(frozen=True, slots=True)
class DurableIntent:
    attempt_id: UUID
    installation_namespace: str
    execution_identity: str
    execution_spec_hash: str
    dispatch_phase: DispatchPhase


class DurableIntentStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def create(self, intent: DurableIntent) -> None:
        path = self._path(intent.attempt_id)
        if path.exists():
            raise FileExistsError(path)
        self._write(path, intent)

    def load(self, attempt_id: UUID) -> DurableIntent:
        value = json.loads(self._path(attempt_id).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("invalid_durable_intent")
        return DurableIntent(
            attempt_id=UUID(str(value["attempt_id"])),
            installation_namespace=str(value["installation_namespace"]),
            execution_identity=str(value["execution_identity"]),
            execution_spec_hash=str(value["execution_spec_hash"]),
            dispatch_phase=DispatchPhase(str(value["dispatch_phase"])),
        )

    def mark_dispatch_may_have_occurred(self, attempt_id: UUID) -> DurableIntent:
        intent = self.load(attempt_id)
        if intent.dispatch_phase is DispatchPhase.DISPATCH_MAY_HAVE_OCCURRED:
            return intent
        changed = replace(intent, dispatch_phase=DispatchPhase.DISPATCH_MAY_HAVE_OCCURRED)
        self._write(self._path(attempt_id), changed)
        return changed

    def _path(self, attempt_id: UUID) -> Path:
        return self._root / f"{attempt_id.hex}.json"

    def _write(self, path: Path, intent: DurableIntent) -> None:
        value = {
            "attempt_id": str(intent.attempt_id),
            "installation_namespace": intent.installation_namespace,
            "execution_identity": intent.execution_identity,
            "execution_spec_hash": intent.execution_spec_hash,
            "dispatch_phase": intent.dispatch_phase.value,
        }
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self._root,
                prefix=f".{path.name}.",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(
                    value,
                    temporary,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, path)
            directory_fd = os.open(self._root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
