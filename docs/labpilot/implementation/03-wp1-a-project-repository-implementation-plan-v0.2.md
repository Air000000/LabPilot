# LabPilot WP1-A Project and Repository Implementation Plan

```text
Document type: Work Package Detailed Implementation Plan
Version: v0.2
Status: Approved Implementation Plan
Date: 2026-07-28
Target branch: feature/wp1-domain-context
Baseline: main@769e8fd
Approved design input: WP1 Domain and Context Overall Design v0.5
Approved design SHA-256: 82bcceac0abb8f9176f827b421d567d8d8fb427037e3b9c21d963df6d9f1c003
Supersedes for planning: WP1-A implementation plan v0.1
Execution authorization: none until this exact v0.2 plan is approved and reissued as Approved Implementation Plan
```

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute this plan task-by-task. Project constraints prohibit subagents and parallel agents. Stop after every Task for independent review before continuing.

**Goal:** Implement and independently accept the WP1-A Project/Repository foundation: generic command idempotency, Project lifecycle, secure local Git registration, immutable clean/dirty snapshots, freshness projection, deterministic inspection, evidence manifests, public APIs, RuntimeEvent/Outbox atomicity, and PostgreSQL constraints.

**Architecture:** Reuse WP0 `Database`, `UnitOfWork`, RuntimeEvent, Outbox, Canonical JSON V1, and namespaced SHA-256. `experiments` owns `Project` and Project pointer handlers; `context` owns Repository, Snapshot, Freshness, Inspection, Evidence, and content capture. Idempotency lookup occurs before any external preparation; Git/file preparation executes without an open database transaction; authoritative mutations then use one short UoW. Every authoritative state change atomically commits its domain mutation, RuntimeEvent, Outbox, and bounded `CommandReceipt`; semantic reuse under a new key commits only a new Receipt referencing the existing domain result.

**Tech Stack:** Python 3.12, FastAPI 0.116, Pydantic 2.11, SQLAlchemy 2.0 asyncio, Alembic 1.16, PostgreSQL, `asyncpg`, `psycopg`, `orjson`, `pytest`, `pytest-asyncio`, Ruff, mypy.

## Global Constraints

- Baseline is `main@769e8fd`; WP0 Manifest, evidence, accepted tag, and `v0.1.0-foundation` remain untouched.
- Work only on `feature/wp1-domain-context`; do not push automatically.
- The approved design input is `docs/labpilot/implementation/02-wp1-domain-context-overall-design-v0.5.md`, SHA-256 `82bcceac0abb8f9176f827b421d567d8d8fb427037e3b9c21d963df6d9f1c003`; its only post-approval editorial correction is section 0.1 item numbering `14 -> 6`.
- Python must remain `>=3.12,<3.13`; PostgreSQL is the acceptance database.
- Preserve the module rule `context -> experiments`; `experiments -> context` is forbidden.
- One public command owns exactly one `UnitOfWork`; internal handlers accept that UoW and never open nested transactions.
- No Router performs domain decisions or directly mutates ORM rows.
- No LLM, network request, training process, Runner, Coordinator, Attempt command, or unbounded external work is introduced in WP1-A.
- Git/file observations run outside the short database transaction; database commit records the exact immutable observation identity.
- All semantic identities use Canonical JSON V1 plus `namespaced_sha256()` with explicit namespace/version.
- Clients never author identity/hash, authority, freshness, pass/fail, actor, or timestamp fields.
- Every public write command requires `Idempotency-Key`; the executor checks a committed Receipt before external preparation, so a replay never probes Git, reads files, or reconstructs results.
- Every authoritative state change commits Domain mutation + RuntimeEvent + Outbox + CommandReceipt atomically. Semantic reuse under a new key commits only a new CommandReceipt and emits no duplicate created/completed Event or Outbox record.
- External `prepare()` work runs only after a Receipt miss and while no database transaction is open. A second Receipt lookup may occur after preparation and before opening the UoW.
- A unique-key race on `CommandReceipt` rolls back the losing UoW and reloads the winner in a new read-only session.
- Domain execution errors, invariant violations, and infrastructure failures do not leave success receipts.
- `CommandResultEnvelope` is a fixed-field Canonical JSON record capped at 16 KiB; normalized command requests are capped at 64 KiB before hashing. Arbitrary metadata, raw paths, file contents, stacks, and credentials are forbidden.
- Event payloads remain bounded, versioned, secret-scanned summaries; large manifests and file contents never enter RuntimeEvent payloads.
- HTTP semantics are fixed: first creation `201`; Receipt replay `200`; semantic reuse with a new key `200`; first successful state update `200`.
- Event aggregate ownership is fixed: `project.*` -> Project; `repository.registration.*` -> RepositoryRegistration; `repository.snapshot.*` -> RepositorySnapshot; `repository.inspection.*` and `repository.evidence_manifest.created` -> RepositoryInspectionRun.
- Every Task ends with tests, a focused commit, and reviewer approval. Do not combine WP1-A/B/C implementation commits.
- Do not silently modify an accepted earlier Task. Record impact, add tests, and re-review the affected Task.


---

## 0. v0.2 Closed Revision Set

v0.2 changes only the execution contracts rejected in the v0.1 review:

```text
1. planning approval state and Task 0 dependency governance
2. Receipt lookup before prepare, with prepare outside UoW
3. Receipt replay versus semantic reuse Event semantics
4. live RepositoryRegistration root partial uniqueness
5. absolute paths and complete read-only Git observation rules
6. dirty Snapshot fail-closed capture stability protocol
7. fixed CommandResultEnvelope and request resource limits
8. complete Inspector registry/request/result identity formulas
9. evidence commit before independent review
10. fixed HTTP replay codes, CAS directory fsync, and Event aggregate ownership
11. non-self-matching Task 0 approval and placeholder checks
12. evidence commit handoff without self-referential evidence rewrites
13. distinct Evidence entries-manifest and full-manifest hashes
14. versioned Git rename/copy detection independent of Repository-local config
```

This revision does not add any WP1-B, WP1-C, WP2, WP3, or P1 capability.

## 0.1 Idempotent Command Execution Contract

The shared executor contract is:

```python
PreparedT = TypeVar("PreparedT")

async def execute_idempotent_command(
    *,
    session_context_factory: SessionContextFactory,
    identity: CommandIdentity,
    prepare: Callable[[], Awaitable[PreparedT]],
    operation: Callable[
        [UnitOfWork, PreparedT],
        Awaitable[CommandResultEnvelope],
    ],
) -> IdempotentCommandOutcome: ...
```

Required algorithm:

```text
1. short read-only Session: read committed Receipt
2. hit + same request hash: replay immediately; do not call prepare
3. hit + different request hash: IdempotencyKeyConflict
4. miss: close Session, then run prepare with no open database transaction
5. optional second short read-only Receipt lookup after prepare
6. open one short UoW and call operation(uow, prepared)
7. insert CommandReceipt and commit
8. exact Receipt unique race: loser UoW rolls back completely
9. loser opens a new read-only Session, reads winner, then replays or conflicts
```

Commands without external preparation use an async no-op prepare returning `None`.

## 0.2 Replay, Semantic Reuse, and Event Semantics

```text
Receipt replay:
  same scope + command + version + key + request hash
  -> original Result Envelope
  -> replayed = true
  -> no prepare, mutation, Event, Outbox, or new Receipt

Semantic reuse:
  new key, but a domain semantic identity already exists
  -> existing domain object
  -> replayed = false
  -> outcome_kind ends in _reused
  -> one new CommandReceipt only
  -> no duplicate created/completed Event or Outbox

Authoritative state change:
  -> domain mutation + RuntimeEvent + Outbox + CommandReceipt
  -> one UoW
```

Semantic reuse outcomes used in WP1-A are:

```text
repository_registration_reused
repository_snapshot_reused
repository_inspection_reused
```

## 0.3 Command Envelope Limits

```text
MAX_RESULT_ENVELOPE_BYTES = 16 * 1024
MAX_NORMALIZED_REQUEST_BYTES = 64 * 1024
MAX_RELATED_RESULT_REFS = 64
MAX_RESULT_REF_BYTES = 300
MAX_OUTCOME_KIND_BYTES = 80
MAX_RESULT_TYPE_BYTES = 100
MAX_RESULT_ID_BYTES = 200
MAX_RESULT_IDENTITY_BYTES = 200
MAX_COMMITTED_STATE_BYTES = 80
```

`CommandResultEnvelope` permits only:

```text
outcome_kind
primary_result_type
primary_result_id
primary_result_identity
related_result_refs
committed_state
```

All strings are measured in UTF-8 bytes. Control characters, non-JSON-native values, arbitrary metadata, file contents, raw repository paths, credentials, and exception stacks are rejected.

## 0.4 Repository Observation Contract

WP1-A accepts only absolute API-container paths. Relative paths fail with `RepositoryPathMustBeAbsolute`; the server never guesses an allowed root.

Every Git subprocess uses an argv list, no shell, no network-capable command, and this deterministic read-only environment:

```text
GIT_OPTIONAL_LOCKS=0
GIT_TERMINAL_PROMPT=0
LC_ALL=C
LANG=C
```

Commands use `git --no-optional-locks ...` where supported. A Repository must have a resolvable `HEAD`; an empty/unborn Repository fails with `RepositoryHeadUnavailable`.

Git paths are parsed from NUL-delimited porcelain/ls-tree bytes and must decode as strict UTF-8. Unsupported non-UTF-8 paths fail closed with `RepositorySnapshotInspectionBundleUnsupportedEntry`.

Rename/copy normalization is fixed:

```text
rename old -> new:
  old path = deletion
  new path = tracked_overlay(original_relative_path=old)

copy old -> new:
  old path remains
  new path = tracked_overlay(original_relative_path=old)
```

Regular files are read with `open`/`O_NOFOLLOW` plus `fstat`; symlinks are read with `lstat -> readlink -> lstat` and hashed over normalized link-target bytes.

## 0.5 Dirty Snapshot Capture Guarantee

WP1-A does not claim a filesystem-level globally atomic snapshot against arbitrary external writers. It guarantees that each captured Bundle entry matches its recorded bytes and stable metadata, and it fails closed on detectable concurrent changes.

Required protocol:

```text
probe_before
-> per path lstat before
-> open/read/fstat or lstat/readlink/lstat
-> validate inode/type/size/mtime_ns/ctime_ns stability
-> write content-addressed blobs
-> probe_after
-> lstat every captured path again
-> require same HEAD, tree, normalized status identity, and stat tokens
-> otherwise RepositorySnapshotCaptureConflict and no Snapshot row
```

`submodule_presence` and `lfs_pointer_presence` are computed from the captured Git tree/status and persisted as real observations.

---

## 1. Planned File Structure

### Shared command and persistence foundation

```text
src/labpilot/common/commands.py
    Server-authoritative actor/installation context and command metadata.

src/labpilot/db/model_registry.py
    Single import root for all SQLAlchemy models used by runtime and Alembic.

src/labpilot/db/idempotency_models.py
    CommandReceipt ORM row only.

src/labpilot/db/idempotency.py
    Canonical request/result envelopes, receipt repository, conflict handling,
    rollback-and-winner-reload executor.
```

### Experiments-owned Project foundation

```text
src/labpilot/experiments/__init__.py
src/labpilot/experiments/models.py
    ProjectRow.

src/labpilot/experiments/project_types.py
    Project status, commands, immutable results, identity functions.

src/labpilot/experiments/project_repository.py
    Project query/lock/persist operations.

src/labpilot/experiments/project_service.py
    CreateProject and ArchiveProject public application service.

src/labpilot/experiments/project_bindings.py
    Internal set/clear primary Repository pointer handlers.

src/labpilot/experiments/events.py
    Project event schema registration and envelope builders.
```

### Context-owned Repository foundation

```text
src/labpilot/context/__init__.py
src/labpilot/context/models.py
    RepositoryRegistration, Snapshot, InspectionBundle, BundleEntry,
    SnapshotFreshness, InspectionRun/Result, Evidence, EvidenceManifest rows.

src/labpilot/context/repository_types.py
    Enums, command/result dataclasses, canonical manifest entry types.

src/labpilot/context/repository_identities.py
    Registration, state, snapshot, bundle, evidence, and manifest hashes.

src/labpilot/context/repository_paths.py
    realpath, allowed-root containment, relative-path and symlink safety.

src/labpilot/context/git_adapter.py
    Bounded subprocess adapter and immutable Git observation primitives.

src/labpilot/context/content_store.py
    Content-addressed blob write/read/verify operations.

src/labpilot/context/snapshot_reader.py
    Clean Git-object reader and dirty base-plus-bundle overlay reader.

src/labpilot/context/inspection_registry.py
    Versioned required/optional deterministic inspector registry.

src/labpilot/context/inspectors/file_inventory.py
src/labpilot/context/inspectors/project_manifests.py
src/labpilot/context/inspectors/python_static_signals.py
    Initial bounded inspector implementations.

src/labpilot/context/repository_repository.py
    Context persistence operations and row locks.

src/labpilot/context/repository_service.py
    Registration, availability, revoke, capture, freshness, inspection commands.

src/labpilot/context/events.py
    Repository event schemas and envelope builders.
```

### API composition

```text
src/labpilot/api/actor.py
    Server-configured local actor provider; no client actor headers.

src/labpilot/api/dependencies.py
    WP1-A service container and FastAPI dependency accessors.

src/labpilot/api/errors.py
    Structured application error -> HTTP mapping.

src/labpilot/api/schemas/projects.py
src/labpilot/api/schemas/repositories.py
    Request/response DTOs.

src/labpilot/api/routers/projects.py
src/labpilot/api/routers/repositories.py
    Command and query routes.
```

### Migrations

```text
migrations/versions/0003_command_receipts.py
migrations/versions/0004_projects.py
migrations/versions/0005_repository_registrations.py
migrations/versions/0006_repository_snapshots.py
migrations/versions/0007_repository_inspections.py
```

### Tests

