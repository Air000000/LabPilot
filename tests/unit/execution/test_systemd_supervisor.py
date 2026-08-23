import json
import subprocess
from dataclasses import replace

import pytest

from labpilot.execution.systemd_supervisor import (
    ControlOutcomeKind,
    PhysicalObservationKind,
    SystemdSupervisor,
)
from labpilot.execution.types import FrozenLaunchSpec, frozen_launch_spec_hash

X = f"labpilot-v1-{'a' * 64}-{'b' * 32}"
UNIT = f"{X}.service"
BOOT_A = "a" * 32
BOOT_B = "b" * 32
INVOCATION_A = "1" * 32
INVOCATION_B = "2" * 32
STARTING_ID = "7d4958e842da4a758f6c1cdc7b36dcc5"
STARTED_ID = "39f53479d3a045ac8e11786248231fbf"
SUCCESS_ID = "7ad2d189f7e94e70a38c781354912448"
PROCESS_EXIT_ID = "98e322203f7a4ed290d09fe03c09fe15"
FAILURE_ID = "d9b373ed55a64feb8242e02dbe79a49c"
STOPPING_ID = "de5b426a63be47a7b6ac3eaac82e2f6f"
STOPPED_ID = "9d1aaa27d60140bd96365438aad20286"


def _spec() -> FrozenLaunchSpec:
    return FrozenLaunchSpec(
        "v1",
        "/srv/labpilot/attempts/a",
        "a" * 64,
        "python-3.12",
        "/opt/python/bin/python",
        "train.py",
        ".",
        ("--output", "/srv/labpilot/attempts/a/runtime/output"),
        "uri",
        "s3://dataset/v1",
        "b" * 64,
        (("PATH", "/opt/python/bin:/usr/bin"),),
        300,
        "labpilot-train",
        "labpilot-train",
        "candidate-b-v2",
        "c" * 64,
    )


class Commands:
    def __init__(self, outputs: list[tuple[int, str]]) -> None:
        self.outputs = outputs
        self.argv: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
        assert timeout == 10.0
        self.argv.append(argv)
        code, stdout = self.outputs.pop(0)
        return subprocess.CompletedProcess(argv, code, stdout, "")


def _loaded(*, active: str, description: str) -> str:
    return (
        "LoadState=loaded\n"
        f"ActiveState={active}\n"
        f"Description={description}\n"
        f"Result=success\nMainPID=42\nInvocationID={INVOCATION_A}\n"
        "ExecMainCode=1\nExecMainStatus=0\n"
    )


def _manager_record(
    message: str,
    message_id: str | None,
    *,
    unit: str = UNIT,
    boot_id: str = BOOT_A,
    invocation_id: str = INVOCATION_A,
    **fields: str,
) -> dict[str, str]:
    record = {
        "_PID": "1",
        "_SYSTEMD_UNIT": "init.scope",
        "SYSLOG_IDENTIFIER": "systemd",
        "UNIT": unit,
        "_BOOT_ID": boot_id,
        "INVOCATION_ID": invocation_id,
        "MESSAGE": message,
        **fields,
    }
    if message_id is not None:
        record["MESSAGE_ID"] = message_id
    return record


def _start_records(
    execution_spec_hash: str,
    *,
    unit: str = UNIT,
    boot_id: str = BOOT_A,
    invocation_id: str = INVOCATION_A,
) -> list[dict[str, str]]:
    description = f"labpilot:{execution_spec_hash}"
    return [
        _manager_record(
            f"Starting {unit} - {description}...",
            STARTING_ID,
            unit=unit,
            boot_id=boot_id,
            invocation_id=invocation_id,
            JOB_TYPE="start",
        ),
        _manager_record(
            f"Started {unit} - {description}.",
            STARTED_ID,
            unit=unit,
            boot_id=boot_id,
            invocation_id=invocation_id,
            JOB_TYPE="start",
            JOB_RESULT="done",
        ),
    ]


def _journal_supervisor(
    records: list[dict[str, str]],
    *,
    receipts: list[dict[str, object]] | None = None,
    receipt_returncode: int = 0,
    current_boot_id: str = BOOT_B,
) -> SystemdSupervisor:
    return SystemdSupervisor(
        Commands(
            [
                (0, "LoadState=not-found\n"),
                (0, "\n".join(json.dumps(record) for record in records)),
                (
                    receipt_returncode,
                    "\n".join(json.dumps(record) for record in receipts or []),
                ),
            ]
        ),
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
        boot_id=current_boot_id,
    )


