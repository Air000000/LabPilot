# LabPilot V1 Slice 2 — Trial → Real Training → Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute this plan task-by-task. Do **not** dispatch implementation subagents for this repository. Every production change uses TDD and stops at the review/authorization gates below.

**Goal:** Build one controlled V1 path from an approved `ExperimentVersion` to exact source materialization, real systemd-supervised training, durable restart reconciliation, bounded cancellation, and no duplicate physical launch.

**Architecture:** PostgreSQL is the durable execution ledger; the selected Candidate B root Runner is a narrow privileged adapter; systemd plus persistent journald are the physical supervision/evidence authority; Coordinator is the only component that compares durable expectation with physical observation and advances domain state. Task 2 creates only logical/prepared execution state, Task 3 introduces the first production physical side effect, Task 4 composes scheduling/reconciliation/API, and Task 5 proves the combined recovery contract on a real native Linux host.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x async, PostgreSQL 16, Alembic, pytest, Ruff, mypy, AF_UNIX + `SO_PEERCRED`, native Linux systemd/systemctl/systemd-run/journalctl, Docker Compose for API/Coordinator composition.

**Post-S2.0 amendment baseline:** `536ad6e38c66c667014beb1ac214271452dfd42d`

**Plan-branch CI portability fix:** `5fe5f5aa5ae349504ec360665762caccb6c338d2`

**Accepted S2.0 implementation/evidence baseline:** `4109afa9f871ae45cf32c301040bcff6f109939d`

**Selected supervision ADR:** `docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-adr.md`

**Native evidence:** `docs/superpowers/evidence/2026-08-13-labpilot-v1-slice2-runner-supervision-poc.md`

**Status:** Draft r3 post-S2.0 production amendment. Task 1 is complete and must not be rerun merely for familiarity. Tasks 2–5 are specified below at execution precision but remain **NOT AUTHORIZED FOR IMPLEMENTATION** until this amendment is reviewed and approved.

**Historical governance note:** Candidate B was admitted and implemented before a separate focused pre-implementation amendment was committed. This r3 records the post-S2.0 production plan and does not backdate or rewrite that history.

---

## Global Constraints

- Normative precedence is `V1 Scope v0.3 > Architecture 10 v0.3 > Master Roadmap v0.3 > accepted S2.0 ADR > this approved plan`.
- Preserve the five formal Slice 2 tasks. Internal TDD steps are not additional governance tasks.
- Task 1 is evidence infrastructure only. Production code must not import `tests/poc/s2_runner/**`.
- PostgreSQL is the only durable business/execution ledger. Runner owns no DB, receipt ledger, retry policy, Trial state, Attempt state, or workflow engine.
- Candidate B topology is fixed: non-root controller → narrow AF_UNIX protocol + `SO_PEERCRED` → root-owned narrow Runner → fixed Runner-derived systemd profile → non-root `labpilot-train` workload.
- Runner protocol surface is only `INFO`, `LAUNCH`, `OBSERVE`, `STOP`. No shell, arbitrary exec, raw unit name, raw systemd property, arbitrary PID/signal, scheduler, plugin, or remote host surface.
- `X` is one stable persisted supervisor execution identity per Attempt. PID, start time, boot ID, InvocationID, journal cursor, and systemd object lifetime are observation facts only.
- `H` is the versioned canonical SHA-256 of the concrete `FrozenLaunchSpec`. Same X/H reconciles; same X/different H is `IDENTITY_CONFLICT` and fails closed.
- `NOT_FOUND` is not `NEVER_ACCEPTED_PROVEN`. Possible prior acceptance means observe-only recovery; never blind-create the same Attempt.
- A launch/control response that becomes unknown after any request bytes may have been sent triggers reconciliation, not automatic resend.
- Destructive control requires fresh exact `ACTIVE_MATCH`. `TERMINAL_MATCH`, `IDENTITY_CONFLICT`, and `UNKNOWN` never authorize stop/kill.
- `UNKNOWN` never releases an active physical slot and never authorizes retry.
- V1 has one Coordinator, one local host, and a fixed maximum of four physical-intent Attempts. Do not add leases/claims/fencing unless a demonstrated race proves they are required.
- DB transactions remain short. Git/filesystem/systemd/socket operations happen outside the UoW; after external work, reopen a short transaction and revalidate authority before committing state.
- `RuntimeEvent` remains immutable audit/history. Existing Outbox behavior remains available, but Outbox delivery must never be the mechanism that invokes Runner `LAUNCH` or reconstructs first-dispatch authority.
- `DOCUMENT-STATUS.md` is updated once, only after full Slice 2 acceptance.
- Do not implement a generic Runner backend registry, remote/SSH runner, multi-host scheduler, worker pool/resource allocator, generic workflow engine, Runner DB, Docker/systemd privileged socket exposure, generic shell/exec protocol, chaos platform, or production fault-injection API.

---

# 1. Formal Slice 2 task status

```text
Task 1 — S2.0 Runner Supervision PoC + ADR                    COMPLETE / SELECT_B
Task 2 — S2.1 Attempt Preparation + Exact Source Materialization  PLANNED
Task 3 — S2.2 Durable Launch / Observe / Stop                    PLANNED
Task 4 — S2.3 Coordinator Reconciliation + S2.4 API Composition PLANNED
Task 5 — S2.5 Real Recovery / Fault-Injection Acceptance        PLANNED
```

Task 1's accepted evidence stack is the ADR plus evidence document above. Do not rerun P0–P13 unless a later approved production change materially changes a physical invariant that those scenarios established.

---

# 2. Production file ownership map

The current dependency guard intentionally keeps `labpilot.execution` independent of `experiments` and `context`; `execution` may depend only on `common`, `db`, `observability`, and itself. Preserve that rule. Cross-domain composition belongs in `coordinator`, which is already permitted to depend on both `experiments` and `execution`.

**Create in `src/labpilot/execution/`:**

- `__init__.py` — package marker only.
- `types.py` — closed execution enums/value objects, Trial/Attempt identity helpers, logical/concrete spec types.
- `models.py` — SQLAlchemy `BatchRow`, `TrialRow`, `AttemptRow`.
- `repository.py` — DB queries/CAS/locks for Batch/Trial/Attempt.
- `ports.py` — narrow protocols consumed by preparation/execution without importing `experiments` or `context`.
- `preparation_service.py` — Task 2 exact-source preparation and crash-safe resume.
- `runner_protocol.py` — production INFO/LAUNCH/OBSERVE/STOP wire contract.
- `runner_client.py` — non-root AF_UNIX client and send-boundary outcome classification.
- `runner_policy.py` — root-owned production host policy parsing/validation.
- `systemd_supervisor.py` — exact X/H systemd/journal adapter.
- `runner_daemon.py` — privileged narrow adapter; no DB/domain imports.
- `execution_service.py` — Task 3 dispatch/reconcile/stop application primitives.
- `batch_service.py` — Task 4 user command service for Create/Start/Cancel.
- `query_service.py` — derived Batch/Trial/Attempt reads; no persisted Batch status projection.