```text
tests/unit/common/test_commands.py
tests/unit/db/test_idempotency.py
tests/unit/experiments/test_project_types.py
tests/unit/context/test_repository_paths.py
tests/unit/context/test_repository_identities.py
tests/unit/context/test_git_adapter.py
tests/unit/context/test_content_store.py
tests/unit/context/test_snapshot_reader.py
tests/unit/context/test_inspection_registry.py
tests/unit/context/inspectors/test_file_inventory.py
tests/unit/context/inspectors/test_project_manifests.py
tests/unit/context/inspectors/test_python_static_signals.py

tests/integration/db/test_command_receipts.py
tests/integration/experiments/test_project_service.py
tests/integration/context/test_repository_registration.py
tests/integration/context/test_repository_snapshot.py
tests/integration/context/test_repository_freshness.py
tests/integration/context/test_repository_inspection.py
tests/integration/context/test_repository_constraints.py

tests/integration/api/test_projects.py
tests/integration/api/test_repositories.py

tests/architecture/test_model_registry.py
```

---

### Task 0: Freeze the Approved WP1-A Planning Baseline

**Files:**
- Create: `docs/labpilot/implementation/02-wp1-domain-context-overall-design-v0.5.md`
- Create: `docs/labpilot/implementation/03-wp1-a-project-repository-implementation-plan-v0.2.md`
- Create: `docs/labpilot/implementation/wp1-a-v0.2.dependencies.sha256`
- Create: `docs/labpilot/implementation/wp1-a-v0.2-dependency-verification.txt`
- Modify: `DOCUMENT-STATUS.md`

**Interfaces:**
- Consumes: `main@769e8fd`, approved design v0.5, the separately approved exact-byte v0.2 plan artifact, frozen architecture documents, WP0 phase-specific dependency verification.
- Produces: immutable planning baseline and exact dependency verification command for all WP1-A Tasks.

- [ ] **Step 1: Verify branch and working tree before creating planning records**

Run:

```bash
git status --short
git branch --show-current
git rev-parse HEAD
git rev-parse HEAD^{tree}
git log --oneline --decorate -8
```

Expected:

```text
branch = feature/wp1-domain-context
working tree contains only the approved design/plan files intended for this Task
base ancestry includes main@769e8fd
```

- [ ] **Step 2: Install the approved design and implementation plan at the exact repository paths**

The installed design must have SHA-256 `82bcceac0abb8f9176f827b421d567d8d8fb427037e3b9c21d963df6d9f1c003`; the only delta from the originally approved artifact is the non-semantic section 0.1 numbering correction `14 -> 6`. Its header must contain:

```text
Version: v0.5
Status: Approved Design Input
Target branch: feature/wp1-domain-context
Baseline: main@769e8fd
```

Before Task 0 may begin, the reviewed v0.2 artifact must be reissued with:

```text
Version: v0.2
Status: Approved Implementation Plan
```

and the user must approve that exact byte sequence and SHA-256. If the installed file still says `Draft for Approval` or `Draft for Review`, stop without generating a dependency Manifest. The approved plan header must also retain the Goal, Architecture, Tech Stack, Global Constraints, and v0.2 closed revision set.

- [ ] **Step 3: Create the dependency manifest from exact planning inputs**

Run:

```bash
sha256sum \
  docs/labpilot/01-v1-scope-and-acceptance-v0.2-frozen.md \
  docs/labpilot/architecture/02-experiment-domain-v0.2-frozen.md \
  docs/labpilot/architecture/04-context-and-repository-v0.2-frozen.md \
  docs/labpilot/architecture/05-tools-policy-and-workspace-v0.2-frozen.md \
  docs/labpilot/architecture/06-workflow-and-executors-v0.2-frozen.md \
  docs/labpilot/architecture/07-results-validation-and-agentops-v0.2-frozen.md \
  docs/labpilot/architecture/08-agent-abstention-and-progress-control-v0.2.md \
  docs/labpilot/implementation/01-master-implementation-roadmap-v0.2.3-frozen.md \
  docs/labpilot/implementation/02-wp1-domain-context-overall-design-v0.5.md \
  docs/labpilot/implementation/03-wp1-a-project-repository-implementation-plan-v0.2.md \
  > docs/labpilot/implementation/wp1-a-v0.2.dependencies.sha256
```

- [ ] **Step 4: Verify the manifest with the existing verifier**

Run:

```bash
bash scripts/verify_architecture_dependencies.sh \
  docs/labpilot/implementation/wp1-a-v0.2.dependencies.sha256 \
  | tee docs/labpilot/implementation/wp1-a-v0.2-dependency-verification.txt
```

Expected: every entry reports `OK`; the command exits `0`.

- [ ] **Step 5: Record the approved document status without modifying WP0 records**

Add to `DOCUMENT-STATUS.md`:

```markdown
## WP1

- `02-wp1-domain-context-overall-design-v0.5.md`: Approved Design Input
- `03-wp1-a-project-repository-implementation-plan-v0.2.md`: Approved Implementation Plan
- WP1-A implementation: Not started
```

- [ ] **Step 6: Run documentation integrity checks**

Run:

```bash
test "$(
  sed -n 's/^Status: //p' \
    docs/labpilot/implementation/03-wp1-a-project-repository-implementation-plan-v0.2.md \
    | head -n 1
)" = "Approved Implementation Plan"

! rg -n 'T[B]D|T[O]DO|F[I]XME' \
  docs/labpilot/implementation/02-wp1-domain-context-overall-design-v0.5.md \
  docs/labpilot/implementation/03-wp1-a-project-repository-implementation-plan-v0.2.md

git diff --check
```

Expected: the status assertion succeeds, the placeholder scan returns no matches without self-matching its own pattern, and `git diff --check` exits `0`.

- [ ] **Step 7: Commit the planning baseline**

```bash
git add \
  DOCUMENT-STATUS.md \
  docs/labpilot/implementation/02-wp1-domain-context-overall-design-v0.5.md \
  docs/labpilot/implementation/03-wp1-a-project-repository-implementation-plan-v0.2.md \
  docs/labpilot/implementation/wp1-a-v0.2.dependencies.sha256 \
  docs/labpilot/implementation/wp1-a-v0.2-dependency-verification.txt

git commit -m "docs: freeze approved WP1-A implementation plan"
```

**Review gate:** Stop. Record the complete commit hash, tree hash, approved-plan SHA-256, and dependency Manifest SHA-256. Do not start Task 1 until the Task 0 commit is independently reviewed.

---

### Task 1: Add the Model Registry and Generic CommandReceipt Schema

**Files:**
- Create: `src/labpilot/experiments/__init__.py`
- Create: `src/labpilot/experiments/models.py`
- Create: `src/labpilot/context/__init__.py`
- Create: `src/labpilot/context/models.py`
- Create: `src/labpilot/db/idempotency_models.py`
- Create: `src/labpilot/db/model_registry.py`
- Create: `migrations/versions/0003_command_receipts.py`
- Modify: `migrations/env.py:1-12`
- Modify: `tests/integration/db/test_migrations.py:1-35`
- Create: `tests/architecture/test_model_registry.py`
- Create: `tests/integration/db/test_command_receipts.py`

**Interfaces:**
- Consumes: `Base`, Alembic `target_metadata`, PostgreSQL JSONB and UUID.
- Produces: `CommandReceiptRow`, central model registration root, migration head `0003_command_receipts`.

- [ ] **Step 1: Write a failing architecture test for a single model registration root**

```python
from labpilot.db.base import Base
import labpilot.db.model_registry  # noqa: F401


def test_model_registry_registers_command_receipts() -> None:
    assert "command_receipts" in Base.metadata.tables
```

Run:

```bash
pytest tests/architecture/test_model_registry.py -v
```

Expected: FAIL because `labpilot.db.model_registry` does not exist.

- [ ] **Step 2: Write failing PostgreSQL schema tests for CommandReceipt**

Test exact columns and unique key:

```python
assert {
    "command_receipt_id",
    "idempotency_scope_identity",
    "command_name",
    "command_version",
    "idempotency_key",
    "normalized_request_hash",
    "outcome_kind",
    "primary_result_type",
    "primary_result_id",
    "result_envelope_schema_version",
    "result_envelope_json",
    "result_envelope_hash",
    "created_at",
} <= columns
```

Also insert two rows with the same:

```text
scope + command_name + command_version + idempotency_key
```

and assert the second insert raises `IntegrityError`.

Run:

```bash
pytest tests/integration/db/test_command_receipts.py -v
```

Expected: FAIL because the table does not exist.

- [ ] **Step 3: Implement `CommandReceiptRow`**

Create `src/labpilot/db/idempotency_models.py` with this contract:

```python
class CommandReceiptRow(Base):
    __tablename__ = "command_receipts"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_scope_identity",
            "command_name",
            "command_version",
            "idempotency_key",
            name="uq_command_receipt_idempotency_key",
        ),
        CheckConstraint("command_version > 0", name="ck_command_receipt_version_positive"),
        CheckConstraint(
            "result_envelope_schema_version > 0",
            name="ck_command_receipt_envelope_version_positive",
        ),
    )

    command_receipt_id: Mapped[UUID]
    idempotency_scope_identity: Mapped[str]  # String(64)
    command_name: Mapped[str]                # String(120)
    command_version: Mapped[int]
    idempotency_key: Mapped[str]             # String(200)
    normalized_request_hash: Mapped[str]     # String(64)
    outcome_kind: Mapped[str]                # String(80)
    primary_result_type: Mapped[str]         # String(100)
    primary_result_id: Mapped[str]           # String(200)
    result_envelope_schema_version: Mapped[int]
    result_envelope_json: Mapped[dict[str, object]]  # JSONB
    result_envelope_hash: Mapped[str]        # String(64)
    created_at: Mapped[datetime]
```

Do not add `project_id` or a Project foreign key.

- [ ] **Step 4: Create the central model registry**

`src/labpilot/db/model_registry.py` must import modules, not individual rows:

```python
import labpilot.context.models  # noqa: F401
import labpilot.db.idempotency_models  # noqa: F401
import labpilot.experiments.models  # noqa: F401
import labpilot.observability.models  # noqa: F401
```

Replace the direct observability import in `migrations/env.py` with:

```python
import labpilot.db.model_registry  # noqa: F401
```

- [ ] **Step 5: Implement migration `0003_command_receipts`**

Requirements:

```text
down_revision = 0002_runtime_events_outbox
PostgreSQL UUID primary key
JSONB result envelope
clock_timestamp() created_at
named unique and CHECK constraints matching ORM metadata
complete downgrade
```

- [ ] **Step 6: Update migration-head assertions**

Change the expected migration version in `tests/integration/db/test_migrations.py` to:

```python
assert version == "0003_command_receipts"
```

and assert all three migration files exist.

- [ ] **Step 7: Run focused tests**

```bash
pytest tests/architecture/test_model_registry.py -v
pytest tests/integration/db/test_command_receipts.py -v
pytest tests/integration/db/test_migrations.py -v
```

Expected: PASS.

- [ ] **Step 8: Run static checks for the new foundation**

```bash
ruff check src/labpilot/db src/labpilot/context src/labpilot/experiments \
  tests/architecture/test_model_registry.py \
  tests/integration/db/test_command_receipts.py
mypy src/labpilot/db src/labpilot/context src/labpilot/experiments
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add migrations src/labpilot/db src/labpilot/context src/labpilot/experiments \
  tests/architecture/test_model_registry.py \
  tests/integration/db/test_command_receipts.py \
  tests/integration/db/test_migrations.py

git commit -m "feat: add command receipt persistence foundation"
```

**Review gate:** Confirm metadata registration does not depend on test import order and `downgrade 0003 -> 0002 -> upgrade head` succeeds.

---

### Task 2: Implement Canonical Command Envelopes and Idempotent Execution

**Files:**
- Create: `src/labpilot/common/commands.py`
- Create: `src/labpilot/db/idempotency.py`
- Create: `tests/unit/common/test_commands.py`
- Create: `tests/unit/db/test_idempotency.py`
- Extend: `tests/integration/db/test_command_receipts.py`

**Interfaces:**
- Consumes: `UnitOfWork`, `Database.session`, `CommandReceiptRow`, Canonical JSON V1, `namespaced_sha256()`.
- Produces:
  - `CommandActor`
  - `CommandContext`
  - `CommandIdentity`
  - `CommandResultEnvelope`
  - generic `PreparedT` prepare result
  - `CommandReceiptRepository`
  - `execute_idempotent_command()`

- [ ] **Step 1: Write failing unit tests for server-authoritative command context**

```python
actor = CommandActor(actor_type="local_user", actor_id="air")
context = CommandContext(
    installation_identity="installation-1",
    actor=actor,
    interaction_correlation_id="interaction-1",
)
assert context.actor.actor_id == "air"
```

Also assert empty installation identity, empty actor type, and actor IDs over 200 UTF-8 bytes are rejected.

Run:

```bash
pytest tests/unit/common/test_commands.py -v
```

Expected: FAIL because the types do not exist.

- [ ] **Step 2: Define the immutable command contracts**

Use frozen dataclasses:

```python
@dataclass(frozen=True, slots=True)
class CommandActor:
    actor_type: str
    actor_id: str | None

@dataclass(frozen=True, slots=True)
class CommandContext:
    installation_identity: str
    actor: CommandActor
    interaction_correlation_id: str

@dataclass(frozen=True, slots=True)
class CommandIdentity:
    scope_identity: str
    command_name: str
    command_version: int
    idempotency_key: str
    normalized_request_hash: str

@dataclass(frozen=True, slots=True)
class CommandResultEnvelope:
    outcome_kind: str
    primary_result_type: str
    primary_result_id: str
    primary_result_identity: str | None
    related_result_refs: tuple[str, ...]
    committed_state: str
```

`CommandResultEnvelope.to_json()` must return only the six allowed fields, sort `related_result_refs` before hashing, reject control characters/non-JSON-native values, and enforce the limits in section 0.3. `normalized_request` must be Canonical JSON no larger than `MAX_NORMALIZED_REQUEST_BYTES` before hashing.

