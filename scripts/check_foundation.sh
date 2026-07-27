#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

if [ -n "$(git status --porcelain)" ]; then
  echo "acceptance requires a clean Git worktree" >&2
  git status --short >&2
  exit 1
fi

PROJECT="labpilot_wp0_acceptance_$$"
TMP_DIR=$(mktemp -d)
API_PORT=$(python3 - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)
export LABPILOT_API_PORT="$API_PORT"

cleanup() {
  docker compose -p "$PROJECT" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT INT TERM

TESTED_COMMIT=$(git rev-parse HEAD)
TESTED_TREE=$(git rev-parse 'HEAD^{tree}')
PLAN_PATH=docs/superpowers/plans/2026-07-27-labpilot-phase-0-foundation-v0.3.4-ready-for-execution.md
MANIFEST_PATH=docs/labpilot/implementation/phase-0-v0.3.4.dependencies.sha256
PLAN_HASH=$(sha256sum "$PLAN_PATH" | awk '{print $1}')
MANIFEST_HASH=$(sha256sum "$MANIFEST_PATH" | awk '{print $1}')
SCRIPT_HASH=$(sha256sum scripts/check_foundation.sh | awk '{print $1}')
VERIFIED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)

./scripts/verify_architecture_dependencies.sh "$MANIFEST_PATH" \
  > "$TMP_DIR/dependency-verification.txt"
uv sync --frozen --all-groups
uv run ruff check src tests scripts
uv run mypy src scripts
uv run pytest tests/unit tests/architecture -v
uv run pytest tests/integration -v -m integration
./tests/scripts/test_verify_architecture_dependencies.sh

docker compose -p "$PROJECT" up --build -d

python3 - "$API_PORT" <<'PY'
import json
import sys
import time
import urllib.request

port = int(sys.argv[1])
url = f"http://127.0.0.1:{port}/health/ready"
for _ in range(60):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            payload = json.load(response)
            if response.status == 200 and payload["status"] == "ready":
                break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit("API readiness did not become healthy")
PY

curl --fail "http://127.0.0.1:${API_PORT}/health/live"
coordinator_id=$(docker compose -p "$PROJECT" ps -q coordinator)
projection_id=$(docker compose -p "$PROJECT" ps -q projection)
docker compose -p "$PROJECT" stop -t 10 coordinator projection
docker compose -p "$PROJECT" logs coordinator projection \
  > "$TMP_DIR/worker-shells.log"
if grep -q 'Traceback' "$TMP_DIR/worker-shells.log"; then
  cat "$TMP_DIR/worker-shells.log" >&2
  exit 1
fi
for container_id in "$coordinator_id" "$projection_id"; do
  state=$(docker inspect \
    --format '{{.State.ExitCode}} {{.State.OOMKilled}} {{.State.Error}}' \
    "$container_id")
  [ "$state" = "0 false " ] || {
    echo "unexpected worker exit state: $state" >&2
    exit 1
  }
done

cat > docs/labpilot/implementation/phase-0-acceptance.md <<EOF_RECORD
# LabPilot Phase 0 Acceptance

- Tested commit: \`$TESTED_COMMIT\`
- Tested tree: \`$TESTED_TREE\`
- Verified at: \`$VERIFIED_AT\`
- WP0 plan SHA-256: \`$PLAN_HASH\`
- Dependency manifest SHA-256: \`$MANIFEST_HASH\`
- Acceptance script SHA-256: \`$SCRIPT_HASH\`
- Command identity: \`LABPILOT_TEST_ADMIN_DATABASE_URL=<redacted> ./scripts/check_foundation.sh\`
- Compose project: \`$PROJECT\`
- API port: \`$API_PORT\`
- Architecture dependencies: PASS
- Canonical JSON V1 unit tests: PASS
- Strict UnitOfWork lifecycle: PASS
- Empty-database Alembic migration: PASS
- RuntimeEvent/Outbox atomicity and concurrent sequence: PASS
- Module dependency allowlist: PASS
- PostgreSQL SKIP LOCKED capability: PASS
- API liveness/readiness: PASS
- API, Coordinator shell, Projection shell topology: PASS
- Coordinator/Projection SIGTERM exit code 0 within 10 seconds: PASS
- Worker Database.dispose() unit tests: PASS

This record does not claim Durable Coordinator Job processing, projection delivery,
Runner execution, Agent Runtime, Tool execution, or Result Plane capabilities.
EOF_RECORD
