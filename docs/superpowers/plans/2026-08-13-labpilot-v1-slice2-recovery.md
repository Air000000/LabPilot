# LabPilot V1 Slice 2 — Trial → Real Training → Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute this plan task-by-task. Do **not** dispatch implementation subagents for this repository. Every production change uses TDD and stops at the review/authorization gates below.

**Goal:** Build one controlled V1 path from an approved `ExperimentVersion` to exact source materialization, real systemd-supervised training, durable restart reconciliation, bounded cancellation, environment observation, and no duplicate physical launch.

**Architecture:** PostgreSQL is the durable execution ledger; the selected Candidate B root Runner is a narrow privileged adapter; systemd plus persistent journald are the physical supervision/evidence authority; Coordinator is the only component that compares durable expectation with physical observation and advances domain state. Task 2 creates only logical/prepared execution state, Task 3 introduces the first production physical side effect, Task 4 composes scheduling/reconciliation/API, and Task 5 proves the combined recovery contract on a real native Linux host.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x async, PostgreSQL 16, Alembic, pytest, Ruff, mypy, AF_UNIX + `SO_PEERCRED`, native Linux systemd/systemctl/systemd-run/journalctl, Docker Compose for API/Coordinator composition.

**Spec:** `docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-adr.md` plus `docs/superpowers/specs/2026-08-24-labpilot-v1-slice2-terminal-receipt-adr-amendment.md`

**Planning baseline — R0 authority root:** `032dd87d3366dc6edd66fb75451d44047e49f7dd`

**Accepted Slice 1 baseline:** `ff7909503c3b755bffb564423de3a56ce5bb9b4a`

**Accepted S2.0 implementation/evidence baseline:** `4109afa9f871ae45cf32c301040bcff6f109939d`

**Post-S2.0 handoff/amendment base:** `536ad6e38c66c667014beb1ac214271452dfd42d`

**Plan-branch CI portability history:** `5fe5f5aa5ae349504ec360665762caccb6c338d2`

**Selected supervision ADR:** `docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-adr.md`

**Accepted supervision amendment:** `docs/superpowers/specs/2026-08-24-labpilot-v1-slice2-terminal-receipt-adr-amendment.md`

**Candidate B v2 plan-delta baseline:** `b6eada82a02d1d684fcb3a6e40b83c154352d8a5`

**Candidate B v2 implementation-plan external acceptance baseline:** `8a62fb6df5a4cf67479b27776a3cb8b49baf575a`

**Candidate B v2 plan-delta status:** **ACCEPTED / CURRENT IMPLEMENTATION PLAN DELTA**

**Native S2.0 evidence:** `docs/superpowers/evidence/2026-08-13-labpilot-v1-slice2-runner-supervision-poc.md`

**Status:** r4.1 is **APPROVED AT G0**. Task 1 remains **COMPLETE / SELECT_B**. Task 2 is **ACCEPTED AT G1**. Task 3 is **ACCEPTED AT G2**. Task 4 is **ACCEPTED AT G3**. The historical Task 5 authorization and findings remain recorded below. Candidate B v2 architecture is Accepted and the Candidate B v2 implementation-plan delta is **ACCEPTED / CURRENT IMPLEMENTATION PLAN DELTA** at external acceptance baseline `8a62fb6df5a4cf67479b27776a3cb8b49baf575a`. Plan acceptance does not authorize Candidate B v2 implementation: Candidate B v2 implementation, its native campaign, and F18 remain **NOT AUTHORIZED** until the required later gates. G4 has not been reached. This governance state does not imply Task 5 or Slice 2 acceptance. `DOCUMENT-STATUS.md` remains unchanged and may be updated only after full Slice 2 acceptance.

**Historical governance note:** Candidate B was admitted and implemented before a separate focused pre-implementation amendment was committed. This plan records the post-S2.0 production contract and does not backdate or rewrite that history.

**G1 finding-specific review:** **APPROVE** with 0 Critical and 0 Important findings. G1-1 (`FrozenExecutionSpec` exact `{output_dir}` argument provenance) and G1-2 (supported-Linux Attempt workspace execution-group ownership) are **CLOSED**. CI evidence is run **#60** at exact HEAD `e5c13fbbc846f0a1c173817a7231e39e071e094a`, completed successfully. This was a finding-specific review of `f908cc0..e5c13fb`, not a new broad Task 2 review.

---

## Global Constraints

- Normative precedence is `V1 Scope v0.3 > Architecture 10 v0.3 > Master Roadmap v0.3 > original Accepted Candidate B Runner Supervision ADR + Accepted Candidate B v2 / TerminalReceiptV1 Amendment > the currently approved/reviewed portions of this implementation plan`. The Accepted amendment supersedes only the affected original-ADR clauses it names; Candidate B itself is not replaced.
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

The final Task 3 G2 finding-specific review is **APPROVE** with 0 Critical and 0 Important findings. The final reviewed Task 3 HEAD is `d26c545515c1a2206fdba1d007faad09e95760ad`; GitHub Actions run `32010897375` completed successfully for that exact HEAD.

```text
G2-1  CLOSED — restore exact X -> X.service supervisor identity mapping
G2-2  CLOSED — canonicalize INFO inspection without rewriting accepted G1 identity
G2-3  CLOSED — implement the frozen ordinary-UNKNOWN grace state machine
G2-4  CLOSED — run installer INFO readiness as labpilot-controller:labpilot-control
G2-5  CLOSED — preserve historical journal _BOOT_ID provenance
G2-6  CLOSED — explicitly activate the newly installed Runner tree/policy on update
G2-7  CLOSED — reject a policy-unadmitted INFO python_profile before probe execution with bounded policy evidence and preserve continued service on the same RunnerDaemon instance
```

The final G2-7 review proves bounded policy rejection and continued service on the same `RunnerDaemon` instance. It is not native systemd process-level acceptance. Native root/systemd/persistent-journald recovery evidence remains Task 5 scope. Task 4 is **AUTHORIZED** for a new implementation window; Task 5 remains **NOT AUTHORIZED**. `DOCUMENT-STATUS.md` remains unchanged until full Slice 2 acceptance.

## 0.2 Task 4 G3 finding status

The initial Task 4 G3 review returned **CHANGES REQUIRED** with 0 Critical and 5 Important findings: G3-1 through G3-5 were OPEN. That historical result remains part of the audit trail.

The final Task 4 G3 finding-specific review is **APPROVE** with 0 Critical and 0 Important findings. The final reviewed Task 4 HEAD is `6da2c6c2591c17c948508b0ad8b72b5c04785e52`; GitHub Actions run `32028039084` completed successfully for that exact HEAD.

```text
G3-1  CLOSED — real cancel API and durable command state are consistently cancel_requested; replay does not increment row_version again
G3-2  CLOSED — fixed controller UID/GID and train GID composition inputs are positive, required for Coordinator composition, and use the same Compose identity variables
G3-3  CLOSED — Coordinator mounts the Runner runtime directory rather than one socket inode; host path auto-creation is disabled; RuntimeDirectoryPreserve is restart; stale socket replacement is bounded by exact metadata, liveness, and inode revalidation
G3-4  CLOSED — derived Trial/Batch presentation converges for cancelled untouched work and mixed succeeded/failed terminal work without persisted Batch.status
G3-5  CLOSED — missing ExperimentVersion is translated to existing public not-found semantics while unrelated ExecutionInputError values are not converted to 404
```

Task 4 is **ACCEPTED AT G3**. Task 5 is **AUTHORIZED** only for work in a new Task 5 window after the Task 4 governance closure commit; it is not COMPLETE or ACCEPTED. `DOCUMENT-STATUS.md` remains unchanged until full Slice 2 acceptance.

---

# 1. Formal Slice 2 task status

```text
Task 1 — S2.0 Runner Supervision PoC + ADR                        COMPLETE / SELECT_B
Task 2 — S2.1 Attempt Preparation + Exact Source Materialization  ACCEPTED AT G1
Task 3 — S2.2 Durable Launch / Observe / Stop                     ACCEPTED AT G2
Task 4 — S2.3 Coordinator Reconciliation + S2.4 API Composition  ACCEPTED AT G3
Task 5 — S2.5 Real Recovery / Fault-Injection Acceptance         AUTHORIZED
```

The Task 5 row preserves the existing outer governance record. It does not
authorize the proposed Candidate B v2 delta: implementation requires external
plan acceptance and a separate explicit implementation authorization.

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
- `.env.example` — Task 4 documents the required fixed host numeric identity inputs; no guessed defaults.

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

### 5.6.1 Task 5 native deployment finding amendment

Task 5 native acceptance at `TESTED_SHA`
`5a6271a7a077d11f7200611e7d84ad2e39874106` exposed a production deployment
defect after the historical G2/G3 acceptance: `uv sync --locked --no-dev`
created an editable LabPilot installation whose `.pth` retained the temporary
`/opt/labpilot-runner/.stage.*` source path after that staging tree was moved to
`/opt/labpilot-runner/app`. The effective service therefore failed with
`ModuleNotFoundError`, and Python startup also created installation-tree
bytecode owned by `root:labpilot-control`.

The historical G2/G3 record above remains unchanged. For all deployment and
Task 5 acceptance work after this finding, its contract is narrowly amended:

1. dependency installation is `uv sync --locked --no-dev --no-editable`;
2. after the staging tree is moved and normalized, but before service
   activation, the final interpreter runs with `-E -s -B` and must import
   `labpilot` from the final installed venv site-packages tree, never a deleted
   `.stage.*` path;
3. the effective Runner `ExecStart` is exactly
   `/opt/labpilot-runner/app/.venv/bin/python -E -s -B -m labpilot.execution.runner_daemon --policy /etc/labpilot/runner-policy.json`;
4. the controller-identity `INFO` readiness interpreter also uses `-E -s -B`;
5. only after activation, effective-service verification, active/runtime/socket
   verification, and controller `INFO` readiness, the installer re-verifies
   the complete application tree without following symlinks: every node is
   `root:root`, and every non-symlink has no group/world write bits. Symlink
   ownership is checked, but Linux symlink mode `0777` is not treated as
   writable-file authority. This final verifier fails closed and does not
   repair detected mutation.

Task 5 native preflight and evidence must require the amended `-E -s -B`
contract. Loss of `-E`, `-s`, `-B`, `NoNewPrivileges=yes`, the `AF_UNIX`
restriction, or `UMask=0077` is a C17 critical acceptance failure. This
amendment does not accept Task 5, G4, or Slice 2, and `DOCUMENT-STATUS.md`
remains unchanged.

### 5.6.2 Task 5 native controller-traversal follow-up

Task 5 deployment remediation at `TESTED_SHA`
`95ed4c35c0530f9a86a12a48412b46a54abe050e` exposed a second narrow
deployment defect: the final `/opt/labpilot-runner/app` root retained the
staging directory's `mktemp -d` mode `0700`. The root Runner could start, but
`labpilot-controller:labpilot-control` could not traverse the final application
root to execute the readiness interpreter.

The amended deployment contract is:

1. `/opt/labpilot-runner/app` is exactly `root:root 0711`. Non-root identities
   receive traversal only; listing permission is intentionally unnecessary;
2. the rest of the installed tree remains recursively `root:root`, and every
   non-symlink retains no group/world write bits;
3. after the staging tree is moved, ownership and no-write normalization are
   complete, the installer explicitly establishes app-root mode `0711`;
4. before systemd activation, the installer runs a controller-identity
   final-path import smoke test as `labpilot-controller:labpilot-control` with
   `/opt/labpilot-runner/app/.venv/bin/python -E -s -B`;
5. that smoke test must execute the final interpreter, import `labpilot` and
   the Runner client/protocol modules required by readiness, resolve LabPilot
   from final venv site-packages, and reject any `.stage.*` resolution;
6. post-readiness installed-tree verification additionally requires the app
   root itself to remain exactly `root:root 0711`, while preserving all prior
   descendant ownership/write and symlink rules;
7. Task 5 native preflight independently re-verifies app-root
   `root:root 0711` and controller final-path Python/import capability.

This follow-up changes no Task 5, G4, or Slice 2 acceptance state and leaves
`DOCUMENT-STATUS.md` unchanged.

### 5.6.3 Task 5 production-image Git dependency follow-up

Task 5 native acceptance at `TESTED_SHA`
`fe81c7839651aa773274bdef397d1c24adc091b8` exposed a production container
dependency-closure defect. The committed API composition in
`src/labpilot/main.py` and Coordinator composition in
`src/labpilot/coordinator/worker.py` both construct `GitAdapter` with the
deterministic absolute executable authority `/usr/bin/git`. `GitAdapter`
requires that path to be absolute, a file, and executable, but the production
Dockerfile did not provide it. The resulting API and Coordinator containers
therefore failed before readiness with `RepositoryGitExecutableInvalid`.

The production-image contract is narrowly amended:

1. the production image contains executable `/usr/bin/git`;
2. the Dockerfile installs only the Debian `git` package needed for this
   contract, using `apt-get update`, `apt-get install -y
   --no-install-recommends`, and `/var/lib/apt/lists/*` cleanup in one layer;