**Create in `src/labpilot/coordinator/`:**

- `execution_adapters.py` — the composition-layer adapters that translate accepted `experiments`/`context` authority into the narrow `execution.ports` contracts.
- `service.py` — startup barrier and deterministic scan ordering.

**Modify existing production files:**

- `src/labpilot/experiments/proposal_types.py`
- `src/labpilot/experiments/proposal_service.py`
- `src/labpilot/experiments/proposal_verifier.py`
- `src/labpilot/observability/payloads.py`
- `src/labpilot/db/model_registry.py`
- `src/labpilot/settings.py`
- `src/labpilot/coordinator/worker.py`
- `src/labpilot/api/dependencies.py`
- `src/labpilot/api/app.py`
- `src/labpilot/api/errors.py`
- `src/labpilot/api/routers/experiments.py`
- `src/labpilot/main.py`
- `compose.yaml`

**Create API files:**

- `src/labpilot/api/routers/execution.py`
- `src/labpilot/api/schemas/execution.py`

**Create migrations:**

- `migrations/versions/0009_execution_preparation.py`
- `migrations/versions/0010_attempt_physical_execution.py`

**Architecture tests:**

- modify `tests/architecture/dependency_rules.py` only to allow `db/model_registry.py -> labpilot.execution.models` as the same narrow model-registration exception already used by other domain model modules;
- modify `tests/architecture/test_model_registry.py` to require execution models;
- create `tests/architecture/test_execution_privilege_boundary.py` to mechanically reject API imports of Runner/host-control modules and reject Runner host modules importing DB/domain orchestration.

---

# 3. Task 1 — S2.0 Runner Supervision PoC + ADR

**Status:** COMPLETE. No implementation steps remain.

Accepted invariants imported into Tasks 2–5:

```text
SELECT_B
PostgreSQL = durable execution expectation
systemd + persistent journald = physical supervision/evidence
Coordinator = reconciliation owner
Runner = privileged adapter only
INFO / LAUNCH / OBSERVE / STOP only
same X/H = reconcile
same X/different H = IDENTITY_CONFLICT
NOT_FOUND != NEVER_ACCEPTED_PROVEN
possible acceptance -> observe, never blind LAUNCH replay
stop only ACTIVE_MATCH
reboot never resurrects same X automatically
```

**Gate:** Any production design that widens the privilege surface beyond these invariants stops for focused review before implementation.

---

# 4. Task 2 — S2.1 Attempt Preparation + Exact Source Materialization

**Risk:** Recovery / provenance critical.

**Deliverable:** Approved immutable `ExperimentVersion` → deterministic Batch/Trial identity → non-launchable Attempt → exact snapshot materialization → verified immutable source manifest → host-independent `FrozenExecutionSpec` → `LAUNCH_READY`. No Runner call and no physical process.

## 4.1 Interfaces produced by Task 2

`src/labpilot/execution/types.py` must expose these public concepts:

```python
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import UUID


class AttemptStatus(StrEnum):
    PREPARING = "preparing"
    LAUNCH_READY = "launch_ready"
    PREPARATION_FAILED = "preparation_failed"
    CANCELLED_PRE_LAUNCH = "cancelled_pre_launch"
    LAUNCH_PENDING = "launch_pending"
    RUNNING = "running"
    UNKNOWN = "unknown"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class VersionExecutionSemantics:
    experiment_version_id: UUID
    experiment_version_identity: str
    proposal_revision_id: UUID
    proposal_payload_hash: str
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    train_entrypoint: str
    workdir: str
    train_argv_template: tuple[str, ...]
    parameter_matrix: dict[str, tuple[str | int | float | bool | None, ...]]
    seeds: tuple[int, ...]
    dataset_reference_type: str
    dataset_path_or_uri: str
    dataset_reference_hash: str
    runtime_profile: str
    artifact_relative_paths: tuple[str, ...]
    timeout_seconds: int
    max_retries: int


@dataclass(frozen=True, slots=True)
class SourceManifestEntry:
    relative_path: str
    file_type: str
    file_mode: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class SourceManifest:
    schema_version: str
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    entries: tuple[SourceManifestEntry, ...]


@dataclass(frozen=True, slots=True)
class FrozenExecutionSpec:
    schema_version: str
    experiment_version_id: UUID
    experiment_version_identity: str
    trial_identity: str
    train_entrypoint_relative_path: str
    workdir_relative_path: str
    runtime_profile: str
    argv: tuple[str, ...]
    dataset_reference_type: str
    dataset_argument: str
    dataset_reference_hash: str
    timeout_seconds: int
```

`FrozenExecutionSpec.argv` is fully resolved for parameter/seed/dataset semantics, except the literal token `"{output_dir}"` remains as the only host-dependent substitution for Task 3. No other unresolved template token is legal.

`src/labpilot/execution/ports.py` must define dependency-inversion contracts; concrete adapters live in Coordinator:

```python
class VersionExecutionSemanticsPort(Protocol):
    async def load(self, experiment_version_id: UUID) -> VersionExecutionSemantics:
        raise NotImplementedError


class SnapshotMaterializationPort(Protocol):
    async def materialize_exact_source(
        self,
        *,
        repository_snapshot_id: UUID,
        expected_repository_snapshot_identity: str,
        destination: Path,
    ) -> SourceManifest:
        raise NotImplementedError
```

## 4.2 Proposal execution semantics extension

`ProposalPayload` gains one optional persisted field placed at the end of the dataclass:

```python
train_argv_template: tuple[str, ...] | None = None
```

Legacy revisions where the JSON key is physically absent remain readable and keep their existing stored `payload_hash`; the deserializer must not materialize the missing key into a rehashed legacy payload. Legacy Versions are queryable but Task 2 rejects them as `EXECUTION_SEMANTICS_UNSUPPORTED`.

New/revised proposals must supply `train_argv_template` before verification can pass. Only these placeholders are valid:

```text
{parameter:<exact-key>}
{seed}
{dataset}
{output_dir}
```

Rules:

