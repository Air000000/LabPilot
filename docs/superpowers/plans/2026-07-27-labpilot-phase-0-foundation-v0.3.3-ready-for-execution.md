# LabPilot Phase 0 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Immutable execution specification:** This versioned plan is hash-bound and read-only. Do not change `- [ ]` to `- [x]`, edit prose, or append execution notes. Record progress only in `docs/labpilot/implementation/phase-0-execution-progress.md`, which is intentionally excluded from the dependency manifest.

```text
Version: v0.3.3
Status: Ready for Execution
Date: 2026-07-27
```

Revision scope:
- Tasks 0–7 were completed and approved before this revision.
- WP0 v0.3.3 supersedes v0.3.2 for execution of Tasks 8–10 only.
- This revision corrects the Task 8 one-shot migration runner boundary:
  SQLAlchemy dialect URLs must not be passed directly to psycopg.connect().
- Task 8 may modify scripts/run_migrations.py and add a focused regression test.
- No completed implementation task is reopened or rewritten.

**Goal:** 建立 LabPilot 后续所有模块共同依赖、且不需要在 WP1/WP4 结构性返工的 Python/FastAPI、PostgreSQL、Migration、严格事务、Canonical Identity、完整 RuntimeEvent/Outbox、进程拓扑和 CI 底座。

**Architecture:** 使用 Python 3.12 模块化单体。FastAPI 只提供命令、查询和健康检查入口；SQLAlchemy 2 async + PostgreSQL 16 提供真实事务、行锁和并发语义；Migration 由唯一 one-shot 服务执行。Phase 0 不实现任何业务聚合，但必须冻结后续领域命令共同依赖的严格 Unit of Work、Canonical JSON V1、完整 RuntimeEvent/Outbox Envelope 和模块依赖 Allowlist。

**Tech Stack:** Python 3.12, uv, FastAPI, Pydantic v2, Pydantic Settings, SQLAlchemy 2.x async, asyncpg, Alembic, PostgreSQL 16, pytest, pytest-asyncio, Ruff, mypy, Docker Compose, GitHub Actions.

## Global Constraints

- 应用形态必须保持模块化单体；不得拆成微服务。
- 核心集成测试必须使用 PostgreSQL，不使用 SQLite 模拟 Lease、行锁、Advisory Lock 或 `SKIP LOCKED`。
- 所有数据库变更必须通过 Alembic；Migration Job 是唯一 Schema Upgrader。
- 一个 `UnitOfWork` 实例只能拥有一个显式 PostgreSQL 事务；commit 或 rollback 后禁止继续写入。
- 权威状态更新、`RuntimeEvent` 和 `OutboxRecord` 必须能够在同一个 PostgreSQL 事务中提交或回滚。
- `RuntimeEvent` 不是 Event Sourcing；权威状态仍由所属业务表持有。
- `common` 和 `db` 不得 import 业务模块；所有内部依赖使用 Allowlist 检查。
- Canonical JSON V1 不执行 Unicode normalization；拒绝孤立 surrogate、float、自定义容器和超出固定资源上限的输入。
- API、Coordinator shell 和 Projection shell 必须是独立进程；WP0 只证明进程拓扑、数据库连接和优雅停止，不声称 Durable Job 已实现。
- Phase 0 不实现 Agent、Tool、Workflow、Runner、Artifact、Web UI 或 P1 能力。
- Python 版本固定为 `>=3.12,<3.13`；PostgreSQL 固定为 `16+`。
- 代码步骤使用 TDD；每个 Task 以独立可测试 commit 结束。
- 开始 Task 1 前，必须完成 Task 0，并验证 `00–07`、Architecture Index v0.2、Master Roadmap v0.2.1 与本 WP0 v0.3.3 计划的版本化哈希清单。

---

## File Structure Locked by This Plan

```text
pyproject.toml
alembic.ini
compose.yaml
Dockerfile
.env.example
src/labpilot/
  __init__.py
  main.py
  settings.py
  api/app.py
  api/routers/health.py
  common/canonical_json.py
    common/errors.py
  common/hashing.py
  common/readiness.py
  common/signals.py
  db/base.py
  db/session.py
  db/uow.py
  db/readiness.py
  observability/models.py
  observability/payloads.py
  observability/events.py
  observability/outbox.py
  observability/projection_worker.py
  coordinator/worker.py
migrations/env.py
migrations/script.py.mako
migrations/versions/0001_initial.py
migrations/versions/0002_runtime_events_outbox.py
scripts/verify_architecture_dependencies.sh
scripts/run_migrations.py
scripts/wait_for_postgres.py
scripts/check_foundation.sh
tests/scripts/test_verify_architecture_dependencies.sh
tests/unit/
tests/integration/
tests/architecture/
.github/workflows/ci.yml
docs/labpilot/implementation/phase-0-v0.3.3.dependencies.sha256
docs/labpilot/implementation/phase-0-v0.3.3-dependency-verification.txt
docs/labpilot/implementation/phase-0-execution-progress.md  # mutable; not in dependency manifest
docs/superpowers/plans/2026-07-27-labpilot-phase-0-foundation-v0.3.3-ready-for-execution.md
```

`coordinator/worker.py` 与 `observability/projection_worker.py` 只是进程入口，分别属于 `coordinator` 和 `observability` 模块；不得创建语义模糊的 `labpilot.workers` 业务包。

---

### Task 0: Harden and Verify the Architecture Dependency Guard

**Files:**
- Modify: `scripts/verify_architecture_dependencies.sh`
- Create: `tests/scripts/test_verify_architecture_dependencies.sh`
- Verify: `docs/labpilot/implementation/phase-0-v0.3.3.dependencies.sha256`
- Verify: `docs/labpilot/implementation/phase-0-v0.3.3-dependency-verification.txt`
- Create during execution: `docs/labpilot/implementation/phase-0-execution-progress.md`

**Interfaces:**
- Consumes: frozen architecture files `00–07`, Architecture Index v0.2, Master Roadmap v0.2.1, and this immutable WP0 v0.3.3 plan.
- Produces: `scripts/verify_architecture_dependencies.sh <repo-relative-manifest>`; non-zero exit on malformed, missing, escaped, absolute, or mismatched entries.

- [ ] **Step 1: Write the failing shell test suite**

Create `tests/scripts/test_verify_architecture_dependencies.sh`:

```sh
#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
SCRIPT="$ROOT/scripts/verify_architecture_dependencies.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT INT TERM

make_repo() {
  name=$1
  repo="$TMP/$name"
  mkdir -p "$repo/scripts" "$repo/docs"
  cp "$SCRIPT" "$repo/scripts/verify_architecture_dependencies.sh"
  chmod +x "$repo/scripts/verify_architecture_dependencies.sh"
  printf 'alpha\n' > "$repo/docs/a.md"
  printf 'beta\n' > "$repo/docs/b.md"
  (
    cd "$repo"
    sha256sum docs/a.md docs/b.md > manifest.sha256
  )
  printf '%s\n' "$repo"
}

expect_success() {
  name=$1
  shift
  if ! "$@" >"$TMP/$name.out" 2>"$TMP/$name.err"; then
    cat "$TMP/$name.out" "$TMP/$name.err" >&2
    echo "expected success: $name" >&2
    exit 1
  fi
}

expect_failure() {
  name=$1
  shift
  if "$@" >"$TMP/$name.out" 2>"$TMP/$name.err"; then
    echo "expected failure: $name" >&2
    exit 1
  fi
}

repo=$(make_repo success)
expect_success valid "$repo/scripts/verify_architecture_dependencies.sh" manifest.sha256
grep -q 'docs/a.md: OK' "$TMP/valid.out"
grep -q 'docs/b.md: OK' "$TMP/valid.out"

repo=$(make_repo missing)
rm "$repo/docs/b.md"
expect_failure missing "$repo/scripts/verify_architecture_dependencies.sh" manifest.sha256
grep -q 'missing' "$TMP/missing.err"

repo=$(make_repo changed)
printf 'changed\n' > "$repo/docs/a.md"
expect_failure changed "$repo/scripts/verify_architecture_dependencies.sh" manifest.sha256
grep -q 'expected=' "$TMP/changed.err"
grep -q 'actual=' "$TMP/changed.err"

repo=$(make_repo malformed)
printf 'not-a-manifest-line\n' > "$repo/manifest.sha256"
expect_failure malformed "$repo/scripts/verify_architecture_dependencies.sh" manifest.sha256
grep -q 'malformed manifest line' "$TMP/malformed.err"

repo=$(make_repo absolute)
hash=$(sha256sum "$repo/docs/a.md" | awk '{print $1}')
printf '%s  %s\n' "$hash" "$repo/docs/a.md" > "$repo/manifest.sha256"
expect_failure absolute "$repo/scripts/verify_architecture_dependencies.sh" manifest.sha256
grep -q 'absolute dependency path' "$TMP/absolute.err"

repo=$(make_repo escape)
printf 'outside\n' > "$TMP/outside.md"
hash=$(sha256sum "$TMP/outside.md" | awk '{print $1}')
printf '%s  ../outside.md\n' "$hash" > "$repo/manifest.sha256"
expect_failure escape "$repo/scripts/verify_architecture_dependencies.sh" manifest.sha256
grep -q 'escapes repository root' "$TMP/escape.err"

echo 'architecture dependency guard tests: PASS'
```

Make it executable:

```bash
chmod +x tests/scripts/test_verify_architecture_dependencies.sh
```

- [ ] **Step 2: Run the suite and verify the current script fails**

Run:

```bash
./tests/scripts/test_verify_architecture_dependencies.sh
```

Expected: FAIL in at least the absolute-path, escape, malformed-line, or expected/actual diagnostic case.

- [ ] **Step 3: Replace the guard with a validated repository-relative implementation**

Replace `scripts/verify_architecture_dependencies.sh` with:

```sh
#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <repo-relative-dependency-manifest>" >&2
  exit 64
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
MANIFEST_ARG=$1

case "$MANIFEST_ARG" in
  /*)
    echo "absolute manifest path is not allowed: $MANIFEST_ARG" >&2
    exit 64
    ;;
esac

MANIFEST=$(realpath -m -- "$ROOT_DIR/$MANIFEST_ARG")
case "$MANIFEST" in
  "$ROOT_DIR"/*) ;;
  *)
    echo "manifest escapes repository root: $MANIFEST_ARG" >&2
    exit 64
    ;;
esac

if [ ! -f "$MANIFEST" ]; then
  echo "dependency manifest missing: $MANIFEST_ARG" >&2
  exit 66
fi

line_number=0
failed=0
while IFS= read -r line || [ -n "$line" ]; do
  line_number=$((line_number + 1))
  case "$line" in
    '') continue ;;
  esac

  if ! printf '%s\n' "$line" | grep -Eq '^[0-9a-f]{64}  [^[:space:]].*$'; then
    echo "malformed manifest line $line_number: $line" >&2
    failed=1
    continue
  fi

  expected=${line%%  *}
  dependency=${line#*  }

  case "$dependency" in
    /*)
      echo "absolute dependency path is not allowed: $dependency" >&2
      failed=1
      continue
      ;;
  esac

  target=$(realpath -m -- "$ROOT_DIR/$dependency")
  case "$target" in
    "$ROOT_DIR"/*) ;;
    *)
      echo "dependency path escapes repository root: $dependency" >&2
      failed=1
      continue
      ;;
  esac

  if [ ! -f "$target" ]; then
    echo "$dependency: missing" >&2
    failed=1
    continue
  fi

  actual=$(sha256sum "$target" | awk '{print $1}')
  if [ "$actual" != "$expected" ]; then
    echo "$dependency: FAILED expected=$expected actual=$actual" >&2
    failed=1
    continue
  fi

  echo "$dependency: OK"
done < "$MANIFEST"

if [ "$failed" -ne 0 ]; then
  exit 1
fi
```

Make it executable:

```bash
chmod 755 scripts/verify_architecture_dependencies.sh
```

- [ ] **Step 4: Run the guard tests and verify all cases pass**

Run:

```bash
./tests/scripts/test_verify_architecture_dependencies.sh
```

Expected: exit `0` and output `architecture dependency guard tests: PASS`.

- [ ] **Step 5: Verify the real manifest without modifying frozen evidence**

Run:

```bash
verification_output=$(mktemp)
trap 'rm -f "$verification_output"' EXIT INT TERM
./scripts/verify_architecture_dependencies.sh \
  docs/labpilot/implementation/phase-0-v0.3.3.dependencies.sha256 \
  | tee "$verification_output"
test "$(grep -c ': OK$' "$verification_output")" -eq 11
```

Expected: eleven `OK` lines for frozen `00–07`, Architecture Index v0.2, Master Roadmap v0.2.1, and this immutable WP0 v0.3.3 plan. The hash-bound planning-time verification record is not overwritten.

- [ ] **Step 6: Commit the dependency guard implementation**

```bash
git add scripts/verify_architecture_dependencies.sh \
  tests/scripts/test_verify_architecture_dependencies.sh
git commit -m "build: guard frozen architecture dependencies"
```

- [ ] **Step 7: Initialize the mutable execution progress log from real values**

Run:

```bash
completed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
implementation_commit=$(git rev-parse HEAD)
cat > docs/labpilot/implementation/phase-0-execution-progress.md <<EOF_PROGRESS
# LabPilot Phase 0 Execution Progress

This file records mutable execution state. It is not part of the frozen dependency manifest.

| Task | Status | Started at | Completed at | Implementation commit | Verification | Review |
|---:|---|---|---|---|---|---|
| 0 | completed | $completed_at | $completed_at | $implementation_commit | dependency guard 11/11 | pending |
| 1 | pending | — | — | — | — | — |
| 2 | pending | — | — | — | — | — |
| 3 | pending | — | — | — | — | — |
| 4 | pending | — | — | — | — | — |
| 5 | pending | — | — | — | — | — |
| 6 | pending | — | — | — | — | — |
| 7 | pending | — | — | — | — | — |
| 8 | pending | — | — | — | — | — |
| 9 | pending | — | — | — | — | — |
| 10 | pending | — | — | — | — | — |
EOF_PROGRESS
```

Never edit the versioned WP0 plan checkboxes.

- [ ] **Step 8: Commit the mutable progress ledger**

```bash
git add docs/labpilot/implementation/phase-0-execution-progress.md
git commit -m "docs: initialize WP0 execution progress"
```

---

### Task 1: Bootstrap the Python Package and Quality Toolchain

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/labpilot/__init__.py`
- Create: `tests/unit/test_package.py`

**Interfaces:**
- Consumes: successful Task 0 dependency verification.
- Produces: importable `labpilot` package; `uv run pytest`, `uv run ruff`, and `uv run mypy` commands used by all later tasks.

- [ ] **Step 1: Create project metadata, an installable stub package, and a failing version test**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[project]
name = "labpilot"
version = "0.1.0"
description = "Experiment-centric Research Agent OS"
requires-python = ">=3.12,<3.13"
dependencies = [
  "alembic>=1.16,<2",
  "asyncpg>=0.30,<0.31",
  "fastapi>=0.116,<0.117",
  "httpx>=0.28,<0.29",
  "orjson>=3.10,<4",
  "pydantic>=2.11,<3",
  "pydantic-settings>=2.10,<3",
  "sqlalchemy[asyncio]>=2.0.41,<2.1",
  "uvicorn[standard]>=0.35,<0.36",
]

[dependency-groups]
dev = [
  "mypy>=1.17,<2",
  "pytest>=8.4,<9",
  "pytest-asyncio>=1.1,<2",
  "ruff>=0.12,<0.13",
]

[tool.hatch.build.targets.wheel]
packages = ["src/labpilot"]

[tool.pytest.ini_options]
addopts = "-ra --strict-config --strict-markers"
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
  "integration: requires PostgreSQL",
  "architecture: verifies module dependency rules",
  "destructive: creates and drops an isolated PostgreSQL database",
]

[tool.ruff]
target-version = "py312"
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "ASYNC", "RUF"]

[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]
mypy_path = "src"
packages = ["labpilot"]

[tool.pydantic-mypy]
init_forbid_extra = true
warn_required_dynamic_aliases = true
```

