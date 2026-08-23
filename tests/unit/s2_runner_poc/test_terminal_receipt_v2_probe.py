import copy
import importlib
import inspect
import json
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

MODULE_NAME = "tests.poc.s2_runner.terminal_receipt_v2_probe"
EVIDENCE_ROOT = Path("/var/lib/labpilot-s2-evidence/terminal-receipt-m1")
PROOF_ASSET = "/opt/labpilot-runner-mechanism-proof/libexec/terminal-receipt-probe.py"
WAIT_STDOUT = b""
WAIT_STDERR = (
    b"Running as unit: labpilot-terminal-receipt-m1-exit0.service; invocation ID: "
    b"da3ff7dec0054c5b80b41f8ea0a97815\n"
    b"          Finished with result: success\n"
    b"Main processes terminated with: code=exited, status=0/SUCCESS\n"
    b"               Service runtime: 1.939s\n"
    b"             CPU time consumed: 1.041s\n"
    b"                  Memory peak: 11.2M (swap: 0B)\n"
)
BOOT_ID_HYPHENATED = "538aa523-d757-4732-811a-8e938b3bb450"
BOOT_ID_COMPACT = "538aa523d7574732811a8e938b3bb450"
REPORTER_MESSAGE = (
    '{"event":"labpilot_terminal_receipt_m1_reporter","exit_code":"exited",'
    '"exit_status":"0","import_canary_seen":false,'
    '"loader_environment_present":false,"reporter_gid":0,"reporter_uid":0,'
    '"service_result":"success"}'
)
UNIT = "labpilot-terminal-receipt-m1-exit0.service"
CURSOR = (
    "s=4c208ddbd6ac40ebbf4a3aac7ba5b609;i=3bd5;"
    "b=538aa523d7574732811a8e938b3bb450;m=163297aa92;"
    "t=659bdf289f413;x=b296095ce04c8fd7"
)
INVOCATION_A = "07ebd966b42349c4b9bd61b68079d762"
INVOCATION_B = "17ebd966b42349c4b9bd61b68079d762"
STARTED_MESSAGE_ID = "39f53479d3a045ac8e11786248231fbf"


def _probe():
    return importlib.import_module(MODULE_NAME)


def _manager_record(
    *, invocation: str = INVOCATION_A, boot_id: str = BOOT_ID_COMPACT
) -> dict[str, str]:
    return {
        "UNIT": UNIT,
        "_PID": "1",
        "_SYSTEMD_UNIT": "init.scope",
        "SYSLOG_IDENTIFIER": "systemd",
        "MESSAGE_ID": STARTED_MESSAGE_ID,
        "JOB_TYPE": "start",
        "JOB_RESULT": "done",
        "INVOCATION_ID": invocation,
        "_BOOT_ID": boot_id,
    }


def _reporter_records(
    *,
    root_invocation: str = INVOCATION_A,
    spoof_invocation: str = INVOCATION_A,
    boot_id: str = BOOT_ID_COMPACT,
) -> list[dict[str, str]]:
    return [
        {
            "MESSAGE": REPORTER_MESSAGE,
            "_UID": "995",
            "_GID": "982",
            "_SYSTEMD_UNIT": UNIT,
            "_SYSTEMD_INVOCATION_ID": spoof_invocation,
            "_BOOT_ID": boot_id,
        },
        {
            "MESSAGE": REPORTER_MESSAGE,
            "_UID": "0",
            "_GID": "0",
            "_SYSTEMD_UNIT": UNIT,
            "_SYSTEMD_INVOCATION_ID": root_invocation,
            "_BOOT_ID": boot_id,
        },
    ]


def _good_controls() -> dict[str, dict[str, object]]:
    main = {
        "uid": 991,
        "gid": 991,
        "no_new_privs": 1,
        "cap_prm": "0000000000000000",
        "cap_eff": "0000000000000000",
        "cap_amb": "0000000000000000",
        "environment": [
            ["LD_LIBRARY_PATH", "/opt/labpilot-runner-mechanism-proof/canary/ld"],
            ["PYTHONPATH", "/opt/labpilot-runner-mechanism-proof/canary/python"],
        ],
        "import_canary_seen": False,
    }
    reporter = {
        "_UID": "0",
        "_GID": "0",
        "_spoof_UID": "991",
        "_spoof_GID": "991",
        "service_result": "success",
        "exit_code": "exited",
        "exit_status": "0",
        "loader_environment_present": False,
        "import_canary_seen": False,
    }
    unit = {
        "Result": "success",
        "ExecMainCode": "exited",
        "ExecMainStatus": "0",
    }
    wait_report = {
        "service_result": "success",
        "main_code": "exited",
        "main_status": "0",
        "systemd_run_return_code": 0,
    }
    return {
        "exit0": {
            "main": copy.deepcopy(main),
            "reporter": copy.deepcopy(reporter),
            "unit": copy.deepcopy(unit),
            "wait_report": copy.deepcopy(wait_report),
        },
        "exit71": {
            "main": copy.deepcopy(main),
            "reporter": copy.deepcopy(reporter),
            "unit": copy.deepcopy(unit),
            "wait_report": copy.deepcopy(wait_report),
        },
    }