- [ ] **Step 3: Write failing hash tests**

Verify exact namespaces:

```text
labpilot-idempotency-scope:v1
labpilot-command-request:v1
labpilot-command-result-envelope:v1
```

Two request dictionaries with different key order must have the same request hash. Changing `expected_row_version` must change the hash. The idempotency key and request time must not enter the hash.

- [ ] **Step 4: Implement scope/request/result preparation**

Create functions with exact signatures:

```python
def prepare_create_scope(context: CommandContext) -> str: ...

def prepare_project_scope(
    context: CommandContext,
    *,
    project_id: UUID,
) -> str: ...

def prepare_command_identity(
    *,
    scope_identity: str,
    command_name: str,
    command_version: int,
    idempotency_key: str,
    normalized_request: CanonicalObject,
) -> CommandIdentity: ...

def prepare_result_envelope(
    envelope: CommandResultEnvelope,
) -> tuple[dict[str, object], str]: ...
```

Reject idempotency keys that are empty, exceed 200 UTF-8 bytes, or contain control characters. Reject normalized requests over 64 KiB before hashing. `prepare_result_envelope()` enforces every field/count/byte limit from section 0.3 and rejects any extra field.

- [ ] **Step 5: Write failing repository tests for receipt integrity**

Test that `CommandReceiptRepository.add()` rejects:

```text
result envelope hash mismatch
outcome_kind mismatch
primary_result_type mismatch
primary_result_id mismatch
Envelope over 16 KiB
more than 64 related refs
single ref over 300 bytes
control characters
non-JSON-native values
```

Also prove a normalized request over 64 KiB is rejected before the operation or prepare callback can run.

These are application invariants; do not add a fake SQL CHECK for Canonical JSON hashing.

- [ ] **Step 6: Implement `CommandReceiptRepository`**

Required methods:

```python
async def get(
    self,
    session: AsyncSession,
    identity: CommandIdentity,
) -> CommandReceiptRow | None: ...

async def add(
    self,
    uow: UnitOfWork,
    *,
    identity: CommandIdentity,
    envelope: CommandResultEnvelope,
) -> CommandReceiptRow: ...
```

`add()` computes the envelope JSON/hash itself and flushes the row without committing.

- [ ] **Step 7: Write the failing concurrency test**

Start two tasks with the same identity. Each `prepare` returns a distinct immutable marker outside any transaction; each operation attempts to insert a distinct probe row plus Receipt. Assert:

```text
Receipt hit never calls prepare
prepare runs with no active transaction
one transaction commits
one transaction rolls back its probe row
loser opens a new Session
loser returns the winner Result Envelope with replayed=True
only one Receipt exists
```

Add a replay test in which the first command succeeds, the external resource is then made unavailable, and the same key still replays without invoking `prepare`.

- [ ] **Step 8: Implement rollback-and-winner-reload**

Use this public contract:

```python
@dataclass(frozen=True, slots=True)
class IdempotentCommandOutcome:
    envelope: CommandResultEnvelope
    replayed: bool

PreparedT = TypeVar("PreparedT")

async def execute_idempotent_command(
    *,
    session_context_factory: SessionContextFactory,
    identity: CommandIdentity,
    prepare: Callable[[], Awaitable[PreparedT]],
    operation: Callable[
        [UnitOfWork, PreparedT],
        Awaitable[CommandResultEnvelope],
    ],
) -> IdempotentCommandOutcome: ...
```

Algorithm:

```text
short read-only Session: read committed Receipt
if hit: compare request hash and replay/conflict; never call prepare
if miss: close Session and run prepare with no open transaction
optionally recheck Receipt in a second short read-only Session
open UoW, execute operation(uow, prepared), insert Receipt, commit
if uq_command_receipt_idempotency_key wins elsewhere:
    allow the complete loser UoW to roll back
    open a new read-only Session
    read winner
    compare request hash
    replay or raise IdempotencyKeyConflict
```

Provide `async_noop_prepare() -> None` for commands with no external preparation. Only classify an `IntegrityError` as a Receipt race when PostgreSQL reports the exact named constraint; re-raise every other integrity error.


- [ ] **Step 9: Run tests**

```bash
pytest tests/unit/common/test_commands.py -v
pytest tests/unit/db/test_idempotency.py -v
pytest tests/integration/db/test_command_receipts.py -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/labpilot/common/commands.py src/labpilot/db/idempotency.py \
  tests/unit/common/test_commands.py tests/unit/db/test_idempotency.py \
  tests/integration/db/test_command_receipts.py

git commit -m "feat: add transactional command idempotency"
```

**Review gate:** Verify a Receipt hit returns before `prepare`, external preparation runs with no transaction, and replay never reconstructs a result from a mutable domain row.

---

### Task 3: Add the Project Domain, Migration, Events, and Application Service

**Files:**
- Modify: `src/labpilot/experiments/models.py`
- Create: `src/labpilot/experiments/project_types.py`
- Create: `src/labpilot/experiments/project_repository.py`
- Create: `src/labpilot/experiments/project_service.py`
- Create: `src/labpilot/experiments/events.py`
- Create: `migrations/versions/0004_projects.py`
- Create: `tests/unit/experiments/test_project_types.py`
- Create: `tests/integration/experiments/test_project_service.py`
- Modify: `tests/integration/db/test_migrations.py`

**Interfaces:**
- Consumes: command idempotency, UoW, RuntimeEvent/Outbox, Canonical JSON/hash.
- Produces: `ProjectRow`, `CreateProject`, `ArchiveProject`, `ProjectApplicationService`, `ProjectRepository`.

- [ ] **Step 1: Write failing Project identity and validation tests**

```python
command = CreateProject(
    name="Causal Diffusion",
    description="Counterfactual image experiments",
    artifact_root_ref="artifact-root:projects/causal-diffusion",
)
assert command.name == "Causal Diffusion"
```

Test rejection of blank names, names over 200 bytes, descriptions over 8 KiB, and artifact refs over 1000 bytes.

Define Project identity payload:

```python
{
    "project_id": str(project_id),
    "identity_schema_version": 1,
}
```

Namespace:

```text
labpilot-project:v1
```

- [ ] **Step 2: Define Project ORM schema and migration**

`ProjectRow` fields:

```text
project_id UUID PK
project_identity String(64) UNIQUE NOT NULL
name String(200) NOT NULL
description Text NULL
status String(30) NOT NULL CHECK active|archived
primary_repository_registration_id UUID NULL  # FK added in 0005
artifact_root_ref String(1000) NOT NULL
created_at timestamptz clock_timestamp()
updated_at timestamptz clock_timestamp()
row_version bigint NOT NULL default 1 CHECK > 0
```

Migration:

```text
revision = 0004_projects
down_revision = 0003_command_receipts
```

- [ ] **Step 3: Write failing service tests for atomic Project creation**

Assert `CreateProject` commits exactly:

```text
ProjectRow(status=active,row_version=1)
RuntimeEvent(project.created, aggregate_type=project, aggregate_id=project_id)
OutboxRecord
CommandReceipt(outcome_kind=project_created)
```

Force an event payload error and assert none of the four rows remain.

- [ ] **Step 4: Register bounded Project event payload schemas**

`project.created` v1 payload:

```python
{
    "project_id": str(project_id),
    "project_identity": project_identity,
    "status": "active",
}
```

`project.archived` v1 payload:

```python
{
    "project_id": str(project_id),
    "project_identity": project_identity,
    "previous_status": "active",
    "status": "archived",
    "row_version": row_version,
}
```

Do not include name, description, or artifact path in Event payloads.

- [ ] **Step 5: Implement Project repository methods**

```python
async def get(self, session: AsyncSession, project_id: UUID) -> ProjectRow | None: ...

async def get_for_update(
    self,
    session: AsyncSession,
    project_id: UUID,
) -> ProjectRow | None: ...

async def add(self, uow: UnitOfWork, row: ProjectRow) -> None: ...
```

`get_for_update()` must use `SELECT ... FOR UPDATE`.

- [ ] **Step 6: Implement `ProjectApplicationService.create_project()`**

Exact signature:

```python
async def create_project(
    self,
    *,
    context: CommandContext,
    idempotency_key: str,
    command: CreateProject,
) -> IdempotentCommandOutcome: ...
```

Call the shared executor with `prepare=async_noop_prepare`. Inside `operation(uow, prepared_none)`:

```text
generate project_id server-side
compute project_identity
add ProjectRow
append project.created RuntimeEvent + Outbox
return bounded CommandResultEnvelope
receipt inserted by executor
commit one UoW
```

- [ ] **Step 7: Implement `archive_project()` with optimistic concurrency**

Command:

```python
@dataclass(frozen=True, slots=True)
class ArchiveProject:
    project_id: UUID
    expected_row_version: int
```

Rules:

```text
active + expected version -> archived, row_version + 1
archived + same idempotency key -> receipt replay
archived + new key -> ProjectStateConflict
wrong expected version -> ProjectVersionConflict
```

- [ ] **Step 8: Run focused tests**

```bash
pytest tests/unit/experiments/test_project_types.py -v
pytest tests/integration/experiments/test_project_service.py -v
pytest tests/integration/db/test_migrations.py -v
```

Expected: PASS with migration head `0004_projects`.

- [ ] **Step 9: Commit**

```bash
git add src/labpilot/experiments migrations/versions/0004_projects.py \
  tests/unit/experiments tests/integration/experiments \
  tests/integration/db/test_migrations.py

git commit -m "feat: add project domain foundation"
```

**Review gate:** Confirm Project has no WP1-B placeholder FKs and no Router exists yet.

---

### Task 4: Add Server-Configured Actor Context, API Composition, and Project Routes

**Files:**
- Create: `src/labpilot/api/actor.py`
- Create: `src/labpilot/api/dependencies.py`
- Create: `src/labpilot/api/errors.py`
- Create: `src/labpilot/api/schemas/__init__.py`
- Create: `src/labpilot/api/schemas/projects.py`
- Create: `src/labpilot/api/routers/projects.py`
- Modify: `src/labpilot/api/app.py:1-44`
- Modify: `src/labpilot/main.py:1-12`
- Modify: `src/labpilot/settings.py:1-28`
- Modify: `tests/unit/test_settings.py`
- Modify: `tests/unit/api/test_health.py`
- Modify: `tests/integration/api/test_readiness.py`
- Create: `tests/integration/api/test_projects.py`

**Interfaces:**
- Consumes: `ProjectApplicationService`, `Database`, Settings, `CommandContext`.
- Produces: service container, server-authoritative local actor provider, `POST /projects`, `POST /projects/{id}/archive`, `GET /projects/{id}`.

- [ ] **Step 1: Write failing Settings tests**

Add server-owned settings:

```python
installation_identity: str = Field(min_length=1, max_length=200)
local_actor_id: str = Field(min_length=1, max_length=200)
```

Environment variables:

```text
LABPILOT_INSTALLATION_IDENTITY
LABPILOT_LOCAL_ACTOR_ID
```

The API must not accept actor identity from request body or headers.

- [ ] **Step 2: Implement `ServerConfiguredActorProvider`**

```python
class ActorProvider(Protocol):
    async def get_command_context(self, request: Request) -> CommandContext: ...

class ServerConfiguredActorProvider:
    def __init__(self, installation_identity: str, actor_id: str) -> None: ...
```

Generate `interaction_correlation_id` server-side per request. Actor type is fixed to `local_user` in WP1-A.

- [ ] **Step 3: Write failing API tests**

Test:

```text
POST /projects without Idempotency-Key -> 422
POST /projects with valid input -> 201
same key and body -> 200 with replayed=true and same primary_result_id
same key and different body -> 409 IdempotencyKeyConflict
GET /projects/{id} -> current state
archive with expected_row_version -> 200
archive version conflict -> 409
unknown project -> 404
```

Use an injected test actor provider. Do not set actor headers.

- [ ] **Step 4: Define Project API schemas**

Requests:

```python
class CreateProjectRequest(BaseModel):
    name: str
    description: str | None = None
    artifact_root_ref: str

class ArchiveProjectRequest(BaseModel):
    expected_row_version: int = Field(gt=0)
```

HTTP mapping is fixed:

```text
first CreateProject -> 201
CreateProject Receipt replay -> 200
first ArchiveProject state update -> 200
ArchiveProject Receipt replay -> 200
```

Responses must include:

```text
project_id
project_identity
committed_state
replayed
```

The GET response additionally includes mutable Project fields and `row_version`.

- [ ] **Step 5: Implement structured error mapping**

Create an application error payload with nullable aggregate/state fields:

```python
class ErrorResponse(BaseModel):
    error_code: str
    human_message: str
    aggregate_type: str | None
    aggregate_id: str | None
    current_state: str | None
    expected_state: str | None
    retryable: bool
    details: dict[str, object]
```

Map:

```text
request/schema -> 422
not found -> 404
state/version/idempotency conflict -> 409
unconfigured service/infrastructure -> 503
unexpected invariant -> 500
```

- [ ] **Step 6: Add an explicit `ApplicationServices` container**

```python
@dataclass(frozen=True, slots=True)
class ApplicationServices:
    projects: ProjectApplicationService
```

`create_app()` signature becomes:

```python
def create_app(
    *,
    readiness_probe: ReadinessProbe | None = None,
    shutdown_callbacks: tuple[ShutdownCallback, ...] = (),
    services: ApplicationServices,
    actor_provider: ActorProvider,
) -> FastAPI: ...
```

Tests must construct the services explicitly. Update every existing `create_app()` call in `tests/unit/api/test_health.py` and `tests/integration/api/test_readiness.py` to inject a minimal Project service container and test actor provider. `main.py` builds the real Database, repository, idempotency executor dependencies, Project service, and server-configured actor provider.

- [ ] **Step 7: Implement Project Router**

Router may only:

```text
parse request
obtain CommandContext
call one ProjectApplicationService method
map result/error
```

It must not open a Session or call SQLAlchemy.

- [ ] **Step 8: Run tests**