Create `.gitignore`:

```gitignore
.venv/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
.env
*.pyc
*.pyo
*.egg-info/
dist/
build/
.artifacts/
.runtime/
```

Create `src/labpilot/__init__.py`:

```python
"""LabPilot application package."""
```

Create `tests/unit/test_package.py`:

```python
import labpilot


def test_package_exposes_the_release_version() -> None:
    assert labpilot.__version__ == "0.1.0"
```

- [ ] **Step 2: Install dependencies and verify the focused test fails at the intended assertion**

Run:

```bash
uv sync --all-groups
uv run pytest tests/unit/test_package.py -v
```

Expected: FAIL with `AttributeError: module 'labpilot' has no attribute '__version__'`. Installation itself must succeed.

- [ ] **Step 3: Add the minimal package version**

Replace `src/labpilot/__init__.py` with:

```python
"""LabPilot application package."""

__version__ = "0.1.0"
```

- [ ] **Step 4: Run focused and quality checks**

Run:

```bash
uv run pytest tests/unit/test_package.py -v
uv run ruff check src tests
uv run mypy src
```

Expected: all commands exit `0`; pytest reports `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock .gitignore src/labpilot/__init__.py tests/unit/test_package.py
git commit -m "build: bootstrap LabPilot Python package"
```

---

### Task 2: Add Typed Settings, App Factory, and Live/Ready Health Contracts

**Files:**
- Create: `.env.example`
- Create: `src/labpilot/settings.py`
- Create: `src/labpilot/api/__init__.py`
- Create: `src/labpilot/api/app.py`
- Create: `src/labpilot/common/__init__.py`
- Create: `src/labpilot/common/readiness.py`
- Create: `src/labpilot/api/routers/__init__.py`
- Create: `src/labpilot/api/routers/health.py`
- Create: `src/labpilot/main.py`
- Create: `tests/unit/api/test_health.py`
- Create: `tests/unit/test_settings.py`

**Interfaces:**
- Consumes: importable package from Task 1.
- Produces: `Settings`, `ReadinessProbe`, `ReadinessResult`, `create_app()`, `/health/live`, `/health/ready`.

- [ ] **Step 1: Write failing settings and health tests**

Create `tests/unit/test_settings.py`:

```python
from labpilot.settings import Settings


def test_settings_require_a_postgresql_async_url() -> None:
    settings = Settings(database_url="postgresql+asyncpg://user:pass@db:5432/labpilot")
    assert settings.database_url.startswith("postgresql+asyncpg://")
```

Create `tests/unit/api/test_health.py`:

```python
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from labpilot.api.app import create_app
from labpilot.common.readiness import ReadinessResult


@dataclass
class FakeProbe:
    result: ReadinessResult

    async def check(self) -> ReadinessResult:
        return self.result


def test_liveness_does_not_depend_on_database_readiness() -> None:
    app = create_app(
        readiness_probe=FakeProbe(
            ReadinessResult(
                ready=False,
                database="unavailable",
                migration="unknown",
                detail="database is down",
            )
        )
    )
    response = TestClient(app).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_returns_200_when_dependencies_are_ready() -> None:
    app = create_app(
        readiness_probe=FakeProbe(
            ReadinessResult(
                ready=True,
                database="ok",
                migration="head",
                detail=None,
            )
        )
    )
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "ok",
        "migration": "head",
        "detail": None,
    }


def test_readiness_returns_503_when_dependencies_are_not_ready() -> None:
    app = create_app(
        readiness_probe=FakeProbe(
            ReadinessResult(
                ready=False,
                database="unavailable",
                migration="unknown",
                detail="database is down",
            )
        )
    )
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_app_lifespan_runs_shutdown_callbacks_once() -> None:
    calls = 0

    async def shutdown() -> None:
        nonlocal calls
        calls += 1

    app = create_app(shutdown_callbacks=(shutdown,))
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
    assert calls == 1


@pytest.mark.asyncio
async def test_lifespan_body_failure_still_runs_shutdown() -> None:
    calls = 0

    async def shutdown() -> None:
        nonlocal calls
        calls += 1

    app = create_app(shutdown_callbacks=(shutdown,))
    with pytest.raises(ValueError, match="body failed"):
        async with app.router.lifespan_context(app):
            raise ValueError("body failed")
    assert calls == 1


@pytest.mark.asyncio
async def test_lifespan_preserves_body_and_cleanup_failures() -> None:
    async def shutdown() -> None:
        raise RuntimeError("cleanup failed")

    app = create_app(shutdown_callbacks=(shutdown,))
    with pytest.raises(BaseExceptionGroup) as captured:
        async with app.router.lifespan_context(app):
            raise ValueError("body failed")
    messages = {str(error) for error in captured.value.exceptions}
    assert messages == {"body failed", "cleanup failed"}
```

- [ ] **Step 2: Run tests and verify imports fail**

Run:

```bash
uv run pytest tests/unit/test_settings.py tests/unit/api/test_health.py -v
```

Expected: FAIL because `labpilot.settings` and `labpilot.api.app` do not exist.

- [ ] **Step 3: Implement typed settings and injectable readiness contracts**

Create `.env.example`:

```dotenv
LABPILOT_ENVIRONMENT=development
LABPILOT_DATABASE_URL=postgresql+asyncpg://labpilot:labpilot@postgres:5432/labpilot
LABPILOT_LOG_LEVEL=INFO
```

Create `src/labpilot/settings.py`:

```python
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="LABPILOT_",
        extra="ignore",
        frozen=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = Field(min_length=1)
    log_level: str = "INFO"

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("database_url must use postgresql+asyncpg")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Create `src/labpilot/common/__init__.py`:

```python
"""Stable cross-module value and infrastructure helpers."""
```

Create `src/labpilot/common/readiness.py`:

```python
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    ready: bool
    database: str
    migration: str
    detail: str | None


class ReadinessProbe(Protocol):
    async def check(self) -> ReadinessResult: ...


class UnconfiguredReadinessProbe:
    async def check(self) -> ReadinessResult:
        return ReadinessResult(
            ready=False,
            database="unknown",
            migration="unknown",
            detail="database readiness probe is not configured",
        )
```

Create `src/labpilot/api/routers/health.py`:

```python
from fastapi import APIRouter, Request, Response, status

from labpilot.common.readiness import ReadinessProbe

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict[str, str | None]:
    probe: ReadinessProbe = request.app.state.readiness_probe
    result = await probe.check()
    if not result.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if result.ready else "not_ready",
        "database": result.database,
        "migration": result.migration,
        "detail": result.detail,
    }
```

Create `src/labpilot/api/app.py`:

```python
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from labpilot.api.routers.health import router as health_router
from labpilot.common.readiness import ReadinessProbe, UnconfiguredReadinessProbe

ShutdownCallback = Callable[[], Awaitable[None]]