def _correlated_reporter_evidence(
    *,
    proc_boot_id: str,
    journal_boot_id: str,
) -> dict[str, object]:
    probe = _probe()
    manager = probe._manager_lifecycle(
        UNIT, [_manager_record(boot_id=journal_boot_id)]
    )
    return probe._reporter_evidence(
        UNIT,
        _reporter_records(boot_id=journal_boot_id),
        manager,
        proc_boot_id_raw=proc_boot_id,
    )


def test_step0_module_and_result_contract_exist() -> None:
    probe = _probe()

    assert [field.name for field in fields(probe.MechanismProbeResult)] == [
        "main_uid",
        "main_gid",
        "main_no_new_privs",
        "main_cap_prm",
        "main_cap_eff",
        "main_cap_amb",
        "main_environment",
        "reporter_uid",
        "reporter_gid",
        "service_result",
        "exit_code",
        "exit_status",
        "reporter_failure_neutral",
        "loader_canary_seen",
    ]
    result = probe._mechanism_result(_good_controls())
    with pytest.raises(FrozenInstanceError):
        result.main_uid = 0


def test_candidate_b_v2_probe_argv_is_exact_m1() -> None:
    argv = _probe().candidate_b_v2_probe_argv(
        "labpilot-terminal-receipt-m1-exit0.service"
    )

    assert argv == (
        "systemd-run",
        "--system",
        "--no-ask-password",
        "--unit=labpilot-terminal-receipt-m1-exit0.service",
        "--description=labpilot-terminal-receipt-m1:exit0",
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
        "--property=UnsetEnvironment=LD_AUDIT LD_DEBUG LD_LIBRARY_PATH "
        "LD_PRELOAD PYTHONHOME PYTHONPATH",
        f"--property=ExecStopPost=-+/usr/bin/python3 -I -B {PROOF_ASSET}",
        "--",
        "/usr/bin/env",
        "-i",
        "LABPILOT_M1_CONTROL=exit0",
        "LABPILOT_M1_MAIN_EVIDENCE=/var/lib/labpilot-s2-evidence/terminal-receipt-m1/exit0-main.json",
        "LD_LIBRARY_PATH=/opt/labpilot-runner-mechanism-proof/canary/ld",
        "PYTHONPATH=/opt/labpilot-runner-mechanism-proof/canary/python",
        "/usr/bin/python3",
        "-I",
        "-B",
        PROOF_ASSET,
        "main",
    )


def test_exit71_uses_the_same_fixed_mechanism() -> None:
    argv = _probe().candidate_b_v2_probe_argv(
        "labpilot-terminal-receipt-m1-exit71.service"
    )

    assert argv[0:3] == ("systemd-run", "--system", "--no-ask-password")
    assert "--unit=labpilot-terminal-receipt-m1-exit71.service" in argv
    assert "--description=labpilot-terminal-receipt-m1:exit71" in argv
    assert (
        "LABPILOT_M1_MAIN_EVIDENCE=/var/lib/labpilot-s2-evidence/terminal-receipt-m1/exit71-main.json"
        in argv
    )
    assert "LABPILOT_M1_CONTROL=exit71" in argv
    assert f"--property=ExecStopPost=-+/usr/bin/python3 -I -B {PROOF_ASSET}" in argv


@pytest.mark.parametrize(
    "unit",
    [
        "labpilot-terminal-receipt-m1.service",
        "labpilot-terminal-receipt-m1-exit0.service;id",
        "labpilot-runner.service",
        "../labpilot-terminal-receipt-m1-exit0.service",
        "",
    ],
)
def test_probe_rejects_arbitrary_unit_input(unit: str) -> None:
    with pytest.raises(ValueError, match="unsupported_probe_unit"):
        _probe().candidate_b_v2_probe_argv(unit)


