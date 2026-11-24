#!/usr/bin/env bash
# Interview-only bootstrap; preserve the existing database and host Runner boundary.
set -euo pipefail
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
demo_user=${LABPILOT_INTERVIEW_USER:-$(stat -c %U "$repo")}
demo_uid=$(id -u "$demo_user") || exit 1
demo_home=$(getent passwd "$demo_user" | cut -d: -f6)
[[ "$demo_uid" != 0 && "$demo_home" == /* ]] || {
  echo 'Select a non-root interview account with LABPILOT_INTERVIEW_USER' >&2
  exit 1
}
config=${LABPILOT_INTERVIEW_CONFIG_DIR:-$demo_home/.config/labpilot}
[[ "$config" == /* ]] || { echo 'LABPILOT_INTERVIEW_CONFIG_DIR must be absolute' >&2; exit 1; }
dbpass="$config/operator-interview-db-password"
provider="$config/interview-provider.env"
container=labpilot-operator-interview-postgres
api_unit=labpilot-operator-interview-api.service
coordinator_unit=labpilot-operator-interview-coordinator.service
probe="$repo/scripts/interview_demo_diagnostics.py"

require_files() {
  [[ -x "$repo/.venv/bin/python" ]] || { echo "Python missing: run uv sync --locked --all-groups in $repo" >&2; return 1; }
  [[ -f "$dbpass" && "$(stat -c %a "$dbpass")" == 600 ]] || { echo 'Operator DB password file missing or not 0600' >&2; return 1; }
  [[ -f "$provider" && "$(stat -c %a "$provider")" == 600 && "$(stat -c %u "$provider")" == "$demo_uid" ]] || { echo 'Provider env missing, not owned by the interview account, or not 0600' >&2; return 1; }
  grep -q '^LABPILOT_AGENT_PROVIDER_API_KEY=.' "$provider" || { echo 'Provider key missing' >&2; return 1; }
}

configure_docker() {
  docker_host=unix:///var/run/docker.sock
  if [[ -e "$config/interview-docker-host" ]]; then
    docker_host=$(cat "$config/interview-docker-host") || return 1
  fi
  [[ "$docker_host" =~ ^unix:///[A-Za-z0-9_./-]+$ ]] || {
    echo 'Invalid interview-docker-host: expected a local unix:/// socket path' >&2
    return 1
  }
  echo "Docker endpoint=$docker_host"
}

docker_cli() (
  # Keep routing local to this script, independent of the caller's Desktop context.
  unset DOCKER_CONTEXT DOCKER_HOST DOCKER_TLS_VERIFY DOCKER_CERT_PATH
  docker --host "$docker_host" "$@"
)

configure_database() {
  export LABPILOT_DATABASE_URL
  LABPILOT_DATABASE_URL=$("$repo/.venv/bin/python" "$probe" url "$dbpass")
}

service_state() {
  # No journal or environment output: those can contain secrets.
  echo "service=$1"
  systemctl show "$1" --property=ActiveState,SubState,Result,ExecMainStatus --no-pager
}

failure() {
  echo "$1" >&2
  service_state labpilot-runner.service >&2 || true
  service_state "$api_unit" >&2 || true
  service_state "$coordinator_unit" >&2 || true
  echo "Run: bash $repo/scripts/start_interview_operator_demo.sh doctor" >&2
  exit 1
}

http_status() {
  curl --noproxy '127.0.0.1,localhost' --silent --connect-timeout 3 --max-time 8 --output /dev/null --write-out '%{http_code}' "$1" || true
}

case "${1:-start}" in
  api|coordinator)
    cd "$repo"
    configure_database
    export LABPILOT_ENVIRONMENT=development
    export LABPILOT_INSTALLATION_IDENTITY=labpilot-operator-interview
    export LABPILOT_LOCAL_ACTOR_ID=interview-operator
    export LABPILOT_DATA_EGRESS_POLICY=remote_provider_allowed
    if [[ -z "${LABPILOT_REPOSITORY_ALLOWED_ROOTS:-}" ]]; then
      LABPILOT_REPOSITORY_ALLOWED_ROOTS=$("$repo/.venv/bin/python" \
        -c 'import json, sys; print(json.dumps(sys.argv[1:]))' "$demo_home/labpilot-demo-repo")
    fi
    export LABPILOT_REPOSITORY_ALLOWED_ROOTS
    export LABPILOT_REPOSITORY_CONTENT_STORE_ROOT=/var/lib/labpilot/operator-content
    export LABPILOT_ATTEMPT_WORKSPACE_ROOT=/var/lib/labpilot/attempts
    if [[ "$1" == api ]]; then
      export LABPILOT_AGENT_PROVIDER_KIND=deepseek
      export LABPILOT_AGENT_PROVIDER_MODEL=deepseek-v4-flash
      export LABPILOT_AGENT_PROVIDER_API_KEY="$(sed -n 's/^LABPILOT_AGENT_PROVIDER_API_KEY=//p' "$provider" | head -n 1 | sed 's/[[:space:]]*$//')"
      test -n "$LABPILOT_AGENT_PROVIDER_API_KEY"
      exec runuser -u "$demo_user" -g labpilot-train -- "$repo/.venv/bin/uvicorn" labpilot.main:app --host 127.0.0.1 --port 18001
    fi
    export LABPILOT_RUNNER_SOCKET_PATH=/run/labpilot-runner/control.sock
    export LABPILOT_CONTROLLER_UID="$(id -u labpilot-controller)"
    export LABPILOT_CONTROLLER_GID="$(getent group labpilot-control | cut -d: -f3)"
    export LABPILOT_TRAIN_GID="$(getent group labpilot-train | cut -d: -f3)"
    exec runuser -u labpilot-controller -g labpilot-control -G labpilot-train -- "$repo/.venv/bin/python" -m labpilot.coordinator.worker
    ;;
  start)
    [[ "$(id -u)" == 0 ]] || { echo 'Run as root with sudo or wsl -u root' >&2; exit 1; }
    require_files || exit 1
    configure_docker || exit 1
    systemctl is-active --quiet labpilot-runner.service || failure 'Host Runner is not active; start labpilot-runner.service first'
    docker_cli start "$container" >/dev/null || failure 'Interview PostgreSQL container failed to start; check Docker endpoint and interview-docker-host; do not recreate the database'
    binding=$(docker_cli port "$container" 5432/tcp) || failure 'Cannot inspect interview PostgreSQL port'
    [[ "$binding" == '127.0.0.1:55436' ]] || failure "Interview DB port mismatch: expected 127.0.0.1:55436; refusing migration"
    cd "$repo"
    configure_database
    database_state='database=unavailable migration=unknown detail=PostgreSQLStartupTimeout'
    database_ready=false
    for _ in $(seq 1 30); do
      if docker_cli exec "$container" pg_isready -U labpilot_operator -d labpilot_operator_interview >/dev/null 2>&1; then
        if database_state=$("$repo/.venv/bin/python" "$probe" database); then
          database_ready=true
          break
        fi
      fi
      sleep 1
    done
    $database_ready || failure "Interview PostgreSQL not ready: $database_state; check container and DB credentials"
    echo "Interview DB before migration: $database_state"
    migration_log=$(mktemp)
    trap 'rm -f "$migration_log"' EXIT
    # Reuse the supported upgrade-only path with its PostgreSQL advisory lock.
    if ! PGCONNECT_TIMEOUT=5 timeout 90 "$repo/.venv/bin/python" "$repo/scripts/run_migrations.py" >"$migration_log" 2>&1; then
      database_state=$("$repo/.venv/bin/python" "$probe" database) || true
      failure "Interview migration upgrade failed: $database_state; check DB role/schema ownership and current migration history (no downgrade)"
    fi
    database_state=$("$repo/.venv/bin/python" "$probe" database) || failure 'Database unavailable after migration'
    [[ "$database_state" == *'migration=head '* ]] || failure "Interview migration did not reach unique head: $database_state"
    echo "Interview DB after migration: $database_state"
    install -d -o labpilot-controller -g labpilot-train -m 2770 /var/lib/labpilot/operator-content
    unit_env=("--setenv=LABPILOT_INTERVIEW_USER=$demo_user"
              "--setenv=LABPILOT_INTERVIEW_CONFIG_DIR=$config")
    if [[ -n "${LABPILOT_REPOSITORY_ALLOWED_ROOTS:-}" ]]; then
      unit_env+=("--setenv=LABPILOT_REPOSITORY_ALLOWED_ROOTS=$LABPILOT_REPOSITORY_ALLOWED_ROOTS")
    fi
    if ! systemctl is-active --quiet "$api_unit"; then
      ss -ltn '( sport = :18001 )' | grep -q 127.0.0.1 && failure 'Port 18001 is occupied; inspect the existing listener'
      systemd-run --unit=labpilot-operator-interview-api --collect \
        --property=Restart=on-failure --property=RestartSec=2 "${unit_env[@]}" \
        /bin/bash "$repo/scripts/start_interview_operator_demo.sh" api >/dev/null
    fi
    if ! systemctl is-active --quiet "$coordinator_unit"; then
      systemd-run --unit=labpilot-operator-interview-coordinator --collect \
        --property=Restart=on-failure --property=RestartSec=2 "${unit_env[@]}" \
        /bin/bash "$repo/scripts/start_interview_operator_demo.sh" coordinator >/dev/null
    fi
    readiness='http=000 database=unavailable migration=unknown detail=APIStartupTimeout'
    for _ in $(seq 1 30); do
      if readiness=$("$repo/.venv/bin/python" "$probe" ready); then
        systemctl is-active --quiet "$coordinator_unit" || failure 'API ready but Coordinator is not active'
        echo "$readiness"
        echo 'Operator ready: http://127.0.0.1:18001/operator'
        exit 0
      fi
      sleep 1
    done
    failure "API readiness failed: $readiness; run doctor to check database revision and service states"
    ;;
  doctor)
    [[ "$(id -u)" == 0 ]] || { echo 'Run doctor as root with sudo or wsl -u root' >&2; exit 1; }
    cd "$repo"
    failed=0
    echo "repository=$repo"
    echo "Python/.venv=$repo/.venv/bin/python"
    if require_files; then
      configure_database
      "$repo/.venv/bin/python" --version
      database_state=$("$repo/.venv/bin/python" "$probe" database) || failed=1
      echo "$database_state"
      [[ "$database_state" == *'migration=head '* ]] || failed=1
    else
      failed=1
      echo 'database=unavailable migration=unknown detail=ConfigurationInvalid'
    fi
    echo "PostgreSQL container=$container"
    binding=unavailable
    if configure_docker; then
      docker_cli inspect --format '{{.State.Status}}' "$container" || failed=1
      binding=$(docker_cli port "$container" 5432/tcp) || { binding=unavailable; failed=1; }
    else
      failed=1
    fi
    echo 'DB port expected=127.0.0.1:55436'
    echo "DB port actual=$binding"
    [[ "$binding" == '127.0.0.1:55436' ]] || failed=1
    for unit in labpilot-runner.service "$api_unit" "$coordinator_unit"; do
      service_state "$unit" || failed=1
      systemctl is-active --quiet "$unit" || failed=1
    done
    echo 'Runner control socket=/run/labpilot-runner/control.sock'
    if [[ -S /run/labpilot-runner/control.sock ]] && runuser -u labpilot-controller -g labpilot-control -G labpilot-train -- test -r /run/labpilot-runner/control.sock -a -w /run/labpilot-runner/control.sock; then
      echo 'Runner control socket=accessible_to_coordinator'
    else
      echo 'Runner control socket=unavailable_or_permission_denied'
      failed=1
    fi
    for path in operator ops; do
      code=$(http_status "http://127.0.0.1:18001/$path")
      echo "/$path http=$code"
      [[ "$code" == 200 ]] || failed=1
    done
    if [[ -x "$repo/.venv/bin/python" ]]; then
      "$repo/.venv/bin/python" "$probe" ready || failed=1
    fi
    code=$(curl --head --silent --connect-timeout 3 --max-time 8 --output /dev/null --write-out '%{http_code}' https://api.deepseek.com/chat/completions) || code=000
    if [[ "$code" == 000 ]]; then
      echo 'WARNING: provider endpoint unreachable (DNS/TCP/TLS/HTTP); local startup is independent'
    else
      echo "provider endpoint reachable http=$code (network only; no authorization or model request)"
    fi
    exit "$failed"
    ;;
  *) echo 'Usage: start_interview_operator_demo.sh [start|doctor|api|coordinator]' >&2; exit 2 ;;
esac
