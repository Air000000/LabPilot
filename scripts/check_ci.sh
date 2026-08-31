#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

echo "== Verify WP0 frozen dependencies =="
./scripts/verify_architecture_dependencies.sh \
  docs/labpilot/implementation/phase-0-v0.3.4.dependencies.sha256

echo "== Verify WP1-A frozen dependencies =="
./scripts/verify_architecture_dependencies.sh \
  docs/labpilot/implementation/wp1-a-v0.2.dependencies.sha256

echo "== Install =="
uv lock --check
uv sync --locked --all-groups

echo "== Ruff =="
uv run ruff check src tests scripts

echo "== Mypy =="
uv run mypy src scripts

echo "== Unit and architecture tests =="
uv run pytest tests/unit tests/architecture -v

echo "== Complete integration tests =="
uv run pytest tests/integration -v -m integration

echo "== Dependency guard shell tests =="
./tests/scripts/test_verify_architecture_dependencies.sh
