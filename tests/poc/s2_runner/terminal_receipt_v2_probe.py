from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

_EVIDENCE_ROOT = Path("/var/lib/labpilot-s2-evidence/terminal-receipt-m1")
_PROOF_ROOT = Path("/opt/labpilot-runner-mechanism-proof")
_PROOF_ASSET = _PROOF_ROOT / "libexec/terminal-receipt-probe.py"
_CANARY_LD = _PROOF_ROOT / "canary/ld"
_CANARY_PYTHON = _PROOF_ROOT / "canary/python"
_CANARY_MARKER = _EVIDENCE_ROOT / "import-canary-seen"
_REPORTER_EXIT = _PROOF_ROOT / "reporter-exit-code"
_PRODUCTION_PATHS = (
    Path("/opt/labpilot-runner/app"),
    Path("/opt/labpilot-runner/libexec"),
    Path("/etc/systemd/system/labpilot-runner.service"),
)
_LOADER_KEYS = (
    "LD_AUDIT",
    "LD_DEBUG",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "PYTHONHOME",
    "PYTHONPATH",
)
_UNSET_ENVIRONMENT = " ".join(_LOADER_KEYS)
_CONTROL_UNITS = {
    "labpilot-terminal-receipt-m1-exit0.service": "exit0",
    "labpilot-terminal-receipt-m1-exit71.service": "exit71",
}
_MAX_COMMAND_BYTES = 128 * 1024
_MAX_EVIDENCE_BYTES = 64 * 1024
_MAX_CURSOR_BYTES = 4096
_MAX_JOURNAL_RECORDS = 64
_SYSTEMD_STARTED_MESSAGE_ID = "39f53479d3a045ac8e11786248231fbf"
_MAX_TREE_NODES = 100_000
_MAX_TREE_BYTES = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class MechanismProbeResult:
    main_uid: int
    main_gid: int
    main_no_new_privs: int
    main_cap_prm: str
    main_cap_eff: str
    main_cap_amb: str
    main_environment: tuple[tuple[str, str], ...]
    reporter_uid: int
    reporter_gid: int
    service_result: str | None
    exit_code: str | None
    exit_status: str | None
    reporter_failure_neutral: bool
    loader_canary_seen: bool


def candidate_b_v2_probe_argv(unit: str) -> tuple[str, ...]:
    try:
        control = _CONTROL_UNITS[unit]
    except KeyError as error:
        raise ValueError("unsupported_probe_unit") from error
    environment = (
        ("LABPILOT_M1_CONTROL", control),
        ("LABPILOT_M1_MAIN_EVIDENCE", str(_EVIDENCE_ROOT / f"{control}-main.json")),
        ("LD_LIBRARY_PATH", str(_CANARY_LD)),
        ("PYTHONPATH", str(_CANARY_PYTHON)),
    )
    return (
        "systemd-run",
        "--system",
        "--no-ask-password",
        f"--unit={unit}",
        f"--description=labpilot-terminal-receipt-m1:{control}",
        "--service-type=exec",
        "--wait",
        "--property=Restart=no",
        "--property=StandardOutput=journal",
        "--property=StandardError=journal",
        "--property=User=labpilot-train",
        "--property=Group=labpilot-train",
        "--property=NoNewPrivileges=yes",
        "--property=AmbientCapabilities=",
        "--property=CapabilityBoundingSet=",
        f"--property=UnsetEnvironment={_UNSET_ENVIRONMENT}",
        f"--property=ExecStopPost=-+/usr/bin/python3 -I -B {_PROOF_ASSET}",
        "--",
        "/usr/bin/env",
        "-i",
        *(f"{key}={value}" for key, value in sorted(environment)),
        "/usr/bin/python3",
        "-I",
        "-B",
        str(_PROOF_ASSET),
        "main",
    )