def create_app(
    *,
    readiness_probe: ReadinessProbe | None = None,
    shutdown_callbacks: tuple[ShutdownCallback, ...] = (),
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        body_error: BaseException | None = None
        try:
            yield
        except BaseException as exc:
            body_error = exc

        cleanup_errors: list[BaseException] = []
        for callback in reversed(shutdown_callbacks):
            try:
                await callback()
            except BaseException as exc:
                cleanup_errors.append(exc)

        if body_error is not None and cleanup_errors:
            raise BaseExceptionGroup(
                "application lifespan and cleanup failed",
                [body_error, *cleanup_errors],
            )
        if body_error is not None:
            raise body_error
        if len(cleanup_errors) == 1:
            raise cleanup_errors[0]
        if cleanup_errors:
            raise BaseExceptionGroup("application cleanup failed", cleanup_errors)

    app = FastAPI(title="LabPilot", version="0.1.0", lifespan=lifespan)
    app.state.readiness_probe = readiness_probe or UnconfiguredReadinessProbe()
    app.include_router(health_router)
    return app
```

Create empty package files:

```python
# src/labpilot/api/__init__.py
"""HTTP API package."""
```

```python
# src/labpilot/api/routers/__init__.py
"""HTTP router package."""
```

Create `src/labpilot/main.py`:

```python
from labpilot.api.app import create_app

app = create_app()
```

- [ ] **Step 4: Run focused tests and quality checks**

Run:

```bash
uv run pytest tests/unit/test_settings.py tests/unit/api/test_health.py -v
uv run ruff check src tests
uv run mypy src
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit**

```bash
git add .env.example src/labpilot/settings.py src/labpilot/main.py \
  src/labpilot/api src/labpilot/common/readiness.py tests/unit/test_settings.py tests/unit/api/test_health.py
git commit -m "feat: add typed settings and health contracts"
```

---

### Task 3: Add PostgreSQL Async Sessions and a Strict Single-Transaction Unit of Work

**Files:**
- Create: `src/labpilot/db/__init__.py`
- Create: `src/labpilot/db/base.py`
- Create: `src/labpilot/db/session.py`
- Create: `src/labpilot/db/uow.py`
- Create: `tests/unit/db/test_uow.py`
- Create: `tests/__init__.py`
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/db/test_database_session.py`

**Interfaces:**
- Consumes: `Settings.database_url`.
- Produces: `Database`, `Base`, `UnitOfWork`, `UnitOfWorkState`, `UnitOfWorkOutcome`.

- [ ] **Step 1: Write strict UoW lifecycle tests**

Create `tests/unit/db/test_uow.py`:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from labpilot.db.uow import UnitOfWork, UnitOfWorkError, UnitOfWorkOutcome, UnitOfWorkState


class FakeTransaction:
    def __init__(
        self,
        *,
        fail_commit: bool = False,
        fail_rollback: bool = False,
    ) -> None:
        self.fail_commit = fail_commit
        self.fail_rollback = fail_rollback
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("commit failed")

    async def rollback(self) -> None:
        self.rollbacks += 1
        if self.fail_rollback:
            raise RuntimeError("rollback failed")


class FakeSession:
    def __init__(
        self,
        transaction: FakeTransaction,
        *,
        fail_begin: bool = False,
        fail_close: bool = False,
    ) -> None:
        self.transaction = transaction
        self.fail_begin = fail_begin
        self.fail_close = fail_close
        self.closed = False

    async def begin(self) -> FakeTransaction:
        if self.fail_begin:
            raise RuntimeError("begin failed")
        return self.transaction

    async def close(self) -> None:
        self.closed = True
        if self.fail_close:
            raise RuntimeError("close failed")


@asynccontextmanager
async def fake_factory(session: FakeSession) -> AsyncIterator[FakeSession]:
    try:
        yield session
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_begin_failure_still_closes_session() -> None:
    session = FakeSession(FakeTransaction(), fail_begin=True)
    uow = UnitOfWork(lambda: fake_factory(session))
    with pytest.raises(RuntimeError, match="begin failed"):
        async with uow:
            pass
    assert session.closed is True
    assert uow.state is UnitOfWorkState.CLOSED
    assert uow.outcome is UnitOfWorkOutcome.FAILED
    with pytest.raises(UnitOfWorkError, match="active"):
        _ = uow.session


@pytest.mark.asyncio
async def test_begin_and_close_failures_are_both_preserved() -> None:
    session = FakeSession(
        FakeTransaction(),
        fail_begin=True,
        fail_close=True,
    )
    uow = UnitOfWork(lambda: fake_factory(session))
    with pytest.raises(BaseExceptionGroup) as captured:
        async with uow:
            pass
    messages = {str(error) for error in captured.value.exceptions}
    assert messages == {"begin failed", "close failed"}
    assert uow.state is UnitOfWorkState.CLOSED
    assert uow.outcome is UnitOfWorkOutcome.FAILED


@pytest.mark.asyncio
async def test_commit_can_only_happen_once() -> None:
    session = FakeSession(FakeTransaction())
    async with UnitOfWork(lambda: fake_factory(session)) as uow:
        await uow.commit()
        with pytest.raises(UnitOfWorkError, match="active"):
            await uow.commit()
    assert uow.state is UnitOfWorkState.CLOSED
    assert uow.outcome is UnitOfWorkOutcome.COMMITTED


@pytest.mark.asyncio
async def test_commit_then_session_access_is_rejected() -> None:
    session = FakeSession(FakeTransaction())
    async with UnitOfWork(lambda: fake_factory(session)) as uow:
        await uow.commit()
        with pytest.raises(UnitOfWorkError, match="active"):
            _ = uow.session


@pytest.mark.asyncio
async def test_rollback_then_commit_is_rejected() -> None:
    session = FakeSession(FakeTransaction())
    async with UnitOfWork(lambda: fake_factory(session)) as uow:
        await uow.rollback()
        with pytest.raises(UnitOfWorkError, match="active"):
            await uow.commit()
    assert uow.outcome is UnitOfWorkOutcome.ROLLED_BACK


@pytest.mark.asyncio
async def test_uncommitted_context_rolls_back_and_closes() -> None:
    transaction = FakeTransaction()
    session = FakeSession(transaction)
    async with UnitOfWork(lambda: fake_factory(session)):
        pass
    assert transaction.rollbacks == 1
    assert session.closed is True


@pytest.mark.asyncio
async def test_exception_after_work_rolls_back() -> None:
    transaction = FakeTransaction()
    session = FakeSession(transaction)
    with pytest.raises(RuntimeError, match="boom"):
        async with UnitOfWork(lambda: fake_factory(session)):
            raise RuntimeError("boom")
    assert transaction.rollbacks == 1
    assert session.closed is True


@pytest.mark.asyncio
async def test_commit_failure_is_not_reported_as_committed() -> None:
    session = FakeSession(FakeTransaction(fail_commit=True))
    uow = UnitOfWork(lambda: fake_factory(session))
    with pytest.raises(RuntimeError, match="commit failed"):
        async with uow:
            await uow.commit()
    assert uow.state is UnitOfWorkState.CLOSED
    assert uow.outcome is UnitOfWorkOutcome.FAILED
    assert session.closed is True


@pytest.mark.asyncio
async def test_rollback_failure_still_closes_session() -> None:
    session = FakeSession(FakeTransaction(fail_rollback=True))
    uow = UnitOfWork(lambda: fake_factory(session))
    with pytest.raises(RuntimeError, match="rollback failed"):
        async with uow:
            pass
    assert uow.state is UnitOfWorkState.CLOSED
    assert uow.outcome is UnitOfWorkOutcome.FAILED
    assert session.closed is True


@pytest.mark.asyncio
async def test_business_and_cleanup_errors_are_both_preserved() -> None:
    session = FakeSession(FakeTransaction(fail_rollback=True))
    uow = UnitOfWork(lambda: fake_factory(session))
    with pytest.raises(BaseExceptionGroup) as captured:
        async with uow:
            raise ValueError("business failed")
    messages = {str(error) for error in captured.value.exceptions}
    assert messages == {"business failed", "rollback failed"}
    assert session.closed is True
```

- [ ] **Step 2: Run the focused test and verify imports fail**

Run:

```bash
uv run pytest tests/unit/db/test_uow.py -v
```

Expected: FAIL because `labpilot.db.uow` does not exist.

- [ ] **Step 3: Implement the database factory and strict UoW state machine**

Create `src/labpilot/db/base.py`:

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

Create `src/labpilot/db/session.py`:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Database:
    def __init__(self, database_url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(
            database_url,
            pool_pre_ping=True,
        )
        self._session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        session = self._session_factory()
        try:
            yield session
        finally:
            await session.close()

    async def dispose(self) -> None:
        await self.engine.dispose()
```

Create `src/labpilot/db/uow.py`:

```python
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from enum import StrEnum
from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, AsyncTransaction


class UnitOfWorkError(RuntimeError):
    pass


class UnitOfWorkState(StrEnum):
    NEW = "new"
    ACTIVE = "active"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    CLOSED = "closed"


class UnitOfWorkOutcome(StrEnum):
    NONE = "none"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


SessionContextFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class UnitOfWork:
    def __init__(self, session_context_factory: SessionContextFactory) -> None:
        self._session_context_factory = session_context_factory
        self._session_context: AbstractAsyncContextManager[AsyncSession] | None = None
        self._session: AsyncSession | None = None
        self._transaction: AsyncTransaction | None = None
        self.state = UnitOfWorkState.NEW
        self.outcome = UnitOfWorkOutcome.NONE

    @property
    def session(self) -> AsyncSession:
        if self.state is not UnitOfWorkState.ACTIVE or self._session is None:
            raise UnitOfWorkError("UnitOfWork session is only available while active")
        return self._session

    async def __aenter__(self) -> "UnitOfWork":
        if self.state is not UnitOfWorkState.NEW:
            raise UnitOfWorkError("UnitOfWork cannot be entered more than once")
        self._session_context = self._session_context_factory()
        try:
            self._session = await self._session_context.__aenter__()
        except BaseException:
            self._clear_after_failed_enter()
            raise

        try:
            self._transaction = await self._session.begin()
        except BaseException as begin_error:
            cleanup_errors: list[BaseException] = []
            try:
                await self._session_context.__aexit__(
                    type(begin_error),
                    begin_error,
                    begin_error.__traceback__,
                )
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
            self._clear_after_failed_enter()
            if cleanup_errors:
                raise BaseExceptionGroup(
                    "UnitOfWork begin and cleanup failed",
                    [begin_error, *cleanup_errors],
                )
            raise

        self.state = UnitOfWorkState.ACTIVE
        return self

    def _clear_after_failed_enter(self) -> None:
        self._session_context = None
        self._session = None
        self._transaction = None
        self.state = UnitOfWorkState.CLOSED
        self.outcome = UnitOfWorkOutcome.FAILED

    async def commit(self) -> None:
        self._require_active()
        assert self._transaction is not None
        try:
            await self._transaction.commit()
        except BaseException:
            self.state = UnitOfWorkState.FAILED
            self.outcome = UnitOfWorkOutcome.FAILED
            raise
        self.state = UnitOfWorkState.COMMITTED
        self.outcome = UnitOfWorkOutcome.COMMITTED

    async def rollback(self) -> None:
        self._require_active()
        assert self._transaction is not None
        try:
            await self._transaction.rollback()
        except BaseException:
            self.state = UnitOfWorkState.FAILED
            self.outcome = UnitOfWorkOutcome.FAILED
            raise
        self.state = UnitOfWorkState.ROLLED_BACK
        self.outcome = UnitOfWorkOutcome.ROLLED_BACK

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        cleanup_errors: list[BaseException] = []
        try:
            if self.state in {UnitOfWorkState.ACTIVE, UnitOfWorkState.FAILED}:
                assert self._transaction is not None
                try:
                    await self._transaction.rollback()
                    if self.state is UnitOfWorkState.ACTIVE:
                        self.outcome = UnitOfWorkOutcome.ROLLED_BACK
                except BaseException as cleanup_error:
                    self.outcome = UnitOfWorkOutcome.FAILED
                    cleanup_errors.append(cleanup_error)
        finally:
            if self._session_context is not None:
                try:
                    await self._session_context.__aexit__(None, None, None)
                except BaseException as cleanup_error:
                    cleanup_errors.append(cleanup_error)
            self._session_context = None
            self._session = None
            self._transaction = None
            self.state = UnitOfWorkState.CLOSED

        if cleanup_errors:
            if exc is not None:
                raise BaseExceptionGroup(
                    "UnitOfWork body and cleanup failed",
                    [exc, *cleanup_errors],
                )
            if len(cleanup_errors) == 1:
                raise cleanup_errors[0]
            raise BaseExceptionGroup("UnitOfWork cleanup failed", cleanup_errors)
        return False

    def _require_active(self) -> None:
        if self.state is not UnitOfWorkState.ACTIVE:
            raise UnitOfWorkError(
                f"UnitOfWork must be active; current state is {self.state.value}"
            )
```

Create `src/labpilot/db/__init__.py`:

```python
"""Database infrastructure."""
```

- [ ] **Step 4: Add an isolated PostgreSQL fixture and session smoke test**

Create empty package markers `tests/__init__.py` and `tests/integration/__init__.py`, then create `tests/integration/conftest.py`:

```python
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import asyncpg
import pytest_asyncio
from sqlalchemy.engine import make_url


@pytest_asyncio.fixture
async def isolated_database_url() -> AsyncIterator[str]:
    admin_url = os.environ["LABPILOT_TEST_ADMIN_DATABASE_URL"]
    parsed = make_url(admin_url)
    database_name = f"labpilot_test_{uuid4().hex}"
    admin_dsn = parsed.render_as_string(hide_password=False).replace("+asyncpg", "")
    connection = await asyncpg.connect(admin_dsn)
    try:
        await connection.execute(f'CREATE DATABASE "{database_name}"')
    finally:
        await connection.close()

    test_url = parsed.set(database=database_name).render_as_string(hide_password=False)
    try:
        yield test_url
    finally:
        connection = await asyncpg.connect(admin_dsn)
        try:
            await connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                database_name,
            )
            await connection.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        finally:
            await connection.close()
```

Create `tests/integration/db/test_database_session.py`:

```python
import pytest
from sqlalchemy import text

from labpilot.db.session import Database
from labpilot.db.uow import UnitOfWork


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unit_of_work_commits_exactly_one_database_transaction(
    isolated_database_url: str,
) -> None:
    database = Database(isolated_database_url)
    try:
        async with UnitOfWork(database.session) as uow:
            value = await uow.session.scalar(text("SELECT 1"))
            assert value == 1
            await uow.commit()
    finally:
        await database.dispose()
```

- [ ] **Step 5: Run unit and PostgreSQL integration tests**

Run:

```bash
uv run pytest tests/unit/db/test_uow.py -v
LABPILOT_TEST_ADMIN_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres \
  uv run pytest tests/integration/db/test_database_session.py -v -m integration
```

Expected: all tests pass; no test drops the shared `public` schema.

- [ ] **Step 6: Run quality checks and commit**

```bash
uv run ruff check src tests
uv run mypy src

git add src/labpilot/db tests/unit/db tests/integration/conftest.py \
  tests/integration/db/test_database_session.py
git commit -m "feat: add strict PostgreSQL unit of work"
```

---

### Task 4: Add Alembic, Isolated Migration Tests, and Database Readiness

**Files:**
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/0001_initial.py`
- Create: `scripts/run_migrations.py`
- Create: `src/labpilot/db/readiness.py`
- Modify: `src/labpilot/main.py`
- Create: `tests/integration/db_helpers.py`
- Create: `tests/integration/db/test_migrations.py`
- Create: `tests/integration/api/test_readiness.py`

**Interfaces:**
- Consumes: `Database`, `Settings`, readiness protocol.
- Produces: unique Migration owner, advisory-locked `run_migrations.py`, `DatabaseReadinessProbe`.

- [ ] **Step 1: Write failing migration and readiness tests**

Create `tests/integration/db_helpers.py`:

```python
from alembic import command
from alembic.config import Config


def upgrade_database_to_head(database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option(
        "sqlalchemy.url",
        database_url.replace("+asyncpg", "+psycopg"),
    )
    command.upgrade(config, "head")
```

Create `tests/integration/db/test_migrations.py`:

```python
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.integration.db_helpers import upgrade_database_to_head


@pytest.mark.integration
@pytest.mark.destructive
async def test_upgrade_head_succeeds_on_an_isolated_empty_database(
    isolated_database_url: str,
) -> None:
    sync_url = isolated_database_url.replace("+asyncpg", "+psycopg")
    upgrade_database_to_head(isolated_database_url)

    engine = create_engine(sync_url)
    try:
        with engine.connect() as connection:
            version = connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert version == "0001_initial"
    finally:
        engine.dispose()

    assert Path("migrations/versions/0001_initial.py").exists()
```

Create `tests/integration/api/test_readiness.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from labpilot.api.app import create_app
from labpilot.db.readiness import DatabaseReadinessProbe
from labpilot.db.session import Database
from tests.integration.db_helpers import upgrade_database_to_head


async def get_readiness(database: Database) -> tuple[int, dict[str, object]]:
    app = create_app(
        readiness_probe=DatabaseReadinessProbe(database.engine, "alembic.ini")
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health/ready")
    return response.status_code, response.json()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_readiness_is_503_before_migration(
    isolated_database_url: str,
) -> None:
    database = Database(isolated_database_url)
    try:
        status_code, payload = await get_readiness(database)
        assert status_code == 503
        assert payload["migration"] == "behind"
    finally:
        await database.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_readiness_is_200_at_alembic_head(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        status_code, payload = await get_readiness(database)
        assert status_code == 200
        assert payload == {
            "status": "ready",
            "database": "ok",
            "migration": "head",
            "detail": None,
        }
    finally:
        await database.dispose()
```

- [ ] **Step 2: Run tests and verify migration/readiness modules are missing**

Run:

```bash
LABPILOT_TEST_ADMIN_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres \
  uv run pytest tests/integration/db/test_migrations.py \
    tests/integration/api/test_readiness.py -v -m integration
```

Expected: FAIL because Alembic configuration and `DatabaseReadinessProbe` do not exist.

- [ ] **Step 3: Add Alembic configuration and the initial migration**

Create `alembic.ini`:

```ini
[alembic]
script_location = migrations
prepend_sys_path = .
sqlalchemy.url = postgresql+psycopg://labpilot:labpilot@localhost:5432/labpilot

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

Create `migrations/env.py`:

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from labpilot.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

Create `migrations/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

Create `migrations/versions/0001_initial.py`:

```python
"""initialize LabPilot schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-24
"""

from collections.abc import Sequence

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
```

Add `psycopg` to the main `[project].dependencies` list in `pyproject.toml`, because the production Migration service imports it:

```toml
"psycopg[binary]>=3.2,<4",
```

- [ ] **Step 4: Add the database readiness probe**

Create `src/labpilot/db/readiness.py`:

```python
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from labpilot.common.readiness import ReadinessResult


class DatabaseReadinessProbe:
    def __init__(self, engine: AsyncEngine, alembic_config_path: str) -> None:
        self._engine = engine
        self._alembic_config_path = Path(alembic_config_path)

    async def check(self) -> ReadinessResult:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                current = await connection.run_sync(
                    lambda sync_connection: MigrationContext.configure(
                        sync_connection
                    ).get_current_revision()
                )
        except Exception as exc:
            return ReadinessResult(
                ready=False,
                database="unavailable",
                migration="unknown",
                detail=type(exc).__name__,
            )

        config = Config(str(self._alembic_config_path))
        heads = set(ScriptDirectory.from_config(config).get_heads())
        at_head = current is not None and current in heads and len(heads) == 1
        return ReadinessResult(
            ready=at_head,
            database="ok",
            migration="head" if at_head else "behind",
            detail=None if at_head else f"current={current}, heads={sorted(heads)}",
        )
```

Replace `src/labpilot/main.py` with:

```python
from labpilot.api.app import create_app
from labpilot.db.readiness import DatabaseReadinessProbe
from labpilot.db.session import Database
from labpilot.settings import get_settings

settings = get_settings()
database = Database(settings.database_url)
app = create_app(
    readiness_probe=DatabaseReadinessProbe(database.engine, "alembic.ini"),
    shutdown_callbacks=(database.dispose,),
)
```

- [ ] **Step 5: Add the advisory-locked migration runner**

Create `scripts/run_migrations.py`:

```python
import os

import psycopg
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

MIGRATION_LOCK_KEY = 0x4C414250


def main() -> None:
    async_url = os.environ["LABPILOT_DATABASE_URL"]
    sync_url = make_url(async_url.replace("+asyncpg", "+psycopg"))
    dsn = sync_url.render_as_string(hide_password=False)
    with psycopg.connect(dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        try:
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", dsn)
            command.upgrade(config, "head")
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run isolated migration and readiness tests**

Run:

```bash
LABPILOT_TEST_ADMIN_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres \
  uv run pytest tests/integration/db/test_migrations.py \
    tests/integration/api/test_readiness.py -v -m integration
```

Expected: all three tests pass: isolated empty-database migration, readiness `503` before migration, and readiness `200` at Alembic head. Each test receives a separately created database from `isolated_database_url`; no test shares or drops another test's schema.

- [ ] **Step 7: Run quality checks and commit**

```bash
uv run ruff check src tests scripts
uv run mypy src scripts

git add alembic.ini migrations scripts/run_migrations.py src/labpilot/db/readiness.py \
  src/labpilot/main.py tests/integration/db/test_migrations.py \
  tests/integration/api/test_readiness.py tests/integration/db_helpers.py pyproject.toml uv.lock
git commit -m "feat: add isolated migrations and readiness"
```

---

### Task 5: Implement Canonical JSON V1 and Namespaced Hashing

**Files:**
- Create: `src/labpilot/common/errors.py`
- Create: `src/labpilot/common/canonical_json.py`
- Create: `src/labpilot/common/hashing.py`
- Create: `tests/unit/common/test_canonical_json.py`
- Create: `tests/unit/common/test_hashing.py`

**Interfaces:**
- Consumes: Python standard library only.
- Produces: `canonical_json_bytes(value)`, `CanonicalizationLimitsV1`, `namespaced_sha256(namespace, value)`, `namespaced_sha256_bytes(namespace, canonical_payload)`.

- [ ] **Step 1: Write failing Canonical JSON and hashing tests**

Create `tests/unit/common/test_canonical_json.py`:

```python
from collections import UserDict
from decimal import Decimal

import pytest

import labpilot.common.canonical_json as canonical_json
from labpilot.common.canonical_json import (
    CanonicalizationError,
    CanonicalizationLimitsV1,
    canonical_json_bytes,
)


def test_object_keys_use_utf8_byte_order() -> None:
    assert canonical_json_bytes({"é": 1, "z": 2}) == b'{"z":2,"\xc3\xa9":1}'


def test_control_characters_are_json_escaped() -> None:
    assert canonical_json_bytes({"value": "a\nb\t"}) == b'{"value":"a\\nb\\t"}'


def test_lone_surrogate_is_rejected_with_stable_error() -> None:
    with pytest.raises(CanonicalizationError, match="surrogate"):
        canonical_json_bytes({"value": "\ud800"})


def test_semantically_equal_decimals_have_identical_bytes() -> None:
    assert canonical_json_bytes(Decimal("1.2300")) == b"1.23"
    assert canonical_json_bytes(Decimal("1.23")) == b"1.23"
    assert canonical_json_bytes(Decimal("-0")) == b"0"


@pytest.mark.parametrize("value", [1.5, (1, 2), {1, 2}, b"bytes", UserDict({"a": 1})])
def test_unsupported_types_are_rejected(value: object) -> None:
    with pytest.raises(CanonicalizationError, match="unsupported type"):
        canonical_json_bytes(value)


def test_excessive_decimal_exponent_is_rejected() -> None:
    with pytest.raises(CanonicalizationError, match="exponent"):
        canonical_json_bytes(Decimal("1E+10001"))


def test_excessive_depth_is_rejected() -> None:
    value: object = None
    for _ in range(66):
        value = [value]
    with pytest.raises(CanonicalizationError, match="depth"):
        canonical_json_bytes(value)


def test_output_budget_is_enforced_while_tokens_are_written(monkeypatch) -> None:
    monkeypatch.setattr(
        canonical_json,
        "_LIMITS",
        CanonicalizationLimitsV1(
            max_string_bytes=16,
            max_output_bytes=12,
        ),
    )
    with pytest.raises(CanonicalizationError, match="max_output_bytes"):
        canonical_json_bytes(["aaaa", "bbbb"])


def test_exact_output_budget_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(
        canonical_json,
        "_LIMITS",
        CanonicalizationLimitsV1(
            max_string_bytes=16,
            max_output_bytes=8,
        ),
    )
    assert canonical_json_bytes(["aaaa"]) == b'["aaaa"]'


def test_extreme_integer_uses_stable_canonicalization_error() -> None:
    with pytest.raises(CanonicalizationError, match="integer"):
        canonical_json_bytes(1 << 20_000)
```

Create `tests/unit/common/test_hashing.py`:

```python
import pytest

from labpilot.common.canonical_json import CanonicalizationError
from labpilot.common.hashing import namespaced_sha256, namespaced_sha256_bytes


def test_namespaced_hash_is_stable() -> None:
    value = {"a": 1}
    assert namespaced_sha256("labpilot-test:v1", value) == namespaced_sha256(
        "labpilot-test:v1",
        value,
    )


def test_namespaced_byte_hash_matches_value_hash() -> None:
    assert namespaced_sha256_bytes("labpilot-test:v1", b'{"a":1}') == namespaced_sha256(
        "labpilot-test:v1",
        {"a": 1},
    )


@pytest.mark.parametrize("namespace", ["", "line\nbreak", "control\x00", "x" * 129])
def test_invalid_namespace_is_rejected(namespace: str) -> None:
    with pytest.raises(CanonicalizationError, match="namespace"):
        namespaced_sha256(namespace, {"a": 1})
```

- [ ] **Step 2: Run tests and verify imports fail**

Run:

```bash
uv run pytest tests/unit/common/test_canonical_json.py \
  tests/unit/common/test_hashing.py -v
```

Expected: FAIL because common canonicalization modules do not exist.

- [ ] **Step 3: Implement incremental Canonical JSON V1**

Create `src/labpilot/common/errors.py`:

```python
class LabPilotError(Exception):
    """Base application error."""
```

Create `src/labpilot/common/canonical_json.py`:

```python
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TypeAlias


class CanonicalizationError(ValueError):
    pass


CanonicalScalar: TypeAlias = str | int | Decimal | bool | None
CanonicalValue: TypeAlias = CanonicalScalar | list["CanonicalValue"] | dict[
    str,
    "CanonicalValue",
]
CanonicalObject: TypeAlias = dict[str, CanonicalValue]


@dataclass(frozen=True, slots=True)
class CanonicalizationLimitsV1:
    max_depth: int = 64
    max_total_nodes: int = 100_000
    max_container_entries: int = 10_000
    max_string_bytes: int = 1_000_000
    max_output_bytes: int = 8_000_000
    max_integer_digits: int = 4_096
    max_decimal_digits: int = 1_000
    max_decimal_absolute_exponent: int = 10_000


_LIMITS = CanonicalizationLimitsV1()


@dataclass(slots=True)
class _ByteBudgetWriter:
    max_output_bytes: int
    buffer: bytearray = field(default_factory=bytearray)

    def append(self, chunk: bytes) -> None:
        if len(self.buffer) + len(chunk) > self.max_output_bytes:
            raise CanonicalizationError("canonical output exceeds max_output_bytes")
        self.buffer.extend(chunk)

    def finish(self) -> bytes:
        return bytes(self.buffer)


def canonical_json_bytes(value: CanonicalValue) -> bytes:
    writer = _ByteBudgetWriter(max_output_bytes=_LIMITS.max_output_bytes)
    state = _EncoderState(limits=_LIMITS, writer=writer)
    state.encode(value, depth=0)
    return writer.finish()


@dataclass(slots=True)
class _EncoderState:
    limits: CanonicalizationLimitsV1
    writer: _ByteBudgetWriter
    nodes: int = 0

    def encode(self, value: CanonicalValue, *, depth: int) -> None:
        self.nodes += 1
        if self.nodes > self.limits.max_total_nodes:
            raise CanonicalizationError("canonical value exceeds max_total_nodes")
        if depth > self.limits.max_depth:
            raise CanonicalizationError("canonical value exceeds max_depth")

        if value is None:
            self.writer.append(b"null")
            return
        if type(value) is bool:
            self.writer.append(b"true" if value else b"false")
            return
        if type(value) is int:
            self.writer.append(self._encode_integer(value))
            return
        if type(value) is Decimal:
            self.writer.append(self._encode_decimal(value).encode("ascii"))
            return
        if type(value) is str:
            self.writer.append(self._encode_string(value))
            return
        if type(value) is list:
            self._encode_list(value, depth=depth)
            return
        if type(value) is dict:
            self._encode_object(value, depth=depth)
            return
        raise CanonicalizationError(f"unsupported type: {type(value).__name__}")

    def _encode_integer(self, value: int) -> bytes:
        bit_length = abs(value).bit_length()
        estimated_digits = 1 if bit_length == 0 else (bit_length * 30_103) // 100_000 + 1
        if estimated_digits > self.limits.max_integer_digits:
            raise CanonicalizationError("integer exceeds max_integer_digits")
        try:
            rendered = str(value)
        except ValueError as exc:
            raise CanonicalizationError("integer conversion exceeds runtime limit") from exc
        if len(rendered.lstrip("-")) > self.limits.max_integer_digits:
            raise CanonicalizationError("integer exceeds max_integer_digits")
        return rendered.encode("ascii")

    def _encode_string(self, value: str) -> bytes:
        try:
            raw = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise CanonicalizationError("string contains an isolated surrogate") from exc
        if len(raw) > self.limits.max_string_bytes:
            raise CanonicalizationError("string exceeds max_string_bytes")
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except UnicodeEncodeError as exc:
            raise CanonicalizationError("string contains an isolated surrogate") from exc

    def _encode_list(self, value: list[CanonicalValue], *, depth: int) -> None:
        if len(value) > self.limits.max_container_entries:
            raise CanonicalizationError("list exceeds max_container_entries")
        self.writer.append(b"[")
        for index, item in enumerate(value):
            if index:
                self.writer.append(b",")
            self.encode(item, depth=depth + 1)
        self.writer.append(b"]")

    def _encode_object(
        self,
        value: dict[str, CanonicalValue],
        *,
        depth: int,
    ) -> None:
        if len(value) > self.limits.max_container_entries:
            raise CanonicalizationError("object exceeds max_container_entries")
        for key in value:
            if type(key) is not str:
                raise CanonicalizationError("object keys must be strings")
        try:
            keys = sorted(value, key=lambda key: key.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise CanonicalizationError("string contains an isolated surrogate") from exc
        self.writer.append(b"{")
        for index, key in enumerate(keys):
            if index:
                self.writer.append(b",")
            self.writer.append(self._encode_string(key))
            self.writer.append(b":")
            self.encode(value[key], depth=depth + 1)
        self.writer.append(b"}")

    def _encode_decimal(self, value: Decimal) -> str:
        if not value.is_finite():
            raise CanonicalizationError("Decimal must be finite")
        if value.is_zero():
            return "0"
        _, digits, exponent = value.as_tuple()
        if len(digits) > self.limits.max_decimal_digits:
            raise CanonicalizationError("Decimal exceeds max_decimal_digits")
        if abs(exponent) > self.limits.max_decimal_absolute_exponent:
            raise CanonicalizationError("Decimal exponent exceeds limit")
        rendered = format(value, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return "0" if rendered == "-0" else rendered
```

Create `src/labpilot/common/hashing.py`:

```python
import re
from hashlib import sha256

from labpilot.common.canonical_json import (
    CanonicalValue,
    CanonicalizationError,
    canonical_json_bytes,
)

_NAMESPACE_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _namespace_prefix(namespace: str) -> bytes:
    if _NAMESPACE_PATTERN.fullmatch(namespace) is None:
        raise CanonicalizationError(
            "namespace must be 1-128 restricted ASCII characters"
        )
    return namespace.encode("ascii") + b"\n"


def namespaced_sha256_bytes(namespace: str, canonical_payload: bytes) -> str:
    return sha256(_namespace_prefix(namespace) + canonical_payload).hexdigest()


def namespaced_sha256(namespace: str, value: CanonicalValue) -> str:
    return namespaced_sha256_bytes(namespace, canonical_json_bytes(value))
```

- [ ] **Step 4: Run unit tests and add conformance vectors**

Run:

```bash
uv run pytest tests/unit/common/test_canonical_json.py \
  tests/unit/common/test_hashing.py -v
```

Expected: all tests pass.

Add this parameterized conformance test, then rerun:

```python
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, b"null"),
        (True, b"true"),
        (123, b"123"),
        (Decimal("100.000"), b"100"),
        ({"b": 1, "a": 2}, b'{"a":2,"b":1}'),
        (["x", Decimal("0.10")], b'["x",0.1]'),
    ],
)
def test_canonical_json_v1_conformance_vectors(value: object, expected: bytes) -> None:
    assert canonical_json_bytes(value) == expected