def test_probe_exposes_no_caller_selected_privileged_authority() -> None:
    probe = _probe()

    assert tuple(inspect.signature(probe.candidate_b_v2_probe_argv).parameters) == ("unit",)
    with pytest.raises(TypeError):
        probe.candidate_b_v2_probe_argv(
            "labpilot-terminal-receipt-m1-exit0.service",
            exec_stop_post="/bin/sh",
        )
    with pytest.raises(TypeError):
        probe.candidate_b_v2_probe_argv(
            "labpilot-terminal-receipt-m1-exit0.service",
            properties=("User=root",),
        )
    with pytest.raises(TypeError):
        probe.candidate_b_v2_probe_argv(
            "labpilot-terminal-receipt-m1-exit0.service",
            proof_path=Path("/tmp/probe.py"),
        )


def test_probe_argv_has_no_shell_or_unit_wide_workload_environment() -> None:
    argv = _probe().candidate_b_v2_probe_argv(
        "labpilot-terminal-receipt-m1-exit0.service"
    )

    assert not any(part.startswith("--setenv=") for part in argv)
    assert not any(part in {"sh", "bash", "/bin/sh", "/bin/bash", "-c"} for part in argv)
    assert "/usr/bin/env" in argv
    assert argv[argv.index("/usr/bin/env") + 1] == "-i"
    assert not any("/opt/labpilot-runner/libexec" in part for part in argv)
    assert not any("/opt/labpilot-runner/app" in part for part in argv)


def test_unprivileged_spoof_message_is_byte_identical_without_self_injected_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _probe()
    monkeypatch.setenv("SERVICE_RESULT", "success")
    monkeypatch.setenv("EXIT_CODE", "exited")
    monkeypatch.setenv("EXIT_STATUS", "0")
    root_record = probe._reporter_message(
        uid=0, gid=0, loader_present=False, import_seen=False
    )
    monkeypatch.delenv("SERVICE_RESULT")
    monkeypatch.delenv("EXIT_CODE")
    monkeypatch.delenv("EXIT_STATUS")

    spoof_record = probe._reporter_message(
        uid=0,
        gid=0,
        loader_present=False,
        import_seen=False,
        terminal_values=("success", "exited", "0"),
    )

    assert spoof_record == root_record


def test_probe_requires_the_fixed_evidence_root_before_native_work() -> None:
    with pytest.raises(ValueError, match="unexpected_evidence_dir"):
        _probe().run_candidate_b_v2_probe(Path("/tmp/arbitrary-evidence"))


def test_wait_report_is_authoritative_when_unit_is_collected_after_wait() -> None:
    report = _probe()._parse_wait_report(
        stdout=WAIT_STDOUT.decode("ascii"),
        stderr=WAIT_STDERR.decode("ascii"),
        return_code=0,
    )

    assert report == {
        "service_result": "success",
        "main_code": "exited",
        "main_status": "0",
        "systemd_run_return_code": 0,
    }


def test_failure_neutrality_requires_matching_complete_wait_reports() -> None:
    controls = _good_controls()
    controls["exit71"]["wait_report"]["systemd_run_return_code"] = 1

    with pytest.raises(RuntimeError, match="reporter_not_failure_neutral"):
        _probe()._mechanism_result(controls)

    controls = _good_controls()
    del controls["exit71"]["wait_report"]["main_status"]

    with pytest.raises(RuntimeError, match="incomplete_probe_evidence"):
        _probe()._mechanism_result(controls)


@pytest.mark.parametrize(
    "stderr",
    [
        WAIT_STDERR.replace(b"          Finished with result: success\n", b""),
        WAIT_STDERR.replace(
            b"Main processes terminated with: code=exited, status=0/SUCCESS\n",
            b"Main processes terminated with: status=0/SUCCESS\n",
        ),
        WAIT_STDERR.replace(
            b"Main processes terminated with: code=exited, status=0/SUCCESS\n",
            b"Main processes terminated with: code=exited\n",
        ),
    ],
)
def test_wait_report_parser_fails_closed_when_required_field_missing(
    stderr: bytes,
) -> None:
    with pytest.raises(RuntimeError, match="wait_report_incomplete"):
        _probe()._parse_wait_report(stdout=WAIT_STDOUT, stderr=stderr, return_code=0)


