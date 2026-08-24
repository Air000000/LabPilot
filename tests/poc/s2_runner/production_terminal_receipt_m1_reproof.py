from __future__ import annotations

import hashlib
import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

from tests.poc.s2_runner import terminal_receipt_v2_probe as accepted_probe

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
REPORTER_SHA256 = "5966c05fd18d6d16c0086fbd4f6906ac36f252cbdc5dfbf7c6df3002d43ac499"
INPUT = REPOSITORY_ROOT / "deploy/libexec/labpilot-terminal-receipt-v1.py"
PROOF = Path("/opt/labpilot-runner-mechanism-production-reproof")
REPORTER = PROOF / "libexec/labpilot-terminal-receipt-v1.py"
MAIN = PROOF / "libexec/main-probe.py"
FAILURE = PROOF / "libexec/failure-wrapper.py"
CANARY = PROOF / "canary/python/sitecustomize.py"
EVIDENCE = Path("/var/lib/labpilot-s2-evidence/terminal-receipt-m1-production-reproof")
MARKER = EVIDENCE / "loader-canary-seen"
UNITS = (
    "labpilot-m1-production-reproof-direct.service",
    "labpilot-m1-production-reproof-exit71.service",
)
LOADER_KEYS = (
    "LD_AUDIT",
    "LD_DEBUG",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "PYTHONHOME",
    "PYTHONPATH",
)
EXPECTED_RECEIPT = {
    "schema_version": "labpilot-terminal-receipt-v1",
    "service_result": "exit-code",
    "exit_code": "exited",
    "exit_status": "23",
}
MAX_COMMAND_BYTES = 128 * 1024
MAX_JOURNAL_RECORDS = 64

MAIN_SOURCE = '''import json
import importlib.util
import os
from pathlib import Path

status = {}
with open("/proc/self/status", encoding="ascii") as stream:
    for line in stream:
        if ":" in line:
            key, value = line.split(":", 1)
            status[key] = value.strip()
payload = {
    "uid": os.getuid(),
    "gid": os.getgid(),
    "no_new_privs": status.get("NoNewPrivs"),
    "cap_prm": status.get("CapPrm"),
    "cap_eff": status.get("CapEff"),
    "cap_amb": status.get("CapAmb"),
    "environment": sorted(os.environ.items()),
    "import_canary_seen": Path(__CANARY_MARKER__).exists()
    or importlib.util.find_spec("labpilot_m1_import_canary") is not None,
}
descriptor = os.open(
    os.environ["LABPILOT_M1_MAIN_EVIDENCE"],
    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
    0o600,
)
try:
    os.write(descriptor, (json.dumps(payload, sort_keys=True) + "\\n").encode("ascii"))
    os.fsync(descriptor)
finally:
    os.close(descriptor)
os.write(1, __SPOOF_RECEIPT__.encode("ascii"))
raise SystemExit(23)
'''
MAIN_SOURCE = MAIN_SOURCE.replace("__CANARY_MARKER__", repr(str(MARKER))).replace(
    "__SPOOF_RECEIPT__",
    repr(json.dumps(EXPECTED_RECEIPT, separators=(",", ":"), sort_keys=True) + "\n"),
)

FAILURE_SOURCE = '''import json
import os
import runpy
import sys

facts = {
    "uid": os.getuid(),
    "gid": os.getgid(),
    "loader_environment": {key: os.environ.get(key) for key in __LOADER_KEYS__},
}
print("LABPILOT_M1_WRAPPER=" + json.dumps(facts, sort_keys=True), flush=True)
try:
    runpy.run_path(sys.argv[1], run_name="__main__")
except SystemExit as error:
    if error.code not in (None, 0):
        raise
raise SystemExit(71)
'''.replace("__LOADER_KEYS__", repr(LOADER_KEYS))

CANARY_SOURCE = '''from pathlib import Path
Path(__MARKER__).write_text("seen\\n", encoding="ascii")
'''.replace("__MARKER__", repr(str(MARKER)))