3. `FROM python:3.12-slim`, the existing uv version and sync semantics, and the
   existing `CMD` remain unchanged;
4. no source-level Git executable authority changes: both composition paths
   continue to use exact `/usr/bin/git`;
5. no PATH lookup, configurable executable, Compose host bind, host Git
   injection, or runtime package installation workaround is permitted;
6. ordinary CI gains a real production-image gate that builds the committed
   Dockerfile, executes `/usr/bin/git --version`, and constructs the real
   `GitAdapter` against `/usr/bin/git` inside the built container;
7. native N4 independently repeats the executable and `GitAdapter` proofs on
   images built from the exact tested commit before runtime acceptance;
8. Task 5 build-only proxy authority remains test-admin state and must not be
   persisted in production image configuration or history;
9. because this is a repository change, the production Runner is reinstalled
   from the new exact `TESTED_SHA` before native acceptance continues;
10. historical failing evidence at `fe81c7839651aa773274bdef397d1c24adc091b8`
    remains immutable and retains its failing verdict;
11. Task 5 remains **AUTHORIZED / IN PROGRESS** and G4 has not occurred; and
12. `DOCUMENT-STATUS.md` remains unchanged.

### 5.6.4 Task 5 Runner readiness follow-up

Task 5 native deployment at `TESTED_SHA`
`7f2d1221dc626654d6a9dabc08d8d41ba507824c` exposed a bounded startup race:
`systemctl restart` returned before the restarted Runner accepted controller
connections. A preserved socket pathname and its valid metadata do not prove
that the new process is listening, so the installer's single immediate `INFO`
request failed pre-send even though the service subsequently became ready.

The deployment contract is narrowly amended:

1. after restart and effective-service verification, the installer polls a
   controller-identity `INFO` request for at most 50 attempts, sleeping 200 ms
   between retryable attempts and using a 1.0 second Runner client timeout;
2. only `RunnerPreSendError` is retryable startup unavailability and is mapped
   by the embedded controller probe to the dedicated exit status `75`;
3. exit status `75` is retried only while the service remains active and
   attempts remain. Service inactivity or readiness exhaustion fails the
   installation;
4. protocol, authentication, response-integrity, control-unknown, and every
   other probe failure remain immediate hard failures and are never retried;
5. only after readiness succeeds does the installer perform its mandatory
   service/runtime/socket metadata verification and final installed-tree
   verification;
6. Task 5 remains **AUTHORIZED / IN PROGRESS**, G4 has not occurred, and
   `DOCUMENT-STATUS.md` remains unchanged.

### 5.6.5 Task 5 native-worker metadata follow-up

Task 5 fresh N7 at `TESTED_SHA`
`5e5eaf8991f73e2da4235d38d117a855ce222ecf` stopped before workspace
permission proof because every native-worker operation runs in a fresh Python
interpreter, while the test harness did not explicitly load the complete
production SQLAlchemy model registry at that process boundary.

The test-harness contract is narrowly clarified:

1. every fresh native-worker interpreter explicitly registers the existing
   complete production SQLAlchemy metadata before product database operations;
2. the previous N7 `STOPPED` result remains historical evidence and is not
   promoted to `PASS`;
3. the harness remediation creates a new exact `SCENARIO_TESTED_SHA`;
4. after that SHA is frozen, exact-SHA native installation, production images,
   and N1-N7 preflight are rerun before F01; and
5. no N1-N6 result from the previous SHA is promoted into the final campaign.

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

### 5.9.1 Task 5 systemd journal terminal-retention finding

Task 5 F01 at `TESTED_SHA=d3d3513022b1526ab6763ec2e91dca1e86c6ce5d`
showed that real `Type=exec` transient units can be collected immediately after
completion. The production journal fallback had only been tested with a
synthetic `Finished UNIT - labpilot:H.` record. On the dedicated systemd 259
host, a successful lifecycle instead retained a trusted exact-H start record
followed by a PID-1 `UNIT: Deactivated successfully.` terminal record that did
not repeat the Description/H marker.

The existing parser therefore found the matching start witness but failed to
correlate its later terminal manager record, incorrectly degrading normal
unit-GC recovery to UNKNOWN. This violates the accepted terminal-retention
contract. The bounded remediation remains within the selected systemd plus
persistent-journald evidence stack: correlate trusted records for the exact
UNIT only when their structured `INVOCATION_ID` and historical `_BOOT_ID`
match, preserve wrong-H as IDENTITY_CONFLICT, and consume structured terminal
fields only when present. It introduces no Runner authority, ledger, worker,
retry system, or execution state.

### 5.9.2 Task 5 bounded-manager and signal-retention findings

Follow-up diagnostics on the dedicated systemd 259 host showed that the
bounded journal query must select the trusted manager stream before applying
its tail limit. A `Type=exec` throwaway unit that emitted 600 journal lines
left 99 workload records and only the H-less terminal manager record in the
last 100 records selected by `--unit=UNIT`; its exact-H `Started` witness was
outside that mixed tail. The bounded query `journalctl UNIT=UNIT _PID=1
_SYSTEMD_UNIT=init.scope SYSLOG_IDENTIFIER=systemd --output=json --no-pager
-n 100` instead returned only the three trusted PID-1 lifecycle records,
including the exact-H `Started` witness and terminal success. It intentionally
has no current-boot filter so historical `_BOOT_ID` evidence remains eligible.

The same host also proved that signal semantics must come from structured
records rather than the signal name alone: SIGTERM was treated as a clean
termination for this service type, while direct SIGKILL produced a trusted
same-invocation/same-boot process-exit record with `EXIT_CODE=killed` and
`EXIT_STATUS=9`, followed by `Failed with result 'signal'`. The GC fallback
therefore admits only that observed `signal` failure grammar and preserves the
structured termination signal; it does not infer a signal from result text or
from explicit systemctl stop.

### 5.9.3 Candidate B v2 / TerminalReceiptV1 accepted remediation

**Delta status:** **ACCEPTED / CURRENT IMPLEMENTATION PLAN DELTA**.
External implementation-plan review is **ACCEPTED** at baseline
`8a62fb6df5a4cf67479b27776a3cb8b49baf575a`. The Candidate B v2 architecture
remains Accepted. Candidate B v2 implementation, native execution, and F18
remain **NOT AUTHORIZED**. G4 is not reached and `DOCUMENT-STATUS.md` remains
unchanged.

Sections 5.9.1 and 5.9.2 remain sealed historical findings. The Accepted
TerminalReceiptV1 amendment supersedes their interim parser-only
terminal-cause strategy where the two conflict. In particular:

```text
Deactivated successfully
+ no retained signal/stop record
!= exit_code 0
```

ManagerEvidence remains terminality and H authority. TerminalReceiptV1 adds
retained terminal-cause evidence. A successful receipt query with no matching
receipt and manager-proven terminality yields `TERMINAL_MATCH` with no exit
code or termination signal; it never guesses zero.

#### Current code gap at plan baseline

This delta is bound to
`b6eada82a02d1d684fcb3a6e40b83c154352d8a5`:

- `src/labpilot/execution/systemd_supervisor.py` still infers zero from a
  retained `Deactivated successfully` record when no signal/stop record was
  retained; its loaded-unit path can also expose killed `TERM` directly when
  `Result=timeout`; `_launch_argv()` has no TerminalReceiptV1 `ExecStopPost`.
- `src/labpilot/execution/runner_policy.py` treats
  `launch_profile_version` as policy JSON data, hashes only policy JSON into
  `policy_identity`, and admits workload `LD_LIBRARY_PATH`.
- `src/labpilot/execution/runner_daemon.py` reports
  `RUNNER_VERSION = "candidate-b-v1"` and returns
  `readiness_reason=ready` without validating the immutable reporter,
  interpreter, manifest, or trusted path chain.
- `scripts/install_labpilot_runner_host.sh` replaces
  `/opt/labpilot-runner/app` during upgrade and has no sibling immutable
  `/opt/labpilot-runner/libexec` reporter installation.
- The four focused execution test modules and the existing Task 5 harness do
  not yet freeze the Candidate B v2 producer, identity, observation,
  deployment, readiness, or pre-campaign profile matrix.

No DB migration, Runner protocol operation, Coordinator behavior, retry
policy, wrapper daemon, persistent receipt file, ledger, or generic hook
framework is required by the inspected code.

#### Candidate B v2 file-responsibility map

Create:

```text
deploy/libexec/labpilot-terminal-receipt-v1.py
    standalone stdlib-only four-field producer; no LabPilot import
src/labpilot/execution/terminal_receipt.py
    bounded TerminalReceiptV1 parsing and cause mapping only
tests/unit/execution/test_terminal_receipt_reporter.py
    standalone producer byte/schema/isolation tests
tests/unit/execution/test_terminal_receipt.py
    parser and admitted/unsupported cause-mapping tests
tests/poc/s2_runner/terminal_receipt_v2_probe.py
    bounded systemd-259 mechanism probe for the exact candidate command form
tests/unit/s2_runner_poc/test_terminal_receipt_v2_probe.py
    deterministic probe argv/result/evidence tests
docs/superpowers/evidence/2026-08-24-labpilot-v1-slice2-terminal-receipt-mechanism.md
    exact native mechanism result, host version, commands, and bounded evidence
tests/acceptance/slice2/candidate_b_v2_fixtures.py
    closed test-admin fixtures, isolation classes, cleanup, and fingerprints
tests/acceptance/slice2/fixtures/a18_installer_drift_probe.sh
    fixed mount-namespace invocation of real installer validation logic
```

Modify for production:

```text
src/labpilot/execution/systemd_supervisor.py
    ManagerEvidence/ReceiptEvidence correlation, cardinality, loaded/GC
    equivalence, and fixed Candidate B v2 launch construction
src/labpilot/execution/runner_policy.py
    fixed v2 manifest, artifact-bound policy identity, v2 admission, trusted
    deployment/interpreter validation
src/labpilot/execution/runner_daemon.py
    candidate-b-v2 version and ready-only-after-validation behavior
scripts/install_labpilot_runner_host.sh
    immutable libexec install/reuse/fail-closed verification
```

Modify for focused production verification:

```text
tests/unit/execution/test_systemd_supervisor.py
tests/unit/execution/test_runner_policy.py
tests/unit/execution/test_runner_daemon.py
tests/unit/execution/test_runner_deployment.py
tests/architecture/test_execution_privilege_boundary.py
tests/acceptance/slice2/native_preflight.py
tests/acceptance/slice2/native_campaign.py
tests/acceptance/slice2/scenarios.py
tests/acceptance/slice2/test_native_preflight.py
tests/acceptance/slice2/test_native_slice2.py
scripts/run_slice2_native_acceptance.py
```

Modify only after external production code review for the two deferred harness
repairs:

```text
tests/acceptance/slice2/native_preflight.py
tests/acceptance/slice2/native_campaign.py
tests/acceptance/slice2/native_worker.py
tests/acceptance/slice2/oracles.py
tests/acceptance/slice2/test_native_preflight.py
tests/acceptance/slice2/test_native_slice2.py
```

The repository source for the immutable reporter is exactly
`deploy/libexec/labpilot-terminal-receipt-v1.py`. Its installed ABI path is
exactly
`/opt/labpilot-runner/libexec/labpilot-terminal-receipt-v1.py`. It is kept out
of the replaceable application package and installed from the exact Git
archive by the root installer. The new `terminal_receipt.py` module is
justified because parsing/cause mapping is independently testable and would
otherwise make the already multi-responsibility `systemd_supervisor.py`
larger; it owns no journal query, systemd command, policy, or deployment
mutation.

#### Internal Step 0 — native systemd-259 mechanism proof gate

**Files:**

```text
Create: tests/poc/s2_runner/terminal_receipt_v2_probe.py
Create: tests/unit/s2_runner_poc/test_terminal_receipt_v2_probe.py
Create: docs/superpowers/evidence/2026-08-24-labpilot-v1-slice2-terminal-receipt-mechanism.md
```

**Interfaces:**

```text
@dataclass(frozen=True, slots=True)
class MechanismProbeResult:
    main_uid: int
    main_gid: int
    main_no_new_privs: int
    main_cap_prm: str
    main_cap_eff: str
    main_cap_amb: str
    main_environment: tuple[tuple[str, str], ...]
    reporter_uid: int
    reporter_gid: int
    service_result: str | None
    exit_code: str | None
    exit_status: str | None
    reporter_failure_neutral: bool
    loader_canary_seen: bool

candidate_b_v2_probe_argv(unit: str) -> tuple[str, ...]
run_candidate_b_v2_probe(evidence_dir: Path) -> MechanismProbeResult
```

The only candidate admitted to this gate is `M1`:

```text
main command:
/usr/bin/env -i <sorted KEY=VALUE workload pairs>
  <absolute admitted runtime> <absolute prepared entrypoint> <fixed argv>

unit properties:
User=labpilot-train
Group=labpilot-train
NoNewPrivileges=yes
AmbientCapabilities=
CapabilityBoundingSet=
UnsetEnvironment=LD_AUDIT LD_DEBUG LD_LIBRARY_PATH LD_PRELOAD PYTHONHOME PYTHONPATH

proof post-stop command:
ExecStopPost=-+/usr/bin/python3 -I -B
  /opt/labpilot-runner-mechanism-proof/libexec/terminal-receipt-probe.py

production form admitted only after PASS:
ExecStopPost=-+/usr/bin/python3 -I -B
  /opt/labpilot-runner/libexec/labpilot-terminal-receipt-v1.py
```

The probe path is a fixed root-owned, non-group/world-writable test-admin path;
it never creates or occupies the immutable production v1 ABI path. The only
production substitution after PASS is the fixed final reporter path shown
above. The angle-bracket values in the mechanism notation are inputs already
frozen by `FrozenLaunchSpec`, not implementation placeholders. There is no
shell and no fallback command. Workload environment is introduced inside the non-root
main command by fixed `/usr/bin/env -i`, rather than as unit-wide `--setenv`
properties inherited by the privileged post-stop process. The absolute root
reporter command therefore does not receive workload `LD_LIBRARY_PATH`,
`PYTHONPATH`, or other runtime-profile values. `-I` remains defense in depth
for Python imports; it is not treated as dynamic-loader isolation.

- [ ] **RED:** freeze the exact M1 argv/property sequence and reject missing,
  reordered, caller-selected, shell-based, or unit-wide workload environment
  injection. Run
  `uv run pytest tests/unit/s2_runner_poc/test_terminal_receipt_v2_probe.py -v`.
  Expected RED: `terminal_receipt_v2_probe` and its exact M1 constructor do not
  exist.
- [ ] **Minimal probe:** create one bounded probe that installs only a
  root-owned test artifact beneath the trusted proof root, launches a
  throwaway `Type=exec` unit, and records `/proc/self/status` facts from the
  main process plus trusted journal facts from the root post-stop process.
  Use an admitted workload `LD_LIBRARY_PATH`/`PYTHONPATH` canary and make the
  post-stop probe deliberately return `71` after emitting its one record.
- [ ] **GREEN:** rerun the unit command, then on the dedicated systemd 259 host
  run
  `sudo -- uv run python tests/poc/s2_runner/terminal_receipt_v2_probe.py --evidence-dir /var/lib/labpilot-s2-evidence/terminal-receipt-m1`.
  Pass requires main UID/GID `labpilot-train`, main `NoNewPrivs: 1`, empty main
  effective/permitted/ambient capability sets, root receipt `_UID=0` and
  `_GID=0`, correct systemd injection of `SERVICE_RESULT`, `EXIT_CODE`, and
  `EXIT_STATUS`, no loader/import canary, and identical main `Result` with a
  post-stop exit of `0` and `71`.
- [ ] **Focused regression:**
  `uv run pytest tests/unit/s2_runner_poc -v`.
- [ ] **Commit:** `test: prove terminal receipt systemd mechanism`.

**Stop condition:** any failure of privilege override, failure neutrality,
workload environment preservation, loader isolation, main NNP/capability
preservation, trusted provenance, or systemd variable injection stops Task 5
with `ARCHITECTURE REVIEW REQUIRED`. No production edit follows a failed or
inconclusive mechanism gate.

#### Internal Step 1 — standalone TerminalReceiptV1 producer

**Files:**

```text
Create: deploy/libexec/labpilot-terminal-receipt-v1.py
Create: tests/unit/execution/test_terminal_receipt_reporter.py
Modify: tests/architecture/test_execution_privilege_boundary.py
```

**Interfaces:** the standalone file defines only
`main(environ: Mapping[str, str] | None = None) -> int` and a private
`_optional_token(environ, name) -> str | None`. It imports only `json`, `os`,
`re`, `sys`, and `collections.abc.Mapping` from the standard library.

```python
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,63}")
_SOURCE_NAMES = ("SERVICE_RESULT", "EXIT_CODE", "EXIT_STATUS")

payload = {
    "schema_version": "labpilot-terminal-receipt-v1",
    "service_result": _optional_token(source, "SERVICE_RESULT"),
    "exit_code": _optional_token(source, "EXIT_CODE"),
    "exit_status": _optional_token(source, "EXIT_STATUS"),
}
encoded = json.dumps(
    payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
).encode("ascii") + b"\n"
os.write(1, encoded)
```

Input is validated completely before the single `os.write`. Missing variables
become `null`; present values must be ASCII, match `_TOKEN`, and occupy at most
64 bytes. Invalid input returns nonzero without emitting a partial valid
record. The producer does not interpret combinations and performs no LabPilot
import, workspace access, subprocess, shell, network, DB, or Runner-socket
operation.

- [ ] **RED:** add exact-field, canonical-byte, missing-to-null,
  malformed/oversized fail-closed, deterministic-repeat, import isolation,
  forbidden-import/operation AST, and nonzero-invalid-input tests. Run
  `uv run pytest tests/unit/execution/test_terminal_receipt_reporter.py tests/architecture/test_execution_privilege_boundary.py -v`.
  Expected RED: the fixed reporter asset is absent.
- [ ] **Minimal implementation:** add the exact standalone serializer above;
  do not add a package entry point or dependency.
- [ ] **GREEN:** rerun the RED command and require all producer invocations to
  emit either one canonical newline-terminated JSON record or zero bytes with
  nonzero status.
- [ ] **Focused regression:**
  `uv run ruff check deploy/libexec/labpilot-terminal-receipt-v1.py tests/unit/execution/test_terminal_receipt_reporter.py tests/architecture/test_execution_privilege_boundary.py`.
- [ ] **Commit:** `feat: add immutable terminal receipt producer`.

**Review/stop condition:** any need for a LabPilot import, third-party module,
subprocess, caller-selected argument, additional payload field, or more than
one output record stops for architecture review.

#### Internal Step 2 — Candidate B v2 profile identity

**Files:**

```text
Modify: src/labpilot/execution/runner_policy.py
Modify: tests/unit/execution/test_runner_policy.py
Modify: tests/unit/execution/test_types.py
Modify: tests/unit/execution/test_execution_service.py
```

**Interfaces:**

```text
CANDIDATE_B_V2 = "candidate-b-v2"
TERMINAL_RECEIPT_SCHEMA = "labpilot-terminal-receipt-v1"
TERMINAL_RECEIPT_REPORTER_PATH = Path(
    "/opt/labpilot-runner/libexec/labpilot-terminal-receipt-v1.py"
)
TERMINAL_RECEIPT_BUNDLED_SOURCE_PATH = Path(
    "/opt/labpilot-runner/app/deploy/libexec/labpilot-terminal-receipt-v1.py"
)
TRUSTED_REPORTER_INTERPRETER = Path("/usr/bin/python3")

@dataclass(frozen=True, slots=True)
class CandidateBV2ProfileManifest:
    launch_profile_version: str
    terminal_receipt_schema: str
    reporter_path: str
    reporter_sha256: str

def candidate_b_v2_profile_manifest(
    reporter_sha256: str,
) -> CandidateBV2ProfileManifest

def runner_policy_identity(
    policy_value: Mapping[str, object],
    profile_manifest: CandidateBV2ProfileManifest,
) -> str
```

`candidate_b_v2_profile_manifest()` accepts only a lowercase 64-hex SHA-256
and freezes the other three fields to the constants above.
`runner_policy_identity()` hashes the exact policy JSON value plus the
canonical manifest value under namespace `labpilot-runner-policy:v2`.
`parse_runner_policy()` accepts only
`launch_profile_version=candidate-b-v2` and receives the already validated
fixed manifest from `load_runner_policy()`; reporter path and digest never
become policy JSON keys. `FrozenLaunchSpec`,
`frozen_launch_spec_value()`, and `frozen_launch_spec_hash()` remain unchanged.

- [ ] **RED:** add tests named
  `test_candidate_b_v2_policy_identity_binds_reporter_digest`,
  `test_changed_reporter_bytes_change_policy_identity_and_rebuilt_h`,
  `test_policy_rejects_unsupported_launch_profile_version`, and
  `test_reporter_fields_are_not_policy_schema`. Run
  `uv run pytest tests/unit/execution/test_runner_policy.py tests/unit/execution/test_types.py tests/unit/execution/test_execution_service.py -v`.
  Expected RED: policy identity is still the v1 hash of policy JSON alone and
  unsupported profile strings are admitted.
- [ ] **Minimal implementation:** add the immutable manifest value and v2
  identity derivation; preserve the FrozenLaunchSpec schema and existing H
  algorithm. The expected digest is the lowercase SHA-256 of the Step 1
  asset's UTF-8/LF bytes at the fixed bundled-source path from the exact Git
  archive. A test freezes equality between those bundled bytes, the immutable
  installed bytes, and the manifest digest.
- [ ] **GREEN:** rerun the RED command; require byte-identical policy JSON with
  different reporter bytes to produce different
  `launch_policy_identity` values and different rebuilt H values.
- [ ] **Focused regression:**
  `uv run pytest tests/unit/execution/test_runner_protocol.py tests/unit/execution/test_physical_types.py -v`.
- [ ] **Commit:** `feat: bind candidate b v2 profile identity`.

**Review/stop condition:** a proposal to change FrozenLaunchSpec fields, the H
algorithm, policy JSON schema with reporter configuration, Runner protocol, or
Coordinator profile awareness stops as an Accepted-ADR contradiction.

#### Internal Step 3 — receipt parsing and systemd observation

**Files:**

```text
Create: src/labpilot/execution/terminal_receipt.py
Create: tests/unit/execution/test_terminal_receipt.py
Modify: src/labpilot/execution/systemd_supervisor.py
Modify: tests/unit/execution/test_systemd_supervisor.py
```

**Interfaces:**

```text
@dataclass(frozen=True, slots=True)
class TerminalReceiptV1:
    service_result: str | None
    exit_code: str | None
    exit_status: str | None

@dataclass(frozen=True, slots=True)
class TerminalCause:
    result: str | None
    exit_code: int | None
    termination_signal: int | None

parse_terminal_receipt_v1(message: object) -> TerminalReceiptV1
map_terminal_receipt_v1(receipt: TerminalReceiptV1) -> TerminalCause
SystemdSupervisor._current_unit_terminal_cause(
    properties: Mapping[str, str],
) -> TerminalCause | None
SystemdSupervisor._retained_manager_terminal_cause(
    records: Sequence[Mapping[str, object]],
    invocation_id: str,
    boot_id: str,
) -> TerminalCause | None
```

The parser requires canonical JSON with exactly the four Accepted fields,
schema `labpilot-terminal-receipt-v1`, and the same bounded-token/null rules as
the producer. Known mappings are exactly natural zero, exit N > 0, clean TERM,
KILL, and timeout precedence. A valid unsupported combination returns the
bounded supervisor result with no exit code or signal.

`SystemdSupervisor` adds
`_observe_receipts(unit, invocation_id, boot_id) -> tuple[TerminalReceiptV1, ...] | None`.
`None` means query failure/timeout; an empty tuple means a successful query
with no match. The exact query is:

```text
journalctl
_SYSTEMD_UNIT=<exact X.service>
_UID=0
_GID=0
_SYSTEMD_INVOCATION_ID=<exact manager invocation>
_BOOT_ID=<exact historical boot>
--output=json
--no-pager
-n 10
```

The angle-bracket values are the already verified correlation key. The
unit/root/invocation/boot provenance filters precede the tail bound. H is
accepted only from the exact trusted PID1 Started witness. Receipt bytes never
establish X, H, invocation, boot, or terminality.

Cause evidence uses this exact precedence after ManagerEvidence proves the
matching lifecycle and terminality:

```text
A. current-unit cause independently proven by trusted systemd state
   + exact X/H
   + real ExecMainCode / ExecMainStatus
   -> preserve exit code or signal
   -> Result=timeout takes precedence over killed TERM

B. historical cause independently proven by a trusted manager process-exit
   record correlated to exact unit + invocation + boot
   -> preserve exit code or signal
   -> correlated manager timeout takes precedence over killed TERM

C. no independently proven current/retained cause
   + one valid correlated TerminalReceiptV1
   -> apply the admitted receipt cause mapping

D. no independently proven current/retained cause
   + manager terminality
   + successful receipt query with zero matching receipts
   -> TERMINAL_MATCH / PROCESS_TERMINAL_UNCLASSIFIED
```

Receipt absence, query failure, or weaker receipt content never erases a cause
that current-unit or retained trusted process evidence already proves.
`Deactivated successfully` alone remains terminality evidence only and never
becomes exit zero. Historical v1 and Candidate B v2 use the same physical
evidence precedence; no profile field, persisted profile state, Coordinator
awareness, or Runner protocol change is introduced.

Loaded-unit/post-GC equivalence applies when the retained trusted fact sets are
the same. In particular, a retained process-exit fact and its loaded-unit
counterpart map identically, and RuntimeMax timeout precedence is identical on
both paths. A loaded current-unit cause that was never retained is a stronger
fact set than manager terminality alone after GC; preserving it while loaded
does not license fabrication after it disappears.

