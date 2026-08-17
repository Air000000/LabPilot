import configparser
import os
import subprocess
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
        "execstart": (
            "/opt/labpilot-runner/app/.venv/bin/python -E -s -m "
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
    fake_systemctl.write_text(
        """#!/bin/sh
case "$4" in
  FragmentPath) echo /etc/systemd/system/labpilot-runner.service ;;
  User) echo root ;;
  Group) echo labpilot-control ;;
  UMask) echo 0077 ;;
  NoNewPrivileges) echo yes ;;
  RestrictAddressFamilies) echo AF_UNIX ;;
  ExecStart) printf '%s%s\n' \
    '/opt/labpilot-runner/app/.venv/bin/python -E -s -m ' \
    'labpilot.execution.runner_daemon --policy /etc/labpilot/runner-policy.json' ;;
esac
"""
    )
    fake_systemctl.chmod(0o755)
    command = f"PATH='{tmp_path}':$PATH; source '{INSTALLER}'; verify_effective_service"
    result = subprocess.run(["bash", "-c", command], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_installer_script_has_valid_bash_syntax() -> None:
    subprocess.run(["bash", "-n", str(INSTALLER)], check=True)
