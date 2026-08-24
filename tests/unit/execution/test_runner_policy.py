import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from labpilot.execution import runner_policy
from labpilot.execution.runner_policy import load_runner_policy, parse_runner_policy

REPORTER = Path("deploy/libexec/labpilot-terminal-receipt-v1.py")
REPORTER_SHA256 = "5966c05fd18d6d16c0086fbd4f6906ac36f252cbdc5dfbf7c6df3002d43ac499"


def _manifest(
    reporter_sha256: str = REPORTER_SHA256,
) -> runner_policy.CandidateBV2ProfileManifest:
    return runner_policy.candidate_b_v2_profile_manifest(reporter_sha256)


def _value() -> dict[str, object]:
    return {
        "controller_user": "labpilot-controller",
        "controller_group": "labpilot-control",
        "execution_user": "labpilot-train",
        "execution_group": "labpilot-train",
        "socket_path": "/run/labpilot-runner/control.sock",
        "workspace_root": "/srv/labpilot/attempts",
        "runtime_profiles": {
            "python-3.12": {
                "runtime_path": "/opt/python/bin/python",
                "environment": {"PATH": "/opt/python/bin:/usr/bin"},
            }
        },
        "allowed_local_dataset_roots": ["/datasets"],
        "launch_profile_version": "candidate-b-v2",
        "max_runtime_seconds": 3600,
        "stop_timeout_seconds": 30,
    }


def test_policy_identity_exact_golden_vector() -> None:
    policy = parse_runner_policy(
        _value(),
        controller_uid=1000,
        controller_gid=1001,
        profile_manifest=_manifest(),
    )
    assert policy.policy_identity == (
        "b5ea004715593dac04f34466e60d97f260fd7ee91271714fabe80293a9ecd293"
    )
    assert policy.runtime_profiles[0].environment == (("PATH", "/opt/python/bin:/usr/bin"),)


def test_candidate_b_v2_policy_identity_binds_reporter_digest() -> None:
    manifest = _manifest()

    assert hashlib.sha256(REPORTER.read_bytes()).hexdigest() == REPORTER_SHA256
    assert manifest.launch_profile_version == "candidate-b-v2"
    assert manifest.terminal_receipt_schema == "labpilot-terminal-receipt-v1"
    assert manifest.reporter_path == (
        "/opt/labpilot-runner/libexec/labpilot-terminal-receipt-v1.py"
    )
    assert manifest.reporter_sha256 == REPORTER_SHA256
    assert runner_policy.runner_policy_identity(_value(), manifest) != (
        runner_policy.runner_policy_identity(_value(), _manifest("a" * 64))
    )


def test_policy_rejects_unsupported_launch_profile_version() -> None:
    value = {**_value(), "launch_profile_version": "candidate-b-v1"}

    with pytest.raises(ValueError, match="launch_profile_version"):
        parse_runner_policy(
            value,
            controller_uid=1,
            controller_gid=2,
            profile_manifest=_manifest(),
        )


@pytest.mark.parametrize("field", ["reporter_path", "reporter_sha256"])
def test_reporter_fields_are_not_policy_schema(field: str) -> None:
    with pytest.raises(ValueError, match="policy_schema"):
        parse_runner_policy(
            {**_value(), field: "caller-selected"},
            controller_uid=1,
            controller_gid=2,
            profile_manifest=_manifest(),
        )


@pytest.mark.parametrize("extra", [{"unknown": True}, {"runtime_profiles": {}}])
def test_policy_schema_is_strict(extra: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="policy_schema"):
        parse_runner_policy(
            {**_value(), **extra},
            controller_uid=1,
            controller_gid=2,
            profile_manifest=_manifest(),
        )


def test_policy_rejects_environment_outside_fixed_allowlist() -> None:
    value = _value()
    value["runtime_profiles"] = {
        "python-3.12": {
            "runtime_path": "/opt/python/bin/python",
            "environment": {"AWS_SECRET_ACCESS_KEY": "secret"},
        }
    }
    with pytest.raises(ValueError, match="environment"):
        parse_runner_policy(
            value,
            controller_uid=1,
            controller_gid=2,
            profile_manifest=_manifest(),
        )


