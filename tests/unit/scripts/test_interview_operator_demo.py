from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
diagnostics = import_module("scripts.interview_demo_diagnostics")
STUB = r"""#!/usr/bin/python3
import json, os, sys
from pathlib import Path
from types import SimpleNamespace
state_path = Path(os.environ['FAKE_STATE'])
state = json.loads(state_path.read_text())
name = Path(sys.argv[0]).name
args = sys.argv[1:]
state['calls'].append([name, *args])
code = 0
if name == 'id':
    print(12345 if len(args) > 1 else 0)
elif name == 'stat':
    print({'%U': 'preview-user', '%u': '12345'}.get(args[1])
          or format(Path(args[2]).stat().st_mode & 0o777, 'o'))
elif name == 'getent':
    if args[0] == 'passwd':
        print(f"{args[1]}:x:12345:12345::" + state['home'] + ":/bin/bash")
elif name == 'runuser':
    if args[-1] == '18001':
        state['api_roots'] = json.loads(os.environ['LABPILOT_REPOSITORY_ALLOWED_ROOTS'])
elif name == 'docker':
    assert args[0] == '--host'
    assert args[1] == state.get('docker_host', 'unix:///var/run/docker.sock')
    assert not any(k in os.environ for k in ['DOCKER_CONTEXT', 'DOCKER_HOST',
                                            'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'])
    args = args[2:]
    if state.get('docker_missing', False):
        print('No such container on selected Docker endpoint', file=sys.stderr)
        code = 1
    elif args[0] == 'port':
        print('127.0.0.1:55436')
    elif args[0] == 'inspect':
        print('running')
    elif args[0] == 'exec':
        state['pg_checks'] += 1
        code = int(state['pg_checks'] <= 2)
elif name == 'systemctl':
    if args[0] == 'is-active':
        code = int(args[-1] not in state['active'])
    else:
        print('ActiveState=active\nSubState=running\nResult=success\nExecMainStatus=0')
elif name == 'systemd-run':
    unit = next(a.split('=', 1)[1] + '.service' for a in args if a.startswith('--unit='))
    state['active'].append(unit)
elif name == 'curl':
    if args[-1].startswith('https:'):
        print('000', end='')
        code = 7
    else:
        print('200', end='')
elif name == 'python':
    if args[0] == '-c':
        print(json.dumps(args[2:]))
    elif args[0] == '--version':
        print('Python 3.12')
    elif args[0].endswith('run_migrations.py'):
        print('DB_PASSWORD_SENTINEL API_KEY_SENTINEL', file=sys.stderr)
        state['migration_head'] = not state.get('migration_fail', False)
        code = int(state.get('migration_fail', False))
    elif args[1] == 'url':
        print('postgresql+asyncpg://user:DB_PASSWORD_SENTINEL@127.0.0.1:55436/interview')
    elif args[1] == 'database':
        print('database=ok migration=' + ('head' if state['migration_head'] else 'behind')
              + ' detail=current=0012_version_identity_nonunique, '
              + 'heads=[\'0013_result_aware_agent_runs\']')
    elif args[1] == 'ready':
        code = int(state.get('readiness_fail', False))
        print('http=503 status=not_ready database=ok migration=behind '
              'detail=current=0012, heads=[\'0013\']'
              if code else 'http=200 status=ready database=ok migration=head detail=none')
state_path.write_text(json.dumps(state))
sys.exit(code)
"""


def fake_host(tmp_path: Path, **changes: object) -> tuple[Path, dict[str, str], Path]:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / ".venv/bin").mkdir(parents=True)
    home = tmp_path / "home"
    config = home / ".config/labpilot"
    config.mkdir(parents=True)
    for name, text in [
        ("operator-interview-db-password", "DB_PASSWORD_SENTINEL"),
        ("interview-provider.env", "LABPILOT_AGENT_PROVIDER_API_KEY=API_KEY_SENTINEL\n"),
    ]:
        path = config / name
        path.write_text(text)
        path.chmod(0o600)
    control_socket = tmp_path / "control.sock"
    with socket.socket(socket.AF_UNIX) as connection:
        connection.bind(str(control_socket))
    script = repo / "scripts/start_interview_operator_demo.sh"
    script.write_text(
        (ROOT / "scripts/start_interview_operator_demo.sh")
        .read_text()
        .replace("/run/labpilot-runner/control.sock", str(control_socket))
    )
    stubs = tmp_path / "bin"
    stubs.mkdir()
    stub = stubs / "stub"
    stub.write_text(STUB)
    stub.chmod(0o755)
    for name in [
        "id",
        "stat",
        "getent",
        "docker",
        "systemctl",
        "systemd-run",
        "curl",
        "sleep",
        "install",
        "ss",
        "runuser",
    ]:
        (stubs / name).symlink_to(stub)
    (repo / ".venv/bin/python").symlink_to(stub)
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "calls": [],
                "home": str(home),
                "pg_checks": 0,
                "migration_head": False,
                "active": ["labpilot-runner.service"],
                **changes,
            }
        )
    )
    env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}", "FAKE_STATE": str(state_path)}
    for variable in (
        "LABPILOT_INTERVIEW_USER", "LABPILOT_INTERVIEW_CONFIG_DIR",
        "LABPILOT_REPOSITORY_ALLOWED_ROOTS",
    ):
        env.pop(variable, None)
    return script, env, state_path


