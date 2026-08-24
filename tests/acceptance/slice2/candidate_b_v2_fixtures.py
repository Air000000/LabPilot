from __future__ import annotations

import argparse
import asyncio
import grp
import hashlib
import json
import os
import pwd
import re
import shutil
import stat
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from tests.acceptance.slice2.native_preflight import (
    CommandResult,
    NativePreflightError,
    run_command,
)

from labpilot.execution.runner_policy import (
    TERMINAL_RECEIPT_REPORTER_PATH,
    load_runner_policy,
    validate_candidate_b_v2_deployment,
)
from labpilot.execution.runner_protocol import RunnerOperation

_SHA = re.compile(r"^[0-9a-f]{40}$")
_UNIT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,199}\.service$")
_PROFILE_SELECTION = "V2-A1-V2-A20"
_PROOF_ROOT = Path("/opt/labpilot-runner-mechanism-proof")
_A12_ROOT = _PROOF_ROOT / "candidate-b-v2-a12"
_A12_ASSETS = {
    "malformed": _A12_ROOT / "receipt-malformed.py",
    "duplicate": _A12_ROOT / "receipt-duplicate.py",
}
_A12_VARIANTS: tuple[Literal["malformed", "duplicate"], ...] = (
    "malformed",
    "duplicate",
)
_A18_SCRIPT = Path("tests/acceptance/slice2/fixtures/a18_installer_drift_probe.sh")
_INSTALLED_POLICY = Path("/etc/labpilot/runner-policy.json")
_APP_PYTHON = "/opt/labpilot-runner/app/.venv/bin/python"
_UNSET_ENVIRONMENT = (
    "LD_AUDIT LD_DEBUG LD_LIBRARY_PATH LD_PRELOAD PYTHONHOME PYTHONPATH"
)
_MAIN_SECURITY_CODE = """
import json
import os
import sys

status = {}
for line in open("/proc/self/status", encoding="ascii"):
    name, separator, value = line.partition(":")
    if separator:
        status[name] = value.strip()
evidence = {
    "uid": os.getuid(),
    "gid": os.getgid(),
    "no_new_privs": int(status["NoNewPrivs"]),
    "cap_prm": status["CapPrm"],
    "cap_eff": status["CapEff"],
    "cap_amb": status["CapAmb"],
    "environment": {
        name: os.environ.get(name)
        for name in ("LD_LIBRARY_PATH", "PYTHONPATH")
    },
}
with open(sys.argv[1], "w", encoding="ascii") as stream:
    json.dump(evidence, stream, separators=(",", ":"), sort_keys=True)
""".strip()
_RUNNER_REQUEST = """
import json
import sys

from labpilot.execution.runner_client import RunnerClient
from labpilot.execution.runner_protocol import (
    PROTOCOL_VERSION,
    RunnerInfoRequest,
    RunnerObserveRequest,
    RunnerOperation,
    RunnerStopRequest,
)

socket_path = sys.argv[1]
operation = sys.argv[2]
client = RunnerClient(socket_path, timeout_seconds=10)
if operation == "INFO":
    request = RunnerInfoRequest(PROTOCOL_VERSION, RunnerOperation.INFO, None)
elif operation == "OBSERVE":
    request = RunnerObserveRequest(
        PROTOCOL_VERSION, RunnerOperation.OBSERVE, sys.argv[3], sys.argv[4]
    )
elif operation == "STOP":
    request = RunnerStopRequest(
        PROTOCOL_VERSION, RunnerOperation.STOP, sys.argv[3], sys.argv[4]
    )
else:
    raise SystemExit("unsupported operation")
print(json.dumps(client.request(request), separators=(",", ":"), sort_keys=True))
""".strip()


class V2MatrixIsolation(StrEnum):
    NON_DESTRUCTIVE_FINAL_STACK_CHECK = "NON_DESTRUCTIVE_FINAL_STACK_CHECK"
    ISOLATED_TEST_ADMIN_FIXTURE = "ISOLATED_TEST_ADMIN_FIXTURE"
    DESTRUCTIVE_HOST_CHECK_REQUIRING_FULL_REBASELINE_BEFORE_CAMPAIGN = (
        "DESTRUCTIVE_HOST_CHECK_REQUIRING_FULL_REBASELINE_BEFORE_CAMPAIGN"
    )


V2_MATRIX_ROWS = {
    f"V2-A{number}": (
        V2MatrixIsolation.NON_DESTRUCTIVE_FINAL_STACK_CHECK
        if number in {1, 15, 20}
        else V2MatrixIsolation.ISOLATED_TEST_ADMIN_FIXTURE
    )
    for number in range(1, 21)
}


@dataclass(frozen=True, slots=True)
class FinalStackFingerprint:
    tested_sha: str
    reporter_sha256: str
    policy_identity: str
    runner_exec_start: str
    runner_active_state: str
    runner_invocation_id: str
    compose_container_ids: tuple[tuple[str, str], ...]
    compose_image_ids: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class V2FixtureLease:
    row: str
    units: tuple[str, ...]
    proof_root: Path | None
    compose_project: str | None
    cleanup_is_domain_evidence: bool

    def __post_init__(self) -> None:
        if (
            self.row not in V2_MATRIX_ROWS
            or any(_UNIT.fullmatch(unit) is None for unit in self.units)
            or self.cleanup_is_domain_evidence
            or (
                self.proof_root is not None
                and not self.proof_root.is_absolute()
            )
            or (
                self.compose_project is not None
                and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", self.compose_project)
                is None
            )
        ):
            raise ValueError("invalid Candidate B v2 fixture lease")


def select_v2_profile_matrix(selection: str) -> tuple[str, ...]:
    if selection != _PROFILE_SELECTION:
        raise ValueError("profile matrix selection must be exactly V2-A1-V2-A20")
    return tuple(V2_MATRIX_ROWS)


def capture_final_stack_fingerprint(
    config: Mapping[str, object],
) -> FinalStackFingerprint:
    repository_root = _config_path(config, "repository_root")
    tested_sha = _text(
        run_command(("git", "-C", str(repository_root), "rev-parse", "HEAD"))
    )
    if _SHA.fullmatch(tested_sha) is None:
        raise NativePreflightError("final stack tested SHA is invalid")
    if _text(
        run_command(("git", "-C", str(repository_root), "status", "--short"))
    ):
        raise NativePreflightError("final stack checkout is not clean")

    manifest = validate_candidate_b_v2_deployment()
    policy = load_runner_policy(_INSTALLED_POLICY)
    service = _properties(
        _text(
            run_command(
                (
                    "systemctl",
                    "show",
                    "labpilot-runner.service",
                    "--no-pager",
                    "--property=ExecStart",
                    "--property=ActiveState",
                    "--property=InvocationID",
                )
            )
        )
    )
    if service.get("ActiveState") != "active":
        raise NativePreflightError("final Runner is not active")
    runner_info = _runner_request("V2-A1", "INFO")
    result = runner_info.get("result")
    if (
        runner_info.get("outcome") != "ok"
        or not isinstance(result, Mapping)
        or result.get("readiness_reason") != "ready"
        or result.get("runner_version") != "candidate-b-v2"
        or result.get("policy_identity") != policy.policy_identity
    ):
        raise NativePreflightError("final Runner INFO fingerprint mismatch")

    project = _config_text(config, "compose_project")
    container_ids: list[tuple[str, str]] = []
    image_ids: list[tuple[str, str]] = []
    for service_name in ("api", "coordinator", "postgres"):
        container = _single_line(
            run_command(
                (
                    "docker",
                    "ps",
                    "--filter",
                    f"label=com.docker.compose.project={project}",
                    "--filter",
                    f"label=com.docker.compose.service={service_name}",
                    "--format",
                    "{{.ID}}",
                )
            )
        )
        image = _single_line(
            run_command(("docker", "inspect", "--format", "{{.Image}}", container))
        )
        container_ids.append((service_name, container))
        image_ids.append((service_name, image))
    return FinalStackFingerprint(
        tested_sha=tested_sha,
        reporter_sha256=manifest.reporter_sha256,
        policy_identity=policy.policy_identity,
        runner_exec_start=_required(service, "ExecStart"),
        runner_active_state=_required(service, "ActiveState"),
        runner_invocation_id=_required(service, "InvocationID"),
        compose_container_ids=tuple(container_ids),
        compose_image_ids=tuple(image_ids),
    )