Cardinality and errors are exact:

```text
query failure/timeout                         -> UNKNOWN
zero + manager terminality                    -> TERMINAL_MATCH / unclassified
one valid                                     -> admitted or bounded-unsupported mapping
one malformed trusted matching record         -> UNKNOWN
more than one trusted matching record          -> UNKNOWN
wrong invocation/boot                         -> cannot complete current lifecycle
same X/wrong H                                 -> sticky IDENTITY_CONFLICT before cause use
```

- [ ] **RED/characterization:** first freeze these exact historical v1 tests
  in `tests/unit/execution/test_systemd_supervisor.py`:
  `test_loaded_v1_real_exit_zero_precedes_receipt_absence`,
  `test_loaded_v1_real_sigkill_precedes_receipt_absence`,
  `test_retained_v1_real_exit_seven_precedes_receipt_absence`, and
  `test_gc_v1_manager_only_without_receipt_is_terminal_unclassified`.
  The first three are characterization tests and must PASS before the
  observation refactor; the fourth must RED on the unsafe inferred-zero
  baseline. Then add parser tests and loaded/post-GC paired parameterized tests
  for natural 0, exit 7, clean SIGTERM, SIGKILL, RuntimeMaxSec, explicit stop,
  missing, malformed, duplicate, query failure/timeout, unsupported bounded,
  wrong invocation/boot, byte-identical workload spoof, and wrong H. Run
  `uv run pytest tests/unit/execution/test_terminal_receipt.py tests/unit/execution/test_systemd_supervisor.py -v`.
  Expected RED: retained success guesses zero, loaded timeout exposes TERM, and
  no ReceiptEvidence query/parser exists.
- [ ] **Minimal implementation:** remove the inferred-zero fallback; parse
  ManagerEvidence and ReceiptEvidence separately; apply A→B→C→D precedence;
  share only the receipt cause mapper and trusted process-cause normalizer
  between loaded and GC observations. Do not change `PhysicalObservation`,
  domain terminal kinds, or Coordinator code.
- [ ] **GREEN:** rerun the RED command. Each paired loaded/GC case must produce
  identical `PhysicalObservation` fields for the same retained facts; all four
  named v1 tests must pass; timeout must be
  `result="timeout"`, `exit_code=None`, `termination_signal=None` on both
  paths.
- [ ] **Focused regression:**
  `uv run pytest tests/unit/execution/test_execution_service.py tests/unit/execution/test_runner_daemon.py -v`.
- [ ] **Commit:** `fix: recover terminal cause from trusted receipt`.

**Review/stop condition:** if exact unit + H witness + invocation + boot cannot
be established independently of receipt MESSAGE bytes, or journal selection
cannot filter trusted provenance before `-n`, stop for architecture review.

#### Internal Step 4 — fixed Candidate B v2 launch profile

**Files:**

```text
Modify: src/labpilot/execution/systemd_supervisor.py
Modify: tests/unit/execution/test_systemd_supervisor.py
```

**Interfaces:** keep `_launch_argv(x, h, spec) -> tuple[str, ...]`; add no
caller-facing hook interface. Its fixed construction preserves all v1
properties and makes only these mechanical changes proven by Step 0:

```text
remove: --setenv=<workload key>=<workload value> unit properties
add:    --property=UnsetEnvironment=LD_AUDIT LD_DEBUG LD_LIBRARY_PATH LD_PRELOAD PYTHONHOME PYTHONPATH
add:    --property=ExecStopPost=-+/usr/bin/python3 -I -B /opt/labpilot-runner/libexec/labpilot-terminal-receipt-v1.py
main:   -- /usr/bin/env -i <sorted KEY=VALUE pairs> <runtime> <entrypoint> <argv>
```

Those bracketed values are FrozenLaunchSpec data, not caller-selected hook
data. The unchanged properties remain `Type=exec`, `User=labpilot-train`,
`Group=labpilot-train`, `Restart=no`, `KillMode=control-group`,
`NoNewPrivileges=yes`, empty main capability sets, journal stdout/stderr,
RuntimeMaxSec, TimeoutStopSec, fixed X-derived unit, and
`Description=labpilot:<H>`. Workload profile values remain visible to the
non-root main process. They are never placed in the unit-wide environment and
cannot influence privileged interpreter, dynamic-library, or reporter-code
resolution. The exact `-+` form is usable only after Step 0 passes.

- [ ] **RED:** replace the fixed-launch golden test with an exact Candidate B
  v2 argv assertion and add tests proving a caller cannot select hook/path/argv,
  `LD_LIBRARY_PATH` occurs only after `/usr/bin/env -i`, and main UID/GID,
  NNP, capabilities, timeout, stop timeout, X, and H properties remain frozen.
  Run
  `uv run pytest tests/unit/execution/test_systemd_supervisor.py -v -k 'launch or profile or environment'`.
  Expected RED: `_launch_argv()` uses unit-wide `--setenv` and has no fixed
  `ExecStopPost`.
- [ ] **Minimal implementation:** change only `_launch_argv()` and fixed
  constants needed by it; do not add a generic command builder or hook model.
- [ ] **GREEN:** rerun the RED command and compare the full tuple, not a
  substring.
- [ ] **Focused regression:**
  `uv run pytest tests/unit/execution/test_systemd_supervisor.py tests/unit/execution/test_runner_daemon.py -v`.
- [ ] **Commit:** `feat: launch candidate b v2 receipt profile`.

**Review/stop condition:** any need for shell evaluation, caller-selected root
argv, unit-wide workload loader environment, broader root execution, or loss
of the main workload security profile stops for architecture review.

#### Internal Step 5 — Runner readiness and immutable deployment

**Files:**

```text
Modify: src/labpilot/execution/runner_policy.py
Modify: src/labpilot/execution/runner_daemon.py
Modify: scripts/install_labpilot_runner_host.sh
Modify: tests/unit/execution/test_runner_policy.py
Modify: tests/unit/execution/test_runner_daemon.py
Modify: tests/unit/execution/test_runner_deployment.py
```

**Interfaces:**

```text
def validate_candidate_b_v2_deployment(
    reporter_path: Path = TERMINAL_RECEIPT_REPORTER_PATH,
    interpreter_path: Path = TRUSTED_REPORTER_INTERPRETER,
) -> CandidateBV2ProfileManifest

RUNNER_VERSION = "candidate-b-v2"

shell: install_or_verify_terminal_receipt() -> status
```

The validator walks `/opt`, `/opt/labpilot-runner`, and
`/opt/labpilot-runner/libexec` without following untrusted symlinks; each
directory must be root-owned and not group/world writable. It opens the final
reporter with `O_NOFOLLOW`, requires a regular root-owned non-group/world-
writable file, hashes bytes through that descriptor, and compares the digest
with the exact bundled source asset at
`/opt/labpilot-runner/app/deploy/libexec/labpilot-terminal-receipt-v1.py` from
the installed Git archive. That digest constructs the fixed manifest used by
policy identity. `/usr/bin/python3` must resolve entirely through a
root-owned non-group/world-writable path to a root-owned regular interpreter.
An isolated sanity execution uses the absolute interpreter with `-I -B`, an
empty environment, a short timeout, and the expected canonical all-null
receipt. `load_runner_policy()` validates these facts before constructing the
Runner policy identity; a failed check prevents daemon readiness entirely.

Installer behavior is exact:

```text
v1 reporter absent
-> create trusted libexec directory
-> install exact archive asset root:root, non-group/world-writable
-> verify bytes/path/interpreter/sanity

v1 reporter present + exact bytes and required metadata
-> reuse without replacement

v1 reporter present + different bytes or unsafe metadata
-> fail before app activation; never overwrite in place
```

`/opt/labpilot-runner/app` remains replaceable, while sibling `libexec` is not
removed by upgrades. Tests assert ownership/write/symlink/digest invariants,
not an arbitrary single numeric mode beyond the existing root-owned
non-group/world-writable deployment contract.

- [ ] **RED:** add fail-closed tests for unsafe directory ownership/write
  bits, symlink/path redirection, non-regular final file, reporter ownership,
  reporter write bits, digest mismatch, manifest mismatch, missing/untrusted
  interpreter, isolated sanity failure, immutable install/reuse/drift, and app
  upgrade preservation. Update INFO tests to require
  `runner_version=candidate-b-v2`, `launch_profile_version=candidate-b-v2`, and
  `readiness_reason=ready` only after validator success. Run
  `uv run pytest tests/unit/execution/test_runner_policy.py tests/unit/execution/test_runner_daemon.py tests/unit/execution/test_runner_deployment.py -v`.
  Expected RED: Runner v1 reports ready without validation and the installer
  has no immutable libexec contract.
- [ ] **Minimal implementation:** add the descriptor-based validator, update
  version/admission, and add installer install-or-reuse checks before service
  activation. Preserve INFO/LAUNCH/OBSERVE/STOP wire operations.
- [ ] **GREEN:** rerun the RED command; every unsafe case must fail before
  `readiness_reason=ready` or app activation.
- [ ] **Focused regression:**
  `bash -n scripts/install_labpilot_runner_host.sh && uv run pytest tests/unit/execution -v`.
- [ ] **Commit:** `feat: enforce candidate b v2 immutable deployment`.

**Review/stop condition:** in-place replacement of a different v1 artifact,
an untrusted writable path component, a policy-configurable reporter, or a
need to alter the Runner protocol stops for architecture review.

#### Internal Step 6 — production verification and review gate

No H1/H2 harness robustness edit is included in this gate.

**Files:**

```text
Create: tests/acceptance/slice2/candidate_b_v2_fixtures.py
Create: tests/acceptance/slice2/fixtures/a18_installer_drift_probe.sh
Modify: tests/acceptance/slice2/native_preflight.py
Modify: tests/acceptance/slice2/native_campaign.py
Modify: tests/acceptance/slice2/scenarios.py
Modify: tests/acceptance/slice2/test_native_preflight.py
Modify: tests/acceptance/slice2/test_native_slice2.py
Modify: scripts/run_slice2_native_acceptance.py
```

**Interfaces:** add test-admin-only
`run_profile_matrix(selection: str, config: NativeAcceptanceConfig) -> None`
and CLI option `--profile-matrix V2-A1-V2-A20`. The closed formal scenario
registry and Runner wire operations remain unchanged.

`candidate_b_v2_fixtures.py` owns these exact closed interfaces:

```text
class V2MatrixIsolation(StrEnum):
    NON_DESTRUCTIVE_FINAL_STACK_CHECK
    ISOLATED_TEST_ADMIN_FIXTURE
    DESTRUCTIVE_HOST_CHECK_REQUIRING_FULL_REBASELINE_BEFORE_CAMPAIGN

@dataclass(frozen=True, slots=True)
class FinalStackFingerprint:
    tested_sha: str
    reporter_sha256: str
    policy_identity: str
    runner_exec_start: str
    runner_active_state: str
    runner_invocation_id: str
    compose_container_ids: tuple[tuple[str, str], ...]
    compose_image_ids: tuple[tuple[str, str], ...]

@dataclass(frozen=True, slots=True)
class V2FixtureLease:
    row: str
    units: tuple[str, ...]
    proof_root: Path | None
    compose_project: str | None
    cleanup_is_domain_evidence: bool

capture_final_stack_fingerprint(
    config: NativeAcceptanceConfig,
) -> FinalStackFingerprint
create_missing_receipt_fixture(
    config: NativeAcceptanceConfig,
    tested_sha: str,
) -> V2FixtureLease
create_trusted_receipt_fixture(
    config: NativeAcceptanceConfig,
    tested_sha: str,
    variant: Literal["malformed", "duplicate"],
) -> V2FixtureLease
create_isolated_v2_runner_fixture(
    config: NativeAcceptanceConfig,
    tested_sha: str,
) -> V2FixtureLease
create_isolated_v2_compose_fixture(
    config: NativeAcceptanceConfig,
    tested_sha: str,
) -> V2FixtureLease
prepare_v1_compatibility_before_v2_install(
    config: NativeAcceptanceConfig,
    tested_sha: str,
) -> V2FixtureLease
verify_v1_compatibility_after_v2_install(
    config: NativeAcceptanceConfig,
    lease: V2FixtureLease,
) -> dict[str, object]
run_isolated_installer_drift_fixture(
    config: NativeAcceptanceConfig,
    tested_sha: str,
) -> dict[str, object]
cleanup_candidate_b_v2_fixture(
    config: NativeAcceptanceConfig,
    lease: V2FixtureLease,
) -> None
```

Every helper switches on a closed row/variant and constructs fixed argv. It
accepts no shell fragment, arbitrary executable, systemd property, unit name,
root path, or cleanup command from the caller. X, H, unit, proof root, socket,
Compose project, and evidence paths are deterministically derived from the
exact tested SHA and row under the configured dedicated test-admin roots.