```bash
pytest tests/unit/test_settings.py -v
pytest tests/integration/api/test_projects.py -v
pytest tests/unit/api/test_health.py tests/integration/api/test_readiness.py -v
```

Expected: PASS; health/readiness behavior remains unchanged.

- [ ] **Step 9: Commit**

```bash
git add src/labpilot/api src/labpilot/main.py src/labpilot/settings.py \
  tests/unit/test_settings.py tests/unit/api/test_health.py \
  tests/integration/api/test_readiness.py tests/integration/api/test_projects.py

git commit -m "feat: expose idempotent project APIs"
```

**Review gate:** Verify actor identity is server-configured and no request field/header can override it.

---

### Task 5: Add Repository Settings, Secure Path Resolution, and Bounded Git Adapter

**Files:**
- Create: `src/labpilot/context/repository_types.py`
- Create: `src/labpilot/context/repository_paths.py`
- Create: `src/labpilot/context/git_adapter.py`
- Modify: `src/labpilot/settings.py`
- Modify: `.env.example`
- Modify: `compose.yaml`
- Modify: `README.md`
- Modify: `tests/unit/test_settings.py`
- Create: `tests/unit/context/test_repository_paths.py`
- Create: `tests/unit/context/test_git_adapter.py`

**Interfaces:**
- Consumes: server settings, local filesystem, Git executable.
- Produces: safe canonical repository root, bounded Git command execution, immutable `RepositoryProbe` and status-entry types.

- [ ] **Step 1: Add explicit repository settings and tests**

Fields:

```python
repository_allowed_roots: tuple[Path, ...]
repository_content_store_root: Path
repository_git_timeout_seconds: int = Field(default=15, ge=1, le=120)
repository_snapshot_max_entries: int = Field(default=10_000, ge=1)
repository_snapshot_max_file_bytes: int = Field(default=16 * 1024 * 1024, ge=1)
repository_snapshot_max_total_bytes: int = Field(default=256 * 1024 * 1024, ge=1)
repository_inspection_max_files: int = Field(default=50_000, ge=1)
repository_inspection_max_total_bytes: int = Field(default=512 * 1024 * 1024, ge=1)
```

Reject an empty `repository_allowed_roots` tuple outside tests. Normalize all configured roots with `resolve(strict=True)` during service construction, not at module import.

Update `.env.example` with server-owned values:

```text
LABPILOT_INSTALLATION_IDENTITY=local-development
LABPILOT_LOCAL_ACTOR_ID=local-user
LABPILOT_REPOSITORY_ALLOWED_ROOTS=["/repositories"]
LABPILOT_REPOSITORY_CONTENT_STORE_ROOT=/var/lib/labpilot/content
```

Update `compose.yaml` so API, migration, coordinator, and projection services receive every required Settings value, including installation, actor, allowed roots, and content-store root. Only the API receives a read-only repository-root bind mount and a read-write named content-store volume:

```yaml
volumes:
  - ${LABPILOT_REPOSITORY_HOST_ROOT:-.}:/repositories:ro
  - labpilot_content:/var/lib/labpilot/content
```

Declare `labpilot_content` under top-level volumes. Document that the mounted repository root is read-only and Git commands are observation-only.

- [ ] **Step 2: Write path-escape tests**

Cover:

```text
path inside allowed root -> accepted
relative path -> RepositoryPathMustBeAbsolute
path outside allowed root -> RepositoryPathOutsideAllowedRoot
symlink inside allowed root pointing outside -> RepositorySymlinkEscape
nonexistent path -> RepositoryUnavailable
file instead of directory -> RepositoryNotDirectory
```

- [ ] **Step 3: Implement safe path resolution**

```python
def resolve_repository_root(
    user_path: Path,
    *,
    allowed_roots: tuple[Path, ...],
) -> Path: ...
```

Reject `user_path` unless `user_path.is_absolute()`; WP1-A never selects a base root by allowed-root order. Then use `Path.resolve(strict=True)` and `Path.is_relative_to()` on resolved paths. Never use string-prefix containment.

- [ ] **Step 4: Define immutable Git observation types**

At minimum:

```python
@dataclass(frozen=True, slots=True)
class GitStatusEntry:
    relative_path: str
    original_relative_path: str | None
    change_kind: str
    index_status: str
    worktree_status: str
    file_type: str
    file_mode: str
    head_blob_oid: str | None
    index_blob_oid: str | None
    is_untracked: bool

@dataclass(frozen=True, slots=True)
class RepositoryProbe:
    canonical_root_path: str
    head_commit: str
    current_branch: str | None
    is_detached_head: bool
    tracked_tree_identity: str
    status_entries: tuple[GitStatusEntry, ...]
    remote_origin_sanitized: str | None
    default_branch: str | None
    observation_profile_version: str
```

- [ ] **Step 5: Write Git adapter tests using temporary repositories**

Create repositories with `git init`, at least one local commit, staged modifications, unstaged modifications, deletion, mode change, symlink, untracked files, tracked rename, staged rename plus unstaged modification, and copy detection. Assert parsed status entries distinguish index/worktree state and normalize rename/copy exactly as section 0.4 requires.

Run the same rename/copy cases after setting the Repository-local configuration to each of:

```text
status.renames=false
status.renames=true
status.renames=copies
```

LabPilot must produce the same normalized observation in every case because the adapter supplies its own versioned status configuration. Assert `RepositoryProbe.observation_profile_version == "wp1-a-git-observation:v1"`.

Also test an unborn Repository with no commit -> `RepositoryHeadUnavailable`, a strict non-UTF-8 path -> `RepositorySnapshotInspectionBundleUnsupportedEntry`, and symlink metadata parsing without following the target.

The test must also prove:

```text
command uses argv list, not shell=True
command timeout becomes RepositoryGitCommandTimeout
stderr is bounded and sanitized
remote URL credentials are removed
```

- [ ] **Step 6: Implement `GitAdapter`**

Required commands:

```text
git rev-parse --show-toplevel
git rev-parse --verify HEAD
git symbolic-ref --quiet --short HEAD
git rev-parse HEAD^{tree}
git --no-optional-locks \
  -c status.renames=copies \
  -c status.renameLimit=1000 \
  status --porcelain=v2 -z --untracked-files=all
git config --get remote.origin.url
git symbolic-ref refs/remotes/origin/HEAD
git ls-tree -rz --full-tree <commit>
git cat-file --batch
```

Use `asyncio.create_subprocess_exec`, fixed environment, timeout, bounded stdout/stderr, and no network-capable Git commands. Set `GIT_OPTIONAL_LOCKS=0`, `GIT_TERMINAL_PROMPT=0`, `LC_ALL=C`, and `LANG=C`; use `git --no-optional-locks` where supported. For status observation, always override Repository/user configuration with `-c status.renames=copies -c status.renameLimit=1000`. Treat that argv/configuration set as the immutable profile `wp1-a-git-observation:v1`; return it in `RepositoryProbe.observation_profile_version` and persist it as the Snapshot Bundle `capture_tool_version` and Freshness `probe_version`. Changing any command/configuration value requires a new observation profile version. Parse NUL-delimited path bytes with strict UTF-8. Tests must verify no `.git/index.lock` or other Git write artifact is created on a read-only observation path.

- [ ] **Step 7: Run focused tests**

```bash
pytest tests/unit/context/test_repository_paths.py -v
pytest tests/unit/context/test_git_adapter.py -v
pytest tests/unit/test_settings.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/labpilot/context/repository_types.py \
  src/labpilot/context/repository_paths.py \
  src/labpilot/context/git_adapter.py \
  src/labpilot/settings.py .env.example compose.yaml README.md \
  tests/unit/context tests/unit/test_settings.py

git commit -m "feat: add secure repository observation primitives"
```

**Review gate:** Inspect subprocess argv/environment and prove no user-controlled text reaches a shell, no optional Git lock is created, relative paths are rejected, and an unborn HEAD fails structurally.

---

### Task 6: Add Canonical Repository Identities and the Content-Addressed Blob Store

**Files:**
- Create: `src/labpilot/context/repository_identities.py`
- Create: `src/labpilot/context/content_store.py`
- Create: `tests/unit/context/test_repository_identities.py`
- Create: `tests/unit/context/test_content_store.py`

**Interfaces:**
- Consumes: `RepositoryProbe`, Canonical JSON V1, namespaced SHA-256, filesystem root.
- Produces: canonical manifest hashes, registration/state/snapshot/bundle identities, `ContentAddressedStore`.

- [ ] **Step 1: Write exact identity tests from the frozen schemas**

Namespaces:

```text
labpilot-repository-registration:v1
labpilot-dirty-worktree-manifest:v1
labpilot-untracked-manifest:v1
labpilot-repository-state:v1
labpilot-repository-snapshot:v2
labpilot-repository-inspection-bundle-entry:v1
labpilot-repository-inspection-bundle:v1
labpilot-repository-evidence:v1
labpilot-repository-evidence-manifest-entries:v1
labpilot-repository-evidence-manifest:v1
labpilot-inspector-registry:v1
labpilot-repository-inspection-request:v1
labpilot-repository-inspection-result:v1
```

Test that:

```text
entry input order does not change manifest hash
path/change/file-type changes do change hash
captured_at does not change snapshot identity
same commit + different dirty content changes state and snapshot identity
same state under different registration IDs keeps state digest but changes snapshot identity
inspector registry input order does not change registry identity
request identity changes with Snapshot, registry, limits profile, or request schema version
result hash changes with normalized candidates, unresolved questions, completed inspector set, or result schema version
entries_manifest_hash changes only with the ordered Evidence identity set or entries schema version
full Evidence manifest hash also changes with Snapshot identity, Inspection Result hash, or manifest schema version
```

- [ ] **Step 2: Implement canonical relative-path validation**

Every identity function must reject:

```text
absolute path
backslash separator
empty component
. or .. component
NUL
isolated surrogate
```

Sort entries by UTF-8 path bytes, change type, and file type before Canonical JSON encoding. Add exact identity functions:

```text
inspector_registry_identity = hash(
  profile_id,
  ordered required inspector identities,
  ordered optional inspector identities,
  registry_schema_version
)

inspection_request_identity = hash(
  repository_snapshot_identity,
  inspector_profile_id,
  inspector_registry_identity,
  inspection_limits_profile_identity,
  request_schema_version
)

inspection_result_hash = hash(
  inspection_request_identity,
  result_status,
  normalized candidate collections,
  normalized unresolved questions,
  ordered completed inspector identities,
  result_schema_version
)

entries_manifest_hash = namespaced_sha256(
  "labpilot-repository-evidence-manifest-entries:v1",
  {
    "ordered_evidence_identities": [...],
    "entries_schema_version": 1
  }
)

evidence_manifest_hash = namespaced_sha256(
  "labpilot-repository-evidence-manifest:v1",
  {
    "repository_snapshot_identity": ...,
    "inspection_result_hash": ...,
    "entries_manifest_hash": ...,
    "manifest_schema_version": ...
  }
)
```

All schema/registry/profile versions are explicit inputs and are later persisted by Tasks 13-14. `entries_manifest_hash` proves only the stable ordered Evidence identity set; `evidence_manifest_hash` additionally binds that set to the exact Snapshot and Inspection Result. They are distinct authoritative hashes and must never be treated as aliases.

- [ ] **Step 3: Define content store behavior tests**

```python
ref = await store.put_bytes(b"hello")
assert ref == f"sha256:{sha256(b'hello').hexdigest()}"
assert await store.read_bytes(ref) == b"hello"
```

Also test:

```text
same content -> same ref and no rewrite requirement
parent directory fsync occurs after os.replace
partial temporary file never becomes visible
existing blob with wrong bytes -> ContentStoreCorruption
unknown ref -> ContentBlobMissing
path traversal in ref -> rejected
```

- [ ] **Step 4: Implement `ContentAddressedStore`**

Required methods:

```python
async def put_bytes(self, value: bytes) -> str: ...
async def read_bytes(self, ref: str) -> bytes: ...
async def verify(self, ref: str, expected_hash: str) -> None: ...
```

Storage path:

```text
<root>/sha256/<first-two-hex>/<remaining-hex>
```

Write to a same-directory temporary file, `fsync` the temporary file, atomically `os.replace`, then open and `fsync` the parent directory before returning; verify existing blobs before reuse. Content ref is the digest, not a mutable filesystem path.

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/context/test_repository_identities.py -v
pytest tests/unit/context/test_content_store.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/labpilot/context/repository_identities.py \
  src/labpilot/context/content_store.py \
  tests/unit/context/test_repository_identities.py \
  tests/unit/context/test_content_store.py