def create_missing_receipt_fixture(
    config: Mapping[str, object], tested_sha: str
) -> V2FixtureLease:
    del config
    execution_identity, execution_hash = _identity("V2-A11", tested_sha)
    unit = f"{execution_identity}.service"
    _reset_failed_if_loaded(unit)
    run_command(
        _systemd_run_argv(
            "V2-A11",
            execution_identity,
            execution_hash,
            ("/usr/bin/true",),
            reporter=None,
            wait=True,
        ),
        timeout_seconds=60,
    )
    return V2FixtureLease("V2-A11", (unit,), None, None, False)


def create_trusted_receipt_fixture(
    config: Mapping[str, object],
    tested_sha: str,
    variant: Literal["malformed", "duplicate"],
) -> V2FixtureLease:
    del config
    try:
        asset = _A12_ASSETS[variant]
    except KeyError as error:
        raise ValueError("unsupported trusted receipt fixture") from error
    _install_fixed_proof_asset(asset, _a12_asset_bytes(variant))
    row_key = f"V2-A12-{variant}"
    execution_identity, execution_hash = _identity(row_key, tested_sha)
    unit = f"{execution_identity}.service"
    _reset_failed_if_loaded(unit)
    run_command(
        _systemd_run_argv(
            "V2-A12",
            execution_identity,
            execution_hash,
            ("/usr/bin/true",),
            reporter=asset,
            wait=True,
        ),
        timeout_seconds=60,
    )
    return V2FixtureLease("V2-A12", (unit,), _A12_ROOT, None, False)


def create_isolated_v2_runner_fixture(
    config: Mapping[str, object], tested_sha: str
) -> V2FixtureLease:
    root, runner_unit = _start_isolated_runner(config, tested_sha, "V2-A13")
    return V2FixtureLease("V2-A13", (runner_unit,), root, None, False)


def _start_isolated_runner(
    config: Mapping[str, object], tested_sha: str, row: Literal["V2-A13", "V2-A14"]
) -> tuple[Path, str]:
    policy_source = _config_path(config, "runner_policy_source")
    suffix = row.lower().replace("v2-", "")
    root = _PROOF_ROOT / f"candidate-b-v2-{suffix}-{tested_sha[:12]}"
    policy_target = root / "runner-policy.json"
    if root.exists() or root.is_symlink():
        raise RuntimeError("isolated v2 Runner proof root already exists")
    root.mkdir(parents=True, mode=0o750)
    controller_gid = grp.getgrnam("labpilot-control").gr_gid
    os.chown(root, 0, controller_gid)
    os.chmod(root, 0o750)
    value = json.loads(policy_source.read_text(encoding="utf-8"))
    value["socket_path"] = str(root / "control.sock")
    if row == "V2-A14":
        value["workspace_root"] = str(
            _matrix_root(config, tested_sha) / "fixtures/V2-A14/workspace"
        )
    policy_target.write_text(
        json.dumps(value, separators=(",", ":"), sort_keys=True), encoding="utf-8"
    )
    os.chown(policy_target, 0, 0)
    os.chmod(policy_target, 0o600)
    runner_unit = f"labpilot-v2-{suffix}-runner.service"
    run_command(
        (
            "systemd-run",
            "--system",
            "--no-ask-password",
            f"--unit={runner_unit.removesuffix('.service')}",
            "--service-type=exec",
            "--property=User=root",
            "--property=Group=labpilot-control",
            "--property=NoNewPrivileges=yes",
            "--property=RestrictAddressFamilies=AF_UNIX",
            "--",
            _APP_PYTHON,
            "-E",
            "-s",
            "-B",
            "-m",
            "labpilot.execution.runner_daemon",
            "--policy",
            str(policy_target),
        )
    )
    _wait_isolated_runner(root / "control.sock")
    return root, runner_unit


def create_isolated_v2_compose_fixture(
    config: Mapping[str, object], tested_sha: str
) -> V2FixtureLease:
    repository_root = _config_path(config, "repository_root")
    project = f"labpilot-v2-a14-{tested_sha[:12]}"
    runner_root, runner_unit = _start_isolated_runner(config, tested_sha, "V2-A14")
    fixture_root = _matrix_root(config, tested_sha) / "fixtures/V2-A14"
    workspace = fixture_root / "workspace"
    workspace.mkdir(parents=True, mode=0o770)
    train_gid = pwd.getpwnam("labpilot-train").pw_gid
    os.chown(workspace, 0, train_gid)
    os.chmod(workspace, 0o770)
    override = fixture_root / "compose.override.yaml"
    override.write_text(
        "services:\n"
        "  coordinator:\n"
        "    environment:\n"
        f"      LABPILOT_RUNNER_SOCKET_PATH: {runner_root}/control.sock\n"
        "    volumes: !override\n"
        f"      - {repository_root}:/repositories:ro\n"
        "      - labpilot_content:/var/lib/labpilot/content\n"
        f"      - {workspace}:{workspace}\n"
        "      - type: bind\n"
        f"        source: {runner_root}\n"
        f"        target: {runner_root}\n"
        "        read_only: true\n",
        encoding="utf-8",
    )
    prefix = _a14_compose_prefix(
        config,
        tested_sha,
        project=project,
        override=override,
        workspace=workspace,
    )
    run_command(
        (*prefix, "up", "--detach", "postgres"),
        timeout_seconds=180,
    )
    run_command((*prefix, "run", "--rm", "migrate"), timeout_seconds=180)
    return V2FixtureLease(
        "V2-A14", (runner_unit,), runner_root, project, False
    )