- [ ] **RED:** add registry/CLI/evidence tests requiring exactly V2-A1 through
  V2-A20, rejecting unknown/partial/duplicate IDs, binding the frozen tested
  SHA, and preventing any F scenario dispatch. Add exact tests named
  `test_v2_a11_uses_real_missing_receipt_unit_without_reporter_mutation`,
  `test_v2_a12_uses_real_root_post_stop_records`,
  `test_v2_a15_is_prepared_before_v2_install_without_downgrade`,
  `test_v2_a18_uses_mount_namespace_and_fixed_installer_validator`, and
  `test_matrix_cleanup_requires_equal_final_stack_fingerprint`. Run
  `uv run pytest tests/acceptance/slice2/test_native_preflight.py tests/acceptance/slice2/test_native_slice2.py -v -m 'not native' -k 'profile_matrix or v2'`.
  Expected RED: no profile-matrix selector, closed fixture module, final-stack
  fingerprint, or isolated A11/A12/A15/A18 mechanic exists.
- [ ] **Minimal implementation:** implement the closed fixtures and wire the
  exact 20-row matrix under §7.1.1. Reuse existing bounded command/evidence
  primitives; add no generic root executor, production API, or fabricated
  systemd/journal record.
- [ ] **GREEN:** rerun the RED command, then run at one exact production
  implementation HEAD:

```bash
uv run pytest tests/unit/execution/test_terminal_receipt_reporter.py -v
uv run pytest tests/unit/execution/test_terminal_receipt.py -v
uv run pytest tests/unit/execution/test_systemd_supervisor.py -v
uv run pytest tests/unit/execution/test_runner_policy.py -v
uv run pytest tests/unit/execution/test_runner_daemon.py -v
uv run pytest tests/unit/execution/test_runner_deployment.py -v
uv run pytest tests/architecture -v -m architecture
uv run pytest tests/unit/execution -v
uv run pytest tests/integration -v -m integration
uv run ruff check src tests scripts deploy/libexec/labpilot-terminal-receipt-v1.py
uv run mypy src scripts
bash -n scripts/install_labpilot_runner_host.sh
./scripts/check_ci.sh
git diff --check
```

Then rerun the bounded dedicated systemd-259 Step 0 mechanism proof using the
exact production reporter bytes and Accepted M1 semantics under its isolated
proof boundary. Do not execute any V2-A row in Internal Step 6: this step only
implements and statically/non-natively verifies the complete matrix harness;
authoritative native matrix evidence remains pending.
- [ ] **Focused regression:**
  `uv run pytest tests/acceptance/slice2 -v -m 'not native'`.
- [ ] **Fixture script syntax:**
  `bash -n tests/acceptance/slice2/fixtures/a18_installer_drift_probe.sh`.
- [ ] **Commit:** `test: add candidate b v2 profile matrix harness`.

After the non-native harness/production verification and the isolated Step 0
mechanism re-proof pass, **STOP FOR EXTERNAL PRODUCTION CODE REVIEW**. The
complete V2-A1–V2-A20 harness exists at this gate, but no V2 profile acceptance
is claimed. Its sole authoritative native execution remains §7.5 item 14,
after final SHA freeze, exact Phase H, fresh Phase I, and fresh N1–N7. H1, H2,
final SHA freeze, F01+, and F18 do not begin inside the production semantic
review unit.

#### Internal Step 7 — deferred harness remediation H1

This step begins only after external production code review.

**Files:**

```text
Modify: tests/acceptance/slice2/native_preflight.py
Modify: tests/acceptance/slice2/native_campaign.py
Modify: tests/acceptance/slice2/oracles.py
Modify: tests/acceptance/slice2/test_native_preflight.py
Modify: tests/acceptance/slice2/test_native_slice2.py
```

**Interfaces:**

```text
class NativeCommandFailure(NativePreflightError):
    operation: str
    argv_sha256: str
    return_code: int
    stdout: bytes
    stderr: bytes

NativeCommandFailure(
    operation: str,
    argv_sha256: str,
    return_code: int,
    stdout: bytes,
    stderr: bytes,
)

bounded_failure_stream(value: bytes, *, limit: int = 8192) -> dict[str, object]
```

`run_command()` continues bounding each captured stream at its existing 64 KiB
execution limit. On nonzero status it raises `NativeCommandFailure` carrying
only a fixed operation basename, SHA-256 of canonical argv bytes, return code,
and bounded stdout/stderr bytes; raw argv is not evidence. Human-readable
failure evidence is at most 8192 bytes per stream. Invalid UTF-8, control
characters other than line/tab, configured secret values, private-key/token/
credential markers, or redaction uncertainty cause the whole excerpt to be
`"[REDACTED]"`; byte count, truncation flag, and SHA-256 remain available.

H1 explicitly adds one test-only evidence filename:

```text
worker-failure.json
```

Its exact fields are:

```text
schema_version               integer 1
scenario                     exact F01 through F22
operation                    bounded safe executable basename
argv_sha256                  64 lowercase hex
return_code                  integer
stdout                       byte_count, sha256, truncated, excerpt
stderr                       byte_count, sha256, truncated, excerpt
unexpected_worker_failure    true
```

This file is diagnostic evidence only. `verdict.json` remains the sole verdict
authority. The deliberate worker status `86` in F05/F22 remains an expected
fault boundary and never creates `worker-failure.json`.

- [ ] **RED:** add deterministic tests that a nonzero command retains both
  bounded streams, output above the execution bound still terminates the
  process group, unsafe excerpts redact completely, safe excerpts truncate at
  exactly 8192 bytes, worker-failure schema is exact, and status 86 remains
  expected. Run
  `uv run pytest tests/acceptance/slice2/test_native_preflight.py tests/acceptance/slice2/test_native_slice2.py -v -m 'not native' -k 'command or worker_failure or status_86'`.
  Expected RED: current `run_command()` raises only a status string and drops
  both buffers.
- [ ] **Minimal implementation:** add the focused exception, conservative
  serializer, authorized evidence name, and unexpected-failure catch; do not
  change scenario verdict calculation.
- [ ] **GREEN:** rerun the RED command.
- [ ] **Focused regression:**
  `uv run pytest tests/acceptance/slice2 -v -m 'not native'`.
- [ ] **Commit:** `test: retain bounded native command failure evidence`.

**Review/stop condition:** any raw secret, unbounded stream, raw argv, second
verdict field, or status-86 misclassification stops before H2.

#### Internal Step 8 — deferred harness remediation H2

This step follows H1 as a separate test-only commit.

**Files:**

```text
Modify: tests/acceptance/slice2/native_worker.py
Modify: tests/acceptance/slice2/test_native_slice2.py
```

**Interface:** keep
`NativeWorkerRuntime._workload_oracle(workspace: Path) -> dict[str, object] | None`.
Open the oracle once in binary read mode, acquire `fcntl.LOCK_SH` on that same
file descriptor, read at most `16 * 1024 + 1` bytes, validate the bound and
JSON object, then unlock and close in `finally`.

- [ ] **RED:** a writer process opens the oracle, acquires `LOCK_EX`, truncates,
  signals a test barrier, and waits. A reader thread calls
  `_workload_oracle`; an instrumented real `fcntl.flock` signals when the
  reader requests `LOCK_SH`. Before the implementation, the reader observes
  the truncated document and raises `NativeWorkerExecutionError`; the test
  fails because `LOCK_SH` was never requested. Run
  `uv run pytest tests/acceptance/slice2/test_native_slice2.py -v -k workload_oracle_flock_symmetry`.
- [ ] **Minimal implementation:** import `fcntl` in `native_worker.py` and
  perform lock/read/validate on one descriptor. After the reader's `LOCK_SH`
  attempt is observed, the test writer writes one canonical complete document
  and releases `LOCK_EX`; GREEN requires the reader to return only that full
  object.
- [ ] **GREEN:** rerun the RED command with no timing-based acceptance oracle.
- [ ] **Focused regression:**
  `uv run pytest tests/acceptance/slice2 -v -m 'not native'`.
- [ ] **Commit:** `test: lock native workload oracle reads`.

**Review/stop condition:** do not attribute historical F01 at
`d3d3513022b1526ab6763ec2e91dca1e86c6ce5d` to this race. Its exception
remains UNKNOWN and its evidence remains sealed.

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

**Status:** ACCEPTED AT G3. Implementation began in a new Task 4 window from the Task 3 G2 governance closure commit and ended with the Task 4 G3 governance closure commit.

**Risk:** Workflow / race critical.

**Deliverable:** Thin durable user commands, deterministic single-Coordinator recovery/scheduling, fixed concurrency ≤4 including UNKNOWN and IDENTITY_CONFLICT, startup reconciliation barrier with durable restart audit, and production composition where API has no Runner socket/workspace authority.

## 6.0 Narrow Task 4 compatibility amendment

**Governance:** Task 3 remains ACCEPTED AT G2. Task 4 remains AUTHORIZED / in implementation. Task 5 remains NOT AUTHORIZED. `DOCUMENT-STATUS.md` remains unchanged.

The accepted Task 3 stop primitive is compatibly extended so a startup cancellation pass can persist its mandatory fresh pre-stop observation and restart audit atomically:

```python
async def request_stop(
    attempt_id: UUID,
    *,
    reason: ReconciliationReason = ReconciliationReason.PERIODIC,
    coordinator_start_id: UUID | None = None,
) -> None
```

It uses the same validation as `reconcile_attempt()`: STARTUP requires `coordinator_start_id`; PERIODIC forbids it. Default callers remain PERIODIC and retain the accepted Task 3 behavior.

For a STARTUP cancellation pass, the first fresh observation inside `request_stop()` is the Attempt's STARTUP reconciliation observation. That observation and `attempt.reconciled_after_restart` are persisted atomically through the existing `_persist_observation()` path, including when `TERMINAL_MATCH` terminalizes the Attempt in that UoW. A non-`ACTIVE_MATCH` observation returns without STOP. An `ACTIVE_MATCH` observation permits the existing exact-match STOP; any post-STOP reconciliation remains PERIODIC and cannot duplicate the startup audit. Coordinator never appends the restart audit directly.

Production Coordinator numeric identity inputs are frozen as required positive decimal integers:

```text
LABPILOT_CONTROLLER_UID = host UID of fixed user labpilot-controller
LABPILOT_CONTROLLER_GID = host GID of fixed group labpilot-control
LABPILOT_TRAIN_GID      = host GID of fixed group labpilot-train
```

They are deployment identity only and never enter Trial identity, `FrozenExecutionSpec`, `FrozenLaunchSpec`, or H. Compose uses:

```yaml
user: "${LABPILOT_CONTROLLER_UID}:${LABPILOT_CONTROLLER_GID}"
group_add:
  - "${LABPILOT_TRAIN_GID}"
```

Coordinator receives `LABPILOT_TRAIN_GID` and passes its exact numeric value as `AttemptPreparationService(..., execution_gid=settings.train_gid)`. It never infers the GID from workspace ownership, process groups, Runner responses, or container account databases. Task 5 native acceptance will prove the configured numbers correspond to the fixed host identities.

Execution-only Settings are process-scoped. The shared Settings object may keep `attempt_workspace_root`, `runner_socket_path`, and `train_gid` optional, with strict validation whenever supplied. API does not require or receive them. Coordinator outside tests fails closed at its composition boundary when any is absent. This supersedes the older global requirement that every non-test process must configure `LABPILOT_ATTEMPT_WORKSPACE_ROOT`; no generic process-role framework is introduced.

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

