# LabPilot V1 Slice 2 — Trial → Real Training → Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute this plan task-by-task. Do **not** dispatch implementation subagents for this repository. Every production change uses TDD and stops at the review/authorization gates below.

**Goal:** Build one controlled V1 path from an approved `ExperimentVersion` to exact source materialization, real systemd-supervised training, durable restart reconciliation, bounded cancellation, environment observation, and no duplicate physical launch.

**Architecture:** PostgreSQL is the durable execution ledger; the selected Candidate B root Runner is a narrow privileged adapter; systemd plus persistent journald are the physical supervision/evidence authority; Coordinator is the only component that compares durable expectation with physical observation and advances domain state. Task 2 creates only logical/prepared execution state, Task 3 introduces the first production physical side effect, Task 4 composes scheduling/reconciliation/API, and Task 5 proves the combined recovery contract on a real native Linux host.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x async, PostgreSQL 16, Alembic, pytest, Ruff, mypy, AF_UNIX + `SO_PEERCRED`, native Linux systemd/systemctl/systemd-run/journalctl, Docker Compose for API/Coordinator composition.

**Planning baseline — R0 authority root:** `032dd87d3366dc6edd66fb75451d44047e49f7dd`

**Accepted Slice 1 baseline:** `ff7909503c3b755bffb564423de3a56ce5bb9b4a`

**Accepted S2.0 implementation/evidence baseline:** `4109afa9f871ae45cf32c301040bcff6f109939d`

**Post-S2.0 handoff/amendment base:** `536ad6e38c66c667014beb1ac214271452dfd42d`

**Plan-branch CI portability history:** `5fe5f5aa5ae349504ec360665762caccb6c338d2`

**Selected supervision ADR:** `docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-adr.md`

**Native S2.0 evidence:** `docs/superpowers/evidence/2026-08-13-labpilot-v1-slice2-runner-supervision-poc.md`

**Status:** r4.1 is **APPROVED AT G0**. Task 1 remains **COMPLETE / SELECT_B**. Task 2 is **ACCEPTED AT G1**. Task 3 implementation is **COMPLETE**, and Task 3 G2 remains **CHANGES REQUIRED** with 0 Critical and 1 Important finding: `G2-1` through `G2-6` are closed by the finding-specific review, while `G2-7` remains open. Tasks 4–5 remain **NOT AUTHORIZED FOR IMPLEMENTATION**. This governance state does not imply Slice 2 acceptance. `DOCUMENT-STATUS.md` remains unchanged and may be updated only after full Slice 2 acceptance.

**Historical governance note:** Candidate B was admitted and implemented before a separate focused pre-implementation amendment was committed. This plan records the post-S2.0 production contract and does not backdate or rewrite that history.

**G1 finding-specific review:** **APPROVE** with 0 Critical and 0 Important findings. G1-1 (`FrozenExecutionSpec` exact `{output_dir}` argument provenance) and G1-2 (supported-Linux Attempt workspace execution-group ownership) are **CLOSED**. CI evidence is run **#60** at exact HEAD `e5c13fbbc846f0a1c173817a7231e39e071e094a`, completed successfully. This was a finding-specific review of `f908cc0..e5c13fb`, not a new broad Task 2 review.

---

## Global Constraints

- Normative precedence is `V1 Scope v0.3 > Architecture 10 v0.3 > Master Roadmap v0.3 > accepted S2.0 ADR > this approved plan`.
- Preserve the five formal Slice 2 tasks. Internal TDD steps are not additional governance tasks.
- Task 1 is evidence infrastructure only. Production code must not import `tests/poc/s2_runner/**`.
- PostgreSQL is the only durable business/execution ledger. Runner owns no DB, receipt ledger, retry policy, Trial state, Attempt state, or workflow engine.
- Candidate B topology is fixed: non-root controller → narrow AF_UNIX protocol + `SO_PEERCRED` → root-owned narrow Runner → fixed Runner-derived systemd profile → non-root `labpilot-train` workload.
- Runner protocol surface is only `INFO`, `LAUNCH`, `OBSERVE`, `STOP`. No shell, arbitrary exec, raw unit name, raw systemd property, arbitrary PID/signal, scheduler, plugin, or remote-host surface.
- `X` is one stable persisted supervisor execution identity per Attempt. PID, start time, boot ID, InvocationID, journal cursor, and systemd object lifetime are observation facts only.
- `H` is the versioned canonical SHA-256 identity of the concrete `FrozenLaunchSpec`. Same X/H reconciles; same X/different H is durable `IDENTITY_CONFLICT` and fails closed.
- `IDENTITY_CONFLICT` is **not** `UNKNOWN`, is never downgraded to `UNKNOWN`, remains nonterminal with `terminal_at = NULL`, consumes physical capacity, and prohibits stop/retry/relaunch. Slice 2 implements no operator conflict-clear API.
- `NOT_FOUND` is not `NEVER_ACCEPTED_PROVEN`. Possible prior acceptance means observe-only recovery; never blind-create the same Attempt.
- A launch/control response that becomes unknown after any request bytes may have been sent triggers reconciliation, not automatic resend.
- Destructive control requires fresh exact `ACTIVE_MATCH`. `TERMINAL_MATCH`, `IDENTITY_CONFLICT`, and `UNKNOWN` never authorize stop/kill.
- `UNKNOWN` never releases an active physical slot and never authorizes retry.
- V1 has one Coordinator, one local host, and a fixed maximum of four physical-intent Attempts. Do not add leases/claims/fencing unless a demonstrated race proves they are required.
- DB transactions remain short. Git/filesystem/systemd/socket operations happen outside the UoW; after external work, reopen a short transaction and revalidate authority before committing state.
- `RuntimeEvent` remains immutable audit/history. Existing Outbox behavior remains available, but Outbox delivery must never invoke Runner `LAUNCH`, reconstruct first-dispatch authority, or become a physical-execution queue.
- `DOCUMENT-STATUS.md` is updated once, only after full Slice 2 acceptance.
- Do not implement a generic Runner backend registry, remote/SSH runner, multi-host scheduler, worker pool/resource allocator, generic workflow engine, Runner DB, Docker/systemd privileged socket exposure, generic shell/exec protocol, chaos platform, production fault-injection API, or generic environment platform.

---

# 0. r4/r4.1 targeted remediation scope

r4 closed the focused review of r3. r4.1 changes only the direct deployment regression R4-D1 and its mechanically required consistency checks; it does not reopen accepted architecture choices.

```text
R4-1  record exact R0 Planning baseline and exact Slice 1 acceptance baseline
R4-2  make IDENTITY_CONFLICT a distinct durable Attempt state, never UNKNOWN
R4-3  carry full EnvironmentExpectation into execution and persist bounded observed environment
R4-4  map Task 5 explicitly to API restart, failed launch, timeout, restart-during-reconciliation
R4-5  define the production root-owned Runner deployment/service/socket contract
R4-6  freeze byte-level Trial/X/workspace/SourceManifest/FrozenSpec identities
R4-7  define durable launch/start/restart-reconcile/terminal audit events
R4-8  clarify retry eligibility wording
R4-9  define ArtifactDeclaration.relative_path relative to Attempt runtime/output
R4-10 freeze final ExecStart composition and argv safety budgets
R4-D1 restore tested root Runner service hardening: Python -E -s, NoNewPrivileges=yes, RestrictAddressFamilies=AF_UNIX, UMask=0077; keep socket sharing explicit
```

The final G0 re-review checks R4-D1 and direct consistency edits only. A second broad architecture review is not required unless this revision materially widens the accepted Candidate B authority surface.

## 0.1 Task 3 G2 finding status

Task 3 G2 remains **CHANGES REQUIRED** with 0 Critical and 1 Important finding open:

```text
G2-1  CLOSED — restore exact X -> X.service supervisor identity mapping
G2-2  CLOSED — canonicalize INFO inspection without rewriting accepted G1 identity
G2-3  CLOSED — implement the frozen ordinary-UNKNOWN grace state machine
G2-4  CLOSED — run installer INFO readiness as labpilot-controller:labpilot-control
G2-5  CLOSED — preserve historical journal _BOOT_ID provenance
G2-6  CLOSED — explicitly activate the newly installed Runner tree/policy on update
G2-7  OPEN — a syntactically valid but policy-unadmitted INFO python_profile can raise ValueError before probe execution and escape the Runner connection boundary
```

Task 4 and Task 5 remain **NOT AUTHORIZED**. Task 5 native root/systemd/persistent-journald acceptance has not been performed. `DOCUMENT-STATUS.md` remains unchanged.

---

# 1. Formal Slice 2 task status

```text
Task 1 — S2.0 Runner Supervision PoC + ADR                        COMPLETE / SELECT_B
Task 2 — S2.1 Attempt Preparation + Exact Source Materialization  ACCEPTED AT G1
Task 3 — S2.2 Durable Launch / Observe / Stop                     G2 CHANGES REQUIRED (implementation complete; G2-1..G2-6 closed; G2-7 open)
Task 4 — S2.3 Coordinator Reconciliation + S2.4 API Composition  NOT AUTHORIZED
Task 5 — S2.5 Real Recovery / Fault-Injection Acceptance         NOT AUTHORIZED
```

Task 1's accepted evidence stack is the ADR plus S2.0 evidence document above. Do not rerun P0–P13 unless a later approved production change materially changes a physical invariant that those scenarios established.

---

# 2. Production file ownership map

The current dependency guard intentionally keeps `labpilot.execution` independent of `experiments` and `context`; `execution` may depend only on `common`, `db`, `observability`, and itself. Preserve that rule. Cross-domain composition belongs in `coordinator`, which is already permitted to depend on both `experiments` and `execution`.

**Create in `src/labpilot/execution/`:**

- `__init__.py` — package marker only.
- `types.py` — closed execution enums/value objects, canonical Trial/Attempt identities, manifest/spec types.
- `models.py` — SQLAlchemy `BatchRow`, `TrialRow`, `AttemptRow`.
- `repository.py` — DB queries/CAS/locks for Batch/Trial/Attempt.
- `ports.py` — narrow protocols consumed by preparation/execution without importing `experiments` or `context`.
- `preparation_service.py` — Task 2 exact-source preparation and crash-safe resume.
- `runner_protocol.py` — production INFO/LAUNCH/OBSERVE/STOP wire contract.
- `runner_client.py` — non-root AF_UNIX client and send-boundary outcome classification.
- `runner_policy.py` — root-owned production host-policy parsing/validation and policy identity.
- `runtime_probe.py` — fixed non-shell Python runtime/environment probe source and bounded parser; no user-provided code.
- `systemd_supervisor.py` — exact X/H systemd/journal adapter.
- `runner_daemon.py` — privileged narrow adapter and service entrypoint; no DB/domain imports.
- `execution_service.py` — Task 3 dispatch/reconcile/stop application primitives.
- `batch_service.py` — Task 4 user command service for Create/Start/Cancel.
- `query_service.py` — derived Batch/Trial/Attempt reads; no persisted Batch status projection.

**Create in `src/labpilot/coordinator/`:**

- `execution_adapters.py` — composition-layer adapters translating accepted `experiments`/`context` authority into `execution.ports`.
- `service.py` — startup barrier and deterministic scan ordering.

**Modify existing production files:**

- `src/labpilot/experiments/proposal_types.py`
- `src/labpilot/experiments/proposal_service.py`
- `src/labpilot/experiments/proposal_verifier.py`
- `src/labpilot/observability/events.py`
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

**Create host deployment files:**

- `deploy/systemd/labpilot-runner.service` — fixed root-managed service unit preserving the selected Candidate B service hardening.
- `scripts/install_labpilot_runner_host.sh` — narrow root install/update script for an exact committed source tree plus an explicit policy file; not a general installer platform.

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
IDENTITY_CONFLICT != UNKNOWN
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

## 4.1 Canonical identity and path contract

Use the existing `labpilot.common.hashing.namespaced_sha256()` and `canonical_json_bytes()` semantics. Do not invent a second JSON canonicalizer.

Freeze these namespaces:

```text
Trial identity                  labpilot-trial:v1
Supervisor installation digest labpilot-supervisor-installation:v1
SourceManifest                  labpilot-source-manifest:v1
FrozenExecutionSpec             labpilot-frozen-execution-spec:v1
FrozenLaunchSpec / H            labpilot-frozen-launch-spec:v1
Runner host policy              labpilot-runner-policy:v1
```

Trial identity is exactly:

```python
trial_identity = namespaced_sha256(
    "labpilot-trial:v1",
    {
        "experiment_version_identity": experiment_version_identity,
        "resolved_parameters": canonical_resolved_parameters,
        "seed": seed,
    },
)
```

`canonical_resolved_parameters` uses the existing scalar semantics: `None`, `str`, `int`, `bool`, and finite float converted through `Decimal(str(value))`. Object keys are therefore ordered by the existing canonical JSON UTF-8 byte ordering.

Supervisor execution identity is exactly:

```python
installation_digest = namespaced_sha256(
    "labpilot-supervisor-installation:v1",
    {"installation_identity": settings.installation_identity},
)
X = f"labpilot-v1-{installation_digest}-{attempt_id.hex}"
unit_name = f"{X}.service"
```

`X` is generated and persisted when the Attempt row is created. Historical X is never recomputed from a later `installation_identity` setting.

Production workspace root must be an existing absolute canonical directory with no symlink component (`Path.resolve(strict=True) == configured_path`). Per Attempt:

```python
workspace_path = attempt_workspace_root / attempt_id.hex
```

The exact absolute string is persisted at Attempt creation and never recomputed for historical Attempts.

## 4.2 Interfaces produced by Task 2

`src/labpilot/execution/types.py` exposes at least:

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
    IDENTITY_CONFLICT = "identity_conflict"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class VersionExecutionSemantics:
    project_id: UUID
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
    environment_expectation_hash: str
    python_profile: str
    framework_requirements: tuple[str, ...]
    device_requirement: str | None
    cuda_requirement: str | None
    artifact_relative_paths: tuple[str, ...]
    timeout_seconds: int
    max_retries: int


@dataclass(frozen=True, slots=True)
class SourceManifestEntry:
    relative_path: str
    file_type: str       # regular | symlink
    file_mode: str       # 100644 | 100755 | 120000
    content_sha256: str  # SHA-256 of raw blob bytes; symlink = raw link-target bytes


@dataclass(frozen=True, slots=True)
class SourceManifest:
    schema_version: str  # "v1"
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    entries: tuple[SourceManifestEntry, ...]


@dataclass(frozen=True, slots=True)
class FrozenExecutionSpec:
    schema_version: str  # "v1"
    experiment_version_id: UUID
    experiment_version_identity: str
    trial_identity: str
    train_entrypoint_relative_path: str
    workdir_relative_path: str
    python_profile: str
    argv: tuple[str, ...]
    output_dir_arg_indexes: tuple[int, ...]
    dataset_reference_type: str
    dataset_argument: str
    dataset_reference_hash: str
    environment_expectation_hash: str
    framework_requirements: tuple[str, ...]
    framework_distribution_names: tuple[str, ...]
    device_requirement: str | None
    cuda_requirement: str | None
    timeout_seconds: int