def test_wait_report_parser_fails_closed_on_ambiguous_duplicate_terminal_fields() -> None:
    conflicting = WAIT_STDERR + (
        b"          Finished with result: failed\n"
        b"Main processes terminated with: code=killed, status=9/KILL\n"
    )

    with pytest.raises(RuntimeError, match="wait_report_ambiguous"):
        _probe()._parse_wait_report(
            stdout=WAIT_STDOUT,
            stderr=conflicting,
            return_code=0,
        )


@pytest.mark.parametrize(
    "stderr",
    [
        WAIT_STDERR.replace(b"result: success", b"result: unknown-result"),
        WAIT_STDERR.replace(b"code=exited", b"code=unknown-code"),
        WAIT_STDERR.replace(b"status=0/SUCCESS", b"status=999999/UNKNOWN"),
    ],
)
def test_wait_report_parser_fails_closed_on_unrecognized_terminal_value(
    stderr: bytes,
) -> None:
    with pytest.raises(RuntimeError, match="wait_report_unrecognized"):
        _probe()._parse_wait_report(stdout=WAIT_STDOUT, stderr=stderr, return_code=0)


def test_wait_report_parser_bounds_captured_output() -> None:
    with pytest.raises(RuntimeError, match="wait_report_too_large"):
        _probe()._parse_wait_report(
            stdout=b"x" * (128 * 1024 + 1),
            stderr=WAIT_STDERR,
            return_code=0,
        )


def test_systemd_run_invocation_suffix_is_not_required_for_step0() -> None:
    without_suffix = WAIT_STDERR.replace(
        b"; invocation ID: da3ff7dec0054c5b80b41f8ea0a97815", b""
    )

    with_suffix = _probe()._parse_wait_report(
        stdout=WAIT_STDOUT, stderr=WAIT_STDERR, return_code=0
    )
    without_suffix_result = _probe()._parse_wait_report(
        stdout=WAIT_STDOUT, stderr=without_suffix, return_code=0
    )

    assert without_suffix_result == with_suffix == {
        "service_result": "success",
        "main_code": "exited",
        "main_status": "0",
        "systemd_run_return_code": 0,
    }


def test_journal_cursor_parser_requires_one_bounded_token() -> None:
    probe = _probe()
    stdout = f"-- No entries --\n-- cursor: {CURSOR}\n".encode("ascii")

    assert probe._parse_journal_cursor(stdout=stdout, stderr=b"") == CURSOR
    with pytest.raises(RuntimeError, match="journal_cursor_invalid"):
        probe._parse_journal_cursor(stdout=b"-- No entries --\n", stderr=b"")
    with pytest.raises(RuntimeError, match="journal_cursor_invalid"):
        probe._parse_journal_cursor(
            stdout=stdout + f"-- cursor: {CURSOR}\n".encode("ascii"), stderr=b""
        )


def test_journal_queries_are_bound_to_prelaunch_cursor() -> None:
    probe = _probe()

    assert probe._manager_journal_argv(UNIT, CURSOR) == (
        "journalctl",
        "--no-pager",
        "--output=json",
        f"--after-cursor={CURSOR}",
        "--lines=65",
        f"UNIT={UNIT}",
        "_PID=1",
        "_SYSTEMD_UNIT=init.scope",
        "SYSLOG_IDENTIFIER=systemd",
    )
    assert probe._reporter_journal_argv(UNIT, CURSOR) == (
        "journalctl",
        "--no-pager",
        "--output=json",
        f"--after-cursor={CURSOR}",
        "--lines=65",
        f"_SYSTEMD_UNIT={UNIT}",
    )
    with pytest.raises(RuntimeError, match="journal_cursor_invalid"):
        probe._manager_journal_argv(UNIT, "")
    with pytest.raises(SystemExit):
        probe._parse_args(("--journal-cursor", CURSOR))


def test_previous_same_unit_lifecycle_before_cursor_is_not_candidate() -> None:
    previous_cursor = CURSOR.replace("i=3bd5", "i=3bd4")
    argv = _probe()._manager_journal_argv(UNIT, CURSOR)

    assert f"--after-cursor={CURSOR}" in argv
    assert f"--after-cursor={previous_cursor}" not in argv


def test_bounded_journal_records_fail_closed_at_overflow_sentinel() -> None:
    record = json.dumps(_manager_record(), separators=(",", ":")).encode("ascii")
    output = b"\n".join([record] * 65) + b"\n"

    with pytest.raises(RuntimeError, match="journal_record_bound_exceeded"):
        _probe()._parse_journal_records(output)