git commit -m "feat: add repository identities and content store"
```

**Review gate:** Recompute every test hash from canonical payloads; no timestamp or random ID may enter content identities.

---

### Task 7: Add RepositoryRegistration Persistence and Project Primary-Pointer Handlers

**Files:**
- Modify: `src/labpilot/context/models.py`
- Modify: `src/labpilot/experiments/models.py`
- Create: `src/labpilot/context/repository_repository.py`
- Create: `src/labpilot/experiments/project_bindings.py`
- Create: `migrations/versions/0005_repository_registrations.py`
- Create: `tests/integration/context/test_repository_registration.py`
- Modify: `tests/integration/db/test_migrations.py`

**Interfaces:**
- Consumes: `ProjectRow`, safe path/Git observation, UoW.
- Produces: `RepositoryRegistrationRow`, composite Project pointer integrity, `set_primary_repository_binding()` and `clear_primary_repository_binding()` internal handlers.

- [ ] **Step 1: Write failing database constraint tests**

Prove:

```text
registration.project_id must exist
repository_type must be local_git
status must be active|unavailable|invalid_git_repository|revoked
row_version > 0
(project_id, registration_id) is unique target for composite pointer FK
one registration cannot be primary for two Projects
Project cannot point to a registration owned by another Project
physical Project deletion with registration is restricted
revoked Registration does not block a new Registration for the same Project/root
non-revoked Registration does block a duplicate live root
```

- [ ] **Step 2: Add ORM rows**

`RepositoryRegistrationRow` fields:

```text
repository_registration_id UUID PK
project_id UUID FK projects.project_id ON DELETE RESTRICT
repository_type String(30)
canonical_root_path Text
root_path_identity String(64)
remote_origin_sanitized Text NULL
default_branch String(300) NULL
status String(30)
last_access_check_at timestamptz NULL
created_at timestamptz
updated_at timestamptz
row_version bigint
```

Constraints:

```text
UNIQUE(project_id, repository_registration_id)
```

Live-root uniqueness is a PostgreSQL partial unique index:

```sql
CREATE UNIQUE INDEX uq_repository_registration_live_root
ON repository_registrations(project_id, root_path_identity)
WHERE status <> 'revoked';
```

Add `ProjectRow.primary_repository_registration_id` and a composite FK:

```text
(project_id, primary_repository_registration_id)
→ repository_registrations(project_id, repository_registration_id)
```

Add a unique partial index on `projects.primary_repository_registration_id WHERE NOT NULL`.

- [ ] **Step 3: Implement migration `0005_repository_registrations`**

Ordering:

```text
create repository_registrations
add Project pointer column
add composite FK
add checks, composite target uniqueness, and `uq_repository_registration_live_root` partial index
```

Downgrade reverses pointer FK/column before dropping registration table.

- [ ] **Step 4: Implement Context repository methods**

```python
async def get_registration(...)
async def get_registration_for_update(...)
async def add_registration(...)
async def list_project_registrations(...)
```

No method commits.

- [ ] **Step 5: Implement experiments-owned pointer handlers**

```python
async def set_primary_repository_binding(
    uow: UnitOfWork,
    *,
    project_id: UUID,
    repository_registration_id: UUID,
    expected_project_row_version: int,
) -> ProjectRow: ...

async def clear_primary_repository_binding(
    uow: UnitOfWork,
    *,
    project_id: UUID,
    expected_registration_id: UUID,
    expected_project_row_version: int,
) -> ProjectRow: ...
```

Handlers lock Project, validate its identity/row version, update only the pointer, increment `row_version`, flush, and never import `context` types.

- [ ] **Step 6: Run tests**

```bash
pytest tests/integration/context/test_repository_registration.py -v
pytest tests/integration/db/test_migrations.py -v
pytest -m architecture -v
```

Expected: PASS with migration head `0005_repository_registrations` and no dependency violation.

- [ ] **Step 7: Commit**

```bash
git add src/labpilot/context/models.py \
  src/labpilot/context/repository_repository.py \
  src/labpilot/experiments/models.py \
  src/labpilot/experiments/project_bindings.py \
  migrations/versions/0005_repository_registrations.py \
  tests/integration/context/test_repository_registration.py \
  tests/integration/db/test_migrations.py

git commit -m "feat: add repository registration persistence"
```

**Review gate:** Confirm `experiments` contains no import from `labpilot.context`, and the root-path uniqueness is live-only rather than permanent across revoked history.

---

### Task 8: Implement Repository Registration, Availability, and Revoke Commands

**Files:**
- Create: `src/labpilot/context/events.py`
- Create: `src/labpilot/context/repository_service.py`
- Extend: `src/labpilot/context/repository_types.py`
- Extend: `tests/integration/context/test_repository_registration.py`

**Interfaces:**
- Consumes: `GitAdapter`, path safety, registration repository, Project pointer handlers, command idempotency, RuntimeEvent/Outbox.
- Produces: `RegisterLocalRepository`, `CheckRepositoryAvailability`, `RevokeRepositoryRegistration`.

- [ ] **Step 1: Define exact command contracts**

```python
@dataclass(frozen=True, slots=True)
class RegisterLocalRepository:
    project_id: UUID
    user_path: Path
    expected_project_row_version: int

@dataclass(frozen=True, slots=True)
class CheckRepositoryAvailability:
    repository_registration_id: UUID
    expected_row_version: int

@dataclass(frozen=True, slots=True)
class RevokeRepositoryRegistration:
    repository_registration_id: UUID
    expected_row_version: int
    expected_project_row_version: int
```

Clients do not provide canonical path, root identity, remote URL, status, or timestamps.

- [ ] **Step 2: Register Repository event schemas**

Events:

```text
repository.registration.created
repository.registration.availability_changed
repository.registration.revoked
```

Payloads contain IDs, identity/status, reason code, and row version only. All three event types use `aggregate_type=repository_registration` and `aggregate_id=repository_registration_id`. Canonical path and remote origin are excluded from Event payloads.

- [ ] **Step 3: Write failing registration service tests**

Prove:

```text
absolute realpath inside allowed root registers
outside-root and symlink escape create no DB/Event/Outbox/Receipt rows
non-Git path creates no rows
registration + Project pointer + Event + Outbox + Receipt commit atomically
same key/body replays same registration
same live root with new key returns `repository_registration_reused`, adds one Receipt, and adds no Registration/Event/Outbox
register A -> revoke A -> register same root again creates a new Registration and repoints Project
first successful registration -> Repository becomes unavailable -> same-key replay does not call GitAdapter
concurrent registration for one Project leaves one primary pointer
```

- [ ] **Step 4: Implement `register_local_repository()`**

Call `execute_idempotent_command()` with:

```text
prepare (no DB transaction):
  require absolute path
  resolve safe path
  perform bounded read-only Git probe
  sanitize remote
  compute root/registration identities

operation (one short UoW):
  lock Project
  recheck expected Project version/current pointer
  if matching live Registration already exists and pointer is compatible:
      create Receipt with repository_registration_reused
      do not append Event/Outbox
  else:
      add new Registration
      call set_primary_repository_binding using same UoW
      append repository.registration.created Event/Outbox
      insert Receipt
  commit
```

The initial Receipt lookup happens before `prepare`, so same-key replay remains available when the Repository is offline.

If a live primary already exists, return `RepositoryRegistrationConflict`; do not silently replace it.

- [ ] **Step 5: Implement availability check**

Use the shared prepare/operation executor: Receipt lookup first; `prepare` probes the exact canonical root with no transaction; `operation` locks Registration and updates:

```text
active -> unavailable
unavailable -> active
active/unavailable -> invalid_git_repository when root exists but is no longer a valid Git repository
revoked -> command state conflict
```

Increment `row_version`, use database clock, append Event/Outbox/Receipt.

- [ ] **Step 6: Implement revoke**

Inside one UoW:

```text
lock Registration
lock Project
Registration status -> revoked
if Project points to it: call clear_primary_repository_binding
append revoked Event/Outbox
insert Receipt
commit
```

Revoke of a non-primary live Registration is allowed only if its Project binding is valid. Repeat with same key replays; new key against already revoked state conflicts.

- [ ] **Step 7: Run tests**

```bash
pytest tests/integration/context/test_repository_registration.py -v
pytest tests/unit/observability/test_event_payloads.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/labpilot/context/events.py \
  src/labpilot/context/repository_types.py \
  src/labpilot/context/repository_service.py \
  tests/integration/context/test_repository_registration.py

git commit -m "feat: add repository registration commands"
```

**Review gate:** Verify Receipt replay returns before any probe, every external probe finishes before Project/Registration rows are locked, semantic reuse emits no duplicate Event/Outbox, and revoked history permits a new Registration for the same root.

---

### Task 9: Add Snapshot, InspectionBundle, BundleEntry, and Freshness Persistence

**Files:**
- Modify: `src/labpilot/context/models.py`
- Create: `migrations/versions/0006_repository_snapshots.py`
- Create: `tests/integration/context/test_repository_snapshot.py`
- Create: `tests/integration/context/test_repository_freshness.py`
- Modify: `tests/integration/db/test_migrations.py`

**Interfaces:**
- Consumes: RepositoryRegistration.
- Produces: immutable Snapshot rows, single-direction dirty Bundle ownership, one current Freshness projection per Snapshot.

- [ ] **Step 1: Write failing schema/constraint tests**

Cover:

```text
UNIQUE(registration_id, snapshot_sequence)
UNIQUE(registration_id, repository_snapshot_identity)
Bundle UNIQUE(repository_snapshot_id)
BundleEntry UNIQUE(bundle_id, stable_ordinal)
BundleEntry UNIQUE(bundle_id, entry_identity)
Freshness UNIQUE(repository_snapshot_id)
Snapshot registration FK RESTRICT
Bundle/Entry/Snapshot delete restrictions preserve history
status and positive version CHECK constraints
```

Also assert `repository_snapshots` has no `inspection_bundle_id` column.

- [ ] **Step 2: Add Snapshot ORM fields**

```text
repository_snapshot_id UUID PK
repository_registration_id UUID FK RESTRICT
snapshot_sequence bigint
captured_at timestamptz
canonical_root_path Text
head_commit String(64)
current_branch String(300) NULL
is_detached_head bool
is_clean bool
tracked_tree_identity String(64)
dirty_worktree_manifest_ref String(300) NULL
dirty_worktree_manifest_hash String(64) NULL
untracked_manifest_ref String(300) NULL
untracked_manifest_hash String(64) NULL
repository_state_digest String(64)
repository_snapshot_identity String(64)
submodule_presence bool
lfs_pointer_presence bool
inspector_compatibility_version int
```

Add SQL CHECKs that clean snapshots have null dirty/untracked refs and hashes. Dirty cross-table Bundle existence remains an application invariant.

- [ ] **Step 3: Add Bundle rows**

Bundle:

```text
inspection_bundle_id UUID PK
repository_snapshot_id UUID UNIQUE FK RESTRICT
base_commit_sha String(64)
bundle_schema_version int
capture_tool_version String(100)
entries_manifest_hash String(64)
inspection_bundle_identity String(64) UNIQUE
created_at timestamptz
```

Entry:

```text
inspection_bundle_entry_id UUID PK
inspection_bundle_id UUID FK RESTRICT
repository_path Text
original_repository_path Text NULL
entry_kind String(30) CHECK tracked_overlay|untracked_file|deletion|mode_change
file_type String(30)
file_mode String(20)
head_blob_oid String(64) NULL
index_blob_oid String(64) NULL
content_ref String(300) NULL
content_hash String(64) NULL
size_bytes bigint
stable_ordinal int
entry_identity String(64)
```

Add CHECKs for deletion/mode-change content nullability and non-negative size/ordinal. `original_repository_path` is non-null only for a normalized rename/copy destination. Enforce in service tests: clean Snapshot has no Bundle; dirty Snapshot has exactly one Bundle in the same UoW.

- [ ] **Step 4: Add Freshness row**

```text
repository_snapshot_id UUID PK/FK RESTRICT
status String(30) CHECK current|stale|unverifiable|invalid
reason_code String(100) NULL
current_probe_identity String(64) NULL
probe_version String(100)
checked_at timestamptz
row_version bigint
```

- [ ] **Step 5: Implement migration and downgrade**

Migration:

```text
revision = 0006_repository_snapshots
down_revision = 0005_repository_registrations
```

Create in dependency order:

```text
repository_snapshots
repository_snapshot_inspection_bundles
repository_snapshot_inspection_bundle_entries
repository_snapshot_freshness
```

Downgrade in reverse order.

- [ ] **Step 6: Run focused migration/constraint tests**

```bash
pytest tests/integration/context/test_repository_snapshot.py -v
pytest tests/integration/context/test_repository_freshness.py -v
pytest tests/integration/db/test_migrations.py -v
```

Expected: PASS with head `0006_repository_snapshots`.

- [ ] **Step 7: Commit**

```bash
git add src/labpilot/context/models.py \
  migrations/versions/0006_repository_snapshots.py \
  tests/integration/context/test_repository_snapshot.py \
  tests/integration/context/test_repository_freshness.py \
  tests/integration/db/test_migrations.py

git commit -m "feat: add repository snapshot persistence"
```

**Review gate:** Confirm Bundle ownership is Snapshot -> unique child only; no circular FK or post-create Snapshot mutation exists.

---

### Task 10: Implement Immutable Clean and Dirty Snapshot Capture

**Files:**
- Extend: `src/labpilot/context/git_adapter.py`
- Extend: `src/labpilot/context/repository_types.py`
- Extend: `src/labpilot/context/repository_identities.py`
- Extend: `src/labpilot/context/repository_repository.py`
- Extend: `src/labpilot/context/repository_service.py`
- Extend: `src/labpilot/context/events.py`
- Extend: `tests/unit/context/test_git_adapter.py`
- Extend: `tests/unit/context/test_repository_identities.py`
- Extend: `tests/integration/context/test_repository_snapshot.py`

**Interfaces:**
- Consumes: Git probe, content store, registration, identity functions.
- Produces: `CaptureRepositorySnapshot`, exact dirty Bundle capture, initial `current` Freshness projection.

- [ ] **Step 1: Define capture observation types**

```python
@dataclass(frozen=True, slots=True)
class StableStatToken:
    inode: int
    file_type: str
    size_bytes: int
    mtime_ns: int
    ctime_ns: int

@dataclass(frozen=True, slots=True)
class CapturedBundleEntry:
    relative_path: str
    original_relative_path: str | None
    entry_kind: str
    file_type: str
    file_mode: str
    head_blob_oid: str | None
    index_blob_oid: str | None
    content_ref: str | None
    content_hash: str | None
    size_bytes: int
    stable_ordinal: int
    entry_identity: str
    stable_stat_token: StableStatToken | None

@dataclass(frozen=True, slots=True)
class RepositorySnapshotObservation:
    probe: RepositoryProbe
    dirty_manifest_ref: str | None
    dirty_manifest_hash: str | None
    untracked_manifest_ref: str | None
    untracked_manifest_hash: str | None
    state_digest: str
    snapshot_identity: str
    bundle_entries: tuple[CapturedBundleEntry, ...]
    bundle_identity: str | None