def run_candidate_b_v2_probe(evidence_dir: Path) -> MechanismProbeResult:
    if evidence_dir != _EVIDENCE_ROOT:
        raise ValueError("unexpected_evidence_dir")
    if os.geteuid() != 0:
        raise PermissionError("root_required")
    host = _command(("hostname",), timeout=10).stdout.decode().strip()
    if host != "labpilot-s2":
        raise RuntimeError("unexpected_native_host")
    systemd_version = _command(("systemctl", "--version"), timeout=10).stdout.decode()
    first_version_line = systemd_version.splitlines()[0] if systemd_version else ""
    if not first_version_line.startswith("systemd 259 "):
        raise RuntimeError("systemd_259_required")
    train = pwd.getpwnam("labpilot-train")
    if _EVIDENCE_ROOT.exists():
        raise RuntimeError("evidence_dir_already_exists")
    if _PROOF_ROOT.exists() or _PROOF_ROOT.is_symlink():
        raise RuntimeError("proof_root_already_exists")

    _prepare_evidence_root(train.pw_uid, train.pw_gid)
    before = _production_fingerprint()
    _write_json(_EVIDENCE_ROOT / "production-before.json", before)
    _write_bytes(
        _EVIDENCE_ROOT / "systemd-version.txt",
        (first_version_line + "\n").encode("ascii"),
    )
    _install_probe_asset()
    _write_json(_EVIDENCE_ROOT / "proof-artifact.json", _artifact_metadata(_PROOF_ASSET))

    controls: dict[str, dict[str, object]] = {}
    cleanup: dict[str, object] = {"units": [], "proof_root_removed": False}
    try:
        for unit, control in _CONTROL_UNITS.items():
            controls[control] = _run_control(unit, control)
        result = _mechanism_result(controls)
        if (result.main_uid, result.main_gid) != (train.pw_uid, train.pw_gid):
            raise RuntimeError("main_identity_failed")
        for control, evidence in controls.items():
            _seal_main_evidence(control)
            _write_json(_EVIDENCE_ROOT / f"{control}-control.json", evidence)
    finally:
        cleanup = _cleanup_probe()

    after = _production_fingerprint()
    _write_json(_EVIDENCE_ROOT / "production-after.json", after)
    if after != before:
        raise RuntimeError("production_runner_mutated")
    _write_json(_EVIDENCE_ROOT / "cleanup.json", cleanup)
    _write_json(
        _EVIDENCE_ROOT / "probe-result.json",
        {
            "repository_head": _repository_head(),
            "probe_source_sha256": _sha256(Path(__file__).resolve()),
            "hostname": host,
            "systemd_version": first_version_line,
            "units": list(_CONTROL_UNITS),
            "result": asdict(result),
            "production_runner_mutated": False,
        },
    )
    _write_manifest()
    return result


def _parse_wait_report(
    *, stdout: bytes | str, stderr: bytes | str, return_code: int
) -> dict[str, object]:
    captured: list[bytes] = []
    for value in (stdout, stderr):
        if isinstance(value, str):
            try:
                encoded = value.encode("ascii")
            except UnicodeEncodeError as error:
                raise RuntimeError("wait_report_unrecognized") from error
        elif isinstance(value, bytes):
            encoded = value
        else:
            raise TypeError("wait_report_must_be_bytes_or_text")
        if len(encoded) > _MAX_COMMAND_BYTES:
            raise RuntimeError("wait_report_too_large")
        captured.append(encoded)
    stdout_bytes, stderr_bytes = captured
    if stdout_bytes:
        raise RuntimeError("wait_report_unrecognized")
    if isinstance(return_code, bool) or not isinstance(return_code, int):
        raise TypeError("wait_report_return_code_must_be_integer")
    if not 0 <= return_code <= 255:
        raise RuntimeError("wait_report_unrecognized")
    try:
        lines = stderr_bytes.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise RuntimeError("wait_report_unrecognized") from error

    result_prefix = "          Finished with result: "
    main_prefix = "Main processes terminated with: code="
    result_lines = [line for line in lines if line.startswith(result_prefix)]
    main_lines = [line for line in lines if line.startswith(main_prefix)]
    if not result_lines or not main_lines:
        raise RuntimeError("wait_report_incomplete")
    if len(result_lines) != 1 or len(main_lines) != 1:
        raise RuntimeError("wait_report_ambiguous")

    service_result = result_lines[0][len(result_prefix) :]
    main_tail = main_lines[0][len(main_prefix) :]
    try:
        main_code, main_status = main_tail.split(", status=", 1)
    except ValueError as error:
        raise RuntimeError("wait_report_incomplete") from error
    if (service_result, main_code, main_status) != ("success", "exited", "0/SUCCESS"):
        raise RuntimeError("wait_report_unrecognized")
    return {
        "service_result": service_result,
        "main_code": main_code,
        "main_status": "0",
        "systemd_run_return_code": return_code,
    }