```

`FrozenExecutionSpec.argv` is fully resolved for parameter/seed/dataset semantics, except the literal token `"{output_dir}"` remains as the only host-dependent substitution for Task 3. `output_dir_arg_indexes` contains the zero-based, strictly ascending, unique, in-bounds positions that originated from exact `"{output_dir}"` tokens in the original Proposal template. Parameter or dataset values equal to `"{output_dir}"` remain ordinary values and create no index. Multiple real output slots are allowed and all are retained. Task 3 substitutes only these recorded positions and must not scan rendered argv for string equality. No other unresolved template token is legal.

The canonical `FrozenExecutionSpec` value includes `"output_dir_arg_indexes": list(spec.output_dir_arg_indexes)`. This is the G1-accepted v1 representation; no v2 namespace or schema is introduced.

`src/labpilot/execution/ports.py` defines dependency-inversion contracts; concrete adapters live in Coordinator:

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

## 4.3 Proposal execution semantics extension

`ProposalPayload` gains one optional persisted field placed at the end of the dataclass:

```python
train_argv_template: tuple[str, ...] | None = None
```

Legacy revisions where the JSON key is physically absent remain readable and keep their existing stored `payload_hash`; the deserializer must not materialize the missing key into a rehashed legacy payload. Legacy Versions remain queryable but Task 2 rejects them as `EXECUTION_SEMANTICS_UNSUPPORTED`.

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
- string parameter values render unchanged; non-string scalars use the canonical scalar semantics above, not inferred CLI flag conventions;
- `framework_requirements` are preserved exactly from the approved `EnvironmentExpectation`; for bounded observation, execution derives the maximal leading ASCII distribution-name token matching `[A-Za-z0-9][A-Za-z0-9._-]{0,127}` from each requirement. If a non-empty framework requirement has no such leading token, preparation fails `EXECUTION_ENVIRONMENT_UNSUPPORTED` rather than guessing a package name.

The verifier's Trial count becomes:

```python
expanded_trial_count = parameter_combination_count * len(payload.seeds)
if not 1 <= expanded_trial_count <= 20:
    findings.append("expanded_trial_count")
```

### Argv safety contract

Proposal template literals and rendered values must be valid UTF-8 without isolated surrogates and must not contain NUL. The final Task 3 `ExecStart` vector is exactly:

```text
(runtime_path, absolute_source_entrypoint, *rendered_argv)
```

Budgets are fixed for V1:

```text
rendered_argv tokens                    <= 256
final ExecStart tokens                  <= 258
one token UTF-8 bytes                   <= 8192
sum(len(token_utf8) + 1) for ExecStart <= 32768
NUL                                     forbidden
```

Proposal verification rejects an already-invalid literal template token. Task 2 rechecks the fully rendered argv budget before `LAUNCH_READY`, because parameter/dataset substitutions may be larger than template literals.

### Artifact path contract

For every `ArtifactDeclaration`:

```text
ArtifactDeclaration.relative_path
= path relative to <workspace_path>/runtime/output
```

It is never relative to source cwd and may never be absolute or escape the output root. Slice 3 collection must use this same interpretation.

## 4.4 Task 2 persistence

`0009_execution_preparation.py` creates:

```text
batches
- batch_id UUID PK
- project_id UUID FK projects RESTRICT
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

`project_id` is immutable Batch owner context used by execution audit events; it is not a second authority for ExperimentVersion semantics.

`frozen_execution_spec_hash` is:

```python
namespaced_sha256("labpilot-frozen-execution-spec:v1", canonical_spec_value)
```

## 4.5 Workspace and source authority

Production setting:

```text
LABPILOT_ATTEMPT_WORKSPACE_ROOT
```

is required outside test mode, absolute/canonical as specified in §4.1, and has no guessed default. The PoC path `/var/lib/labpilot-s2-poc/*` is never a production default.

`AttemptPreparationService` also requires an explicit numeric `execution_gid: int` with `type(execution_gid) is int` and `execution_gid >= 0`. It is preparation/deployment authority, not scientific execution semantics, and does not enter Trial identity, `FrozenExecutionSpec`, or `FrozenLaunchSpec`/H. Future production composition resolves the fixed `labpilot-train` GID; Task 2 does not infer it from the process, workspace, environment, account lookup, or Runner.

Per Attempt:

```text
<workspace_path>/
├── source/
└── runtime/
    └── output/
```

`Proposal.workdir` stays repository-relative. Task 3 physical cwd is `<workspace_path>/source/<workdir>`.

After successful materialization:

```text
<workspace>                  group = execution_gid, mode 0750
<workspace>/source dirs      group = execution_gid, mode 0550
source regular non-exec      group = execution_gid, mode 0440
source regular executable    group = execution_gid, mode 0550
<workspace>/runtime          group = execution_gid, mode 0770
<workspace>/runtime/output   group = execution_gid, mode 0770
```

On supported POSIX/Linux hosts, preparation changes only group ownership of the Attempt workspace and its non-symlink descendants, never follows symlinks, and re-verifies exact GID/modes before `LAUNCH_READY`. It does not mutate the administrator-provisioned `attempt_workspace_root`. Failure to establish or verify ownership/modes fails closed. Windows remains a development/test platform and is not native Linux ownership evidence. Nothing is world-writable.

Task 2 does not add logs/artifacts/checkpoints/metrics directories and does not implement retention or a janitor.

### SourceManifest byte contract

- Git directories are implicit and are not manifest entries because Git has no tracked empty-directory semantic.
- Manifest entries contain only regular files (`100644`/`100755`) and symlinks (`120000`).
- Entry order is ascending `relative_path.encode("utf-8")`.
- `content_sha256` is plain SHA-256 of the exact Git blob bytes.
- For a symlink, the Git blob bytes are the raw link-target bytes and are hashed before any filesystem creation.
- A symlink target must decode as UTF-8, must not be absolute, and lexical resolution relative to the symlink's parent must remain within `source/`; otherwise preparation fails `SOURCE_SYMLINK_ESCAPE`.
- Gitlink/submodule mode `160000` fails `SOURCE_SUBMODULE_UNSUPPORTED`.
- A Git LFS pointer blob fails `SOURCE_LFS_UNSUPPORTED`; Task 2 never runs `git lfs pull`.
- Manifest hash is `namespaced_sha256("labpilot-source-manifest:v1", manifest_value)` over `schema_version="v1"`, exact snapshot id/identity, and the ordered entries.
- Current worktree bytes are never used as fallback source.

## 4.6 Crash-safe preparation algorithm

`AttemptPreparationService.prepare_attempt(attempt_id)`:

```text
short UoW:
    load Attempt PREPARING
    persist/reuse exact X and workspace_path
    load immutable logical inputs
commit

outside UoW:
    materialize exact source into <workspace_path>.staging
    create runtime/output
    compute SourceManifest with §4.5 byte contract
    verify manifest and source identity
    render logical argv and enforce §4.3 budgets
    build FrozenExecutionSpec including full EnvironmentExpectation + hash
    set exact Attempt workspace group ownership and permissions
    atomic rename staging -> final workspace
    re-verify exact final GID/modes

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
PREPARING + final workspace present  -> verify exact final manifest plus GID/modes; finalize same Attempt; do not rematerialize
process interruption                 -> remain PREPARING
safety/deterministic preparation failure -> PREPARATION_FAILED + terminal_at
```

## 4.7 Task 2 TDD execution steps

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

Add failing tests for: argv-template hash participation; legacy missing-key round trip preserving stored hash; placeholder validation; NUL/UTF-8/literal-token bounds; full `parameter_matrix × seeds <= 20` enforcement.

Run:

```bash
uv run pytest tests/unit/experiments/test_proposal_types.py tests/unit/experiments/test_proposal_verifier.py -v
```

Implement the minimum changes and rerun, then:

```bash
uv run pytest tests/integration/test_proposal_lifecycle.py -v -m integration
```

Commit message:

```text
feat: bind proposal execution argv semantics
```

- [ ] **Step 2: Add execution persistence and exact identity types.**

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

Red tests must assert exact golden vectors for `trial_identity`, installation digest/X, workspace path, SourceManifest hash, and FrozenExecutionSpec hash; also prove unique active Attempt per Trial and fresh migration constraints.

Run:

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