```

- [ ] **Step 5: Run quality checks and commit**

```bash
uv run ruff check src tests
uv run mypy src

git add src/labpilot/common tests/unit/common
git commit -m "feat: implement Canonical JSON V1"
```

---

### Task 6: Add the Complete RuntimeEvent and Outbox Foundation

**Files:**
- Create: `src/labpilot/observability/__init__.py`
- Create: `src/labpilot/observability/models.py`
- Create: `src/labpilot/observability/payloads.py`
- Create: `src/labpilot/observability/events.py`
- Create: `src/labpilot/observability/outbox.py`
- Create: `migrations/versions/0002_runtime_events_outbox.py`
- Create: `tests/unit/observability/test_event_payloads.py`
- Modify: `tests/integration/db/test_migrations.py`
- Create: `tests/integration/observability/test_event_outbox.py`
- Create: `tests/integration/observability/test_event_sequence_concurrency.py`
- Create: `tests/integration/observability/test_outbox_fencing.py`

**Interfaces:**
- Consumes: strict `UnitOfWork`, Canonical JSON V1, PostgreSQL.
- Produces: schema-registered `prepare_runtime_event_payload()`, complete `RuntimeEventRow`, `OutboxRecordRow`, atomic `append_runtime_event()`, fenced Outbox claim/publish functions.

- [ ] **Step 1: Write failing payload safety, event, atomicity, concurrency, and fencing tests**

Create `tests/unit/observability/test_event_payloads.py`:

```python
import json
from decimal import Decimal

import pytest

from labpilot.common.canonical_json import CanonicalizationError
from labpilot.observability.payloads import prepare_runtime_event_payload


def test_foundation_payload_is_copied_canonicalized_and_hashed() -> None:
    source = {"aggregate_id": "probe-1", "value": 1}
    prepared = prepare_runtime_event_payload(
        event_type="foundation.probe.created",
        event_schema_version=1,
        payload=source,
    )
    source["password"] = "mutated after validation"
    assert json.loads(prepared.canonical_bytes) == {
        "aggregate_id": "probe-1",
        "value": 1,
    }
    assert prepared.event_type == "foundation.probe.created"
    assert prepared.event_schema_version == 1
    assert len(prepared.payload_hash) == 64
    assert prepared.canonical_size_bytes == len(prepared.canonical_bytes)


@pytest.mark.parametrize(
    "secret_key",
    ["password", "apiKey", "access-token", "Authorization", "private_key"],
)
def test_secret_like_keys_are_rejected_recursively(secret_key: str) -> None:
    with pytest.raises(CanonicalizationError, match="secret-like field"):
        prepare_runtime_event_payload(
            event_type="foundation.probe.created",
            event_schema_version=1,
            payload={"aggregate_id": "probe-1", "nested": {secret_key: "value"}},
        )


def test_unknown_event_schema_is_rejected() -> None:
    with pytest.raises(CanonicalizationError, match="unregistered event payload schema"):
        prepare_runtime_event_payload(
            event_type="unknown.event",
            event_schema_version=1,
            payload={},
        )


def test_large_runtime_event_string_is_rejected() -> None:
    with pytest.raises(CanonicalizationError, match="event string exceeds"):
        prepare_runtime_event_payload(
            event_type="foundation.probe.created",
            event_schema_version=1,
            payload={"aggregate_id": "x" * 2049},
        )


def test_decimal_is_rejected_at_event_json_boundary() -> None:
    with pytest.raises(CanonicalizationError, match="JSON-native"):
        prepare_runtime_event_payload(
            event_type="foundation.probe.created",
            event_schema_version=1,
            payload={"aggregate_id": "probe-1", "value": Decimal("1.2")},
        )
```

Modify `tests/integration/db/test_migrations.py`:

```python
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.integration.db_helpers import upgrade_database_to_head


@pytest.mark.integration
@pytest.mark.destructive
async def test_upgrade_head_succeeds_on_an_isolated_empty_database(
    isolated_database_url: str,
) -> None:
    sync_url = isolated_database_url.replace("+asyncpg", "+psycopg")
    upgrade_database_to_head(isolated_database_url)

    engine = create_engine(sync_url)
    try:
        with engine.connect() as connection:
            version = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
        assert version == "0002_runtime_events_outbox"
    finally:
        engine.dispose()

    assert Path("migrations/versions/0001_initial.py").exists()
    assert Path(
        "migrations/versions/0002_runtime_events_outbox.py"
    ).exists()
```

Create `tests/integration/observability/test_event_outbox.py`:

```python
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from labpilot.common.canonical_json import CanonicalizationError
from labpilot.common.hashing import namespaced_sha256
from labpilot.db.session import Database
from labpilot.db.uow import UnitOfWork
from labpilot.observability.events import RuntimeEventEnvelope, append_runtime_event
from labpilot.observability.models import OutboxRecordRow, RuntimeEventRow
from labpilot.observability.payloads import prepare_runtime_event_payload
from labpilot.observability.outbox import claim_outbox_records
from tests.integration.db_helpers import upgrade_database_to_head


