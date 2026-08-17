from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

_SHA = re.compile(r"^[0-9a-f]{40}$")


class ScenarioSelectionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    name: str
    title: str


_TITLES = (
    "happy vertical and observed environment",
    "exact source and prelaunch tamper",
    "physical capacity and durable FIFO",
    "durable start while coordinator is down",
    "crash after launch intent before runner call",
    "launch acknowledgement loss",
    "coordinator kill while workload runs",
    "workload exit while coordinator is down",
    "database outage while workload runs",
    "cancel persisted while coordinator is down",
    "control-group cancellation",
    "cancel versus natural exit",
    "stop acknowledgement loss",
    "observation outage beyond grace",
    "real wrong-H identity conflict",
    "unknown retains physical capacity",
    "mixed startup reconciliation barrier",
    "real host reboot",
    "API restart while training runs",
    "definite pre-send non-acceptance",
    "real RuntimeMaxSec",
    "crash after observation before commit",
)
SCENARIOS = {
    f"F{number:02d}": ScenarioDefinition(f"F{number:02d}", title)
    for number, title in enumerate(_TITLES, 1)
}


def select_scenarios(value: str) -> tuple[ScenarioDefinition, ...]:
    names = value.split(",") if value else []
    if not names or len(names) != len(set(names)):
        raise ScenarioSelectionError("scenario selection must be non-empty and unique")
    unknown = [name for name in names if name not in SCENARIOS]
    if unknown:
        raise ScenarioSelectionError(f"unknown scenario: {','.join(unknown)}")
    return tuple(SCENARIOS[name] for name in names)


@dataclass(frozen=True, slots=True)
class ScenarioState:
    scenario: str
    tested_commit: str
    installation_identity: str
    phase: str = "run"

    @classmethod
    def create(
        cls,
        *,
        scenario: str,
        tested_commit: str,
        installation_seed: str,
    ) -> ScenarioState:
        if scenario not in SCENARIOS or _SHA.fullmatch(tested_commit) is None:
            raise ValueError("invalid scenario state")
        digest = hashlib.sha256(
            f"labpilot-slice2-task5:v1\0{scenario}\0{tested_commit}\0{installation_seed}".encode()
        ).hexdigest()
        return cls(scenario, tested_commit, f"slice2-task5:{digest}")

    def for_phase(self, phase: str) -> ScenarioState:
        if phase not in {"run", "prepare", "verify"}:
            raise ValueError("invalid scenario phase")
        return replace(self, phase=phase)

    def save(self, path: Path) -> None:
        _write_json(path, asdict(self))

    @classmethod
    def load(cls, path: Path) -> ScenarioState:
        return cls(**_read_json(path))


@dataclass(frozen=True, slots=True)
class F18Metadata:
    scenario: Literal["F18"]
    tested_commit: str
    installation_identity: str
    database_identity: str
    database_location: str
    attempt_id: str
    execution_identity: str
    execution_spec_hash: str
    pre_reboot_status: str
    boot_id: str
    runner_policy_identity: str

    def save(self, path: Path) -> None:
        _write_json(path, asdict(self))

    @classmethod
    def load(cls, path: Path) -> F18Metadata:
        value = _read_json(path)
        if value.get("scenario") != "F18":
            raise ValueError("F18 metadata scenario mismatch")
        return cls(
            scenario="F18",
            tested_commit=value["tested_commit"],
            installation_identity=value["installation_identity"],
            database_identity=value["database_identity"],
            database_location=value["database_location"],
            attempt_id=value["attempt_id"],
            execution_identity=value["execution_identity"],
            execution_spec_hash=value["execution_spec_hash"],
            pre_reboot_status=value["pre_reboot_status"],
            boot_id=value["boot_id"],
            runner_policy_identity=value["runner_policy_identity"],
        )


def administrative_cleanup_record(action: str) -> dict[str, object]:
    if not action or len(action.encode("utf-8")) > 500:
        raise ValueError("cleanup action must be bounded")
    return {
        "record_type": "test_admin_cleanup",
        "action": action,
        "recorded_at": datetime.now(UTC).isoformat(),
        "domain_terminal_evidence": False,
    }


def run_native_request(request: object) -> None:
    from tests.acceptance.slice2.native_preflight import redact_evidence, run_preflight_request

    if not isinstance(request, dict):
        raise RuntimeError(f"native request must be a mapping, got {type(request).__name__}")
    facts = run_preflight_request(request)
    if request.get("phase") == "preflight":
        print(json.dumps(redact_evidence(facts), separators=(",", ":"), sort_keys=True))
        return
    # ponytail: scenario control is added only when its concrete native proof is implemented.
    raise RuntimeError("native scenario drivers are not bound")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError("invalid scenario metadata")
    return value