def prepare_v1_compatibility_before_v2_install(
    config: Mapping[str, object], tested_sha: str
) -> V2FixtureLease:
    evidence_root = _matrix_root(config, tested_sha)
    evidence_root.mkdir(parents=True, exist_ok=True)
    execution_identity, execution_hash = _identity("V2-A15", tested_sha)
    unit = f"{execution_identity}.service"
    _reset_failed_if_loaded(unit)
    run_command(
        _systemd_run_argv(
            "V2-A15",
            execution_identity,
            execution_hash,
            ("/usr/bin/sleep", "3600"),
            reporter=None,
            wait=False,
            extra_properties=(
                "--property=Environment=LABPILOT_PROFILE_VERSION=candidate-b-v1",
            ),
        )
    )
    properties = _properties(
        _text(
            run_command(
                (
                    "systemctl",
                    "show",
                    unit,
                    "--no-pager",
                    "--property=ActiveState",
                    "--property=InvocationID",
                )
            )
        )
    )
    if properties.get("ActiveState") != "active":
        raise RuntimeError("v1 compatibility lifecycle is not active")
    _write_json(
        evidence_root / "v2-a15-prepared.json",
        {
            "tested_sha": tested_sha,
            "execution_identity": execution_identity,
            "execution_spec_hash": execution_hash,
            "unit": unit,
            "invocation_id": _required(properties, "InvocationID"),
            "profile": "candidate-b-v1",
            "start_cardinality": 1,
        },
    )
    return V2FixtureLease("V2-A15", (unit,), evidence_root, None, False)


def verify_v1_compatibility_after_v2_install(
    config: Mapping[str, object], lease: V2FixtureLease
) -> dict[str, object]:
    if lease.row != "V2-A15" or lease.proof_root is None:
        raise ValueError("invalid v1 compatibility lease")
    value = _read_json(lease.proof_root / "v2-a15-prepared.json")
    tested_sha = _required_text(value, "tested_sha")
    execution_identity = _required_text(value, "execution_identity")
    execution_hash = _required_text(value, "execution_spec_hash")
    if tested_sha != capture_final_stack_fingerprint(config).tested_sha:
        raise RuntimeError("v1 compatibility tested SHA mismatch")
    observed = _runner_request("V2-A15", "OBSERVE", execution_identity, execution_hash)
    if _physical_kind(observed) != "active_match":
        raise RuntimeError("v2 Runner did not preserve active v1 lifecycle")
    stopped = _runner_request("V2-A15", "STOP", execution_identity, execution_hash)
    records = _journal_records(
        (
            "journalctl",
            "--no-pager",
            "--output=json",
            f"UNIT={_required_text(value, 'unit')}",
            "MESSAGE_ID=39f53479d3a045ac8e11786248231fbf",
        )
    )
    starts = [
        record
        for record in records
        if record.get("JOB_TYPE") == "start" and record.get("JOB_RESULT") == "done"
    ]
    if len(starts) != 1:
        raise RuntimeError("v1 compatibility start cardinality changed")
    verified = {
        "tested_sha": tested_sha,
        "prepared": value,
        "observed": observed,
        "stopped": stopped,
        "start_cardinality": 1,
    }
    _write_json(lease.proof_root / "v2-a15-verified.json", verified)
    return verified


def run_isolated_installer_drift_fixture(
    config: Mapping[str, object], tested_sha: str
) -> dict[str, object]:
    _require_sha(tested_sha)
    repository_root = _config_path(config, "repository_root")
    fixture_root = _config_path(config, "evidence_root") / (
        f"candidate-b-v2/a18-{tested_sha[:12]}"
    )
    script = repository_root / _A18_SCRIPT
    if script != repository_root / _A18_SCRIPT or not script.is_file():
        raise RuntimeError("fixed A18 fixture script is unavailable")
    _prepare_a18_fixture(fixture_root, repository_root)
    before = _host_reporter_sha256()
    result = run_command(
        ("bash", str(script), str(fixture_root), str(repository_root)),
        timeout_seconds=120,
        max_output_bytes=16 * 1024,
    )
    after = _host_reporter_sha256()
    if before != after:
        raise RuntimeError("A18 changed the host production reporter")
    try:
        value = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("A18 fixture evidence is invalid") from error
    if type(value) is not dict or value != {
        "host_reporter_unchanged": True,
        "validator_failed_closed": True,
    }:
        raise RuntimeError("A18 fixture did not fail closed")
    return value


def cleanup_candidate_b_v2_fixture(
    config: Mapping[str, object], lease: V2FixtureLease
) -> None:
    if lease.cleanup_is_domain_evidence:
        raise ValueError("fixture cleanup cannot be domain evidence")
    for unit in lease.units:
        if _UNIT.fullmatch(unit) is None:
            raise ValueError("fixture cleanup unit is invalid")
        loaded = _properties(
            _text(
                run_command(
                    (
                        "systemctl",
                        "show",
                        unit,
                        "--no-pager",
                        "--property=LoadState",
                    )
                )
            )
        )
        if loaded.get("LoadState") == "not-found":
            continue
        run_command(("systemctl", "stop", unit))
        _reset_failed_if_loaded(unit)
    if lease.compose_project is not None:
        repository_root = _config_path(config, "repository_root")
        if lease.row == "V2-A14":
            tested_sha = _text(
                run_command(
                    ("git", "-C", str(repository_root), "rev-parse", "HEAD")
                )
            )
            compose_prefix = _a14_compose_prefix(
                config, tested_sha, project=lease.compose_project
            )
        else:
            compose_prefix = (
                "docker",
                "compose",
                "--project-directory",
                str(repository_root),
                "--project-name",
                lease.compose_project,
            )
        run_command(
            (
                *compose_prefix,
                "down",
                "--volumes",
                "--remove-orphans",
            ),
            timeout_seconds=180,
        )
    if lease.proof_root is not None and lease.proof_root.exists():
        allowed_roots = (_PROOF_ROOT, _config_path(config, "evidence_root"))
        resolved = lease.proof_root.resolve(strict=True)
        if lease.proof_root.is_symlink() or not any(
            resolved == root or resolved.is_relative_to(root) for root in allowed_roots
        ):
            raise RuntimeError("fixture cleanup proof root escaped its lease")
        shutil.rmtree(resolved)