def _mechanism_result(controls: Mapping[str, Mapping[str, object]]) -> MechanismProbeResult:
    try:
        exit0 = controls["exit0"]
        exit71 = controls["exit71"]
        main0 = _object(exit0["main"])
        main71 = _object(exit71["main"])
        reporter0 = _object(exit0["reporter"])
        reporter71 = _object(exit71["reporter"])
        wait0 = _object(exit0["wait_report"])
        wait71 = _object(exit71["wait_report"])
    except (KeyError, TypeError) as error:
        raise RuntimeError("incomplete_probe_evidence") from error

    for main in (main0, main71):
        if (
            _integer(main.get("uid")) <= 0
            or _integer(main.get("gid")) <= 0
            or main.get("no_new_privs") != 1
            or any(
                main.get(field) != "0000000000000000"
                for field in ("cap_prm", "cap_eff", "cap_amb")
            )
        ):
            raise RuntimeError("main_security_boundary_failed")
    if main0 != main71:
        raise RuntimeError("main_control_evidence_differed")

    expected_environment = [
        ["LD_LIBRARY_PATH", str(_CANARY_LD)],
        ["PYTHONPATH", str(_CANARY_PYTHON)],
    ]
    if main0.get("environment") != expected_environment:
        raise RuntimeError("workload_environment_not_preserved")

    for reporter in (reporter0, reporter71):
        if reporter.get("_UID") != "0" or reporter.get("_GID") != "0":
            raise RuntimeError("reporter_provenance_failed")
        if reporter.get("_spoof_UID") in (None, "0") or reporter.get("_spoof_GID") in (None, "0"):
            raise RuntimeError("anti_spoof_proof_failed")
        if reporter.get("loader_environment_present") is not False:
            raise RuntimeError("loader_canary_seen")
        if reporter.get("import_canary_seen") is not False:
            raise RuntimeError("loader_canary_seen")
    if main0.get("import_canary_seen") is not False:
        raise RuntimeError("loader_canary_seen")

    terminal0 = tuple(reporter0.get(key) for key in ("service_result", "exit_code", "exit_status"))
    terminal71 = tuple(
        reporter71.get(key) for key in ("service_result", "exit_code", "exit_status")
    )
    if terminal0 != terminal71:
        raise RuntimeError("terminal_variables_differ")
    if terminal0 != ("success", "exited", "0"):
        raise RuntimeError("terminal_variables_unexpected")

    wait_keys = {
        "service_result",
        "main_code",
        "main_status",
        "systemd_run_return_code",
    }
    if set(wait0) != wait_keys or set(wait71) != wait_keys:
        raise RuntimeError("incomplete_probe_evidence")
    if wait0 != wait71:
        raise RuntimeError("reporter_not_failure_neutral")
    if wait0 != {
        "service_result": "success",
        "main_code": "exited",
        "main_status": "0",
        "systemd_run_return_code": 0,
    }:
        raise RuntimeError("main_result_unexpected")

    environment = tuple((str(key), str(value)) for key, value in expected_environment)
    return MechanismProbeResult(
        main_uid=_integer(main0["uid"]),
        main_gid=_integer(main0["gid"]),
        main_no_new_privs=_integer(main0["no_new_privs"]),
        main_cap_prm=str(main0["cap_prm"]),
        main_cap_eff=str(main0["cap_eff"]),
        main_cap_amb=str(main0["cap_amb"]),
        main_environment=environment,
        reporter_uid=0,
        reporter_gid=0,
        service_result=_optional_string(reporter0.get("service_result")),
        exit_code=_optional_string(reporter0.get("exit_code")),
        exit_status=_optional_string(reporter0.get("exit_status")),
        reporter_failure_neutral=True,
        loader_canary_seen=False,
    )