`VersionExecutionSemanticsAdapter` re-reads `ExperimentVersionRow` plus its exact bound `ProposalRevisionRow`, verifies proposal hash, repository snapshot binding, DatasetReference hash, and full EnvironmentExpectation hash, and returns all fields in `VersionExecutionSemantics`. `RepositorySnapshotMaterializationAdapter` reuses accepted snapshot source machinery and never reads mutable current worktree bytes as authoritative source.

Red tests prove wrong revision/hash/snapshot/dataset/environment binding fails closed and `execution` imports neither `experiments` nor `context`.

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

Red tests cover exact source, regular/symlink manifest golden vectors, symlink escape, submodule/LFS rejection, read-only source permissions, output root, rendered argv limits, tamper detection, interruption leaving PREPARING, final-workspace crash resume, legacy Version rejection, full EnvironmentExpectation carry-through, and cancel revalidation before `LAUNCH_READY`.

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

Acceptance requires zero Runner/systemd calls in all Task 2 tests. G1 is satisfied by the finding-specific approval recorded above; Task 3 starts in a new implementation window from the governance closure commit containing this record.

---

# 5. Task 3 — S2.2 Durable Launch / Observe / Stop

**Risk:** Safety / recovery critical.

**Deliverable:** `LAUNCH_READY` → durable `LAUNCH_PENDING` → one first physical dispatch through Candidate B → observation-driven `RUNNING`/terminal/UNKNOWN/IDENTITY_CONFLICT → exact-match stop. Persist bounded observed environment before first dispatch. PostgreSQL remains the durable ledger; Runner remains stateless with respect to domain workflow.

## 5.1 Production physical state contract

Allowed lifecycle:

```text
LAUNCH_READY -> LAUNCH_PENDING -> RUNNING -> TERMINAL
LAUNCH_PENDING -> TERMINAL
LAUNCH_PENDING/RUNNING -> UNKNOWN
UNKNOWN -> RUNNING
UNKNOWN -> TERMINAL
LAUNCH_PENDING/RUNNING/UNKNOWN -> IDENTITY_CONFLICT
```

`IDENTITY_CONFLICT` is sticky for Slice 2: no automatic transition out, no operator clear endpoint, `terminal_at` remains NULL, and it continues to consume capacity. External test/admin cleanup may remove a conflicting physical fixture only after evidence is sealed; it must not rewrite the Attempt row.

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
observed_environment_json JSONB NULL
observed_environment_recorded_at timestamptz NULL
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

Do not add Runner receipt tables, launch-delivery tables, lease tables, reconciliation-case tables, or environment inventory tables.

## 5.3 Bounded observed environment

Scope/Architecture require each Attempt to retain enough observed environment to later validate the approved `EnvironmentExpectation`; Slice 2 records the observation but does not implement Slice 3 scientific validation.

`ObservedEnvironment` is an Attempt-owned versioned JSON value object:

```text
schema_version = "v1"
environment_expectation_hash
python_profile
runtime_path
python_version
framework_versions: ordered [{distribution_name, installed_version|null}]
cuda_runtime_version: integer|null
cuda_device_count: integer|null
probe_issue_codes: ordered bounded list
```

The observation is collected by Runner `INFO` immediately before `FrozenLaunchSpec` construction. It is persisted once with the `LAUNCH_PENDING` commit and is not overwritten by later restart reconciliation.

`INFO` remains the same Runner operation; it accepts an optional bounded inspection request:

```text
python_profile
framework_distribution_names <= 32
one normalized ASCII distribution name <= 128 bytes
```

Runner resolves only root-policy-admitted `python_profile`. It executes a fixed Runner-owned Python `-c` probe under `labpilot-train` using the selected absolute runtime and exact policy environment, with `shell=False`. The fixed probe uses Python stdlib only:

- `sys.version` for Python observation;
- `importlib.metadata.version(name)` for only the requested distribution names;
- `ctypes` best-effort loading of `libcudart.so` for CUDA runtime version/device count; failure is represented by bounded issue codes, not arbitrary stderr.

Caller cannot provide probe source, module names to import, executable path, shell text, or filesystem path.

If the selected `python_profile` does not exist in current Runner policy, dispatch fails before launch. Other observed framework/CUDA mismatches are recorded for later deterministic Validation; Task 3 does not silently rewrite the approved expectation.

## 5.4 Production `FrozenLaunchSpec` and H

Task 3 resolves Task 2 logical semantics against Runner `INFO` and persisted workspace facts. `FrozenLaunchSpec` binds exactly:

```text
schema_version = "v1"
workspace_path
source_manifest_sha256
python_profile
runtime_path
train_entrypoint_relative_path
workdir_relative_path
argv with concrete output path substituted only at recorded output_dir_arg_indexes
dataset_reference_type
dataset_argument
dataset_reference_hash
environment: exact non-secret root-policy environment pairs
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
ExecStart   = (runtime_path, entrypoint, *argv)
```

Task 3 replaces only `FrozenExecutionSpec.output_dir_arg_indexes` with the concrete absolute `output_dir`; it must not scan argv for `"{output_dir}"` string equality. It then rechecks the §4.3 final ExecStart token/byte budget.

The only V1 policy environment keys admitted are:

```text
PATH
PYTHONPATH
LD_LIBRARY_PATH
CUDA_HOME
CUDA_VISIBLE_DEVICES
OMP_NUM_THREADS
MKL_NUM_THREADS
TOKENIZERS_PARALLELISM
```

The policy may omit any/all of them. Caller cannot add or override environment keys. Secret/credential environment injection is not part of Slice 2. Exact admitted key/value pairs are included in `FrozenLaunchSpec` and therefore H.

`ObservedEnvironment` is evidence and is **not** part of H. H remains identity/integrity of the frozen physical launch request, not a mutable host-inventory fingerprint.

H is exactly:

```python
H = namespaced_sha256("labpilot-frozen-launch-spec:v1", canonical_launch_spec_value)
```

## 5.5 Runner INFO and host policy

Production Runner policy is root-owned, regular-file only, not group/world writable, strict-schema, and fails startup on unknown fields. It contains exactly:

```text
controller_user
controller_group
execution_user
execution_group
socket_path
workspace_root
runtime_profiles:
    profile -> {
        runtime_path: absolute executable,
        environment: exact subset of the fixed safe keys in §5.4
    }
allowed_local_dataset_roots
launch_profile_version
max_runtime_seconds
stop_timeout_seconds
```

Policy identity is:

```python
namespaced_sha256("labpilot-runner-policy:v1", canonical_nonsecret_policy_value)
```

`INFO` returns bounded non-secret facts including protocol/runner version, launch profile/policy identity, execution identity names, canonical workspace root, supported runtime profiles/runtime paths/policy environments, boot ID, readiness reason, and the optional bounded environment observation from §5.3.

Coordinator does not maintain a second runtime-profile map. Runner LAUNCH recomputes/validates the policy identity; if policy changes after INFO and before any systemd creation, LAUNCH returns definite non-acceptance.

## 5.6 Production root Runner deployment contract

Repository deployment artifacts:

```text
deploy/systemd/labpilot-runner.service
scripts/install_labpilot_runner_host.sh
```

Installed host contract:

```text
application tree: /opt/labpilot-runner/app
owner/mode:       recursively root:root; no group/world write
python:           /opt/labpilot-runner/app/.venv/bin/python
policy:           /etc/labpilot/runner-policy.json
policy owner/mode root:root 0600; regular file; no symlink
service unit:     /etc/systemd/system/labpilot-runner.service
unit owner/mode:  root:root 0644
runtime dir:      /run/labpilot-runner
runtime owner:    root:labpilot-control
runtime mode:     0750
socket:           /run/labpilot-runner/control.sock
socket owner:     root:labpilot-control
socket mode:      0660
```

`deploy/systemd/labpilot-runner.service` freezes:

```ini
[Unit]
Description=LabPilot Candidate B Runner
After=local-fs.target

[Service]
Type=simple
User=root
Group=labpilot-control
UMask=0077
RuntimeDirectory=labpilot-runner
RuntimeDirectoryMode=0750
ExecStart=/opt/labpilot-runner/app/.venv/bin/python -E -s -m labpilot.execution.runner_daemon --policy /etc/labpilot/runner-policy.json
Restart=on-failure
RestartSec=1s
NoNewPrivileges=yes
RestrictAddressFamilies=AF_UNIX

[Install]
WantedBy=multi-user.target
```

`Group=labpilot-control` is a production-specific specialization for the runtime-directory/socket boundary; it does not widen default daemon-created-file access because the service-wide umask remains `0077`.

The daemon creates the AF_UNIX socket under that restrictive umask and, before it reports readiness or accepts a request, explicitly applies and verifies the final socket metadata:

```text
owner uid = 0
owner group = labpilot-control gid from the validated root-owned policy/host identity
mode = 0660
```

Failure to set or re-read exact final socket ownership/mode is a fatal startup error. Other daemon-created files retain the restrictive service umask and are not implicitly exposed to `labpilot-control`.

The installer is intentionally narrow:

1. must run as root;
2. accepts an exact committed Git SHA and an explicit policy source file path;
3. uses `git archive <sha>` to populate `/opt/labpilot-runner/app` so the daemon never imports a controller-writable checkout;
4. runs `uv sync --locked --no-dev` in the root-owned install tree;
5. installs policy/service with the exact ownership/modes above;
6. `systemctl daemon-reload`, then `systemctl enable labpilot-runner.service`, then an explicit `systemctl restart labpilot-runner.service`; `restart` starts an inactive unit on first installation and replaces an already-active process on update so subsequent checks exercise the newly installed exact tree and in-memory policy;
7. only after that activation, verifies the **effective loaded service** has exact `ExecStart` Python `-E -s`, `UMask=0077`, `NoNewPrivileges=yes`, and `RestrictAddressFamilies=AF_UNIX`; then verifies service active state, runtime-directory ownership/mode, socket ownership/mode, and controller-identity `INFO` readiness.

The effective-service check must interrogate systemd's loaded unit/properties after `daemon-reload`; checking only the repository unit file is insufficient.

No site-specific `workspace_root` or runtime path is guessed by the repository. The explicit policy source supplied to the installer owns those deployment values.

## 5.7 First-dispatch authority

`dispatch_attempt(attempt_id)`:

```text
outside UoW:
    read logical data
    verify source manifest
    Runner.INFO with bounded environment-inspection request
    require Runner canonical workspace_root == configured/persisted root authority
    resolve runtime + local-dataset policy
    capture ObservedEnvironment
    build FrozenLaunchSpec + H
    enforce final ExecStart budget

short UoW:
    lock/reload Attempt
    require LAUNCH_READY
    require parent Batch started and not cancelled
    verify workspace/manifest/logical spec unchanged
    persist ObservedEnvironment once
    persist FrozenLaunchSpec + H
    set LAUNCH_PENDING + launch_intent_committed_at
    append attempt.launch_intent_committed RuntimeEvent with enqueue_outbox=False
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

## 5.8 Durable audit contract and non-destructive Outbox bridge

Architecture 10 requires durable history for launch intent, physical start, restart reconciliation, and terminal observation.

Modify the accepted foundation compatibly:

```python
async def append_runtime_event(
    uow: UnitOfWork,
    envelope: RuntimeEventEnvelope,
    *,
    enqueue_outbox: bool = True,
) -> RuntimeEventRow:
    ...
```

Default remains `True`, so all historical call sites keep accepted behavior. Slice 2 uses `enqueue_outbox=False` for audit-only events because no asynchronous consumer is required for physical execution.

Minimum immutable Attempt events:

```text
attempt.launch_intent_committed
attempt.physical_started_observed
attempt.reconciled_after_restart
attempt.identity_conflict_observed
attempt.terminal_observed
```

Event rules:

- `launch_intent_committed` is appended in the same UoW as `LAUNCH_PENDING`.
- `physical_started_observed` is appended only on the first authoritative transition to RUNNING, same UoW.
- `reconciled_after_restart` is appended once per Attempt per Coordinator startup-barrier pass, even if the current durable status does not change.
- `identity_conflict_observed` is appended in the same UoW that sets durable `IDENTITY_CONFLICT`.
- `terminal_observed` is appended in the same UoW that sets terminal fields.

`attempt.reconciled_after_restart` payload contains bounded fields only:

```text
attempt_id
X
H
coordinator_start_id
pre_reconciliation_status
physical_observation_kind
post_reconciliation_status
physical_boot_id if observed
supervisor_invocation_id if observed
```

No event contains secret/full environment/raw journal. The authoritative current row plus immutable selected RuntimeEvents must answer both “what is true now?” and “what happened across restart?”.

## 5.9 Production Runner/systemd profile

Runner derives unit name only from X and `Description` metadata only from H. It uses the system manager with:

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

## 5.10 Task 3 TDD execution steps

- [ ] **Step 1: Add physical persistence, closed state types, and audit bridge.**

Files:

```text
Modify: src/labpilot/execution/types.py
Modify: src/labpilot/execution/models.py
Modify: src/labpilot/execution/repository.py
Modify: src/labpilot/observability/events.py
Modify: src/labpilot/observability/payloads.py
Create: migrations/versions/0010_attempt_physical_execution.py
Create: tests/unit/execution/test_physical_types.py
Modify: tests/unit/observability/test_events.py
Create: tests/integration/test_attempt_physical_schema.py
```

Red tests assert: exact H golden vector; behavior-changing launch fields change H; observation-only fields do not; `IDENTITY_CONFLICT` is distinct from UNKNOWN; terminal-unclassified permits terminality without invented exit code; `enqueue_outbox=False` appends RuntimeEvent without Outbox while default behavior remains unchanged.

Run:

```bash
uv run pytest tests/unit/execution/test_physical_types.py tests/unit/observability/test_events.py -v
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

Wire requirements: AF_UNIX, length-prefixed bounded JSON, strict protocol version, exact field-set rejection, one request per connection, no automatic reconnect/retry, INFO inspection bounded as §5.3, and no raw unit/user/group/systemd property selection on LAUNCH/STOP.

Red tests distinguish connect/encode failure before send from partial-send/response-loss uncertainty.

Run:

```bash
uv run pytest tests/unit/execution/test_runner_protocol.py tests/unit/execution/test_runner_client.py -v
```

Commit message:

```text
feat: add narrow production runner protocol
```

- [ ] **Step 3: Implement root policy, fixed runtime probe, systemd supervisor, and daemon.**

Files:

```text
Create: src/labpilot/execution/runner_policy.py
Create: src/labpilot/execution/runtime_probe.py
Create: src/labpilot/execution/systemd_supervisor.py
Create: src/labpilot/execution/runner_daemon.py
Create: tests/unit/execution/test_runner_policy.py
Create: tests/unit/execution/test_runtime_probe.py
Create: tests/unit/execution/test_systemd_supervisor.py
Create: tests/unit/execution/test_runner_daemon.py
Create: tests/architecture/test_execution_privilege_boundary.py
```

Red tests prove: policy symlink/owner/write checks; exact policy hash; safe environment-key allowlist; peer credential mismatch performs zero systemd calls; INFO runtime probe cannot select arbitrary executable/code; same-X/different-H returns conflict; NOT_FOUND does not create without positive creation authority; workload stdout cannot fabricate terminal; STOP only after exact active observation; socket bind explicitly sets and re-verifies `root:labpilot-control 0660` before readiness and fails startup if the final metadata differs.

Run:

```bash
uv run pytest tests/unit/execution/test_runner_policy.py tests/unit/execution/test_runtime_probe.py tests/unit/execution/test_systemd_supervisor.py tests/unit/execution/test_runner_daemon.py tests/architecture/test_execution_privilege_boundary.py -v
```

