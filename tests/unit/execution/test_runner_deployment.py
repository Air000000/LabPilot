import configparser
import os
import subprocess
import venv
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
SERVICE = ROOT / "deploy/systemd/labpilot-runner.service"
INSTALLER = ROOT / "scripts/install_labpilot_runner_host.sh"


def _run_fake_installer(
    tmp_path: Path, *, controller_import_status: int = 0, receipt_status: int = 0
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    events = tmp_path / "events.log"
    policy = tmp_path / "policy.json"
    policy.write_text("{}")
    sha = "a" * 40
    command = f"""
source '{INSTALLER}'
test_root='{tmp_path}'
runner_root="$test_root"
app_root="$test_root/app"
policy_target="$test_root/runner-policy.json"
service_target="$test_root/labpilot-runner.service"
id() {{ echo 0; }}
git() {{
  if [[ "$*" == 'rev-parse --show-toplevel' ]]; then
    echo "$test_root/repository"
  elif [[ "$*" == *'rev-parse'* ]]; then
    echo '{sha}'
  fi
}}
install() {{ :; }}
tar() {{ :; }}
chown() {{ printf 'chown:%s\n' "$*" >> '{events}'; }}
chmod() {{ printf 'chmod:%s\n' "$*" >> '{events}'; }}
mktemp() {{ command mktemp -d "$test_root/.stage.XXXXXX"; }}
uv() {{
  printf 'uv:%s\n' "$*" >> '{events}'
  mkdir -p .venv/bin
  printf '#!/bin/sh\nprintf "root-import:%%s\\n" "$*" >> "%s"\n' '{events}' > .venv/bin/python
  command chmod 755 .venv/bin/python
}}
mv() {{ printf 'move\n' >> '{events}'; command mv "$@"; }}
runuser() {{
  printf 'controller-import:%s\n' "$*" >> '{events}'
  return {controller_import_status}
}}
activate_and_verify() {{ printf 'activate\n' >> '{events}'; }}
install_or_verify_terminal_receipt() {{
  printf 'receipt\n' >> '{events}'
  return {receipt_status}
}}
verify_trusted_directory() {{ :; }}
main '{sha}' '{policy}'
"""
    result = subprocess.run(
        ["bash", "-c", command], check=False, capture_output=True, text=True
    )
    return result, events.read_text().splitlines()


def _run_readiness_activation(
    tmp_path: Path,
    *,
    probe_statuses: tuple[int, ...],
    service_active: bool = True,
    runtime_status: int = 0,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    events = tmp_path / "readiness.log"
    statuses = " ".join(str(status) for status in probe_statuses)
    command = f"""
source '{INSTALLER}'
probe_statuses=({statuses})
probe_count=0
run_controller_info_probe() {{
  printf 'probe\n' >> '{events}'
  local index=$probe_count
  if (( index >= ${{#probe_statuses[@]}} )); then
    index=$((${{#probe_statuses[@]}} - 1))
  fi
  probe_count=$((probe_count + 1))
  return "${{probe_statuses[$index]}}"
}}
systemctl() {{
  printf 'systemctl:%s\n' "$*" >> '{events}'
  if [[ "$1 ${{2-}}" == 'is-active --quiet' ]]; then
    {'return 0' if service_active else 'return 1'}
  fi
}}
sleep() {{ printf 'sleep:%s\n' "$*" >> '{events}'; }}
verify_effective_service() {{ printf 'effective\n' >> '{events}'; }}
verify_runtime_boundary() {{
  printf 'runtime\n' >> '{events}'
  return {runtime_status}
}}
verify_install_tree() {{ printf 'tree\n' >> '{events}'; }}
activate_and_verify
"""
    result = subprocess.run(
        ["bash", "-c", command], check=False, capture_output=True, text=True
    )
    return result, events.read_text().splitlines()


def test_service_freezes_candidate_b_hardening() -> None:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.read(SERVICE)

    assert dict(parser["Unit"]) == {
        "description": "LabPilot Candidate B Runner",
        "after": "local-fs.target",
    }
    assert dict(parser["Service"]) == {
        "type": "simple",
        "user": "root",
        "group": "labpilot-control",
        "umask": "0077",
        "runtimedirectory": "labpilot-runner",
        "runtimedirectorymode": "0750",
        "runtimedirectorypreserve": "restart",
        "execstart": (
            "/opt/labpilot-runner/app/.venv/bin/python -E -s -B -m "
            "labpilot.execution.runner_daemon --policy /etc/labpilot/runner-policy.json"
        ),
        "restart": "on-failure",
        "restartsec": "1s",
        "nonewprivileges": "yes",
        "ambientcapabilities": "CAP_SETUID",
        "restrictaddressfamilies": "AF_UNIX",
    }
    assert dict(parser["Install"]) == {"wantedby": "multi-user.target"}


def test_installer_refuses_non_root_before_any_mutation() -> None:
    result = subprocess.run(
        ["bash", str(INSTALLER), "a" * 40, "/missing/policy.json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "must run as root" in result.stderr


def test_installer_rejects_non_exact_sha_before_archive(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_id = fake_bin / "id"
    fake_id.write_text("#!/bin/sh\necho 0\n")
    fake_id.chmod(0o755)
    policy = tmp_path / "policy.json"
    policy.write_text("{}")
    result = subprocess.run(
        ["bash", str(INSTALLER), "main", str(policy)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )
    assert result.returncode != 0
    assert "exact 40-character Git SHA" in result.stderr


def test_effective_service_verifier_checks_loaded_properties(tmp_path: Path) -> None:
    fake_systemctl = tmp_path / "systemctl"
    log = tmp_path / "properties.log"
    fake_systemctl.write_text(
        f"""#!/bin/sh
printf '%s\n' "$4" >> '{log}'
case "$4" in
  FragmentPath) echo /etc/systemd/system/labpilot-runner.service ;;
  User) echo root ;;
  Group) echo labpilot-control ;;
  UMask) echo 0077 ;;
  RuntimeDirectoryPreserve) echo restart ;;
  NoNewPrivileges) echo yes ;;
  AmbientCapabilities) echo cap_setuid ;;
  RestrictAddressFamilies) echo AF_UNIX ;;
  ExecStart) printf '%s%s\n' \
    '/opt/labpilot-runner/app/.venv/bin/python -E -s -B -m ' \
    'labpilot.execution.runner_daemon --policy /etc/labpilot/runner-policy.json' ;;
esac
"""
    )
    fake_systemctl.chmod(0o755)
    command = f"PATH='{tmp_path}':$PATH; source '{INSTALLER}'; verify_effective_service"
    result = subprocess.run(["bash", "-c", command], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "RuntimeDirectoryPreserve" in log.read_text().splitlines()


def test_installer_script_has_valid_bash_syntax() -> None:
    subprocess.run(["bash", "-n", str(INSTALLER)], check=True)


def test_info_probe_runs_as_fixed_controller_uid_and_gid_with_short_timeout(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "app"
    venv.create(app_root / ".venv", with_pip=False)
    site_packages = next((app_root / ".venv/lib").glob("python*/site-packages"))
    execution = site_packages / "labpilot/execution"
    execution.mkdir(parents=True)
    (execution.parent / "__init__.py").write_text("")
    (execution / "__init__.py").write_text("")
    (execution / "runner_client.py").write_text(
        "import os\n"
        "class RunnerPreSendError(ConnectionError): pass\n"
        "class RunnerClient:\n"
        "    def __init__(self, socket_path, *, timeout_seconds):\n"
        "        assert socket_path == '/run/labpilot-runner/control.sock'\n"
        "        assert timeout_seconds == 1.0\n"
        "    def request(self, request):\n"
        "        outcome = os.environ['LABPILOT_TEST_PROBE_OUTCOME']\n"
        "        if outcome == 'pre-send': raise RunnerPreSendError()\n"
        "        if outcome == 'hard': raise RuntimeError('hard failure')\n"
        "        return {'outcome': 'ok'}\n"
    )
    (execution / "runner_protocol.py").write_text(
        "PROTOCOL_VERSION = 1\n"
        "class RunnerInfoRequest:\n"
        "    def __init__(self, *args): pass\n"
        "class RunnerOperation: INFO = 'INFO'\n"
    )
    python = app_root / ".venv/bin/python"
    log = tmp_path / "runuser.argv"
    command = f"""
source '{INSTALLER}'
app_root='{app_root}'
runuser() {{
  printf '%s\n' "$@" > '{log}'
  shift 5
  "$@"
}}
run_controller_info_probe
"""
    results = {
        outcome: subprocess.run(
            ["bash", "-c", command],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "LABPILOT_TEST_PROBE_OUTCOME": outcome},
        )
        for outcome in ("success", "pre-send", "hard")
    }

    assert results["success"].returncode == 0, results["success"].stderr
    assert results["pre-send"].returncode == 75
    assert results["hard"].returncode not in (0, 75)
    assert log.read_text().splitlines()[:10] == [
        "--user",
        "labpilot-controller",
        "--group",
        "labpilot-control",
        "--",
        str(python),
        "-E",
        "-s",
        "-B",
        "-c",
    ]


def test_installer_sets_exact_app_root_mode_before_controller_import_and_activation(
    tmp_path: Path,
) -> None:
    result, lines = _run_fake_installer(tmp_path)

    assert result.returncode == 0, result.stderr
    lines = [line for line in lines if ".stage." not in line]
    assert lines[0] == "uv:sync --locked --no-dev --no-editable"
    final_events = lines[1:]
    assert final_events == [
        "receipt",
        "move",
        f"chown:-R root:root {tmp_path}/app",
        f"chmod:-R go-w {tmp_path}/app",
        f"chmod:0711 {tmp_path}/app",
        f"root-import:-E -s -B - {tmp_path}/app",
        (
            "controller-import:--user labpilot-controller --group "
            f"labpilot-control -- {tmp_path}/app/.venv/bin/python -E -s -B - "
            f"{tmp_path}/app"
        ),
        "activate",
    ]


def test_installer_stops_before_activation_when_controller_final_import_fails(
    tmp_path: Path,
) -> None:
    result, lines = _run_fake_installer(tmp_path, controller_import_status=19)

    assert result.returncode == 19
    assert not any(line == "activate" for line in lines)


def test_installer_stops_before_app_replacement_when_receipt_validation_fails(
    tmp_path: Path,
) -> None:
    result, lines = _run_fake_installer(tmp_path, receipt_status=29)

    assert result.returncode == 29
    assert "receipt" in lines
    assert "move" not in lines
    assert "activate" not in lines


def _run_receipt_install(
    tmp_path: Path,
    *,
    installed: bytes | None,
    directory_metadata: str = "0 0 755",
) -> tuple[subprocess.CompletedProcess[str], Path, list[str]]:
    runner_root = tmp_path / "opt/labpilot-runner"
    libexec_dir = runner_root / "libexec"
    target = libexec_dir / "labpilot-terminal-receipt-v1.py"
    source = tmp_path / "source.py"
    source.write_bytes(b"expected")
    if installed is not None:
        libexec_dir.mkdir(parents=True)
        target.write_bytes(installed)
    events = tmp_path / "receipt-events.log"
    command = f"""
source '{INSTALLER}'
runner_root='{runner_root}'
libexec_dir='{libexec_dir}'
reporter_target='{target}'
reporter_source='{source}'
stat() {{
  local path="${{@: -1}}"
  if [[ -d "$path" ]]; then
    printf '{directory_metadata}\n'
  else
    printf '0 0 555\n'
  fi
}}
install() {{
  printf 'install:%s\n' "$*" >> '{events}'
  if [[ "$1" == '-d' ]]; then
    command mkdir -p "${{@: -1}}"
  else
    command cp "${{@: -2:1}}" "${{@: -1}}"
    command chmod 0555 "${{@: -1}}"
  fi
}}
install_or_verify_terminal_receipt
"""
    result = subprocess.run(
        ["bash", "-c", command], check=False, capture_output=True, text=True
    )
    return result, target, events.read_text().splitlines() if events.exists() else []


def test_terminal_receipt_installs_once_when_absent(tmp_path: Path) -> None:
    result, target, events = _run_receipt_install(tmp_path, installed=None)

    assert result.returncode == 0, result.stderr
    assert target.read_bytes() == b"expected"
    assert any(not event.startswith("install:-d") for event in events)


def test_terminal_receipt_exact_match_is_reused_without_replacement(
    tmp_path: Path,
) -> None:
    result, target, events = _run_receipt_install(tmp_path, installed=b"expected")
    inode = target.stat().st_ino

    assert result.returncode == 0, result.stderr
    assert target.stat().st_ino == inode
    assert events == []


def test_terminal_receipt_drift_fails_without_overwrite(tmp_path: Path) -> None:
    result, target, events = _run_receipt_install(tmp_path, installed=b"drift")
    inode = target.stat().st_ino

    assert result.returncode != 0
    assert "digest" in result.stderr
    assert target.read_bytes() == b"drift"
    assert target.stat().st_ino == inode
    assert events == []


@pytest.mark.parametrize("metadata", ["1000 0 755", "0 0 777"])
def test_terminal_receipt_rejects_unsafe_trusted_directory_without_mutation(
    tmp_path: Path, metadata: str
) -> None:
    result, target, events = _run_receipt_install(
        tmp_path, installed=b"expected", directory_metadata=metadata
    )

    assert result.returncode != 0
    assert target.read_bytes() == b"expected"
    assert events == []


def test_controller_final_import_runs_as_controller_and_imports_runner_modules(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "app"
    venv.create(app_root / ".venv", with_pip=False)
    site_packages = next((app_root / ".venv/lib").glob("python*/site-packages"))
    execution = site_packages / "labpilot/execution"
    execution.mkdir(parents=True)
    (execution.parent / "__init__.py").write_text("")
    (execution / "__init__.py").write_text("")
    (execution / "runner_client.py").write_text("class RunnerClient: pass\n")
    (execution / "runner_protocol.py").write_text(
        "PROTOCOL_VERSION = 1\n"
        "class RunnerInfoRequest: pass\n"
        "class RunnerOperation: pass\n"
    )
    log = tmp_path / "runuser.argv"
    command = f"""
source '{INSTALLER}'
app_root='{app_root}'
runuser() {{
  printf '%s\n' "$@" > '{log}'
  shift 5
  "$@"
}}
verify_controller_final_import
"""

    result = subprocess.run(
        ["bash", "-c", command], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines()[:10] == [
        "--user",
        "labpilot-controller",
        "--group",
        "labpilot-control",
        "--",
        str(app_root / ".venv/bin/python"),
        "-E",
        "-s",
        "-B",
        "-",
    ]
    assert not list((site_packages / "labpilot").rglob("__pycache__"))


def test_final_import_smoke_uses_only_the_final_venv_site_packages(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    venv.create(app_root / ".venv", with_pip=False)
    site_packages = next((app_root / ".venv/lib").glob("python*/site-packages"))
    package = site_packages / "labpilot"
    package.mkdir()
    (package / "__init__.py").write_text("")
    command = f"source '{INSTALLER}'; app_root='{app_root}'; verify_final_import"

    result = subprocess.run(
        ["bash", "-c", command], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    assert not list(package.glob("__pycache__"))


def test_final_import_smoke_rejects_stale_staging_resolution(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    venv.create(app_root / ".venv", with_pip=False)
    site_packages = next((app_root / ".venv/lib").glob("python*/site-packages"))
    stale_source = tmp_path / ".stage.dead/src"
    package = stale_source / "labpilot"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (site_packages / "_editable_impl_labpilot.pth").write_text(f"{stale_source}\n")
    command = f"source '{INSTALLER}'; app_root='{app_root}'; verify_final_import"

    result = subprocess.run(
        ["bash", "-c", command], check=False, capture_output=True, text=True
    )

    assert result.returncode != 0
    assert "final installed venv" in result.stderr


def test_install_tree_verifier_checks_ownership_writes_and_symlink_mode(
    tmp_path: Path,
) -> None:
    scenarios = {
        "valid": ("0 0 711 d", "0 0 777 l", 0),
        "app-root-700": ("0 0 700 d", "0 0 777 l", 1),
        "app-root-755": ("0 0 755 d", "0 0 777 l", 1),
        "wrong-user": ("1000 0 711 d", "0 0 777 l", 1),
        "wrong-group": ("0 983 711 d", "0 0 777 l", 1),
        "group-write": ("0 0 711 d", "0 0 775 d", 1),
        "symlink-wrong-group": ("0 0 711 d", "0 983 777 l", 1),
    }
    for name, (root_stat, link_stat, expected_status) in scenarios.items():
        command = f"""
source '{INSTALLER}'
app_root='{tmp_path}/app'
find() {{
  [[ "$1" == '-P' ]] || return 9
  printf '%s %s\\0%s %s\\0' '{root_stat}' "$app_root" '{link_stat}' "$app_root/link"
}}
verify_install_tree
"""
        result = subprocess.run(
            ["bash", "-c", command], check=False, capture_output=True, text=True
        )
        assert result.returncode == expected_status, f"{name}: {result.stderr}"


def test_transient_readiness_retries_then_activation_reaches_tree(
    tmp_path: Path,
) -> None:
    result, lines = _run_readiness_activation(
        tmp_path, probe_statuses=(75, 75, 0)
    )

    assert result.returncode == 0, result.stderr
    assert lines.count("probe") == 3
    assert lines.count("sleep:0.2") == 2
    assert lines[-2:] == ["runtime", "tree"]


def test_readiness_exhaustion_stops_after_exact_bound(tmp_path: Path) -> None:
    result, lines = _run_readiness_activation(tmp_path, probe_statuses=(75,))

    assert result.returncode != 0
    assert lines.count("probe") == 50
    assert "runtime" not in lines
    assert "tree" not in lines
    assert "readiness exhausted" in result.stderr


def test_hard_readiness_failure_is_not_retried(tmp_path: Path) -> None:
    result, lines = _run_readiness_activation(tmp_path, probe_statuses=(19, 0))

    assert result.returncode == 19
    assert lines.count("probe") == 1
    assert not any(line.startswith("sleep:") for line in lines)
    assert "runtime" not in lines
    assert "tree" not in lines


def test_readiness_fails_when_service_becomes_inactive(tmp_path: Path) -> None:
    result, lines = _run_readiness_activation(
        tmp_path, probe_statuses=(75, 0), service_active=False
    )

    assert result.returncode != 0
    assert lines.count("probe") == 1
    assert lines.count("systemctl:is-active --quiet labpilot-runner.service") == 1
    assert not any(line.startswith("sleep:") for line in lines)
    assert "runtime" not in lines
    assert "tree" not in lines


def test_activation_orders_readiness_before_metadata_and_tree(
    tmp_path: Path,
) -> None:
    result, lines = _run_readiness_activation(tmp_path, probe_statuses=(0,))

    assert result.returncode == 0, result.stderr
    assert lines == [
        "systemctl:daemon-reload",
        "systemctl:enable labpilot-runner.service",
        "systemctl:restart labpilot-runner.service",
        "effective",
        "probe",
        "runtime",
        "tree",
    ]


def test_successful_readiness_does_not_skip_final_metadata_failure(
    tmp_path: Path,
) -> None:
    result, lines = _run_readiness_activation(
        tmp_path, probe_statuses=(0,), runtime_status=23
    )

    assert result.returncode == 23
    assert lines.count("probe") == 1
    assert "runtime" in lines
    assert "tree" not in lines