@pytest.mark.integration
@pytest.mark.asyncio
async def test_event_and_outbox_are_committed_atomically(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    occurred_at = datetime.now(UTC) - timedelta(seconds=1)
    try:
        async with UnitOfWork(database.session) as uow:
            event = await append_runtime_event(
                uow,
                RuntimeEventEnvelope(
                    project_id=uuid4(),
                    aggregate_type="foundation_probe",
                    aggregate_id="probe-1",
                    event_type="foundation.probe.created",
                    event_schema_version=1,
                    payload=prepare_runtime_event_payload(
                        event_type="foundation.probe.created",
                        event_schema_version=1,
                        payload={"aggregate_id": "probe-1", "value": 1},
                    ),
                    occurred_at=occurred_at,
                    interaction_correlation_id="interaction-1",
                    workflow_correlation_id=None,
                    causation_id=None,
                    actor_type="system",
                    actor_id=None,
                    visibility_class="internal",
                    topic="runtime-events",
                    partition_key="foundation_probe:probe-1",
                ),
            )
            await uow.commit()

        async with database.session() as session:
            stored_event = await session.scalar(
                select(RuntimeEventRow).where(RuntimeEventRow.event_id == event.event_id)
            )
            stored_outbox = await session.scalar(
                select(OutboxRecordRow).where(OutboxRecordRow.event_id == event.event_id)
            )
        assert stored_event is not None
        assert stored_outbox is not None
        assert stored_event.payload_hash == namespaced_sha256(
            "labpilot-runtime-event-payload:v1",
            {"aggregate_id": "probe-1", "value": 1},
        )
        assert stored_event.occurred_at == occurred_at
        assert stored_event.recorded_at > occurred_at
        assert stored_event.created_at is not None
        assert stored_event.project_id is not None
        assert stored_outbox.event_id == stored_event.event_id
    finally:
        await database.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_prepared_payload_cannot_be_used_for_a_different_schema(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    envelope = RuntimeEventEnvelope.for_test("schema-mismatch")
    mismatched = RuntimeEventEnvelope(
        project_id=envelope.project_id,
        aggregate_type=envelope.aggregate_type,
        aggregate_id=envelope.aggregate_id,
        event_type="another.event",
        event_schema_version=99,
        payload=envelope.payload,
        occurred_at=envelope.occurred_at,
        interaction_correlation_id=None,
        workflow_correlation_id=None,
        causation_id=None,
        actor_type="system",
        actor_id=None,
        visibility_class="internal",
        topic="runtime-events",
        partition_key="foundation_probe:schema-mismatch",
    )
    try:
        with pytest.raises(CanonicalizationError, match="event_type mismatch"):
            async with UnitOfWork(database.session) as uow:
                await append_runtime_event(uow, mismatched)
    finally:
        await database.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rollback_persists_neither_event_nor_outbox(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    event_id = None
    try:
        with pytest.raises(RuntimeError, match="force rollback"):
            async with UnitOfWork(database.session) as uow:
                event = await append_runtime_event(
                    uow,
                    RuntimeEventEnvelope.for_test("probe-rollback"),
                )
                event_id = event.event_id
                raise RuntimeError("force rollback")

        assert event_id is not None
        async with database.session() as session:
            assert await session.scalar(
                select(RuntimeEventRow).where(RuntimeEventRow.event_id == event_id)
            ) is None
            assert await session.scalar(
                select(OutboxRecordRow).where(OutboxRecordRow.event_id == event_id)
            ) is None
    finally:
        await database.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_outbox_redelivery_reuses_the_existing_event_id(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        async with UnitOfWork(database.session) as uow:
            event = await append_runtime_event(
                uow,
                RuntimeEventEnvelope.for_test("probe-redelivery"),
            )
            await uow.commit()

        async with UnitOfWork(database.session) as uow:
            first = await claim_outbox_records(
                uow,
                owner="publisher-1",
                limit=1,
                lease_seconds=0,
            )
            await uow.commit()
        async with UnitOfWork(database.session) as uow:
            second = await claim_outbox_records(
                uow,
                owner="publisher-2",
                limit=1,
                lease_seconds=30,
            )
            await uow.commit()

        assert first[0].event_id == event.event_id
        assert second[0].event_id == event.event_id
    finally:
        await database.dispose()
```

Create `tests/integration/observability/test_event_sequence_concurrency.py`:

```python
import asyncio

import pytest

from labpilot.db.session import Database
from labpilot.db.uow import UnitOfWork
from labpilot.observability.events import RuntimeEventEnvelope, append_runtime_event
from tests.integration.db_helpers import upgrade_database_to_head


async def append_one(database: Database, aggregate_id: str) -> int:
    async with UnitOfWork(database.session) as uow:
        event = await append_runtime_event(
            uow,
            RuntimeEventEnvelope.for_test(aggregate_id),
        )
        sequence = event.aggregate_sequence
        await uow.commit()
        return sequence


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_sequence_allocation_is_dense_and_unique(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        sequences = await asyncio.gather(
            *(append_one(database, "shared") for _ in range(20))
        )
        assert sorted(sequences) == list(range(1, 21))
    finally:
        await database.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rolled_back_sequence_is_not_permanently_consumed(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        async with UnitOfWork(database.session) as uow:
            first = await append_runtime_event(
                uow,
                RuntimeEventEnvelope.for_test("rollback-sequence"),
            )
            assert first.aggregate_sequence == 1

        committed = await append_one(database, "rollback-sequence")
        assert committed == 1
    finally:
        await database.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_different_aggregates_have_independent_sequences(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        assert await append_one(database, "aggregate-a") == 1
        assert await append_one(database, "aggregate-b") == 1
        assert await append_one(database, "aggregate-a") == 2
    finally:
        await database.dispose()
```

Create `tests/integration/observability/test_outbox_fencing.py`:

```python
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from labpilot.db.session import Database
from labpilot.db.uow import UnitOfWork
from labpilot.observability.events import RuntimeEventEnvelope, append_runtime_event
from labpilot.observability.models import OutboxRecordRow
from labpilot.observability.outbox import claim_outbox_records, mark_outbox_published
from tests.integration.db_helpers import upgrade_database_to_head


@pytest.mark.integration
@pytest.mark.asyncio
async def test_claim_lease_uses_postgresql_clock(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        async with UnitOfWork(database.session) as uow:
            await append_runtime_event(
                uow,
                RuntimeEventEnvelope.for_test("database-clock"),
            )
            await uow.commit()

        async with UnitOfWork(database.session) as uow:
            db_before = await uow.session.scalar(select(func.clock_timestamp()))
            claimed = (
                await claim_outbox_records(
                    uow,
                    owner="clock-worker",
                    limit=1,
                    lease_seconds=30,
                )
            )[0]
            assert db_before is not None
            assert claimed.leased_until is not None
            assert claimed.leased_until >= db_before + timedelta(seconds=30)
            await uow.commit()
    finally:
        await database.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stale_outbox_lease_generation_cannot_publish(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        async with UnitOfWork(database.session) as uow:
            event = await append_runtime_event(
                uow,
                RuntimeEventEnvelope.for_test("fencing"),
            )
            await uow.commit()

        async with UnitOfWork(database.session) as uow:
            old_claim = (
                await claim_outbox_records(
                    uow,
                    owner="old-worker",
                    limit=1,
                    lease_seconds=0,
                )
            )[0]
            old_generation = old_claim.lease_generation
            outbox_id = old_claim.outbox_id
            await uow.commit()

        async with UnitOfWork(database.session) as uow:
            new_claim = (
                await claim_outbox_records(
                    uow,
                    owner="new-worker",
                    limit=1,
                    lease_seconds=30,
                )
            )[0]
            assert new_claim.lease_generation == old_generation + 1
            await uow.commit()

        async with UnitOfWork(database.session) as uow:
            published = await mark_outbox_published(
                uow,
                outbox_id=outbox_id,
                owner="old-worker",
                lease_generation=old_generation,
            )
            assert published is False
            await uow.commit()

        async with database.session() as session:
            row = await session.scalar(
                select(OutboxRecordRow).where(OutboxRecordRow.event_id == event.event_id)
            )
        assert row is not None
        assert row.status == "pending"
        assert row.lease_owner == "new-worker"
    finally:
        await database.dispose()
```

- [ ] **Step 2: Run focused tests and verify the RED state**

Run:

```bash
LABPILOT_TEST_ADMIN_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres \
  uv run pytest \
  tests/integration/db/test_migrations.py \
  tests/unit/observability/test_event_payloads.py \
  tests/integration/observability \
  -v
```

Expected: FAIL because the observability module does not exist,
`0002_runtime_events_outbox` does not exist, and the current migration head
is still `0001_initial`. The migration smoke test must fail in this RED stage
because the target revision/file does not yet exist.

- [ ] **Step 3: Implement the complete persistence envelope**

Create `src/labpilot/observability/models.py` with these exact mapped fields:

```python
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from labpilot.db.base import Base


class AggregateEventCursorRow(Base):
    __tablename__ = "aggregate_event_cursors"

    aggregate_type: Mapped[str] = mapped_column(String(100), primary_key=True)
    aggregate_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)


class RuntimeEventRow(Base):
    __tablename__ = "runtime_events"
    __table_args__ = (
        UniqueConstraint(
            "aggregate_type",
            "aggregate_id",
            "aggregate_sequence",
            name="uq_runtime_event_aggregate_sequence",
        ),
        Index("ix_runtime_event_interaction_correlation", "interaction_correlation_id"),
        Index("ix_runtime_event_workflow_correlation", "workflow_correlation_id"),
    )

    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(200), nullable=False)
    aggregate_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(200), nullable=False)
    event_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    interaction_correlation_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    workflow_correlation_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    causation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    visibility_class: Mapped[str] = mapped_column(String(50), nullable=False)


class OutboxRecordRow(Base):
    __tablename__ = "outbox_records"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_outbox_event_id"),
        Index("ix_outbox_claim", "status", "available_at"),
    )

    outbox_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runtime_events.event_id"), nullable=False
    )
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    partition_key: Mapped[str] = mapped_column(String(300), nullable=False)
    payload_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Create `src/labpilot/observability/payloads.py`:

```python
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeAlias

from labpilot.common.canonical_json import (
    CanonicalizationError,
    canonical_json_bytes,
)
from labpilot.common.hashing import namespaced_sha256_bytes

EventJsonScalar: TypeAlias = str | int | bool | None
EventJsonValue: TypeAlias = EventJsonScalar | list["EventJsonValue"] | dict[
    str,
    "EventJsonValue",
]
EventJsonObject: TypeAlias = dict[str, EventJsonValue]
EventPayloadValidator = Callable[[EventJsonObject], None]
_MAX_EVENT_PAYLOAD_BYTES = 16 * 1024
_MAX_EVENT_STRING_BYTES = 2 * 1024
_SECRET_FIELD_NAMES = {
    "password",
    "passwd",
    "secret",
    "token",
    "accesstoken",
    "refreshtoken",
    "apikey",
    "authorization",
    "cookie",
    "setcookie",
    "credential",
    "privatekey",
    "accesskey",
}
_SCHEMA_VALIDATORS: dict[tuple[str, int], EventPayloadValidator] = {}
_PREPARED_PAYLOAD_SEAL = object()


@dataclass(frozen=True, slots=True)
class PreparedRuntimeEventPayload:
    event_type: str
    event_schema_version: int
    canonical_bytes: bytes
    payload_hash: str
    canonical_size_bytes: int
    _seal: object = field(repr=False, compare=False)

    def assert_integrity(self) -> None:
        if self._seal is not _PREPARED_PAYLOAD_SEAL:
            raise CanonicalizationError("runtime event payload was not prepared")
        if len(self.canonical_bytes) != self.canonical_size_bytes:
            raise CanonicalizationError("runtime event payload size mismatch")
        expected = namespaced_sha256_bytes(
            "labpilot-runtime-event-payload:v1",
            self.canonical_bytes,
        )
        if expected != self.payload_hash:
            raise CanonicalizationError("runtime event payload hash mismatch")

    def persisted_json(self) -> EventJsonObject:
        self.assert_integrity()
        decoded = json.loads(self.canonical_bytes)
        if type(decoded) is not dict:
            raise CanonicalizationError("runtime event payload must decode to an object")
        return decoded


def register_runtime_event_payload_schema(
    event_type: str,
    event_schema_version: int,
    validator: EventPayloadValidator,
) -> None:
    key = (event_type, event_schema_version)
    if key in _SCHEMA_VALIDATORS:
        raise RuntimeError(f"event payload schema already registered: {key}")
    _SCHEMA_VALIDATORS[key] = validator


def prepare_runtime_event_payload(
    *,
    event_type: str,
    event_schema_version: int,
    payload: EventJsonObject,
) -> PreparedRuntimeEventPayload:
    validator = _SCHEMA_VALIDATORS.get((event_type, event_schema_version))
    if validator is None:
        raise CanonicalizationError("unregistered event payload schema")
    _scan_payload(payload)
    validator(payload)
    encoded = canonical_json_bytes(payload)
    if len(encoded) > _MAX_EVENT_PAYLOAD_BYTES:
        raise CanonicalizationError("runtime event payload exceeds 16 KiB")
    return PreparedRuntimeEventPayload(
        event_type=event_type,
        event_schema_version=event_schema_version,
        canonical_bytes=bytes(encoded),
        payload_hash=namespaced_sha256_bytes(
            "labpilot-runtime-event-payload:v1",
            encoded,
        ),
        canonical_size_bytes=len(encoded),
        _seal=_PREPARED_PAYLOAD_SEAL,
    )


def _scan_payload(value: object) -> None:
    if value is None or type(value) in {bool, int}:
        return
    if type(value) is str:
        if len(value.encode("utf-8")) > _MAX_EVENT_STRING_BYTES:
            raise CanonicalizationError("event string exceeds 2 KiB")
        return
    if type(value) is list:
        for item in value:
            _scan_payload(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise CanonicalizationError("event object keys must be strings")
            normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
            if normalized in _SECRET_FIELD_NAMES:
                raise CanonicalizationError(f"secret-like field is forbidden: {key}")
            _scan_payload(item)
        return
    raise CanonicalizationError(
        f"runtime event payload must use JSON-native values: {type(value).__name__}"
    )


def _validate_foundation_probe(payload: EventJsonObject) -> None:
    allowed = {"aggregate_id", "value", "nested"}
    unexpected = set(payload) - allowed
    if unexpected:
        raise CanonicalizationError(
            f"foundation probe contains unexpected fields: {sorted(unexpected)}"
        )
    if type(payload.get("aggregate_id")) is not str:
        raise CanonicalizationError("foundation probe requires aggregate_id string")
    if "value" in payload and type(payload["value"]) is not int:
        raise CanonicalizationError("foundation probe value must be an integer")


register_runtime_event_payload_schema(
    "foundation.probe.created",
    1,
    _validate_foundation_probe,
)
```

Create `src/labpilot/observability/events.py` with:

```python
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from labpilot.common.canonical_json import CanonicalizationError
from labpilot.db.uow import UnitOfWork
from labpilot.observability.models import (
    AggregateEventCursorRow,
    OutboxRecordRow,
    RuntimeEventRow,
)
from labpilot.observability.payloads import (
    PreparedRuntimeEventPayload,
    prepare_runtime_event_payload,
)

_FOUNDATION_PROJECT_ID = UUID("00000000-0000-0000-0000-000000000001")


@dataclass(frozen=True, slots=True)
class RuntimeEventEnvelope:
    project_id: UUID
    aggregate_type: str
    aggregate_id: str
    event_type: str
    event_schema_version: int
    payload: PreparedRuntimeEventPayload
    occurred_at: datetime | None
    interaction_correlation_id: str | None
    workflow_correlation_id: str | None
    causation_id: UUID | None
    actor_type: str
    actor_id: str | None
    visibility_class: str
    topic: str
    partition_key: str

    @classmethod
    def for_test(cls, aggregate_id: str) -> "RuntimeEventEnvelope":
        return cls(
            project_id=_FOUNDATION_PROJECT_ID,
            aggregate_type="foundation_probe",
            aggregate_id=aggregate_id,
            event_type="foundation.probe.created",
            event_schema_version=1,
            payload=prepare_runtime_event_payload(
                event_type="foundation.probe.created",
                event_schema_version=1,
                payload={"aggregate_id": aggregate_id},
            ),
            occurred_at=None,
            interaction_correlation_id=None,
            workflow_correlation_id=None,
            causation_id=None,
            actor_type="system",
            actor_id=None,
            visibility_class="internal",
            topic="runtime-events",
            partition_key=f"foundation_probe:{aggregate_id}",
        )


async def _allocate_sequence(
    uow: UnitOfWork,
    aggregate_type: str,
    aggregate_id: str,
) -> int:
    await uow.session.execute(
        insert(AggregateEventCursorRow)
        .values(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            last_sequence=0,
        )
        .on_conflict_do_nothing(
            index_elements=[
                AggregateEventCursorRow.aggregate_type,
                AggregateEventCursorRow.aggregate_id,
            ]
        )
    )
    cursor = await uow.session.scalar(
        select(AggregateEventCursorRow)
        .where(
            AggregateEventCursorRow.aggregate_type == aggregate_type,
            AggregateEventCursorRow.aggregate_id == aggregate_id,
        )
        .with_for_update()
    )
    assert cursor is not None
    cursor.last_sequence += 1
    await uow.session.flush()
    return cursor.last_sequence


async def append_runtime_event(
    uow: UnitOfWork,
    envelope: RuntimeEventEnvelope,
) -> RuntimeEventRow:
    if envelope.payload.event_type != envelope.event_type:
        raise CanonicalizationError("prepared payload event_type mismatch")
    if envelope.payload.event_schema_version != envelope.event_schema_version:
        raise CanonicalizationError("prepared payload schema version mismatch")
    payload_json = envelope.payload.persisted_json()
    db_now = await uow.session.scalar(select(func.clock_timestamp()))
    assert db_now is not None
    sequence = await _allocate_sequence(
        uow,
        envelope.aggregate_type,
        envelope.aggregate_id,
    )
    event_id = uuid4()
    event = RuntimeEventRow(
        event_id=event_id,
        project_id=envelope.project_id,
        aggregate_type=envelope.aggregate_type,
        aggregate_id=envelope.aggregate_id,
        aggregate_sequence=sequence,
        event_type=envelope.event_type,
        event_schema_version=envelope.event_schema_version,
        payload_json=payload_json,
        payload_hash=envelope.payload.payload_hash,
        occurred_at=envelope.occurred_at or db_now,
        interaction_correlation_id=envelope.interaction_correlation_id,
        workflow_correlation_id=envelope.workflow_correlation_id,
        causation_id=envelope.causation_id,
        actor_type=envelope.actor_type,
        actor_id=envelope.actor_id,
        visibility_class=envelope.visibility_class,
    )
    outbox = OutboxRecordRow(
        outbox_id=uuid4(),
        event_id=event_id,
        topic=envelope.topic,
        partition_key=envelope.partition_key,
        payload_ref=f"runtime-event:{event_id}",
        status="pending",
        available_at=db_now,
        attempt_count=0,
        lease_generation=0,
    )
    uow.session.add_all([event, outbox])
    await uow.session.flush()
    return event
```

Create `src/labpilot/observability/outbox.py` with:

```python
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select, update

from labpilot.db.uow import UnitOfWork
from labpilot.observability.models import OutboxRecordRow


async def claim_outbox_records(
    uow: UnitOfWork,
    *,
    owner: str,
    limit: int,
    lease_seconds: int,
) -> list[OutboxRecordRow]:
    db_now = await uow.session.scalar(select(func.clock_timestamp()))
    assert db_now is not None
    rows = list(
        (
            await uow.session.scalars(
                select(OutboxRecordRow)
                .where(
                    OutboxRecordRow.status == "pending",
                    OutboxRecordRow.available_at <= db_now,
                    (
                        OutboxRecordRow.leased_until.is_(None)
                        | (OutboxRecordRow.leased_until <= db_now)
                    ),
                )
                .order_by(OutboxRecordRow.available_at, OutboxRecordRow.outbox_id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    for row in rows:
        row.lease_owner = owner
        row.lease_generation += 1
        row.leased_until = db_now + timedelta(seconds=lease_seconds)
        row.attempt_count += 1
    await uow.session.flush()
    return rows


async def mark_outbox_published(
    uow: UnitOfWork,
    *,
    outbox_id: UUID,
    owner: str,
    lease_generation: int,
) -> bool:
    db_now = await uow.session.scalar(select(func.clock_timestamp()))
    assert db_now is not None
    result = await uow.session.execute(
        update(OutboxRecordRow)
        .where(
            OutboxRecordRow.outbox_id == outbox_id,
            OutboxRecordRow.status == "pending",
            OutboxRecordRow.lease_owner == owner,
            OutboxRecordRow.lease_generation == lease_generation,
            OutboxRecordRow.leased_until > db_now,
        )
        .values(status="published", published_at=db_now)
    )
    return result.rowcount == 1
```

Create the package marker `src/labpilot/observability/__init__.py`.

- [ ] **Step 4: Add migration `0002_runtime_events_outbox`**

In `migrations/env.py`, add this import before `target_metadata = Base.metadata`:

```python
import labpilot.observability.models  # noqa: F401
```

Create `migrations/versions/0002_runtime_events_outbox.py`:

```python
"""add RuntimeEvent and Outbox foundation

Revision ID: 0002_runtime_events_outbox
Revises: 0001_initial
Create Date: 2026-07-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_runtime_events_outbox"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aggregate_event_cursors",
        sa.Column("aggregate_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", sa.String(length=200), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "aggregate_type",
            "aggregate_id",
            name="pk_aggregate_event_cursors",
        ),
    )

    op.create_table(
        "runtime_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", sa.String(length=200), nullable=False),
        sa.Column("aggregate_sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=200), nullable=False),
        sa.Column("event_schema_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("interaction_correlation_id", sa.String(length=200), nullable=True),
        sa.Column("workflow_correlation_id", sa.String(length=200), nullable=True),
        sa.Column("causation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_type", sa.String(length=50), nullable=False),
        sa.Column("actor_id", sa.String(length=200), nullable=True),
        sa.Column("visibility_class", sa.String(length=50), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name="pk_runtime_events"),
        sa.UniqueConstraint(
            "aggregate_type",
            "aggregate_id",
            "aggregate_sequence",
            name="uq_runtime_event_aggregate_sequence",
        ),
    )
    op.create_index(
        "ix_runtime_event_interaction_correlation",
        "runtime_events",
        ["interaction_correlation_id"],
        unique=False,
    )
    op.create_index(
        "ix_runtime_event_workflow_correlation",
        "runtime_events",
        ["workflow_correlation_id"],
        unique=False,
    )

    op.create_table(
        "outbox_records",
        sa.Column("outbox_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.String(length=200), nullable=False),
        sa.Column("partition_key", sa.String(length=300), nullable=False),
        sa.Column("payload_ref", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=200), nullable=True),
        sa.Column("lease_generation", sa.BigInteger(), nullable=False),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["runtime_events.event_id"],
            name="fk_outbox_event_id_runtime_events",
        ),
        sa.PrimaryKeyConstraint("outbox_id", name="pk_outbox_records"),
        sa.UniqueConstraint("event_id", name="uq_outbox_event_id"),
    )
    op.create_index(
        "ix_outbox_claim",
        "outbox_records",
        ["status", "available_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_claim", table_name="outbox_records")
    op.drop_table("outbox_records")
    op.drop_index("ix_runtime_event_workflow_correlation", table_name="runtime_events")
    op.drop_index("ix_runtime_event_interaction_correlation", table_name="runtime_events")
    op.drop_table("runtime_events")
    op.drop_table("aggregate_event_cursors")
```

- [ ] **Step 5: Run payload and event/outbox tests**

Run:

```bash
uv run alembic heads
uv run pytest tests/unit/observability/test_event_payloads.py -v
LABPILOT_TEST_ADMIN_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres \
  uv run pytest tests/integration/observability -v -m integration
LABPILOT_TEST_ADMIN_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres \
  uv run pytest tests/integration -v -m integration
```

Expected:

```text
0002_runtime_events_outbox (head)
payload schema, secret-field, and size-limit tests pass
atomic commit/rollback tests pass
20 concurrent events produce sequences 1..20
rolled-back allocation leaves no committed gap
separate aggregate sequences are independent
stale lease_generation cannot publish
all integration tests pass
Task 4 migration smoke test passes under the new head
```

The Alembic output must contain only `0002_runtime_events_outbox (head)`.
Do not defer any integration failure to Task 9 or Task 10.

- [ ] **Step 6: Run migrations, quality checks, and commit**

```bash
uv run ruff check src tests
uv run mypy src

git add src/labpilot/observability migrations/env.py \
  migrations/versions/0002_runtime_events_outbox.py \
  tests/unit/observability tests/integration/db/test_migrations.py \
  tests/integration/observability
git commit -m "feat: add RuntimeEvent and outbox foundation"
```

---

### Task 7: Enforce the Complete Internal Module Dependency Allowlist

**Files:**
- Create: `tests/architecture/dependency_rules.py`
- Create: `tests/architecture/test_module_dependencies.py`

**Interfaces:**
- Consumes: frozen architecture index Section 5.1.
- Produces: AST-based architecture test that rejects every internal dependency not explicitly allowed.

- [ ] **Step 1: Write failing allowlist tests against synthetic modules**

Create `tests/architecture/test_module_dependencies.py`:

```python
from pathlib import Path

import pytest

from tests.architecture.dependency_rules import (
    DependencyViolation,
    find_dependency_violations,
)

pytestmark = pytest.mark.architecture


def write_module(root: Path, relative_path: str, source: str) -> Path:
    path = root / "src" / "labpilot" / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return path


def test_all_aliases_in_import_statement_are_checked(tmp_path: Path) -> None:
    write_module(
        tmp_path,
        "agent_runtime/example.py",
        "import labpilot.common, labpilot.coordinator\n",
    )
    violations = find_dependency_violations(tmp_path / "src" / "labpilot")
    assert violations == [
        DependencyViolation(
            path="agent_runtime/example.py",
            source_module="agent_runtime",
            target_module="coordinator",
            line=1,
        )
    ]


def test_absolute_from_import_is_checked(tmp_path: Path) -> None:
    write_module(
        tmp_path,
        "agent_runtime/example.py",
        "from labpilot.execution import contracts\n",
    )
    violations = find_dependency_violations(tmp_path / "src" / "labpilot")
    assert violations[0].target_module == "execution"


def test_root_package_alias_import_is_checked(tmp_path: Path) -> None:
    write_module(
        tmp_path,
        "agent_runtime/example.py",
        "from labpilot import coordinator, execution\n",
    )
    violations = find_dependency_violations(tmp_path / "src" / "labpilot")
    assert {violation.target_module for violation in violations} == {
        "coordinator",
        "execution",
    }


def test_relative_root_alias_import_is_checked(tmp_path: Path) -> None:
    write_module(
        tmp_path,
        "agent_runtime/sub/example.py",
        "from ... import coordinator\n",
    )
    violations = find_dependency_violations(tmp_path / "src" / "labpilot")
    assert violations[0].target_module == "coordinator"


def test_relative_import_is_resolved(tmp_path: Path) -> None:
    write_module(
        tmp_path,
        "agent_runtime/sub/example.py",
        "from ...coordinator import worker\n",
    )
    violations = find_dependency_violations(tmp_path / "src" / "labpilot")
    assert violations[0].target_module == "coordinator"


def test_type_checking_import_is_not_ignored(tmp_path: Path) -> None:
    write_module(
        tmp_path,
        "context/example.py",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from labpilot.coordinator import worker\n",
    )
    violations = find_dependency_violations(tmp_path / "src" / "labpilot")
    assert violations[0].target_module == "coordinator"


def test_allowed_common_import_passes(tmp_path: Path) -> None:
    write_module(
        tmp_path,
        "agent_runtime/example.py",
        "from labpilot.common import readiness\n",
    )
    assert find_dependency_violations(tmp_path / "src" / "labpilot") == []


def test_real_source_tree_respects_the_allowlist() -> None:
    violations = find_dependency_violations(Path("src/labpilot"))
    assert violations == []
```

Create an empty `tests/architecture/__init__.py` so `dependency_rules` can be imported.

- [ ] **Step 2: Run the architecture test and verify the rules module is missing**

Run:

```bash
uv run pytest tests/architecture/test_module_dependencies.py -v -m architecture
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tests.architecture.dependency_rules'`.

- [ ] **Step 3: Implement the exact allowlist and AST resolver**

Create `tests/architecture/dependency_rules.py`:

```python
from dataclasses import dataclass
import ast
from pathlib import Path


ALLOWED_INTERNAL_IMPORTS: dict[str, set[str]] = {
    "common": {"common"},
    "db": {"common", "db"},
    "observability": {"common", "db", "observability"},
    "experiments": {"common", "db", "observability", "experiments"},
    "context": {"common", "db", "observability", "experiments", "context"},
    "tools_policy": {"common", "db", "observability", "context", "tools_policy"},
    "agent_runtime": {
        "common",
        "db",
        "observability",
        "experiments",
        "context",
        "tools_policy",
        "agent_runtime",
    },
    "execution": {"common", "db", "observability", "execution"},
    "results_validation": {
        "common",
        "db",
        "observability",
        "experiments",
        "execution",
        "results_validation",
    },
    "coordinator": {
        "common",
        "db",
        "observability",
        "experiments",
        "execution",
        "results_validation",
        "coordinator",
    },
    "api": {
        "common",
        "db",
        "observability",
        "experiments",
        "context",
        "tools_policy",
        "agent_runtime",
        "execution",
        "results_validation",
        "coordinator",
        "api",
    },
}

ROOT_MODULE_CLASSIFICATION = {
    "__init__": "common",
    "settings": "common",
    "main": "api",
}


@dataclass(frozen=True, slots=True, order=True)
class DependencyViolation:
    path: str
    source_module: str
    target_module: str
    line: int


def classify_source_module(relative_path: Path) -> str:
    if len(relative_path.parts) == 1:
        return ROOT_MODULE_CLASSIFICATION[relative_path.stem]
    return relative_path.parts[0]


def classify_absolute_target(module_name: str) -> str | None:
    parts = module_name.split(".")
    if not parts or parts[0] != "labpilot":
        return None
    if len(parts) == 1:
        return "common"
    return ROOT_MODULE_CLASSIFICATION.get(parts[1], parts[1])


def resolve_relative_target(
    *,
    relative_path: Path,
    level: int,
    module_name: str | None,
) -> str:
    package_parts = list(relative_path.parent.parts)
    ascend = level - 1
    if ascend > len(package_parts):
        raise AssertionError(
            f"invalid relative import level={level} in {relative_path.as_posix()}"
        )
    base = package_parts[: len(package_parts) - ascend]
    suffix = module_name.split(".") if module_name else []
    resolved = base + suffix
    if not resolved:
        return "common"
    return ROOT_MODULE_CLASSIFICATION.get(resolved[0], resolved[0])


def imported_targets(tree: ast.AST, relative_path: Path) -> list[tuple[str, int]]:
    targets: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = classify_absolute_target(alias.name)
                if target is not None:
                    targets.append((target, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module == "labpilot":
                for alias in node.names:
                    target = classify_absolute_target(f"labpilot.{alias.name}")
                    if target is not None:
                        targets.append((target, node.lineno))
                continue
            if node.level > 0 and node.module is None:
                root_target = resolve_relative_target(
                    relative_path=relative_path,
                    level=node.level,
                    module_name=None,
                )
                if root_target == "common":
                    for alias in node.names:
                        targets.append(
                            (
                                ROOT_MODULE_CLASSIFICATION.get(alias.name, alias.name),
                                node.lineno,
                            )
                        )
                else:
                    targets.append((root_target, node.lineno))
                continue
            if node.level > 0:
                target = resolve_relative_target(
                    relative_path=relative_path,
                    level=node.level,
                    module_name=node.module,
                )
            else:
                target = classify_absolute_target(node.module or "")
            if target is not None:
                targets.append((target, node.lineno))
    return targets


def find_dependency_violations(source_root: Path) -> list[DependencyViolation]:
    violations: list[DependencyViolation] = []
    for path in sorted(source_root.rglob("*.py")):
        relative_path = path.relative_to(source_root)
        source_module = classify_source_module(relative_path)
        allowed = ALLOWED_INTERNAL_IMPORTS[source_module]
        tree = ast.parse(path.read_text(), filename=relative_path.as_posix())
        for target_module, line in imported_targets(tree, relative_path):
            if target_module not in allowed:
                violations.append(
                    DependencyViolation(
                        path=relative_path.as_posix(),
                        source_module=source_module,
                        target_module=target_module,
                        line=line,
                    )
                )
    return sorted(violations)
```

The parser intentionally walks `TYPE_CHECKING` blocks, checks every alias in a multi-import statement, and resolves relative import levels. No production package is exempted.

- [ ] **Step 4: Run architecture and quality checks**

```bash
uv run pytest tests/architecture/test_module_dependencies.py -v -m architecture
uv run ruff check src tests
```

Expected: PASS. Verify the current directory contains no `src/labpilot/workers` package:

```bash
test ! -d src/labpilot/workers
```

- [ ] **Step 5: Commit**

```bash
git add tests/architecture
git commit -m "test: enforce module dependency allowlist"
```

---

### Task 8: Add Process Shells, Graceful Shutdown, and the One-Shot Migration Service

**Files:**
- Modify: `scripts/run_migrations.py`
- Create: `tests/unit/scripts/test_run_migrations.py`
- Create: `src/labpilot/common/signals.py`
- Create: `src/labpilot/coordinator/__init__.py`
- Create: `src/labpilot/coordinator/worker.py`
- Create: `src/labpilot/observability/projection_worker.py`
- Create: `scripts/wait_for_postgres.py`
- Create: `Dockerfile`
- Create: `compose.yaml`
- Create: `tests/unit/common/test_signals.py`
- Create: `tests/unit/coordinator/test_worker.py`
- Create: `tests/unit/observability/test_projection_worker.py`
- Create: `tests/integration/processes/test_worker_sigterm.py`

**Interfaces:**
- Consumes: `Database`, actual migration runner, liveness/readiness endpoints.
- Produces: independently startable API, Coordinator shell, Projection shell, and unique Migration service.

- [ ] **Step 1: Write the failing migration URL regression tests**

Create `tests/unit/scripts/test_run_migrations.py`:

```python
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import make_url

import scripts.run_migrations as run_migrations


@pytest.mark.parametrize(
    ("source", "expected_alembic", "expected_direct"),
    [
        (
            "postgresql+asyncpg://postgres:postgres@postgres:5432/postgres",
            "postgresql+psycopg://postgres:postgres@postgres:5432/postgres",
            "postgresql://postgres:postgres@postgres:5432/postgres",
        ),
        (
            "postgresql+psycopg://postgres:postgres@postgres:5432/postgres",
            "postgresql+psycopg://postgres:postgres@postgres:5432/postgres",
            "postgresql://postgres:postgres@postgres:5432/postgres",
        ),
        (
            "postgresql://postgres:postgres@postgres:5432/postgres",
            "postgresql+psycopg://postgres:postgres@postgres:5432/postgres",
            "postgresql://postgres:postgres@postgres:5432/postgres",
        ),
    ],
)
def test_migration_urls_route_each_postgresql_driver(
    source: str,
    expected_alembic: str,
    expected_direct: str,
) -> None:
    assert run_migrations.migration_urls(source) == (
        expected_alembic,
        expected_direct,
    )


def test_migration_urls_reject_non_postgresql_url() -> None:
    with pytest.raises(
        ValueError,
        match="LABPILOT_DATABASE_URL must use PostgreSQL",
    ):
        run_migrations.migration_urls("sqlite:///labpilot.db")


def test_migration_urls_preserve_escaped_credentials_and_query() -> None:
    source = (
        "postgresql+asyncpg://user%40example.com:p%40ss%2Fword"
        "@postgres:5432/postgres"
        "?application_name=LabPilot%20Migrate&sslmode=require"
    )
    source_url = make_url(source)
    alembic_dsn, direct_dsn = run_migrations.migration_urls(source)

    for dsn in (alembic_dsn, direct_dsn):
        parsed = make_url(dsn)
        assert parsed.username == source_url.username
        assert parsed.password == source_url.password
        assert parsed.database == source_url.database
        assert parsed.query == source_url.query
        assert "***" not in dsn


def test_main_routes_alembic_and_psycopg_urls(monkeypatch) -> None:
    source = "postgresql+asyncpg://postgres:postgres@postgres:5432/postgres"
    monkeypatch.setenv("LABPILOT_DATABASE_URL", source)

    connect = MagicMock()
    monkeypatch.setattr(run_migrations.psycopg, "connect", connect)
    config = MagicMock()
    monkeypatch.setattr(run_migrations, "Config", lambda _: config)
    upgrade = MagicMock()
    monkeypatch.setattr(run_migrations.command, "upgrade", upgrade)

    run_migrations.main()

    connect.assert_called_once_with(
        "postgresql://postgres:postgres@postgres:5432/postgres",
        autocommit=True,
    )
    config.set_main_option.assert_called_once_with(
        "sqlalchemy.url",
        "postgresql+psycopg://postgres:postgres@postgres:5432/postgres",
    )
    upgrade.assert_called_once_with(config, "head")
```

- [ ] **Step 2: Run the migration URL regression tests and verify RED**

Run:

```bash
uv run pytest tests/unit/scripts/test_run_migrations.py -v
```

Expected: FAIL because `scripts.run_migrations.migration_urls` does not exist.

- [ ] **Step 3: Separate Alembic and direct psycopg URLs with the SQLAlchemy URL API**

Modify `scripts/run_migrations.py`:

```python
import os

import psycopg
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

MIGRATION_LOCK_KEY = 0x4C414250


def migration_urls(database_url: str) -> tuple[str, str]:
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "postgresql":
        raise ValueError("LABPILOT_DATABASE_URL must use PostgreSQL")
    alembic_url = parsed.set(drivername="postgresql+psycopg")
    direct_url = parsed.set(drivername="postgresql")
    return (
        alembic_url.render_as_string(hide_password=False),
        direct_url.render_as_string(hide_password=False),
    )


def main() -> None:
    alembic_dsn, direct_dsn = migration_urls(
        os.environ["LABPILOT_DATABASE_URL"]
    )
    with psycopg.connect(direct_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        try:
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", alembic_dsn)
            command.upgrade(config, "head")
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_unlock(%s)",
                    (MIGRATION_LOCK_KEY,),
                )


if __name__ == "__main__":
    main()
```

This implementation must use `make_url()` plus `URL.set(drivername=...)`; do not normalize drivers with chained string replacement.

Run:

```bash
uv run pytest tests/unit/scripts/test_run_migrations.py -v
```

Expected: PASS; Alembic receives `postgresql+psycopg://`, direct `psycopg.connect()` receives `postgresql://`, non-PostgreSQL URLs are rejected, and escaped credentials plus query parameters survive without password masking.

- [ ] **Step 4: Write failing graceful-shutdown tests**

Create `tests/unit/common/test_signals.py`:

```python
import asyncio

from labpilot.common.signals import install_shutdown_handlers


def test_shutdown_handler_registers_sigterm(monkeypatch) -> None:
    event = asyncio.Event()
    registered: list[tuple[object, object]] = []

    class FakeLoop:
        def add_signal_handler(self, signal_value, callback) -> None:
            registered.append((signal_value, callback))

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: FakeLoop())
    install_shutdown_handlers(event)
    assert len(registered) == 2
    registered[1][1]()
    assert event.is_set()
```

Create `tests/unit/coordinator/test_worker.py`:

```python
import asyncio
from contextlib import asynccontextmanager

import pytest

from labpilot.coordinator.worker import run


class FakeSession:
    def __init__(self, stop_event: asyncio.Event) -> None:
        self.stop_event = stop_event
        self.probes = 0

    async def execute(self, statement: object) -> None:
        self.probes += 1
        self.stop_event.set()


class FakeDatabase:
    def __init__(self, stop_event: asyncio.Event) -> None:
        self.session_value = FakeSession(stop_event)
        self.disposed = False

    @asynccontextmanager
    async def session(self):
        yield self.session_value

    async def dispose(self) -> None:
        self.disposed = True


@pytest.mark.asyncio
async def test_process_shell_probes_once_and_disposes() -> None:
    stop_event = asyncio.Event()
    database = FakeDatabase(stop_event)
    await run(stop_event=stop_event, database=database, probe_interval_seconds=0.01)
    assert database.session_value.probes == 1
    assert database.disposed is True
```

Create `tests/unit/observability/test_projection_worker.py`:

```python
import asyncio
from contextlib import asynccontextmanager

import pytest

from labpilot.observability.projection_worker import run


class FakeSession:
    def __init__(self, stop_event: asyncio.Event) -> None:
        self.stop_event = stop_event
        self.probes = 0

    async def execute(self, statement: object) -> None:
        self.probes += 1
        self.stop_event.set()


class FakeDatabase:
    def __init__(self, stop_event: asyncio.Event) -> None:
        self.session_value = FakeSession(stop_event)
        self.disposed = False

    @asynccontextmanager
    async def session(self):
        yield self.session_value

    async def dispose(self) -> None:
        self.disposed = True


@pytest.mark.asyncio
async def test_process_shell_probes_once_and_disposes() -> None:
    stop_event = asyncio.Event()
    database = FakeDatabase(stop_event)
    await run(stop_event=stop_event, database=database, probe_interval_seconds=0.01)
    assert database.session_value.probes == 1
    assert database.disposed is True
```

Create `tests/integration/processes/test_worker_sigterm.py`:

```python
import asyncio
import json
import os
import signal
import sys

import pytest


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module_name",
    [
        "labpilot.coordinator.worker",
        "labpilot.observability.projection_worker",
    ],
)
async def test_worker_process_exits_zero_on_sigterm(
    module_name: str,
    isolated_database_url: str,
) -> None:
    environment = os.environ.copy()
    environment["LABPILOT_DATABASE_URL"] = isolated_database_url
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        module_name,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    try:
        line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
        marker = json.loads(line)
        assert marker["event"] == "process_shell.ready"
        assert marker["process"] in {"coordinator", "projection"}
        process.send_signal(signal.SIGTERM)
        return_code = await asyncio.wait_for(process.wait(), timeout=5)
        assert return_code == 0
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
```

- [ ] **Step 5: Run focused tests and verify worker modules are missing**

Run:

```bash
uv run pytest tests/unit/common/test_signals.py \
  tests/unit/coordinator/test_worker.py \
  tests/unit/observability/test_projection_worker.py -v
```

Expected: FAIL because process shell modules do not exist.

- [ ] **Step 6: Implement shared shutdown handling and process shells**

Create `src/labpilot/common/signals.py`:

```python
import asyncio
import signal


def install_shutdown_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for handled_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(handled_signal, stop_event.set)
```

Create `src/labpilot/coordinator/worker.py`:

```python
import asyncio
import json

from sqlalchemy import text

from labpilot.common.signals import install_shutdown_handlers
from labpilot.db.session import Database
from labpilot.settings import get_settings


async def run(
    stop_event: asyncio.Event | None = None,
    database: Database | None = None,
    probe_interval_seconds: float = 5.0,
) -> None:
    event = stop_event or asyncio.Event()
    if stop_event is None:
        install_shutdown_handlers(event)
    active_database = database or Database(get_settings().database_url)
    ready_emitted = False
    try:
        while not event.is_set():
            async with active_database.session() as session:
                await session.execute(text("SELECT 1"))
            if not ready_emitted:
                print(
                    json.dumps(
                        {"event": "process_shell.ready", "process": "coordinator"}
                    ),
                    flush=True,
                )
                ready_emitted = True
            try:
                await asyncio.wait_for(event.wait(), timeout=probe_interval_seconds)
            except TimeoutError:
                pass
    finally:
        await active_database.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
```

Create `src/labpilot/observability/projection_worker.py`:

```python
import asyncio
import json

from sqlalchemy import text

from labpilot.common.signals import install_shutdown_handlers
from labpilot.db.session import Database
from labpilot.settings import get_settings


async def run(
    stop_event: asyncio.Event | None = None,
    database: Database | None = None,
    probe_interval_seconds: float = 5.0,
) -> None:
    event = stop_event or asyncio.Event()
    if stop_event is None:
        install_shutdown_handlers(event)
    active_database = database or Database(get_settings().database_url)
    ready_emitted = False
    try:
        while not event.is_set():
            async with active_database.session() as session:
                await session.execute(text("SELECT 1"))
            if not ready_emitted:
                print(
                    json.dumps(
                        {"event": "process_shell.ready", "process": "projection"}
                    ),
                    flush=True,
                )
                ready_emitted = True
            try:
                await asyncio.wait_for(event.wait(), timeout=probe_interval_seconds)
            except TimeoutError:
                pass
    finally:
        await active_database.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
```

Create `src/labpilot/coordinator/__init__.py`:

```python
"""Coordinator process package."""
```

- [ ] **Step 7: Add PostgreSQL wait script, Dockerfile, and Compose topology**

Create `scripts/wait_for_postgres.py`:

```python
import asyncio
import os

from sqlalchemy import text

from labpilot.db.session import Database


async def main() -> None:
    database = Database(os.environ["LABPILOT_DATABASE_URL"])
    try:
        for attempt in range(60):
            try:
                async with database.session() as session:
                    await session.execute(text("SELECT 1"))
                return
            except Exception:
                if attempt == 59:
                    raise
                await asyncio.sleep(1)
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
```

Create `Dockerfile`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.8.0
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
CMD ["uvicorn", "labpilot.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Create `compose.yaml` with these services and dependencies:

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: labpilot
      POSTGRES_PASSWORD: labpilot
      POSTGRES_DB: labpilot
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U labpilot -d labpilot"]
      interval: 2s
      timeout: 2s
      retries: 30
    volumes:
      - labpilot_pgdata:/var/lib/postgresql/data

  migrate:
    build: .
    restart: "no"
    environment: &app-env
      LABPILOT_DATABASE_URL: postgresql+asyncpg://labpilot:labpilot@postgres:5432/labpilot
      LABPILOT_ENVIRONMENT: development
    command: ["sh", "-c", "python scripts/wait_for_postgres.py && python scripts/run_migrations.py"]
    depends_on:
      postgres:
        condition: service_healthy

  api:
    build: .
    environment: *app-env
    command: ["uvicorn", "labpilot.main:app", "--host", "0.0.0.0", "--port", "8000"]
    ports:
      - "${LABPILOT_API_PORT:-8000}:8000"
    depends_on:
      migrate:
        condition: service_completed_successfully
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/ready')"]
      interval: 5s
      timeout: 3s
      retries: 20

  coordinator:
    build: .
    environment: *app-env
    command: ["python", "-m", "labpilot.coordinator.worker"]
    depends_on:
      migrate:
        condition: service_completed_successfully

  projection:
    build: .
    environment: *app-env
    command: ["python", "-m", "labpilot.observability.projection_worker"]
    depends_on:
      migrate:
        condition: service_completed_successfully

volumes:
  labpilot_pgdata:
```

- [ ] **Step 8: Run unit and Compose smoke tests**

Run:

```bash
uv run pytest tests/unit/common/test_signals.py \
  tests/unit/coordinator/test_worker.py \
  tests/unit/observability/test_projection_worker.py -v

LABPILOT_TEST_ADMIN_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres \
  uv run pytest tests/integration/processes/test_worker_sigterm.py -v -m integration

PROJECT="labpilot_wp0_task8_smoke_$$"
API_PORT=$(python - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)
export LABPILOT_API_PORT="$API_PORT"
cleanup() {
  docker compose -p "$PROJECT" down -v --remove-orphans || true
}
trap cleanup EXIT INT TERM
docker compose -p "$PROJECT" up --build -d
curl --fail "http://127.0.0.1:${API_PORT}/health/live"
curl --fail "http://127.0.0.1:${API_PORT}/health/ready"
coordinator_id=$(docker compose -p "$PROJECT" ps -q coordinator)
projection_id=$(docker compose -p "$PROJECT" ps -q projection)
docker compose -p "$PROJECT" stop -t 10 coordinator projection
for container_id in "$coordinator_id" "$projection_id"; do
  state=$(docker inspect \
    --format '{{.State.ExitCode}} {{.State.OOMKilled}} {{.State.Error}}' \
    "$container_id")
  [ "$state" = "0 false " ] || {
    echo "unexpected worker exit state: $state" >&2
    exit 1
  }
done
docker compose -p "$PROJECT" logs coordinator projection
```

Expected:

```text
both health endpoints return 200 after migrate completes
subprocess and container workers receive SIGTERM and exit 0 within the deadline
OOMKilled is false and container Error is empty
unit tests prove Database.dispose() is called
logs prove only process-topology probes, not durable job processing
```

- [ ] **Step 9: Run quality checks and commit**

```bash
uv run ruff check src tests scripts
uv run mypy src scripts

git add src/labpilot/common/signals.py src/labpilot/coordinator \
  src/labpilot/observability/projection_worker.py scripts/wait_for_postgres.py scripts/run_migrations.py \
  Dockerfile compose.yaml tests/unit/common tests/unit/coordinator \
  tests/unit/observability tests/unit/scripts tests/integration/processes
git commit -m "feat: add control-plane process shells"
```

---

### Task 9: Add PostgreSQL Capability Tests and Continuous Integration

**Files:**
- Create: `tests/integration/db/test_skip_locked_capability.py`
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: PostgreSQL integration fixture, migrations, all WP0 tests.
- Produces: explicit PostgreSQL capability evidence and clean-checkout CI.

- [ ] **Step 1: Add a PostgreSQL `SKIP LOCKED` capability test**

Create `tests/integration/db/test_skip_locked_capability.py`:

```python
import pytest
from sqlalchemy import text

from labpilot.db.session import Database


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgresql_skip_locked_returns_the_next_unlocked_row(
    isolated_database_url: str,
) -> None:
    database = Database(isolated_database_url)
    try:
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE capability_jobs ("
                    "id integer PRIMARY KEY, status text NOT NULL)"
                )
            )
            await connection.execute(
                text("INSERT INTO capability_jobs VALUES (1, 'pending'), (2, 'pending')")
            )

        connection_a = await database.engine.connect()
        connection_b = await database.engine.connect()
        transaction_a = await connection_a.begin()
        transaction_b = await connection_b.begin()
        try:
            locked = await connection_a.scalar(
                text(
                    "SELECT id FROM capability_jobs "
                    "WHERE status = 'pending' ORDER BY id "
                    "FOR UPDATE LIMIT 1"
                )
            )
            skipped = await connection_b.scalar(
                text(
                    "SELECT id FROM capability_jobs "
                    "WHERE status = 'pending' ORDER BY id "
                    "FOR UPDATE SKIP LOCKED LIMIT 1"
                )
            )
            assert locked == 1
            assert skipped == 2
        finally:
            await transaction_b.rollback()
            await transaction_a.rollback()
            await connection_b.close()
            await connection_a.close()
    finally:
        await database.dispose()
```

This is a capability test and is expected to pass on PostgreSQL 16; do not label it as a deliberately failing TDD step.

- [ ] **Step 2: Run the capability test**

Run:

```bash
LABPILOT_TEST_ADMIN_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres \
  uv run pytest tests/integration/db/test_skip_locked_capability.py -v -m integration
```

Expected: PASS.

- [ ] **Step 3: Add GitHub Actions CI**

Create `.github/workflows/ci.yml`:

```yaml
name: ci

on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: postgres
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U postgres -d postgres"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 20
    env:
      LABPILOT_DATABASE_URL: postgresql+asyncpg://postgres:postgres@localhost:5432/labpilot_ci
      LABPILOT_TEST_ADMIN_DATABASE_URL: postgresql+asyncpg://postgres:postgres@localhost:5432/postgres
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          version: "0.8.0"
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Verify frozen dependencies
        run: ./scripts/verify_architecture_dependencies.sh docs/labpilot/implementation/phase-0-v0.3.3.dependencies.sha256
      - name: Install
        run: uv sync --frozen --all-groups
      - name: Ruff
        run: uv run ruff check src tests scripts
      - name: Mypy
        run: uv run mypy src scripts
      - name: Unit and architecture tests
        run: uv run pytest tests/unit tests/architecture -v
      - name: PostgreSQL integration tests
        run: uv run pytest tests/integration -v -m integration
      - name: Dependency guard shell tests
        run: ./tests/scripts/test_verify_architecture_dependencies.sh
```

- [ ] **Step 4: Run the exact CI commands locally**

```bash
./scripts/verify_architecture_dependencies.sh \
  docs/labpilot/implementation/phase-0-v0.3.3.dependencies.sha256
uv sync --frozen --all-groups
uv run ruff check src tests scripts
uv run mypy src scripts
uv run pytest tests/unit tests/architecture -v
LABPILOT_TEST_ADMIN_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres \
  uv run pytest tests/integration -v -m integration
./tests/scripts/test_verify_architecture_dependencies.sh
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/db/test_skip_locked_capability.py .github/workflows/ci.yml
git commit -m "ci: verify PostgreSQL foundation"
```

---

### Task 10: Verify Phase 0 from a Clean Environment and Record the Baseline

**Files:**
- Create: `scripts/check_foundation.sh`
- Create during execution: `docs/labpilot/implementation/phase-0-acceptance.md`
- Modify during execution: `docs/labpilot/implementation/phase-0-execution-progress.md`

**Interfaces:**
- Consumes: committed Tasks 0–9, immutable WP0 v0.3.3 plan, and the versioned dependency manifest.
- Produces: an isolated acceptance run bound to an exact clean Git commit/tree, followed by a separate evidence commit and a tag pointing at the tested code commit.

- [ ] **Step 1: Create the isolated clean-environment acceptance script**

Create `scripts/check_foundation.sh`:

```sh
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
API_PORT=$(python - <<'PY'
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
PLAN_PATH=docs/superpowers/plans/2026-07-27-labpilot-phase-0-foundation-v0.3.3-ready-for-execution.md
MANIFEST_PATH=docs/labpilot/implementation/phase-0-v0.3.3.dependencies.sha256
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

python - "$API_PORT" <<'PY'
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
```

Make it executable:

```bash
chmod 755 scripts/check_foundation.sh
```

- [ ] **Step 2: Test and commit the acceptance tooling before acceptance**

Run static checks for the new script:

```bash
sh -n scripts/check_foundation.sh
uv run ruff check src tests scripts
uv run mypy src scripts
```

Commit only the tooling:

```bash
git add scripts/check_foundation.sh
git commit -m "test: add isolated foundation acceptance runner"
test -z "$(git status --porcelain)"
```

Expected: the worktree is clean, so the next commit and tree are exactly what acceptance will test.

- [ ] **Step 3: Run the isolated acceptance against the clean tested tree**

Run:

```bash
LABPILOT_TEST_ADMIN_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres \
  ./scripts/check_foundation.sh
```

Expected: exit `0`; use a unique Compose project and dynamic API port; never stop or delete a pre-existing development environment; create only `phase-0-acceptance.md` as new tracked evidence.

- [ ] **Step 4: Review the acceptance evidence and tested identity**

Run:

```bash
cat docs/labpilot/implementation/phase-0-acceptance.md
git status --short
```

Expected: the record contains `tested commit`, `tested tree`, plan/manifest/script hashes, isolated Compose identity, and only the acceptance record is uncommitted.

- [ ] **Step 5: Update mutable execution progress without editing the plan**

Run:

```bash
python - <<'PY'
from datetime import UTC, datetime
from pathlib import Path
import re

acceptance = Path("docs/labpilot/implementation/phase-0-acceptance.md").read_text()
match = re.search(r"^- Tested commit: `([^`]+)`$", acceptance, re.MULTILINE)
if match is None:
    raise SystemExit("tested commit is missing from acceptance evidence")
tested_commit = match.group(1)
completed_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
path = Path("docs/labpilot/implementation/phase-0-execution-progress.md")
lines = path.read_text().splitlines()
replacement = (
    f"| 10 | completed | {completed_at} | {completed_at} | {tested_commit} | "
    "isolated foundation acceptance PASS | pending |"
)
updated = [replacement if line.startswith("| 10 |") else line for line in lines]
if updated == lines:
    raise SystemExit("Task 10 progress row was not found")
path.write_text("\n".join(updated) + "\n")
PY
```

Do not modify this versioned plan or its checkbox markers.

- [ ] **Step 6: Commit evidence and tag the exact tested code commit**

```bash
TESTED_COMMIT=$(sed -n 's/^- Tested commit: `\([^`]*\)`.*/\1/p' \
  docs/labpilot/implementation/phase-0-acceptance.md)
git add docs/labpilot/implementation/phase-0-acceptance.md \
  docs/labpilot/implementation/phase-0-execution-progress.md
git commit -m "docs: record LabPilot foundation acceptance"
git tag -a v0.1.0-foundation "$TESTED_COMMIT" \
  -m "LabPilot WP0 tested foundation"
```

Expected: the tag points to the code/tooling commit that was actually tested, while the later evidence commit contains the acceptance record.

- [ ] **Step 7: Verify the evidence commit did not alter frozen inputs**

```bash
./scripts/verify_architecture_dependencies.sh \
  docs/labpilot/implementation/phase-0-v0.3.3.dependencies.sha256
changed=$(git diff --name-only "$TESTED_COMMIT"..HEAD)
expected=$(printf '%s\n' \
  docs/labpilot/implementation/phase-0-acceptance.md \
  docs/labpilot/implementation/phase-0-execution-progress.md)
test "$changed" = "$expected"
```

Expected: dependency verification passes; the post-test evidence commit changes exactly the two mutable evidence/progress files and no frozen input or implementation file.

---

## Verified Commands Required Before Declaring WP0 Complete

```bash
./scripts/verify_architecture_dependencies.sh \
  docs/labpilot/implementation/phase-0-v0.3.3.dependencies.sha256
./tests/scripts/test_verify_architecture_dependencies.sh
uv sync --frozen --all-groups
uv run ruff check src tests scripts
uv run mypy src scripts
uv run pytest tests/unit tests/architecture -v
LABPILOT_TEST_ADMIN_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres \
  uv run pytest tests/integration -v -m integration
LABPILOT_TEST_ADMIN_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres \
  ./scripts/check_foundation.sh
```

## Proven Foundation Contracts

After all tasks pass, WP0 proves only the following:

```text
frozen architecture inputs are hash-verified
Python package and quality toolchain work from a clean checkout
FastAPI has independent liveness and dependency-aware readiness
PostgreSQL async sessions and strict single-transaction UoW work
Alembic upgrades an isolated empty database
Migration execution has a unique advisory-locked owner
Canonical JSON V1 has fixed Unicode, type, and resource boundaries
RuntimeEvent and Outbox use the complete frozen envelope, including required project_id and created_at
RuntimeEvent, Outbox, and authority writes can share one transaction
aggregate event sequence is concurrency-safe
Outbox publication is lease-generation fenced and uses PostgreSQL time
internal module dependencies are enforced by allowlist
API, Coordinator shell, and Projection shell are independent processes
process shells emit a ready marker, receive SIGTERM, release database resources in unit tests, and exit 0 within the acceptance deadline
Docker Compose waits for one-shot migration completion
PostgreSQL SKIP LOCKED capability is verified
CI executes the same foundation checks
```

WP0 does not prove:

```text
Experiment Domain behavior
Context compilation
Agent Runtime or Provider calls
Tool execution or Approval
Durable Coordinator Job processing
Projection delivery or replay
Local Runner or process supervision
Artifact, Validation, or Summary
Web UI or Core Demo behavior
```

## Next Planning Gate

Only after `phase-0-acceptance.md` exists on the accepted branch may WP1 planning begin. The WP1 plan must inspect the actual implementations of:

```text
labpilot.db.uow.UnitOfWork
labpilot.common.canonical_json.canonical_json_bytes
labpilot.common.hashing.namespaced_sha256
labpilot.observability.events.append_runtime_event
labpilot.observability.outbox.claim_outbox_records
labpilot.observability.models.RuntimeEventRow
labpilot.observability.models.OutboxRecordRow
```

It must not redefine these interfaces inside the WP1 plan without an explicit Architecture Deviation Record.

## Plan Self-Review Results

```text
Spec coverage: WP0 foundation requirements mapped to Tasks 0–10
Review feedback coverage: v0.3 final execution review corrections incorporated
Placeholder scan: no unresolved placeholders or undefined task references
Type consistency: UoW, readiness, event, outbox, and process-shell names are stable
Scope check: no business aggregate, Agent, Tool, Runner, Result Plane, or Web implementation
Dependency guard: runs before Task 1 and at final acceptance
Migration ownership: one-shot service only
Test isolation: each destructive PostgreSQL test owns an isolated database
TDD accuracy: capability tests are not mislabeled as intentionally failing tests
Execution status: v0.3.3 Ready for Execution after dependency verification
```