Commit message:

```text
feat: add candidate b production runner
```

- [ ] **Step 4: Install the real root-owned host Runner boundary.**

Files:

```text
Create: deploy/systemd/labpilot-runner.service
Create: scripts/install_labpilot_runner_host.sh
Create: tests/unit/execution/test_runner_deployment.py
```

Static deployment tests assert all exact directives below and reject regressions:

```text
User=root
Group=labpilot-control
UMask=0077
ExecStart uses /opt/labpilot-runner/app/.venv/bin/python -E -s -m labpilot.execution.runner_daemon
NoNewPrivileges=yes
RestrictAddressFamilies=AF_UNIX
RuntimeDirectory=labpilot-runner
RuntimeDirectoryMode=0750
```

They also reject a service that executes from a repository/controller-writable path or relaxes to `UMask=0007`. Installer tests exercise non-root refusal, argument validation, and effective-systemd-property verification logic without mutating host systemd. Native Task 5 performs real root install/start checks against the effective loaded service.

Run:

```bash
uv run pytest tests/unit/execution/test_runner_deployment.py -v
bash -n scripts/install_labpilot_runner_host.sh
```

Commit message:

```text
feat: define production runner deployment
```

- [ ] **Step 5: Implement `AttemptExecutionService`.**

Files:

```text
Create: src/labpilot/execution/execution_service.py
Create: tests/unit/execution/test_execution_service.py
Create: tests/integration/test_attempt_execution_service.py
```

Public application primitives:

```python
async def dispatch_attempt(attempt_id: UUID) -> None
async def reconcile_attempt(
    attempt_id: UUID,
    *,
    reason: ReconciliationReason,
    coordinator_start_id: UUID | None = None,
) -> None
async def request_stop(attempt_id: UUID) -> None
```

`ReconciliationReason` is `PERIODIC | STARTUP`; STARTUP requires `coordinator_start_id` and emits `attempt.reconciled_after_restart`.

`request_stop()` observes first and issues STOP only on exact `ACTIVE_MATCH`. There is no public arbitrary force-kill API in Slice 2; fixed systemd `TimeoutStopSec`/control-group semantics provide bounded shutdown after durable Batch cancel intent.

Red tests cover: exactly one CAS winner dispatch; observed environment persisted before dispatch; pre-existing LAUNCH_PENDING never LAUNCHes; CREATED does not set RUNNING; ack loss observes without resend; short terminal job; distinct sticky IDENTITY_CONFLICT; transient UNKNOWN grace; STOP ACK does not terminalize; natural terminal truth is preserved during cancel race; required audit events occur in the same UoW; Outbox never delivers LAUNCH.

Run:

```bash
uv run pytest tests/unit/execution/test_execution_service.py -v
uv run pytest tests/integration/test_attempt_execution_service.py -v -m integration
```

Commit message:

```text
feat: reconcile durable attempts with runner
```

- [ ] **Step 6: Task 3 gate.**

Run:

```bash
uv run pytest tests/unit/execution tests/unit/observability tests/architecture -v
uv run pytest tests/integration/test_attempt_physical_schema.py tests/integration/test_attempt_execution_service.py -v -m integration
uv run ruff check src tests scripts
uv run mypy src scripts
bash -n scripts/install_labpilot_runner_host.sh
git diff --check
```

Stop for G2 review before Task 4. The gate fails if any test reconstructs first-dispatch authority from durable state, aliases IDENTITY_CONFLICT to UNKNOWN, invokes Runner LAUNCH from Outbox processing, or drops the service hardening in §5.6.

---

# 6. Task 4 — S2.3 Coordinator Reconciliation + S2.4 API Composition

**Risk:** Workflow / race critical.

**Deliverable:** Thin durable user commands, deterministic single-Coordinator recovery/scheduling, fixed concurrency ≤4 including UNKNOWN and IDENTITY_CONFLICT, startup reconciliation barrier with durable restart audit, and production composition where API has no Runner socket/workspace authority.

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
next_attempt_number <= 1 + max_retries
if next_attempt_number > 1:
    latest Attempt must be terminal
    latest Attempt outcome must be explicitly retryable infrastructure failure
```

Automatic success is only `PROCESS_EXITED` with `exit_code == 0`.

The V1 retryable infrastructure allowlist is finite:

```text
PREPARATION_TEMPORARY_IO
RUNNER_UNAVAILABLE_BEFORE_SEND
PHYSICAL_PREEMPTION_PROVEN
```

Do not automatically retry command non-zero exit, OOM/resource exhaustion, user cancellation, unclassified external termination, `PROCESS_TERMINAL_UNCLASSIFIED`, `UNKNOWN`, or `IDENTITY_CONFLICT`.

A retry always creates a new Attempt and therefore a new X. Same-Attempt relaunch is forbidden.

First Attempts are admitted before retries so one repeatedly failing Trial cannot consume its retry budget ahead of untouched Trials.

## 6.3 Startup barrier and scan order

At every Coordinator process start, generate one in-memory `coordinator_start_id: UUID`. Before any new physical dispatch:

```text
scan all nonterminal Attempts with status:
    LAUNCH_PENDING
    RUNNING
    UNKNOWN
    IDENTITY_CONFLICT

for each:
    apply parent-cancel control only when exact ACTIVE_MATCH permits it
    call reconcile_attempt(..., reason=STARTUP, coordinator_start_id=...)
    persist attempt.reconciled_after_restart event

only after every such Attempt receives one startup pass may new dispatch occur
```

An existing UNKNOWN/IDENTITY_CONFLICT need not resolve for the barrier to complete; both keep their slot. IDENTITY_CONFLICT remains sticky.

Every normal scan uses fixed priority:

```text
1. control existing physical/prelaunch work
2. reconcile LAUNCH_PENDING/RUNNING/UNKNOWN/IDENTITY_CONFLICT
3. resume PREPARING and create/prepare eligible Attempts
4. admit LAUNCH_READY in durable FIFO order
```

Physical active count is exactly:

```text
terminal_at IS NULL
AND status IN ('launch_pending', 'running', 'unknown', 'identity_conflict')
```

Maximum is exactly 4.

FIFO is `LAUNCH_READY ORDER BY created_at, attempt_id`. If the oldest candidate remains unchanged because Runner/DB/observation is transiently unavailable, stop admission for that scan; do not skip it and launch younger work.

## 6.4 Cancellation

`POST /batches/{id}/cancel` only commits `cancel_requested_at`; it performs no socket/systemd call.

Coordinator behavior:

```text
PREPARING/LAUNCH_READY + parent cancel -> CANCELLED_PRE_LAUNCH + terminal_at, no Runner
LAUNCH_PENDING/RUNNING/UNKNOWN + parent cancel -> observe; STOP only if fresh ACTIVE_MATCH
IDENTITY_CONFLICT + parent cancel -> preserve cancel intent; no STOP/kill/retry
TERMINAL_MATCH during cancel race -> persist observed natural terminal truth
UNKNOWN -> preserve cancel intent, no destructive action, no fabricated terminal
```

## 6.5 UNKNOWN and IDENTITY_CONFLICT projection

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
terminal_at = NULL
```

A later exact ACTIVE_MATCH or TERMINAL_MATCH may resolve an ordinary UNKNOWN episode.

`IDENTITY_CONFLICT` is different:

```text
status = IDENTITY_CONFLICT immediately
attention_required_at = now
execution_issue_code = IDENTITY_CONFLICT
terminal_at = NULL
```

Generic UNKNOWN observations never replace or downgrade this status. Slice 2 provides no automatic or public operator transition out of `IDENTITY_CONFLICT`.