```

- [ ] **Step 2: Write clean-capture tests**

Assert:

```text
clean repository -> no Bundle/Entries
Snapshot head/tree/branch exact
Freshness row current with probe identity
same Git state + concurrent capture -> one Snapshot, one sequence, one created Event/Outbox, and same Receipt result
capture time does not affect identity
```

- [ ] **Step 3: Write dirty-capture tests**

Construct staged, unstaged, deleted, mode-changed, symlink, and untracked entries. Assert:

```text
every required content entry has content_ref/hash
symlink hash is over normalized link target, not target file bytes
deletion can have null content
Bundle exists exactly once
working tree changes after capture do not change stored blob bytes
same commit + changed content -> different snapshot identity
tracked rename hides old path and exposes new path
staged rename plus unstaged modification captures final new-path bytes
copy preserves old path and captures new path
non-UTF-8 path fails closed
submodule_presence and lfs_pointer_presence are computed, not defaulted
concurrent detectable mutation -> RepositorySnapshotCaptureConflict and no Snapshot
```

- [ ] **Step 4: Implement bounded observation and blob capture outside UoW**

Use the idempotent executor so Receipt lookup occurs before capture. `prepare` runs without a database transaction and follows this fail-closed protocol:

```text
read-only precheck of Registration identity/status
probe_before
normalize rename/copy and strict UTF-8 paths
for each required entry:
  lstat before
  regular file: open with O_NOFOLLOW, read, fstat after
  symlink: lstat, readlink, lstat after
  require inode/type/size/mtime_ns/ctime_ns stability
  verify path remains under canonical root
  apply per-file/count/total byte limits
  write content-addressed blob
write canonical dirty/untracked manifest blobs
probe_after
lstat every captured path again
require same HEAD, tracked tree, normalized status identity, and stable stat tokens
compute submodule_presence and lfs_pointer_presence
if any check differs: RepositorySnapshotCaptureConflict
otherwise compute all identities from captured bytes/metadata
```

Symlink content hash is over normalized link-target bytes; symlink targets are never opened. The guarantee is fail-closed detection of observable concurrent changes, not a filesystem-wide atomic snapshot against arbitrary writers.

- [ ] **Step 5: Persist Snapshot atomically in a short UoW**

Inside UoW:

```text
lock Registration
verify status is not revoked and canonical root identity matches observation
allocate snapshot_sequence under Registration lock
insert Snapshot or detect existing identity
if new dirty Snapshot: insert Bundle + Entries
insert initial Freshness(current)
append snapshot.captured Event/Outbox
insert Receipt
commit
```

if identity already exists, verify its Bundle identity matches the prepared observation, create only a new Receipt with `repository_snapshot_reused`, and append no new Event/Outbox. A mismatch is `RepositorySnapshotInvariantViolation`. If new, create Snapshot/Bundle/Freshness plus one `repository.snapshot.captured` Event/Outbox/Receipt.

- [ ] **Step 6: Register `repository.snapshot.captured` aggregate and payload**

Aggregate ownership:

```text
aggregate_type = repository_snapshot
aggregate_id = repository_snapshot_id
```

Payload:

```python
{
    "repository_snapshot_id": str(snapshot_id),
    "repository_registration_id": str(registration_id),
    "repository_snapshot_identity": snapshot_identity,
    "repository_state_digest": state_digest,
    "is_clean": is_clean,
    "snapshot_sequence": sequence,
}
```

- [ ] **Step 7: Run tests**

```bash
pytest tests/unit/context/test_git_adapter.py -v
pytest tests/unit/context/test_repository_identities.py -v
pytest tests/integration/context/test_repository_snapshot.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/labpilot/context tests/unit/context \
  tests/integration/context/test_repository_snapshot.py

git commit -m "feat: capture immutable repository snapshots"
```

**Review gate:** Verify replay skips capture, the double-probe/stat protocol fails closed, semantic reuse emits no duplicate Event/Outbox, rename/copy views are correct, and no dirty Inspector path reads the current worktree.

---

### Task 11: Implement RepositorySnapshot Freshness Assessment

**Files:**
- Extend: `src/labpilot/context/repository_types.py`
- Extend: `src/labpilot/context/repository_repository.py`
- Extend: `src/labpilot/context/repository_service.py`
- Extend: `src/labpilot/context/events.py`
- Extend: `tests/integration/context/test_repository_freshness.py`

**Interfaces:**
- Consumes: current Registration root, immutable Snapshot state digest, Git probe, Freshness projection.
- Produces: `CheckRepositorySnapshotFreshness`, deterministic current/stale/unverifiable/invalid projection updates.

- [ ] **Step 1: Define command and result**

```python
@dataclass(frozen=True, slots=True)
class CheckRepositorySnapshotFreshness:
    repository_snapshot_id: UUID
    expected_row_version: int
```

Result includes Snapshot ID, status, reason code, current probe identity, checked_at, row_version, replayed.

- [ ] **Step 2: Write transition tests**

Prove:

```text
unchanged repository -> current
changed HEAD -> stale(head_changed)
changed tracked bytes -> stale(dirty_manifest_changed)
changed untracked bytes -> stale(untracked_manifest_changed)
missing root/permission/Git timeout -> unverifiable
corrupt stored manifest/blob identity -> invalid
invalid does not automatically recover in WP1-A
current <-> stale and unverifiable -> current/stale are allowed
wrong expected row version -> conflict
```

- [ ] **Step 3: Perform external probe in `prepare()` after Receipt miss**

The executor first checks Receipt. Only on a miss does `prepare()` compute the current state digest using the same canonical identity implementation used by capture. A probe failure produces an `unverifiable` candidate result; it does not throw unless the failure is an internal bug or unsupported schema.

- [ ] **Step 4: Update the unique current projection**

Inside one UoW:

```text
lock Snapshot and Freshness
validate expected row_version
validate Registration binding
set status/reason/probe identity/DB clock
increment row_version
append freshness_assessed Event/Outbox
insert Receipt
commit
```

Use database `clock_timestamp()` for `checked_at`.

- [ ] **Step 5: Register Event aggregate and payload**

```text
aggregate_type = repository_snapshot
aggregate_id = repository_snapshot_id
```

```python
{
    "repository_snapshot_id": str(snapshot_id),
    "repository_snapshot_identity": snapshot_identity,
    "previous_status": previous_status,
    "status": status,
    "reason_code": reason_code,
    "row_version": row_version,
}
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/integration/context/test_repository_freshness.py -v
pytest tests/integration/context/test_repository_snapshot.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/labpilot/context \
  tests/integration/context/test_repository_freshness.py \
  tests/integration/context/test_repository_snapshot.py

git commit -m "feat: assess repository snapshot freshness"
```

**Review gate:** Confirm freshness history is only RuntimeEvent history; the Snapshot row remains immutable.

---

### Task 12: Implement the Immutable Snapshot Reader and Versioned Inspector Registry

**Files:**
- Create: `src/labpilot/context/snapshot_reader.py`
- Create: `src/labpilot/context/inspection_registry.py`
- Create: `src/labpilot/context/inspectors/__init__.py`
- Create: `src/labpilot/context/inspectors/file_inventory.py`
- Create: `src/labpilot/context/inspectors/project_manifests.py`
- Create: `src/labpilot/context/inspectors/python_static_signals.py`
- Create: `tests/unit/context/test_snapshot_reader.py`
- Create: `tests/unit/context/test_inspection_registry.py`
- Create: `tests/unit/context/inspectors/test_file_inventory.py`
- Create: `tests/unit/context/inspectors/test_project_manifests.py`
- Create: `tests/unit/context/inspectors/test_python_static_signals.py`

**Interfaces:**
- Consumes: clean commit/tree objects or dirty base objects + Bundle blobs.
- Produces: immutable file view, versioned deterministic inspector profile, normalized candidate/evidence observations.

- [ ] **Step 1: Define `SnapshotContentReader` tests**

Required behavior:

```text
clean Snapshot reads file bytes from git cat-file at captured commit
dirty Snapshot overlays bundle entry bytes over base commit
deletion hides base file
untracked entry appears
mode change updates metadata without inventing bytes
working tree edits after capture do not alter reads
missing/corrupt bundle blob -> SnapshotContentInvalid
path not in snapshot -> SnapshotPathNotFound
```

- [ ] **Step 2: Implement snapshot reader contract**

```python
@dataclass(frozen=True, slots=True)
class SnapshotFile:
    relative_path: str
    file_type: str
    file_mode: str
    content: bytes | None
    content_hash: str | None

class SnapshotContentReader:
    async def list_files(self) -> tuple[SnapshotFile, ...]: ...
    async def read_file(self, relative_path: str) -> SnapshotFile: ...
```

Clean reader uses captured commit/tree via Git object database. Dirty reader never reads Registration working tree.

- [ ] **Step 3: Define the inspector protocol and profile**

```python
@dataclass(frozen=True, slots=True)
class InspectorIdentity:
    inspector_name: str
    inspector_version: str

class DeterministicInspector(Protocol):
    identity: InspectorIdentity

    async def inspect(
        self,
        reader: SnapshotContentReader,
        limits: InspectionLimits,
    ) -> InspectorOutput: ...
```

Initial required profile `wp1-a-default:v1` contains exactly:

```text
file_inventory:v1
project_manifests:v1
python_static_signals:v1
```

Registry identity uses `labpilot-inspector-registry:v1` over profile ID, ordered required identities, ordered optional identities, and `registry_schema_version`. Define and test a versioned `InspectionLimitsProfile` with `inspection_limits_profile_identity`; later request identity must bind it.

- [ ] **Step 4: Implement `file_inventory:v1`**

Produces file-presence Evidence and normalized inventory metadata. It does not infer semantic roles.

- [ ] **Step 5: Implement `project_manifests:v1`**

Parse bounded, supported files:

```text
pyproject.toml
requirements*.txt
environment.yml
Dockerfile
compose.yaml
compose.yml
```

Outputs configuration/environment/detected-command candidates and Evidence with exact content hashes/locators. Parse errors become unresolved questions, not execution exceptions, unless the inspector cannot complete its required scan.

- [ ] **Step 6: Implement `python_static_signals:v1`**

Use Python `ast` on bounded `.py` files to detect:

```text
if __name__ == "__main__"
function definitions
argparse option declarations
obvious metric logging calls
obvious artifact write calls
```

Every candidate carries Evidence identity. Comments and README examples remain evidence with low authority; they are not confirmed entrypoints.

- [ ] **Step 7: Test resource bounds and determinism**

Same Snapshot must yield byte-identical normalized outputs and same registry/profile identity. Exceeding file/count/byte limits raises `InspectionResourceLimitExceeded` before persistence.

- [ ] **Step 8: Run tests**

```bash
pytest tests/unit/context/test_snapshot_reader.py -v
pytest tests/unit/context/test_inspection_registry.py -v
pytest tests/unit/context/inspectors -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/labpilot/context/snapshot_reader.py \
  src/labpilot/context/inspection_registry.py \
  src/labpilot/context/inspectors \
  tests/unit/context/test_snapshot_reader.py \
  tests/unit/context/test_inspection_registry.py \
  tests/unit/context/inspectors

git commit -m "feat: add deterministic repository inspectors"
```

**Review gate:** Confirm inspectors receive no Database Session, network client, model client, or mutable repository path.

---

### Task 13: Add Inspection, Result, Evidence, and EvidenceManifest Persistence

**Files:**
- Modify: `src/labpilot/context/models.py`
- Create: `migrations/versions/0007_repository_inspections.py`
- Create: `tests/integration/context/test_repository_inspection.py`
- Create: `tests/integration/context/test_repository_constraints.py`
- Modify: `tests/integration/db/test_migrations.py`

**Interfaces:**
- Consumes: RepositorySnapshot.
- Produces: immutable inspection/evidence schema and constraints.

- [ ] **Step 1: Write failing PostgreSQL constraint tests**

Required constraints:

```text
InspectionRun Snapshot FK RESTRICT
InspectionRun inspection_request_identity UNIQUE
InspectionResult one deterministic result per run in WP1-A
InspectionResult Snapshot must match Run at application layer
Evidence Snapshot FK RESTRICT
Evidence identity UNIQUE within Snapshot
EvidenceManifest one per InspectionResult
EvidenceManifest hash UNIQUE per Snapshot/Result identity
line_start/line_end positive and ordered
stable evidence ordinal unique within manifest
```

- [ ] **Step 2: Add `RepositoryInspectionRunRow`**

```text
inspection_run_id UUID PK
repository_snapshot_id UUID FK RESTRICT
inspection_request_identity String(64) UNIQUE
inspector_profile_id String(100)
inspector_registry_schema_version int
inspector_registry_identity String(64)
inspection_limits_profile_identity String(64)
inspection_request_schema_version int
required_inspector_identity_set_json JSONB
completed_inspector_identity_set_json JSONB
status String(30)
started_at timestamptz
completed_at timestamptz NULL
failure_code String(100) NULL
row_version bigint
```

WP1 synchronous command persists only complete domain terminal states. `pending/running` remain allowed schema values for frozen compatibility but are not committed by the public WP1-A command.

- [ ] **Step 3: Add Result, Evidence, and Manifest rows**

Result:

```text
inspection_result_id UUID PK
inspection_run_id UUID UNIQUE FK RESTRICT
repository_snapshot_id UUID FK RESTRICT
result_sequence int = 1
result_status String(30) CHECK complete|partial|ambiguous|failed
candidate_entrypoints JSONB
configuration_candidates JSONB
metric_candidates JSONB
artifact_candidates JSONB
dataset_reference_candidates JSONB
environment_candidates JSONB
detected_commands JSONB
unresolved_questions JSONB
result_hash String(64) UNIQUE
result_schema_version int
created_at timestamptz
```

Evidence:

```text
repository_evidence_id UUID PK
repository_snapshot_id UUID FK RESTRICT
repository_path Text
repository_evidence_identity String(64)
evidence_kind String(80)
line_start int NULL
line_end int NULL
structural_locator String(500) NULL
symbol_name String(500) NULL
file_mode String(20)
content_hash String(64)
excerpt_ref String(300) NULL
excerpt_hash String(64) NULL
declared_or_inferred String(30) CHECK declared|deterministic_inference
inspector_name String(100)
inspector_version String(100)
captured_at timestamptz
UNIQUE(repository_snapshot_id, repository_evidence_identity)
```

Manifest:

```text
repository_evidence_manifest_id UUID PK
inspection_result_id UUID UNIQUE FK RESTRICT
repository_snapshot_id UUID FK RESTRICT
entries_schema_version int
manifest_schema_version int
inspection_result_hash String(64)
entries_manifest_hash String(64)
manifest_hash String(64) UNIQUE
created_at timestamptz
```

Join table:

```text
repository_evidence_manifest_entries
manifest_id UUID FK RESTRICT
evidence_id UUID FK RESTRICT
stable_ordinal int
entry_identity String(64)
PK(manifest_id, evidence_id)
UNIQUE(manifest_id, stable_ordinal)
UNIQUE(manifest_id, entry_identity)
```

- [ ] **Step 4: Implement migration `0007_repository_inspections`**

```text
revision = 0007_repository_inspections
down_revision = 0006_repository_snapshots
```

Use named constraints and complete reverse-order downgrade.

- [ ] **Step 5: Update model registry/migration tests**

Assert head is `0007_repository_inspections` and all WP1-A tables appear in `Base.metadata` after only importing `labpilot.db.model_registry`.

- [ ] **Step 6: Run tests**

```bash
pytest tests/integration/context/test_repository_inspection.py -v
pytest tests/integration/context/test_repository_constraints.py -v
pytest tests/integration/db/test_migrations.py -v
pytest tests/architecture/test_model_registry.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/labpilot/context/models.py \
  migrations/versions/0007_repository_inspections.py \
  tests/integration/context/test_repository_inspection.py \
  tests/integration/context/test_repository_constraints.py \
  tests/integration/db/test_migrations.py \
  tests/architecture/test_model_registry.py

