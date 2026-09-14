#!/usr/bin/env bash
set -euo pipefail

service_name=labpilot-runner.service
runner_root=/opt/labpilot-runner
app_root=/opt/labpilot-runner/app
libexec_dir=/opt/labpilot-runner/libexec
reporter_target="$libexec_dir/labpilot-terminal-receipt-v1.py"
reporter_source="$app_root/deploy/libexec/labpilot-terminal-receipt-v1.py"
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
  [[ "$(systemd_value AmbientCapabilities)" == cap_setuid ]] || fail "loaded AmbientCapabilities mismatch"
  [[ "$(systemd_value RestrictAddressFamilies)" == AF_UNIX ]] || fail "loaded address family mismatch"
  exec_start=$(systemd_value ExecStart)
  [[ "$exec_start" == *"/opt/labpilot-runner/app/.venv/bin/python -E -s -B -m labpilot.execution.runner_daemon --policy /etc/labpilot/runner-policy.json"* ]] || fail "loaded ExecStart mismatch"
}

run_controller_info_probe() {
  runuser --user labpilot-controller --group labpilot-control -- \
    "$app_root/.venv/bin/python" -E -s -B -c '
from labpilot.execution.runner_client import RunnerClient, RunnerPreSendError
from labpilot.execution.runner_protocol import PROTOCOL_VERSION, RunnerInfoRequest, RunnerOperation

try:
    response = RunnerClient(
        "/run/labpilot-runner/control.sock", timeout_seconds=1.0
    ).request(RunnerInfoRequest(PROTOCOL_VERSION, RunnerOperation.INFO, None))
except RunnerPreSendError:
    raise SystemExit(75)
assert response["outcome"] == "ok"
'
}

wait_for_runner_readiness() {
  local attempt status
  for ((attempt = 1; attempt <= 50; attempt++)); do
    if run_controller_info_probe; then
      echo "Runner readiness established after $attempt attempt(s)"
      return 0
    else
      status=$?
    fi
    [[ "$status" == 75 ]] || return "$status"
    if (( attempt == 50 )); then
      fail "Runner readiness exhausted after 50 attempts"
      return 1
    fi
    if ! systemctl is-active --quiet "$service_name"; then
      fail "Runner service became inactive before readiness"
      return 1
    fi
    sleep 0.2
  done
}

verify_runtime_boundary() {
  systemctl is-active --quiet "$service_name"
  [[ "$(stat -c '%U:%G %a' "$runtime_dir")" == "root:labpilot-control 750" ]] || fail "runtime directory metadata mismatch"
  [[ "$(stat -c '%U:%G %a' "$socket_path")" == "root:labpilot-control 660" ]] || fail "socket metadata mismatch"
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

verify_trusted_directory() {
  local path=$1 uid gid mode
  [[ -d "$path" && ! -L "$path" ]] || fail "terminal receipt directory invalid: $path"
  read -r uid gid mode < <(stat -c '%u %g %a' -- "$path")
  [[ "$uid" == 0 && "$gid" == 0 ]] || fail "terminal receipt directory not root owned: $path"
  (( (8#$mode & 8#022) == 0 )) || fail "terminal receipt directory permissions unsafe: $path"
}

verify_terminal_receipt() {
  local uid gid mode
  [[ -f "$reporter_target" && ! -L "$reporter_target" ]] || fail "terminal receipt reporter must be a regular non-symlink file"
  read -r uid gid mode < <(stat -c '%u %g %a' -- "$reporter_target")
  [[ "$uid" == 0 && "$gid" == 0 ]] || fail "terminal receipt reporter not root owned"
  (( (8#$mode & 8#022) == 0 )) || fail "terminal receipt reporter permissions unsafe"
  cmp -s -- "$reporter_source" "$reporter_target" || fail "terminal receipt reporter digest drift"
}

install_or_verify_terminal_receipt() {
  [[ -f "$reporter_source" && ! -L "$reporter_source" ]] || fail "terminal receipt source must be a regular non-symlink file"
  if [[ "$runner_root" == /opt/* ]]; then
    verify_trusted_directory /opt
  fi
  if [[ ! -e "$runner_root" && ! -L "$runner_root" ]]; then
    install -d -o root -g root -m 0755 "$runner_root"
  fi
  verify_trusted_directory "$runner_root"
  if [[ ! -e "$libexec_dir" && ! -L "$libexec_dir" ]]; then
    install -d -o root -g root -m 0755 "$libexec_dir"
  fi
  verify_trusted_directory "$libexec_dir"

  if [[ -e "$reporter_target" || -L "$reporter_target" ]]; then
    verify_terminal_receipt
    return
  fi

  local staged_reporter
  staged_reporter=$(mktemp "$libexec_dir/.terminal-receipt.XXXXXX")
  install -o root -g root -m 0555 "$reporter_source" "$staged_reporter"
  if ! ln -- "$staged_reporter" "$reporter_target"; then
    rm -f -- "$staged_reporter"
    fail "terminal receipt reporter appeared during install"
    return 1
  fi
  rm -f -- "$staged_reporter"
  verify_terminal_receipt
}

activate_and_verify() {
  systemctl daemon-reload
  systemctl enable "$service_name"
  systemctl restart "$service_name"
  verify_effective_service
  wait_for_runner_readiness
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

  local cleanup repo_root staging reporter_source
  repo_root=$(git rev-parse --show-toplevel)
  [[ "$(git -C "$repo_root" rev-parse "$git_sha^{commit}")" == "$git_sha" ]] || fail "Git SHA is not an exact commit"
  verify_trusted_directory /opt
  if [[ ! -e "$runner_root" && ! -L "$runner_root" ]]; then
    install -d -o root -g root -m 0755 "$runner_root"
  fi
  verify_trusted_directory "$runner_root"
  staging=$(mktemp -d "$runner_root/.stage.XXXXXX")
  printf -v cleanup 'rm -rf -- %q' "$staging"
  trap "$cleanup" EXIT
  git -C "$repo_root" archive "$git_sha" | tar -x -C "$staging"
  chown -R root:root "$staging"
  chmod -R go-w "$staging"
  (cd "$staging" && uv sync --locked --no-dev --no-editable)
  reporter_source="$staging/deploy/libexec/labpilot-terminal-receipt-v1.py"
  install_or_verify_terminal_receipt

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
