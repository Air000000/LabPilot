from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from tests.acceptance.slice2.candidate_b_v2_fixtures import (
    V2_MATRIX_ROWS,
    V2MatrixIsolation,
    capture_final_stack_fingerprint,
    cleanup_candidate_b_v2_fixture,
    execute_candidate_b_v2_row,
    select_v2_profile_matrix,
)
from tests.acceptance.slice2.native_preflight import (
    NativePreflightError,
    compose_admin_command,
    exact_systemd_unit,
    native_worker_command,
    redact_evidence,
    run_command,
)
from tests.acceptance.slice2.oracles import (
    EvidenceWriter,
    OracleViolation,
    ScenarioVerdict,
    require_capacity_at_most_four,
    require_fifo_dispatch,
    require_natural_terminal_preserved,
    require_single_start,
)
from tests.acceptance.slice2.scenarios import (
    F18Metadata,
    ScenarioIsolation,
    ScenarioProcedure,
    require_f18_boot_change,
)

Worker = Callable[[str, str, str, str, str], dict[str, object]]
Admin = Callable[[tuple[str, ...]], None]
NativeAcceptanceConfig = Mapping[str, object]


def run_profile_matrix(
    selection: str, config: NativeAcceptanceConfig
) -> None:
    rows = select_v2_profile_matrix(selection)
    initial = capture_final_stack_fingerprint(config)
    evidence_root_value = config.get("evidence_root")
    if not isinstance(evidence_root_value, str):
        raise ValueError("profile matrix evidence root is missing")
    evidence_root = (
        Path(evidence_root_value)
        / "candidate-b-v2"
        / "profile-matrix"
        / initial.tested_sha
    )
    if not evidence_root.is_absolute() or evidence_root.exists():
        raise RuntimeError("profile matrix evidence root must be new and absolute")
    evidence_root.mkdir(parents=True)

    for row in rows:
        isolation = V2_MATRIX_ROWS[row]
        before = capture_final_stack_fingerprint(config)
        if before != initial:
            raise RuntimeError(f"profile matrix fingerprint changed before {row}")
        lease = None
        try:
            evidence, lease = execute_candidate_b_v2_row(
                row, config, initial.tested_sha
            )
            if (
                isolation
                is V2MatrixIsolation.NON_DESTRUCTIVE_FINAL_STACK_CHECK
                and lease is not None
            ):
                raise RuntimeError(f"non-destructive profile row leased mutation: {row}")
            if (
                isolation is V2MatrixIsolation.ISOLATED_TEST_ADMIN_FIXTURE
                and (lease is None or lease.row != row)
            ):
                raise RuntimeError(f"isolated profile row has no exact lease: {row}")
        finally:
            if lease is not None:
                cleanup_candidate_b_v2_fixture(config, lease)
        after = capture_final_stack_fingerprint(config)
        value = {
            "schema_version": 1,
            "row": row,
            "isolation": isolation.value,
            "tested_sha": initial.tested_sha,
            "before": asdict(before),
            "after": asdict(after),
            "cleanup_is_domain_evidence": False,
            "evidence": evidence,
            "verdict": "PASS" if after == initial else "STOPPED",
        }
        _write_profile_matrix_evidence(evidence_root / row / "scenario.json", value)
        if after != initial:
            raise RuntimeError(f"profile matrix fingerprint changed after {row}")


def _write_profile_matrix_evidence(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True)
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)