- no shell interpolation, conditions, functions, environment lookup, nested templates, or arbitrary expression syntax;
- every parameter key in `parameter_matrix` is consumed at least once;
- `{seed}` and `{dataset}` appear at least once;
- `{output_dir}` appears when at least one Artifact is declared;
- unknown/malformed placeholders fail verification;
- string parameter values render unchanged; non-string scalars render using the existing canonical scalar semantics (`true`, `false`, `null`, canonical integer/finite decimal text), not inferred CLI flag conventions.

The verifier's trial-count check becomes:

```python
expanded_trial_count = parameter_combination_count * len(payload.seeds)
if not 1 <= expanded_trial_count <= 20:
    findings.append("expanded_trial_count")
```

## 4.3 Task 2 persistence

`0009_execution_preparation.py` creates:

```text
batches
- batch_id UUID PK
- experiment_version_id UUID FK experiment_versions RESTRICT
- start_requested_at timestamptz NULL
- cancel_requested_at timestamptz NULL
- row_version bigint NOT NULL CHECK > 0
- created_at timestamptz NOT NULL

trials
- trial_id UUID PK
- batch_id UUID FK batches RESTRICT
- trial_ordinal bigint NOT NULL CHECK > 0
- trial_identity char(64) NOT NULL
- resolved_parameters_json JSONB NOT NULL
- seed bigint NOT NULL CHECK >= 0
- created_at timestamptz NOT NULL
- UNIQUE(batch_id, trial_identity)
- UNIQUE(batch_id, trial_ordinal)

attempts
- attempt_id UUID PK
- trial_id UUID FK trials RESTRICT
- attempt_number bigint NOT NULL CHECK > 0
- status varchar(40) NOT NULL
- supervisor_execution_identity varchar(200) NOT NULL UNIQUE
- workspace_path text NOT NULL
- frozen_execution_spec_json JSONB NULL
- frozen_execution_spec_hash char(64) NULL
- source_manifest_json JSONB NULL
- source_manifest_sha256 char(64) NULL
- preparation_failure_code varchar(100) NULL
- prepared_at timestamptz NULL
- terminal_at timestamptz NULL
- created_at timestamptz NOT NULL
- UNIQUE(trial_id, attempt_number)
- UNIQUE(trial_id) WHERE terminal_at IS NULL
```

`supervisor_execution_identity` and `workspace_path` are persisted at Attempt creation and are never recomputed for historical Attempts from later settings.

## 4.4 Workspace and source authority

Production setting:

```text
LABPILOT_ATTEMPT_WORKSPACE_ROOT
```

is required outside test mode, must be absolute, and has no guessed default. The PoC path `/var/lib/labpilot-s2-poc/*` is never used as a production default.

Per Attempt:

```text
<workspace_path>/
├── source/
└── runtime/
    └── output/
```

`Proposal.workdir` stays repository-relative. Task 3 physical cwd is later resolved to `<workspace_path>/source/<workdir>`.

After successful materialization:

```text
source directories: readable/executable as needed by labpilot-train, not writable
source regular files: read-only to labpilot-train, preserving executable bit where approved
runtime/output: writable by labpilot-train
```

Task 2 does not add logs/artifacts/checkpoints/metrics directories and does not implement retention or a janitor.

Submodules and Git LFS pointers fail closed as `SOURCE_SUBMODULE_UNSUPPORTED` and `SOURCE_LFS_UNSUPPORTED`; Task 2 never runs `git submodule update`, `git lfs pull`, network download, or worktree fallback.

## 4.5 Crash-safe preparation algorithm

`AttemptPreparationService.prepare_attempt(attempt_id)` uses this sequence:

```text
short UoW:
    load Attempt PREPARING
    persist/reuse workspace_path
    load immutable logical inputs
commit

outside UoW:
    materialize exact source into <workspace_path>.staging
    create runtime/output
    compute SourceManifest
    verify manifest and source identity
    set source permissions
    atomic rename staging -> final workspace

short UoW:
    reload Attempt + parent Trial/Batch authority
    verify still PREPARING
    verify Batch is not cancelled
    persist manifest + hash + FrozenExecutionSpec + hash
    set prepared_at
    status = LAUNCH_READY
commit
```

Resume rules:

```text
PREPARING + no final workspace       -> delete exact staging if present; rematerialize
PREPARING + staging only             -> staging is non-authoritative; delete/rebuild
PREPARING + final workspace present  -> verify final manifest; finalize same Attempt; do not rematerialize
process interruption                 -> remain PREPARING
safety/deterministic preparation failure -> PREPARATION_FAILED + terminal_at
```

## 4.6 Task 2 TDD execution steps

- [ ] **Step 1: Extend Proposal semantics with red tests first.**

Files:

```text
Modify: src/labpilot/experiments/proposal_types.py
Modify: src/labpilot/experiments/proposal_service.py
Modify: src/labpilot/experiments/proposal_verifier.py
Test:   tests/unit/experiments/test_proposal_types.py
Test:   tests/unit/experiments/test_proposal_verifier.py
Test:   tests/integration/test_proposal_lifecycle.py
```

Add failing tests for: new argv template hash participation; legacy missing-key round trip preserving stored hash; placeholder validation; full `parameter_matrix × seeds <= 20` enforcement.

Run:

```bash
uv run pytest tests/unit/experiments/test_proposal_types.py tests/unit/experiments/test_proposal_verifier.py -v
```

Expected before implementation: the new tests fail only for missing argv-template behavior/trial-count behavior.

Implement the minimum changes, rerun the same command, then:

```bash
uv run pytest tests/integration/test_proposal_lifecycle.py -v -m integration
```

Commit:

```bash
git add src/labpilot/experiments tests/unit/experiments tests/integration/test_proposal_lifecycle.py
git commit --file=.git/S2_TASK2_STEP1_MSG --cleanup=verbatim
```

Commit-message file content:

```text
feat: bind proposal execution argv semantics
```

- [ ] **Step 2: Add execution persistence and identity types.**

Files:

```text
Create: src/labpilot/execution/__init__.py
Create: src/labpilot/execution/types.py
Create: src/labpilot/execution/models.py
Create: src/labpilot/execution/repository.py
Create: migrations/versions/0009_execution_preparation.py
Modify: src/labpilot/db/model_registry.py
Modify: tests/architecture/dependency_rules.py
Modify: tests/architecture/test_model_registry.py
Create: tests/unit/execution/test_types.py
Create: tests/unit/execution/test_repository.py
Create: tests/integration/test_execution_schema.py
```

Red tests must prove: Trial identity changes with Version identity/parameters/seed; Attempt X is stable and persisted; unique active Attempt per Trial; `workspace_path` is persisted, not recomputed; fresh migration creates all constraints.