Derived Trial/Batch reads may map both UNKNOWN and IDENTITY_CONFLICT to a higher-level `needs_attention` / `failed_to_coordinate` presentation, but Attempt API must expose the exact distinct durable status.

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
Attempt workspace mounted at the SAME canonical absolute path as host policy
Runner AF_UNIX socket
host-compatible labpilot-controller UID/GID
labpilot-train supplementary GID for workspace preparation; composition resolves that fixed group to numeric execution_gid and passes it explicitly to AttemptPreparationService
```

Do not create a container-path ↔ host-path translation table.

The host Runner itself is **not** containerized and is managed only by the root-owned service contract in §5.6.

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

Red tests: create all Trials/zero Attempts; idempotent replay; stale new key returns conflict; row_version only user commands; cancel-before-start prevents later start; distinct Attempt identity_conflict is preserved while derived Batch presentation becomes failed_to_coordinate.

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
async def run_startup_barrier(*, coordinator_start_id: UUID) -> None
async def run_scan() -> None
```

Red tests mechanically prove: startup barrier before dispatch; one `attempt.reconciled_after_restart` audit event per startup pass; priority control > reconcile > prepare > admit; capacity ≤4 including UNKNOWN/IDENTITY_CONFLICT; strict FIFO no bypass; restart reads PostgreSQL not RAM; retries create new Attempt/X; first Attempts before retries.

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

Attempt GET returns identifiers/state/timestamps/terminal/UNKNOWN/conflict fields plus bounded observed environment summary sufficient for UI/trace; it does not return full `FrozenLaunchSpec`, policy environment values, secrets, raw journal, or raw systemd properties.