def execute_candidate_b_v2_row(
    row: str, config: Mapping[str, object], tested_sha: str
) -> tuple[dict[str, object], V2FixtureLease | None]:
    if row not in V2_MATRIX_ROWS:
        raise ValueError("unknown Candidate B v2 matrix row")
    if row == "V2-A1":
        manifest = validate_candidate_b_v2_deployment()
        return {"manifest": asdict(manifest)}, None
    if row == "V2-A2":
        return _execute_security_row(config, tested_sha)
    if row == "V2-A3":
        return _execute_failure_neutrality_row(config, tested_sha)
    if row == "V2-A11":
        lease = create_missing_receipt_fixture(config, tested_sha)
        execution_identity, execution_hash = _identity(row, tested_sha)
        observed = _runner_request(row, "OBSERVE", execution_identity, execution_hash)
        result = _physical_result(observed)
        if result.get("kind") != "terminal_match" or any(
            result.get(name) is not None for name in ("exit_code", "termination_signal")
        ):
            raise RuntimeError("V2-A11 did not preserve unclassified terminality")
        repeated = _runner_request(row, "OBSERVE", execution_identity, execution_hash)
        if repeated != observed or _start_cardinality(lease.units[0]) != 1:
            raise RuntimeError("V2-A11 retried or relaunched its lifecycle")
        return {"observation": observed, "repeated_observation": repeated}, lease
    if row == "V2-A12":
        leases = [
            create_trusted_receipt_fixture(config, tested_sha, variant)
            for variant in _A12_VARIANTS
        ]
        units = tuple(unit for lease in leases for unit in lease.units)
        lease = V2FixtureLease(row, units, _A12_ROOT, None, False)
        observations: dict[str, object] = {}
        for variant in _A12_VARIANTS:
            execution_identity, execution_hash = _identity(
                f"V2-A12-{variant}", tested_sha
            )
            first = _runner_request(row, "OBSERVE", execution_identity, execution_hash)
            repeated = _runner_request(
                row, "OBSERVE", execution_identity, execution_hash
            )
            unit = f"{execution_identity}.service"
            observations[variant] = {
                "first": first,
                "repeated": repeated,
                "start_cardinality": _start_cardinality(unit),
            }
            if (
                _physical_kind(first) != "unknown"
                or repeated != first
                or _start_cardinality(unit) != 1
            ):
                raise RuntimeError("V2-A12 did not reject receipt cardinality")
        return {"observations": observations}, lease
    if row == "V2-A13":
        lease = create_isolated_v2_runner_fixture(config, tested_sha)
        assert lease.proof_root is not None
        execution_identity, execution_hash = _identity(row, tested_sha)
        workload_unit = f"{execution_identity}.service"
        run_command(
            _systemd_run_argv(
                row,
                execution_identity,
                execution_hash,
                ("/usr/bin/sleep", "2"),
                reporter=TERMINAL_RECEIPT_REPORTER_PATH,
                wait=False,
                collect=False,
            )
        )
        run_command(("systemctl", "restart", lease.units[0]))
        _wait_isolated_runner(lease.proof_root / "control.sock")
        _wait_unit_terminal(workload_unit)
        observed = _runner_request_at_socket(
            row,
            lease.proof_root / "control.sock",
            "OBSERVE",
            execution_identity,
            execution_hash,
        )
        result = _physical_result(observed)
        if result.get("kind") != "terminal_match" or result.get("exit_code") != 0:
            raise RuntimeError("V2-A13 isolated Runner did not recover receipt")
        receipts = _receipt_records(workload_unit)
        if len(receipts) != 1:
            raise RuntimeError("V2-A13 receipt evidence is missing")
        lease = V2FixtureLease(
            row,
            (*lease.units, workload_unit),
            lease.proof_root,
            None,
            False,
        )
        return {
            "isolated_runner_restarted": True,
            "observation": observed,
            "receipts": receipts,
        }, lease
    if row == "V2-A14":
        lease = create_isolated_v2_compose_fixture(config, tested_sha)
        operations: dict[str, object] = {}
        for operation in (
            "initialize",
            "launch_active",
            "wait_real_exit",
            "crash_after_observe",
            "startup_reconcile",
            "snapshot",
        ):
            operations[operation] = _run_a14_operation(
                config, tested_sha, operation
            )
        snapshot = operations["snapshot"]
        if not isinstance(snapshot, Mapping):
            raise RuntimeError("V2-A14 snapshot is invalid")
        attempts = snapshot.get("attempts")
        workloads = snapshot.get("workloads")
        if (
            not isinstance(attempts, list)
            or len(attempts) != 1
            or not isinstance(attempts[0], Mapping)
            or attempts[0].get("status") != "succeeded"
            or attempts[0].get("exit_code") != 0
            or not isinstance(workloads, list)
            or len(workloads) != 1
            or not isinstance(workloads[0], Mapping)
            or workloads[0].get("start_count") != 1
        ):
            raise RuntimeError("V2-A14 startup reconciliation did not converge")
        return {
            "isolated_compose_project": lease.compose_project,
            "operations": operations,
        }, lease
    if row == "V2-A15":
        root = _matrix_root(config, tested_sha)
        verified = _read_json(root / "v2-a15-verified.json")
        if (
            verified.get("tested_sha") != tested_sha
            or verified.get("start_cardinality") != 1
        ):
            raise RuntimeError("sealed v1 compatibility evidence is invalid")
        return {"sealed_transition": verified}, None
    if row == "V2-A18":
        return run_isolated_installer_drift_fixture(config, tested_sha), V2FixtureLease(
            row,
            (),
            _config_path(config, "evidence_root")
            / f"candidate-b-v2/a18-{tested_sha[:12]}",
            None,
            False,
        )
    if row == "V2-A20":
        protocol_operations = tuple(operation.value for operation in RunnerOperation)
        if protocol_operations != ("INFO", "LAUNCH", "OBSERVE", "STOP"):
            raise RuntimeError("Runner operation set changed")
        info = _runner_request(row, "INFO")
        return {"operations": protocol_operations, "runner_info": info}, None
    return _execute_lifecycle_row(row, config, tested_sha)


def _execute_security_row(
    config: Mapping[str, object], tested_sha: str
) -> tuple[dict[str, object], V2FixtureLease]:
    root = _PROOF_ROOT / f"candidate-b-v2-a2-{tested_sha[:12]}"
    evidence_root = _matrix_root(config, tested_sha) / "fixtures/V2-A2"
    main_evidence = evidence_root / "main.json"
    reporter_evidence = evidence_root / "reporter.json"
    reporter = root / "libexec/security-reporter.py"
    _prepare_probe_evidence(main_evidence)
    _install_matrix_reporter(reporter, reporter_evidence, exit_status=0)
    execution_identity, execution_hash = _identity("V2-A2", tested_sha)
    unit = f"{execution_identity}.service"
    canary_ld = root / "canary/ld"
    canary_python = root / "canary/python"
    canary_ld.mkdir(parents=True, exist_ok=True)
    canary_python.mkdir(parents=True, exist_ok=True)
    _reset_failed_if_loaded(unit)
    run_command(
        _systemd_run_argv(
            "V2-A2",
            execution_identity,
            execution_hash,
            (
                "/usr/bin/python3",
                "-I",
                "-B",
                "-c",
                _MAIN_SECURITY_CODE,
                str(main_evidence),
            ),
            reporter=reporter,
            wait=True,
            collect=False,
            workload_environment=(
                ("LD_LIBRARY_PATH", str(canary_ld)),
                ("PYTHONPATH", str(canary_python)),
            ),
        ),
        timeout_seconds=60,
    )
    main = _read_json(main_evidence)
    reporter_value = _read_json(reporter_evidence)
    train = pwd.getpwnam("labpilot-train")
    if (
        (main.get("uid"), main.get("gid")) != (train.pw_uid, train.pw_gid)
        or main.get("no_new_privs") != 1
        or any(main.get(name) != "0000000000000000" for name in ("cap_prm", "cap_eff", "cap_amb"))
        or main.get("environment")
        != {
            "LD_LIBRARY_PATH": str(canary_ld),
            "PYTHONPATH": str(canary_python),
        }
        or reporter_value.get("uid") != 0
        or reporter_value.get("gid") != 0
        or reporter_value.get("loader_environment_present") is not False
    ):
        raise RuntimeError("V2-A2 security boundary failed")
    records = _receipt_records(unit)
    if len([record for record in records if record.get("_UID") == "0"]) != 1:
        raise RuntimeError("V2-A2 root receipt provenance failed")
    return {
        "main": main,
        "reporter": reporter_value,
        "receipt_records": records,
    }, V2FixtureLease("V2-A2", (unit,), root, None, False)