def _receipt_message(
    service_result: str | None,
    exit_code: str | None,
    exit_status: str | None,
) -> str:
    return json.dumps(
        {
            "schema_version": "labpilot-terminal-receipt-v1",
            "service_result": service_result,
            "exit_code": exit_code,
            "exit_status": exit_status,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _receipt_record(
    service_result: str | None,
    exit_code: str | None,
    exit_status: str | None,
    *,
    unit: str = UNIT,
    uid: str = "0",
    gid: str = "0",
    invocation_id: str = INVOCATION_A,
    boot_id: str = BOOT_A,
) -> dict[str, object]:
    return {
        "_SYSTEMD_UNIT": unit,
        "_UID": uid,
        "_GID": gid,
        "_SYSTEMD_INVOCATION_ID": invocation_id,
        "_BOOT_ID": boot_id,
        "MESSAGE": _receipt_message(service_result, exit_code, exit_status),
    }


def test_same_x_different_h_is_identity_conflict_without_creation() -> None:
    commands = Commands([(0, _loaded(active="active", description="labpilot:a"))])
    supervisor = SystemdSupervisor(
        commands,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )
    spec = _spec()

    outcome = supervisor.launch(
        X, frozen_launch_spec_hash(spec), spec, creation_authority=True
    )

    assert outcome.kind is ControlOutcomeKind.IDENTITY_CONFLICT
    assert len(commands.argv) == 1
    assert all("systemd-run" not in argv for argv in commands.argv)


def test_not_found_never_creates_without_positive_authority() -> None:
    commands = Commands([(0, "LoadState=not-found\n"), (0, "")])
    supervisor = SystemdSupervisor(
        commands,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )
    spec = _spec()

    outcome = supervisor.launch(
        X, frozen_launch_spec_hash(spec), spec, creation_authority=False
    )

    assert outcome.kind is ControlOutcomeKind.DEFINITE_NON_ACCEPTANCE
    assert len(commands.argv) == 2


def test_stop_only_runs_after_fresh_exact_active_match() -> None:
    expected = "a" * 64
    commands = Commands(
        [
            (0, _loaded(active="failed", description=f"labpilot:{expected}")),
            (0, _loaded(active="active", description=f"labpilot:{expected}")),
            (0, ""),
        ]
    )
    supervisor = SystemdSupervisor(
        commands,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )

    blocked = supervisor.stop(X, expected)
    accepted = supervisor.stop(X, expected)

    assert blocked.kind is ControlOutcomeKind.TERMINAL_MATCH
    assert accepted.kind is ControlOutcomeKind.CONTROL_ACCEPTED
    assert commands.argv[-1][:4] == ("systemctl", "--system", "--no-ask-password", "stop")
    assert commands.argv[-1][4] == UNIT


def test_workload_stdout_cannot_fabricate_terminal_evidence() -> None:
    fake = json.dumps(
        {
            "_SYSTEMD_UNIT": "some.service",
            "MESSAGE": json.dumps({"event": "terminal", "execution_spec_hash": "a" * 64}),
        }
    )
    commands = Commands([(0, "LoadState=not-found\n"), (0, fake)])
    supervisor = SystemdSupervisor(
        commands,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )

    observation = supervisor.observe(X, "a" * 64)

    assert observation.kind is PhysicalObservationKind.NOT_FOUND


def test_pid1_journal_terminal_evidence_survives_unit_gc() -> None:
    expected = "a" * 64
    commands = Commands(
        [
            (0, "LoadState=not-found\n"),
            (
                0,
                json.dumps(
                    {
                        "_PID": "1",
                        "_SYSTEMD_UNIT": "init.scope",
                        "SYSLOG_IDENTIFIER": "systemd",
                        "UNIT": UNIT,
                        "MESSAGE": (
                            f"Finished {UNIT} - labpilot:{expected}."
                        ),
                    }
                ),
            ),
        ]
    )
    supervisor = SystemdSupervisor(
        commands,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )
    assert supervisor.observe(X, expected).kind is PhysicalObservationKind.TERMINAL_MATCH
    assert commands.argv[0][4] == UNIT
    assert UNIT in commands.argv[1][1]


def test_journal_tail_is_applied_after_exact_trusted_manager_selection() -> None:
    commands = Commands([(0, "LoadState=not-found\n"), (0, "")])
    supervisor = SystemdSupervisor(
        commands,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )

    supervisor.observe(X, "a" * 64)

    assert commands.argv[1] == (
        "journalctl",
        f"UNIT={UNIT}",
        "_PID=1",
        "_SYSTEMD_UNIT=init.scope",
        "SYSLOG_IDENTIFIER=systemd",
        "--output=json",
        "--no-pager",
        "-n",
        "100",
    )


def test_real_natural_exit_zero_survives_unit_gc() -> None:
    expected = "a" * 64
    records = [
        *_start_records(expected),
        _manager_record(f"{UNIT}: Deactivated successfully.", SUCCESS_ID),
    ]

    observation = _journal_supervisor(
        records,
        receipts=[_receipt_record("success", "exited", "0")],
    ).observe(X, expected)

    assert observation.kind is PhysicalObservationKind.TERMINAL_MATCH
    assert observation.observed_spec_hash == expected
    assert observation.invocation_id == INVOCATION_A
    assert observation.boot_id == BOOT_A
    assert observation.result == "success"
    assert observation.exit_code == 0
    assert observation.termination_signal is None


def test_loaded_v1_real_exit_zero_precedes_receipt_absence() -> None:
    expected = "a" * 64
    supervisor = SystemdSupervisor(
        Commands([(0, _loaded(active="inactive", description=f"labpilot:{expected}"))]),
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
        boot_id=BOOT_A,
    )

    observation = supervisor.observe(X, expected)

    assert observation.kind is PhysicalObservationKind.TERMINAL_MATCH
    assert observation.result == "success"
    assert observation.exit_code == 0
    assert observation.termination_signal is None


def test_loaded_v1_real_sigkill_precedes_receipt_absence() -> None:
    expected = "a" * 64
    loaded = _loaded(active="failed", description=f"labpilot:{expected}").replace(
        "Result=success", "Result=signal"
    ).replace("ExecMainCode=1\nExecMainStatus=0", "ExecMainCode=2\nExecMainStatus=9")
    supervisor = SystemdSupervisor(
        Commands([(0, loaded)]),
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
        boot_id=BOOT_A,
    )

    observation = supervisor.observe(X, expected)

    assert observation.kind is PhysicalObservationKind.TERMINAL_MATCH
    assert observation.result == "signal"
    assert observation.exit_code is None
    assert observation.termination_signal == 9


def test_retained_v1_real_exit_seven_precedes_receipt_absence() -> None:
    expected = "a" * 64
    records = [
        *_start_records(expected),
        _manager_record(
            f"{UNIT}: Main process exited, code=exited, status=7/NOTRUNNING",
            PROCESS_EXIT_ID,
            EXIT_CODE="exited",
            EXIT_STATUS="7",
        ),
        _manager_record(f"{UNIT}: Failed with result 'exit-code'.", FAILURE_ID),
    ]

    observation = _journal_supervisor(records).observe(X, expected)

    assert observation.kind is PhysicalObservationKind.TERMINAL_MATCH
    assert observation.result == "exit-code"
    assert observation.exit_code == 7
    assert observation.termination_signal is None


def test_gc_v1_manager_only_without_receipt_is_terminal_unclassified() -> None:
    expected = "a" * 64
    records = [
        *_start_records(expected),
        _manager_record(f"{UNIT}: Deactivated successfully.", SUCCESS_ID),
    ]

    observation = _journal_supervisor(records).observe(X, expected)

    assert observation.kind is PhysicalObservationKind.TERMINAL_MATCH
    assert observation.result == "success"
    assert observation.exit_code is None
    assert observation.termination_signal is None


@pytest.mark.parametrize(
    (
        "receipt_values",
        "manager_result",
        "expected_result",
        "expected_exit_code",
        "expected_signal",
    ),
    [
        (("success", "exited", "0"), "success", "success", 0, None),
        (("exit-code", "exited", "7"), "exit-code", "exit-code", 7, None),
        (("success", "killed", "TERM"), "success", "success", None, 15),
        (("signal", "killed", "KILL"), "signal", "signal", None, 9),
        (("timeout", "killed", "TERM"), "timeout", "timeout", None, None),
        (("watchdog", "killed", "ABRT"), "success", "watchdog", None, None),
    ],
)
def test_loaded_and_gc_receipt_causes_are_equivalent(
    receipt_values: tuple[str, str, str],
    manager_result: str,
    expected_result: str,
    expected_exit_code: int | None,
    expected_signal: int | None,
) -> None:
    expected = "a" * 64
    loaded = (
        _loaded(active="inactive", description=f"labpilot:{expected}")
        .replace("Result=success", f"Result={manager_result}")
        .replace("ExecMainCode=1\nExecMainStatus=0", "ExecMainCode=\nExecMainStatus=")
    )
    receipt = _receipt_record(*receipt_values)
    loaded_supervisor = SystemdSupervisor(
        Commands([(0, loaded), (0, json.dumps(receipt))]),
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
        boot_id=BOOT_A,
    )
    terminal_record = (
        _manager_record(f"{UNIT}: Deactivated successfully.", SUCCESS_ID)
        if manager_result == "success"
        else _manager_record(
            f"{UNIT}: Failed with result '{manager_result}'.",
            FAILURE_ID,
        )
    )
    gc_supervisor = _journal_supervisor(
        [*_start_records(expected), terminal_record],
        receipts=[receipt],
        current_boot_id=BOOT_A,
    )

    loaded_observation = loaded_supervisor.observe(X, expected)
    gc_observation = gc_supervisor.observe(X, expected)

    assert loaded_observation == gc_observation
    assert loaded_observation.kind is PhysicalObservationKind.TERMINAL_MATCH
    assert loaded_observation.result == expected_result
    assert loaded_observation.exit_code == expected_exit_code
    assert loaded_observation.termination_signal == expected_signal


def test_receipt_query_filters_provenance_before_tail_bound() -> None:
    expected = "a" * 64
    commands = Commands(
        [
            (0, "LoadState=not-found\n"),
            (
                0,
                "\n".join(
                    json.dumps(record)
                    for record in [
                        *_start_records(expected),
                        _manager_record(
                            f"{UNIT}: Deactivated successfully.", SUCCESS_ID
                        ),
                    ]
                ),
            ),
            (0, ""),
        ]
    )
    supervisor = SystemdSupervisor(
        commands,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )

    supervisor.observe(X, expected)

    assert commands.argv[2] == (
        "journalctl",
        f"_SYSTEMD_UNIT={UNIT}",
        "_UID=0",
        "_GID=0",
        f"_SYSTEMD_INVOCATION_ID={INVOCATION_A}",
        f"_BOOT_ID={BOOT_A}",
        "--output=json",
        "--no-pager",
        "-n",
        "10",
    )


@pytest.mark.parametrize(
    ("receipts", "receipt_returncode"),
    [
        (
            [
                {
                    "_SYSTEMD_UNIT": UNIT,
                    "_UID": "0",
                    "_GID": "0",
                    "_SYSTEMD_INVOCATION_ID": INVOCATION_A,
                    "_BOOT_ID": BOOT_A,
                    "MESSAGE": "not-json",
                }
            ],
            0,
        ),
        ([_receipt_record("success", "exited", "0")] * 2, 0),
        ([], 1),
    ],
)
def test_malformed_duplicate_or_unavailable_receipt_is_unknown(
    receipts: list[dict[str, object]],
    receipt_returncode: int,
) -> None:
    expected = "a" * 64
    records = [
        *_start_records(expected),
        _manager_record(f"{UNIT}: Deactivated successfully.", SUCCESS_ID),
    ]

    observation = _journal_supervisor(
        records,
        receipts=receipts,
        receipt_returncode=receipt_returncode,
    ).observe(X, expected)

    assert observation.kind is PhysicalObservationKind.UNKNOWN


@pytest.mark.parametrize(
    "receipt",
    [
        _receipt_record("success", "exited", "0", invocation_id=INVOCATION_B),
        _receipt_record("success", "exited", "0", boot_id=BOOT_B),
        _receipt_record("success", "exited", "0", unit="other.service"),
        _receipt_record("success", "exited", "0", uid="995", gid="982"),
    ],
)
def test_wrong_correlation_or_workload_spoof_cannot_supply_cause(
    receipt: dict[str, object],
) -> None:
    expected = "a" * 64
    records = [
        *_start_records(expected),
        _manager_record(f"{UNIT}: Deactivated successfully.", SUCCESS_ID),
    ]

    observation = _journal_supervisor(records, receipts=[receipt]).observe(X, expected)

    assert observation.kind is PhysicalObservationKind.TERMINAL_MATCH
    assert observation.exit_code is None
    assert observation.termination_signal is None


def test_byte_identical_workload_spoof_does_not_create_duplicate_receipt() -> None:
    expected = "a" * 64
    root_receipt = _receipt_record("success", "exited", "0")
    workload_spoof = {**root_receipt, "_UID": "995", "_GID": "982"}
    records = [
        *_start_records(expected),
        _manager_record(f"{UNIT}: Deactivated successfully.", SUCCESS_ID),
    ]

    observation = _journal_supervisor(
        records,
        receipts=[workload_spoof, root_receipt],
    ).observe(X, expected)

    assert observation.kind is PhysicalObservationKind.TERMINAL_MATCH
    assert observation.exit_code == 0


def test_wrong_h_is_identity_conflict_before_receipt_cause_use() -> None:
    expected = "a" * 64
    wrong = "f" * 64
    commands = Commands(
        [
            (0, "LoadState=not-found\n"),
            (
                0,
                "\n".join(
                    json.dumps(record)
                    for record in [
                        *_start_records(wrong),
                        _manager_record(
                            f"{UNIT}: Deactivated successfully.", SUCCESS_ID
                        ),
                    ]
                ),
            ),
            (0, json.dumps(_receipt_record("success", "exited", "0"))),
        ]
    )
    supervisor = SystemdSupervisor(
        commands,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )

    observation = supervisor.observe(X, expected)

    assert observation.kind is PhysicalObservationKind.IDENTITY_CONFLICT
    assert len(commands.argv) == 2


def test_matching_start_without_terminal_record_remains_unknown() -> None:
    expected = "a" * 64

    observation = _journal_supervisor(_start_records(expected)).observe(X, expected)

    assert observation.kind is PhysicalObservationKind.UNKNOWN
    assert observation.observed_spec_hash == expected
    assert observation.invocation_id == INVOCATION_A
    assert observation.boot_id == BOOT_A
    assert observation.result == "systemd_manager_start_retained"


def test_wrong_hash_start_remains_identity_conflict_after_terminal_record() -> None:
    expected = "a" * 64
    wrong = "f" * 64
    records = [
        *_start_records(wrong),
        _manager_record(f"{UNIT}: Deactivated successfully.", SUCCESS_ID),
    ]

    observation = _journal_supervisor(records).observe(X, expected)

    assert observation.kind is PhysicalObservationKind.IDENTITY_CONFLICT
    assert observation.observed_spec_hash == wrong
    assert observation.boot_id == BOOT_A


def test_real_nonzero_exit_preserves_structured_exit_status_after_gc() -> None:
    expected = "a" * 64
    records = [
        *_start_records(expected),
        _manager_record(
            f"{UNIT}: Main process exited, code=exited, status=7/NOTRUNNING",
            PROCESS_EXIT_ID,
            EXIT_CODE="exited",
            EXIT_STATUS="7",
        ),
        _manager_record(
            f"{UNIT}: Failed with result 'exit-code'.",
            FAILURE_ID,
        ),
    ]

    observation = _journal_supervisor(records).observe(X, expected)

    assert observation.kind is PhysicalObservationKind.TERMINAL_MATCH
    assert observation.result == "exit-code"
    assert observation.exit_code == 7
    assert observation.termination_signal is None


def test_real_signal_preserves_structured_signal_after_gc() -> None:
    expected = "a" * 64
    records = [
        *_start_records(expected),
        _manager_record(
            f"{UNIT}: Main process exited, code=killed, status=9/KILL",
            PROCESS_EXIT_ID,
            EXIT_CODE="killed",
            EXIT_STATUS="9",
        ),
        _manager_record(
            f"{UNIT}: Failed with result 'signal'.",
            FAILURE_ID,
        ),
    ]

    observation = _journal_supervisor(records).observe(X, expected)

    assert observation.kind is PhysicalObservationKind.TERMINAL_MATCH
    assert observation.result == "signal"
    assert observation.exit_code is None
    assert observation.termination_signal == 9


@pytest.mark.parametrize(
    ("terminal_invocation", "terminal_boot"),
    ((INVOCATION_B, BOOT_A), (INVOCATION_A, BOOT_B)),
)
def test_real_signal_requires_matching_invocation_and_boot_after_gc(
    terminal_invocation: str, terminal_boot: str
) -> None:
    expected = "a" * 64
    records = [
        *_start_records(expected),
        _manager_record(
            f"{UNIT}: Main process exited, code=killed, status=9/KILL",
            PROCESS_EXIT_ID,
            invocation_id=terminal_invocation,
            boot_id=terminal_boot,
            EXIT_CODE="killed",
            EXIT_STATUS="9",
        ),
        _manager_record(
            f"{UNIT}: Failed with result 'signal'.",
            FAILURE_ID,
            invocation_id=terminal_invocation,
            boot_id=terminal_boot,
        ),
    ]

    observation = _journal_supervisor(records).observe(X, expected)

    assert observation.kind is PhysicalObservationKind.UNKNOWN
    assert observation.invocation_id == INVOCATION_A
    assert observation.boot_id == BOOT_A


def test_real_explicit_stop_receipt_preserves_physical_signal() -> None:
    expected = "a" * 64
    description = f"labpilot:{expected}"
    records = [
        *_start_records(expected),
        _manager_record(
            f"Stopping {UNIT} - {description}...",
            STOPPING_ID,
            JOB_TYPE="stop",
        ),
        _manager_record(f"{UNIT}: Deactivated successfully.", SUCCESS_ID),
        _manager_record(
            f"Stopped {UNIT} - {description}.",
            STOPPED_ID,
            JOB_TYPE="stop",
            JOB_RESULT="done",
        ),
    ]

    observation = _journal_supervisor(
        records,
        receipts=[_receipt_record("success", "killed", "TERM")],
    ).observe(X, expected)

    assert observation.kind is PhysicalObservationKind.TERMINAL_MATCH
    assert observation.result == "success"
    assert observation.exit_code is None
    assert observation.termination_signal == 15


def test_real_runtime_max_timeout_preserves_timeout_result_after_gc() -> None:
    expected = "a" * 64
    records = [
        *_start_records(expected),
        _manager_record(
            f"{UNIT}: Service reached runtime time limit. Stopping.",
            None,
        ),
        _manager_record(
            f"{UNIT}: Main process exited, code=killed, status=15/TERM",
            PROCESS_EXIT_ID,
            EXIT_CODE="killed",
            EXIT_STATUS="15",
        ),
        _manager_record(
            f"{UNIT}: Failed with result 'timeout'.",
            FAILURE_ID,
        ),
    ]

    observation = _journal_supervisor(
        records,
        receipts=[_receipt_record("timeout", "killed", "TERM")],
    ).observe(X, expected)

    assert observation.kind is PhysicalObservationKind.TERMINAL_MATCH
    assert observation.result == "timeout"
    assert observation.exit_code is None
    assert observation.termination_signal is None


def test_loaded_runtime_max_timeout_precedes_killed_term() -> None:
    expected = "a" * 64
    loaded = (
        _loaded(active="failed", description=f"labpilot:{expected}")
        .replace("Result=success", "Result=timeout")
        .replace("ExecMainCode=1\nExecMainStatus=0", "ExecMainCode=2\nExecMainStatus=15")
    )
    supervisor = SystemdSupervisor(
        Commands([(0, loaded)]),
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
        boot_id=BOOT_A,
    )

    observation = supervisor.observe(X, expected)

    assert observation.kind is PhysicalObservationKind.TERMINAL_MATCH
    assert observation.result == "timeout"
    assert observation.exit_code is None
    assert observation.termination_signal is None


def test_workload_cannot_spoof_real_systemd_259_terminal_shape() -> None:
    expected = "a" * 64
    fake = _manager_record(
        f"{UNIT}: Deactivated successfully.",
        SUCCESS_ID,
    )
    fake["_PID"] = "4242"
    fake["_SYSTEMD_UNIT"] = UNIT

    observation = _journal_supervisor([fake]).observe(X, expected)

    assert observation.kind is PhysicalObservationKind.NOT_FOUND


def test_correlated_terminal_preserves_historical_boot_not_current_boot() -> None:
    expected = "a" * 64
    records = [
        *_start_records(expected, boot_id=BOOT_A),
        _manager_record(
            f"{UNIT}: Deactivated successfully.",
            SUCCESS_ID,
            boot_id=BOOT_A,
        ),
    ]

    observation = _journal_supervisor(records, current_boot_id=BOOT_B).observe(X, expected)

    assert observation.kind is PhysicalObservationKind.TERMINAL_MATCH
    assert observation.boot_id == BOOT_A


def test_unrelated_invocation_terminal_does_not_complete_matching_start() -> None:
    expected = "a" * 64
    records = [
        *_start_records(expected, invocation_id=INVOCATION_A),
        _manager_record(
            f"{UNIT}: Deactivated successfully.",
            SUCCESS_ID,
            invocation_id=INVOCATION_B,
        ),
    ]

    observation = _journal_supervisor(records).observe(X, expected)

    assert observation.kind is PhysicalObservationKind.UNKNOWN
    assert observation.invocation_id == INVOCATION_A
    assert observation.result == "systemd_manager_start_retained"


def test_launch_uses_exact_fixed_systemd_profile() -> None:
    commands = Commands([(0, "LoadState=not-found\n"), (0, ""), (0, "")])
    supervisor = SystemdSupervisor(
        commands,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )
    spec = _spec()

    assert (
        supervisor.launch(
            X,
            frozen_launch_spec_hash(spec),
            spec,
            creation_authority=True,
        ).kind
        is ControlOutcomeKind.CREATED
    )
    h = frozen_launch_spec_hash(spec)
    assert commands.argv[-1] == (
        "systemd-run",
        "--system",
        "--no-ask-password",
        f"--unit={UNIT}",
        f"--description=labpilot:{h}",
        "--service-type=exec",
        "--expand-environment=no",
        "--property=Restart=no",
        "--property=KillMode=control-group",
        "--property=NoNewPrivileges=yes",
        "--property=AmbientCapabilities=",
        "--property=CapabilityBoundingSet=",
        "--property=StandardOutput=journal",
        "--property=StandardError=journal",
        "--property=UnsetEnvironment=LD_AUDIT LD_DEBUG LD_LIBRARY_PATH LD_PRELOAD "
        "PYTHONHOME PYTHONPATH",
        "--property=ExecStopPost=-+/usr/bin/python3 -I -B "
        "/opt/labpilot-runner/libexec/labpilot-terminal-receipt-v1.py",
        "--property=RuntimeMaxSec=300s",
        "--property=TimeoutStopSec=30s",
        "--uid=labpilot-train",
        "--gid=labpilot-train",
        "--working-directory=/srv/labpilot/attempts/a/source/.",
        "--",
        "/usr/bin/env",
        "-i",
        "PATH=/opt/python/bin:/usr/bin",
        "/opt/python/bin/python",
        "/srv/labpilot/attempts/a/source/train.py",
        "--output",
        "/srv/labpilot/attempts/a/runtime/output",
    )


def test_launch_environment_is_only_inside_nonroot_env_i() -> None:
    supervisor = SystemdSupervisor(
        Commands([]),
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )
    spec = replace(
        _spec(),
        environment=(
            ("LD_LIBRARY_PATH", "/workspace/loader-canary"),
            ("PYTHONPATH", "/workspace/import-canary"),
        ),
    )

    argv = supervisor._launch_argv(X, "a" * 64, spec)

    env_index = argv.index("/usr/bin/env")
    reporter = (
        "--property=ExecStopPost=-+/usr/bin/python3 -I -B "
        "/opt/labpilot-runner/libexec/labpilot-terminal-receipt-v1.py"
    )
    assert "--setenv=LD_LIBRARY_PATH=/workspace/loader-canary" not in argv
    assert argv.index("LD_LIBRARY_PATH=/workspace/loader-canary") > env_index
    assert argv.index("PYTHONPATH=/workspace/import-canary") > env_index
    assert argv.index(reporter) < env_index


def test_launch_profile_has_no_caller_selected_root_hook() -> None:
    spec = _spec()

    assert not hasattr(spec, "exec_stop_post")
    assert not hasattr(spec, "reporter_path")
    assert not hasattr(spec, "reporter_argv")


def test_terminal_signal_is_not_invented_as_exit_code() -> None:
    expected = "a" * 64
    loaded = _loaded(active="failed", description=f"labpilot:{expected}").replace(
        "ExecMainCode=1\nExecMainStatus=0",
        "ExecMainCode=2\nExecMainStatus=15",
    )
    supervisor = SystemdSupervisor(
        Commands([(0, loaded)]),
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )

    observation = supervisor.observe(X, expected)

    assert observation.exit_code is None
    assert observation.termination_signal == 15


def test_observe_uses_exact_production_x_service() -> None:
    commands = Commands([(0, "LoadState=not-found\n"), (0, "")])
    supervisor = SystemdSupervisor(
        commands,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )

    supervisor.observe(X, "a" * 64)

    assert commands.argv[0][4] == UNIT
    assert UNIT in commands.argv[1][1]


@pytest.mark.parametrize(
    "invalid_identity",
    (
        "ssh",
        "ssh.service",
        "../ssh",
        "/etc/systemd/system/ssh.service",
        "labpilot-v1-x",
        f"{X} ",
        f"{X}\n",
        f"labpilot-v1-{'a' * 63}-{'b' * 32}",
        f"labpilot-v1-{'g' * 64}-{'b' * 32}",
    ),
)
def test_malformed_execution_identity_is_rejected_before_systemd(
    invalid_identity: str,
) -> None:
    commands = Commands(
        [(0, _loaded(active="active", description=f"labpilot:{'a' * 64}"))]
    )
    supervisor = SystemdSupervisor(
        commands,
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
    )

    with pytest.raises(ValueError, match="execution_identity_invalid"):
        supervisor.observe(invalid_identity, "a" * 64)

    assert commands.argv == []


def test_loaded_unit_reports_current_boot_id() -> None:
    supervisor = SystemdSupervisor(
        Commands([(0, _loaded(active="active", description=f"labpilot:{'a' * 64}"))]),
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
        boot_id=BOOT_B,
    )

    observation = supervisor.observe(X, "a" * 64)

    assert observation.boot_id == BOOT_B


@pytest.mark.parametrize("journal_boot_id", [None, "not-a-boot-id"])
def test_missing_or_malformed_journal_boot_id_is_not_replaced_with_current_boot(
    journal_boot_id: str | None,
) -> None:
    record = {
        "_PID": "1",
        "_SYSTEMD_UNIT": "init.scope",
        "SYSLOG_IDENTIFIER": "systemd",
        "UNIT": UNIT,
        "MESSAGE": f"Finished {UNIT} - labpilot:{'a' * 64}.",
    }
    if journal_boot_id is not None:
        record["_BOOT_ID"] = journal_boot_id
    supervisor = SystemdSupervisor(
        Commands([(0, "LoadState=not-found\n"), (0, json.dumps(record))]),
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
        boot_id=BOOT_B,
    )

    observation = supervisor.observe(X, "a" * 64)

    assert observation.kind is PhysicalObservationKind.TERMINAL_MATCH
    assert observation.boot_id is None


def test_journal_terminal_preserves_historical_boot_id() -> None:
    record = {
        "_PID": "1",
        "_SYSTEMD_UNIT": "init.scope",
        "SYSLOG_IDENTIFIER": "systemd",
        "UNIT": UNIT,
        "_BOOT_ID": BOOT_A,
        "MESSAGE": f"Finished {UNIT} - labpilot:{'a' * 64}.",
    }
    supervisor = SystemdSupervisor(
        Commands([(0, "LoadState=not-found\n"), (0, json.dumps(record))]),
        execution_user="labpilot-train",
        execution_group="labpilot-train",
        stop_timeout_seconds=30,
        boot_id=BOOT_B,
    )

    observation = supervisor.observe(X, "a" * 64)

    assert observation.kind is PhysicalObservationKind.TERMINAL_MATCH
    assert observation.boot_id == BOOT_A
