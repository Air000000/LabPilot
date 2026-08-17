import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from labpilot.execution.runner_policy import load_runner_policy, parse_runner_policy


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
        "launch_profile_version": "candidate-b-v1",
        "max_runtime_seconds": 3600,
        "stop_timeout_seconds": 30,
    }


def test_policy_identity_exact_golden_vector() -> None:
    policy = parse_runner_policy(_value(), controller_uid=1000, controller_gid=1001)
    assert policy.policy_identity == (
        "14e26a8bbd4ce36ef41f00ebfd8478227ba7c319433b842d438b2f682feb4229"
    )
    assert policy.runtime_profiles[0].environment == (("PATH", "/opt/python/bin:/usr/bin"),)


@pytest.mark.parametrize("extra", [{"unknown": True}, {"runtime_profiles": {}}])
def test_policy_schema_is_strict(extra: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="policy_schema"):
        parse_runner_policy({**_value(), **extra}, controller_uid=1, controller_gid=2)


def test_policy_rejects_environment_outside_fixed_allowlist() -> None:
    value = _value()
    value["runtime_profiles"] = {
        "python-3.12": {
            "runtime_path": "/opt/python/bin/python",
            "environment": {"AWS_SECRET_ACCESS_KEY": "secret"},
        }
    }
    with pytest.raises(ValueError, match="environment"):
        parse_runner_policy(value, controller_uid=1, controller_gid=2)


def test_policy_cannot_select_root_execution_identity() -> None:
    value = {**_value(), "execution_user": "root", "execution_group": "root"}
    with pytest.raises(ValueError, match="policy_identity"):
        parse_runner_policy(value, controller_uid=1, controller_gid=2)


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