def _execute_failure_neutrality_row(
    config: Mapping[str, object], tested_sha: str
) -> tuple[dict[str, object], V2FixtureLease]:
    root = _PROOF_ROOT / f"candidate-b-v2-a3-{tested_sha[:12]}"
    evidence_root = _matrix_root(config, tested_sha) / "fixtures/V2-A3"
    controls: dict[str, object] = {}
    units: list[str] = []
    for exit_status in (0, 71):
        control = f"exit{exit_status}"
        main_evidence = evidence_root / f"{control}-main.json"
        reporter_evidence = evidence_root / f"{control}-reporter.json"
        reporter = root / f"libexec/reporter-{control}.py"
        _prepare_probe_evidence(main_evidence)
        _install_matrix_reporter(reporter, reporter_evidence, exit_status=exit_status)
        execution_identity, execution_hash = _identity(
            f"V2-A3-{control}", tested_sha
        )
        unit = f"{execution_identity}.service"
        units.append(unit)
        _reset_failed_if_loaded(unit)
        run_command(
            _systemd_run_argv(
                "V2-A3",
                execution_identity,
                execution_hash,
                (
                    "/usr/bin/python3",
                    "-I",
                    "-B",
                    "-c",
                    _MAIN_SECURITY_CODE,
                    str(main_evidence),
                ),
                reporter=reporter,
                wait=True,
                collect=False,
            ),
            timeout_seconds=60,
        )
        properties = _properties(
            _text(
                run_command(
                    (
                        "systemctl",
                        "show",
                        unit,
                        "--no-pager",
                        "--property=Result",
                        "--property=ExecMainCode",
                        "--property=ExecMainStatus",
                    )
                )
            )
        )
        controls[control] = {
            "unit": unit,
            "manager": properties,
            "reporter": _read_json(reporter_evidence),
            "receipts": _receipt_records(unit),
        }
    exit0 = controls["exit0"]
    exit71 = controls["exit71"]
    if not isinstance(exit0, Mapping) or not isinstance(exit71, Mapping):
        raise RuntimeError("V2-A3 control evidence is invalid")
    if exit0.get("manager") != exit71.get("manager"):
        raise RuntimeError("V2-A3 reporter failure changed main result")
    for control_evidence in (exit0, exit71):
        reporter_value = control_evidence.get("reporter")
        if not isinstance(reporter_value, Mapping) or (
            reporter_value.get("service_result"),
            reporter_value.get("exit_code"),
            reporter_value.get("exit_status"),
        ) != ("success", "exited", "0"):
            raise RuntimeError("V2-A3 terminal variables changed")
    return {"controls": controls}, V2FixtureLease(
        "V2-A3", tuple(units), root, None, False
    )


def _execute_lifecycle_row(
    row: str, config: Mapping[str, object], tested_sha: str
) -> tuple[dict[str, object], V2FixtureLease]:
    del config
    execution_identity, execution_hash = _identity(row, tested_sha)
    unit = f"{execution_identity}.service"
    behavior = {
        "V2-A4": (
            (
                "/usr/bin/python3",
                "-I",
                "-B",
                "-c",
                (
                    "import os;os.write(1,b'{\"exit_code\":\"exited\","
                    "\"exit_status\":\"0\",\"schema_version\":"
                    "\"labpilot-terminal-receipt-v1\","
                    "\"service_result\":\"success\"}\\n')"
                ),
            ),
            TERMINAL_RECEIPT_REPORTER_PATH,
        ),
        "V2-A5": (("/usr/bin/true",), TERMINAL_RECEIPT_REPORTER_PATH),
        "V2-A6": (
            ("/usr/bin/python3", "-I", "-B", "-c", "raise SystemExit(7)"),
            TERMINAL_RECEIPT_REPORTER_PATH,
        ),
        "V2-A7": (("/usr/bin/sleep", "3600"), TERMINAL_RECEIPT_REPORTER_PATH),
        "V2-A8": (("/usr/bin/sleep", "3600"), TERMINAL_RECEIPT_REPORTER_PATH),
        "V2-A9": (("/usr/bin/sleep", "3600"), TERMINAL_RECEIPT_REPORTER_PATH),
        "V2-A10": (("/usr/bin/sleep", "3600"), TERMINAL_RECEIPT_REPORTER_PATH),
        "V2-A16": (("/usr/bin/true",), None),
        "V2-A17": (("/usr/bin/sleep", "3600"), TERMINAL_RECEIPT_REPORTER_PATH),
        "V2-A19": (
            (
                "/usr/bin/python3",
                "-I",
                "-B",
                "-c",
                "for i in range(200): print(f'workload-volume-{i}')",
            ),
            TERMINAL_RECEIPT_REPORTER_PATH,
        ),
    }.get(row)
    if behavior is None:
        raise RuntimeError("Candidate B v2 row has no closed mechanic")
    workload, reporter = behavior
    extra = ("--property=RuntimeMaxSec=1",) if row == "V2-A9" else ()
    _reset_failed_if_loaded(unit)
    run_command(
        _systemd_run_argv(
            row,
            execution_identity,
            execution_hash,
            workload,
            reporter=reporter,
            wait=row not in {"V2-A7", "V2-A8", "V2-A9", "V2-A10", "V2-A17"},
            extra_properties=extra,
            collect=row != "V2-A9",
        ),
        timeout_seconds=60,
    )
    if row in {"V2-A7", "V2-A8"}:
        signal_name = "SIGTERM" if row == "V2-A7" else "SIGKILL"
        run_command(("systemctl", "kill", f"--signal={signal_name}", unit))
    elif row == "V2-A10":
        _runner_request(row, "STOP", execution_identity, execution_hash)
    if row in {"V2-A7", "V2-A8", "V2-A9", "V2-A10"}:
        _wait_unit_terminal(unit)
    loaded_observation: dict[str, object] | None = None
    if row == "V2-A9":
        loaded_observation = _runner_request(
            row, "OBSERVE", execution_identity, execution_hash
        )
        _require_lifecycle_result(row, _physical_result(loaded_observation))
        _reset_failed_if_loaded(unit)
    observation_hash = execution_hash
    if row == "V2-A17":
        observation_hash = hashlib.sha256(b"V2-A17-wrong-H").hexdigest()
    observation = _runner_request(
        row, "OBSERVE", execution_identity, observation_hash
    )
    result = _physical_result(observation)
    _require_lifecycle_result(row, result)
    evidence: dict[str, object] = {"observation": observation}
    if row == "V2-A17":
        repeated = _runner_request(
            row, "OBSERVE", execution_identity, observation_hash
        )
        if repeated != observation or _start_cardinality(unit) != 1:
            raise RuntimeError("V2-A17 identity conflict was not sticky")
        evidence["repeated_observation"] = repeated
    if loaded_observation is not None:
        evidence["loaded_observation"] = loaded_observation
    if row == "V2-A4":
        records = _journal_records(
            (
                "journalctl",
                "--no-pager",
                "--output=json",
                f"_SYSTEMD_UNIT={unit}",
                "--lines=20",
            )
        )
        receipt = (
            '{"exit_code":"exited","exit_status":"0",'
            '"schema_version":"labpilot-terminal-receipt-v1",'
            '"service_result":"success"}'
        )
        matching = [record for record in records if record.get("MESSAGE") == receipt]
        if not any(record.get("_UID") == "0" for record in matching) or not any(
            record.get("_UID") not in {None, "0"} for record in matching
        ):
            raise RuntimeError("V2-A4 did not produce both root and workload receipts")
        evidence["receipt_provenance"] = matching
    return evidence, V2FixtureLease(
        row, (unit,), None, None, False
    )


