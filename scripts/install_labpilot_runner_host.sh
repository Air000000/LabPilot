#!/usr/bin/env bash
set -euo pipefail

service_name=labpilot-runner.service
app_root=/opt/labpilot-runner/app
policy_target=/etc/labpilot/runner-policy.json
service_target=/etc/systemd/system/labpilot-runner.service
runtime_dir=/run/labpilot-runner
socket_path=/run/labpilot-runner/control.sock

fail() {
  echo "$*" >&2
  return 1
}

systemd_value() {
  systemctl show "$service_name" --property "$1" --value
}

verify_effective_service() {
  local exec_start
  [[ "$(systemd_value FragmentPath)" == "$service_target" ]] || fail "loaded FragmentPath mismatch"
  [[ "$(systemd_value User)" == root ]] || fail "loaded User mismatch"
  [[ "$(systemd_value Group)" == labpilot-control ]] || fail "loaded Group mismatch"
  [[ "$(systemd_value UMask)" == 0077 ]] || fail "loaded UMask mismatch"
  [[ "$(systemd_value RuntimeDirectoryPreserve)" == restart ]] || fail "loaded RuntimeDirectoryPreserve mismatch"
  [[ "$(systemd_value NoNewPrivileges)" == yes ]] || fail "loaded NoNewPrivileges mismatch"
  [[ "$(systemd_value RestrictAddressFamilies)" == AF_UNIX ]] || fail "loaded address family mismatch"
  exec_start=$(systemd_value ExecStart)
  [[ "$exec_start" == *"/opt/labpilot-runner/app/.venv/bin/python -E -s -B -m labpilot.execution.runner_daemon --policy /etc/labpilot/runner-policy.json"* ]] || fail "loaded ExecStart mismatch"
}

verify_runtime_boundary() {
  systemctl is-active --quiet "$service_name"
  [[ "$(stat -c '%U:%G %a' "$runtime_dir")" == "root:labpilot-control 750" ]] || fail "runtime directory metadata mismatch"
  [[ "$(stat -c '%U:%G %a' "$socket_path")" == "root:labpilot-control 660" ]] || fail "socket metadata mismatch"
  runuser --user labpilot-controller --group labpilot-control -- \
    "$app_root/.venv/bin/python" -E -s -B -c '
from labpilot.execution.runner_client import RunnerClient
from labpilot.execution.runner_protocol import PROTOCOL_VERSION, RunnerInfoRequest, RunnerOperation
response = RunnerClient("/run/labpilot-runner/control.sock").request(
    RunnerInfoRequest(PROTOCOL_VERSION, RunnerOperation.INFO, None)
)
assert response["outcome"] == "ok"
'
}

verify_final_import() {
  "$app_root/.venv/bin/python" -E -s -B - "$app_root" <<'PY'
import sys
import sysconfig
from pathlib import Path

import labpilot

app_root = Path(sys.argv[1]).resolve(strict=True)
venv_root = (app_root / ".venv").resolve(strict=True)
site_packages = Path(sysconfig.get_path("purelib")).resolve(strict=True)
module_file = getattr(labpilot, "__file__", None)
if not module_file:
    raise SystemExit("labpilot must resolve from final installed venv")
resolved_module = Path(module_file).resolve(strict=True)
if not site_packages.is_relative_to(venv_root) or not resolved_module.is_relative_to(
    site_packages
):
    raise SystemExit("labpilot must resolve from final installed venv")
if any(part.startswith(".stage.") for part in resolved_module.parts):
    raise SystemExit("labpilot must not resolve from staging")
PY
}

verify_controller_final_import() {
  runuser --user labpilot-controller --group labpilot-control -- \
    "$app_root/.venv/bin/python" -E -s -B - "$app_root" <<'PY'
import sys
import sysconfig
from pathlib import Path

import labpilot
from labpilot.execution.runner_client import RunnerClient
from labpilot.execution.runner_protocol import (
    PROTOCOL_VERSION,
    RunnerInfoRequest,
    RunnerOperation,
)

app_root = Path(sys.argv[1]).resolve(strict=True)
venv_root = (app_root / ".venv").resolve(strict=True)
site_packages = Path(sysconfig.get_path("purelib")).resolve(strict=True)
module_file = getattr(labpilot, "__file__", None)
if not module_file:
    raise SystemExit("labpilot must resolve from final installed venv")
resolved_module = Path(module_file).resolve(strict=True)
if not site_packages.is_relative_to(venv_root) or not resolved_module.is_relative_to(
    site_packages
):
    raise SystemExit("labpilot must resolve from final installed venv")
if any(part.startswith(".stage.") for part in resolved_module.parts):
    raise SystemExit("labpilot must not resolve from staging")
assert RunnerClient and PROTOCOL_VERSION and RunnerInfoRequest and RunnerOperation
PY
}

verify_install_tree() {
  find -P "$app_root" -printf '%U %G %m %y %p\0' |
    while IFS=' ' read -r -d '' uid gid mode kind path; do
      if [[ "$uid" != 0 || "$gid" != 0 ]]; then
        fail "installed application tree ownership mismatch: $path"
        exit 1
      fi
      if [[ "$path" == "$app_root" && ( "$kind" != d || "$mode" != 711 ) ]]; then
        fail "installed application root mode mismatch: $path"
        exit 1
      fi
      if [[ "$kind" != l ]] && (( (8#$mode & 8#022) != 0 )); then
        fail "installed application tree writable node: $path"
        exit 1
      fi
    done
}

activate_and_verify() {
  systemctl daemon-reload
  systemctl enable "$service_name"
  systemctl restart "$service_name"
  verify_effective_service
  verify_runtime_boundary
  verify_install_tree
}

main() {
  [[ "$(id -u)" == 0 ]] || fail "must run as root"
  [[ $# == 2 ]] || fail "usage: $0 <exact-git-sha> <policy-source>"
  local git_sha=$1
  local policy_source=$2
  [[ "$git_sha" =~ ^[0-9a-f]{40}$ ]] || fail "exact 40-character Git SHA required"
  [[ -f "$policy_source" && ! -L "$policy_source" ]] || fail "policy source must be a regular non-symlink file"

  local repo_root staging
  repo_root=$(git rev-parse --show-toplevel)
  [[ "$(git -C "$repo_root" rev-parse "$git_sha^{commit}")" == "$git_sha" ]] || fail "Git SHA is not an exact commit"
  install -d -o root -g root -m 0755 /opt/labpilot-runner
  staging=$(mktemp -d /opt/labpilot-runner/.stage.XXXXXX)
  trap 'rm -rf -- "$staging"' EXIT
  git -C "$repo_root" archive "$git_sha" | tar -x -C "$staging"
  chown -R root:root "$staging"
  chmod -R go-w "$staging"
  (cd "$staging" && uv sync --locked --no-dev --no-editable)

  rm -rf -- "$app_root"
  mv "$staging" "$app_root"
  trap - EXIT
  chown -R root:root "$app_root"
  chmod -R go-w "$app_root"
  chmod 0711 "$app_root"
  verify_final_import
  verify_controller_final_import

  install -d -o root -g root -m 0755 /etc/labpilot
  install -o root -g root -m 0600 "$policy_source" "$policy_target"
  install -o root -g root -m 0644 \
    "$app_root/deploy/systemd/labpilot-runner.service" "$service_target"

  activate_and_verify
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