Derived state must converge: a cancelled Trial with zero Attempts presents `cancelled`; a cancelled Batch with no active, pending, or needs-attention Trial presents `cancelled`; and a started, non-cancelled Batch with no active, pending, or needs-attention Trial and at least one failed Trial presents `failed`, including mixed succeeded plus failed terminal outcomes. UNKNOWN and IDENTITY_CONFLICT keep their existing priority and presentation. No persisted `Batch.status` is added.

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
    if status == IDENTITY_CONFLICT:
        preserve parent cancel intent
        call reconcile_attempt(..., reason=STARTUP, coordinator_start_id=...)
        never request_stop / STOP / kill
    elif parent cancel exists:
        call request_stop(
            attempt_id,
            reason=STARTUP,
            coordinator_start_id=coordinator_start_id,
        )
        this call is the startup reconciliation/audit pass
        do not issue a second STARTUP reconcile
    else:
        call reconcile_attempt(..., reason=STARTUP, coordinator_start_id=...)

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
Runner AF_UNIX runtime directory mounted at `/run/labpilot-runner`
host-compatible labpilot-controller UID/GID
labpilot-train supplementary GID for workspace preparation; composition resolves that fixed group to numeric execution_gid and passes it explicitly to AttemptPreparationService
```

Compose obtains those identities only from the required `LABPILOT_CONTROLLER_UID`, `LABPILOT_CONTROLLER_GID`, and `LABPILOT_TRAIN_GID` deployment variables frozen in §6.0. It does not hardcode numeric identities, mount host passwd/group databases, or generalize the fixed identity names into configuration.

Coordinator bind-mounts the host `/run/labpilot-runner` directory at the identical path with Compose long bind syntax, `bind.create_host_path: false`, and read-only access when supported by the accepted local Compose path. It must not bind-mount the single socket inode. API receives no Runner runtime-directory mount, and `RunnerClient` continues to connect to `/run/labpilot-runner/control.sock`.

The accepted root Runner service adds `RuntimeDirectoryPreserve=restart` so the mounted runtime-directory identity survives both `Restart=on-failure` and installer `systemctl restart`. Runner startup handles only its own trusted stale socket: it uses `lstat`, rejects symlink/non-socket/wrong-owner/wrong-group/unsafe modes, accepts only root:`labpilot-control` socket mode 0700 or 0660 under the hardened root:`labpilot-control` 0750 runtime directory, refuses to unlink a socket with a live listener, and unlinks only a trusted stale socket proven to have no listener before binding. The existing post-bind root:`labpilot-control` 0660 verification remains exact. Runner protocol and systemd authority do not widen.

Task 3 remains **ACCEPTED AT G2**. This is a direct Task 4 composition compatibility correction; the affected deployment/socket invariants are subject to finding-specific G3 re-review.

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
LABPILOT_ATTEMPT_WORKSPACE_ROOT          required at Coordinator composition outside tests; canonical absolute; no guessed default
LABPILOT_RUNNER_SOCKET_PATH              required at Coordinator composition; production value /run/labpilot-runner/control.sock
LABPILOT_RUNNER_REQUEST_TIMEOUT_SECONDS  positive bounded value, default 10
LABPILOT_UNKNOWN_ATTENTION_GRACE_SECONDS positive bounded value, default 60
LABPILOT_CONTROLLER_UID                  required positive integer at Coordinator composition
LABPILOT_CONTROLLER_GID                  required positive integer at Coordinator composition
LABPILOT_TRAIN_GID                       required positive integer at Coordinator composition
```

`LABPILOT_ATTEMPT_WORKSPACE_ROOT` is required at the Coordinator composition boundary outside tests, not globally for API. API requires none of the workspace, Runner socket, controller UID/GID, or train GID settings.

Compose mounts `${LABPILOT_ATTEMPT_WORKSPACE_ROOT}` into Coordinator at the same absolute path and mounts the host `/run/labpilot-runner` directory only into Coordinator using long bind syntax with `bind.create_host_path: false`. API receives neither.

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

**Authorization:** Task 5 remains historically `AUTHORIZED / IN PROGRESS`, but
the current Candidate B v2 implementation-plan delta is **PROPOSED / PENDING
EXTERNAL IMPLEMENTATION-PLAN REVIEW**. The Accepted architecture authorizes
this plan draft only. Candidate B v2 implementation, native acceptance, and
F18 are **NOT AUTHORIZED** by this draft. Task 5 and Slice 2 are not COMPLETE
or ACCEPTED; G4 has not occurred.

Task 4 G3 acceptance does not prove any of the following; each remains a Task 5 evidence obligation:

- actual host numeric IDs correspond to `labpilot-controller`, `labpilot-control`, and `labpilot-train`;
- real `SO_PEERCRED` succeeds between the composed Coordinator and Runner;
- real `labpilot-train` can traverse, read, execute, and write with the exact workspace permissions;
- an already-running Coordinator container reconnects after `systemctl restart labpilot-runner.service` without container recreation;
- real `RuntimeDirectoryPreserve` and AF_UNIX stale-socket replacement work end-to-end across systemd and Docker mount namespaces;
- persistent journald recovery works across actual process or host restart; and
- failed launch, timeout, and restart-during-reconciliation behave correctly on the native stack.

Do not execute those proofs in the Task 4 window. `DOCUMENT-STATUS.md` remains unchanged until full Slice 2 acceptance.

**Risk:** Release critical.

**Deliverable:** Real native-Linux evidence that production Tasks 2–4 survive process/database/control-plane failures and preserve exact source, environment observation, exact X/H, no duplicate launch, cancellation, UNKNOWN/conflict, capacity, timeout, and reboot invariants.

### 7.0 G4 and document-status closure

The final repository gate becoming green does not accept G4 or Slice 2. Task 5
implementation stops for external G4 review after all native evidence and
repository gates are complete. `DOCUMENT-STATUS.md` remains unchanged in this
implementation window. Only after external G4 approval may a separate
governance-only closure mark Task 5 accepted, mark Slice 2 accepted, and update
`DOCUMENT-STATUS.md` exactly once.

### 7.0.1 Non-native marker gate and native test-admin authority

Task 5 acceptance selection is marker-based. `native` is the explicit pytest
marker for tests that perform or require real native-host acceptance. Pure
fixture, oracle, serialization, registry, config, bounded-polling,
fault-decorator, and F18 metadata round-trip tests are not marked `native`.
Ordinary CI and every non-native Task 5 gate use `-m "not native"`; filename or
node-name text is never the isolation mechanism.

Native mode requires `LABPILOT_SLICE2_ACCEPTANCE_CONFIG` to name an explicit
absolute test-admin config path. Its absence fails before systemd interaction,
acceptance-database mutation, Runner installation, Compose startup, process
control, or reboot preparation. The config is test-only and does not alter
production Settings or API contracts. It has no defaults and provides at
least:

```text
version = 1
dedicated_candidate_b_host = true
expected_hostname
repository_root
workspace_root
runner_policy_source
evidence_root
dataset_root
database_url_env
api_base_url
compose_project
python_profile
```

Every filesystem path is an explicit absolute native-host path.
`database_url_env` names the environment variable holding database authority;
the config and evidence never contain database credentials. SSH credentials,
passwords, and private keys are not repository config or evidence. The actual
hostname must equal `expected_hostname`, and WSL is rejected even if systemd is
available. Task 5 remains AUTHORIZED / IN PROGRESS as the historical outer
status; that status does not authorize Candidate B v2 implementation before
this delta is externally accepted and implementation is separately
authorized. G4 has not occurred and `DOCUMENT-STATUS.md` remains unchanged.

### 7.0.2 Task 5 scenario verdict evidence contract

Every new F01-F22 scenario evidence directory has exactly one authoritative
acceptance status field in `verdict.json`: `verdict`. Its allowed values and
semantics are:

```text
PASS     every required scenario obligation was positively established from
         real evidence and no relevant critical-class violation was observed
FAIL     determinate evidence established that the system under test violated
         a required invariant
STOPPED  the harness cannot produce a valid determinate scenario verdict due
         to an environment, authority, infrastructure, evidence, or mandated-
         stop condition
```

`STOPPED` is never `PASS`. `UNKNOWN` and `IDENTITY_CONFLICT` are domain states
or physical observations, not scenario verdicts. F14 may therefore end with
domain state `UNKNOWN` and verdict `PASS` when every required UNKNOWN invariant
holds. F15 may similarly end with domain state `IDENTITY_CONFLICT` and verdict
`PASS` when every conflict invariant holds.

The exact core schema is:

```json
{
  "schema_version": 1,
  "scenario": "F01",
  "tested_sha": "<40 lowercase hex>",
  "verdict": "PASS",
  "reason_code": "obligations_satisfied",
  "sealed": true,
  "administrative_cleanup_is_domain_evidence": false
}
```

The requirements are exact:

- `schema_version` is integer `1`; a boolean is invalid;
- `scenario` is exactly `F01` through `F22`;
- `tested_sha` is exactly 40 lowercase hexadecimal characters;
- `verdict` is exactly `PASS`, `FAIL`, or `STOPPED`;
- `reason_code` is non-empty lowercase ASCII snake case and at most 80 ASCII
  bytes;
- `sealed=true` and `administrative_cleanup_is_domain_evidence=false` are
  added by `EvidenceWriter` and are not caller-controlled; and
- top-level `outcome`, `passing_verdict`, `status`, `success`, and `passed`
  aliases are forbidden. Scenario-specific domain facts belong in the other
  authorized evidence files, not in competing verdict fields.

No second authoritative boolean is introduced. Canonical bounded JSON bytes
are mode `0600`, data-fsynced, and published atomically without replacing an
existing `verdict.json`. Sealing is filesystem write-once for a scenario
evidence directory: neither the same writer nor a new process/new
`EvidenceWriter` may create, replace, or reseal the target through the harness
API, and generic `write_json("verdict.json", ...)` cannot bypass sealing. The
returned digest is SHA-256 of the exact published bytes. Publication must fail
closed when the target already exists and must not follow or replace a symlink.

The authorized evidence filename set remains unchanged until the separately
reviewed H1 step. H1 explicitly adds only `worker-failure.json` with the exact
diagnostic-only schema in §5.9.3; it does not add verdict authority.
Historical evidence is immutable and is not migrated to that schema.

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
ExecStart Python flags include -E -s -B
UMask=0077
NoNewPrivileges=yes
RestrictAddressFamilies=AF_UNIX
runtime directory root:labpilot-control 0750
control.sock root:labpilot-control 0660
INFO ready with expected policy identity
```

A repository unit file that is correct while systemd has a stale/different loaded unit is an acceptance failure.

### 7.1.1 Candidate B v2 pre-formal-campaign profile matrix

V2-A1 through V2-A20 are Task 5 profile preflight obligations, not new formal
Slice 2 tasks and not replacements for F01-F22. Execute them exactly once at
§7.5 item 14, against the frozen production implementation HEAD and only after
exact Phase H, fresh Phase I, and fresh N1–N7. That is the sole authoritative
native matrix evidence lineage; Internal Step 6 only implements and
non-natively verifies this harness. Every row records the exact tested SHA,
unit/invocation/boot where applicable, bounded manager and receipt evidence,
and a determinate pass/fail result.

| ID | Class | Exact proof and pass condition |
|---|---|---|
| V2-A1 | NON-DESTRUCTIVE FINAL-STACK CHECK | Read and verify the immutable root-owned reporter, trusted `/opt/labpilot-runner` and `libexec` path chain, regular non-symlink artifact, write safety, and manifest digest. |
| V2-A2 | ISOLATED TEST-ADMIN FIXTURE | Launch a dedicated throwaway unit with adversarial `LD_LIBRARY_PATH`/`PYTHONPATH`; main UID/GID remain `labpilot-train`, main NNP is 1, capability sets remain empty, root receipt provenance is `_UID=0`/`_GID=0`, and no loader/import canary appears. |
| V2-A3 | ISOLATED TEST-ADMIN FIXTURE | Use the fixed mechanism-proof producer in a dedicated unit and compare reporter exit 71 with the exit-0 control; main `Result`, `SERVICE_RESULT`, `EXIT_CODE`, and `EXIT_STATUS` remain identical. |
| V2-A4 | ISOLATED TEST-ADMIN FIXTURE | Emit byte-identical receipt bytes from the non-root workload in a dedicated unit; root provenance filtering rejects them and only the real root producer record is eligible. |
| V2-A5 | ISOLATED TEST-ADMIN FIXTURE | Natural exit 0 in a dedicated unit, allow GC, then production OBSERVE returns `TERMINAL_MATCH`, `exit_code=0`, and no signal from one valid correlated receipt. |
| V2-A6 | ISOLATED TEST-ADMIN FIXTURE | Exit 7 in a dedicated unit, allow GC, then production OBSERVE returns exact `exit_code=7`. |
| V2-A7 | ISOLATED TEST-ADMIN FIXTURE | Clean SIGTERM in a dedicated unit, allow GC, then production OBSERVE returns exact `termination_signal=15`. |
| V2-A8 | ISOLATED TEST-ADMIN FIXTURE | SIGKILL a dedicated unit, allow GC, then production OBSERVE returns exact `termination_signal=9`. |
| V2-A9 | ISOLATED TEST-ADMIN FIXTURE | RuntimeMax on a dedicated unit returns `result=timeout`, no exit code, and no signal both loaded and post-GC. |
| V2-A10 | ISOLATED TEST-ADMIN FIXTURE | Explicitly STOP a dedicated unit; production OBSERVE retains the strongest proven physical signal while receipt alone makes no Batch-cancel causality claim. |
| V2-A11 | ISOLATED TEST-ADMIN FIXTURE | Launch a separate deterministic X/unit with real PID1 lifecycle evidence and no post-stop producer; production OBSERVE sees a successful zero-match receipt query and returns terminal-unclassified without modifying the production reporter. |
| V2-A12 | ISOLATED TEST-ADMIN FIXTURE | Run two dedicated units using the fixed root-owned test-admin post-stop producer: one emits exactly one malformed root-provenance record and one emits exactly two canonical records. Real unit/invocation/boot correlation reaches production OBSERVE and each case yields `UNKNOWN`, attention, no retry, and no relaunch. |
| V2-A13 | ISOLATED TEST-ADMIN FIXTURE | Use a separate fixed v2 Runner service/socket/policy and dedicated active unit; restart only that Runner, then prove systemd emitted the receipt and later production OBSERVE recovers it. |
| V2-A14 | ISOLATED TEST-ADMIN FIXTURE | Use a separate Compose project, DB/schema, workspace, evidence root, and isolated Runner fixture; stop its Coordinator after physical terminal/before DB commit, then prove startup reconciliation consumes the retained cause and converges the original Attempt. |
| V2-A15 | NON-DESTRUCTIVE FINAL-STACK CHECK | Consume the sealed Phase H v1→v2 transition evidence prepared before v2 installation: v2 OBSERVE/STOP preserved the already-active v1-compatible X/H lifecycle without relaunch or v2→v1 downgrade. |
| V2-A16 | ISOLATED TEST-ADMIN FIXTURE | Create a dedicated v1-equivalent GC'd lifecycle with manager terminality and no retained cause/receipt; production OBSERVE returns terminal-unclassified, never fabricated zero. |
| V2-A17 | ISOLATED TEST-ADMIN FIXTURE | Create a dedicated same-X/wrong-H unit; production OBSERVE remains sticky `IDENTITY_CONFLICT` with no stop, replacement, retry, or relaunch. |
| V2-A18 | ISOLATED TEST-ADMIN FIXTURE | In a private mount namespace, bind an isolated disposable tree over the fixed `/opt/labpilot-runner` view, drift one isolated reporter byte, and invoke the real fixed-path installer validation function; it fails closed while the host production reporter hash remains unchanged. |
| V2-A19 | ISOLATED TEST-ADMIN FIXTURE | Emit high workload journal volume from a dedicated unit; trusted manager filters precede their tail bound and retain the exact H/lifecycle witness. |
| V2-A20 | NON-DESTRUCTIVE FINAL-STACK CHECK | Read protocol/INFO state and run closed decoder tests; operation set remains exactly INFO/LAUNCH/OBSERVE/STOP and no hook/reporter operation exists. |

#### Matrix mutation, cleanup, and rebaseline contract

Each row records its class in `scenario.json`. Before the first row and before
and after every isolated fixture, the harness calls
`capture_final_stack_fingerprint()` and compares exact tested SHA, production
reporter digest, Runner policy identity/effective ExecStart/active state, and
Runner invocation plus Compose container/image IDs.

```text
NON-DESTRUCTIVE FINAL-STACK CHECK
mutation scope: none; read-only inspection only
cleanup authority: none
cleanup is domain evidence: NO / not applicable
Phase H / Phase I / N1-N7 rerun: NO
evidence remaining valid: all exact-SHA preflight and earlier matrix evidence