def _systemd_run_argv(
    row: str,
    execution_identity: str,
    execution_hash: str,
    workload: tuple[str, ...],
    *,
    reporter: Path | None,
    wait: bool,
    extra_properties: tuple[str, ...] = (),
    workload_environment: tuple[tuple[str, str], ...] = (),
    collect: bool = True,
) -> tuple[str, ...]:
    if row not in V2_MATRIX_ROWS or not workload:
        raise ValueError("invalid fixed Candidate B v2 unit")
    command = [
        "systemd-run",
        "--system",
        "--no-ask-password",
        f"--unit={execution_identity}",
        f"--description=labpilot:{execution_hash}",
        "--service-type=exec",
        "--property=Restart=no",
        "--property=StandardOutput=journal",
        "--property=StandardError=journal",
        "--property=User=labpilot-train",
        "--property=Group=labpilot-train",
        "--property=NoNewPrivileges=yes",
        "--property=AmbientCapabilities=",
        "--property=CapabilityBoundingSet=",
        f"--property=UnsetEnvironment={_UNSET_ENVIRONMENT}",
        *extra_properties,
    ]
    if collect:
        command.insert(6, "--collect")
    if reporter is not None:
        command.append(
            f"--property=ExecStopPost=-+/usr/bin/python3 -I -B {reporter}"
        )
    if wait:
        command.append("--wait")
    command.extend(
        (
            "--",
            "/usr/bin/env",
            "-i",
            "PATH=/usr/bin:/bin",
            *(f"{key}={value}" for key, value in sorted(workload_environment)),
            *workload,
        )
    )
    return tuple(command)


def _runner_request(
    row: str,
    operation: Literal["INFO", "OBSERVE", "STOP"],
    execution_identity: str | None = None,
    execution_hash: str | None = None,
) -> dict[str, object]:
    return _runner_request_at_socket(
        row,
        Path("/run/labpilot-runner/control.sock"),
        operation,
        execution_identity,
        execution_hash,
    )


def _runner_request_at_socket(
    row: str,
    socket_path: Path,
    operation: Literal["INFO", "OBSERVE", "STOP"],
    execution_identity: str | None = None,
    execution_hash: str | None = None,
) -> dict[str, object]:
    if row not in V2_MATRIX_ROWS or operation not in {"INFO", "OBSERVE", "STOP"}:
        raise ValueError("unsupported fixed Runner matrix request")
    if socket_path != Path("/run/labpilot-runner/control.sock") and not socket_path.is_relative_to(
        _PROOF_ROOT
    ):
        raise ValueError("unsupported fixed Runner matrix socket")
    if operation != "INFO" and (
        execution_identity is None
        or execution_hash is None
        or re.fullmatch(r"labpilot-v1-[0-9a-f]{64}-[0-9a-f]{32}", execution_identity)
        is None
        or re.fullmatch(r"[0-9a-f]{64}", execution_hash) is None
    ):
        raise ValueError("invalid fixed Runner matrix identity")
    argv = (
        "runuser",
        "--user",
        "labpilot-controller",
        "--group",
        "labpilot-control",
        "--",
        _APP_PYTHON,
        "-E",
        "-s",
        "-B",
        "-c",
        _RUNNER_REQUEST,
        str(socket_path),
        operation,
        *(part for part in (execution_identity, execution_hash) if part is not None),
    )
    try:
        value = json.loads(_text(run_command(argv, timeout_seconds=20)))
    except json.JSONDecodeError as error:
        raise RuntimeError("Runner matrix response is invalid JSON") from error
    if type(value) is not dict:
        raise RuntimeError("Runner matrix response shape is invalid")
    return value


def _identity(row: str, tested_sha: str) -> tuple[str, str]:
    _require_sha(tested_sha)
    if not re.fullmatch(
        r"V2-A(?:3-exit(?:0|71)|12-(?:malformed|duplicate)|[1-9]|1[0-9]|20)",
        row,
    ):
        raise ValueError("invalid Candidate B v2 identity row")
    trial = hashlib.sha256(f"labpilot-v2-matrix\0{tested_sha}\0{row}\0x".encode()).hexdigest()
    nonce = hashlib.sha256(
        f"labpilot-v2-matrix\0{tested_sha}\0{row}\0nonce".encode()
    ).hexdigest()[:32]
    execution_hash = hashlib.sha256(
        f"labpilot-v2-matrix\0{tested_sha}\0{row}\0h".encode()
    ).hexdigest()
    return f"labpilot-v1-{trial}-{nonce}", execution_hash


def _wait_isolated_runner(socket_path: Path) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            response = _runner_request_at_socket("V2-A13", socket_path, "INFO")
        except (NativePreflightError, RuntimeError):
            time.sleep(0.1)
            continue
        result = response.get("result")
        if (
            response.get("outcome") == "ok"
            and isinstance(result, Mapping)
            and result.get("readiness_reason") == "ready"
            and result.get("runner_version") == "candidate-b-v2"
        ):
            return
        time.sleep(0.1)
    raise RuntimeError("isolated Candidate B v2 Runner did not become ready")


def _install_fixed_proof_asset(path: Path, content: bytes) -> None:
    if path not in _A12_ASSETS.values() or content != _a12_asset_bytes(
        next(name for name, candidate in _A12_ASSETS.items() if candidate == path)
    ):
        raise ValueError("unrecognized fixed proof asset")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    os.chown(path.parent, 0, 0)
    os.chmod(path.parent, 0o755)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or path.read_bytes() != content:
            raise RuntimeError("fixed proof asset drift")
        return
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o555)
    try:
        os.write(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chown(path, 0, 0)
    os.chmod(path, 0o555)


def _prepare_probe_evidence(path: Path) -> None:
    train = pwd.getpwnam("labpilot-train")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    if path.exists() or path.is_symlink():
        raise RuntimeError("matrix probe evidence already exists")
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    os.chown(path, train.pw_uid, train.pw_gid)


def _install_matrix_reporter(
    path: Path, evidence_path: Path, *, exit_status: int
) -> None:
    if (
        not path.is_relative_to(_PROOF_ROOT)
        or not evidence_path.is_absolute()
        or exit_status not in {0, 71}
    ):
        raise ValueError("matrix reporter authority is invalid")
    source = f"""#!/usr/bin/python3
import json
import os

payload = {{
    "exit_code": os.environ.get("EXIT_CODE"),
    "exit_status": os.environ.get("EXIT_STATUS"),
    "schema_version": "labpilot-terminal-receipt-v1",
    "service_result": os.environ.get("SERVICE_RESULT"),
}}
evidence = {{
    "uid": os.getuid(),
    "gid": os.getgid(),
    "loader_environment_present": any(
        name in os.environ
        for name in (
            "LD_AUDIT", "LD_DEBUG", "LD_LIBRARY_PATH", "LD_PRELOAD",
            "PYTHONHOME", "PYTHONPATH",
        )
    ),
    "service_result": payload["service_result"],
    "exit_code": payload["exit_code"],
    "exit_status": payload["exit_status"],
}}
with open({str(evidence_path)!r}, "x", encoding="ascii") as stream:
    json.dump(evidence, stream, separators=(",", ":"), sort_keys=True)
os.write(1, json.dumps(
    payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
).encode("ascii") + b"\\n")
raise SystemExit({exit_status})
""".encode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or path.read_bytes() != source:
            raise RuntimeError("matrix reporter asset drift")
        return
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o555)
    try:
        os.write(descriptor, source)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chown(path, 0, 0)
    os.chmod(path, 0o555)