Run red/green cycle:

```bash
uv run pytest tests/unit/execution/test_types.py tests/unit/execution/test_repository.py tests/architecture/test_model_registry.py -v
uv run pytest tests/integration/test_execution_schema.py -v -m integration
```

Commit message:

```text
feat: add slice two execution ledger
```

- [ ] **Step 3: Add narrow execution ports and Coordinator adapters.**

Files:

```text
Create: src/labpilot/execution/ports.py
Create: src/labpilot/coordinator/execution_adapters.py
Create: tests/unit/coordinator/test_execution_adapters.py
```

`VersionExecutionSemanticsAdapter` must re-read `ExperimentVersionRow` plus its exact bound `ProposalRevisionRow`, verify the stored proposal hash and repository snapshot binding, and return `VersionExecutionSemantics`. `RepositorySnapshotMaterializationAdapter` must reuse the accepted snapshot source machinery; it may call `RepositorySnapshotSourceService`/GitAdapter/content store but must never read mutable current worktree bytes as authoritative source.

Red tests must prove wrong bound revision/hash/snapshot fails closed and that `execution` itself imports neither `experiments` nor `context`.

Run:

```bash
uv run pytest tests/unit/coordinator/test_execution_adapters.py tests/architecture/test_module_dependencies.py -v
```

Commit message:

```text
feat: bridge approved inputs into execution
```

- [ ] **Step 4: Implement crash-safe Attempt preparation.**

Files:

```text
Create: src/labpilot/execution/preparation_service.py
Create: tests/unit/execution/test_preparation_service.py
Create: tests/integration/test_attempt_preparation.py
Modify: src/labpilot/settings.py
Modify: tests/unit/test_settings.py
```

The first red tests must cover exact source, read-only source permissions, output root, manifest determinism, tamper detection, interruption leaving PREPARING, final-workspace crash resume, submodule/LFS rejection, legacy Version rejection, and cancel revalidation before `LAUNCH_READY`.

Run:

```bash
uv run pytest tests/unit/execution/test_preparation_service.py tests/unit/test_settings.py -v
uv run pytest tests/integration/test_attempt_preparation.py -v -m integration
```

Commit message:

```text
feat: prepare exact attempt source
```

- [ ] **Step 5: Task 2 gate.**

Run:

```bash
uv run pytest tests/unit/experiments tests/unit/execution tests/unit/coordinator/test_execution_adapters.py tests/architecture -v
uv run pytest tests/integration/test_proposal_lifecycle.py tests/integration/test_execution_schema.py tests/integration/test_attempt_preparation.py -v -m integration
uv run ruff check src tests scripts
uv run mypy src scripts
git diff --check
```

Acceptance requires zero Runner/systemd calls in all Task 2 tests. Stop for review before Task 3.

---

# 5. Task 3 — S2.2 Durable Launch / Observe / Stop

**Risk:** Safety / recovery critical.

**Deliverable:** `LAUNCH_READY` → durable `LAUNCH_PENDING` → one first physical dispatch through Candidate B → observation-driven `RUNNING`/terminal/UNKNOWN → exact-match stop. PostgreSQL remains the durable ledger; Runner remains stateless with respect to domain workflow.

## 5.1 Production physical state contract

Allowed physical lifecycle:

```text
LAUNCH_READY -> LAUNCH_PENDING -> RUNNING -> TERMINAL
LAUNCH_PENDING -> TERMINAL
LAUNCH_PENDING/RUNNING -> UNKNOWN
UNKNOWN -> RUNNING
UNKNOWN -> TERMINAL
```

Pre-launch terminal states remain `PREPARATION_FAILED` and `CANCELLED_PRE_LAUNCH`.

`RUNNING` is written only after authoritative `ACTIVE_MATCH`. A successful Runner LAUNCH response is not `RUNNING` evidence.

Terminal kinds:

```text
PROCESS_EXITED
PROCESS_SIGNALED
PROCESS_TERMINAL_UNCLASSIFIED
LAUNCH_NOT_ACCEPTED
```

`PROCESS_TERMINAL_UNCLASSIFIED` is required when trusted PID-1/systemd evidence proves terminality but exact exit causality/code cannot safely be recovered.

## 5.2 `0010_attempt_physical_execution.py`

Add to `attempts`:

```text
frozen_launch_spec_json JSONB NULL
execution_spec_hash char(64) NULL
launch_intent_committed_at timestamptz NULL
physical_started_at timestamptz NULL
physical_ended_at timestamptz NULL
exit_code integer NULL
termination_signal integer NULL
supervisor_result varchar(100) NULL
terminal_kind varchar(60) NULL
unknown_since timestamptz NULL
attention_required_at timestamptz NULL
execution_issue_code varchar(100) NULL
supervisor_invocation_id varchar(200) NULL
physical_boot_id varchar(200) NULL
```

Do not add Runner receipt tables, launch-delivery tables, lease tables, or reconciliation-case tables.

## 5.3 Production `FrozenLaunchSpec` and H

Task 3 resolves the Task 2 logical spec against Runner `INFO` and persisted Attempt workspace facts. `FrozenLaunchSpec` binds at least:

```text
schema_version
workspace_path
source_manifest_sha256
runtime_profile
runtime_path
train_entrypoint_relative_path
workdir_relative_path
argv with concrete output path
dataset_reference_type
dataset_argument
dataset_reference_hash
timeout_seconds
execution_user
execution_group
launch_profile_version
launch_policy_identity
```

The concrete paths are derived, not independently caller-selectable:

```text
source_root = <workspace>/source
entrypoint  = <workspace>/source/<train_entrypoint>
cwd         = <workspace>/source/<workdir>
output_dir  = <workspace>/runtime/output
```

`H = SHA256(canonical FrozenLaunchSpec)` using the project canonical hashing conventions. Every caller-controlled behavior-changing launch semantic is inside H.

## 5.4 Runner INFO and host policy

Production Runner policy is root-owned, regular-file only, not group/world writable, strict-schema, and fails startup on unknown fields. It contains exactly the bounded host authority required for V1:

```text
controller_user
controller_group
execution_user
execution_group
socket_path
workspace_root
runtime_profiles: profile -> absolute executable
allowed_local_dataset_roots
launch_profile_version
max_runtime_seconds
stop_timeout_seconds
```

`INFO` returns bounded non-secret facts including protocol/runner version, launch profile/policy identity, execution identity names, workspace root, supported runtime profiles/runtime paths, boot ID, and readiness reason.