class NativeCampaign:
    def __init__(
        self,
        request: Mapping[str, object],
        *,
        preflight: Mapping[str, object],
        worker: Worker | None = None,
        admin: Admin | None = None,
        campaign_nonce: str | None = None,
    ) -> None:
        config = request.get("config")
        containers = preflight.get("containers")
        if not isinstance(config, Mapping) or not isinstance(containers, Mapping):
            raise ValueError("native campaign authority is invalid")
        self.tested_sha = _text(request, "tested_sha")
        self.database_url = _text(request, "database_url")
        self.evidence_root = Path(_text(config, "evidence_root"))
        self.workspace_root = Path(_text(config, "workspace_root"))
        self.python_profile = _text(config, "python_profile")
        self.coordinator = _text(containers, "coordinator")
        self.postgres = _text(containers, "postgres")
        self.compose_project = _text(config, "compose_project")
        self.preflight = preflight
        self.worker = worker or self._run_worker
        self.admin = admin or self._run_admin
        self.campaign_nonce = campaign_nonce or secrets.token_hex(4)
        self.coordinator_suspended = False

    def execute(self, procedure: ScenarioProcedure) -> None:
        if procedure.scenario == "F18":
            self._execute_f18(procedure)
            return
        isolation = ScenarioIsolation.create(
            root=self.evidence_root,
            scenario=procedure.scenario,
            tested_sha=self.tested_sha,
            campaign_nonce=self.campaign_nonce,
        )
        writer = EvidenceWriter(isolation.evidence)
        observations: list[dict[str, object]] = []
        cleanup_requested = False
        try:
            self._reset_database()
            observations.append(self._operation(procedure.scenario, "initialize"))
            for operation in procedure.operations:
                if operation == "seal_verdict":
                    observations.append(
                        {
                            "scenario": procedure.scenario,
                            "operation": operation,
                            "tested_sha": self.tested_sha,
                            "evidence_boundary": "seal_before_cleanup",
                        }
                    )
                    continue
                if operation == "admin_cleanup":
                    cleanup_requested = True
                    continue
                if procedure.scenario == "F20" and operation == "stop_runner_before_connect":
                    observations.append(
                        {
                            "scenario": procedure.scenario,
                            "operation": operation,
                            "tested_sha": self.tested_sha,
                            "administrative_action": "armed_at_first_launch_connect",
                        }
                    )
                    continue
                if procedure.scenario == "F20" and operation == "attempt_dispatch":
                    observations.append(self._f20_dispatch())
                    continue
                if operation == "verify_workload_survives":
                    observations.append(
                        self._verify_workload_survives(procedure.scenario, observations)
                    )
                    continue
                if operation == "stop_coordinator":
                    self._coordinator_signal("SIGSTOP")
                    observations.append(
                        {
                            "scenario": procedure.scenario,
                            "operation": operation,
                            "tested_sha": self.tested_sha,
                            "administrative_action": {
                                "service": "coordinator",
                                "action": "SIGSTOP",
                            },
                        }
                    )
                    continue
                if operation == "start_coordinator":
                    self._compose_action("coordinator", "restart")
                    observations.append(
                        {
                            "scenario": procedure.scenario,
                            "operation": operation,
                            "tested_sha": self.tested_sha,
                            "administrative_action": {
                                "service": "coordinator",
                                "action": "restart",
                            },
                        }
                    )
                    continue
                if operation in _COMPOSE_OPERATIONS:
                    service, action = _COMPOSE_OPERATIONS[operation]
                    self._compose_action(service, action)
                    observations.append(
                        {
                            "scenario": procedure.scenario,
                            "operation": operation,
                            "tested_sha": self.tested_sha,
                            "administrative_action": {"service": service, "action": action},
                        }
                    )
                else:
                    observations.append(self._operation(procedure.scenario, operation))
            observations.append(self._operation(procedure.scenario, "snapshot"))
            self._write_evidence(writer, procedure, observations)
            _require_scenario(procedure, observations)
            digest = writer.seal_verdict(
                ScenarioVerdict(
                    1,
                    procedure.scenario,
                    self.tested_sha,
                    "PASS",
                    "obligations_satisfied",
                )
            )
            self._cleanup_after_verdict(
                procedure.scenario,
                observations[-1],
                requested=cleanup_requested,
            )
            verdict_bytes = writer.root.joinpath("verdict.json").read_bytes()
            if hashlib.sha256(verdict_bytes).hexdigest() != digest:
                raise RuntimeError("verdict changed during administrative cleanup")
        except OracleViolation:
            if not writer.root.joinpath("verdict.json").exists():
                writer.seal_verdict(
                    ScenarioVerdict(
                        1,
                        procedure.scenario,
                        self.tested_sha,
                        "FAIL",
                        "invariant_violated",
                    )
                )
            raise
        except BaseException:
            if not writer.root.joinpath("verdict.json").exists():
                writer.seal_verdict(
                    ScenarioVerdict(
                        1,
                        procedure.scenario,
                        self.tested_sha,
                        "STOPPED",
                        "harness_stopped",
                    )
                )
            raise

    def _execute_f18(self, procedure: ScenarioProcedure) -> None:
        isolation = ScenarioIsolation.create(
            root=self.evidence_root,
            scenario="F18",
            tested_sha=self.tested_sha,
            campaign_nonce=self.tested_sha[:8],
        )
        if procedure.phase == "prepare":
            self._prepare_f18(procedure, isolation)
        elif procedure.phase == "verify":
            self._verify_f18(procedure, isolation)
        else:
            raise ValueError("F18 requires prepare or verify phase")

    def _prepare_f18(self, procedure: ScenarioProcedure, isolation: ScenarioIsolation) -> None:
        writer = EvidenceWriter(isolation.evidence)
        try:
            self._reset_database()
            observations = [
                self._operation("F18", "initialize"),
                self._operation("F18", "persist_pre_reboot_identity"),
                self._operation("F18", "snapshot"),
            ]
            final = observations[-1]
            attempt = _one(_attempts(final), "F18")
            runner = _runner_result(self.preflight)
            execution_identity = _required_fact(attempt, "execution_identity")
            unit = exact_systemd_unit(execution_identity)
            metadata = F18Metadata(
                scenario="F18",
                tested_commit=self.tested_sha,
                installation_identity=_required_fact(final, "installation_identity"),
                database_identity=hashlib.sha256(self.database_url.encode()).hexdigest(),
                database_location=_database_location(self.database_url),
                attempt_id=_required_fact(attempt, "attempt_id"),
                execution_identity=execution_identity,
                execution_spec_hash=_required_fact(attempt, "execution_spec_hash"),
                pre_reboot_status=_required_fact(attempt, "status"),
                boot_id=_required_fact(runner, "boot_id"),
                runner_policy_identity=_required_fact(runner, "policy_identity"),
                journal_evidence_locator=self._journal_locator(unit),
            )
            self._write_evidence(writer, procedure, observations)
            writer.write_json("scenario.json", asdict(metadata))
            if self.coordinator_suspended:
                self._coordinator_signal("SIGCONT")
        except BaseException:
            if not writer.root.joinpath("verdict.json").exists():
                writer.seal_verdict(
                    ScenarioVerdict(1, "F18", self.tested_sha, "STOPPED", "prepare_stopped")
                )
            raise

    def _verify_f18(self, procedure: ScenarioProcedure, isolation: ScenarioIsolation) -> None:
        writer = EvidenceWriter(isolation.evidence)
        try:
            metadata = F18Metadata.load(writer.root / "scenario.json")
            runner = _runner_result(self.preflight)
            current_boot = _required_fact(runner, "boot_id")
            require_f18_boot_change(metadata, current_boot)
            self._coordinator_signal("SIGSTOP")
            observations = [
                self._operation("F18", "verify_new_boot"),
                self._operation("F18", "observe_old_x"),
                self._operation("F18", "snapshot"),
            ]
            final = observations[-1]
            _require_f18(metadata, final)
            self._write_f18_verify_evidence(writer, final)
            digest = writer.seal_verdict(
                ScenarioVerdict(1, "F18", self.tested_sha, "PASS", "obligations_satisfied")
            )
            self._cleanup_after_verdict("F18", final, requested=False)
            if hashlib.sha256((writer.root / "verdict.json").read_bytes()).hexdigest() != digest:
                raise RuntimeError("F18 verdict changed during administrative cleanup")
        except OracleViolation:
            if not writer.root.joinpath("verdict.json").exists():
                writer.seal_verdict(
                    ScenarioVerdict(1, "F18", self.tested_sha, "FAIL", "invariant_violated")
                )
            raise
        except BaseException:
            if not writer.root.joinpath("verdict.json").exists():
                writer.seal_verdict(
                    ScenarioVerdict(1, "F18", self.tested_sha, "STOPPED", "verify_stopped")
                )
            raise

    def _write_f18_verify_evidence(
        self, writer: EvidenceWriter, final: Mapping[str, object]
    ) -> None:
        attempts = _attempts(final)
        systemd, journal = self._exact_unit_evidence(attempts)
        writer.write_json("db-after.json", {"rows": [redact_evidence(final)]})
        writer.write_json(
            "runner-info.json",
            redact_evidence(self.preflight.get("runner_info", {"not_applicable": True})),
        )
        writer.write_jsonl("physical-observations.jsonl", attempts)
        writer.write_jsonl("runtime-events.jsonl", _events(final))
        writer.write_json("systemd-exact-unit.json", systemd)
        writer.write_jsonl("journal-exact-unit.jsonl", journal)
        writer.write_json("workload-test-oracle.json", _workload(final))

    def _journal_locator(self, unit: str) -> str:
        result = run_command(
            ("journalctl", "--unit", unit, "--lines", "0", "--show-cursor", "--no-pager"),
            timeout_seconds=20,
            max_output_bytes=4096,
        )
        try:
            lines = result.stdout.decode("utf-8", "strict").splitlines()
        except UnicodeError as error:
            raise RuntimeError("F18 journal cursor is invalid") from error
        cursor = next(
            (line.removeprefix("-- cursor: ") for line in lines if line.startswith("-- cursor: ")),
            "",
        )
        if not cursor or len(cursor.encode()) > 1000:
            raise RuntimeError("F18 journal cursor is missing")
        return f"unit={unit};cursor={cursor}"

    def _operation(self, scenario: str, operation: str) -> dict[str, object]:
        value = self.worker(
            self.coordinator,
            scenario,
            operation,
            self.tested_sha,
            self.python_profile,
        )
        if (
            type(value) is not dict
            or value.get("scenario") != scenario
            or value.get("operation") != operation
            or value.get("tested_sha") != self.tested_sha
        ):
            raise RuntimeError("native worker returned mismatched evidence")
        return value

    def _run_worker(
        self, container: str, scenario: str, operation: str, sha: str, profile: str
    ) -> dict[str, object]:
        try:
            result = run_command(
                native_worker_command(
                    container,
                    scenario=scenario,
                    operation=operation,
                    tested_sha=sha,
                    python_profile=profile,
                ),
                timeout_seconds=240,
            )
        except NativePreflightError as error:
            if (scenario, operation) in {
                ("F05", "crash_before_runner"),
                ("F22", "crash_before_uow_commit"),
            } and str(error) == "native command failed with status 86":
                return {
                    "scenario": scenario,
                    "operation": operation,
                    "tested_sha": sha,
                    "process_exit_boundary": 86,
                }
            raise
        try:
            value = json.loads(result.stdout)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError("native worker output is invalid") from error
        if type(value) is not dict:
            raise RuntimeError("native worker output must be a mapping")
        return value

    def _run_admin(self, argv: tuple[str, ...]) -> None:
        run_command(argv, timeout_seconds=120)

    def _verify_workload_survives(
        self, scenario: str, observations: list[dict[str, object]]
    ) -> dict[str, object]:
        prior = next(
            (value for value in reversed(observations) if isinstance(value.get("attempts"), list)),
            None,
        )
        if prior is None:
            raise RuntimeError("workload survival requires prior Attempt evidence")
        attempt = _one(_attempts(prior), scenario)
        execution_identity = _required_fact(attempt, "execution_identity")
        unit = exact_systemd_unit(execution_identity)
        result = run_command(
            (
                "systemctl",
                "show",
                unit,
                "--property=ActiveState",
                "--property=SubState",
                "--property=InvocationID",
            ),
            timeout_seconds=20,
            max_output_bytes=4096,
        )
        properties = _properties(result.stdout)
        if properties.get("ActiveState") != "active":
            raise OracleViolation(f"{scenario} workload did not survive control-plane failure")
        return {
            "scenario": scenario,
            "operation": "verify_workload_survives",
            "tested_sha": self.tested_sha,
            "execution_identity": execution_identity,
            "systemd_unit": unit,
            "properties": properties,
        }

    def _coordinator_signal(self, signal_name: str) -> None:
        if signal_name not in {"SIGSTOP", "SIGCONT"}:
            raise ValueError("Coordinator signal is not authorized")
        self.admin(
            (
                "docker",
                "exec",
                self.coordinator,
                "python",
                "-c",
                f"import os,signal;os.kill(1,signal.{signal_name})",
            )
        )
        self.coordinator_suspended = signal_name == "SIGSTOP"

    def _compose_action(self, service: str, action: str) -> None:
        if service == "coordinator" and self.coordinator_suspended:
            self._coordinator_signal("SIGCONT")
        self.admin(compose_admin_command(self.compose_project, service, action))
        if service == "coordinator":
            self.coordinator_suspended = False

    def _f20_dispatch(self) -> dict[str, object]:
        root = self.workspace_root / ".task5-acceptance" / self.tested_sha / "F20"
        ready = root / ".f20-launch-boundary"
        release = root / ".f20-runner-stopped"
        errors: list[BaseException] = []

        def stop_at_boundary() -> None:
            try:
                deadline = time.monotonic() + 30
                while not ready.is_file():
                    if time.monotonic() >= deadline:
                        raise RuntimeError("F20 launch boundary was not reached")
                    time.sleep(0.05)
                self.admin(("systemctl", "stop", "labpilot-runner.service"))
                descriptor = os.open(release, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(b"runner_stopped\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException as error:
                errors.append(error)

        watcher = threading.Thread(target=stop_at_boundary, daemon=True)
        watcher.start()
        result = self._operation("F20", "attempt_dispatch")
        watcher.join(timeout=35)
        if watcher.is_alive() or errors:
            raise RuntimeError("F20 host boundary failed") from (errors[0] if errors else None)
        return result

    def _cleanup_after_verdict(
        self,
        scenario: str,
        final: Mapping[str, object],
        *,
        requested: bool,
    ) -> None:
        attempts = _attempts(final)
        cleanup_targets = [
            attempt
            for attempt in attempts
            if attempt.get("status") in {"running", "unknown", "identity_conflict"}
        ]
        if requested and not cleanup_targets:
            raise RuntimeError("required exact-unit cleanup target is missing")
        for attempt in cleanup_targets:
            execution_identity = attempt.get("execution_identity")
            if not isinstance(execution_identity, str):
                raise RuntimeError("cleanup requires exact execution identity")
            self.admin(("systemctl", "stop", exact_systemd_unit(execution_identity)))
        if scenario == "F20":
            self.admin(("systemctl", "start", "labpilot-runner.service"))
        if self.coordinator_suspended:
            self._coordinator_signal("SIGCONT")

    def _reset_database(self) -> None:
        if self.coordinator_suspended:
            self._coordinator_signal("SIGCONT")
        for service in ("api", "coordinator"):
            self._compose_action(service, "stop")
        self.admin(
            (
                "docker",
                "exec",
                self.postgres,
                "dropdb",
                "--force",
                "--if-exists",
                "-U",
                "labpilot",
                "labpilot",
            )
        )
        self.admin(
            (
                "docker",
                "exec",
                self.postgres,
                "createdb",
                "-U",
                "labpilot",
                "labpilot",
            )
        )
        self.admin(
            (
                "docker",
                "compose",
                "--project-name",
                self.compose_project,
                "run",
                "--rm",
                "migrate",
            )
        )
        self._compose_action("coordinator", "start")
        self._coordinator_signal("SIGSTOP")
        self._compose_action("api", "start")

    def _write_evidence(
        self,
        writer: EvidenceWriter,
        procedure: ScenarioProcedure,
        observations: list[dict[str, object]],
    ) -> None:
        initial, final = observations[0], observations[-1]
        attempts = final.get("attempts")
        events = final.get("runtime_events")
        workload = _workload(final)
        environment = _first_environment(attempts)
        writer.write_json(
            "scenario.json",
            {
                "scenario": procedure.scenario,
                "phase": procedure.phase,
                "tested_sha": self.tested_sha,
                "operations": list(procedure.operations),
                "obligations": list(procedure.obligations),
            },
        )
        writer.write_json("db-before.json", {"rows": [redact_evidence(initial)]})
        writer.write_json("db-after.json", {"rows": [redact_evidence(final)]})
        writer.write_json(
            "runner-info.json",
            redact_evidence(self.preflight.get("runner_info", {"not_applicable": True})),
        )
        writer.write_json(
            "runner-service-effective.json",
            redact_evidence(self.preflight.get("runner_service", {"not_applicable": True})),
        )
        writer.write_json("observed-environment.json", {"environment": environment})
        writer.write_jsonl(
            "physical-observations.jsonl",
            attempts if isinstance(attempts, list) else ({"not_applicable": True},),
        )
        writer.write_jsonl(
            "runtime-events.jsonl",
            events if isinstance(events, list) else ({"not_applicable": True},),
        )
        systemd, journal = self._exact_unit_evidence(attempts)
        writer.write_json("systemd-exact-unit.json", systemd)
        writer.write_jsonl("journal-exact-unit.jsonl", journal)
        writer.write_json("workload-test-oracle.json", workload)

    def _exact_unit_evidence(
        self, attempts: object
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        units: list[tuple[str, str]] = []
        if isinstance(attempts, list):
            for attempt in attempts:
                identity = (
                    attempt.get("execution_identity") if isinstance(attempt, Mapping) else None
                )
                if not isinstance(identity, str):
                    continue
                try:
                    unit = exact_systemd_unit(identity)
                except ValueError:
                    continue
                if (identity, unit) not in units:
                    units.append((identity, unit))
        if not units:
            return {"not_applicable": True}, [{"not_applicable": True}]

        systemd: list[dict[str, object]] = []
        journal: list[dict[str, object]] = []
        for identity, unit in units:
            listing = run_command(
                (
                    "systemctl",
                    "list-units",
                    "--all",
                    "--full",
                    "--no-legend",
                    "--plain",
                    unit,
                ),
                timeout_seconds=20,
                max_output_bytes=4096,
            ).stdout.decode("utf-8", "strict")
            listed = any(line.split(maxsplit=1)[0] == unit for line in listing.splitlines())
            properties: dict[str, str] = {}
            if listed:
                properties = _properties(
                    run_command(
                        (
                            "systemctl",
                            "show",
                            unit,
                            "--property=LoadState",
                            "--property=ActiveState",
                            "--property=SubState",
                            "--property=Result",
                            "--property=ExecMainCode",
                            "--property=ExecMainStatus",
                            "--property=InvocationID",
                            "--property=RuntimeMaxUSec",
                        ),
                        timeout_seconds=20,
                        max_output_bytes=4096,
                    ).stdout
                )
            systemd.append(
                {
                    "execution_identity": identity,
                    "unit": unit,
                    "listed": listed,
                    "properties": properties,
                }
            )
            encoded = run_command(
                (
                    "journalctl",
                    "--unit",
                    unit,
                    "--output=json",
                    "--lines=32",
                    "--no-pager",
                ),
                timeout_seconds=20,
                max_output_bytes=32 * 1024,
            ).stdout
            records: list[dict[str, object]] = []
            try:
                for line in encoded.decode("utf-8", "strict").splitlines():
                    value = json.loads(line)
                    if type(value) is not dict:
                        raise ValueError
                    records.append(cast(dict[str, object], redact_evidence(value)))
            except (UnicodeError, json.JSONDecodeError, ValueError) as error:
                raise RuntimeError("exact-unit journal evidence is invalid") from error
            journal.extend(records or [{"unit": unit, "records": 0}])
        return {"units": systemd}, journal


def _first_environment(attempts: object) -> dict[str, object]:
    if isinstance(attempts, list):
        for attempt in attempts:
            if isinstance(attempt, Mapping) and isinstance(
                attempt.get("observed_environment"), dict
            ):
                return cast(dict[str, object], attempt["observed_environment"])
    return {"not_applicable": True}


def _require_scenario(procedure: ScenarioProcedure, observations: list[dict[str, object]]) -> None:
    final = observations[-1]
    attempts = _attempts(final)
    events = _events(final)
    by_operation = {str(value["operation"]): value for value in observations}
    scenario = procedure.scenario

    if scenario == "F01":
        attempt = _one(attempts, scenario)
        workload = _single_workload(final, scenario)
        if not (
            _exited_zero(attempt)
            and isinstance(attempt.get("observed_environment"), Mapping)
            and bool(attempt.get("source_manifest_sha256"))
            and bool(attempt.get("execution_spec_hash"))
            and workload.get("source_sha256") == attempt.get("prepared_train_sha256")
        ):
            raise OracleViolation("F01 terminal/source/environment obligations failed")
        require_single_start(workload)
        return
    if scenario == "F02":
        prepared = _one(_attempts(by_operation["prepare"]), scenario)
        attempt = _one(attempts, scenario)
        if not (
            prepared.get("status") == "launch_ready"
            and prepared.get("prepared_train_sha256")
            != by_operation["prepare"].get("repository_train_sha256")
            and attempt.get("status") == "launch_ready"
            and attempt.get("prepared_train_sha256") != prepared.get("prepared_train_sha256")
            and not _workloads(final)
        ):
            raise OracleViolation("F02 source isolation or tamper fail-closed failed")
        return
    if scenario in {"F03", "F16"}:
        statuses = tuple(str(item.get("status")) for item in attempts)
        require_capacity_at_most_four(statuses)
        trials = final.get("trials")
        if not isinstance(trials, list) or len(trials) != 6 or len(attempts) != 6:
            raise OracleViolation(f"{scenario} requires six durable candidates")
        candidates = tuple(str(item.get("trial_id")) for item in attempts)
        physical = {"launch_pending", "running", "unknown", "identity_conflict"}
        dispatched = tuple(
            str(item.get("trial_id")) for item in attempts if item.get("status") in physical
        )
        require_fifo_dispatch(candidates, dispatched)
        if scenario == "F16" and "unknown" not in statuses:
            raise OracleViolation("F16 requires UNKNOWN to consume physical capacity")
        return
    if scenario == "F04":
        if _attempts(by_operation["persist_start"]):
            raise OracleViolation("F04 created Attempt while Coordinator was down")
        _require_one_physical(attempts, scenario)
        return
    if scenario == "F05":
        attempt = _one(attempts, scenario)
        if not (
            attempt.get("status") in {"launch_pending", "unknown"}
            and not _workloads(final)
            and _event_count(events, "attempt.physical_started_observed") == 0
        ):
            raise OracleViolation("F05 fabricated acceptance or relaunched the Attempt")
        return
    if scenario in {"F06", "F07", "F19", "F22"}:
        _require_single_physical_start(final, events, scenario)
        if scenario in {"F07", "F19"} and not _active_survival(
            by_operation.get("verify_workload_survives")
        ):
            raise OracleViolation(f"{scenario} physical workload survival evidence is missing")
        if scenario == "F22" and _event_count(events, "attempt.reconciled_after_restart") != 1:
            raise OracleViolation("F22 restart reconciliation audit is missing or duplicated")
        return
    if scenario == "F08":
        attempt = _one(attempts, scenario)
        workload = _single_workload(final, scenario)
        if not _exited_zero(attempt) or workload.get("natural_exit") is not True:
            raise OracleViolation("F08 did not converge to the real natural terminal result")
        require_single_start(workload)
        return
    if scenario == "F09":
        _require_single_physical_start(final, events, scenario)
        if not _active_survival(by_operation.get("verify_workload_survives")):
            raise OracleViolation("F09 workload did not survive the database outage")
        return
    if scenario in {"F10", "F11", "F13"}:
        attempt = _one(attempts, scenario)
        workload = _single_workload(final, scenario)
        if attempt.get("status") != "terminal":
            raise OracleViolation(f"{scenario} cancellation did not converge terminal")
        require_single_start(workload)
        return
    if scenario == "F12":
        before = _one(_attempts(by_operation["launch_short_workload"]), scenario)
        after = _one(attempts, scenario)
        if before.get("terminal_at") is not None:
            require_natural_terminal_preserved(before, after)
        elif not _exited_zero(after):
            raise OracleViolation("F12 did not preserve natural terminal truth")
        return
    if scenario == "F14":
        attempt = _one(attempts, scenario)
        if not (
            attempt.get("status") == "unknown"
            and attempt.get("terminal_at") is None
            and attempt.get("attention_required_at") is not None
            and len(attempts) == 1
        ):
            raise OracleViolation("F14 UNKNOWN invariants failed")
        require_single_start(_single_workload(final, scenario))
        return
    if scenario == "F15":
        attempt = _one(attempts, scenario)
        if not (
            attempt.get("status") == "identity_conflict"
            and attempt.get("terminal_at") is None
            and attempt.get("execution_issue_code") == "IDENTITY_CONFLICT"
            and _event_count(events, "attempt.identity_conflict_observed") == 1
        ):
            raise OracleViolation("F15 identity conflict invariants failed")
        return
    if scenario == "F17":
        statuses = tuple(str(item.get("status")) for item in attempts)
        audits = [
            event
            for event in events
            if event.get("event_type") == "attempt.reconciled_after_restart"
        ]
        pre_statuses = {
            str(payload.get("pre_reconciliation_status"))
            for event in audits
            if isinstance((payload := event.get("payload")), Mapping)
        }
        if not (
            len(attempts) == 6
            and statuses[:4] == ("running", "launch_pending", "running", "identity_conflict")
            and statuses[4:] == ("launch_ready", "launch_ready")
            and len(audits) == 4
            and pre_statuses == {"running", "launch_pending", "unknown", "identity_conflict"}
        ):
            raise OracleViolation("F17 mixed startup barrier state or audit is invalid")
        queued = {str(item.get("attempt_id")) for item in attempts[4:]}
        if any(
            event.get("aggregate_id") in queued
            and event.get("event_type") == "attempt.physical_started_observed"
            for event in events
        ):
            raise OracleViolation("F17 dispatched queued work across a full startup barrier")
        return
    if scenario == "F18":
        raise RuntimeError("F18 phase evaluator is not implemented")
    if scenario == "F20":
        attempt = _one(attempts, scenario)
        if not (
            attempt.get("status") == "terminal"
            and attempt.get("terminal_kind") == "launch_not_accepted"
            and not _workloads(final)
            and _event_count(events, "attempt.physical_started_observed") == 0
        ):
            raise OracleViolation("F20 pre-send non-acceptance obligations failed")
        return
    if scenario == "F21":
        attempt = _one(attempts, scenario)
        if not (
            attempt.get("status") == "terminal"
            and attempt.get("supervisor_result") == "timeout"
            and _event_count(events, "attempt.physical_started_observed") == 1
        ):
            raise OracleViolation("F21 real RuntimeMaxSec obligations failed")
        return
    raise RuntimeError("scenario evaluator is not implemented")


def _attempts(value: Mapping[str, object]) -> list[Mapping[str, object]]:
    attempts = value.get("attempts")
    if not isinstance(attempts, list) or not all(isinstance(item, Mapping) for item in attempts):
        raise OracleViolation("native Attempt evidence is invalid")
    return attempts


def _events(value: Mapping[str, object]) -> list[Mapping[str, object]]:
    events = value.get("runtime_events")
    if not isinstance(events, list) or not all(isinstance(item, Mapping) for item in events):
        raise OracleViolation("native RuntimeEvent evidence is invalid")
    return events


def _workloads(value: Mapping[str, object]) -> list[Mapping[str, object]]:
    records = value.get("workload_oracles")
    if records is None and isinstance(value.get("workload_oracle"), Mapping):
        records = [value["workload_oracle"]]
    if not isinstance(records, list) or not all(isinstance(item, Mapping) for item in records):
        return []
    return records


def _one(values: list[Mapping[str, object]], scenario: str) -> Mapping[str, object]:
    if len(values) != 1:
        raise OracleViolation(f"{scenario} requires exactly one Attempt")
    return values[0]


def _single_workload(value: Mapping[str, object], scenario: str) -> Mapping[str, object]:
    return _one(_workloads(value), scenario)


def _exited_zero(attempt: Mapping[str, object]) -> bool:
    return (
        attempt.get("status") == "terminal"
        and attempt.get("terminal_kind") == "process_exited"
        and attempt.get("exit_code") == 0
        and attempt.get("termination_signal") is None
    )


def _event_count(events: list[Mapping[str, object]], event_type: str) -> int:
    return sum(item.get("event_type") == event_type for item in events)


def _active_survival(value: object) -> bool:
    properties = value.get("properties") if isinstance(value, Mapping) else None
    return isinstance(properties, Mapping) and properties.get("ActiveState") == "active"


def _require_one_physical(attempts: list[Mapping[str, object]], scenario: str) -> None:
    attempt = _one(attempts, scenario)
    if attempt.get("status") not in {"launch_pending", "running", "terminal"}:
        raise OracleViolation(f"{scenario} did not create one physical Attempt")


def _require_single_physical_start(
    final: Mapping[str, object], events: list[Mapping[str, object]], scenario: str
) -> None:
    _require_one_physical(_attempts(final), scenario)
    require_single_start(_single_workload(final, scenario))
    if _event_count(events, "attempt.physical_started_observed") != 1:
        raise OracleViolation(f"{scenario} physical start audit count is not one")


def _workload(value: Mapping[str, object]) -> object:
    if "workload_oracle" in value:
        return value["workload_oracle"]
    records = value.get("workload_oracles")
    if isinstance(records, list) and len(records) == 1:
        return records[0]
    return {"records": records} if isinstance(records, list) else {"not_applicable": True}


def _runner_result(preflight: Mapping[str, object]) -> Mapping[str, object]:
    info = preflight.get("runner_info")
    result = info.get("result") if isinstance(info, Mapping) else None
    if not isinstance(result, Mapping):
        raise RuntimeError("fresh Runner INFO evidence is missing")
    return result


def _required_fact(value: Mapping[str, object], name: str) -> str:
    fact = value.get(name)
    if not isinstance(fact, str) or not fact or len(fact.encode()) > 1000:
        raise RuntimeError(f"required native fact is missing: {name}")
    return fact


def _database_location(database_url: str) -> str:
    parsed = urlsplit(database_url)
    if not parsed.hostname or not parsed.path or parsed.path == "/":
        raise RuntimeError("F18 database location is invalid")
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.hostname}{port}{parsed.path}"


def _properties(encoded: bytes) -> dict[str, str]:
    try:
        lines = encoded.decode("utf-8", "strict").splitlines()
    except UnicodeError as error:
        raise RuntimeError("systemd evidence is not UTF-8") from error
    value: dict[str, str] = {}
    for line in lines:
        name, separator, item = line.partition("=")
        if not separator or not name or name in value:
            raise RuntimeError("systemd evidence is invalid")
        value[name] = item
    return value


def _require_f18(metadata: F18Metadata, final: Mapping[str, object]) -> None:
    attempt = _one(_attempts(final), "F18")
    events = _events(final)
    workload = _single_workload(final, "F18")
    status = attempt.get("status")
    allowed_truth = status == "terminal" or (
        status == "unknown" and attempt.get("terminal_at") is None
    )
    if not (
        final.get("tested_sha") == metadata.tested_commit
        and final.get("installation_identity") == metadata.installation_identity
        and attempt.get("attempt_id") == metadata.attempt_id
        and attempt.get("execution_identity") == metadata.execution_identity
        and attempt.get("execution_spec_hash") == metadata.execution_spec_hash
        and allowed_truth
        and _event_count(events, "attempt.physical_started_observed") == 1
    ):
        raise OracleViolation("F18 old-X reboot convergence obligations failed")
    require_single_start(workload)


_COMPOSE_OPERATIONS = {
    "restart_coordinator": ("coordinator", "restart"),
    "sigkill_coordinator": ("coordinator", "kill"),
    "stop_postgres": ("postgres", "stop"),
    "start_postgres": ("postgres", "start"),
    "kill_api": ("api", "kill"),
    "restart_api": ("api", "restart"),
}


def _text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item or len(item.encode()) > 4096:
        raise ValueError(f"native campaign authority is missing {key}")
    return item