git commit -m "feat: add repository inspection persistence"
```

**Review gate:** Confirm no LLM inference fields or RepositoryInference rows are added.

---

### Task 14: Implement Synchronous Inspection and Evidence Manifest Commands

**Files:**
- Extend: `src/labpilot/context/repository_types.py`
- Extend: `src/labpilot/context/repository_identities.py`
- Extend: `src/labpilot/context/repository_repository.py`
- Extend: `src/labpilot/context/repository_service.py`
- Extend: `src/labpilot/context/events.py`
- Extend: `tests/integration/context/test_repository_inspection.py`

**Interfaces:**
- Consumes: Snapshot reader, inspector registry, persistence rows, command idempotency.
- Produces: `RunDeterministicRepositoryInspection` with complete Result/Evidence/Manifest atomicity.

- [ ] **Step 1: Define command and immutable normalized outputs**

```python
@dataclass(frozen=True, slots=True)
class RunDeterministicRepositoryInspection:
    repository_snapshot_id: UUID
    inspector_profile_id: str = "wp1-a-default:v1"
```

Define canonical candidate/evidence dataclasses; all unordered sets are normalized before hashing. Persist and expose `registry_schema_version`, `inspector_registry_identity`, `inspection_limits_profile_identity`, `request_schema_version`, `result_schema_version`, and `manifest_schema_version`.

- [ ] **Step 2: Write service tests for complete domain results**

Prove:

```text
clean Snapshot inspection completes
captured dirty Snapshot inspection completes from Bundle after worktree changes
required identity set == completed identity set
result and evidence hashes deterministic
same Snapshot/profile/key replays exact envelope
same Snapshot/profile/new key returns `repository_inspection_reused`, adds one Receipt, and adds no Run/Result/Evidence/Manifest/Event/Outbox
parse ambiguity persists complete/ambiguous domain result when all required inspectors finish
resource limit/inspector exception rolls back Run/Result/Evidence/Manifest/Event/Outbox/Receipt
```

- [ ] **Step 3: Execute inspection in `prepare()` after Receipt miss**

The executor checks Receipt first. On a miss, `prepare()` reads immutable Snapshot content with no open database transaction and run all required inspectors. Build and independently test these exact formulas:

```text
inspector_registry_identity = namespaced hash(
  labpilot-inspector-registry:v1,
  profile_id,
  ordered required inspector identities,
  ordered optional inspector identities,
  registry_schema_version
)

inspection_request_identity = namespaced hash(
  labpilot-repository-inspection-request:v1,
  repository_snapshot_identity,
  inspector_profile_id,
  inspector_registry_identity,
  inspection_limits_profile_identity,
  request_schema_version
)

inspection_result_hash = namespaced hash(
  labpilot-repository-inspection-result:v1,
  inspection_request_identity,
  result_status,
  normalized candidate collections,
  normalized unresolved questions,
  ordered completed inspector identities,
  result_schema_version
)

entries_manifest_hash = namespaced hash(
  labpilot-repository-evidence-manifest-entries:v1,
  ordered evidence identities,
  entries_schema_version=1
)

evidence_manifest_hash = namespaced hash(
  labpilot-repository-evidence-manifest:v1,
  repository_snapshot_identity,
  inspection_result_hash,
  entries_manifest_hash,
  manifest_schema_version
)
```

No database row is created until all required inspectors reach a terminal output.

- [ ] **Step 4: Persist the full inspection in one UoW**

Inside UoW:

```text
lock Snapshot
verify Snapshot identity/schema and Bundle integrity
check existing inspection_request_identity

if existing:
  verify persisted Run/Result/Manifest identities equal prepared identities
  insert only Receipt(outcome_kind=repository_inspection_reused)
  append no Event/Outbox
else:
  insert completed InspectionRun
  insert immutable Result
  insert Evidence rows
  insert EvidenceManifest with entries_schema_version=1, entries_manifest_hash, and manifest_hash
  insert ordered manifest entries whose identity order exactly matches entries_manifest_hash
  append repository.inspection.completed Event/Outbox
  append repository.evidence_manifest.created Event/Outbox

insert Receipt
commit
```

The two events share the command interaction correlation. Both events use `aggregate_type=repository_inspection_run` and `aggregate_id=inspection_run_id`; the second event uses the first event ID as causation ID.

- [ ] **Step 5: Register bounded Event payloads**

`repository.inspection.completed`:

```text
inspection_run_id
inspection_result_id
repository_snapshot_id
result_status
result_hash
inspector_profile_id
required_inspector_count
completed_inspector_count
```

`repository.evidence_manifest.created`:

```text
manifest_id
repository_snapshot_id
inspection_result_id
manifest_hash
evidence_count
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/integration/context/test_repository_inspection.py -v
pytest tests/unit/context/inspectors -v
pytest tests/unit/observability/test_event_payloads.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/labpilot/context \
  tests/integration/context/test_repository_inspection.py \
  tests/unit/context/inspectors \
  tests/unit/observability/test_event_payloads.py

git commit -m "feat: persist deterministic repository inspections"
```

**Review gate:** Confirm replay skips inspectors, semantic reuse emits no duplicate Event/Outbox, identities are recomputable from persisted versions, and execution exceptions never masquerade as completed domain results.

---

### Task 15: Expose Repository, Snapshot, Freshness, Inspection, and Query APIs

**Files:**
- Extend: `src/labpilot/api/dependencies.py`
- Create: `src/labpilot/api/schemas/repositories.py`
- Create: `src/labpilot/api/routers/repositories.py`
- Modify: `src/labpilot/api/app.py`
- Modify: `src/labpilot/main.py`
- Create: `tests/integration/api/test_repositories.py`

**Interfaces:**
- Consumes: all WP1-A application services and query repositories.
- Produces: complete WP1-A public command/query API.

- [ ] **Step 1: Extend `ApplicationServices` without optional domain services**

```python
@dataclass(frozen=True, slots=True)
class ApplicationServices:
    projects: ProjectApplicationService
    repositories: RepositoryApplicationService
```

The real container constructs:

```text
GitAdapter
ContentAddressedStore
InspectorRegistry
ProjectApplicationService
RepositoryApplicationService
```

from Settings and Database. Tests inject temporary roots/stores.

- [ ] **Step 2: Define command request schemas**

```python
class RegisterRepositoryRequest(BaseModel):
    path: str  # must be an absolute API-container path
    expected_project_row_version: int = Field(gt=0)

class AvailabilityCheckRequest(BaseModel):
    expected_row_version: int = Field(gt=0)

class RevokeRepositoryRequest(BaseModel):
    expected_row_version: int = Field(gt=0)
    expected_project_row_version: int = Field(gt=0)

class CaptureSnapshotRequest(BaseModel):
    expected_registration_row_version: int = Field(gt=0)

class FreshnessCheckRequest(BaseModel):
    expected_row_version: int = Field(gt=0)

class RunInspectionRequest(BaseModel):
    inspector_profile_id: Literal["wp1-a-default:v1"] = "wp1-a-default:v1"
```

- [ ] **Step 3: Implement exact command routes**

```text
POST /projects/{project_id}/repositories
POST /repositories/{registration_id}/availability-checks
POST /repositories/{registration_id}/revoke
POST /repositories/{registration_id}/snapshots
POST /repository-snapshots/{snapshot_id}/freshness-checks
POST /repository-snapshots/{snapshot_id}/inspections
```

Every route requires `Idempotency-Key`. Router passes one command to one service method. HTTP response mapping is fixed:

```text
first create of Project/Registration/Snapshot/Inspection -> 201
Receipt replay of any command -> 200
semantic reuse under a new key -> 200 with replayed=false and *_reused outcome
first availability/revoke/freshness state update -> 200
```

- [ ] **Step 4: Implement bounded query routes**

```text
GET /projects/{project_id}
GET /repositories/{registration_id}
GET /repositories/{registration_id}/snapshots
GET /repository-snapshots/{snapshot_id}
GET /repository-snapshots/{snapshot_id}/freshness
GET /repository-snapshots/{snapshot_id}/inspection-result
GET /repository-snapshots/{snapshot_id}/evidence-manifest
```

List routes require deterministic ordering and explicit `limit`/`cursor`; default limit 50, maximum 200. They return refs/hashes, not blob contents or full Repository files.

- [ ] **Step 5: Write API tests for all command boundaries**

Cover happy path and errors:

```text
relative path -> 422 RepositoryPathMustBeAbsolute
path outside root -> 422 structured error
symlink escape -> 422
registration conflict -> 409
availability unavailable committed result -> 2xx
capture clean and dirty -> 201
snapshot size limit -> 422 and no receipt
freshness stale -> 2xx committed projection
inspection complete/ambiguous -> 2xx committed domain result
inspection execution exception -> 500/503 and no committed rows
idempotency replay -> 200, same result, replayed=true, no Git/Inspector call
semantic reuse with new key -> 200, replayed=false, *_reused outcome, no duplicate Event/Outbox
query endpoints never expose blob bytes or remote credentials
```

- [ ] **Step 6: Run API and regression tests**

```bash
pytest tests/integration/api/test_projects.py -v
pytest tests/integration/api/test_repositories.py -v
pytest tests/unit/api/test_health.py tests/integration/api/test_readiness.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/labpilot/api src/labpilot/main.py \
  tests/integration/api/test_projects.py \
  tests/integration/api/test_repositories.py

git commit -m "feat: expose WP1-A repository APIs"
```

**Review gate:** Search Routers for `session`, `select`, `update`, `delete`, and ORM row imports; the search must return no domain SQL usage.

---

### Task 16: Prove Cross-Cutting Concurrency, Atomicity, Constraints, and Module Boundaries

**Files:**
- Extend: `tests/integration/db/test_command_receipts.py`
- Extend: `tests/integration/experiments/test_project_service.py`
- Extend: `tests/integration/context/test_repository_registration.py`
- Extend: `tests/integration/context/test_repository_snapshot.py`
- Extend: `tests/integration/context/test_repository_freshness.py`
- Extend: `tests/integration/context/test_repository_inspection.py`
- Extend: `tests/integration/context/test_repository_constraints.py`
- Extend: `tests/architecture/test_module_dependencies.py`
- Extend: `tests/architecture/test_model_registry.py`

**Interfaces:**
- Consumes: complete WP1-A implementation.
- Produces: proof that race handling, rollback, pointer integrity, immutable capture, and dependency rules hold under PostgreSQL.

- [ ] **Step 1: Add concurrent same-key tests per command class**

At minimum:

```text
CreateProject
RegisterLocalRepository
CaptureRepositorySnapshot
CheckRepositorySnapshotFreshness
RunDeterministicRepositoryInspection
```

For each, assert one committed domain result/Receipt and one replay; no duplicate Event/Outbox rows. For Register, Capture, and Inspection, make the external dependency unavailable after the first commit and prove same-key replay never invokes `prepare`, `GitAdapter`, blob capture, or Inspector.

- [ ] **Step 2: Add same-key/different-request conflict tests**

For each command family, reuse the same Idempotency-Key with a changed semantic field. Assert `IdempotencyKeyConflict` and no second domain mutation.

- [ ] **Step 3: Add semantic-reuse and live-registration tests**

For Registration, Snapshot, and Inspection, submit the same semantic input with a new key and assert:

```text
Receipt count +1
domain row count unchanged
RuntimeEvent count unchanged
Outbox count unchanged
replayed = false
outcome_kind = *_reused
```

Prove `register A -> revoke A -> register same root` creates a new Registration ID, retains the revoked row, and points Project to the new row.

- [ ] **Step 4: Add Project pointer race tests**

Run two concurrent repository registrations for one Project. Assert:

```text
one Registration becomes Project primary
one command conflicts/rolls back
Project pointer and Registration ownership are consistent
no orphan RuntimeEvent/Outbox/Receipt from loser
```

Run revoke concurrently with availability check and assert row locks/expected versions prevent lost updates.

- [ ] **Step 5: Add Snapshot identity/sequence race tests**

Two captures of the same exact state must return one Snapshot identity and one sequence. Inject changes between `probe_before`, per-entry reads, and `probe_after`. Assert the result is either a Snapshot whose every entry bytes/stat token match the recorded observation, or `RepositorySnapshotCaptureConflict` with no Snapshot. Do not claim a filesystem-global atomic snapshot against arbitrary writers.

Also prove rename/copy normalization, strict non-UTF-8 rejection, symlink target hashing, and computed submodule/LFS flags.

- [ ] **Step 6: Add atomic rollback fault injection**

Inject failures at:

```text
after domain row flush
before Event flush
after Event before Outbox flush
before Receipt flush
Receipt unique race
before commit
```

For each public command, assert the entire UoW either commits the complete set or leaves none of its rows.

- [ ] **Step 7: Add architecture and Event aggregate assertions**

Assert:

```text
experiments imports no context modules
context may import experiments internal handlers
api imports services but no ORM models
migrations/env.py imports only db.model_registry as model root
all expected WP1-A tables exist in Base.metadata
project.* aggregate = Project
registration.* aggregate = RepositoryRegistration
snapshot.* aggregate = RepositorySnapshot
inspection.* and evidence_manifest.created aggregate = RepositoryInspectionRun
```

- [ ] **Step 8: Add shared envelope and CAS durability tests**

Prove every section 0.3 envelope/request limit, control-character rejection, and non-JSON-native rejection. Instrument `ContentAddressedStore` to prove `fsync(temp file) -> os.replace -> fsync(parent directory)` ordering.

- [ ] **Step 9: Run all focused WP1-A tests**

```bash
pytest \
  tests/unit/common/test_commands.py \
  tests/unit/db/test_idempotency.py \
  tests/unit/experiments \
  tests/unit/context \
  tests/integration/db/test_command_receipts.py \
  tests/integration/experiments \
  tests/integration/context \
  tests/integration/api/test_projects.py \
  tests/integration/api/test_repositories.py \
  tests/architecture \
  -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add tests