def test_boot_id_normalizes_hyphenated_and_compact_forms_identically() -> None:
    probe = _probe()

    hyphenated = probe._normalize_boot_id(BOOT_ID_HYPHENATED)
    compact = probe._normalize_boot_id(BOOT_ID_COMPACT)

    assert hyphenated == BOOT_ID_COMPACT
    assert compact == BOOT_ID_COMPACT
    assert hyphenated == compact


def test_boot_id_normalization_is_case_insensitive_but_returns_lowercase() -> None:
    probe = _probe()

    assert probe._normalize_boot_id(BOOT_ID_HYPHENATED.upper()) == BOOT_ID_COMPACT
    assert probe._normalize_boot_id(BOOT_ID_COMPACT.upper()) == BOOT_ID_COMPACT


def test_different_boot_ids_do_not_correlate() -> None:
    probe = _probe()

    assert probe._normalize_boot_id(BOOT_ID_COMPACT) != probe._normalize_boot_id(
        "638aa523d7574732811a8e938b3bb450"
    )


@pytest.mark.parametrize(
    "value",
    [
        f" {BOOT_ID_HYPHENATED}",
        f"{BOOT_ID_HYPHENATED} ",
        f"{{{BOOT_ID_HYPHENATED}}}",
        f"urn:uuid:{BOOT_ID_HYPHENATED}",
        "538aa523d7574732811a8e938b3bb45g",
        "5" * 31,
        "5" * 33,
        "538aa523-d757-4732-811a-8e938b3bb45",
        "",
    ],
)
def test_boot_id_normalization_fails_closed(value: str) -> None:
    with pytest.raises(RuntimeError, match="invalid_boot_id"):
        _probe()._normalize_boot_id(value)


def test_boot_correlation_uses_normalized_identity(
) -> None:
    evidence = _correlated_reporter_evidence(
        proc_boot_id=BOOT_ID_HYPHENATED,
        journal_boot_id=BOOT_ID_COMPACT,
    )

    assert evidence["boot_id_normalized"] == BOOT_ID_COMPACT


def test_boot_evidence_preserves_raw_values_after_normalized_correlation(
) -> None:
    evidence = _correlated_reporter_evidence(
        proc_boot_id=BOOT_ID_HYPHENATED,
        journal_boot_id=BOOT_ID_COMPACT,
    )

    assert evidence["proc_boot_id_raw"] == BOOT_ID_HYPHENATED
    assert evidence["journal_boot_id_raw"] == BOOT_ID_COMPACT
    assert evidence["_BOOT_ID"] == BOOT_ID_COMPACT


def test_manager_started_record_establishes_expected_invocation() -> None:
    lifecycle = _probe()._manager_lifecycle(UNIT, [_manager_record()])

    assert lifecycle["manager_invocation_raw"] == INVOCATION_A
    assert lifecycle["INVOCATION_ID"] == INVOCATION_A


def test_receipt_invocation_must_match_manager_invocation() -> None:
    probe = _probe()
    manager = probe._manager_lifecycle(UNIT, [_manager_record()])

    evidence = probe._reporter_evidence(
        UNIT,
        _reporter_records(),
        manager,
        proc_boot_id_raw=BOOT_ID_HYPHENATED,
    )
    assert evidence["reporter_invocation_raw"] == INVOCATION_A

    with pytest.raises(RuntimeError, match="reporter_journal_cardinality_failed"):
        probe._reporter_evidence(
            UNIT,
            _reporter_records(root_invocation=INVOCATION_B),
            manager,
            proc_boot_id_raw=BOOT_ID_HYPHENATED,
        )


def test_receipt_cannot_self_establish_expected_invocation() -> None:
    probe = _probe()
    receipt_only = _reporter_records(root_invocation=INVOCATION_B)
    assert receipt_only[1]["_SYSTEMD_INVOCATION_ID"] == INVOCATION_B

    with pytest.raises(RuntimeError, match="manager_started_cardinality_failed"):
        manager = probe._manager_lifecycle(UNIT, [])
        probe._reporter_evidence(
            UNIT,
            receipt_only,
            manager,
            proc_boot_id_raw=BOOT_ID_HYPHENATED,
        )


def test_manager_started_cardinality_fails_closed() -> None:
    probe = _probe()

    with pytest.raises(RuntimeError, match="manager_started_cardinality_failed"):
        probe._manager_lifecycle(UNIT, [])
    with pytest.raises(RuntimeError, match="manager_started_cardinality_failed"):
        probe._manager_lifecycle(UNIT, [_manager_record(), _manager_record()])