Red tests prove API handlers call Batch/query services only and cannot import/call `runner_client`, `runner_daemon`, `systemd_supervisor`, or physical methods of `AttemptExecutionService`.

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
LABPILOT_ATTEMPT_WORKSPACE_ROOT          required outside tests, canonical absolute, no guessed default
LABPILOT_RUNNER_SOCKET_PATH              required for Coordinator; production value /run/labpilot-runner/control.sock
LABPILOT_RUNNER_REQUEST_TIMEOUT_SECONDS  positive bounded value, default 10
LABPILOT_UNKNOWN_ATTENTION_GRACE_SECONDS positive bounded value, default 60
```

Compose mounts `${LABPILOT_ATTEMPT_WORKSPACE_ROOT}` into Coordinator at the same absolute path and mounts `/run/labpilot-runner/control.sock` only into Coordinator. API receives neither.

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

Stop for G3 review before native Task 5. The gate fails if API can reach Runner authority, Coordinator parses raw systemd/journal evidence directly, UNKNOWN/IDENTITY_CONFLICT releases a slot, IDENTITY_CONFLICT aliases to UNKNOWN, or startup dispatch occurs before audited barrier completion.

---

# 7. Task 5 — S2.5 Real Recovery / Fault-Injection Acceptance

**Risk:** Release critical.

**Deliverable:** Real native-Linux evidence that production Tasks 2–4 survive process/database/control-plane failures and preserve exact source, environment observation, exact X/H, no duplicate launch, cancellation, UNKNOWN/conflict, capacity, timeout, and reboot invariants.

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

Test fault decorators may delay/fail communication or delegate and drop a response, but must never fabricate `ACTIVE_MATCH`, `TERMINAL_MATCH`, wrong-H evidence, journal records, Attempt state, environment observation, or terminal state. Privileged setup/teardown actions are external test-admin actions and are not exposed through production APIs.

The tiny training fixture accepts deterministic argv for seed/value/dataset/output/duration/exit behavior, writes bounded test-oracle records under `runtime/output`, and may spawn a child for control-group cancellation testing. Those records are acceptance oracles only; production reconciliation never trusts them as physical authority.

Native acceptance must exercise a real prepared Attempt as `labpilot-train` and prove it can traverse the workspace and source tree, read source, execute an approved executable source file, and create/write inside `runtime/output`, while it cannot modify frozen source.

Each native scenario installs/starts the exact committed root-owned Runner using §5.6 or verifies the already-installed service matches the tested commit/policy identity **and the effective loaded hardening contract** before running. The preflight records and checks:

```text
ExecStart Python flags include -E -s
UMask=0077
NoNewPrivileges=yes
RestrictAddressFamilies=AF_UNIX
runtime directory root:labpilot-control 0750
control.sock root:labpilot-control 0660
INFO ready with expected policy identity
```

A repository unit file that is correct while systemd has a stale/different loaded unit is an acceptance failure.

## 7.2 Required native scenarios

```text
F01 happy vertical approved Version -> real terminal training; observed environment persisted
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
F15 real same-X/different-H systemd fixture: durable IDENTITY_CONFLICT, never UNKNOWN, no destructive action
F16 one UNKNOWN plus queued work: UNKNOWN consumes one of four physical slots
F17 mixed restart barrier: existing RUNNING/LAUNCH_PENDING/UNKNOWN/IDENTITY_CONFLICT reconciled/audited before new dispatch
F18 real host reboot: no same-X resurrection; old X/H ends terminal or ADR-bounded UNKNOWN
F19 kill API while real training runs; training remains; restart API; same Attempt remains queryable; zero physical relaunch
F20 stop root Runner before first socket connect after LAUNCH_PENDING commit: definite pre-send non-acceptance -> LAUNCH_NOT_ACCEPTED; no physical unit created
F21 real RuntimeMaxSec timeout: long fixture exceeds approved timeout; systemd terminal evidence records timeout; no duplicate launch
F22 crash Coordinator after real OBSERVE result is obtained but before reconciliation UoW commit; restart Coordinator; repeat observation and converge original Attempt; restart audit remains coherent
```

Roadmap acceptance mapping is explicit:

```text
kill/restart API                         F19
kill/restart Coordinator                 F07
failed launch / definite non-acceptance  F20
timeout                                  F21
process exits while Coordinator down     F08
restart during reconciliation            F22
unknown physical identity/evidence       F14 + F15
physical process count = 1 recovery      F06 + F07 + F19 + F22
```

Every scenario uses a disposable DB/schema/workspace/evidence root and a stable installation identity for the entire scenario. Assertions poll bounded DB/physical predicates; arbitrary `sleep(N)` is not an acceptance oracle.

## 7.3 Critical failure classes

Any one is automatic Slice 2 failure:

```text
C1  same X starts more than one physical workload
C2  UNKNOWN or IDENTITY_CONFLICT releases capacity
C3  UNKNOWN or IDENTITY_CONFLICT causes automatic retry
C4  insufficient evidence fabricates TERMINAL
C5  IDENTITY_CONFLICT is persisted/exposed as UNKNOWN
C6  IDENTITY_CONFLICT causes launch/stop/kill
C7  cancel overwrites proven natural terminal result
C8  new dispatch occurs before startup barrier completes
C9  more than four physical-intent Attempts are admitted
C10 mutable current worktree executes instead of approved source
C11 accepted/possibly-accepted same Attempt is relaunched
C12 installation identity changes within one recovery scenario
C13 API gains Runner/systemd physical-control authority
C14 Attempt lacks bounded observed environment for its approved EnvironmentExpectation
C15 restart reconciliation lacks its immutable audit event
C16 production Runner imports/executes from controller-writable checkout
C17 effective production Runner service drops Python -E/-s, NoNewPrivileges, AF_UNIX restriction, or UMask=0077
```

## 7.4 Evidence package

Each scenario stores bounded evidence only:

```text
scenario.json
db-before.json
db-after.json
runner-info.json
runner-service-effective.json
observed-environment.json
physical-observations.jsonl
runtime-events.jsonl
systemd-exact-unit.json
journal-exact-unit.jsonl
workload-test-oracle.json
verdict.json
```

Evidence includes R0/S1/S2.0/implementation commit identities, installation-identity digest, Runner policy identity/version/protocol, effective service hardening facts, boot ID(s), X, H, workspace path, SourceManifest hash, and selected bounded RuntimeEvents. It excludes secrets, full process environment, unbounded journal, and full DB dumps.

For scenarios intentionally ending UNKNOWN or IDENTITY_CONFLICT, seal/hash the verdict before external administrator teardown. Teardown must not write fake `terminal_at`, clear attention state, rewrite conflict as UNKNOWN, or mark domain success.

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

- [ ] **Step 2: Run non-reboot native scenarios.**

On the dedicated Candidate B VM, install/verify the exact committed Runner and effective service hardening in §5.6, then run:

```bash
uv run python scripts/run_slice2_native_acceptance.py --scenarios F01-F17,F19-F22
```

Expected: every scenario verdict passes according to its explicit terminal/UNKNOWN/conflict oracle; no critical failure class appears.

- [ ] **Step 3: Human/admin reboot gate for F18.**

Prepare:

```bash
uv run python scripts/run_slice2_native_acceptance.py --scenario F18 --phase prepare
```

After the script reports exact evidence path and X/H, an authorized human/admin reboots the dedicated VM outside LabPilot.

Verify after reboot:

```bash
uv run python scripts/run_slice2_native_acceptance.py --scenario F18 --phase verify
```

The verifier must re-check the effective Runner service hardening after reboot and must never call LAUNCH for the old Attempt.

- [ ] **Step 4: Seal production acceptance evidence.**

The evidence document records exact host/runtime versions, tested commit SHA, Runner installed commit/policy identity, effective loaded service hardening, F01–F22 verdicts, Roadmap mapping, critical-failure scan, archive checksum, and any ADR-bounded UNKNOWN. Do not claim full Slice 2 acceptance if any scenario lacks required evidence.

Commit message:

```text
docs: record slice two production acceptance
```

- [ ] **Step 5: Final repository release gate.**

Run in a supported Python 3.12 environment with PostgreSQL drivers installed:

```bash
./scripts/check_ci.sh
uv run pytest tests/acceptance/slice2 -v -k "not native"
uv run python -m compileall -q src tests scripts
bash -n scripts/install_labpilot_runner_host.sh
git diff --check
```

Create a fresh PostgreSQL database and verify migrations from zero through `0010_attempt_physical_execution.py`, then rerun integration tests against that fresh DB.

Perform one independent deep review focused on authority boundaries, replay/ack-loss safety, distinct IDENTITY_CONFLICT semantics, observed environment, audit history, Runner deployment/service hardening, UNKNOWN semantics, and API privilege isolation.

Only after this gate passes update `DOCUMENT-STATUS.md` once to mark Slice 2 accepted.

---

# 8. Review / stop gates

Implementation stops for human review at exactly:

```text
G0 r4.1 targeted remediation approved by final R4-D1-only finding-specific review
G1 Task 2 accepted: preparation contract/exact source/identities proven, zero physical launch
G2 Task 3 accepted: Candidate B production deployment + launch/observe/stop + environment/audit/ack-loss proven
G3 Task 4 accepted: Coordinator/API composition, startup barrier, capacity, cancellation, privilege split proven
G4 Task 5 native F01–F22 + final repository gate accepted
```

Stop immediately before editing beyond-plan scope if any arise:

- production needs a fifth Runner operation or arbitrary raw systemd field;
- execution package appears to require direct imports from experiments/context rather than approved ports/composition adapters;
- exact source cannot be materialized without mutable worktree fallback/network fetch;
- a real concurrency race requires claims/leases/fencing;
- same-Attempt liveness appears to require reconstructing first-dispatch authority after crash;
- UNKNOWN recovery requires destructive control without fresh ACTIVE_MATCH;
- IDENTITY_CONFLICT appears to require aliasing to UNKNOWN or automatic conflict clearing;
- production Attempt path requires PoC `/var/lib/labpilot-s2-poc/*` assumptions;
- environment observation appears to require arbitrary caller-supplied probe code or a generic environment inventory platform;
- production Runner appears to require dropping `-E -s`, `NoNewPrivileges=yes`, `RestrictAddressFamilies=AF_UNIX`, or `UMask=0077`;
- API needs Runner socket/workspace/systemd access;
- native evidence contradicts the selected S2.0 ADR.

When a stop condition is hit, identify exact file, commit, invariant, and observed evidence before proposing an amendment. Do not silently reinterpret the ADR.

---

# 9. r4.1 self-review checklist

Before G0 implementation authorization, verify:

- `Planning baseline` is exactly R0 authority root `032dd87d3366dc6edd66fb75451d44047e49f7dd`.
- accepted Slice 1 baseline is explicitly recorded as `ff7909503c3b755bffb564423de3a56ce5bb9b4a` and is distinct from S2.0/handoff lineage.
- Task 1 is historical/complete and no step reruns P0–P13 merely for familiarity.
- Tasks 2–5 preserve the five-task outer shape.
- R4-D1 is the only semantic change after r4: Runner service restores Python `-E -s`, `NoNewPrivileges=yes`, `RestrictAddressFamilies=AF_UNIX`, and `UMask=0077`; direct consistency edits only add verification/evidence for those properties.
- `Group=labpilot-control` remains allowed only for the runtime-directory/socket boundary; restrictive `UMask=0077` prevents accidental group exposure of other daemon-created files.
- Runner explicitly sets and verifies `control.sock` as `root:labpilot-control 0660` before readiness; socket sharing does not depend on service-wide umask relaxation.
- deployment unit tests and installer/native checks verify the **effective loaded systemd service**, not only the repository unit file.
- `execution` does not import `experiments` or `context`; Coordinator adapters bridge those authorities.
- Legacy Proposal hashes remain immutable; legacy Versions fail closed for execution rather than being rewritten.
- `parameter_matrix × seeds` is bounded to 1–20 Trials.
- Trial identity, X, workspace path, SourceManifest, FrozenExecutionSpec, H, and Runner policy identity have exact namespace/byte contracts and golden-vector tests.
- `ArtifactDeclaration.relative_path` is unambiguously relative to Attempt `runtime/output`.
- final ExecStart composition and NUL/token/byte budgets are frozen before implementation.
- Attempt workspace root has explicit deployment authority and no PoC/default path guess.
- source is exact/read-only; output root is explicit/writable; safe symlink semantics are deterministic.
- Task 2 has no Runner/systemd side effect.
- complete approved EnvironmentExpectation reaches FrozenExecutionSpec; Attempt persists bounded ObservedEnvironment before first dispatch.
- only exact non-secret root-policy environment pairs enter FrozenLaunchSpec/H; observed host inventory is evidence, not H.
- production root Runner has exact service/install/policy/runtime-dir/socket ownership and startup contract without widening the native Candidate B service boundary.
- `LAUNCH_PENDING` commit precedes physical dispatch; first-dispatch authority is in-memory only.
- Outbox is not a Runner LAUNCH delivery path.
- launch intent, physical start, startup reconciliation, conflict, and terminal history have explicit immutable RuntimeEvents; audit-only events use the compatible `enqueue_outbox=False` bridge.
- RUNNING/TERMINAL are observation-driven.
- `PROCESS_TERMINAL_UNCLASSIFIED` prevents invented exit/cancel causality.
- UNKNOWN and IDENTITY_CONFLICT are distinct durable states; both retain capacity and prohibit retry/destructive control.
- Coordinator startup barrier includes and audits LAUNCH_PENDING/RUNNING/UNKNOWN/IDENTITY_CONFLICT before new dispatch.
- retry wording uses `next_attempt_number > 1` and a finite explicit infrastructure allowlist.
- API cannot reach the privileged physical boundary.
- Task 5 explicitly maps API restart, Coordinator restart, failed launch, timeout, Coordinator-down exit, restart-during-reconciliation, and unknown/conflicting physical identity to named native scenarios.
- Task 5 native preflight/evidence includes effective Runner service hardening, including after reboot.
- Task 5 uses real physical facts and bounded evidence, not fabricated observations.
- `DOCUMENT-STATUS.md` changes only at full Slice 2 acceptance.