def test_policy_cannot_select_root_execution_identity() -> None:
    value = {**_value(), "execution_user": "root", "execution_group": "root"}
    with pytest.raises(ValueError, match="policy_identity"):
        parse_runner_policy(
            value,
            controller_uid=1,
            controller_gid=2,
            profile_manifest=_manifest(),
        )


@pytest.mark.parametrize(
    ("mode", "uid", "message"),
    [
        (stat.S_IFLNK | 0o777, 0, "symlink"),
        (stat.S_IFDIR | 0o700, 0, "regular"),
        (stat.S_IFREG | 0o600, 1000, "root_owned"),
        (stat.S_IFREG | 0o622, 0, "permissions"),
    ],
)
def test_invalid_policy_metadata_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
    uid: int,
    message: str,
) -> None:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(_value()))
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda self: SimpleNamespace(st_mode=mode, st_uid=uid),
    )
    with pytest.raises(ValueError, match=message):
        load_runner_policy(path)


SANITY_RECEIPT = (
    b'{"active_state":null,"exec_main_code":null,'
    b'"exec_main_status":null,"result":null}\n'
)


def _trusted_deployment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    reporter_bytes: bytes = REPORTER.read_bytes(),
    bundled_bytes: bytes = REPORTER.read_bytes(),
    sanity_result: subprocess.CompletedProcess[bytes] | None = None,
) -> tuple[Path, Path, dict[str, SimpleNamespace], list[tuple[object, ...]]]:
    reporter = tmp_path / "opt/labpilot-runner/libexec/reporter.py"
    bundled = tmp_path / "app/deploy/libexec/reporter.py"
    interpreter = tmp_path / "usr/bin/python3"
    for path, content in (
        (reporter, reporter_bytes),
        (bundled, bundled_bytes),
        (interpreter, b"python"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    overrides: dict[str, SimpleNamespace] = {}
    original_lstat = Path.lstat

    def trusted_lstat(path: Path) -> SimpleNamespace:
        metadata = original_lstat(path)
        default_mode = 0o755 if stat.S_ISDIR(metadata.st_mode) else 0o555
        return overrides.get(
            str(path),
            SimpleNamespace(
                st_mode=stat.S_IFMT(metadata.st_mode) | default_mode,
                st_uid=0,
                st_gid=0,
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino,
            ),
        )

    original_fstat = os.fstat

    def trusted_fstat(fd: int) -> SimpleNamespace:
        metadata = original_fstat(fd)
        return SimpleNamespace(
            st_mode=stat.S_IFREG | 0o555,
            st_uid=0,
            st_gid=0,
            st_dev=metadata.st_dev,
            st_ino=metadata.st_ino,
        )

    calls: list[tuple[object, ...]] = []

    def sanity_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((*args, kwargs))
        return sanity_result or subprocess.CompletedProcess(
            args[0], 0, SANITY_RECEIPT, b""
        )

    monkeypatch.setattr(Path, "lstat", trusted_lstat)
    monkeypatch.setattr(runner_policy, "os", os, raising=False)
    monkeypatch.setattr(runner_policy.os, "fstat", trusted_fstat)
    monkeypatch.setattr(runner_policy, "subprocess", subprocess, raising=False)
    monkeypatch.setattr(runner_policy.subprocess, "run", sanity_run)
    monkeypatch.setattr(runner_policy, "TERMINAL_RECEIPT_BUNDLED_SOURCE_PATH", bundled)
    return reporter, interpreter, overrides, calls


def test_candidate_b_v2_deployment_validates_exact_bytes_and_isolated_sanity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reporter, interpreter, _, calls = _trusted_deployment(tmp_path, monkeypatch)
    opened: list[tuple[object, int]] = []
    original_open = os.open

    def tracked_open(path: object, flags: int) -> int:
        opened.append((path, flags))
        return original_open(path, flags)

    monkeypatch.setattr(runner_policy.os, "open", tracked_open)

    manifest = runner_policy.validate_candidate_b_v2_deployment(
        reporter, interpreter
    )

    assert manifest == _manifest()
    assert [path for path, _ in opened] == [
        reporter,
        runner_policy.TERMINAL_RECEIPT_BUNDLED_SOURCE_PATH,
    ]
    assert all(flags & os.O_NOFOLLOW for _, flags in opened)
    assert len(calls) == 1
    args, kwargs = calls[0][0], calls[0][-1]
    assert args == [str(interpreter), "-I", "-B", str(reporter)]
    assert kwargs == {
        "capture_output": True,
        "check": False,
        "env": {},
        "timeout": 2.0,
    }


@pytest.mark.parametrize(
    ("target", "mode", "uid", "message"),
    [
        ("parent", stat.S_IFLNK | 0o777, 0, "directory"),
        ("parent", stat.S_IFDIR | 0o755, 1000, "root_owned"),
        ("parent", stat.S_IFDIR | 0o775, 0, "permissions"),
        ("reporter", stat.S_IFLNK | 0o777, 0, "symlink"),
        ("reporter", stat.S_IFDIR | 0o755, 0, "regular"),
        ("reporter", stat.S_IFREG | 0o555, 1000, "root_owned"),
        ("reporter", stat.S_IFREG | 0o577, 0, "permissions"),
    ],
)
def test_candidate_b_v2_deployment_rejects_unsafe_reporter_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    mode: int,
    uid: int,
    message: str,
) -> None:
    reporter, interpreter, overrides, _ = _trusted_deployment(tmp_path, monkeypatch)
    path = reporter.parent if target == "parent" else reporter
    metadata = path.lstat()
    overrides[str(path)] = SimpleNamespace(
        **{**metadata.__dict__, "st_mode": mode, "st_uid": uid}
    )

    with pytest.raises(ValueError, match=message):
        runner_policy.validate_candidate_b_v2_deployment(reporter, interpreter)


def test_candidate_b_v2_deployment_rejects_reporter_digest_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reporter, interpreter, _, _ = _trusted_deployment(
        tmp_path, monkeypatch, reporter_bytes=b"drift"
    )

    with pytest.raises(ValueError, match="digest"):
        runner_policy.validate_candidate_b_v2_deployment(reporter, interpreter)


@pytest.mark.parametrize(
    ("mode", "uid", "message"),
    [
        (stat.S_IFDIR | 0o755, 0, "regular"),
        (stat.S_IFREG | 0o555, 1000, "root_owned"),
        (stat.S_IFREG | 0o577, 0, "permissions"),
    ],
)
def test_candidate_b_v2_deployment_rejects_unsafe_interpreter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
    uid: int,
    message: str,
) -> None:
    reporter, interpreter, overrides, _ = _trusted_deployment(tmp_path, monkeypatch)
    metadata = interpreter.lstat()
    overrides[str(interpreter)] = SimpleNamespace(
        **{**metadata.__dict__, "st_mode": mode, "st_uid": uid}
    )

    with pytest.raises(ValueError, match=message):
        runner_policy.validate_candidate_b_v2_deployment(reporter, interpreter)


