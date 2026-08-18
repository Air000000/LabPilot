import configparser
import os
import subprocess
import venv
from pathlib import Path

ROOT = Path(__file__).parents[3]
SERVICE = ROOT / "deploy/systemd/labpilot-runner.service"
INSTALLER = ROOT / "scripts/install_labpilot_runner_host.sh"


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


def test_info_readiness_runs_as_fixed_controller_uid_and_gid(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    python = app_root / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    log = tmp_path / "runuser.argv"
    log.write_text("")
    command = f"""
source '{INSTALLER}'
app_root='{app_root}'
runtime_dir='{tmp_path}/run'
socket_path='{tmp_path}/run/control.sock'
systemctl() {{ return 0; }}
stat() {{
  case "$3" in
    "$runtime_dir") echo 'root:labpilot-control 750' ;;
    "$socket_path") echo 'root:labpilot-control 660' ;;
  esac
}}
runuser() {{ printf '%s\n' "$@" > '{log}'; }}
verify_runtime_boundary
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
        str(python),
        "-E",
        "-s",
        "-B",
        "-c",
    ]


def test_installer_uses_non_editable_sync_and_checks_final_import_before_activation(
    tmp_path: Path,
) -> None:
    events = tmp_path / "events.log"
    policy = tmp_path / "policy.json"
    policy.write_text("{}")
    sha = "a" * 40
    command = f"""
source '{INSTALLER}'
test_root='{tmp_path}'
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
chown() {{ :; }}
chmod() {{ :; }}
mktemp() {{ command mktemp -d "$test_root/.stage.XXXXXX"; }}
uv() {{
  printf 'uv:%s\n' "$*" >> '{events}'
  mkdir -p .venv/bin
  printf '#!/bin/sh\nprintf "python:%%s\\n" "$*" >> "%s"\n' '{events}' > .venv/bin/python
  command chmod 755 .venv/bin/python
}}
mv() {{ printf 'move\n' >> '{events}'; command mv "$@"; }}
activate_and_verify() {{ printf 'activate\n' >> '{events}'; }}
main '{sha}' '{policy}'
"""

    result = subprocess.run(
        ["bash", "-c", command], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    lines = events.read_text().splitlines()
    assert lines[0] == "uv:sync --locked --no-dev --no-editable"
    assert lines[1] == "move"
    assert lines[2] == f"python:-E -s -B - {tmp_path}/app"
    assert lines[3] == "activate"


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
        "valid": ("0 0 755 d", "0 0 777 l", 0),
        "wrong-user": ("1000 0 755 d", "0 0 777 l", 1),
        "wrong-group": ("0 983 755 d", "0 0 777 l", 1),
        "group-write": ("0 0 775 d", "0 0 777 l", 1),
        "symlink-wrong-group": ("0 0 755 d", "0 983 777 l", 1),
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


def test_activation_restarts_before_effective_and_readiness_verification(
    tmp_path: Path,
) -> None:
    log = tmp_path / "activation.log"
    command = f"""
source '{INSTALLER}'
systemctl() {{ printf 'systemctl:%s\n' "$*" >> '{log}'; }}
verify_effective_service() {{ echo effective >> '{log}'; }}
verify_runtime_boundary() {{ echo runtime >> '{log}'; }}
verify_install_tree() {{ echo tree >> '{log}'; }}
activate_and_verify
"""

    result = subprocess.run(
        ["bash", "-c", command], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        "systemctl:daemon-reload",
        "systemctl:enable labpilot-runner.service",
        "systemctl:restart labpilot-runner.service",
        "effective",
        "runtime",
        "tree",
    ]