ISOLATED TEST-ADMIN FIXTURE
mutation scope: only lease-listed dedicated X/unit, proof root, isolated
                Runner socket/service/policy, isolated Compose project/DB/
                workspace, or private mount namespace
cleanup authority: cleanup_candidate_b_v2_fixture() under explicit test-admin
cleanup is domain evidence: NO
Phase H / Phase I / N1-N7 rerun: NO only when cleanup completes and the exact
                                 final-stack fingerprint is unchanged
evidence remaining valid: sealed row evidence and all prior exact-SHA final-
                          stack evidence only after that equality check

DESTRUCTIVE HOST CHECK REQUIRING FULL REBASELINE BEFORE CAMPAIGN
mutation scope: any unavoidable change to final Runner installation, immutable
                reporter, final policy identity, final services, or final
                Compose stack
cleanup authority: external test admin only
cleanup is domain evidence: NO
required order: STOP -> seal evidence -> external admin restoration -> fresh
                Phase H -> fresh Phase I if affected -> fresh N1-N7 -> fresh
                required V2-A validation -> only then F01
evidence remaining valid: behavioral evidence from the destructive row may be
                          reviewed, but no pre-restoration evidence proves the
                          restored final stack
```

No current V2-A row is planned as destructive. If any isolated mechanic cannot
maintain its lease boundary or final-stack fingerprint, the row is
reclassified as destructive at execution time and the full stop/rebaseline
order applies. Cleanup failure, unexpected root-owned state, or a changed
fingerprint is `STOPPED`; test-admin cleanup never fabricates domain success.

Dedicated units use deterministic valid X/H values derived from tested SHA
and row, never an Attempt from the formal F campaign. A fixture may call the
production `SystemdSupervisor.observe(X, H)` to exercise real parsing, but it
cannot write a physical observation, journal JSON, Attempt state, or verdict.

#### V2-A11 real missing-receipt fixture

`create_missing_receipt_fixture()` creates one valid test-admin X and exact
`X.service` with `Description=labpilot:<H>`, `Type=exec`, the accepted non-root
main security profile, and `/usr/bin/true`; it deliberately has no
`ExecStopPost`. It waits for real trusted PID1 start/terminal records and unit
GC, then calls the production observer. The receipt query is real and
successful, with zero exact root/unit/invocation/boot matches. The fixture
first asserts that the retained manager stream contains terminality but no
independent process-exit cause; if systemd retains a real cause, the fixture is
STOPPED rather than hiding that fact. Pass is
`TERMINAL_MATCH` with no exit code or signal. The production immutable reporter
and final Runner installation are read and fingerprinted, never suppressed,
renamed, edited, or replaced.

Cleanup resets/removes only the lease-listed throwaway unit and proof metadata.
It is not domain evidence. Equal before/after final-stack fingerprints mean no
Phase H/I/N1-N7 rerun; otherwise STOP and full rebaseline.

#### V2-A12 real trusted-root cardinality fixtures

`create_trusted_receipt_fixture(..., variant="malformed")` and
`create_trusted_receipt_fixture(..., variant="duplicate")` install one exact-
digest test-only producer under
`/opt/labpilot-runner-mechanism-proof/libexec/receipt-cardinality-probe.py`.
The producer is root-owned, regular, non-group/world-writable, accepts no
caller data, and is referenced by fixed `ExecStopPost=-+` in two separate
throwaway units:

```text
malformed -> one bounded non-JSON stdout record
duplicate -> two newline-delimited canonical TerminalReceiptV1 stdout records
```

systemd/journald, not the harness, creates the records and supplies their real
`_SYSTEMD_UNIT`, `_UID=0`, `_GID=0`, `_SYSTEMD_INVOCATION_ID`, and `_BOOT_ID`.
The helper first records those actual correlation fields and proves manager
terminality with no independently retained process-exit cause, then invokes
the production observer for the exact X/H. If a stronger cause was retained,
the fixture is STOPPED instead of discarding it. The malformed case and
duplicate case must each yield `UNKNOWN`/attention. No synthetic journal JSON
enters the observer and the production immutable reporter is untouched.

Cleanup removes only both lease-listed units and the fixed mechanism-proof
artifact after sealing journal evidence. Cleanup is not domain evidence.
Fingerprint equality avoids rebaseline; any mismatch or incomplete cleanup
stops for the full rebaseline order.

#### V2-A15 sealed v1-to-v2 Phase H transition

Immediately before §7.5 Phase H activates the v2 Runner,
`prepare_v1_compatibility_before_v2_install()` creates one long-running
v1-equivalent physical lifecycle using the exact previously Accepted v1
launch profile, deterministic test-admin X/H, `labpilot-train`, and no receipt
hook. It seals X, H, PID, invocation, boot, unit properties, start cardinality,
and the pre-v2 installation identity. It does not install or start a v1 Runner.

Phase H then performs the normal one-way v2 installation. Without any
downgrade, `verify_v1_compatibility_after_v2_install()` requires the v2 Runner
to return `ACTIVE_MATCH` for the sealed X/H, perform STOP only after that fresh
match, observe terminality, and prove physical start cardinality remained one.
Failure stops Phase H. Cleanup of the dedicated unit is test-admin action and
not domain evidence.

The later V2-A15 matrix row is a non-destructive check of that sealed exact-SHA
transition evidence and current v2 final-stack fingerprint. It never performs
or claims a v2-to-v1 hot downgrade.

#### V2-A18 isolated immutable-drift proof

`run_isolated_installer_drift_fixture()` invokes only the fixed committed
`tests/acceptance/slice2/fixtures/a18_installer_drift_probe.sh`. The Python
caller validates one generated fixture root beneath the configured evidence
root; the shell helper rejects extra operations and enters a private mount
namespace with private propagation. Inside that namespace it bind-mounts the
disposable exact app/libexec tree over the fixed `/opt/labpilot-runner` view,
changes one byte only in the isolated reporter, sources the real
`scripts/install_labpilot_runner_host.sh`, and calls its production
`install_or_verify_terminal_receipt` function. The expected result is fail-closed
before activation or overwrite.

Namespace exit removes the isolated view automatically. The harness hashes the
real host reporter before and after and requires equality, plus the complete
final-stack fingerprint. It never modifies
`/opt/labpilot-runner/libexec/labpilot-terminal-receipt-v1.py` on the host.
Therefore Phase H/I/N1-N7 do not rerun on PASS. If mount isolation is
unavailable, the host hash changes, or the real validator cannot run through
this fixed mechanic, A18 becomes destructive: STOP, external restoration,
fresh Phase H, fresh Phase I if affected, fresh N1-N7, and fresh required V2-A
validation before F01. No evidence predating restoration proves that restored
stack.

The dedicated profile command is:

```bash
uv run python scripts/run_slice2_native_acceptance.py --profile-matrix V2-A1-V2-A20
```

The CLI addition is test-admin selection only; it does not change the Runner
protocol or formal F01-F22 scenario registry. Any matrix FAIL or STOPPED seals
available evidence and prevents F01.

## 7.2 Required native scenarios

```text
F01 happy vertical approved Version -> real terminal training; observed environment persisted; after unit GC, receipt-backed natural exit 0 is required and guessed-zero logic is forbidden
F02 exact approved source; dirty worktree ignored; prelaunch source tamper blocks launch
F03 physical concurrency <= 4 and durable FIFO
F04 Coordinator down: Start persists, zero Attempt/process until restart
F05 crash after LAUNCH_PENDING commit before Runner call: observe-only, no same-Attempt launch
F06 systemd accepted but LAUNCH acknowledgement lost: observe-only recovery, start count = 1
F07 SIGKILL Coordinator while RUNNING: workload survives; restart reattaches; start count = 1
F08 workload exits while Coordinator down: restart obtains retained trusted manager + receipt terminal cause and converges without relaunch
F09 PostgreSQL outage while workload runs: no physical restart; reconcile after DB recovery
F10 durable Batch cancel while Coordinator down: restart observes exact match then stops; physical cause is retained without receipt-only workflow causality
F11 cancellation kills whole systemd control group; no orphan child
F12 cancel vs natural-exit race preserves proven natural terminal truth
F13 STOP acknowledgement lost: no direct terminalization; observe to terminal and preserve physical cause without receipt-only workflow causality
F14 observation unavailable beyond grace: UNKNOWN, attention, slot retained, no retry
F15 real same-X/different-H systemd fixture: durable IDENTITY_CONFLICT, never UNKNOWN, no destructive action
F16 one UNKNOWN plus queued work: UNKNOWN consumes one of four physical slots
F17 mixed restart barrier: existing RUNNING/LAUNCH_PENDING/UNKNOWN/IDENTITY_CONFLICT reconciled/audited before new dispatch
F18 separately authorized real host reboot: no same-X resurrection; historical boot-bound ManagerEvidence + ReceiptEvidence may recover cause; otherwise old X/H ends at the Accepted-ADR bounded terminal-unclassified or UNKNOWN result
F19 kill API while real training runs; training remains; restart API; same Attempt remains queryable; zero physical relaunch
F20 stop root Runner before first socket connect after LAUNCH_PENDING commit: definite pre-send non-acceptance -> LAUNCH_NOT_ACCEPTED; no physical unit created
F21 real RuntimeMaxSec timeout: loaded and post-GC observations are identical `timeout` / terminal-unclassified with no fabricated exit/signal cause; no duplicate launch
F22 crash Coordinator after real OBSERVE result is obtained but before reconciliation UoW commit; restart Coordinator; repeat observation from the same physical manager/receipt evidence and converge original Attempt without duplicate launch; restart audit remains coherent
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

The formal campaign remains stop-on-first-failure. Any `FAIL`, `STOPPED`,
evidence corruption, identity drift, or unexpected production state stops the
campaign, seals available evidence, and returns to external review. A scenario
is never rerun merely to obtain more favorable timing.