def run(
    argv: tuple[str, ...],
    *,
    allowed: tuple[int, ...] = (0,),
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=timeout,
    )
    if completed.returncode not in allowed:
        raise RuntimeError(
            f"command_failed:{argv!r}:rc={completed.returncode}:"
            f"stdout={completed.stdout!r}:stderr={completed.stderr!r}"
        )
    return completed


def parse_expected_wait_report(
    *, stdout: str, stderr: str, return_code: int
) -> dict[str, object]:
    try:
        stdout_bytes = stdout.encode("ascii")
        stderr_bytes = stderr.encode("ascii")
    except (AttributeError, UnicodeEncodeError) as error:
        raise RuntimeError("wait_report_unrecognized") from error
    if len(stdout_bytes) > MAX_COMMAND_BYTES or len(stderr_bytes) > MAX_COMMAND_BYTES:
        raise RuntimeError("wait_report_unrecognized")
    if stdout_bytes or isinstance(return_code, bool) or not isinstance(return_code, int):
        raise RuntimeError("wait_report_unrecognized")

    lines = stderr.splitlines()
    result_prefix = "          Finished with result: "
    main_prefix = "Main processes terminated with: code="
    result_lines = [line for line in lines if line.startswith(result_prefix)]
    main_lines = [line for line in lines if line.startswith(main_prefix)]
    if not result_lines or not main_lines:
        raise RuntimeError("wait_report_incomplete")
    if len(result_lines) != 1 or len(main_lines) != 1:
        raise RuntimeError("wait_report_unrecognized")
    try:
        main_code, main_status = main_lines[0][len(main_prefix) :].split(
            ", status=", 1
        )
    except ValueError as error:
        raise RuntimeError("wait_report_incomplete") from error
    service_result = result_lines[0][len(result_prefix) :]
    if (return_code, service_result, main_code, main_status) != (
        23,
        "exit-code",
        "exited",
        "23/n/a",
    ):
        raise RuntimeError("wait_report_unrecognized")
    return {
        "service_result": service_result,
        "main_code": main_code,
        "main_status": "23",
        "systemd_run_return_code": return_code,
    }


def capture_systemd_wait(
    argv: tuple[str, ...], *, timeout: int = 45
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=timeout,
    )
    if len(completed.stdout.encode()) > MAX_COMMAND_BYTES or len(
        completed.stderr.encode()
    ) > MAX_COMMAND_BYTES:
        raise RuntimeError("command_output_bound_exceeded")
    return completed


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repository_head() -> str:
    head = run(
        ("git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD"), timeout=10
    ).stdout.strip()
    if len(head) != 40 or any(character not in "0123456789abcdef" for character in head):
        raise RuntimeError("unexpected_repository_head")
    return head


def metadata(path: Path) -> dict[str, object]:
    if not path.exists() and not path.is_symlink():
        return {"exists": False}
    item = path.lstat()
    value: dict[str, object] = {
        "exists": True,
        "mode": stat.S_IMODE(item.st_mode),
        "uid": item.st_uid,
        "gid": item.st_gid,
        "type": stat.S_IFMT(item.st_mode),
    }
    if stat.S_ISREG(item.st_mode):
        value["size"] = item.st_size
        value["sha256"] = sha256(path)
    elif stat.S_ISLNK(item.st_mode):
        value["target"] = os.readlink(path)
    return value


def production_fingerprint() -> dict[str, object]:
    return accepted_probe._production_fingerprint()