def _receipt_records(unit: str) -> list[dict[str, object]]:
    records = _journal_records(
        (
            "journalctl",
            "--no-pager",
            "--output=json",
            f"_SYSTEMD_UNIT={unit}",
            "_UID=0",
            "_GID=0",
            "--lines=20",
        )
    )
    return [
        record
        for record in records
        if isinstance(record.get("MESSAGE"), str)
        and "labpilot-terminal-receipt-v1" in str(record["MESSAGE"])
    ]


def _a12_asset_bytes(variant: str) -> bytes:
    if variant == "malformed":
        body = "os.write(1,b'not-json\\n')"
    elif variant == "duplicate":
        body = (
            "record=(json.dumps({'exit_code':os.environ.get('EXIT_CODE'),"
            "'exit_status':os.environ.get('EXIT_STATUS'),"
            "'schema_version':'labpilot-terminal-receipt-v1',"
            "'service_result':os.environ.get('SERVICE_RESULT')},"
            "separators=(',',':'),sort_keys=True).encode('ascii')+b'\\n');"
            "os.write(1,record+record)"
        )
    else:
        raise ValueError("unsupported trusted receipt fixture")
    return f"#!/usr/bin/python3\nimport json\nimport os\n{body}\n".encode("ascii")


def _prepare_a18_fixture(fixture_root: Path, repository_root: Path) -> None:
    if fixture_root.exists() or fixture_root.is_symlink():
        raise RuntimeError("A18 fixture root already exists")
    source = repository_root / "deploy/libexec/labpilot-terminal-receipt-v1.py"
    if not source.is_file() or source.is_symlink():
        raise RuntimeError("A18 source reporter is invalid")
    app_asset = fixture_root / "runner/app/deploy/libexec" / source.name
    installed = fixture_root / "runner/libexec" / source.name
    app_asset.parent.mkdir(parents=True)
    installed.parent.mkdir(parents=True)
    shutil.copyfile(source, app_asset, follow_symlinks=False)
    shutil.copyfile(source, installed, follow_symlinks=False)


def _host_reporter_sha256() -> str:
    metadata = TERMINAL_RECEIPT_REPORTER_PATH.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("host production reporter is unsafe")
    return hashlib.sha256(TERMINAL_RECEIPT_REPORTER_PATH.read_bytes()).hexdigest()


def _matrix_root(config: Mapping[str, object], tested_sha: str) -> Path:
    _require_sha(tested_sha)
    return _config_path(config, "evidence_root") / "candidate-b-v2" / tested_sha


def _a14_compose_prefix(
    config: Mapping[str, object],
    tested_sha: str,
    *,
    project: str | None = None,
    override: Path | None = None,
    workspace: Path | None = None,
) -> tuple[str, ...]:
    _require_sha(tested_sha)
    repository_root = _config_path(config, "repository_root")
    fixture_root = _matrix_root(config, tested_sha) / "fixtures/V2-A14"
    selected_project = project or f"labpilot-v2-a14-{tested_sha[:12]}"
    selected_override = override or fixture_root / "compose.override.yaml"
    selected_workspace = workspace or fixture_root / "workspace"
    controller = pwd.getpwnam("labpilot-controller")
    train = pwd.getpwnam("labpilot-train")
    environment = {
        "LABPILOT_ATTEMPT_WORKSPACE_ROOT": str(selected_workspace),
        "LABPILOT_CONTROLLER_GID": str(controller.pw_gid),
        "LABPILOT_CONTROLLER_UID": str(controller.pw_uid),
        "LABPILOT_INSTALLATION_IDENTITY": f"candidate-b-v2-a14:{tested_sha}",
        "LABPILOT_LOCAL_ACTOR_ID": "candidate-b-v2-a14",
        "LABPILOT_REPOSITORY_HOST_ROOT": str(repository_root),
        "LABPILOT_TRAIN_GID": str(train.pw_gid),
    }
    return (
        "/usr/bin/env",
        *(f"{key}={value}" for key, value in sorted(environment.items())),
        "docker",
        "compose",
        "--project-directory",
        str(repository_root),
        "--file",
        str(repository_root / "compose.yaml"),
        "--file",
        str(selected_override),
        "--project-name",
        selected_project,
    )