Coordinator/controller does not maintain a second runtime-profile-to-executable map. If policy identity changes between INFO and LAUNCH, Runner rejects before creation with definite non-acceptance.

## 5.5 First-dispatch authority

`dispatch_attempt(attempt_id)` sequence is fixed:

```text
outside UoW:
    read logical data
    verify source manifest
    Runner.INFO
    resolve runtime + dataset policy
    build FrozenLaunchSpec + H

short UoW:
    lock/reload Attempt
    require LAUNCH_READY
    require parent Batch started and not cancelled
    verify workspace/manifest/logical spec unchanged
    persist FrozenLaunchSpec + H
    set LAUNCH_PENDING + launch_intent_committed_at
commit

in process memory only:
    create FirstDispatchPermit for the CAS winner

outside UoW:
    Runner.LAUNCH(X, H, spec, FIRST_DISPATCH_PROVEN) exactly once
    then OBSERVE
```

`FirstDispatchPermit` is never persisted, serialized, placed in RuntimeEvent, reconstructed after restart, or delivered by Outbox. A fresh process that sees pre-existing `LAUNCH_PENDING` is observe-only.

Send-boundary classification:

```text
failure before any request byte can be sent -> positive NEVER_ACCEPTED_PROVEN may terminalize as LAUNCH_NOT_ACCEPTED
any partial/full send followed by timeout/EOF/unknown response -> CONTROL_UNKNOWN -> OBSERVE only
```

## 5.6 Production Runner/systemd profile

Runner derives an exact unit name from X and exact `Description` metadata from H. It uses the system manager with:

```text
Type=exec
User=labpilot-train
Group=labpilot-train
Restart=no
KillMode=control-group
NoNewPrivileges=yes
AmbientCapabilities=
CapabilityBoundingSet=
StandardOutput=journal
StandardError=journal
RuntimeMaxSec=<H-bound timeout>
TimeoutStopSec=<fixed root-owned policy>
```

Current loaded unit evidence is preferred. After unit GC/controller loss, bounded persistent journald PID-1 manager evidence may establish exact X/H start/terminal facts. Workload stdout is never authoritative physical evidence.

## 5.7 Task 3 TDD execution steps

- [ ] **Step 1: Add physical persistence fields and closed spec/observation types.**

Files:

```text
Modify: src/labpilot/execution/types.py
Modify: src/labpilot/execution/models.py
Modify: src/labpilot/execution/repository.py
Create: migrations/versions/0010_attempt_physical_execution.py
Create: tests/unit/execution/test_physical_types.py
Create: tests/integration/test_attempt_physical_schema.py
```

Red tests: H changes for any behavior-changing concrete launch field; PID/InvocationID/boot ID are excluded from identity; terminal-unclassified permits terminal_at without fabricated exit code.

Run:

```bash
uv run pytest tests/unit/execution/test_physical_types.py -v
uv run pytest tests/integration/test_attempt_physical_schema.py -v -m integration
```

Commit message:

```text
feat: add durable physical attempt state
```

- [ ] **Step 2: Implement production Runner protocol/client.**

Files:

```text
Create: src/labpilot/execution/runner_protocol.py
Create: src/labpilot/execution/runner_client.py
Create: tests/unit/execution/test_runner_protocol.py
Create: tests/unit/execution/test_runner_client.py
```

Wire requirements: AF_UNIX, length-prefixed bounded JSON, strict protocol version, exact field-set rejection, one request per connection, no automatic reconnect/retry, no raw unit/user/group/systemd properties on LAUNCH/STOP.

Red tests must distinguish connect/encode failure before send from partial-send/response-loss uncertainty.

Run:

```bash
uv run pytest tests/unit/execution/test_runner_protocol.py tests/unit/execution/test_runner_client.py -v
```

Commit message:

```text
feat: add narrow production runner protocol
```

- [ ] **Step 3: Implement root policy, systemd supervisor, and daemon.**

Files:

```text
Create: src/labpilot/execution/runner_policy.py
Create: src/labpilot/execution/systemd_supervisor.py
Create: src/labpilot/execution/runner_daemon.py
Create: tests/unit/execution/test_runner_policy.py
Create: tests/unit/execution/test_systemd_supervisor.py
Create: tests/unit/execution/test_runner_daemon.py
Create: tests/architecture/test_execution_privilege_boundary.py
```

Red tests must prove: peer credential mismatch performs zero systemd calls; policy symlink/owner/write checks; same-X/different-H conflict; `NOT_FOUND` does not create without positive creation authority; workload journal stdout cannot fabricate terminal; STOP only after exact active observation; protocol cannot select arbitrary service/property/runtime outside policy.

Run:

```bash
uv run pytest tests/unit/execution/test_runner_policy.py tests/unit/execution/test_systemd_supervisor.py tests/unit/execution/test_runner_daemon.py tests/architecture/test_execution_privilege_boundary.py -v
```

Commit message:

```text
feat: add candidate b production runner
```

- [ ] **Step 4: Implement `AttemptExecutionService`.**

Files:

```text
Create: src/labpilot/execution/execution_service.py
Create: tests/unit/execution/test_execution_service.py
Create: tests/integration/test_attempt_execution_service.py
```

Public application primitives:

```python
async def dispatch_attempt(attempt_id: UUID) -> None
async def reconcile_attempt(attempt_id: UUID) -> None
async def request_stop(attempt_id: UUID) -> None
```

`request_stop()` always observes first and issues STOP only on exact `ACTIVE_MATCH`. There is no public arbitrary force-kill API in Slice 2; systemd's fixed `TimeoutStopSec`/control-group semantics provide bounded shutdown after the durable Batch cancel intent.

Red tests must cover: exactly one CAS winner dispatch; pre-existing LAUNCH_PENDING never LAUNCHes; CREATED does not set RUNNING; ack loss observes without resend; terminal short job; immediate identity-conflict attention; transient UNKNOWN grace; STOP ACK does not terminalize; natural terminal truth is preserved during cancel race.

Run:

```bash
uv run pytest tests/unit/execution/test_execution_service.py -v
uv run pytest tests/integration/test_attempt_execution_service.py -v -m integration
```

Commit message:

```text
feat: reconcile durable attempts with runner
```

- [ ] **Step 5: Task 3 gate.**

Run:

```bash
uv run pytest tests/unit/execution tests/architecture -v
uv run pytest tests/integration/test_attempt_physical_schema.py tests/integration/test_attempt_execution_service.py -v -m integration
uv run ruff check src tests scripts
uv run mypy src scripts
git diff --check
```

