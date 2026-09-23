#!/usr/bin/env bash
# Start the isolated interview API and coordinator against the existing host Runner.
set -euo pipefail
repo=/home/air/projects/labpilot
config=/home/air/.config/labpilot
dbpass="$config/operator-interview-db-password"
provider="$config/interview-provider.env"
container=labpilot-operator-interview-postgres

case "${1:-start}" in
  api|coordinator)
    cd "$repo"
    export LABPILOT_DATABASE_URL="postgresql+asyncpg://labpilot_operator:$(cat "$dbpass")@127.0.0.1:55436/labpilot_operator_interview"
    export LABPILOT_ENVIRONMENT=development
    export LABPILOT_INSTALLATION_IDENTITY=labpilot-operator-interview
    export LABPILOT_LOCAL_ACTOR_ID=interview-operator
    export LABPILOT_DATA_EGRESS_POLICY=remote_provider_allowed
    export LABPILOT_REPOSITORY_ALLOWED_ROOTS='["/home/air/labpilot-demo-repo"]'
    export LABPILOT_REPOSITORY_CONTENT_STORE_ROOT=/var/lib/labpilot/operator-content
    export LABPILOT_ATTEMPT_WORKSPACE_ROOT=/var/lib/labpilot/attempts
    if [[ "$1" == api ]]; then
      export LABPILOT_AGENT_PROVIDER_KIND=deepseek
      export LABPILOT_AGENT_PROVIDER_MODEL=deepseek-v4-flash
      export LABPILOT_AGENT_PROVIDER_API_KEY="$(sed -n 's/^LABPILOT_AGENT_PROVIDER_API_KEY=//p' "$provider" | head -n 1 | sed 's/[[:space:]]*$//')"
      test -n "$LABPILOT_AGENT_PROVIDER_API_KEY"
      exec runuser -u air -g labpilot-train -- "$repo/.venv/bin/uvicorn" labpilot.main:app --host 127.0.0.1 --port 18001
    fi
    export LABPILOT_RUNNER_SOCKET_PATH=/run/labpilot-runner/control.sock
    export LABPILOT_CONTROLLER_UID="$(id -u labpilot-controller)"
    export LABPILOT_CONTROLLER_GID="$(getent group labpilot-control | cut -d: -f3)"
    export LABPILOT_TRAIN_GID="$(getent group labpilot-train | cut -d: -f3)"
    exec runuser -u labpilot-controller -g labpilot-control -G labpilot-train -- "$repo/.venv/bin/python" -m labpilot.coordinator.worker
    ;;
  start)
    [[ $EUID -eq 0 ]] || { echo 'Run as root with sudo or wsl -u root' >&2; exit 1; }
    [[ -f "$dbpass" && "$(stat -c %a "$dbpass")" == 600 ]] || { echo 'Operator DB password file missing or not 0600' >&2; exit 1; }
    [[ -f "$provider" && "$(stat -c %a "$provider")" == 600 && "$(stat -c %u "$provider")" == "$(id -u air)" ]] || { echo 'Provider env missing, not owned by air, or not 0600' >&2; exit 1; }
    grep -q '^LABPILOT_AGENT_PROVIDER_API_KEY=.' "$provider" || { echo 'Provider key missing' >&2; exit 1; }
    systemctl is-active --quiet labpilot-runner.service || { echo 'Host Runner is not active' >&2; exit 1; }
    docker start "$container" >/dev/null
    install -d -o labpilot-controller -g labpilot-train -m 2770 /var/lib/labpilot/operator-content
    if ! systemctl is-active --quiet labpilot-operator-interview-api.service; then
      ss -ltn '( sport = :18001 )' | grep -q 127.0.0.1 && { echo 'Port 18001 is occupied' >&2; exit 1; }
      systemd-run --unit=labpilot-operator-interview-api --collect \
        --property=Restart=on-failure --property=RestartSec=2 \
        /bin/bash "$repo/scripts/start_interview_operator_demo.sh" api >/dev/null
    fi
    if ! systemctl is-active --quiet labpilot-operator-interview-coordinator.service; then
      systemd-run --unit=labpilot-operator-interview-coordinator --collect \
        --property=Restart=on-failure --property=RestartSec=2 \
        /bin/bash "$repo/scripts/start_interview_operator_demo.sh" coordinator >/dev/null
    fi
    for _ in $(seq 1 30); do
      if curl -fsS http://127.0.0.1:18001/health/ready >/dev/null; then
        echo 'Operator ready: http://127.0.0.1:18001/operator'
        exit 0
      fi
      sleep 1
    done
    systemctl --no-pager --full status labpilot-operator-interview-api.service labpilot-operator-interview-coordinator.service >&2
    exit 1
    ;;
  *) echo 'Usage: start_interview_operator_demo.sh [start|api|coordinator]' >&2; exit 2 ;;
esac