def _run_control(unit: str, control: str) -> dict[str, object]:
    exit_code = 0 if control == "exit0" else 71
    _write_control_exit(exit_code)
    main_path = _EVIDENCE_ROOT / f"{control}-main.json"
    if main_path.exists() or main_path.is_symlink():
        raise RuntimeError("main_evidence_already_exists")
    descriptor = os.open(main_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    train = pwd.getpwnam("labpilot-train")
    os.chown(main_path, train.pw_uid, train.pw_gid)
    _command(("systemctl", "reset-failed", unit), timeout=10, allowed=(0, 1, 5))
    cursor = _capture_journal_cursor()
    _write_bytes(
        _EVIDENCE_ROOT / f"{control}-journal-cursor.txt",
        (cursor + "\n").encode("ascii"),
    )
    launched = _command(candidate_b_v2_probe_argv(unit), timeout=60, allowed=(0, 1))
    wait_report = _parse_wait_report(
        stdout=launched.stdout,
        stderr=launched.stderr,
        return_code=launched.returncode,
    )
    _write_bytes(_EVIDENCE_ROOT / f"{control}-systemd-run.stdout", launched.stdout)
    _write_bytes(_EVIDENCE_ROOT / f"{control}-systemd-run.stderr", launched.stderr)
    _write_json(
        _EVIDENCE_ROOT / f"{control}-systemd-run.json",
        {
            "return_code": launched.returncode,
            "stdout_sha256": hashlib.sha256(launched.stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(launched.stderr).hexdigest(),
        },
    )
    _write_json(_EVIDENCE_ROOT / f"{control}-wait-report.json", wait_report)
    manager_records = _manager_journal_records(unit, cursor)
    _write_json(
        _EVIDENCE_ROOT / f"{control}-manager-records.json",
        {"cursor": cursor, "records": manager_records},
    )
    manager = _manager_lifecycle(unit, manager_records)
    _write_json(_EVIDENCE_ROOT / f"{control}-manager-lifecycle.json", manager)
    reporter_records = _reporter_journal_records(unit, cursor)
    _write_json(
        _EVIDENCE_ROOT / f"{control}-reporter-records.json",
        {"cursor": cursor, "records": reporter_records},
    )
    proc_boot_id_raw = (
        Path("/proc/sys/kernel/random/boot_id")
        .read_text(encoding="ascii")
        .removesuffix("\n")
    )
    reporter = _reporter_evidence(
        unit,
        reporter_records,
        manager,
        proc_boot_id_raw=proc_boot_id_raw,
    )
    _write_json(_EVIDENCE_ROOT / f"{control}-reporter-evidence.json", reporter)
    correlation = {
        "cursor": cursor,
        "manager_invocation_raw": manager["manager_invocation_raw"],
        "reporter_invocation_raw": reporter["reporter_invocation_raw"],
        "proc_boot_id_raw": reporter["proc_boot_id_raw"],
        "manager_boot_raw": manager["manager_boot_raw"],
        "reporter_boot_raw": reporter["reporter_boot_raw"],
        "boot_id_normalized": reporter["boot_id_normalized"],
        "matched": True,
    }
    _write_json(_EVIDENCE_ROOT / f"{control}-correlation.json", correlation)
    unit_properties = _properties(
        _command(
            (
                "systemctl",
                "show",
                unit,
                "--no-pager",
                "--property=LoadState",
                "--property=ActiveState",
                "--property=SubState",
                "--property=Result",
                "--property=ExecMainCode",
                "--property=ExecMainStatus",
                "--property=InvocationID",
            ),
            timeout=10,
        ).stdout.decode()
    )
    main = _read_json(main_path)
    return {
        "systemd_run": {
            "return_code": launched.returncode,
            "stdout_sha256": hashlib.sha256(launched.stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(launched.stderr).hexdigest(),
        },
        "unit_name": unit,
        "journal_cursor": cursor,
        "invocation_id": manager["manager_invocation_raw"],
        "main": main,
        "manager": manager,
        "reporter": reporter,
        "correlation": correlation,
        "wait_report": wait_report,
        "unit_diagnostic": unit_properties,
        "reporter_exit": exit_code,
    }


def _main_entrypoint() -> int:
    control = os.environ.get("LABPILOT_M1_CONTROL")
    if control not in {"exit0", "exit71"}:
        return 64
    expected_path = _EVIDENCE_ROOT / f"{control}-main.json"
    if os.environ.get("LABPILOT_M1_MAIN_EVIDENCE") != str(expected_path):
        return 65
    status = _proc_status()
    environment = [[key, os.environ.get(key, "")] for key in ("LD_LIBRARY_PATH", "PYTHONPATH")]
    import_seen = _CANARY_MARKER.exists() or importlib.util.find_spec(
        "labpilot_m1_import_canary"
    ) is not None
    evidence = {
        "uid": os.getuid(),
        "gid": os.getgid(),
        "no_new_privs": int(status["NoNewPrivs"], 10),
        "cap_prm": status["CapPrm"],
        "cap_eff": status["CapEff"],
        "cap_amb": status["CapAmb"],
        "environment": environment,
        "import_canary_seen": import_seen,
    }
    _write_existing_main_file(expected_path, evidence)
    os.write(
        1,
        _reporter_message(
            uid=0,
            gid=0,
            loader_present=False,
            import_seen=False,
            terminal_values=("success", "exited", "0"),
        ),
    )
    return 0


def _reporter_entrypoint() -> int:
    loader_present = any(name in os.environ for name in _LOADER_KEYS)
    import_seen = _CANARY_MARKER.exists() or importlib.util.find_spec(
        "labpilot_m1_import_canary"
    ) is not None
    os.write(
        1,
        _reporter_message(
            uid=os.getuid(),
            gid=os.getgid(),
            loader_present=loader_present,
            import_seen=import_seen,
        ),
    )
    try:
        value = _REPORTER_EXIT.read_text(encoding="ascii").strip()
    except OSError:
        return 72
    return int(value) if value in {"0", "71"} else 73


def _reporter_message(
    *,
    uid: int,
    gid: int,
    loader_present: bool,
    import_seen: bool,
    terminal_values: tuple[str | None, str | None, str | None] | None = None,
) -> bytes:
    service_result, exit_code, exit_status = terminal_values or (
        _bounded_optional_environment("SERVICE_RESULT"),
        _bounded_optional_environment("EXIT_CODE"),
        _bounded_optional_environment("EXIT_STATUS"),
    )
    service_result, exit_code, exit_status = (
        _bounded_optional_value(value) for value in (service_result, exit_code, exit_status)
    )
    payload = {
        "event": "labpilot_terminal_receipt_m1_reporter",
        "exit_code": exit_code,
        "exit_status": exit_status,
        "import_canary_seen": import_seen,
        "loader_environment_present": loader_present,
        "reporter_gid": gid,
        "reporter_uid": uid,
        "service_result": service_result,
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    ) + b"\n"
    if len(encoded) > 4096:
        raise RuntimeError("reporter_record_too_large")
    return encoded


def _normalize_boot_id(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(
        r"(?:[0-9A-Fa-f]{32}|[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-"
        r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})",
        value,
    ) is None:
        raise RuntimeError("invalid_boot_id")
    return uuid.UUID(value).hex


def _parse_journal_cursor(*, stdout: bytes, stderr: bytes) -> str:
    if not isinstance(stdout, bytes) or not isinstance(stderr, bytes):
        raise TypeError("journal_cursor_output_must_be_bytes")
    if len(stdout) > _MAX_CURSOR_BYTES or len(stderr) > _MAX_CURSOR_BYTES:
        raise RuntimeError("journal_cursor_invalid")
    try:
        lines = (stdout + stderr).decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise RuntimeError("journal_cursor_invalid") from error
    cursor_lines = [
        line.removeprefix("-- cursor: ")
        for line in lines
        if line.startswith("-- cursor: ")
    ]
    if any(
        line and line != "-- No entries --" and not line.startswith("-- cursor: ")
        for line in lines
    ):
        raise RuntimeError("journal_cursor_invalid")
    if len(cursor_lines) != 1 or re.fullmatch(
        r"s=[0-9a-f]{32};i=[0-9a-f]+;b=[0-9a-f]{32};m=[0-9a-f]+;"
        r"t=[0-9a-f]+;x=[0-9a-f]+",
        cursor_lines[0] if cursor_lines else "",
    ) is None:
        raise RuntimeError("journal_cursor_invalid")
    return cursor_lines[0]


def _capture_journal_cursor() -> str:
    result = _command(
        ("journalctl", "--no-pager", "--show-cursor", "--lines=0"),
        timeout=10,
    )
    return _parse_journal_cursor(stdout=result.stdout, stderr=result.stderr)


def _validate_journal_identity(unit: str, cursor: str) -> None:
    if unit not in _CONTROL_UNITS:
        raise ValueError("unsupported_probe_unit")
    _parse_journal_cursor(stdout=f"-- cursor: {cursor}\n".encode("ascii"), stderr=b"")


def _manager_journal_argv(unit: str, cursor: str) -> tuple[str, ...]:
    _validate_journal_identity(unit, cursor)
    return (
        "journalctl",
        "--no-pager",
        "--output=json",
        f"--after-cursor={cursor}",
        f"--lines={_MAX_JOURNAL_RECORDS + 1}",
        f"UNIT={unit}",
        "_PID=1",
        "_SYSTEMD_UNIT=init.scope",
        "SYSLOG_IDENTIFIER=systemd",
    )


def _reporter_journal_argv(unit: str, cursor: str) -> tuple[str, ...]:
    _validate_journal_identity(unit, cursor)
    return (
        "journalctl",
        "--no-pager",
        "--output=json",
        f"--after-cursor={cursor}",
        f"--lines={_MAX_JOURNAL_RECORDS + 1}",
        f"_SYSTEMD_UNIT={unit}",
    )


def _parse_journal_records(output: bytes) -> list[dict[str, object]]:
    if not isinstance(output, bytes):
        raise TypeError("journal_output_must_be_bytes")
    if len(output) > _MAX_EVIDENCE_BYTES:
        raise RuntimeError("journal_output_bound_exceeded")
    try:
        lines = output.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise RuntimeError("journal_record_invalid") from error
    if len(lines) > _MAX_JOURNAL_RECORDS:
        raise RuntimeError("journal_record_bound_exceeded")
    records = [_json_object(line) for line in lines]
    if any(record is None for record in records):
        raise RuntimeError("journal_record_invalid")
    return [record for record in records if record is not None]


def _manager_journal_records(unit: str, cursor: str) -> list[dict[str, object]]:
    result = _command(_manager_journal_argv(unit, cursor), timeout=20)
    return _parse_journal_records(result.stdout)


def _reporter_journal_records(unit: str, cursor: str) -> list[dict[str, object]]:
    result = _command(_reporter_journal_argv(unit, cursor), timeout=20)
    return _parse_journal_records(result.stdout)


def _manager_lifecycle(
    unit: str, records: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    admitted: list[Mapping[str, object]] = []
    for record in records:
        invocation = record.get("INVOCATION_ID")
        boot_id = record.get("_BOOT_ID")
        if (
            record.get("UNIT") != unit
            or record.get("_PID") != "1"
            or record.get("_SYSTEMD_UNIT") != "init.scope"
            or record.get("SYSLOG_IDENTIFIER") != "systemd"
            or record.get("MESSAGE_ID") != _SYSTEMD_STARTED_MESSAGE_ID
            or record.get("JOB_TYPE") != "start"
            or record.get("JOB_RESULT") != "done"
            or not isinstance(invocation, str)
            or re.fullmatch(r"[0-9A-Fa-f]{32}", invocation) is None
            or not isinstance(boot_id, str)
        ):
            continue
        try:
            _normalize_boot_id(boot_id)
        except RuntimeError:
            continue
        admitted.append(record)
    if len(admitted) != 1:
        raise RuntimeError("manager_started_cardinality_failed")
    record = admitted[0]
    invocation = str(record["INVOCATION_ID"])
    boot_id = str(record["_BOOT_ID"])
    return {
        key: record[key]
        for key in (
            "UNIT",
            "_PID",
            "_SYSTEMD_UNIT",
            "SYSLOG_IDENTIFIER",
            "MESSAGE_ID",
            "JOB_TYPE",
            "JOB_RESULT",
            "INVOCATION_ID",
            "_BOOT_ID",
        )
    } | {
        "manager_invocation_raw": invocation,
        "manager_boot_raw": boot_id,
        "boot_id_normalized": _normalize_boot_id(boot_id),
    }


def _reporter_evidence(
    unit: str,
    records: Sequence[Mapping[str, object]],
    manager: Mapping[str, object],
    *,
    proc_boot_id_raw: str,
) -> dict[str, object]:
    manager_invocation = manager.get("manager_invocation_raw")
    manager_boot_raw = manager.get("manager_boot_raw")
    if not isinstance(manager_invocation, str) or not isinstance(manager_boot_raw, str):
        raise RuntimeError("manager_evidence_invalid")
    boot_id_normalized = _normalize_boot_id(proc_boot_id_raw)
    if _normalize_boot_id(manager_boot_raw) != boot_id_normalized:
        raise RuntimeError("manager_boot_provenance_failed")

    root: list[tuple[Mapping[str, object], dict[str, object], str]] = []
    spoof: list[tuple[Mapping[str, object], dict[str, object], str]] = []
    for record in records:
        message_text = record.get("MESSAGE")
        message = _json_object(message_text)
        reporter_boot = record.get("_BOOT_ID")
        if (
            not message
            or message.get("event") != "labpilot_terminal_receipt_m1_reporter"
            or record.get("_SYSTEMD_UNIT") != unit
            or record.get("_SYSTEMD_INVOCATION_ID") != manager_invocation
            or not isinstance(reporter_boot, str)
        ):
            continue
        try:
            reporter_boot_normalized = _normalize_boot_id(reporter_boot)
        except RuntimeError:
            continue
        if reporter_boot_normalized != boot_id_normalized:
            continue
        item = (record, message, str(message_text))
        if record.get("_UID") == "0" and record.get("_GID") == "0":
            root.append(item)
        else:
            spoof.append(item)
    if len(root) != 1 or len(spoof) != 1:
        raise RuntimeError("reporter_journal_cardinality_failed")
    root_record, root_message, root_text = root[0]
    spoof_record, _spoof_message, spoof_text = spoof[0]
    if root_text != spoof_text:
        raise RuntimeError("anti_spoof_message_not_identical")
    reporter_boot_raw = str(root_record["_BOOT_ID"])
    return {
        "_UID": root_record.get("_UID"),
        "_GID": root_record.get("_GID"),
        "_spoof_UID": spoof_record.get("_UID"),
        "_spoof_GID": spoof_record.get("_GID"),
        "_SYSTEMD_UNIT": root_record.get("_SYSTEMD_UNIT"),
        "_SYSTEMD_INVOCATION_ID": root_record.get("_SYSTEMD_INVOCATION_ID"),
        "_BOOT_ID": root_record.get("_BOOT_ID"),
        "proc_boot_id_raw": proc_boot_id_raw,
        "manager_invocation_raw": manager_invocation,
        "manager_boot_raw": manager_boot_raw,
        "reporter_invocation_raw": root_record.get("_SYSTEMD_INVOCATION_ID"),
        "reporter_boot_raw": reporter_boot_raw,
        "journal_boot_id_raw": reporter_boot_raw,
        "boot_id_normalized": boot_id_normalized,
        **root_message,
    }


def _prepare_evidence_root(train_uid: int, train_gid: int) -> None:
    _EVIDENCE_ROOT.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    os.chown(_EVIDENCE_ROOT.parent, 0, 0)
    os.chmod(_EVIDENCE_ROOT.parent, 0o755)
    _EVIDENCE_ROOT.mkdir(mode=0o711)
    os.chown(_EVIDENCE_ROOT, 0, 0)
    os.chmod(_EVIDENCE_ROOT, 0o711)
    marker_parent = _CANARY_MARKER.parent
    if marker_parent != _EVIDENCE_ROOT:
        raise RuntimeError("unexpected_canary_parent")
    if train_uid == 0 or train_gid == 0:
        raise RuntimeError("training_identity_is_root")


def _install_probe_asset() -> None:
    (_PROOF_ROOT / "libexec").mkdir(parents=True, mode=0o755)
    _CANARY_LD.mkdir(parents=True, mode=0o755)
    _CANARY_PYTHON.mkdir(parents=True, mode=0o755)
    source = Path(__file__).resolve()
    shutil.copyfile(source, _PROOF_ASSET, follow_symlinks=False)
    canary = _CANARY_PYTHON / "sitecustomize.py"
    canary.write_text(
        "from pathlib import Path\n"
        f"Path({str(_CANARY_MARKER)!r}).write_text('seen\\n', encoding='ascii')\n",
        encoding="ascii",
    )
    module_canary = _CANARY_PYTHON / "labpilot_m1_import_canary.py"
    module_canary.write_text("CANARY = True\n", encoding="ascii")
    for root, directories, files in os.walk(_PROOF_ROOT):
        os.chown(root, 0, 0)
        os.chmod(root, 0o755)
        for name in directories:
            path = Path(root) / name
            os.chown(path, 0, 0)
            os.chmod(path, 0o755)
        for name in files:
            path = Path(root) / name
            os.chown(path, 0, 0)
            os.chmod(path, 0o644)
    _assert_safe_proof_asset()


def _assert_safe_proof_asset() -> None:
    for path in (_PROOF_ROOT, _PROOF_ROOT / "libexec", _PROOF_ASSET):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_gid != 0:
            raise RuntimeError("unsafe_proof_artifact")
        if metadata.st_mode & 0o022:
            raise RuntimeError("unsafe_proof_artifact")
    if not stat.S_ISREG(_PROOF_ASSET.lstat().st_mode):
        raise RuntimeError("unsafe_proof_artifact")


def _write_control_exit(value: int) -> None:
    encoded = f"{value}\n".encode("ascii")
    temporary = _PROOF_ROOT / ".reporter-exit-code.tmp"
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chown(temporary, 0, 0)
    os.replace(temporary, _REPORTER_EXIT)


def _cleanup_probe() -> dict[str, object]:
    units: list[str] = []
    for unit in _CONTROL_UNITS:
        _command(("systemctl", "stop", unit), timeout=10, allowed=(0, 1, 5))
        _command(("systemctl", "reset-failed", unit), timeout=10, allowed=(0, 1, 5))
        units.append(unit)
    if _PROOF_ROOT.exists() or _PROOF_ROOT.is_symlink():
        resolved = _PROOF_ROOT.resolve(strict=True)
        if resolved != _PROOF_ROOT or _PROOF_ROOT.is_symlink():
            raise RuntimeError("unexpected_proof_root_during_cleanup")
        metadata = _PROOF_ROOT.lstat()
        if metadata.st_uid != 0 or metadata.st_gid != 0:
            raise RuntimeError("unexpected_proof_root_during_cleanup")
        shutil.rmtree(_PROOF_ROOT)
    return {"units": units, "proof_root_removed": not _PROOF_ROOT.exists()}


def _seal_main_evidence(control: str) -> None:
    path = _EVIDENCE_ROOT / f"{control}-main.json"
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError("unsafe_main_evidence")
    os.chown(path, 0, 0)
    os.chmod(path, 0o600)


def _production_fingerprint() -> dict[str, object]:
    service = _command(
        (
            "systemctl",
            "show",
            "labpilot-runner.service",
            "--no-pager",
            "--property=LoadState",
            "--property=ActiveState",
            "--property=SubState",
            "--property=InvocationID",
            "--property=FragmentPath",
            "--property=ExecStart",
        ),
        timeout=10,
        allowed=(0, 1, 4),
    )
    return {
        "paths": {str(path): _tree_fingerprint(path) for path in _PRODUCTION_PATHS},
        "service_return_code": service.returncode,
        "service_stdout_sha256": hashlib.sha256(service.stdout).hexdigest(),
        "service_stdout": service.stdout.decode(errors="strict"),
    }


def _tree_fingerprint(path: Path) -> dict[str, object]:
    if not path.exists() and not path.is_symlink():
        return {"exists": False}
    digest = hashlib.sha256()
    nodes = 0
    total_bytes = 0
    stack = [(path, Path("."))]
    while stack:
        current, relative = stack.pop()
        metadata = current.lstat()
        nodes += 1
        if nodes > _MAX_TREE_NODES:
            raise RuntimeError("production_tree_node_bound_exceeded")
        digest.update(
            (
                f"{relative.as_posix()}\0{stat.S_IFMT(metadata.st_mode):o}\0"
                f"{stat.S_IMODE(metadata.st_mode):o}\0{metadata.st_uid}\0{metadata.st_gid}\0"
                f"{metadata.st_size}\0{metadata.st_mtime_ns}\0"
            ).encode()
        )
        if stat.S_ISLNK(metadata.st_mode):
            digest.update(os.readlink(current).encode("utf-8"))
        elif stat.S_ISREG(metadata.st_mode):
            total_bytes += metadata.st_size
            if total_bytes > _MAX_TREE_BYTES:
                raise RuntimeError("production_tree_byte_bound_exceeded")
            with current.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        elif stat.S_ISDIR(metadata.st_mode):
            children = sorted(current.iterdir(), key=lambda item: item.name, reverse=True)
            stack.extend((child, relative / child.name) for child in children)
    return {
        "exists": True,
        "sha256": digest.hexdigest(),
        "nodes": nodes,
        "regular_file_bytes": total_bytes,
    }


def _write_manifest() -> None:
    lines = []
    for path in sorted(_EVIDENCE_ROOT.iterdir(), key=lambda item: item.name):
        if path.name == "SHA256SUMS" or not path.is_file() or path.is_symlink():
            continue
        lines.append(f"{_sha256(path)}  {path.name}\n")
    _write_bytes(_EVIDENCE_ROOT / "SHA256SUMS", "".join(lines).encode("ascii"))


def _write_existing_main_file(path: Path, value: Mapping[str, object]) -> None:
    encoded = _json_bytes(value)
    descriptor = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError("unsafe_main_evidence")
        if os.write(descriptor, encoded) != len(encoded):
            raise OSError("short_evidence_write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    _write_bytes(path, _json_bytes(value))


def _write_bytes(path: Path, encoded: bytes) -> None:
    if len(encoded) > _MAX_EVIDENCE_BYTES:
        raise RuntimeError("evidence_file_too_large")
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        if os.write(descriptor, encoded) != len(encoded):
            raise OSError("short_evidence_write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chown(path, 0, 0)
    os.chmod(path, 0o600)


def _read_json(path: Path) -> dict[str, object]:
    if path.lstat().st_size > _MAX_EVIDENCE_BYTES:
        raise RuntimeError("evidence_file_too_large")
    value = json.loads(path.read_text(encoding="utf-8"))
    return _object(value)


def _json_bytes(value: Mapping[str, object]) -> bytes:
    encoded = (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("ascii")
    if len(encoded) > _MAX_EVIDENCE_BYTES:
        raise RuntimeError("evidence_file_too_large")
    return encoded


def _command(
    argv: Sequence[str], *, timeout: float, allowed: tuple[int, ...] = (0,)
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        tuple(argv),
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
    )
    if len(result.stdout) > _MAX_COMMAND_BYTES or len(result.stderr) > _MAX_COMMAND_BYTES:
        raise RuntimeError("command_output_bound_exceeded")
    if result.returncode not in allowed:
        raise RuntimeError(f"command_failed:{argv[0]}:{result.returncode}")
    return result


def _proc_status() -> dict[str, str]:
    values = {}
    for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values[key] = value.strip()
    for key in ("NoNewPrivs", "CapPrm", "CapEff", "CapAmb"):
        if key not in values:
            raise RuntimeError("proc_status_field_missing")
    return values


def _bounded_optional_environment(name: str) -> str | None:
    return _bounded_optional_value(os.environ.get(name))


def _bounded_optional_value(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value.encode("ascii", errors="strict")) > 64 or any(
        character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:+-"
        for character in value
    ):
        raise RuntimeError("invalid_terminal_environment")
    return value


def _properties(value: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in value.splitlines() if "=" in line)


def _json_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("expected_object")
    return {str(key): item for key, item in value.items()}


def _integer(value: object) -> int:
    if isinstance(value, bool):
        raise TypeError("expected_integer")
    return int(value) if isinstance(value, int | str) else 0


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _artifact_metadata(path: Path) -> dict[str, object]:
    metadata = path.lstat()
    return {
        "path": str(path),
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": f"{stat.S_IMODE(metadata.st_mode):04o}",
        "regular": stat.S_ISREG(metadata.st_mode),
        "symlink": stat.S_ISLNK(metadata.st_mode),
        "sha256": _sha256(path),
    }


def _repository_head() -> str:
    return _command(("git", "rev-parse", "HEAD"), timeout=10).stdout.decode().strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("mode", nargs="?", choices=("main",))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(tuple(sys.argv[1:] if argv is None else argv))
    if args.mode == "main":
        return _main_entrypoint()
    if args.evidence_dir is None:
        return _reporter_entrypoint()
    result = run_candidate_b_v2_probe(args.evidence_dir)
    print(json.dumps(asdict(result), ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