Stop for review before Task 4. The gate fails if any test path reconstructs first-dispatch authority from durable state or invokes Runner LAUNCH from Outbox processing.

---

# 6. Task 4 — S2.3 Coordinator Reconciliation + S2.4 API Composition

**Risk:** Workflow / race critical.

**Deliverable:** Thin durable user commands, deterministic single-Coordinator recovery/scheduling, fixed concurrency ≤4 including UNKNOWN, startup reconciliation barrier, and production composition where API has no Runner socket/workspace authority.

## 6.1 Batch command contract

Public commands:

```text
POST /experiment-versions/{experiment_version_id}/batches
POST /batches/{batch_id}/start
POST /batches/{batch_id}/cancel
GET  /batches/{batch_id}
GET  /trials/{trial_id}
GET  /attempts/{attempt_id}
```

Create/Start/Cancel require the existing `Idempotency-Key` pattern. Start/Cancel use `expected_row_version` and existing `execute_idempotent_command()` semantics, where an existing receipt wins before stale-version validation.

Batch creation expands the complete approved `parameter_matrix × seeds`, persists all Trials in deterministic order, and creates zero Attempts. Deterministic order is parameter keys by UTF-8 byte order, each approved value list in stored order, then seeds in stored order.

`Batch.row_version` changes only for user control commands:

```text
Create -> 1
Start  -> +1
Cancel -> +1
Coordinator progress -> no row_version change
```

No writable persisted `Batch.status` column is added. Batch/Trial current state is derived by `query_service.py` from durable commands plus Attempts.

## 6.2 Attempt creation and retry

Only Coordinator may call internal `create_attempt_for_trial(trial_id)`; API never creates Trial/Attempt directly.

Creation revalidates in a short transaction:

```text
Batch started
Batch not cancelled
no active Attempt for Trial
Trial not already successful
attempt count < 1 + max_retries
latest terminal outcome is explicitly retryable when attempt_number > 1
```

Automatic success is only `PROCESS_EXITED` with `exit_code == 0`.

Automatic retry is limited to explicit infrastructure evidence such as proven executor-start failure, temporary filesystem failure, or explicitly proven preemption. Do not automatically retry command non-zero exit, OOM/resource exhaustion, user cancellation, unclassified external termination, `PROCESS_TERMINAL_UNCLASSIFIED`, `UNKNOWN`, or `IDENTITY_CONFLICT`.

A retry always creates a new Attempt and therefore a new X. Same-Attempt relaunch is forbidden.

First Attempts are admitted before retries so one repeatedly failing Trial cannot consume its retry budget ahead of untouched Trials.

## 6.3 Startup barrier and scan order

On every Coordinator process start, before any new physical dispatch:

```text
scan all nonterminal Attempts with status LAUNCH_PENDING/RUNNING/UNKNOWN
for each: apply parent cancel control if possible, then reconcile exact X/H at least once
only after every such Attempt receives one pass may new dispatch occur
```

An existing UNKNOWN need not become known for the barrier to complete; it remains UNKNOWN and keeps its slot.

Every normal scan uses fixed priority:

```text
1. control existing physical/prelaunch work
2. reconcile LAUNCH_PENDING/RUNNING/UNKNOWN
3. resume PREPARING and create/prepare eligible Attempts
4. admit LAUNCH_READY in durable FIFO order
```

Physical active count is:

```text
terminal_at IS NULL AND status IN ('launch_pending', 'running', 'unknown')
```

Maximum is exactly 4. UNKNOWN counts as 1.

FIFO is `LAUNCH_READY ORDER BY created_at, attempt_id`. If the oldest candidate remains unchanged because Runner/DB/observation is transiently unavailable, stop admission for that scan; do not skip it and launch younger work.

## 6.4 Cancellation

`POST /batches/{id}/cancel` only commits `cancel_requested_at`; it performs no socket/systemd call.

Coordinator behavior:

```text
PREPARING/LAUNCH_READY + parent cancel -> CANCELLED_PRE_LAUNCH + terminal_at, no Runner
LAUNCH_PENDING/RUNNING/UNKNOWN + parent cancel -> observe; STOP only if fresh ACTIVE_MATCH
TERMINAL_MATCH during cancel race -> persist observed natural terminal truth
IDENTITY_CONFLICT/UNKNOWN -> preserve cancel intent, no destructive action, no fabricated terminal
```

## 6.5 UNKNOWN projection

First ordinary unknown observation:

```text
unknown_since = now
status remains LAUNCH_PENDING or RUNNING during grace
```

After configured grace (`LABPILOT_UNKNOWN_ATTENTION_GRACE_SECONDS`, default 60 seconds):

```text
status = UNKNOWN
attention_required_at = now
execution_issue_code = PHYSICAL_STATE_UNKNOWN
terminal_at remains NULL
```

`IDENTITY_CONFLICT` becomes UNKNOWN with immediate attention and `execution_issue_code = IDENTITY_CONFLICT`; later generic UNKNOWN cannot downgrade that issue. Only authoritative ACTIVE_MATCH or TERMINAL_MATCH resolves the current attention episode.

## 6.6 API and runtime authority split

API process/container:

```text
PostgreSQL + ordinary app dependencies
NO Runner socket mount
NO Attempt workspace mount
NO direct systemd control capability
```

Coordinator process/container:

```text
PostgreSQL
snapshot/content-store access needed for Task 2
Attempt workspace mounted at the SAME absolute path as host policy
Runner AF_UNIX socket
host-compatible labpilot-controller UID/GID
labpilot-train supplementary GID for workspace preparation
```

Do not create a container-path ↔ host-path translation table.

## 6.7 Task 4 TDD execution steps

- [ ] **Step 1: Implement Batch command and query services.**

Files:

```text
Create: src/labpilot/execution/batch_service.py
Create: src/labpilot/execution/query_service.py
Modify: src/labpilot/observability/payloads.py
Create: tests/unit/execution/test_batch_service.py
Create: tests/unit/execution/test_query_service.py
Create: tests/integration/test_batch_commands.py
```

Red tests: create all Trials/zero Attempts; idempotent replay; stale new key returns conflict; row_version only user commands; cancel-before-start prevents later start; derived state reverses from failed-to-coordinate to running after authoritative recovery.

Run:

```bash
uv run pytest tests/unit/execution/test_batch_service.py tests/unit/execution/test_query_service.py -v
uv run pytest tests/integration/test_batch_commands.py -v -m integration
```

Commit message:

```text
feat: add durable batch commands
```

- [ ] **Step 2: Implement deterministic Coordinator.**

Files:

