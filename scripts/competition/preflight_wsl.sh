#!/usr/bin/env bash
set -eu

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

pass() {
  printf 'PASS: %s\n' "$1"
}

[ "$(uname -s)" = "Linux" ] || fail "must run inside Linux/WSL2"
if grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null || grep -qi microsoft /proc/version 2>/dev/null; then
  pass "running inside WSL"
else
  printf 'WARN: Linux detected but WSL marker was not found\n'
fi

pid1="$(ps -p 1 -o comm= | tr -d '[:space:]')"
[ "$pid1" = "systemd" ] || fail "PID 1 is '$pid1'; enable systemd in WSL2 before using the LabPilot Runner"
pass "systemd is PID 1"

case "$PWD" in
  /mnt/c/*|/mnt/d/*|/mnt/e/*|/mnt/f/*|/mnt/g/*|/mnt/h/*)
    fail "repository is under a Windows-mounted filesystem; clone it under the WSL Linux filesystem"
    ;;
esac
pass "repository path is on the Linux filesystem"

command -v git >/dev/null 2>&1 || fail "git is missing"
pass "git is available"

command -v python3.12 >/dev/null 2>&1 || fail "python3.12 is missing"
python_version="$(python3.12 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
case "$python_version" in
  3.12.*) pass "python3.12 is available ($python_version)" ;;
  *) fail "unexpected python3.12 version: $python_version" ;;
esac

command -v uv >/dev/null 2>&1 || fail "uv is missing"
pass "uv is available ($(uv --version | head -n 1))"

command -v docker >/dev/null 2>&1 || fail "docker CLI is missing"
docker compose version >/dev/null 2>&1 || fail "docker compose is unavailable"
docker info >/dev/null 2>&1 || fail "docker daemon is not reachable from WSL2"
pass "Docker and Docker Compose are reachable"

[ -d /run/systemd/system ] || fail "/run/systemd/system is missing"
pass "systemd runtime directory exists"

printf '\nRecommended WSL-local paths:\n'
printf '  LabPilot repo:       ~/src/LabPilot\n'
printf '  demo repository:    ~/labpilot-demo-repo\n'
printf '  attempt workspaces: ~/labpilot-attempts\n'
printf '\nPRECHECK=PASS\n'