def run(script: Path, env: dict[str, str], mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), mode], env=env, capture_output=True, text=True, check=False
    )


def test_start_waits_migrates_and_repeated_start_keeps_active_services(tmp_path: Path) -> None:
    script, env, state_path = fake_host(tmp_path)
    first = run(script, env, "start")
    assert first.returncode == 0, first.stdout + first.stderr
    state = json.loads(state_path.read_text())
    assert state["pg_checks"] == 3
    assert state["migration_head"] is True
    second = run(script, env, "start")
    assert second.returncode == 0, second.stdout + second.stderr
    calls = json.loads(state_path.read_text())["calls"]
    launches = [call for call in calls if call[0] == "systemd-run"]
    assert len(launches) == 2
    assert all("--setenv=LABPILOT_INTERVIEW_USER=preview-user" in call for call in launches)
    config = tmp_path / "home/.config/labpilot"
    assert all(f"--setenv=LABPILOT_INTERVIEW_CONFIG_DIR={config}" in call for call in launches)
    migration_index = next(
        i for i, call in enumerate(calls) if any("run_migrations.py" in a for a in call)
    )
    assert migration_index < calls.index(launches[0])
    assert "migration=behind" in first.stdout and "migration=head" in first.stdout
    for secret in ["DB_PASSWORD_SENTINEL", "API_KEY_SENTINEL"]:
        assert secret not in first.stdout + first.stderr + second.stdout + second.stderr


def test_explicit_interview_settings_reach_service_launches(tmp_path: Path) -> None:
    script, env, state_path = fake_host(tmp_path)
    config = tmp_path / "alternate config"
    (tmp_path / "home/.config/labpilot").rename(config)
    roots = [str(tmp_path / 'research "checkout"')]
    env.update(
        LABPILOT_INTERVIEW_USER="configured-user",
        LABPILOT_INTERVIEW_CONFIG_DIR=str(config),
        LABPILOT_REPOSITORY_ALLOWED_ROOTS=json.dumps(roots),
    )
    result = run(script, env, "start")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = json.loads(state_path.read_text())["calls"]
    launches = [call for call in calls if call[0] == "systemd-run"]
    assert len(launches) == 2
    for call in launches:
        assert "--setenv=LABPILOT_INTERVIEW_USER=configured-user" in call
        assert f"--setenv=LABPILOT_INTERVIEW_CONFIG_DIR={config}" in call
        assert f"--setenv=LABPILOT_REPOSITORY_ALLOWED_ROOTS={json.dumps(roots)}" in call
    api = run(script, env, "api")
    assert api.returncode == 0, api.stdout + api.stderr
    state = json.loads(state_path.read_text())
    assert state["api_roots"] == roots
    assert state["calls"][-1][:5] == ["runuser", "-u", "configured-user", "-g", "labpilot-train"]

    del env["LABPILOT_REPOSITORY_ALLOWED_ROOTS"]
    api = run(script, env, "api")
    assert api.returncode == 0, api.stdout + api.stderr
    assert json.loads(state_path.read_text())["api_roots"] == [
        str(tmp_path / "home/labpilot-demo-repo")
    ]

@pytest.mark.parametrize("failure", ["readiness_fail", "migration_fail"])
def test_start_failures_show_actionable_diagnostics_without_secrets(
    tmp_path: Path, failure: str
) -> None:
    script, env, _ = fake_host(tmp_path, **{failure: True})
    result = run(script, env, "start")
    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert "database=ok" in output and "migration=behind" in output and "detail=" in output
    assert "doctor" in output
    assert "curl: (22)" not in output
    assert "DB_PASSWORD_SENTINEL" not in output and "API_KEY_SENTINEL" not in output


