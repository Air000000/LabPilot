from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

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


@dataclass(frozen=True, slots=True)
class ScenarioProcedure:
    scenario: str
    operations: tuple[str, ...]
    obligations: tuple[str, ...]
    phase: str = "run"


class ScenarioRuntime(Protocol):
    def execute(self, procedure: ScenarioProcedure) -> None: ...


_PROCEDURES = {
    "F01": ScenarioProcedure(
        "F01",
        ("create_real_batch", "start", "dispatch", "observe_terminal"),
        ("exact_source", "observed_environment", "exit_zero", "succeeded"),
    ),
    "F02": ScenarioProcedure(
        "F02",
        (
            "capture_exact_source",
            "dirty_worktree",
            "prepare",
            "tamper_prelaunch",
            "attempt_dispatch",
        ),
        ("dirty_bytes_not_executed", "tamper_fails_closed"),
    ),
    "F03": ScenarioProcedure(
        "F03",
        ("create_six_trials", "start", "scan_until_capacity"),
        ("physical_intent_at_most_four", "fifo_no_bypass"),
    ),
    "F04": ScenarioProcedure(
        "F04",
        ("stop_coordinator", "persist_start", "start_coordinator", "scan"),
        ("zero_attempt_while_down", "single_normal_dispatch"),
    ),
    "F05": ScenarioProcedure(
        "F05",
        (
            "commit_launch_pending",
            "crash_before_runner",
            "restart_coordinator",
            "startup_reconcile",
        ),
        ("no_fabricated_acceptance", "no_same_attempt_launch"),
    ),
    "F06": ScenarioProcedure(
        "F06",
        ("delegate_real_launch", "drop_launch_response", "restart_coordinator", "observe"),
        ("same_attempt_never_relaunched", "start_count_one"),
    ),
    "F07": ScenarioProcedure(
        "F07",
        (
            "launch_active",
            "sigkill_coordinator",
            "verify_workload_survives",
            "restart_coordinator",
            "startup_reconcile",
        ),
        ("same_attempt", "same_x", "same_h", "start_count_one"),
    ),
    "F08": ScenarioProcedure(
        "F08",
        (
            "launch_active",
            "stop_coordinator",
            "wait_real_exit",
            "start_coordinator",
            "startup_reconcile",
        ),
        ("real_terminal_result", "no_relaunch"),
    ),
    "F09": ScenarioProcedure(
        "F09",
        (
            "launch_active",
            "stop_postgres",
            "verify_workload_survives",
            "start_postgres",
            "reconcile",
        ),
        ("no_workload_restart", "database_recovery_converges"),
    ),
    "F10": ScenarioProcedure(
        "F10",
        (
            "launch_active",
            "stop_coordinator",
            "persist_cancel",
            "start_coordinator",
            "fresh_observe",
            "stop_exact_match",
        ),
        ("cancel_durable", "active_match_before_stop"),
    ),
    "F11": ScenarioProcedure(
        "F11",
        ("launch_with_child", "persist_cancel", "scan_stop", "observe_terminal"),
        ("control_group_empty", "no_orphan_child"),
    ),
    "F12": ScenarioProcedure(
        "F12",
        ("launch_short_workload", "race_cancel_with_exit", "observe_terminal"),
        ("natural_terminal_truth_preserved",),
    ),
    "F13": ScenarioProcedure(
        "F13",
        (
            "launch_active",
            "persist_cancel",
            "delegate_real_stop",
            "drop_stop_response",
            "observe_terminal",
        ),
        ("no_direct_terminalization", "reobservation_converges"),
    ),
    "F14": ScenarioProcedure(
        "F14",
        (
            "launch_active",
            "cause_real_observation_outage",
            "poll_beyond_unknown_grace",
            "seal_verdict",
            "admin_cleanup",
        ),
        ("domain_unknown", "terminal_null", "slot_consumed", "no_retry", "no_relaunch"),
    ),
    "F15": ScenarioProcedure(
        "F15",
        ("create_real_wrong_h_unit", "observe_conflict", "seal_verdict", "admin_cleanup"),
        (
            "identity_conflict_distinct",
            "terminal_null",
            "slot_consumed",
            "no_stop",
            "no_retry",
            "no_relaunch",
        ),
    ),
    "F16": ScenarioProcedure(
        "F16",
        ("establish_unknown", "fill_remaining_slots", "queue_excess", "scan"),
        ("unknown_consumes_slot", "maximum_four"),
    ),
    "F17": ScenarioProcedure(
        "F17",
        ("prepare_mixed_recovery_set", "restart_coordinator", "capture_order"),
        ("all_existing_reconciled_and_audited", "barrier_before_dispatch"),
    ),
    "F18": ScenarioProcedure(
        "F18",
        ("persist_pre_reboot_identity", "verify_new_boot", "observe_old_x"),
        ("different_boot_id", "no_same_x_resurrection", "allowed_post_reboot_truth"),
    ),
    "F19": ScenarioProcedure(
        "F19",
        ("launch_active", "kill_api", "verify_workload_survives", "restart_api", "query_attempt"),
        ("same_attempt", "no_relaunch"),
    ),
    "F20": ScenarioProcedure(
        "F20",
        ("commit_launch_pending", "stop_runner_before_connect", "attempt_dispatch"),
        ("pre_send_nonacceptance", "launch_not_accepted", "no_unit_for_x"),
    ),
    "F21": ScenarioProcedure(
        "F21",
        ("launch_beyond_timeout", "observe_systemd_timeout", "reconcile_terminal"),
        ("runtime_max_sec_evidence", "no_harness_timeout_sleep", "no_relaunch"),
    ),
    "F22": ScenarioProcedure(
        "F22",
        (
            "launch_active",
            "delegate_real_observe",
            "crash_before_uow_commit",
            "restart_coordinator",
            "repeat_observe",
        ),
        ("original_attempt_converges", "restart_audit_immutable"),
    ),
}