git commit -m "test: close WP1-A concurrency and atomicity coverage"
```

**Review gate:** Verify Receipt hits precede all external preparation, semantic reuse creates only a Receipt, external Git/blob/Inspector work is absent while row locks are held, and Event aggregate ownership matches section 0.2.

---

### Task 17: Run Migration Round Trips, Full Regression, and WP1-A Acceptance

**Files:**
- Create: `docs/labpilot/implementation/wp1-a-execution-progress.md`
- Create: `docs/labpilot/implementation/wp1-a-acceptance.md`
- Create: `docs/labpilot/implementation/wp1-a-review.md`
- Create: `docs/labpilot/implementation/wp1-a-v0.2-dependency-verification.txt`
- Modify: `DOCUMENT-STATUS.md`

**Interfaces:**
- Consumes: reviewed Task commits 1-16.
- Produces: WP1-A tested commit/tree, execution evidence, acceptance result, reviewer decision, and accepted baseline for WP1-B planning.

- [ ] **Step 1: Verify planning dependencies before acceptance**

```bash
bash scripts/verify_architecture_dependencies.sh \
  docs/labpilot/implementation/wp1-a-v0.2.dependencies.sha256 \
  | tee docs/labpilot/implementation/wp1-a-v0.2-dependency-verification.txt
```

Expected: all inputs `OK`.

- [ ] **Step 2: Run migration upgrade/downgrade/upgrade on an isolated PostgreSQL database**

Ensure `tests/integration/db/test_migrations.py` contains an isolated round-trip test that uses `Alembic Config` with `isolated_database_url` converted to `postgresql+psycopg`:

```python
command.upgrade(config, "head")
assert current_revision(connection) == "0007_repository_inspections"
command.downgrade(config, "0002_runtime_events_outbox")
assert current_revision(connection) == "0002_runtime_events_outbox"
command.upgrade(config, "head")
assert current_revision(connection) == "0007_repository_inspections"
```

Run:

```bash
pytest tests/integration/db/test_migrations.py::test_wp1a_upgrade_downgrade_upgrade -v
```

The fixture-created database is isolated and disposable; never run this downgrade against the development database.

- [ ] **Step 3: Run formatting, lint, type, architecture, and complete tests**

```bash
ruff format --check src tests migrations scripts
ruff check src tests migrations scripts
mypy src/labpilot
pytest -m architecture -v
pytest -m "not integration" -v
pytest -m integration -v
bash tests/scripts/test_verify_architecture_dependencies.sh
```

Expected: every command exits `0`.

- [ ] **Step 4: Validate Compose configuration and container startup**

Run:

```bash
docker compose config
docker compose up --build -d postgres migrate api
docker compose ps
curl --fail http://127.0.0.1:${LABPILOT_API_PORT:-8000}/health/ready
```

Expected: Compose config is valid, migration exits successfully, API is healthy, repository mount is read-only, and content-store volume is writable by the API. Stop containers after the smoke test.

- [ ] **Step 5: Run the WP1-A API acceptance chain**

Using temporary allowed root/content store and isolated PostgreSQL:

```text
POST create Project
POST register local Git Repository
POST capture clean Snapshot
POST freshness check -> current
POST deterministic Inspection
GET Inspection Result
GET Evidence Manifest
modify worktree
POST capture dirty Snapshot
modify worktree again
POST inspect dirty Snapshot and prove captured Bundle content is used
POST freshness check old Snapshot -> stale
POST revoke Registration
GET Project and prove primary_repository_registration_id is null
```

Record request IDs, result IDs, identities, HTTP status, and replay checks; do not record repository file contents or credentials.

- [ ] **Step 6: Record tested commit and tree before evidence commit**

```bash
git status --short
git rev-parse HEAD
git rev-parse HEAD^{tree}
git log --oneline --decorate --graph -20
```

`git status --short` must be empty before evidence files are written. Record the exact tested commit/tree in `wp1-a-execution-progress.md` and `wp1-a-acceptance.md`.

- [ ] **Step 7: Write execution evidence**

`wp1-a-execution-progress.md` must list:

```text
Task number
implementation commit
review result
files changed
focused test commands/results
contract changes to earlier accepted Tasks
```

No “passed” claim may omit the command output summary.

- [ ] **Step 8: Write acceptance evidence against every WP1-A exit condition**

`wp1-a-acceptance.md` must explicitly prove:

```text
Project create/archive
server-authoritative actor scope
CommandReceipt replay independent of mutable Project/Repository state and external availability
absolute-path, read-only Git, unborn-HEAD, non-UTF-8, rename/copy, and symlink safety
registration/pointer atomicity
clean/dirty Snapshot identity
one-way Bundle ownership and fail-closed double-probe/stat immutable content capture
Freshness transitions/concurrency
required Inspector registry completeness
Inspection/Evidence same-Snapshot binding
Inspector request/result determinism and distinct Evidence entries_manifest_hash / manifest_hash recomputation
Receipt replay and semantic reuse row/Event/Outbox count semantics
Event aggregate ownership and Event/Outbox/Receipt atomicity
module dependency rule
migration round trip
full regression
```

Mark each as `passed`, `failed`, or `not applicable`; `not applicable` requires a cited approved scope exclusion.

- [ ] **Step 9: Commit execution evidence before review**

```bash
git add \
  docs/labpilot/implementation/wp1-a-execution-progress.md \
  docs/labpilot/implementation/wp1-a-acceptance.md \
  docs/labpilot/implementation/wp1-a-v0.2-dependency-verification.txt

git commit -m "docs: record WP1-A execution evidence"
```

- [ ] **Step 10: Record the immutable evidence commit and stop**

```bash
git status --short
git rev-parse HEAD
git rev-parse HEAD^{tree}
```

Print and retain the evidence commit/tree as the immutable review handoff. Do not modify the already committed execution-progress or acceptance evidence documents to insert their own commit hash. Stop the implementation session. Do not create `wp1-a-review.md` in the same mutable worktree before this commit exists.

- [ ] **Step 11: Perform independent review in a separate review round**

The reviewer must inspect the fixed tested implementation commit/tree and evidence commit/tree from the Step 10 handoff. “Independent” means a user review or an explicitly separate review-mode pass; it is not self-certification inside the uncommitted implementation workspace.

Review categories:

```text
scope compliance
security/path handling
identity reproducibility
transaction boundaries
idempotency prepare/replay/race handling
semantic reuse Event semantics
database constraints
API authority and fixed HTTP status boundary
observability aggregate ownership and atomicity
test adequacy
deferred WP1-B/C/WP2/WP3/P1 scope
```

- [ ] **Step 12: Write and commit the review decision separately**

Create `wp1-a-review.md` that records:

```text
tested implementation commit / tree
evidence commit / tree
reviewed command and result summary
review decision
```

The review decision must be exactly one of:

```text
approved
changes_required
rejected
```

If `changes_required` or `rejected`, commit the review record only and return to a new repair Task; do not alter prior evidence. If and only if `approved`, update `DOCUMENT-STATUS.md`:

```text
WP1-A implementation: completed / approved
WP1-B plan: not started
```

Then commit:

```bash
git add docs/labpilot/implementation/wp1-a-review.md DOCUMENT-STATUS.md
git commit -m "docs: approve WP1-A project repository foundation"
```

- [ ] **Step 13: Record accepted baseline**

```bash
git rev-parse HEAD
git rev-parse HEAD^{tree}
git log --oneline --decorate --graph -25
```

Record:

```text
WP1-A base commit/tree
WP1-A tested commit/tree
WP1-A evidence commit/tree
WP1-A approval commit/tree
```

Do not push automatically and do not start WP1-B implementation. The next allowed action is to inspect the accepted WP1-A interfaces and draft the WP1-B detailed implementation plan.

---

## 2. Task Dependency Order

```text
Task 0  Approved planning baseline
  ↓
Task 1  Model registry + Receipt schema
  ↓
Task 2  Idempotent command execution
  ↓
Task 3  Project domain
  ↓
Task 4  Project API / service composition
  ↓
Task 5  Path + Git primitives
  ↓
Task 6  Identity + content store
  ↓
Task 7  Registration schema + Project pointer handlers
  ↓
Task 8  Registration / availability / revoke commands
  ↓
Task 9  Snapshot / Bundle / Freshness schema
  ↓
Task 10 Snapshot capture
  ↓
Task 11 Freshness command
  ↓
Task 12 Snapshot reader + Inspector registry
  ↓
Task 13 Inspection/Evidence schema
  ↓
Task 14 Inspection command
  ↓
Task 15 Repository APIs
  ↓
Task 16 Cross-cutting concurrency / atomicity closure
  ↓
Task 17 WP1-A acceptance and approval
```

Tasks are sequential. No Task may be dispatched in parallel because later contracts depend on reviewed outputs from earlier Tasks.

## 3. WP1-A Scope Exclusions Enforced by This Plan

This plan intentionally does not create or implement:

```text
ProjectResearchContextRevision
ProjectPolicyRoot / ProjectPolicyRevision
ContextSourceReference
ContextCompilation
Proposal / ProposalRevision
VerifierResult
DesignApproval / DesignRejection
FreezePreconditionAssessment
ExperimentVersion
RunBatch / Trial / Attempt
AgentRun / ContextSnapshot
Tool Manifest / runtime policy
LLM inspection or planning inference
Runner / Coordinator / retry / reconciliation
Trial rerun authorization
```

The only cross-stage hooks created are:

```text
Project.primary_repository_registration_id
experiments-owned set/clear primary Repository handlers
generic CommandReceipt foundation
central SQLAlchemy model registry
versioned Inspector registry
```

Each hook has an immediate WP1-A caller and test; no nullable placeholder foreign key to a nonexistent authority object is added.

## 4. Plan Self-Review

### 4.1 Approved design coverage

| Approved WP1-A requirement | Implementing Task(s) |
|---|---:|
| Generic CommandReceipt, prepare-before-UoW, replay, semantic reuse, winner reload | 1-2, 8, 10-11, 14, 16 |
| Project core schema and lifecycle | 3-4 |
| Server-authoritative actor/scope | 2, 4 |
| Absolute Repository path and read-only Git observation security | 5, 8, 10, 16 |
| Registration + primary pointer atomicity | 7-8, 16 |
| Clean/dirty Snapshot identity | 6, 9-10 |
| Dirty Bundle exact content, rename/copy, and capture stability | 5-6, 9-10, 12, 16 |
| Snapshot Freshness current projection | 9, 11 |
| Deterministic Inspector registry/request/result identities | 6, 12-14 |
| Inspection/Evidence/Manifest persistence | 13-14 |
| RuntimeEvent/Outbox/Receipt atomicity | 3, 8, 10-11, 14, 16 |
| Public command/query API | 4, 15 |
| PostgreSQL constraints and concurrency | 1, 7, 9, 13, 16 |
| Migration/model registration | 1, 3, 7, 9, 13, 17 |
| Evidence commit followed by independent WP1-A review | 17 |

### 4.2 Type and naming consistency

The plan uses the following stable names throughout:

```text
CommandActor
CommandContext
CommandIdentity
CommandResultEnvelope
IdempotentCommandOutcome
CommandReceiptRow
ProjectRow
RepositoryRegistrationRow
RepositorySnapshotRow
RepositorySnapshotInspectionBundleRow
RepositorySnapshotInspectionBundleEntryRow
RepositorySnapshotFreshnessRow
RepositoryInspectionRunRow
RepositoryInspectionResultRow
RepositoryEvidenceRow
RepositoryEvidenceManifestRow
```

Public service commands use imperative names from the approved design. Internal Project pointer functions are lower-case Python functions owned by `experiments`.

### 4.3 No deferred implementation placeholders

Every WP1-A required object, command, migration, event family, API route, constraint class, and acceptance check is assigned to a Task. Future Work Package objects are listed only under explicit scope exclusions and are not represented by placeholder columns or empty tables.

---

## 5. Execution Gate

This plan is `Draft for Approval`. Do not execute Task 0, install it as an approved dependency, or write production code yet.

After the user approves this exact v0.2 content, reissue the plan with only the status changed to `Approved Implementation Plan`, calculate and record the new SHA-256, and obtain explicit confirmation of that exact approval artifact. Task 0 may then install and freeze the approved bytes.

After approval, execution must remain inline in the same working session using `superpowers:executing-plans`, one Task at a time, with the reviewer gate at the end of every Task. No subagents or parallel agents are permitted.