def safe_write(path: Path, content: bytes, mode: int = 0o644) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, mode)
    try:
        os.write(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chown(path, 0, 0)
    os.chmod(path, mode)


def write_json(path: Path, value: object) -> None:
    safe_write(
        path,
        (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        0o600,
    )


def persist_wait_capture(
    control: str, completed: subprocess.CompletedProcess[str]
) -> None:
    safe_write(
        EVIDENCE / f"{control}-systemd-run.stdout",
        completed.stdout.encode("utf-8"),
        0o600,
    )
    safe_write(
        EVIDENCE / f"{control}-systemd-run.stderr",
        completed.stderr.encode("utf-8"),
        0o600,
    )
    write_json(
        EVIDENCE / f"{control}-systemd-run.json",
        {
            "return_code": completed.returncode,
            "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
        },
    )


def persist_wait_report(control: str, report: dict[str, object]) -> None:
    write_json(EVIDENCE / f"{control}-wait-report.json", report)


def seal_main_file(path: Path) -> None:
    item = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(item.st_mode):
        raise RuntimeError("unsafe_main_evidence")
    os.chown(path, 0, 0)
    os.chmod(path, 0o600)


def seal_main_directory() -> None:
    path = EVIDENCE / "main"
    item = path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(item.st_mode):
        raise RuntimeError("unsafe_main_evidence_directory")
    os.chown(path, 0, 0)
    os.chmod(path, 0o700)


def prepare(train_uid: int, train_gid: int) -> None:
    if EVIDENCE.exists() or EVIDENCE.is_symlink():
        raise RuntimeError("evidence_already_exists")
    if PROOF.exists() or PROOF.is_symlink():
        raise RuntimeError("proof_root_already_exists")
    if not INPUT.is_file() or INPUT.is_symlink() or sha256(INPUT) != REPORTER_SHA256:
        raise RuntimeError("unexpected_reporter_input")

    EVIDENCE.mkdir(parents=True, mode=0o711)
    os.chown(EVIDENCE, 0, 0)
    os.chmod(EVIDENCE, 0o711)
    main_dir = EVIDENCE / "main"
    main_dir.mkdir(mode=0o700)
    os.chown(main_dir, train_uid, train_gid)

    (PROOF / "libexec").mkdir(parents=True, mode=0o755)
    (PROOF / "canary/python").mkdir(parents=True, mode=0o755)
    (PROOF / "canary/ld").mkdir(parents=True, mode=0o755)
    safe_write(REPORTER, INPUT.read_bytes())
    safe_write(MAIN, MAIN_SOURCE.encode("ascii"))
    safe_write(FAILURE, FAILURE_SOURCE.encode("ascii"))
    safe_write(CANARY, CANARY_SOURCE.encode("ascii"))
    for root, directories, _files in os.walk(PROOF):
        os.chown(root, 0, 0)
        os.chmod(root, 0o755)
        for name in directories:
            child = Path(root) / name
            os.chown(child, 0, 0)
            os.chmod(child, 0o755)
    if sha256(REPORTER) != REPORTER_SHA256:
        raise RuntimeError("installed_reporter_digest_mismatch")


def cursor() -> str:
    completed = run(("journalctl", "--no-pager", "--show-cursor", "-n", "0"))
    return accepted_probe._parse_journal_cursor(
        stdout=completed.stdout.encode("ascii"),
        stderr=completed.stderr.encode("ascii"),
    )


def read_boot_id() -> str:
    return (
        Path("/proc/sys/kernel/random/boot_id")
        .read_text(encoding="ascii")
        .removesuffix("\n")
    )


def journal_records(argv: tuple[str, ...]) -> list[dict[str, object]]:
    output = run(argv, timeout=20).stdout.encode("utf-8")
    return accepted_probe._parse_journal_records(output)


def manager_records(unit: str, barrier: str) -> list[dict[str, object]]:
    accepted_probe._parse_journal_cursor(
        stdout=f"-- cursor: {barrier}\n".encode("ascii"), stderr=b""
    )
    return journal_records(
        (
            "journalctl",
            "--no-pager",
            "--output=json",
            f"--after-cursor={barrier}",
            f"--lines={MAX_JOURNAL_RECORDS + 1}",
            f"UNIT={unit}",
            "_PID=1",
            "_SYSTEMD_UNIT=init.scope",
            "SYSLOG_IDENTIFIER=systemd",
        )
    )


def reporter_records(unit: str, barrier: str) -> list[dict[str, object]]:
    accepted_probe._parse_journal_cursor(
        stdout=f"-- cursor: {barrier}\n".encode("ascii"), stderr=b""
    )
    return journal_records(
        (
            "journalctl",
            "--no-pager",
            "--output=json",
            f"--after-cursor={barrier}",
            f"--lines={MAX_JOURNAL_RECORDS + 1}",
            f"_SYSTEMD_UNIT={unit}",
        )
    )


def receipt_evidence(
    unit: str,
    records: list[dict[str, object]],
    manager: dict[str, object],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    invocation = manager.get("manager_invocation_raw")
    manager_boot = manager.get("manager_boot_raw")
    if not isinstance(invocation, str) or not isinstance(manager_boot, str):
        raise RuntimeError("manager_evidence_invalid")
    proc_boot = read_boot_id()
    normalized_boot = accepted_probe._normalize_boot_id(proc_boot)
    if accepted_probe._normalize_boot_id(manager_boot) != normalized_boot:
        raise RuntimeError("manager_boot_provenance_failed")

    root: list[tuple[dict[str, object], str]] = []
    spoof: list[tuple[dict[str, object], str]] = []
    wrappers: list[dict[str, object]] = []
    for record in records:
        message = record.get("MESSAGE")
        if not isinstance(message, str):
            continue
        if message.startswith("LABPILOT_M1_WRAPPER="):
            wrapper = json.loads(message.split("=", 1)[1])
            if isinstance(wrapper, dict):
                wrappers.append(wrapper)
            continue
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            continue
        boot = record.get("_BOOT_ID")
        if (
            payload != EXPECTED_RECEIPT
            or record.get("_SYSTEMD_UNIT") != unit
            or record.get("_SYSTEMD_INVOCATION_ID") != invocation
            or not isinstance(boot, str)
        ):
            continue
        try:
            record_boot = accepted_probe._normalize_boot_id(boot)
        except RuntimeError:
            continue
        if record_boot != normalized_boot:
            continue
        item = (record, message)
        if record.get("_UID") == "0" and record.get("_GID") == "0":
            root.append(item)
        else:
            spoof.append(item)
    if len(root) != 1 or len(spoof) != 1:
        raise RuntimeError("reporter_journal_cardinality_failed")
    root_record, root_message = root[0]
    spoof_record, spoof_message = spoof[0]
    if root_message != spoof_message:
        raise RuntimeError("anti_spoof_message_not_identical")
    return (
        {
            "payload": EXPECTED_RECEIPT,
            "_UID": root_record.get("_UID"),
            "_GID": root_record.get("_GID"),
            "_spoof_UID": spoof_record.get("_UID"),
            "_spoof_GID": spoof_record.get("_GID"),
            "_SYSTEMD_UNIT": root_record.get("_SYSTEMD_UNIT"),
            "manager_invocation_raw": invocation,
            "reporter_invocation_raw": root_record.get("_SYSTEMD_INVOCATION_ID"),
            "manager_boot_raw": manager_boot,
            "reporter_boot_raw": root_record.get("_BOOT_ID"),
            "proc_boot_id_raw": proc_boot,
            "boot_id_normalized": normalized_boot,
        },
        wrappers,
    )


def launch(unit: str, control: str) -> dict[str, object]:
    if (unit, control) == (UNITS[0], "direct"):
        reporter_command = f"-+/usr/bin/python3 -I -B {REPORTER}"
    elif (unit, control) == (UNITS[1], "exit71"):
        reporter_command = f"-+/usr/bin/python3 -I -B {FAILURE} {REPORTER}"
    else:
        raise ValueError("unsupported_reproof_control")
    barrier = cursor()
    main_path = EVIDENCE / "main" / f"{control}.json"
    environment = (
        ("LABPILOT_M1_CONTROL", control),
        ("LABPILOT_M1_MAIN_EVIDENCE", str(main_path)),
        ("LD_LIBRARY_PATH", str(PROOF / "canary/ld")),
        ("PYTHONPATH", str(PROOF / "canary/python")),
    )
    argv = (
        "systemd-run",
        "--system",
        "--no-ask-password",
        f"--unit={unit}",
        f"--description=labpilot-m1-production-reproof:{control}",
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
        f"--property=UnsetEnvironment={' '.join(LOADER_KEYS)}",
        f"--property=ExecStopPost={reporter_command}",
        "--",
        "/usr/bin/env",
        "-i",
        *(f"{key}={value}" for key, value in sorted(environment)),
        "/usr/bin/python3",
        "-I",
        "-B",
        str(MAIN),
    )
    started = capture_systemd_wait(argv, timeout=45)
    persist_wait_capture(control, started)
    wait_report = parse_expected_wait_report(
        stdout=started.stdout,
        stderr=started.stderr,
        return_code=started.returncode,
    )
    persist_wait_report(control, wait_report)
    show = run(
        (
            "systemctl",
            "show",
            unit,
            "--no-pager",
            "--property=Result",
            "--property=ExecMainCode",
            "--property=ExecMainStatus",
            "--property=InvocationID",
            "--property=ActiveState",
            "--property=SubState",
        )
    ).stdout
    time.sleep(0.25)
    manager_journal = manager_records(unit, barrier)
    manager = accepted_probe._manager_lifecycle(unit, manager_journal)
    receipt_journal = reporter_records(unit, barrier)
    receipt, wrappers = receipt_evidence(unit, receipt_journal, manager)
    values = dict(line.split("=", 1) for line in show.splitlines() if "=" in line)
    main = json.loads(main_path.read_text(encoding="ascii"))
    seal_main_file(main_path)
    correlation = {
        "manager_invocation_raw": manager["manager_invocation_raw"],
        "reporter_invocation_raw": receipt["reporter_invocation_raw"],
        "manager_boot_raw": manager["manager_boot_raw"],
        "reporter_boot_raw": receipt["reporter_boot_raw"],
        "proc_boot_id_raw": receipt["proc_boot_id_raw"],
        "boot_id_normalized": receipt["boot_id_normalized"],
        "matched": True,
    }
    return {
        "argv": list(argv),
        "systemd_run_returncode": started.returncode,
        "systemd_run_stdout": started.stdout,
        "systemd_run_stderr": started.stderr,
        "show": values,
        "main": main,
        "wait_report": wait_report,
        "manager": manager,
        "receipt": receipt,
        "correlation": correlation,
        "wrappers": wrappers,
        "manager_records": manager_journal,
        "reporter_records": receipt_journal,
    }


def validate(controls: dict[str, dict[str, object]], train_uid: int, train_gid: int) -> None:
    expected_environment = {
        "LABPILOT_M1_CONTROL",
        "LABPILOT_M1_MAIN_EVIDENCE",
        "LD_LIBRARY_PATH",
        "PYTHONPATH",
    }
    for control, value in controls.items():
        main = value["main"]
        if (main["uid"], main["gid"]) != (train_uid, train_gid):
            raise RuntimeError(f"main_identity_failed:{control}:{main!r}")
        if main["no_new_privs"] != "1":
            raise RuntimeError(f"main_nnp_failed:{control}:{main!r}")
        if any(main[key] != "0000000000000000" for key in ("cap_prm", "cap_eff", "cap_amb")):
            raise RuntimeError(f"main_caps_failed:{control}:{main!r}")
        if {pair[0] for pair in main["environment"]} != expected_environment:
            raise RuntimeError(f"main_environment_failed:{control}:{main!r}")
        if main.get("import_canary_seen") is not False:
            raise RuntimeError(f"main_import_isolation_failed:{control}:{main!r}")
        receipt = value["receipt"]
        if (receipt["_UID"], receipt["_GID"], receipt["_SYSTEMD_UNIT"]) != (
            "0",
            "0",
            UNITS[0] if control == "direct" else UNITS[1],
        ):
            raise RuntimeError(f"receipt_provenance_failed:{control}:{receipt!r}")
        if receipt["reporter_invocation_raw"] != value["manager"][
            "manager_invocation_raw"
        ]:
            raise RuntimeError(f"receipt_invocation_failed:{control}:{receipt!r}")
        if receipt["_spoof_UID"] in (None, "0") or receipt["_spoof_GID"] in (
            None,
            "0",
        ):
            raise RuntimeError(f"anti_spoof_failed:{control}:{receipt!r}")
        if value["correlation"].get("matched") is not True:
            raise RuntimeError(f"correlation_failed:{control}:{value['correlation']!r}")
    if controls["direct"]["wrappers"]:
        raise RuntimeError("unexpected_direct_wrapper")
    wrappers = controls["exit71"]["wrappers"]
    if len(wrappers) != 1:
        raise RuntimeError(f"wrapper_cardinality_failed:{wrappers!r}")
    wrapper = wrappers[0]
    if (wrapper["uid"], wrapper["gid"]) != (0, 0):
        raise RuntimeError(f"wrapper_identity_failed:{wrapper!r}")
    if any(value is not None for value in wrapper["loader_environment"].values()):
        raise RuntimeError(f"reporter_loader_environment_failed:{wrapper!r}")
    if MARKER.exists():
        raise RuntimeError("loader_canary_seen")
    if controls["direct"]["wait_report"] != controls["exit71"]["wait_report"]:
        raise RuntimeError("wait_report_failure_neutrality_failed")


def cleanup() -> dict[str, object]:
    cleaned = []
    for unit in UNITS:
        run(("systemctl", "stop", unit), allowed=(0, 1, 5))
        run(("systemctl", "reset-failed", unit), allowed=(0, 1, 5))
        cleaned.append(unit)
    if PROOF.exists() or PROOF.is_symlink():
        item = PROOF.lstat()
        if PROOF.is_symlink() or item.st_uid != 0 or item.st_gid != 0 or PROOF.resolve() != PROOF:
            raise RuntimeError("unsafe_proof_cleanup")
        shutil.rmtree(PROOF)
    return {"units": cleaned, "proof_root_removed": not PROOF.exists()}


def main() -> int:
    if os.geteuid() != 0:
        raise PermissionError("root_required")
    if run(("hostname",)).stdout.strip() != "labpilot-s2":
        raise RuntimeError("unexpected_host")
    version = run(("systemctl", "--version")).stdout.splitlines()[0]
    if version != "systemd 259 (259.5-0ubuntu3.4)":
        raise RuntimeError(f"unexpected_systemd:{version!r}")
    train = pwd.getpwnam("labpilot-train")
    head = repository_head()
    before = production_fingerprint()
    prepare(train.pw_uid, train.pw_gid)
    write_json(EVIDENCE / "production-before.json", before)
    write_json(
        EVIDENCE / "proof-artifact.json",
        {"path": str(REPORTER), "sha256": sha256(REPORTER), "metadata": metadata(REPORTER)},
    )
    controls: dict[str, dict[str, object]] = {}
    failure: str | None = None
    try:
        controls["direct"] = launch(
            UNITS[0],
            "direct",
        )
        controls["exit71"] = launch(
            UNITS[1],
            "exit71",
        )
        validate(controls, train.pw_uid, train.pw_gid)
    except Exception as error:
        failure = f"{type(error).__name__}:{error}"
    seal_main_directory()
    cleanup_result = cleanup()
    after = production_fingerprint()
    write_json(EVIDENCE / "production-after.json", after)
    write_json(EVIDENCE / "controls.json", controls)
    write_json(EVIDENCE / "cleanup.json", cleanup_result)
    result = {
        "repository_head": head,
        "hostname": "labpilot-s2",
        "systemd_version": version,
        "reporter_sha256": REPORTER_SHA256,
        "accepted_m1_direct_command": f"-+/usr/bin/python3 -I -B {REPORTER}",
        "production_runner_mutated": before != after,
        "failure": failure,
        "passed": failure is None and before == after,
    }
    write_json(EVIDENCE / "result.json", result)
    manifest = []
    for path in sorted(EVIDENCE.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            manifest.append(f"{sha256(path)}  {path.relative_to(EVIDENCE)}\n")
    safe_write(EVIDENCE / "SHA256SUMS", "".join(manifest).encode("ascii"), 0o600)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