def _execute(name: str, runtime: ScenarioRuntime, phase: str) -> None:
    if name == "F18":
        if phase not in {"prepare", "verify"}:
            raise ValueError("F18 requires prepare or verify phase")
    elif phase != "run":
        raise ValueError(f"{name} supports only run phase")
    runtime.execute(replace(_PROCEDURES[name], phase=phase))


def run_f01(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F01", runtime, phase)


def run_f02(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F02", runtime, phase)


def run_f03(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F03", runtime, phase)


def run_f04(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F04", runtime, phase)


def run_f05(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F05", runtime, phase)


def run_f06(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F06", runtime, phase)


def run_f07(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F07", runtime, phase)


def run_f08(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F08", runtime, phase)


def run_f09(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F09", runtime, phase)


def run_f10(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F10", runtime, phase)


def run_f11(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F11", runtime, phase)


def run_f12(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F12", runtime, phase)


def run_f13(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F13", runtime, phase)


def run_f14(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F14", runtime, phase)


def run_f15(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F15", runtime, phase)


def run_f16(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F16", runtime, phase)


def run_f17(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F17", runtime, phase)


def run_f18(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F18", runtime, phase)


def run_f19(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F19", runtime, phase)


def run_f20(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F20", runtime, phase)


def run_f21(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F21", runtime, phase)


def run_f22(runtime: ScenarioRuntime, phase: str = "run") -> None:
    _execute("F22", runtime, phase)


SCENARIO_DRIVERS: dict[str, Callable[[ScenarioRuntime, str], None]] = {
    name: globals()[f"run_{name.lower()}"] for name in SCENARIOS
}


def dispatch_native_scenario(name: str, runtime: ScenarioRuntime, *, phase: str) -> None:
    try:
        driver = SCENARIO_DRIVERS[name]
    except KeyError as error:
        raise ScenarioSelectionError(f"unknown scenario: {name}") from error
    driver(runtime, phase)


def scenario_operation_names(name: str) -> tuple[str, ...]:
    try:
        return _PROCEDURES[name].operations
    except KeyError as error:
        raise ScenarioSelectionError(f"unknown scenario: {name}") from error


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
class ScenarioIsolation:
    database_name: str
    workspace: Path
    evidence: Path

    @classmethod
    def create(
        cls,
        *,
        root: Path,
        scenario: str,
        tested_sha: str,
        campaign_nonce: str,
    ) -> ScenarioIsolation:
        if (
            not root.is_absolute()
            or scenario not in SCENARIOS
            or _SHA.fullmatch(tested_sha) is None
            or re.fullmatch(r"[0-9a-f]{8}", campaign_nonce) is None
        ):
            raise ValueError("invalid scenario isolation authority")
        stem = f"{scenario}-{tested_sha[:7]}-{campaign_nonce}"
        database = f"labpilot_task5_{scenario.lower()}_{tested_sha[:7]}_{campaign_nonce}"
        return cls(database, root / stem / "workspace", root / stem / "evidence")


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
    journal_evidence_locator: str

    def __post_init__(self) -> None:
        bounded = (
            self.installation_identity,
            self.database_identity,
            self.database_location,
        )
        if (
            self.scenario != "F18"
            or _SHA.fullmatch(self.tested_commit) is None
            or any(not value or len(value.encode("utf-8")) > 500 for value in bounded)
            or not _canonical_uuid(self.attempt_id)
            or re.fullmatch(
                r"labpilot-v1-[0-9a-f]{64}-[0-9a-f]{32}",
                self.execution_identity,
            )
            is None
            or re.fullmatch(r"[0-9a-f]{64}", self.execution_spec_hash) is None
            or self.pre_reboot_status
            not in {"launch_pending", "running", "unknown", "identity_conflict"}
            or not _canonical_uuid(self.boot_id)
            or re.fullmatch(r"[0-9a-f]{64}", self.runner_policy_identity) is None
            or not self.journal_evidence_locator
            or len(self.journal_evidence_locator.encode("utf-8")) > 1000
            or "unit=" not in self.journal_evidence_locator
            or "cursor=" not in self.journal_evidence_locator
        ):
            raise ValueError("invalid F18 metadata")

    def save(self, path: Path) -> None:
        _write_json(path, asdict(self))

    @classmethod
    def load(cls, path: Path) -> F18Metadata:
        value = _read_json(path)
        expected = {
            "scenario",
            "tested_commit",
            "installation_identity",
            "database_identity",
            "database_location",
            "attempt_id",
            "execution_identity",
            "execution_spec_hash",
            "pre_reboot_status",
            "boot_id",
            "runner_policy_identity",
            "journal_evidence_locator",
        }
        if set(value) != expected:
            raise ValueError("F18 metadata schema mismatch")
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
            journal_evidence_locator=value["journal_evidence_locator"],
        )


def require_f18_boot_change(metadata: F18Metadata, current_boot_id: str) -> None:
    if not _canonical_uuid(current_boot_id) or current_boot_id == metadata.boot_id:
        raise ValueError("F18 verify requires a different boot ID")


def _canonical_uuid(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


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
    profile_matrix = request.get("profile_matrix")
    if profile_matrix is not None:
        config = request.get("config")
        if (
            profile_matrix != "V2-A1-V2-A20"
            or request.get("phase") != "profile_matrix"
            or request.get("scenarios") != ()
            or not isinstance(config, dict)
        ):
            raise RuntimeError("native profile matrix request is invalid")
        from tests.acceptance.slice2.native_campaign import run_profile_matrix

        run_profile_matrix(profile_matrix, config)
        return
    from tests.acceptance.slice2.native_campaign import NativeCampaign

    runtime = NativeCampaign(request, preflight=facts)
    selected = request.get("scenarios")
    phase = request.get("phase")
    if not isinstance(selected, tuple) or not isinstance(phase, str):
        raise RuntimeError("native scenario request is invalid")
    for scenario in selected:
        if not isinstance(scenario, ScenarioDefinition):
            raise RuntimeError("native scenario selection is invalid")
        dispatch_native_scenario(scenario.name, runtime, phase=phase)


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