def _run_a14_operation(
    config: Mapping[str, object], tested_sha: str, operation: str
) -> dict[str, object]:
    allowed = {
        "initialize",
        "launch_active",
        "wait_real_exit",
        "crash_after_observe",
        "startup_reconcile",
        "snapshot",
    }
    if operation not in allowed:
        raise ValueError("unsupported V2-A14 operation")
    prefix = _a14_compose_prefix(config, tested_sha)
    result = run_command(
        (
            *prefix,
            "run",
            "--rm",
            "--no-deps",
            "coordinator",
            "python",
            "-m",
            "tests.acceptance.slice2.candidate_b_v2_fixtures",
            "--a14-operation",
            operation,
            "--tested-sha",
            tested_sha,
            "--python-profile",
            _config_text(config, "python_profile"),
        ),
        timeout_seconds=180,
        max_output_bytes=64 * 1024,
    )
    try:
        value = json.loads(result.stdout.decode("utf-8", "strict"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("V2-A14 worker evidence is invalid") from error
    if type(value) is not dict:
        raise RuntimeError("V2-A14 worker evidence is invalid")
    return value


def _physical_result(response: Mapping[str, object]) -> Mapping[str, object]:
    result = response.get("result")
    if response.get("outcome") != "ok" or not isinstance(result, Mapping):
        raise RuntimeError("Runner matrix observation failed")
    return result


def _physical_kind(response: Mapping[str, object]) -> object:
    return _physical_result(response).get("kind")


def _require_lifecycle_result(row: str, result: Mapping[str, object]) -> None:
    expected: dict[str, dict[str, object]] = {
        "V2-A2": {"kind": "terminal_match", "exit_code": 0},
        "V2-A3": {"kind": "terminal_match", "exit_code": 0},
        "V2-A4": {"kind": "terminal_match", "exit_code": 0},
        "V2-A5": {"kind": "terminal_match", "exit_code": 0},
        "V2-A6": {"kind": "terminal_match", "exit_code": 7},
        "V2-A7": {"kind": "terminal_match", "termination_signal": 15},
        "V2-A8": {"kind": "terminal_match", "termination_signal": 9},
        "V2-A9": {
            "kind": "terminal_match",
            "result": "timeout",
            "exit_code": None,
            "termination_signal": None,
        },
        "V2-A10": {"kind": "terminal_match", "termination_signal": 15},
        "V2-A16": {
            "kind": "terminal_match",
            "exit_code": None,
            "termination_signal": None,
        },
        "V2-A17": {"kind": "identity_conflict"},
        "V2-A19": {"kind": "terminal_match", "exit_code": 0},
    }
    required = expected.get(row)
    if required is None or any(result.get(name) != value for name, value in required.items()):
        raise RuntimeError(f"{row} physical observation obligations failed")


def _wait_unit_terminal(unit: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        properties = _properties(
            _text(
                run_command(
                    (
                        "systemctl",
                        "show",
                        unit,
                        "--no-pager",
                        "--property=ActiveState",
                    )
                )
            )
        )
        if properties.get("ActiveState") in {"inactive", "failed"}:
            return
        time.sleep(0.1)
    raise RuntimeError(f"fixture unit did not become terminal: {unit}")


def _reset_failed_if_loaded(unit: str) -> None:
    loaded = _properties(
        _text(
            run_command(
                (
                    "systemctl",
                    "show",
                    unit,
                    "--no-pager",
                    "--property=LoadState",
                )
            )
        )
    )
    if loaded.get("LoadState") != "not-found":
        run_command(("systemctl", "reset-failed", unit))


def _journal_records(argv: tuple[str, ...]) -> list[dict[str, object]]:
    result = run_command(argv, timeout_seconds=20, max_output_bytes=64 * 1024)
    try:
        lines = result.stdout.decode("utf-8", "strict").splitlines()
        values = [json.loads(line) for line in lines]
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("fixture journal evidence is invalid") from error
    if len(values) > 256 or any(type(value) is not dict for value in values):
        raise RuntimeError("fixture journal evidence is invalid")
    return values


def _start_cardinality(unit: str) -> int:
    records = _journal_records(
        (
            "journalctl",
            "--no-pager",
            "--output=json",
            f"UNIT={unit}",
            "MESSAGE_ID=39f53479d3a045ac8e11786248231fbf",
        )
    )
    return sum(
        record.get("JOB_TYPE") == "start" and record.get("JOB_RESULT") == "done"
        for record in records
    )


def _properties(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in value.splitlines():
        name, separator, item = line.partition("=")
        if not separator or not name or name in result:
            raise RuntimeError("systemd property output is invalid")
        result[name] = item
    return result


def _required(value: Mapping[str, str], name: str) -> str:
    item = value.get(name)
    if not item:
        raise RuntimeError(f"required final stack field is missing: {name}")
    return item


def _required_text(value: Mapping[str, object], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item:
        raise RuntimeError(f"required fixture field is missing: {name}")
    return item


def _config_text(config: Mapping[str, object], name: str) -> str:
    value = config.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Candidate B v2 config is missing {name}")
    return value


def _config_path(config: Mapping[str, object], name: str) -> Path:
    value = Path(_config_text(config, name))
    if not value.is_absolute():
        raise ValueError(f"Candidate B v2 config path is not absolute: {name}")
    return value


def _require_sha(value: str) -> None:
    if _SHA.fullmatch(value) is None:
        raise ValueError("Candidate B v2 tested SHA is invalid")


def _text(result: CommandResult) -> str:
    try:
        return result.stdout.decode("utf-8", "strict").strip()
    except UnicodeError as error:
        raise RuntimeError("Candidate B v2 command output is invalid") from error


def _single_line(result: CommandResult) -> str:
    value = _text(result)
    if not value or len(value.splitlines()) != 1:
        raise RuntimeError("Candidate B v2 command did not return one value")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise RuntimeError("Candidate B v2 fixture evidence is invalid")
    return value


def _run_a14_worker(
    operation: str, tested_sha: str, python_profile: str
) -> dict[str, object]:
    from tests.acceptance.slice2 import native_worker

    operation_map = {
        "initialize": "initialize",
        "launch_active": "launch_active",
        "wait_real_exit": "wait_real_exit",
        "crash_after_observe": "crash_before_uow_commit",
        "startup_reconcile": "startup_reconcile",
        "snapshot": "snapshot",
    }
    try:
        worker_operation = operation_map[operation]
    except KeyError as error:
        raise ValueError("unsupported V2-A14 worker operation") from error
    _require_sha(tested_sha)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", python_profile) is None:
        raise ValueError("invalid V2-A14 Python profile")
    workspace_root = Path(_required_environment("LABPILOT_ATTEMPT_WORKSPACE_ROOT"))
    request = native_worker.WorkerRequest(
        "V2-A14", worker_operation, tested_sha, python_profile
    )
    runtime = object.__new__(native_worker.NativeWorkerRuntime)
    runtime.database_url = _required_environment("LABPILOT_DATABASE_URL")
    runtime.workspace_root = workspace_root
    runtime.content_store_root = Path("/var/lib/labpilot/content")
    runtime.runner_socket = Path(_required_environment("LABPILOT_RUNNER_SOCKET_PATH"))
    runtime.train_gid = int(_required_environment("LABPILOT_TRAIN_GID"))
    runtime.installation_identity = _required_environment(
        "LABPILOT_INSTALLATION_IDENTITY"
    )
    runtime.request = request
    runtime.root = (
        workspace_root / ".candidate-b-v2" / tested_sha / "V2-A14"
    )
    runtime.state_path = runtime.root / "state.json"

    original_profile = native_worker.fixture_profile

    def fixed_profile(scenario: str) -> native_worker.FixtureProfile:
        if scenario != "V2-A14":
            raise ValueError("A14 worker cannot select a formal scenario")
        return native_worker.FixtureProfile(duration_seconds=0.5)

    native_worker.fixture_profile = fixed_profile
    try:
        try:
            result = asyncio.run(runtime.execute())
        except native_worker._CrashBoundary:
            if operation != "crash_after_observe":
                raise
            return {"boundary": "crash_after_real_observe_before_commit"}
    finally:
        native_worker.fixture_profile = original_profile
    if operation == "crash_after_observe":
        raise RuntimeError("V2-A14 crash boundary was not reached")
    return result


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value or len(value.encode("utf-8")) > 4096:
        raise RuntimeError(f"V2-A14 environment is missing {name}")
    return value


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--a14-operation",
        choices=(
            "initialize",
            "launch_active",
            "wait_real_exit",
            "crash_after_observe",
            "startup_reconcile",
            "snapshot",
        ),
        required=True,
    )
    parser.add_argument("--tested-sha", required=True)
    parser.add_argument("--python-profile", required=True)
    args = parser.parse_args(argv)
    result = _run_a14_worker(
        args.a14_operation, args.tested_sha, args.python_profile
    )
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