@pytest.mark.parametrize(
    "result",
    [
        subprocess.CompletedProcess([], 1, SANITY_RECEIPT, b""),
        subprocess.CompletedProcess([], 0, b"not-canonical\n", b""),
        subprocess.CompletedProcess([], 0, SANITY_RECEIPT, b"unexpected"),
    ],
)
def test_candidate_b_v2_deployment_rejects_failed_sanity_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: subprocess.CompletedProcess[bytes],
) -> None:
    reporter, interpreter, _, _ = _trusted_deployment(
        tmp_path, monkeypatch, sanity_result=result
    )

    with pytest.raises(ValueError, match="sanity"):
        runner_policy.validate_candidate_b_v2_deployment(reporter, interpreter)


def test_load_policy_binds_validated_manifest_before_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(_value()))
    metadata = path.lstat()
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda self: SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_uid=0,
            st_gid=0,
            st_dev=metadata.st_dev,
            st_ino=metadata.st_ino,
        ),
    )
    validated = _manifest("b" * 64)
    validations: list[object] = []
    monkeypatch.setattr(
        runner_policy,
        "validate_candidate_b_v2_deployment",
        lambda: validations.append(True) or validated,
        raising=False,
    )
    monkeypatch.setattr(runner_policy.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=1))
    monkeypatch.setattr(runner_policy.grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=2))

    policy = load_runner_policy(path)

    assert validations == [True]
    assert policy.policy_identity == runner_policy.runner_policy_identity(_value(), validated)
