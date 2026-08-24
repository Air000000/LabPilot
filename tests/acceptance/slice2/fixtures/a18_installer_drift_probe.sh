#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "$*" >&2
  return 1
}

validate_paths() {
  local fixture_root=$1 repository_root=$2
  [[ "$fixture_root" == /* && "$repository_root" == /* ]] || fail "A18 paths must be absolute"
  [[ "$(basename "$fixture_root")" =~ ^a18-[0-9a-f]{12}$ ]] || fail "A18 fixture root is invalid"
  [[ "$(basename "$(dirname "$fixture_root")")" == candidate-b-v2 ]] || fail "A18 fixture parent is invalid"
  [[ "$(git -C "$repository_root" rev-parse --show-toplevel)" == "$repository_root" ]] || fail "A18 repository root is invalid"
  [[ -f "$repository_root/scripts/install_labpilot_runner_host.sh" ]] || fail "A18 installer is unavailable"
  [[ -f "$fixture_root/runner/app/deploy/libexec/labpilot-terminal-receipt-v1.py" ]] || fail "A18 bundled reporter is unavailable"
  [[ -f "$fixture_root/runner/libexec/labpilot-terminal-receipt-v1.py" ]] || fail "A18 installed reporter is unavailable"
}

inside_namespace() {
  local fixture_root=$1 repository_root=$2
  validate_paths "$fixture_root" "$repository_root"
  mount --bind "$fixture_root/runner" /opt/labpilot-runner
  local reporter=/opt/labpilot-runner/libexec/labpilot-terminal-receipt-v1.py
  printf '\n# isolated-a18-drift\n' >> "$reporter"
  local drift_hash status
  drift_hash=$(sha256sum "$reporter" | cut -d ' ' -f 1)
  source "$repository_root/scripts/install_labpilot_runner_host.sh"
  if install_or_verify_terminal_receipt; then
    fail "A18 validator accepted isolated reporter drift"
    return 1
  else
    status=$?
  fi
  [[ "$status" != 0 ]] || fail "A18 validator did not fail closed"
  [[ "$(sha256sum "$reporter" | cut -d ' ' -f 1)" == "$drift_hash" ]] || fail "A18 validator overwrote isolated drift"
}

main() {
  [[ "$(id -u)" == 0 ]] || fail "A18 requires root test-admin authority"
  if [[ "${1-}" == --inside ]]; then
    [[ $# == 3 ]] || fail "A18 internal usage is fixed"
    inside_namespace "$2" "$3"
    return
  fi
  [[ $# == 2 ]] || fail "usage: $0 <fixed-fixture-root> <repository-root>"
  local fixture_root=$1 repository_root=$2
  validate_paths "$fixture_root" "$repository_root"
  local reporter=/opt/labpilot-runner/libexec/labpilot-terminal-receipt-v1.py
  local before after
  before=$(sha256sum "$reporter" | cut -d ' ' -f 1)
  unshare --mount --propagation private -- "$0" --inside "$fixture_root" "$repository_root"
  after=$(sha256sum "$reporter" | cut -d ' ' -f 1)
  [[ "$before" == "$after" ]] || fail "A18 changed the host production reporter"
  printf '%s\n' '{"host_reporter_unchanged":true,"validator_failed_closed":true}'
}

main "$@"
