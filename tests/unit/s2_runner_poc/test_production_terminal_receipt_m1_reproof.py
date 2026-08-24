import importlib
import inspect
import json
import subprocess
from pathlib import Path

import pytest

MODULE = "tests.poc.s2_runner.production_terminal_receipt_m1_reproof"
CURSOR = (
    "s=4c208ddbd6ac40ebbf4a3aac7ba5b609;i=3bd5;"
    "b=538aa523d7574732811a8e938b3bb450;m=163297aa92;"
    "t=659bdf289f413;x=b296095ce04c8fd7"
)
BOOT_ID = "538aa523d7574732811a8e938b3bb450"
INVOCATION = "07ebd966b42349c4b9bd61b68079d762"
WAIT_EXIT_23 = (
    "Running as unit: labpilot-m1-production-control.service; invocation ID: "
    f"{INVOCATION}\n"
    "          Finished with result: exit-code\n"
    "Main processes terminated with: code=exited, status=23/n/a\n"
    "               Service runtime: 184ms\n"
    "             CPU time consumed: 154ms\n"
    "                   Memory peak: 4.6M (swap: 0B)\n"
)


def _driver():
    return importlib.import_module(MODULE)


def _manager_record(unit: str) -> dict[str, str]:
    return {
        "UNIT": unit,
        "_PID": "1",
        "_SYSTEMD_UNIT": "init.scope",
        "SYSLOG_IDENTIFIER": "systemd",
        "MESSAGE_ID": "39f53479d3a045ac8e11786248231fbf",
        "JOB_TYPE": "start",
        "JOB_RESULT": "done",
        "INVOCATION_ID": INVOCATION,
        "_BOOT_ID": BOOT_ID,
    }


def _receipt_records(driver, unit: str) -> list[dict[str, object]]:
    message = json.dumps(driver.EXPECTED_RECEIPT, separators=(",", ":"), sort_keys=True)
    return [
        {
            "MESSAGE": message,
            "_UID": "991",
            "_GID": "991",
            "_SYSTEMD_UNIT": unit,
            "_SYSTEMD_INVOCATION_ID": INVOCATION,
            "_BOOT_ID": BOOT_ID,
        },
        {
            "MESSAGE": message,
            "_UID": "0",
            "_GID": "0",
            "_SYSTEMD_UNIT": unit,
            "_SYSTEMD_INVOCATION_ID": INVOCATION,
            "_BOOT_ID": BOOT_ID,
        },
    ]


def _install_systemd_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    wait_stderr: str = WAIT_EXIT_23,
    return_code: int = 23,
) -> tuple[object, list[tuple[str, ...]]]:
    driver = _driver()
    evidence = tmp_path / "evidence"
    proof = tmp_path / "proof"
    (evidence / "main").mkdir(parents=True)
    (proof / "canary/ld").mkdir(parents=True)
    (proof / "canary/python").mkdir(parents=True)
    monkeypatch.setattr(driver, "EVIDENCE", evidence)
    monkeypatch.setattr(driver, "PROOF", proof)
    monkeypatch.setattr(driver, "MAIN", proof / "libexec/main-probe.py")
    monkeypatch.setattr(driver, "MARKER", evidence / "loader-canary-seen")
    monkeypatch.setattr(driver, "read_boot_id", lambda: BOOT_ID, raising=False)
    monkeypatch.setattr(
        driver, "persist_wait_capture", lambda *_args: None, raising=False
    )
    monkeypatch.setattr(
        driver, "persist_wait_report", lambda *_args: None, raising=False
    )
    monkeypatch.setattr(driver, "seal_main_file", lambda *_args: None, raising=False)
    monkeypatch.setattr(
        driver,
        "UNITS",
        (
            "labpilot-m1-production-control.service",
            "labpilot-m1-production-exit71.service",
        ),
    )
    calls: list[tuple[str, ...]] = []

    def fake_run(argv, **_kwargs):
        command = tuple(argv)
        calls.append(command)
        if command[:4] == ("journalctl", "--no-pager", "--show-cursor", "-n"):
            return subprocess.CompletedProcess(command, 0, f"-- cursor: {CURSOR}\n", "")
        if command[0] == "systemd-run":
            main = {
                "uid": 991,
                "gid": 991,
                "no_new_privs": "1",
                "cap_prm": "0000000000000000",
                "cap_eff": "0000000000000000",
                "cap_amb": "0000000000000000",
                "environment": [
                    ["LABPILOT_M1_CONTROL", "direct"],
                    ["LABPILOT_M1_MAIN_EVIDENCE", str(evidence / "main/direct.json")],
                    ["LD_LIBRARY_PATH", str(proof / "canary/ld")],
                    ["PYTHONPATH", str(proof / "canary/python")],
                ],
                "import_canary_seen": False,
            }
            (evidence / "main/direct.json").write_text(
                json.dumps(main), encoding="ascii"
            )
            return subprocess.CompletedProcess(command, return_code, "", wait_stderr)
        if command[:2] == ("systemctl", "show"):
            return subprocess.CompletedProcess(
                command,
                0,
                "Result=exit-code\nExecMainCode=1\nExecMainStatus=23\n"
                f"InvocationID={INVOCATION}\nActiveState=failed\nSubState=failed\n",
                "",
            )
        if command[0] == "journalctl" and "_PID=1" in command:
            output = json.dumps(_manager_record(driver.UNITS[0])) + "\n"
            return subprocess.CompletedProcess(command, 0, output, "")
        if command[0] == "journalctl":
            output = "\n".join(
                json.dumps(record) for record in _receipt_records(driver, driver.UNITS[0])
            ) + "\n"
            return subprocess.CompletedProcess(command, 0, output, "")
        raise AssertionError(command)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return driver, calls


def test_expected_nonzero_wait_continues_to_trusted_evidence_acquisition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    driver, calls = _install_systemd_boundary(monkeypatch, tmp_path)

    evidence = driver.launch(
        driver.UNITS[0],
        "direct",
    )

    assert evidence["wait_report"] == {
        "service_result": "exit-code",
        "main_code": "exited",
        "main_status": "23",
        "systemd_run_return_code": 23,
    }
    assert evidence["manager"]["manager_invocation_raw"] == INVOCATION
    assert evidence["receipt"]["_UID"] == "0"
    assert evidence["correlation"]["matched"] is True
    assert any("_PID=1" in command for command in calls)


def test_launch_exposes_no_caller_selected_privileged_command() -> None:
    driver = _driver()

    assert tuple(inspect.signature(driver.launch).parameters) == ("unit", "control")
    with pytest.raises(TypeError):
        driver.launch(
            driver.UNITS[0],
            "direct",
            reporter_command="-+/bin/sh -c id",
        )


@pytest.mark.parametrize(
    ("return_code", "stderr"),
    [
        (22, WAIT_EXIT_23),
        (23, WAIT_EXIT_23.replace("status=23/n/a", "status=22/n/a")),
        (23, WAIT_EXIT_23.replace("result: exit-code", "result: success")),
        (23, "systemd-run: failed to connect to bus\n"),
    ],
)
def test_expected_nonzero_wait_rejects_missing_or_contradictory_results(
    return_code: int,
    stderr: str,
) -> None:
    with pytest.raises(RuntimeError, match="wait_report_(?:incomplete|unrecognized)"):
        _driver().parse_expected_wait_report(
            stdout="",
            stderr=stderr,
            return_code=return_code,
        )