def test_manager_and_reporter_boots_are_normalized_before_exact_correlation() -> None:
    probe = _probe()
    manager = probe._manager_lifecycle(
        UNIT, [_manager_record(boot_id=BOOT_ID_HYPHENATED.upper())]
    )
    evidence = probe._reporter_evidence(
        UNIT,
        _reporter_records(boot_id=BOOT_ID_COMPACT.upper()),
        manager,
        proc_boot_id_raw=BOOT_ID_HYPHENATED,
    )

    assert manager["boot_id_normalized"] == BOOT_ID_COMPACT
    assert evidence["boot_id_normalized"] == BOOT_ID_COMPACT


def test_manager_evidence_preserves_raw_invocation_and_boot() -> None:
    raw_boot = BOOT_ID_HYPHENATED.upper()
    evidence = _probe()._manager_lifecycle(
        UNIT, [_manager_record(invocation=INVOCATION_A, boot_id=raw_boot)]
    )

    assert evidence["manager_invocation_raw"] == INVOCATION_A
    assert evidence["manager_boot_raw"] == raw_boot
    assert evidence["_BOOT_ID"] == raw_boot


def test_reporter_evidence_preserves_raw_invocation_and_boot() -> None:
    probe = _probe()
    raw_boot = BOOT_ID_COMPACT.upper()
    manager = probe._manager_lifecycle(UNIT, [_manager_record()])
    evidence = probe._reporter_evidence(
        UNIT,
        _reporter_records(boot_id=raw_boot),
        manager,
        proc_boot_id_raw=BOOT_ID_HYPHENATED,
    )

    assert evidence["reporter_invocation_raw"] == INVOCATION_A
    assert evidence["reporter_boot_raw"] == raw_boot
    assert evidence["_BOOT_ID"] == raw_boot


def test_same_manager_invocation_is_required_for_spoof_discrimination() -> None:
    probe = _probe()
    manager = probe._manager_lifecycle(UNIT, [_manager_record()])

    with pytest.raises(RuntimeError, match="reporter_journal_cardinality_failed"):
        probe._reporter_evidence(
            UNIT,
            _reporter_records(spoof_invocation=INVOCATION_B),
            manager,
            proc_boot_id_raw=BOOT_ID_HYPHENATED,
        )


def test_mechanism_result_accepts_only_complete_native_proof() -> None:
    result = _probe()._mechanism_result(_good_controls())

    assert result.main_uid == 991
    assert result.main_gid == 991
    assert result.main_no_new_privs == 1
    assert result.main_cap_prm == "0000000000000000"
    assert result.main_cap_eff == "0000000000000000"
    assert result.main_cap_amb == "0000000000000000"
    assert result.main_environment == (
        ("LD_LIBRARY_PATH", "/opt/labpilot-runner-mechanism-proof/canary/ld"),
        ("PYTHONPATH", "/opt/labpilot-runner-mechanism-proof/canary/python"),
    )
    assert result.reporter_uid == 0
    assert result.reporter_gid == 0
    assert result.service_result == "success"
    assert result.exit_code == "exited"
    assert result.exit_status == "0"
    assert result.reporter_failure_neutral is True
    assert result.loader_canary_seen is False


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        (("exit0", "main", "no_new_privs"), 0, "main_security_boundary_failed"),
        (("exit0", "main", "cap_eff"), "0000000000000001", "main_security_boundary_failed"),
        (("exit0", "reporter", "_UID"), "991", "reporter_provenance_failed"),
        (("exit0", "reporter", "_spoof_UID"), "0", "anti_spoof_proof_failed"),
        (("exit0", "reporter", "loader_environment_present"), True, "loader_canary_seen"),
        (
            ("exit71", "wait_report", "service_result"),
            "failed",
            "reporter_not_failure_neutral",
        ),
        (("exit71", "reporter", "exit_status"), "7", "terminal_variables_differ"),
    ],
)
def test_mechanism_result_fails_closed(
    path: tuple[str, str, str], value: object, reason: str
) -> None:
    controls = _good_controls()
    control, record, field = path
    nested = controls[control][record]
    assert isinstance(nested, dict)
    nested[field] = value

    with pytest.raises(RuntimeError, match=reason):
        _probe()._mechanism_result(controls)