def test_doctor_is_read_only_and_marks_provider_network_warning(tmp_path: Path) -> None:
    script, env, state_path = fake_host(tmp_path)
    result = run(script, env, "doctor")
    calls = json.loads(state_path.read_text())["calls"]
    assert not any(
        call[0] == "systemd-run" or (call[0] == "docker" and "start" in call) for call in calls
    )
    assert not any(any("run_migrations.py" in a for a in call) for call in calls)
    assert not any("agent-runs" in a or "batches" in a for call in calls for a in call)
    assert "provider endpoint unreachable" in result.stdout
    assert "/operator http=200" in result.stdout and "/ops http=200" in result.stdout
    assert "DB_PASSWORD_SENTINEL" not in result.stdout + result.stderr
    assert "API_KEY_SENTINEL" not in result.stdout + result.stderr


def test_ready_probe_keeps_503_body_and_redacts_unexpected_detail(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    body = json.dumps(
        {
            "status": "not_ready",
            "database": "ok",
            "migration": "behind",
            "detail": "DB_PASSWORD_SENTINEL",
        }
    ).encode()

    def unavailable(*args: object, **kwargs: object) -> None:
        raise HTTPError("http://localhost", 503, "unavailable", None, io.BytesIO(body))

    def local_opener(handler: object) -> SimpleNamespace:
        assert handler.proxies == {}
        return SimpleNamespace(open=unavailable)

    monkeypatch.setattr(diagnostics, "build_opener", local_opener)
    assert diagnostics.ready() == 1
    output = capsys.readouterr().out
    assert "http=503" in output and "migration=behind" in output
    assert "DB_PASSWORD_SENTINEL" not in output
    # Unknown free-text details are not trusted diagnostic output.
    assert diagnostics.safe_detail("password=DB_PASSWORD_SENTINEL") == "redacted_unexpected_detail"
    assert diagnostics.safe_detail("current=0012, heads=['0013']") == "current=0012, heads=['0013']"


@pytest.mark.parametrize("mode", ["start", "doctor"])
def test_interview_docker_endpoint_is_explicit_and_caller_context_is_unchanged(
    tmp_path: Path, mode: str
) -> None:
    host = "unix:///run/labpilot-docker.sock"
    script, env, state_path = fake_host(
        tmp_path, docker_host=host, migration_head=True,
        active=["labpilot-runner.service", "labpilot-operator-interview-api.service",
                "labpilot-operator-interview-coordinator.service"],
    )
    (tmp_path / "home/.config/labpilot/interview-docker-host").write_text(host + "\n")
    caller = {"DOCKER_HOST": "tcp://wrong:2375", "DOCKER_CONTEXT": "desktop-linux",
              "DOCKER_TLS_VERIFY": "1", "DOCKER_CERT_PATH": "/wrong"}
    env.update(caller)
    result = run(script, env, mode)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = json.loads(state_path.read_text())["calls"]
    docker_calls = [call for call in calls if call[0] == "docker"]
    assert docker_calls and all(call[1:3] == ["--host", host] for call in docker_calls)
    assert not any("context" in call for call in docker_calls)
    assert all(env[k] == v for k, v in caller.items())
    assert f"Docker endpoint={host}" in result.stdout


@pytest.mark.parametrize("mode", ["start", "doctor"])
def test_missing_container_never_falls_back_to_another_engine(tmp_path: Path, mode: str) -> None:
    script, env, state_path = fake_host(tmp_path, docker_missing=True)
    result = run(script, env, mode)
    assert result.returncode == 1
    calls = json.loads(state_path.read_text())["calls"]
    assert not any(call[0] == "systemd-run" for call in calls)
    assert not any("run_migrations.py" in arg for call in calls for arg in call)
    assert not any(call[0] == "docker" and any(arg in call for arg in ["run", "create", "context"])
                   for call in calls)
    assert "Docker endpoint=unix:///var/run/docker.sock" in result.stdout
    if mode == "doctor":
        assert "DB port actual=unavailable" in result.stdout


def test_invalid_endpoint_does_not_print_its_value_or_call_docker(tmp_path: Path) -> None:
    script, env, state_path = fake_host(tmp_path)
    (tmp_path / "home/.config/labpilot/interview-docker-host").write_text("tcp://API_KEY_SENTINEL:2375\n")
    result = run(script, env, "start")
    assert result.returncode == 1
    assert "API_KEY_SENTINEL" not in result.stdout + result.stderr
    assert not any(call[0] == "docker" for call in json.loads(state_path.read_text())["calls"])
