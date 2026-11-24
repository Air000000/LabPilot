import sys
from importlib import import_module
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parents[3]))
cli = import_module("scripts.coding_task")


@pytest.mark.parametrize(
    "endpoint", [None, "unix:///run/labpilot-docker.sock", "unix:///private/custom-docker.sock"]
)
def test_worker_preserves_explicit_endpoint_without_extra_environment(
    monkeypatch, tmp_path, endpoint
):
    worker = tmp_path / "tools/coding_worker/.venv/bin/python"
    worker.parent.mkdir(parents=True)
    worker.touch()
    monkeypatch.setattr(cli, "__file__", str(tmp_path / "scripts/coding_task.py"))
    monkeypatch.setattr(sys, "argv", ["coding_task.py", "inspect", "--task", "/private/task"])
    monkeypatch.setenv("DOCKER_CONTEXT", "unselected-context")
    monkeypatch.setenv("OTHER_SECRET", "must-not-forward")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key")
    if endpoint:
        monkeypatch.setenv("DOCKER_HOST", endpoint)
    else:
        monkeypatch.delenv("DOCKER_HOST", raising=False)
    launch = Mock()
    monkeypatch.setattr(cli.os, "execve", launch)
    cli.main()
    executable, argv, environment = launch.call_args.args
    assert executable == worker
    assert argv == [
        str(worker),
        str(tmp_path / "tools/coding_worker/coding_task.py"),
        "inspect",
        "--task",
        "/private/task",
    ]
    assert environment.get("DOCKER_HOST") == endpoint
    assert environment["DEEPSEEK_API_KEY"] == "synthetic-key"
    assert "DOCKER_CONTEXT" not in environment and "OTHER_SECRET" not in environment