```text
Create: src/labpilot/coordinator/service.py
Modify: src/labpilot/coordinator/worker.py
Create: tests/unit/coordinator/test_service.py
Modify: tests/unit/coordinator/test_worker.py
Create: tests/integration/test_coordinator_reconciliation.py
```

Expose:

```python
async def run_startup_barrier() -> None
async def run_scan() -> None
```

Red tests must mechanically prove: startup barrier before dispatch; priority control > reconcile > prepare > admit; capacity ≤4 including UNKNOWN; strict FIFO no bypass; restart reads PostgreSQL, not RAM; retries create new Attempt/X; first Attempts before retries.

Run:

```bash
uv run pytest tests/unit/coordinator -v
uv run pytest tests/integration/test_coordinator_reconciliation.py -v -m integration
```

Commit message:

```text
feat: reconcile slice two execution
```

- [ ] **Step 3: Add thin API routes/schemas.**

Files:

```text
Create: src/labpilot/api/schemas/execution.py
Create: src/labpilot/api/routers/execution.py
Modify: src/labpilot/api/routers/experiments.py
Modify: src/labpilot/api/dependencies.py
Modify: src/labpilot/api/app.py
Modify: src/labpilot/api/errors.py
Create: tests/unit/api/test_execution_routes.py
Create: tests/integration/test_execution_api.py
```

Attempt GET may return identifiers/state/timestamps/terminal/UNKNOWN fields but not full `FrozenLaunchSpec`, full environment, secrets, raw journal, or raw systemd properties.

Red tests must prove API handlers call Batch/query services only and cannot import/call `runner_client`, `runner_daemon`, `systemd_supervisor`, or `AttemptExecutionService` physical methods.

Run:

```bash
uv run pytest tests/unit/api/test_execution_routes.py tests/architecture/test_execution_privilege_boundary.py -v
uv run pytest tests/integration/test_execution_api.py -v -m integration
```

Commit message:

```text
feat: expose slice two batch api
```

- [ ] **Step 4: Compose production settings/process authority.**

Files:

```text
Modify: src/labpilot/settings.py
Modify: src/labpilot/main.py
Modify: compose.yaml
Modify: tests/unit/test_settings.py
Modify: tests/unit/test_main.py
Create: tests/integration/test_execution_composition.py
```

Required settings/contracts:

```text
LABPILOT_ATTEMPT_WORKSPACE_ROOT      required outside tests, absolute, no guessed default
LABPILOT_RUNNER_SOCKET_PATH          required for Coordinator execution composition
LABPILOT_RUNNER_REQUEST_TIMEOUT_SECONDS  positive bounded value, default 10
LABPILOT_UNKNOWN_ATTENTION_GRACE_SECONDS positive bounded value, default 60
```

Compose must mount `${LABPILOT_ATTEMPT_WORKSPACE_ROOT}` into Coordinator at the same absolute path and mount the Runner socket only into Coordinator. API receives neither.

Run:

```bash
uv run pytest tests/unit/test_settings.py tests/unit/test_main.py -v
uv run pytest tests/integration/test_execution_composition.py -v -m integration
```

Commit message:

```text
feat: compose coordinator runner boundary
```

- [ ] **Step 5: Task 4 gate.**

Run:

```bash
uv run pytest tests/unit tests/architecture -v
uv run pytest tests/integration -v -m integration
./scripts/check_ci.sh
git diff --check
```

Stop for review before native Task 5. The gate fails if API can reach the Runner socket, Coordinator can parse raw systemd/journal evidence directly, UNKNOWN releases a slot, or startup dispatch happens before the barrier.

---

# 7. Task 5 — S2.5 Real Recovery / Fault-Injection Acceptance

**Risk:** Release critical.

**Deliverable:** Real native-Linux evidence that the production Tasks 2–4 path survives process/database/controller failures and preserves no-duplicate-launch, exact-source, exact-X/H, cancellation, UNKNOWN, capacity, and reboot invariants.

## 7.1 Test-only files

Create:

```text
tests/acceptance/slice2/__init__.py
tests/acceptance/slice2/training_fixture/train.py
tests/acceptance/slice2/faults.py
tests/acceptance/slice2/oracles.py
tests/acceptance/slice2/scenarios.py
tests/acceptance/slice2/test_native_slice2.py
scripts/run_slice2_native_acceptance.py
docs/superpowers/evidence/2026-08-16-labpilot-v1-slice2-production-acceptance.md
```

Test fault decorators may delay/fail communication or delegate and drop a response, but must never fabricate `ACTIVE_MATCH`, `TERMINAL_MATCH`, wrong-H evidence, journal records, Attempt state, or terminal state. Privileged setup/teardown actions are external test-admin actions and are not exposed through production APIs.

The tiny training fixture accepts deterministic argv for seed/value/dataset/output/duration/exit behavior, writes bounded test-oracle records under `runtime/output`, and may spawn a child for control-group cancellation testing. Those records are acceptance oracles only; production reconciliation never trusts them as physical authority.

## 7.2 Required native scenarios

The native suite implements and names these exact cases:

```text
F01 happy vertical approved Version -> real terminal training
F02 exact approved source; dirty worktree ignored; prelaunch source tamper blocks launch
F03 physical concurrency <= 4 and durable FIFO
F04 Coordinator down: Start persists, zero Attempt/process until restart
F05 crash after LAUNCH_PENDING commit before Runner call: observe-only, no same-Attempt launch
F06 systemd accepted but LAUNCH acknowledgement lost: observe-only recovery, start count = 1
F07 SIGKILL Coordinator while RUNNING: workload survives; restart reattaches; start count = 1
F08 workload exits while Coordinator down: restart obtains deterministic terminal evidence
F09 PostgreSQL outage while workload runs: no physical restart; reconcile after DB recovery
F10 durable Batch cancel while Coordinator down: restart observes exact match then stops
F11 cancellation kills whole systemd control group; no orphan child
F12 cancel vs natural-exit race preserves proven natural terminal truth
F13 STOP acknowledgement lost: no direct terminalization; observe to terminal
F14 observation unavailable beyond grace: UNKNOWN, attention, slot retained, no retry
F15 real same-X/different-H systemd fixture: immediate IDENTITY_CONFLICT, no destructive action
F16 one UNKNOWN plus queued work: UNKNOWN consumes one of four physical slots
F17 mixed restart barrier: existing RUNNING/LAUNCH_PENDING/UNKNOWN reconciled before new dispatch
F18 real host reboot: no same-X resurrection; old X/H ends terminal or ADR-bounded UNKNOWN
```