Historical F01 at
`d3d3513022b1526ab6763ec2e91dca1e86c6ce5d` remains sealed defect evidence and
is neither repaired nor reused. Fresh Candidate B v2 F01 uses a new DB, Batch,
Trial, Attempt, X, H bound to `candidate-b-v2`, workspace, receipt, and evidence
directory.

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
C17 effective production Runner service drops Python -E/-s/-B, NoNewPrivileges, AF_UNIX restriction, or UMask=0077
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
worker-failure.json (only after H1 and only for an unexpected worker failure)
verdict.json
```

Evidence includes R0/S1/S2.0/implementation commit identities, installation-identity digest, Runner policy identity/version/protocol, effective service hardening facts, boot ID(s), X, H, workspace path, SourceManifest hash, and selected bounded RuntimeEvents. It excludes secrets, full process environment, unbounded journal, and full DB dumps.

`worker-failure.json` is optional diagnostic evidence under the exact H1
contract in §5.9.3. Its absence is required for expected F05/F22 status 86,
and it never competes with `verdict.json`.

For scenarios intentionally ending UNKNOWN or IDENTITY_CONFLICT, seal/hash the verdict before external administrator teardown. Teardown must not write fake `terminal_at`, clear attention state, rewrite conflict as UNKNOWN, or mark domain success.

## 7.5 Task 5 execution steps

The obsolete direct-to-campaign ordering is replaced by this exact sequence.
Each numbered item is a gate; later items do not authorize themselves.

1. **Accepted architecture amendment already closed.** Confirm both Accepted supervision
   documents and the Candidate B v2 acceptance baseline; do not edit them.
2. **External implementation-plan review — SATISFIED.** External review
   returned ACCEPT at exact baseline
   `8a62fb6df5a4cf67479b27776a3cb8b49baf575a`. That ACCEPT is a
   governance-review fact only and does not authorize Internal Step 0 or any
   TDD.
3. **Governance-only implementation-plan acceptance — SATISFIED.** This
   docs-only governance closure records the Candidate B v2 plan delta as
   **ACCEPTED / CURRENT IMPLEMENTATION PLAN DELTA**. Plan acceptance does not
   authorize Internal Step 0, production edits, native execution, the formal
   campaign, or F18.
4. **Separate explicit Candidate B v2 IMPLEMENTATION AUTHORIZATION.** Only a
   later authorization that explicitly permits Candidate B v2 implementation
   may start Internal Step 0 and production TDD. Its scope must separately
   state whether the bounded native Step 0 mechanism proof is authorized; it
   never implies final native-campaign or F18 authority.
5. **Candidate B v2 production TDD implementation.** Execute Internal Steps
   0–6 in order, including the native M1 mechanism gate before production
   semantics depend on it.
6. **External production code review.** Stop at the exact production HEAD;
   H1/H2 are excluded from this semantic review.
7. **H1 harness remediation.** Execute Internal Step 7 as its own RED/GREEN
   test-only commit.
8. **H2 harness remediation.** Execute Internal Step 8 as its own deterministic
   RED/GREEN test-only commit.
9. **Exact-head full repository verification.** In supported Python 3.12 with
   PostgreSQL drivers, run:

   ```bash
   ./scripts/check_ci.sh
   uv run pytest tests/acceptance/slice2 -v -m "not native"
   uv run python -m compileall -q src tests scripts deploy/libexec
   bash -n scripts/install_labpilot_runner_host.sh
   git diff --check
   ```

   Create a fresh PostgreSQL database, migrate from zero through
   `0010_attempt_physical_execution.py`, and rerun the integration suite.
10. **Freeze one final Task 5 scenario SHA.** Record one 40-hex `TESTED_SHA`
   after every production, test, and documentation commit required for the
   campaign is present.
11. **Exact Runner install / Phase H.** Before v2 activation, prepare and seal
   the V2-A15 v1-equivalent active lifecycle. Then install that SHA and exact
   policy, verify the v2 Runner observes/stops that lifecycle without relaunch,
   and verify immutable reporter/profile identity, effective service, INFO
   readiness, runtime directory, socket, and installed application tree.
12. **Fresh production stack / Phase I.** Build and start the API,
    Coordinator, and PostgreSQL stack from the same frozen SHA and record
    exact image/container identities.
13. **Fresh N1–N7.** Run the existing native preflight set once against the
    exact installed and composed stack; any failure stops the lineage.
14. **Candidate B v2 profile acceptance matrix.** Run V2-A1–V2-A20 under the
    classification, isolation, cleanup, and rebaseline rules in §7.1.1; seal
    bounded per-row evidence before F01.
15. **Fresh F01–F17.** Run in numeric order with first-failure-stop and fresh
    state per scenario:

    ```bash
    uv run python scripts/run_slice2_native_acceptance.py --scenarios F01-F17
    ```

16. **F18 NOT RUN.** Do not prepare, reboot, or verify F18 in the non-reboot
    campaign.
17. **Fresh F19–F22.** Continue only if F01–F17 all produced determinate PASS:

    ```bash
    uv run python scripts/run_slice2_native_acceptance.py --scenarios F19-F22
    ```

18. **External evidence review.** Review N1–N7, V2-A1–V2-A20, F01–F17, and
    F19–F22 plus hashes, critical-class scan, and stop-on-first-failure
    compliance.
19. **Explicit separate F18 authorization.** No previous gate implies reboot
    authority.
20. **F18 prepare/reboot/verify only if authorized.** After explicit approval:

    ```bash
    uv run python scripts/run_slice2_native_acceptance.py --scenario F18 --phase prepare
    uv run python scripts/run_slice2_native_acceptance.py --scenario F18 --phase verify
    ```

    The authorized human/admin reboot occurs between commands. Verify uses the
    frozen historical boot/X/H/receipt locator and never LAUNCHes the old
    Attempt.
21. **Final G4 external review.** Review the complete evidence set and final
    repository gate; green automation alone does not accept G4.
22. **Governance-only Slice 2 closure after G4.** Only a separate explicit
    governance authorization may mark Task 5/Slice 2 accepted and update
    `DOCUMENT-STATUS.md` exactly once.

At plan-acceptance time the native campaign remains NOT AUTHORIZED, F18 remains
separately gated, G4 remains unreached, and `DOCUMENT-STATUS.md` remains
unchanged.

Any repository commit after item 10 invalidates the campaign lineage. Reinstall
the exact new HEAD, rebuild the production stack, rerun N1–N7 and V2-A1–V2-A20,
and start a fresh formal campaign; never mix evidence across SHAs.

---

# 8. Review / stop gates

Implementation stops for human review at exactly:

```text
G0 r4.1 targeted remediation approved by final R4-D1-only finding-specific review
G1 Task 2 accepted: preparation contract/exact source/identities proven, zero physical launch
G2 Task 3 accepted: Candidate B production deployment + launch/observe/stop + environment/audit/ack-loss proven
G3 Task 4 accepted: Coordinator/API composition, startup barrier, capacity, cancellation, privilege split proven
G4 Task 5 N1–N7 + V2-A1–V2-A20 + fresh F01–F22 + final repository gate accepted
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
- the §5.6.1 post-G3 Task 5 native-finding amendment additionally requires a relocatable non-editable install, Python `-B`, final-path import proof, and post-readiness application-tree integrity verification without changing the historical G2/G3 record.
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

## 9.1 Candidate B v2 plan-delta self-review

### Accepted-amendment coverage

| Accepted criterion | Planned implementation/test/native obligation |
|---|---|
| TR-D1 timely cause capture | Internal Steps 0, 1, 3; V2-A5–V2-A10 |
| TR-D2 fixed producer | Internal Steps 1 and 4; V2-A2–V2-A4 |
| TR-D3 unforgeable authority | Internal Step 3 root/unit/invocation/boot filters; V2-A4 |
| TR-D4 trusted physical identity | Internal Step 3 ManagerEvidence join; V2-A5–V2-A17 |
| TR-D5 cause-only receipt | Internal Steps 1 and 3 exact four-field schema; V2-A4 |
| TR-D6 mechanical distinction | Internal Step 3 paired natural-zero/SIGTERM tests; V2-A5 and V2-A7 |
| TR-D7 no guessed zero | Internal Step 3 missing-receipt test; V2-A11; fresh F01 |
| TR-D8 failure neutrality | Internal Step 0 native proof; V2-A3 |
| TR-D9 artifact-bound H | Internal Step 2 digest/identity/H RED test; V2-A1 and V2-A18 |
| TR-D10 v1 compatibility / v2 admission | Internal Steps 2, 3, and 5; the four named historical-v1 stronger/absent-cause tests; V2-A15 and V2-A16 |
| TR-D11 no domain expansion | File map and every stop condition; V2-A20 |
| TR-D12 retained proven cause | Internal Step 3 loaded/GC equivalence; V2-A5–V2-A16; F08/F18/F22 |

Accepted amendment §§1–2 are bound by the measured-gap record and Step 0;
§§3–19 are covered by Internal Steps 0–5. T1–T5 are covered respectively by
Step 1; Step 3; Steps 0/2/4; Steps 2/5; and Step 5. T6 is deliberately split:
before external production code review, the isolated Step 0 mechanism re-proof
establishes privilege override, failure neutrality, loader isolation, main
NNP/capability preservation, and terminal-variable injection using the exact
production reporter bytes and Accepted M1 semantics. Only after final SHA
freeze, exact Phase H, fresh Phase I, and fresh N1–N7 does the sole §7.5
V2-A1–V2-A20 execution establish complete native Candidate B v2 profile
acceptance: deployment, GC recovery, receipt provenance/cardinality, Runner
restart, Coordinator restart, v1 compatibility, immutable drift, and protocol
preservation. The §21 negative boundary is exercised by the negative tests in
Steps 1–5 and V2-A2/A4/A11/A12/A17/A18/A20. The closure order in §25 is
implemented by §7.5. Sections 23, 24, and 26 are enforced by the authority
precedence, explicit out-of-scope stop conditions, and accepted governance
status in this delta.

### I1–I3 remediation self-review

1. External plan ACCEPT and implementation authorization are distinct facts.
2. Section 7.5 records a docs-only plan-acceptance commit followed by a later
   explicit implementation-authorization gate before Internal Step 0.
3. Current-unit exact X/H + real ExecMainCode/ExecMainStatus evidence outranks
   receipt evidence and preserves historical v1 exit/signal cause with timeout
   precedence.
4. Correlated retained trusted process-exit evidence preserves historical v1
   cause; manager-only terminality still never guesses zero.
5. V2-A11 uses a separate real systemd lifecycle with real zero-match receipt
   absence and never suppresses the production reporter.
6. V2-A12 uses real root post-stop producer records with exact real
   unit/invocation/boot provenance and never fabricated journal JSON.
7. V2-A15 prepares a v1-equivalent active lifecycle before v2 Phase H and
   never performs a v2-to-v1 downgrade.
8. V2-A18 runs real fixed-path installer validation inside a private mount
   namespace and requires the host reporter hash to remain unchanged.
9. Every mutating matrix row has a closed lease, non-domain cleanup, exact
   fingerprint postcondition, and explicit rebaseline consequence.
10. All introduced fixture functions and their owning files are named before
    use; the installer function consumed by A18 is defined in Internal Step 5.
11. The placeholder scan covers the full plan delta and admits no unspecified
    helper or work item.
12. The delta is **ACCEPTED / CURRENT IMPLEMENTATION PLAN DELTA** after
    external implementation-plan review.
13. Candidate B v2 implementation, native execution, and F18 remain **NOT
    AUTHORIZED** by this plan remediation.

### I4 native-order remediation self-review

1. Internal Step 6 implements and non-natively tests all matrix harness
   mechanics but executes no native V2-A row.
2. No V2 profile acceptance is claimed before final Phase H, Phase I, and
   N1–N7.
3. V2-A15 consumes only the sealed evidence produced by the actual §7.5 Phase
   H transition.
4. Section 7.5 item 14 is the sole authoritative V2-A1–V2-A20 native evidence
   lineage.
5. External production code review is preceded by the bounded isolated Step 0
   mechanism re-proof using exact production reporter bytes and Accepted M1
   semantics.
6. Section 7.5 retains plan acceptance, explicit implementation authorization,
   Steps 0–6, production review, H1/H2, verification, final SHA, H/I, N1–N7,
   V2-A1–V2-A20, and the F campaign in that order.
7. The delta is **ACCEPTED / CURRENT IMPLEMENTATION PLAN DELTA** after
   external implementation-plan review.
8. Candidate B v2 implementation, native execution, and F18 remain **NOT
   AUTHORIZED** by this plan remediation.

### Consistency results

- **Spec coverage:** PASS. Every TR-D1–TR-D12 criterion maps to at least one
  named implementation step and one focused or native verification obligation.
- **Placeholder scan:** PASS. This delta contains no unspecified work item or
  deferred helper/function name.
- **Type/interface consistency:** PASS. Producer bytes precede manifest
  identity; manifest identity precedes policy/H tests; parser interfaces
  precede supervisor use; the installer validator precedes A18; closed matrix
  fixture interfaces precede the sole authoritative matrix execution after
  final SHA/Phase H/Phase I/N1–N7; production review precedes H1/H2; H1
  evidence types precede their campaign use.
- **File responsibility:** PASS. The standalone asset only produces a receipt;
  `terminal_receipt.py` only parses/maps it; existing policy, supervisor,
  daemon, installer, and harness files retain their established roles;
  `candidate_b_v2_fixtures.py` owns only closed test-admin matrix mechanics and
  the A18 shell helper owns only its fixed private mount-namespace probe.
- **Governance:** PASS. The delta is **ACCEPTED / CURRENT IMPLEMENTATION PLAN
  DELTA** after external implementation-plan review. Candidate B v2
  implementation, native execution, F18, G4, and `DOCUMENT-STATUS.md` closure
  remain unauthorized.