Every scenario uses a disposable DB/schema/workspace/evidence root and a stable installation identity for the entire scenario. Assertions poll bounded DB/physical predicates; arbitrary `sleep(N)` is not an acceptance oracle.

## 7.3 Critical failure classes

Any one is an automatic Slice 2 failure:

```text
C1  same X starts more than one physical workload
C2  UNKNOWN releases capacity
C3  UNKNOWN causes automatic retry
C4  insufficient evidence fabricates TERMINAL
C5  IDENTITY_CONFLICT causes launch/stop/kill
C6  cancel overwrites proven natural terminal result
C7  new dispatch occurs before startup barrier completes
C8  more than four physical-intent Attempts are admitted
C9  mutable current worktree executes instead of approved source
C10 accepted/possibly-accepted same Attempt is relaunched
C11 installation namespace changes within one recovery scenario
C12 API gains Runner/systemd physical-control authority
```

## 7.4 Evidence package

Each scenario stores bounded evidence only:

```text
scenario.json
db-before.json
db-after.json
runner-info.json
physical-observations.jsonl
systemd-exact-unit.json
journal-exact-unit.jsonl
workload-test-oracle.json
verdict.json
```

Evidence includes installation-identity hash, Runner policy identity/version/protocol, boot ID(s), X, H, workspace path, and source manifest hash. It excludes secrets, full environment, unbounded journal, and full DB dumps.

For scenarios intentionally ending UNKNOWN, seal/hash the verdict before external administrator teardown. Teardown must not write fake `terminal_at`, clear UNKNOWN, or mark domain success.

## 7.5 Task 5 execution steps

- [ ] **Step 1: Build fixture, fault decorators, bounded oracles with deterministic tests.**

Run:

```bash
uv run pytest tests/acceptance/slice2 -v -k "not native"
uv run ruff check tests/acceptance/slice2 scripts/run_slice2_native_acceptance.py
uv run mypy scripts/run_slice2_native_acceptance.py
```

Commit message:

```text
test: add slice two production acceptance harness
```

- [ ] **Step 2: Run non-disruptive native F01–F17.**

Run on the dedicated Candidate B native host:

```bash
uv run python scripts/run_slice2_native_acceptance.py --scenarios F01-F17
```

Expected: every scenario verdict passes according to its explicit terminal/UNKNOWN oracle; no critical failure class appears.

- [ ] **Step 3: Human/admin reboot gate for F18.**

Prepare:

```bash
uv run python scripts/run_slice2_native_acceptance.py --scenario F18 --phase prepare
```

After the script reports the exact evidence path and X/H, an authorized human/admin reboots the dedicated VM outside LabPilot.

Verify after reboot:

```bash
uv run python scripts/run_slice2_native_acceptance.py --scenario F18 --phase verify
```

The verifier must never call LAUNCH for the old Attempt.

- [ ] **Step 4: Seal the production acceptance evidence document.**

The document records exact host/runtime versions, commit SHA, F01–F18 verdicts, critical-failure scan, evidence archive checksum, and any ADR-bounded UNKNOWN. Do not claim full Slice 2 acceptance if any scenario lacks its required evidence.

Commit message:

```text
docs: record slice two production acceptance
```

- [ ] **Step 5: Final repository release gate.**

Run in a fully supported Python 3.12 environment with PostgreSQL drivers installed:

```bash
./scripts/check_ci.sh
uv run pytest tests/acceptance/slice2 -v -k "not native"
uv run python -m compileall -q src tests scripts
git diff --check
```

Create a fresh PostgreSQL database and verify migrations from zero through `0010_attempt_physical_execution.py`, then rerun integration tests against that fresh DB.

Perform one independent deep review focused on authority boundaries, replay/ack-loss safety, UNKNOWN semantics, and API privilege isolation.

Only after this gate passes update `DOCUMENT-STATUS.md` once to mark Slice 2 accepted.

---

# 8. Review / stop gates

Implementation must stop for human review at exactly these points:

```text
G0 this r3 plan amendment approved
G1 Task 2 accepted: preparation contract and exact source proven, zero physical launch
G2 Task 3 accepted: Candidate B production launch/observe/stop and ack-loss semantics proven
G3 Task 4 accepted: Coordinator/API composition, startup barrier, capacity, cancellation, privilege split proven
G4 Task 5 native F01–F18 + final repository gate accepted
```

Stop immediately before editing beyond-plan scope if any of these arise:

- production needs a fifth Runner operation or arbitrary raw systemd field;
- execution package appears to require direct imports from experiments/context rather than its approved ports/composition adapters;
- exact source cannot be materialized without mutable worktree fallback/network fetch;
- a real concurrency race requires claims/leases/fencing;
- same-Attempt liveness appears to require reconstructing first-dispatch authority after crash;
- UNKNOWN recovery requires destructive control without fresh ACTIVE_MATCH;
- production Attempt path requires reusing PoC `/var/lib/labpilot-s2-poc/*` assumptions;
- API needs Runner socket/workspace/systemd access;
- native evidence contradicts the selected S2.0 ADR.

When a stop condition is hit, identify the exact file, commit, invariant, and observed evidence before proposing an amendment. Do not silently reinterpret the ADR.

---

# 9. Plan self-review checklist

Before implementation authorization, verify this document against the accepted ADR and current repository:

- Task 1 is historical/complete and no step asks to rerun P0–P13 for familiarity.
- Tasks 2–5 preserve the five-task outer shape.
- Every named production file has one responsibility and follows the current dependency graph.
- `execution` does not import `experiments` or `context`; Coordinator adapters bridge those authorities.
- Legacy Proposal hashes remain immutable; legacy Versions fail closed for execution rather than being silently rewritten.
- `parameter_matrix × seeds` is bounded to 1–20 Trials.
- Attempt workspace root has an explicit deployment authority and no PoC/default path guess.
- Source is exact/read-only; output root is explicit/writable.
- Task 2 has no Runner/systemd side effect.
- `LAUNCH_PENDING` commit precedes physical dispatch; first-dispatch authority is in-memory only.
- Outbox is not a Runner LAUNCH delivery path.
- RUNNING/TERMINAL are observation-driven.
- `PROCESS_TERMINAL_UNCLASSIFIED` prevents invented exit/cancel causality.
- UNKNOWN and IDENTITY_CONFLICT retain capacity and prohibit retry/destructive control.
- Coordinator startup barrier precedes new dispatch.
- API cannot reach the privileged physical boundary.
- Task 5 uses real physical facts and bounded evidence, not fabricated observations.
- `DOCUMENT-STATUS.md` changes only at full Slice 2 acceptance.
