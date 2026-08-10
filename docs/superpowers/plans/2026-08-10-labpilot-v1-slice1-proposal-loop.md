# LabPilot V1 Slice 1 — Repository → Proposal → Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute this plan task-by-task. Do **not** dispatch implementation subagents for this repository. Stop at the review/authorization gates defined below.

**Planning baseline:** `032dd87d3366dc6edd66fb75451d44047e49f7dd`

**Status:** Approved Implementation Plan

**Goal:** From a real, already-captured `RepositorySnapshot`, let a bounded Agent inspect only approved repository source, produce a structured `ProposalRevision`, pass deterministic verification, receive exact human approval, and materialize one immutable `ExperimentVersion`.

**Architecture:** Preserve WP0 + WP1-A Tasks 0–10 as the Accepted Foundation. Add a narrow Repository source facade rather than exposing Snapshot/Bundle/CAS internals; keep Agent inference non-authoritative; keep Proposal verification and Approval inside the experiments authority boundary; add only a static read-only Tool path and a bounded Agent loop required to reach the Slice 1 outcome. Use short PostgreSQL UoWs around authoritative writes and never hold a transaction across Git/filesystem reads or model/network calls.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy asyncio, PostgreSQL 16, Alembic, `httpx`, native Git CLI through the existing `GitAdapter`, existing `ContentAddressedStore`, `uv`, pytest, Ruff, mypy.

## Global Constraints

- Normative precedence is `V1 Scope v0.3 > Architecture 10 v0.3 > Master Roadmap v0.3 > this approved Slice plan`.
- This plan may specialize Slice 1 only. It must not weaken an R0 Core invariant or introduce Slice 2/3 implementation work.
- Accepted Foundation code is not rewritten for architecture purity. Existing CommandReceipt, RuntimeEvent/Outbox, RepositorySnapshot/Bundle/CAS, and Task 6 identities remain intact.
- Old WP1-A Tasks 11–17 remain `Superseded / Never Executed`; specifically, do not revive the Task 11 persisted Freshness transition workflow.
- Dirty Snapshot may be inspected for Proposal drafting. Design Approval and `ExperimentVersion` require a clean Snapshot that passes an immediate approval-readiness check.
- A formal Agent/repository fact is authoritative only when it is produced by a deterministic repository check or rechecked deterministically against the bound Snapshot.
- Agent prose/confidence is never enough to satisfy a deterministic verifier rule.
- `DatasetReference` and `EnvironmentExpectation` are owner-local value objects, not registries/platforms.
- Design Approval must bind the exact Proposal payload, verifier result, clean RepositorySnapshot, DatasetReference, EnvironmentExpectation, actor, and time.
- `ExperimentVersion` is immutable; semantic changes produce a new Version.
- Slice 1 creates no Batch/Trial/Attempt, does not materialize execution source, and does not launch training.
- No Evidence Closure/Claim graph, Inspector Registry, dynamic Tool Manifest/plugin platform, Projection platform, generic workflow engine, ResultProcessing platform, Artifact CAS, or generic supervision backend.
- No new identity/hash is admitted unless it protects deduplication, idempotency, replay, or immutable semantic comparison.
- Secrets must not enter Prompt, ToolResult, RuntimeEvent, ordinary trace, or persisted Agent decision JSON.
- Remote provider source egress is denied when effective policy is `local_only`.
- Production Slice 1 Tools are read-only Repository Tools only. No production side-effecting Tool is added in this Slice.
- No transaction/UoW may remain open during Git/filesystem I/O, content-store reads, HTTP/model calls, or Tool execution.
- Full CI/fresh PostgreSQL is a Slice acceptance gate, not a per-remediation default.
- Status is updated once at Slice acceptance, not after each implementation unit.
- Do not push an implementation unit until its required review gate is satisfied.
- ChatGPT/Codex workspace model quota is a **development-time tool only**. It is not a LabPilot runtime credential or ModelProvider. Slice 1 production runtime uses the provider-neutral `ModelProvider` port plus one narrow OpenCode Go adapter backed by the user's OpenCode Go API key.
- Slice 1 intentionally supports only the OpenCode Go models currently served through the Go `/v1/chat/completions` protocol family. Models served through `/v1/responses` or `/v1/messages` require a future adapter and are out of this Slice.

### Targeted remediation scope — r2

The original focused plan review is complete. r2 changes only these approved findings plus direct consistency edits:

```text
P1  Freeze one concrete OpenCode Go chat/completions production adapter + contract tests.
P2  Freeze the exact AgentDecision discriminated union.
P3  Keep live repository currentness out of immutable ProposalVerification;
    recheck currentness immediately at Approval instead.
P4  Defer FastAPI/SSE dependency refresh until an SSE endpoint is actually implemented.
P5  Add immutable bound_toolset_hash audit semantics for each model call.
P6  Freeze the Slice 1 structured operational logging/correlation/redaction contract.
P7  Freeze concurrent ExperimentVersion.version_number allocation under Experiment row lock.
```

Explicitly unchanged by remediation:

```text
ToolApproval remains because V1-FR-015 is still a current Slice 1 requirement.
Task 11 Freshness workflow remains superseded.
Repository facade and Task 0–10 Snapshot/Bundle/CAS internals are not redesigned.
No ModelRequestSnapshot, ToolManifest, Projection platform, or new provider family is introduced.
Manual ProposalRevision retry ambiguity remains a non-blocking Deferred Finding.
```

---

## 1. File / ownership map

### Existing Accepted Foundation reused without semantic rewrite

- `src/labpilot/context/git_adapter.py` — authoritative Git observation/tree/blob access. Do not redesign it in Slice 1.
- `src/labpilot/context/content_store.py` — immutable content-addressed dirty-overlay bytes.
- `src/labpilot/context/models.py` — RepositoryRegistration/Snapshot/Bundle/Freshness historical persistence.
- `src/labpilot/context/repository_repository.py` — add narrow read queries only.
- `src/labpilot/db/uow.py` — existing short transaction boundary.
- `src/labpilot/db/idempotency.py` — reuse only where the Idempotency Matrix says Recommended and it materially simplifies retry semantics.
- `src/labpilot/experiments/models.py` / Project files — existing Project foundation remains unchanged except explicit wiring where required.
- `src/labpilot/observability/*` — historical capability remains; Slice 1 does not require Projection Worker.

### New Slice 1 code

#### Repository facade

- Create `src/labpilot/context/repository_source.py`
  - narrow Snapshot source read model;
  - reconstructs base tree + stored dirty overlay;
  - bounded list/read/search/path checks;
  - immediate clean-Snapshot approval-readiness check.
- Create `src/labpilot/context/proposal_repository_adapter.py`
  - implements the experiments-owned repository fact port using `RepositorySnapshotSourceService`.
- Modify `src/labpilot/context/repository_repository.py`
  - read-only `get_snapshot(...)`, `get_freshness(...)` helpers only.

#### Experiment / Proposal authority

- Create `src/labpilot/experiments/proposal_types.py`
  - Proposal value objects, DatasetReference, EnvironmentExpectation, declarations, command/result DTOs, canonical semantic hashes.
- Create `src/labpilot/experiments/proposal_ports.py`
  - repository fact Protocol owned by experiments so experiments never import context.
- Create `src/labpilot/experiments/proposal_models.py`
  - Experiment, ProposalRevision, Verification, DesignDecision, ExperimentVersion rows.
- Create `src/labpilot/experiments/proposal_repository.py`
  - persistence queries/inserts for the above rows.
- Create `src/labpilot/experiments/proposal_verifier.py`
  - deterministic verifier profile `slice1-v1`.
- Create `src/labpilot/experiments/proposal_service.py`
  - create revision, verify, approve/reject, materialize/reuse immutable Version.
- Create `migrations/versions/0007_experiment_proposals.py`.

#### Tool / Agent runtime

- Create `src/labpilot/tools_policy/__init__.py`
- Create `src/labpilot/tools_policy/types.py`
- Create `src/labpilot/tools_policy/models.py`
- Create `src/labpilot/tools_policy/repository.py`
- Create `src/labpilot/tools_policy/service.py`
- Create `src/labpilot/tools_policy/repository_tools.py`
- Create `src/labpilot/agent_runtime/__init__.py`
- Create `src/labpilot/agent_runtime/types.py`
- Create `src/labpilot/agent_runtime/models.py`
- Create `src/labpilot/agent_runtime/repository.py`
- Create `src/labpilot/agent_runtime/provider.py`
- Create `src/labpilot/agent_runtime/opencode_go.py`
- Create `src/labpilot/agent_runtime/context_compiler.py`
- Create `src/labpilot/agent_runtime/service.py`
- Create `migrations/versions/0008_agent_tool_runtime.py`.

#### API

- Create `src/labpilot/api/schemas/agent_runs.py`
- Create `src/labpilot/api/schemas/experiments.py`
- Create `src/labpilot/api/routers/agent_runs.py`
- Create `src/labpilot/api/routers/experiments.py`
- Modify `src/labpilot/api/dependencies.py`
- Modify `src/labpilot/api/app.py`
- Modify `src/labpilot/api/errors.py`
- Modify `src/labpilot/main.py`

#### Foundation alignment

- Modify `scripts/check_ci.sh`
- Modify `compose.yaml`
- Modify `.env.example`
- Modify `src/labpilot/settings.py`
- Modify `tests/unit/test_settings.py`

#### Model/dependency registry

- Modify `src/labpilot/db/model_registry.py`
- Modify `tests/architecture/dependency_rules.py`
- Modify `tests/architecture/test_model_registry.py`
- Modify `tests/architecture/test_module_dependencies.py` only if a new assertion is needed; do not weaken existing module direction rules.

### New tests

- Create `tests/unit/context/test_repository_source.py`
- Create `tests/integration/context/test_repository_source.py`
- Create `tests/unit/experiments/test_proposal_types.py`
- Create `tests/unit/experiments/test_proposal_verifier.py`
- Create `tests/integration/experiments/test_proposal_service.py`
- Create `tests/unit/tools_policy/test_tool_service.py`
- Create `tests/integration/tools_policy/test_tool_persistence.py`
- Create `tests/unit/agent_runtime/test_context_compiler.py`
- Create `tests/unit/agent_runtime/test_opencode_go.py`
- Create `tests/unit/agent_runtime/test_agent_service.py`
- Create `tests/integration/agent_runtime/test_agent_service.py`
- Create `tests/integration/api/test_slice1_api.py`
- Create `tests/integration/slice1/test_proposal_loop.py`
- Create `tests/integration/slice1/test_opencode_go_smoke.py`

No frontend files are in Slice 1. REST/SSE/Web UI remain V1 requirements, but this Slice acceptance is service/API based; no SSE endpoint is needed to prove Repository → Proposal → Approval.

---

## 2. Exact interfaces frozen by this plan

### 2.1 Repository source facade

`src/labpilot/context/repository_source.py` must expose these public types/signatures:

```python
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

RepositorySourceFileType = Literal["regular", "symlink", "submodule"]
ApprovalReadinessStatus = Literal[
    "ready",
    "snapshot_not_found",
    "registration_not_active",
    "snapshot_dirty",
    "repository_unavailable",
    "repository_changed",
]

@dataclass(frozen=True, slots=True)
class RepositorySourceDescriptor:
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    repository_registration_id: UUID
    project_id: UUID
    head_commit: str
    current_branch: str | None
    is_detached_head: bool
    is_clean: bool

@dataclass(frozen=True, slots=True)
class RepositoryPathFact:
    relative_path: str
    file_type: RepositorySourceFileType
    file_mode: str

@dataclass(frozen=True, slots=True)
class RepositoryTextSlice:
    relative_path: str
    start_line: int
    end_line: int
    text: str

@dataclass(frozen=True, slots=True)
class RepositorySearchHit:
    relative_path: str
    line_number: int
    line_text: str

@dataclass(frozen=True, slots=True)
class RepositoryApprovalReadiness:
    status: ApprovalReadinessStatus
    repository_snapshot_id: UUID
    repository_snapshot_identity: str | None
    current_probe_identity: str | None

class RepositorySnapshotSourceService:
    async def describe(self, repository_snapshot_id: UUID) -> RepositorySourceDescriptor: ...

    async def list_tree(
        self,
        repository_snapshot_id: UUID,
        *,
        prefix: str | None,
        max_entries: int,
    ) -> tuple[RepositoryPathFact, ...]: ...

    async def path_fact(
        self,
        repository_snapshot_id: UUID,
        relative_path: str,
    ) -> RepositoryPathFact | None: ...

    async def directory_exists(
        self,
        repository_snapshot_id: UUID,
        relative_path: str,
    ) -> bool: ...

    async def read_text(
        self,
        repository_snapshot_id: UUID,
        relative_path: str,
        *,
        start_line: int,
        end_line: int,
        max_bytes: int,
    ) -> RepositoryTextSlice: ...

    async def search_text(
        self,
        repository_snapshot_id: UUID,
        query: str,
        *,
        prefix: str | None,
        max_results: int,
        max_file_bytes: int,
    ) -> tuple[RepositorySearchHit, ...]: ...

    async def check_approval_readiness(
        self,
        repository_snapshot_id: UUID,
    ) -> RepositoryApprovalReadiness: ...
```

Rules:

- Validate repository-relative paths with the existing strict path validator; never `Path(root) / user_string` into the live worktree.
- `list_tree` and reads are reconstructed from the bound Snapshot:
  1. base `head_commit` via existing `GitAdapter.ls_tree` / `cat_file_batch`;
  2. apply persisted Bundle overlay;
  3. deletion removes path;
  4. mode-change changes metadata only;
  5. tracked/untracked regular/symlink content comes from existing CAS `content_ref`;
  6. submodule is metadata-only and cannot be `read_text`.
- `read_text` uses strict UTF-8 decode. Binary/non-UTF-8 fails closed with a typed source-read error.
- `search_text` is bounded, literal substring search over Snapshot bytes; no index/vector DB.
- `check_approval_readiness` never persists freshness state and never creates a Snapshot. It is valid only for formal clean-Snapshot approval:
  1. load Snapshot + Registration;
  2. require Snapshot `is_clean=True` and Registration `status='active'`;
  3. call existing `GitAdapter.probe()` on the registered root outside a UoW;
  4. require canonical root, HEAD, tracked tree, branch/detached state to equal Snapshot and `probe.status_entries == ()`;
  5. otherwise return `repository_changed`/appropriate typed status.
- Existing `repository_snapshot_freshness` is neither updated nor extended by Slice 1.

### 2.2 Experiments-owned Repository Fact Port

`src/labpilot/experiments/proposal_ports.py`:

```python
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

@dataclass(frozen=True, slots=True)
class ProposalSnapshotState:
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    project_id: UUID
    is_clean: bool

@dataclass(frozen=True, slots=True)
class ApprovalSourceState:
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    project_id: UUID
    ready: bool
    reason_code: str | None

@dataclass(frozen=True, slots=True)
class ProposalPathFact:
    relative_path: str
    file_type: str
    file_mode: str

class ProposalRepositoryFactPort(Protocol):
    async def snapshot_state(self, repository_snapshot_id: UUID) -> ProposalSnapshotState: ...
    async def approval_source_state(self, repository_snapshot_id: UUID) -> ApprovalSourceState: ...
    async def path_fact(
        self,
        repository_snapshot_id: UUID,
        relative_path: str,
    ) -> ProposalPathFact | None: ...
    async def directory_exists(
        self,
        repository_snapshot_id: UUID,
        relative_path: str,
    ) -> bool: ...
```

`context/proposal_repository_adapter.py` structurally implements this port. `experiments` must not import `context`.

`ProposalVerifier` may call `snapshot_state`, `path_fact`, and `directory_exists`; it **must not** call `approval_source_state`. `approval_source_state` is the separate live-currentness boundary used only by approval/materialization eligibility so transient repository state is never cached inside an immutable Verification.

### 2.3 Proposal value objects

`src/labpilot/experiments/proposal_types.py`:

```python
from dataclasses import dataclass
from typing import Literal, Mapping
from uuid import UUID

ParameterValue = str | int | float | bool | None
ExperimentType = Literal[
    "reproduction",
    "comparison",
    "ablation",
    "parameter_sweep",
    "stability",
]
ReproducibilityLevel = Literal["strict", "best_effort"]
DatasetReferenceType = Literal["local_path", "uri"]
ValidationRuleKind = Literal["artifact_exists", "metric_finite"]

@dataclass(frozen=True, slots=True)
class DatasetReference:
    reference_type: DatasetReferenceType
    path_or_uri: str
    stable_identity: str | None
    reproducibility_level: ReproducibilityLevel

@dataclass(frozen=True, slots=True)
class EnvironmentExpectation:
    python_profile: str
    framework_requirements: tuple[str, ...]
    device_requirement: str | None
    cuda_requirement: str | None

@dataclass(frozen=True, slots=True)
class ArtifactDeclaration:
    name: str
    relative_path: str
    required: bool

@dataclass(frozen=True, slots=True)
class MetricDeclaration:
    name: str
    source_artifact: str
    split: str | None

@dataclass(frozen=True, slots=True)
class ValidationRule:
    kind: ValidationRuleKind
    subject: str

@dataclass(frozen=True, slots=True)
class ProposalPayload:
    goal: str
    experiment_type: ExperimentType
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    train_entrypoint: str
    eval_entrypoint: str | None
    workdir: str
    parameter_matrix: Mapping[str, tuple[ParameterValue, ...]]
    seeds: tuple[int, ...]
    dataset_reference: DatasetReference
    environment_expectation: EnvironmentExpectation
    metrics: tuple[MetricDeclaration, ...]
    artifacts: tuple[ArtifactDeclaration, ...]
    validation_rules: tuple[ValidationRule, ...]
    timeout_seconds: int
    max_retries: int
```

Canonical functions in the same module:

```python
def proposal_payload_hash(payload: ProposalPayload) -> str: ...
def dataset_reference_hash(value: DatasetReference) -> str: ...
def environment_expectation_hash(value: EnvironmentExpectation) -> str: ...
def validation_protocol_hash(rules: tuple[ValidationRule, ...]) -> str: ...
def experiment_version_identity(
    *,
    experiment_id: UUID,
    proposal_payload_hash_value: str,
    repository_snapshot_identity: str,
    dataset_reference_hash_value: str,
    environment_expectation_hash_value: str,
    validation_protocol_hash_value: str,
) -> str: ...
```

Hashes use existing canonical JSON + `namespaced_sha256`, exclude timestamps/row versions, and exist only to protect immutable semantic comparison/reuse.

### 2.4 Deterministic verifier

`proposal_verifier.py`:

```python
from dataclasses import dataclass
from typing import Literal

VerificationSeverity = Literal["blocking", "warning"]
VERIFIER_PROFILE = "slice1-v1"

@dataclass(frozen=True, slots=True)
class VerificationFinding:
    code: str
    severity: VerificationSeverity
    field: str | None
    message: str

@dataclass(frozen=True, slots=True)
class ProposalVerificationResult:
    profile: str
    passed: bool
    findings: tuple[VerificationFinding, ...]
    result_hash: str

class ProposalVerifier:
    def __init__(self, repository_facts: ProposalRepositoryFactPort) -> None: ...
    async def verify(
        self,
        *,
        project_id: UUID,
        payload: ProposalPayload,
    ) -> ProposalVerificationResult: ...
```

`slice1-v1` minimum rules are frozen:

1. The bound RepositorySnapshot exists, belongs to the expected `project_id`, has stored `is_clean=True`, and its immutable identity equals `payload.repository_snapshot_identity`. This rule is Snapshot-semantic only: it does **not** call live approval readiness or `GitAdapter.probe()`.
2. `train_entrypoint` exists and is a regular file in the bound Snapshot.
3. `eval_entrypoint`, when provided, exists and is regular.
4. `workdir` is a safe repository-relative directory semantic (`.` allowed only as the explicit repository root value; no `..`, absolute path, NUL or backslash escape) and exists in the bound Snapshot (`.` means repository root).
5. `seeds` contains 1–20 unique non-negative integers.
6. Parameter matrix has 0–16 keys, each key non-blank/restricted UTF-8, each value list 1–20; expanded Trial count is 1–20.
7. `parameter_sweep` requires expanded Trial count > 1. `comparison` and `ablation` require > 1 planned Trial. `stability` requires at least 2 distinct seeds.
8. DatasetReference is `strict` only when `stable_identity` is present; missing stable identity requires `best_effort` and produces warning `dataset_best_effort`.
9. Artifact names are unique; required artifact paths are safe relative paths.
10. Metric names are unique and every Metric references a declared Artifact by name.
11. Every required Artifact has `artifact_exists` validation coverage.
12. Every Metric has `metric_finite` validation coverage.
13. `timeout_seconds` is 1..86400; `max_retries` is 0..3.
14. Findings are sorted deterministically by `(severity, code, field or "", message)` before result hashing.
15. Any blocking finding makes `passed=False`; warnings alone do not.

No rule may rely on LLM judgment. No Verification finding/result may depend on mutable live Repository currentness; currentness is rechecked at Approval.

### 2.5 Proposal service

`proposal_service.py` public methods:

```python
class ProposalApplicationService:
    async def create_manual_revision(
        self,
        *,
        context: CommandContext,
        experiment_id: UUID,
        payload: ProposalPayload,
    ) -> ProposalRevisionDetails: ...

    async def create_agent_revision(
        self,
        *,
        context: CommandContext,
        project_id: UUID,
        origin_agent_step_id: UUID,
        payload: ProposalPayload,
    ) -> ProposalRevisionDetails: ...

    async def get_revision(self, proposal_revision_id: UUID) -> ProposalRevisionDetails: ...

    async def verify_revision(
        self,
        *,
        context: CommandContext,
        proposal_revision_id: UUID,
    ) -> ProposalVerificationDetails: ...

    async def approve_revision(
        self,
        *,
        context: CommandContext,
        proposal_revision_id: UUID,
        verification_id: UUID,
    ) -> ExperimentVersionDetails: ...

    async def reject_revision(
        self,
        *,
        context: CommandContext,
        proposal_revision_id: UUID,
        verification_id: UUID | None,
        reason: str,
    ) -> DesignDecisionDetails: ...
```

Rules:

- `create_agent_revision` uses unique `origin_agent_step_id` as the replay anchor. Same step + same payload returns existing revision; same step + different payload is invariant violation.
- `create_agent_revision` creates the Experiment container on first materialization for that Agent proposal and binds it to the same Project as the AgentRun/Snapshot.
- A new manual revision increments `revision_number` under an Experiment row lock.
- ProposalRevision rows are immutable; editing creates a new revision.
- `verify_revision` performs only deterministic checks over immutable Proposal + bound Snapshot semantics outside UoW, then inserts/reuses an immutable Verification in a short UoW. Same revision + same verifier profile + same payload hash reuses existing Verification. Live repository currentness is deliberately excluded from this cache key and result.
- `approve_revision` first loads the immutable revision/verification identifiers, then calls `approval_source_state(...)` **outside** the write UoW immediately before authorization. `ready=False` rejects approval without creating a DesignDecision/Version; restoring the live repository later allows the same immutable passed Verification to be used after a new readiness check.
- After the live readiness check succeeds, `approve_revision` opens one short write UoW and:
  - reloads immutable revision + verification and requires passed/hash-exact bindings;
  - locks the owning `Experiment` row with `SELECT ... FOR UPDATE`;
  - after acquiring that lock, rechecks whether this revision already has a DesignDecision/Version so a same-request retry returns the existing Version;
  - allocates `version_number = current max version_number for the Experiment + 1` while holding the Experiment lock;
  - inserts the `approved` DesignDecision and immutable ExperimentVersion atomically;
  - relies on `UNIQUE(experiment_id, version_number)` and `UNIQUE(proposal_revision_id)` as database backstops, not as the primary allocation protocol.
- Repeating the same approve after commit returns the existing Version. A contradictory decision or mismatched actor/request returns conflict; no new retry protocol.
- `reject_revision` inserts an immutable rejected DesignDecision, no Version.
- Approval/Version does not use Outbox by default. Durable audit is the DesignDecision + Version domain row.

### 2.6 Tool interfaces

`tools_policy/types.py`:

```python
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, Mapping

ToolRisk = Literal["green", "yellow", "red"]
ToolSideEffect = Literal["read_only", "side_effecting"]
ToolExecutor = Callable[[Mapping[str, object]], Awaitable[Mapping[str, object]]]

@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    version: str
    risk: ToolRisk
    side_effect: ToolSideEffect
    input_schema: Mapping[str, object]
    approval_required: bool
    executor: ToolExecutor
```

Production registry contains exactly these Slice 1 Repository tools:

```text
repository.list_tree:v1
repository.read_text:v1
repository.search_text:v1
repository.path_fact:v1
```

All four are `green`, `read_only`, `approval_required=False` and are backed by the bound `RepositorySnapshotSourceService`; no tool accepts an arbitrary host path or repository root. Production limits are fixed at: list ≤500 entries; one read ≤64 KiB and ≤500 lines; search ≤100 hits and scans only files ≤256 KiB per file.

`ToolApplicationService` methods:

```python
async def request_call(..., definition_name: str, arguments: Mapping[str, object]) -> ToolCallDetails: ...
async def approve_call(..., tool_call_id: UUID, exact_arguments_hash: str) -> ToolApprovalDetails: ...
async def reject_call(..., tool_call_id: UUID, exact_arguments_hash: str) -> ToolApprovalDetails: ...
async def execute_call(..., tool_call_id: UUID) -> ToolCallDetails: ...
```

- Arguments are schema-validated then canonicalized before persistence.
- A production read-only Repository tool is safe to retry but still has one persisted ToolCall/result for traceability.
- `execute_call` runs the executor outside UoW and stores result/error in a second short UoW.
- Generic approval behavior is proven with a synthetic test Tool only; no production side-effecting Tool is introduced.
- For an approval-required Tool, approval binds exact `arguments_hash`; changed args require a new ToolCall.

### 2.7 Agent/provider interfaces

`agent_runtime/provider.py` keeps the product boundary provider-neutral:

```python
from dataclasses import dataclass
from typing import Literal, Mapping, Protocol

ProviderEgress = Literal["local", "remote"]

@dataclass(frozen=True, slots=True)
class ModelRequest:
    model: str
    messages: tuple[Mapping[str, object], ...]
    semantic_parameters: Mapping[str, object]
    output_schema: Mapping[str, object]
    bound_toolset_hash: str

@dataclass(frozen=True, slots=True)
class ModelResponse:
    payload: Mapping[str, object]
    usage: Mapping[str, object]

class ModelProvider(Protocol):
    provider_name: str
    egress: ProviderEgress
    async def complete(self, request: ModelRequest) -> ModelResponse: ...
```

`agent_runtime/opencode_go.py` is the **only production remote adapter in Slice 1**. It is an adapter for the user's OpenCode Go API entitlement, not for OpenAI API credentials and not for ChatGPT/Codex workspace quota.

Frozen wire contract:

```text
provider_name = "opencode_go_chat"
egress = "remote"
HTTP method = POST
endpoint = https://opencode.ai/zen/go/v1/chat/completions
Authorization = Bearer <LABPILOT_AGENT_PROVIDER_API_KEY>
Content-Type = application/json
streaming = disabled in Slice 1
```

Request body is exactly the minimal chat-completions subset LabPilot needs:

```json
{
  "model": "<configured raw OpenCode Go model id>",
  "messages": [
    {"role": "system", "content": "<bounded UTF-8 text>"},
    {"role": "user", "content": "<bounded UTF-8 text>"}
  ]
}
```

Each message object contains **only** `role` and `content`. Slice 1 permits roles `system`, `user`, and `assistant`; Tool observations are rendered by `ContextCompiler` into bounded LabPilot-owned message text rather than provider-native tool/function-call fields.

Rules:

- `agent_provider_model` stores the raw OpenCode Go model id used in the HTTP `model` field. Do not prefix it with `opencode-go/`; that prefix belongs to OpenCode client configuration, not this direct API request.
- Slice 1 intentionally supports only models OpenCode Go currently serves through `/v1/chat/completions`. A model whose Go endpoint is `/v1/responses` or `/v1/messages` is rejected operationally/out of scope; do not add a second protocol adapter in this Slice.
- The production OpenCode Go adapter accepts `semantic_parameters == {}` in Slice 1. Non-empty provider-specific parameters are rejected as unsupported rather than forwarded ambiguously. The persisted empty mapping still satisfies the audit contract that all semantics-affecting parameters are explicit.
- LabPilot does **not** send provider-native `tools`, `response_format`, Conversation/session identifiers, or provider-side state. Repository Tool schemas and the exact AgentDecision schema are rendered by `ContextCompiler` into bounded messages; LabPilot remains the Tool/Proposal authority.
- Response extraction is exactly `choices[0].message.content`; it must be a string containing one JSON object. JSON decoding happens in the adapter, then domain validation against the exact `AgentDecision` union happens before persistence/execution.
- Top-level `usage`, when present and a JSON object, becomes `ModelResponse.usage`; absence is `{}`.
- Non-2xx, timeout, oversized response, malformed provider envelope, non-string content, or non-object JSON produce bounded typed error codes (`provider_http_error`, `provider_timeout`, `provider_response_too_large`, `provider_malformed_response`). Do not persist/log raw response bodies for these failures.
- Use `httpx.AsyncClient`; create the Authorization header only at the call boundary. The API key must never appear in exception text, structured logs, RuntimeEvent, AgentStep, ToolResult, or API response.
- Do not add OpenAI/Anthropic SDK dependencies in Slice 1. `httpx` is already present.
- Do not read OpenCode CLI/TUI auth files. LabPilot receives its runtime key only through its own secret setting/environment boundary.

Settings used by this adapter:

```python
agent_provider_model: str | None = None
agent_provider_api_key: SecretStr | None = None
agent_provider_timeout_seconds: int = Field(default=60, ge=1, le=300)
agent_provider_max_response_bytes: int = Field(default=1024 * 1024, ge=1024)
```

When model/key are not both configured, application composition uses `UnconfiguredModelProvider`; startup never makes a network request.

#### Exact AgentDecision union

`agent_runtime/types.py` freezes a discriminated union. Pydantic validation uses `extra="forbid"`; all strings are valid UTF-8 and have the bounded sizes below.

```text
ToolCallDecision
- kind = "tool_call"
- tool_name: non-blank, <= 100 UTF-8 bytes
- arguments: JSON object
- summary: optional, <= 1000 UTF-8 bytes

HumanInputDecision
- kind = "request_human_input"
- question: non-blank, <= 4000 UTF-8 bytes
- summary: optional, <= 1000 UTF-8 bytes

ProposeDecision
- kind = "propose"
- proposal: exact ProposalPayload
- summary: optional, <= 1000 UTF-8 bytes

StopDecision
- kind = "stop"
- reason_code: [a-z][a-z0-9_]{0,99}
- summary: optional, <= 1000 UTF-8 bytes
```

No variant accepts `rationale`, `reasoning`, `chain_of_thought`, arbitrary extra fields, or a second action in the same response. `TypeAdapter(AgentDecision).json_schema()` (or an exactly equivalent deterministic schema) is canonicalized to produce `output_schema_hash`; this schema is also rendered into the model instruction.

#### Bound Tool contract identity

`tools_policy/types.py` exposes:

```python
def bound_toolset_hash(definitions: tuple[ToolDefinition, ...]) -> str: ...
```

The canonical payload sorts definitions by `(name, version)` and contains only:

```text
name
version
risk
side_effect
approval_required
canonical input_schema
```

The executor callable is excluded. Hash with the existing canonical JSON + `namespaced_sha256`. Same ordered semantics produce the same hash; any Tool version or input-schema semantic change changes the hash. This is an owner-local audit identity, **not** a ToolManifest aggregate/table.

`ContextCompiler` includes the exact bounded Tool definitions in the model messages and passes the computed `bound_toolset_hash` in `ModelRequest`. `canonical_request_hash` covers the complete immutable request semantics, including provider/model, messages, semantic parameters, `output_schema_hash`, and `bound_toolset_hash`.

`AgentApplicationService` public methods remain:

```python
async def create_run(
    self,
    *,
    context: CommandContext,
    project_id: UUID,
    repository_snapshot_id: UUID,
    goal: str,
    budget: AgentBudget,
) -> AgentRunDetails: ...

async def advance_run(
    self,
    *,
    context: CommandContext,
    agent_run_id: UUID,
    expected_row_version: int,
) -> AgentRunDetails: ...

async def submit_human_input(
    self,
    *,
    context: CommandContext,
    agent_run_id: UUID,
    expected_row_version: int,
    text: str,
) -> AgentRunDetails: ...

async def get_run(self, agent_run_id: UUID) -> AgentRunDetails: ...
```

`AgentBudget` fields:

```text
max_steps: 1..20
max_model_calls: 1..20
max_wall_clock_seconds: 1..600
max_tokens: 1..200000
```

Run state:

```text
ready
running
awaiting_human
completed
budget_exhausted
failed
```

Termination reasons are explicit and immutable once terminal.

Agent transaction protocol:

1. Read/compile bounded context outside UoW.
2. Short UoW locks AgentRun, verifies `expected_row_version`, budget, and state; reserves next AgentStep with immutable `canonical_request_hash`, `bound_toolset_hash`, and `output_schema_hash`, increments run row version, and commits.
3. Provider call occurs outside UoW. Slice 1 does not automatically retry a provider call whose outcome is unknown; an interrupted reserved step is failed closed as `model_call_outcome_unknown` on the next advance.
4. Short UoW finalizes step outcome/usage/validated decision and updates counters/state.
5. If decision is `tool_call`, persist ToolCall; execute read-only Tool outside UoW; persist ToolResult; then continue with a new model step while budget remains.
6. If decision is `request_human_input`, set `awaiting_human` and return.
7. If decision is `propose`, persist the model step first, then call `ProposalApplicationService.create_agent_revision(origin_agent_step_id=...)`; unique origin step makes crash/retry materialization safe; finally bind `produced_proposal_revision_id` and terminal `completed` to the run.
8. If decision is `stop`, terminal `completed` with explicit stop reason and no Proposal.
9. Provider/tool failures finalize the reserved step and mark run `failed`; they do not leave a DB transaction open.

Egress gate:

- `ModelProvider.egress == "remote"` and effective policy `local_only` => fail before compiling/sending repository source content and before provider invocation.
- The effective policy is the immutable value stored on AgentRun at creation from installation/project configuration; Slice 1 uses installation-level `Settings.data_egress_policy` only, default `local_only`.
- Project-level override is not added until a real requirement appears.

### 2.8 Slice 1 structured operational logging contract

Slice 1 does **not** create a logging/telemetry platform or new persistence table. New Slice 1 operational log calls use the standard Python logging path and structured key/value fields only.

Applicable correlation contract:

```text
request_id  = existing CommandContext.interaction_correlation_id when a command context exists
project_id  = include when known
agent_run_id = include for Agent lifecycle/model-call logs
tool_call_id = include for Tool lifecycle/execution logs
```

Do not create a second request-correlation identity solely for logging. Background/non-request work may omit `request_id` when no CommandContext exists.

Allowed structured values are bounded scalar identifiers/statuses only, for example:

```text
event name
correlation/domain IDs
status
bounded reason/error code
provider_name / model_name
canonical_request_hash / bound_toolset_hash / output_schema_hash
```

Never log:

```text
provider API key or Authorization header
raw Prompt/messages
raw repository/source content
raw ToolResult/result_json
human secret input
full or sensitive environment
unredacted remote response body
```

Provider/Tool/API tests must use a sentinel secret and captured log records to prove the sentinel is absent from message text and structured fields. This is a contract/test rule, not an OTel/Grafana/structured-logging framework requirement.

---

## 3. Persistence schema frozen by this plan

### 3.1 Migration `0007_experiment_proposals.py`

Create exactly five tables.

#### `experiments`

```text
experiment_id UUID PK
project_id UUID NOT NULL FK projects(project_id) ON DELETE RESTRICT
created_at timestamptz NOT NULL default clock_timestamp()
```

No generic lifecycle/status table is added.

#### `experiment_proposal_revisions`

```text
proposal_revision_id UUID PK
experiment_id UUID NOT NULL FK experiments(experiment_id) ON DELETE RESTRICT
revision_number bigint NOT NULL CHECK > 0
origin_agent_step_id UUID NULL
repository_snapshot_id UUID NOT NULL
repository_snapshot_identity varchar(64) NOT NULL
payload_json jsonb NOT NULL
payload_hash varchar(64) NOT NULL
created_by_actor_type varchar(30) NOT NULL
created_by_actor_id varchar(200) NOT NULL
created_at timestamptz NOT NULL
UNIQUE(experiment_id, revision_number)
UNIQUE(origin_agent_step_id) WHERE origin_agent_step_id IS NOT NULL
```

`repository_snapshot_id` deliberately has no DB FK to context tables: experiments owns the Proposal authority and validates the referenced Snapshot through the `ProposalRepositoryFactPort`, avoiding a reverse Python/module dependency and a circular migration contract. Snapshot rows are already immutable/restricted Accepted Foundation records.

#### `experiment_proposal_verifications`

```text
proposal_verification_id UUID PK
proposal_revision_id UUID NOT NULL FK experiment_proposal_revisions ON DELETE RESTRICT
verifier_profile varchar(100) NOT NULL
proposal_payload_hash varchar(64) NOT NULL
result_json jsonb NOT NULL
result_hash varchar(64) NOT NULL
passed boolean NOT NULL
created_at timestamptz NOT NULL
UNIQUE(proposal_revision_id, verifier_profile, proposal_payload_hash)
```

#### `experiment_design_decisions`

```text
design_decision_id UUID PK
proposal_revision_id UUID NOT NULL FK experiment_proposal_revisions ON DELETE RESTRICT
proposal_verification_id UUID NULL FK experiment_proposal_verifications ON DELETE RESTRICT
decision varchar(20) NOT NULL CHECK IN ('approved','rejected')
proposal_payload_hash varchar(64) NOT NULL
repository_snapshot_id UUID NOT NULL
repository_snapshot_identity varchar(64) NOT NULL
dataset_reference_hash varchar(64) NOT NULL
environment_expectation_hash varchar(64) NOT NULL
actor_type varchar(30) NOT NULL
actor_id varchar(200) NOT NULL
decided_at timestamptz NOT NULL
reason text NULL
UNIQUE(proposal_revision_id)
```

Approval requires non-null verification; rejection may use null verification only when the user rejects without requesting verification.

#### `experiment_versions`

```text
experiment_version_id UUID PK
experiment_id UUID NOT NULL FK experiments ON DELETE RESTRICT
design_decision_id UUID NOT NULL UNIQUE FK experiment_design_decisions ON DELETE RESTRICT
version_number bigint NOT NULL CHECK > 0
experiment_version_identity varchar(64) NOT NULL UNIQUE
proposal_revision_id UUID NOT NULL FK experiment_proposal_revisions ON DELETE RESTRICT
proposal_payload_hash varchar(64) NOT NULL
repository_snapshot_id UUID NOT NULL
repository_snapshot_identity varchar(64) NOT NULL
dataset_reference_json jsonb NOT NULL
dataset_reference_hash varchar(64) NOT NULL
environment_expectation_json jsonb NOT NULL
environment_expectation_hash varchar(64) NOT NULL
validation_protocol_json jsonb NOT NULL
validation_protocol_hash varchar(64) NOT NULL
created_at timestamptz NOT NULL
UNIQUE(experiment_id, version_number)
```

No update service exists for ExperimentVersion.

### 3.2 Migration `0008_agent_tool_runtime.py`

Create Tool tables before Agent tables.

#### `tool_calls`

```text
tool_call_id UUID PK
project_id UUID NOT NULL FK projects ON DELETE RESTRICT
tool_name varchar(100) NOT NULL
tool_version varchar(50) NOT NULL
risk_level varchar(20) NOT NULL CHECK IN ('green','yellow','red')
side_effect_class varchar(20) NOT NULL CHECK IN ('read_only','side_effecting')
arguments_json jsonb NOT NULL
arguments_hash varchar(64) NOT NULL
status varchar(20) NOT NULL CHECK IN ('requested','executed','failed','denied')
result_json jsonb NULL
result_hash varchar(64) NULL
error_code varchar(100) NULL
requested_at timestamptz NOT NULL
completed_at timestamptz NULL
```

#### `tool_approvals`

```text
tool_approval_id UUID PK
tool_call_id UUID NOT NULL UNIQUE FK tool_calls ON DELETE RESTRICT
decision varchar(20) NOT NULL CHECK IN ('approved','rejected')
arguments_hash varchar(64) NOT NULL
actor_type varchar(30) NOT NULL
actor_id varchar(200) NOT NULL
decided_at timestamptz NOT NULL
expires_at timestamptz NULL
```

#### `agent_runs`

```text
agent_run_id UUID PK
project_id UUID NOT NULL FK projects ON DELETE RESTRICT
repository_snapshot_id UUID NOT NULL
goal text NOT NULL
status varchar(30) NOT NULL CHECK IN ('ready','running','awaiting_human','completed','budget_exhausted','failed')
termination_reason varchar(100) NULL
egress_policy varchar(30) NOT NULL CHECK IN ('local_only','remote_provider_allowed')
provider_name varchar(100) NOT NULL
model_name varchar(200) NOT NULL
max_steps integer NOT NULL
max_model_calls integer NOT NULL
max_wall_clock_seconds integer NOT NULL
max_tokens integer NOT NULL
total_model_calls integer NOT NULL default 0
total_tokens bigint NOT NULL default 0
next_step_ordinal integer NOT NULL default 1
produced_proposal_revision_id UUID NULL FK experiment_proposal_revisions ON DELETE RESTRICT
created_at timestamptz NOT NULL
started_at timestamptz NULL
completed_at timestamptz NULL
row_version bigint NOT NULL default 1 CHECK > 0
```

`repository_snapshot_id` has no DB FK for the same boundary reason as Proposal; creation validates it through the context facade.

#### `agent_steps`

```text
agent_step_id UUID PK
agent_run_id UUID NOT NULL FK agent_runs ON DELETE RESTRICT
ordinal integer NOT NULL CHECK > 0
step_kind varchar(30) NOT NULL CHECK IN ('model_call','human_input')
tool_call_id UUID NULL FK tool_calls ON DELETE RESTRICT
provider_name varchar(100) NULL
model_name varchar(200) NULL
canonical_request_hash varchar(64) NULL
bound_toolset_hash varchar(64) NULL
semantic_parameters_json jsonb NULL
output_schema_hash varchar(64) NULL
started_at timestamptz NOT NULL
completed_at timestamptz NULL
outcome varchar(30) NOT NULL CHECK IN ('reserved','succeeded','failed')
token_usage_json jsonb NULL
decision_kind varchar(30) NULL
decision_json jsonb NULL
human_input_text text NULL
UNIQUE(agent_run_id, ordinal)
```

For `step_kind='model_call'`, `canonical_request_hash`, `bound_toolset_hash`, `semantic_parameters_json`, and `output_schema_hash` are all required by service invariants before the provider call is made. `bound_toolset_hash` is stored directly on the owner AgentStep; no separate manifest table exists.

No Lease, WakeupJob, ModelRequestSnapshot, DetachedResponse, dynamic Tool Manifest, or generic retry tables.

---

## 4. Transaction / UoW ownership matrix

| Operation | External work outside UoW | Short UoW authoritative work | Idempotency |
|---|---|---|---|
| S1.0 config alignment | none | none | n/a |
| Repository `list/read/search` | Git/CAS reads | none | read-only |
| Approval readiness | `GitAdapter.probe()` | DB read session only; no write | read-only |
| Create ProposalRevision | none | lock Experiment for revision number; insert immutable revision | unique `(experiment, revision)`; agent origin unique |
| Verify Proposal | immutable Snapshot/path fact checks only; **no live readiness probe** | insert/reuse immutable verification | unique `(revision, profile, payload_hash)` |
| Reject Proposal | none | insert immutable decision | unique `proposal_revision_id` |
| Approve + Version | live `approval_source_state` check immediately before write | lock Experiment; recheck existing decision; allocate next version number; insert Decision + Version atomically | Experiment row lock + unique decision/version constraints; same retry returns existing |
| Request ToolCall | none | insert canonical call | one persisted call per Agent decision |
| Execute read-only Tool | executor outside transaction | transition requested → executed/failed | read-only retry safe; same call persisted once |
| Create AgentRun | repository descriptor read | insert run | Not required: cheap DB create, no model/tool side effect occurs until `advance` |
| Reserve model step | context compile before reservation | lock run/CAS version; insert step with request hash | unique `(run, ordinal)` |
| Model call | HTTP outside transaction | finalize reserved step/counters/decision | retry only when request hash identical |
| Agent proposal materialization | none | Proposal service inserts/reuses by `origin_agent_step_id` | unique origin step |
| Human input | none | lock run; insert human step; update run | expected row_version + unique ordinal |

Slice 1 introduces **no new mandatory CommandReceipt use**. AgentRun creation is a cheap pure-DB create and does not invoke a model; `advance_run` uses row-version reservation to prevent concurrent duplicate model calls. Proposal editing/verify/approve/reject use domain uniqueness/CAS semantics. Existing historical commands keep their existing Receipt contracts unchanged.

---

# Task 1: S1.0 Foundation Alignment

**Model:** GPT-5.6 Terra  
**Reasoning effort:** Medium  
**Risk tier:** Routine with narrow Boundary-sensitive config checks  
**Review:** One focused implementation review; no independent deep review unless a config/security regression appears.

**Files:**
- Modify: `scripts/check_ci.sh`
- Modify: `compose.yaml`
- Modify: `.env.example`
- Modify: `src/labpilot/settings.py`
- Modify: `tests/unit/test_settings.py`
- Test existing: `tests/unit/api/test_health.py`
- Test existing: `tests/integration/api/test_readiness.py`
- Test existing: `tests/unit/observability/test_projection_worker.py`

**Interfaces:**
- Consumes: R0 §14 technical alignment and this plan §2.8 logging contract.
- Produces: lock/project consistency check, loopback default API exposure, optional Projection profile, immutable installation-level egress setting.
- Does **not** refresh FastAPI in Slice 1 because this Slice creates no SSE endpoint. The SSE-capable FastAPI refresh remains JIT work for the first Slice that actually implements SSE.

- [ ] **Step 1: Add failing Settings tests for egress default and accepted values**

Add tests equivalent to:

```python
def test_data_egress_policy_defaults_local_only(base_settings_env: dict[str, str]) -> None:
    settings = Settings(_env_file=None, **base_settings_env)
    assert settings.data_egress_policy == "local_only"


def test_data_egress_policy_accepts_remote_provider_allowed(
    base_settings_env: dict[str, str],
) -> None:
    settings = Settings(
        _env_file=None,
        **base_settings_env,
        data_egress_policy="remote_provider_allowed",
    )
    assert settings.data_egress_policy == "remote_provider_allowed"
```

Also assert any other value is rejected by Pydantic.

- [ ] **Step 2: Run Settings tests and confirm RED**

```bash
uv run pytest tests/unit/test_settings.py -q
```

Expected: failure because `data_egress_policy` does not exist.

- [ ] **Step 3: Add the minimal setting**

In `Settings` add:

```python
data_egress_policy: Literal["local_only", "remote_provider_allowed"] = "local_only"
```

Add `.env.example`:

```text
LABPILOT_DATA_EGRESS_POLICY=local_only
```

Do not add project-level DLP/RBAC fields.

- [ ] **Step 4: Align Compose topology and API exposure**

Change API port publication to:

```yaml
ports:
  - "127.0.0.1:${LABPILOT_API_PORT:-8000}:8000"
```

Keep the `projection` service code but move it behind an explicit non-default profile:

```yaml
projection:
  profiles: ["advanced-ops"]
```

Do not delete projection modules/tests.

- [ ] **Step 5: Make CI prove lock/project consistency without changing dependencies**

Change `scripts/check_ci.sh` install section to:

```bash
uv lock --check
uv sync --locked --all-groups
```

Do not modify `pyproject.toml` or `uv.lock` in this task. Do not remove historical WP0/WP1-A dependency verification.

- [ ] **Step 6: Freeze the S1 structured-log contract at implementation boundary**

No new logging library or observability service is introduced. During Tasks 4–6, any new operational log call must obey §2.8. Task 1 only establishes the rule and confirms the existing `CommandContext.interaction_correlation_id` is the `request_id` source for command logs; do not invent a second request-id subsystem.

- [ ] **Step 7: Run focused regression**

```bash
uv lock --check
uv sync --locked --all-groups
uv run pytest tests/unit/test_settings.py tests/unit/api/test_health.py -q
uv run pytest tests/integration/api/test_readiness.py -q -m integration
uv run ruff check src tests scripts
uv run mypy src scripts
```

Expected: all PASS.

- [ ] **Step 8: Verify Compose behavior mechanically**

```bash
docker compose config
```

Confirm rendered API host binding starts with `127.0.0.1:` and the Projection service is not selected by the default profile.

- [ ] **Step 9: Focused review and commit**

After the one focused review passes:

```bash
git add scripts/check_ci.sh compose.yaml .env.example \
  src/labpilot/settings.py tests/unit/test_settings.py
git commit -m "chore: align V1 core foundation"
```

Do not run the full Slice CI gate solely for this task.

---

# Task 2: S1.1 Minimal Repository Fact / Source Facade

**Model:** GPT-5.6 Terra  
**Reasoning effort:** High  
**Risk tier:** Boundary-sensitive; filesystem/path portions Safety-sensitive  
**Review:** One deep independent review. Any remediation receives finding-specific re-review only.

**Files:**
- Create: `src/labpilot/context/repository_source.py`
- Create: `src/labpilot/context/proposal_repository_adapter.py`
- Modify: `src/labpilot/context/repository_repository.py`
- Create: `tests/unit/context/test_repository_source.py`
- Create: `tests/integration/context/test_repository_source.py`
- Regression: `tests/unit/context/test_git_adapter.py`
- Regression: `tests/integration/context/test_repository_snapshot.py`
- Regression: `tests/integration/context/test_repository_freshness.py`

**Interfaces:**
- Consumes: existing `GitAdapter`, `ContentAddressedStore`, Snapshot/Bundle rows.
- Produces: exact §2.1 Repository facade and §2.2 Proposal repository fact adapter.

- [ ] **Step 1: Write facade unit tests against fake Git/CAS/repository collaborators**

Cover exact behavior:

```text
clean base tree list/read
descriptor exposes owning project_id
directory_exists handles repository root and path prefixes
tracked overlay replaces base bytes
untracked file appears
deletion disappears
mode_change preserves base bytes and new mode
symlink returns stored link-target text, not target file bytes
submodule is metadata-only and read_text rejects it
non-UTF8 content fails closed
absolute / ../ / backslash unsafe paths reject
prefix/max_entries/max_results/max_file_bytes are enforced
```

The fake tests must assert no method ever reads a live worktree file path directly.

- [ ] **Step 2: Run unit tests and confirm RED**

```bash
uv run pytest tests/unit/context/test_repository_source.py -q
```

Expected: import/module failures.

- [ ] **Step 3: Add only the read queries needed by the facade**

Add to `RepositorySnapshotRepository`:

```python
async def get_snapshot(
    self,
    session: AsyncSession,
    repository_snapshot_id: UUID,
) -> RepositorySnapshotRow | None: ...

async def get_freshness(
    self,
    session: AsyncSession,
    repository_snapshot_id: UUID,
) -> RepositorySnapshotFreshnessRow | None: ...
```

Do not add mutation/freshness-transition methods.

- [ ] **Step 4: Implement Snapshot tree reconstruction**

Implement the types/signatures in §2.1.

Algorithm for `_snapshot_entries(snapshot_id)`:

```text
load Snapshot + Registration
ls_tree(snapshot.head_commit)
map base tree by relative_path
if Bundle exists:
    for entry ordered stable_ordinal:
        deletion        -> remove path
        mode_change     -> replace file_mode only
        tracked_overlay -> replace metadata and content source
        untracked_file  -> add metadata and content source
sort final paths by UTF-8 bytes
```

Never expose bundle/entry identity in the public return type.

- [ ] **Step 5: Implement bounded source reads**

For base regular/symlink entries use `GitAdapter.cat_file_batch` by stored object OID. For dirty/untracked regular/symlink entries use `ContentAddressedStore.read_bytes(content_ref)`. Verify expected persisted content hash before returning dirty-overlay content.

Use strict UTF-8 decoding and 1-based line slicing. Reject `start_line < 1`, `end_line < start_line`, and requested ranges above a small fixed maximum of 500 lines.

- [ ] **Step 6: Write approval-readiness integration tests first**

Use a real temporary Git repository and real PostgreSQL Snapshot rows to prove:

```text
clean unchanged snapshot => ready
snapshot marked dirty => snapshot_dirty
registration revoked/unavailable => registration_not_active
edit tracked file after clean snapshot => repository_changed
add untracked file after clean snapshot => repository_changed
stage file after clean snapshot => repository_changed
switch branch/HEAD => repository_changed
repository path disappears => repository_unavailable
```

Also assert `repository_snapshot_freshness` row is byte-for-byte logically unchanged by the check (same status/reason/probe/checked_at/row_version).

- [ ] **Step 7: Run integration test and confirm RED**

```bash
uv run pytest tests/integration/context/test_repository_source.py -q -m integration
```

- [ ] **Step 8: Implement approval readiness using existing `probe()` only**

Do not call old Task 11 transition logic and do not persist currentness.

For a clean Snapshot compare:

```text
canonical_root_path
head_commit
tracked_tree_identity
current_branch
is_detached_head
probe.status_entries == ()
```

Return typed readiness instead of mutating Snapshot/Freshness.

- [ ] **Step 9: Implement the experiments adapter**

`context/proposal_repository_adapter.py` imports `experiments.proposal_ports` and maps the context return types into `ApprovalSourceState` / `ProposalPathFact`. This direction is allowed; the reverse import is forbidden.

- [ ] **Step 10: Run focused regression**

```bash
uv run pytest tests/unit/context/test_repository_source.py tests/unit/context/test_git_adapter.py -q
uv run pytest \
  tests/integration/context/test_repository_source.py \
  tests/integration/context/test_repository_snapshot.py \
  tests/integration/context/test_repository_freshness.py \
  -q -m integration
uv run ruff check src/labpilot/context tests/unit/context tests/integration/context
uv run mypy src/labpilot/context
```

- [ ] **Step 11: Deep review and commit**

Review scope is only the Task 2 diff plus direct regressions in accepted Git/Snapshot semantics. Do not reopen Task 10 unrelated findings.

After APPROVE:

```bash
git add src/labpilot/context/repository_source.py \
  src/labpilot/context/proposal_repository_adapter.py \
  src/labpilot/context/repository_repository.py \
  tests/unit/context/test_repository_source.py \
  tests/integration/context/test_repository_source.py
git commit -m "feat: add repository source facade"
```

---

# Task 3: S1.3 Proposal / Verifier / Approval / Immutable Version Domain

**Model:** GPT-5.6 Sol  
**Reasoning effort:** High  
**Risk tier:** Boundary-sensitive (migration, immutable semantic identity, approval authority)  
**Review:** One deep independent review; finding-specific remediation only.

**Files:**
- Create: `src/labpilot/experiments/proposal_types.py`
- Create: `src/labpilot/experiments/proposal_ports.py`
- Create: `src/labpilot/experiments/proposal_models.py`
- Create: `src/labpilot/experiments/proposal_repository.py`
- Create: `src/labpilot/experiments/proposal_verifier.py`
- Create: `src/labpilot/experiments/proposal_service.py`
- Create: `migrations/versions/0007_experiment_proposals.py`
- Modify: `src/labpilot/db/model_registry.py`
- Modify: `tests/architecture/dependency_rules.py`
- Modify: `tests/architecture/test_model_registry.py`
- Create: `tests/unit/experiments/test_proposal_types.py`
- Create: `tests/unit/experiments/test_proposal_verifier.py`
- Create: `tests/integration/experiments/test_proposal_service.py`
- Regression: `tests/integration/db/test_migrations.py`
- Regression: `tests/integration/experiments/test_project_service.py`

**Interfaces:**
- Consumes: §2.2 repository fact Protocol only; no `experiments -> context` import.
- Produces: Proposal domain interfaces in §2.3–2.5 and migration `0007`.

- [ ] **Step 1: Write value-object/hash unit tests**

Tests must prove:

```text
canonical payload hash independent of dict insertion order
hash excludes timestamps
semantic Dataset/Environment/Validation change changes Version identity
same semantics produce same hash
unsafe artifact/workdir paths rejected
strict DatasetReference without stable_identity rejected
best_effort without stable_identity accepted
```

- [ ] **Step 2: Run and confirm RED**

```bash
uv run pytest tests/unit/experiments/test_proposal_types.py -q
```

- [ ] **Step 3: Implement exact §2.3 value objects and canonical hash functions**

Use existing `canonical_json_bytes`/`namespaced_sha256`; do not add a new generic identity module.

- [ ] **Step 4: Write deterministic verifier unit tests using a fake `ProposalRepositoryFactPort`**

One test per frozen rule in §2.4, including:

```text
Agent-provided entrypoint string cannot pass when the fact port says the path is missing
stored dirty Snapshot fails the immutable clean-source rule
fake approval_source_state raises if called, proving the verifier never performs live readiness/currentness I/O
```


Add deterministic-order/hash test:

```python
assert verifier_result_a.findings == verifier_result_b.findings
assert verifier_result_a.result_hash == verifier_result_b.result_hash
```

for semantically identical inputs with different mapping insertion order.

- [ ] **Step 5: Run verifier tests and confirm RED**

```bash
uv run pytest tests/unit/experiments/test_proposal_verifier.py -q
```

- [ ] **Step 6: Implement the verifier profile `slice1-v1`**

No LLM/provider import is allowed in `proposal_verifier.py`.

- [ ] **Step 7: Write migration/service integration tests before migration**

Use a fresh PostgreSQL DB and prove:

```text
migration 0007 creates exactly five new tables
proposal revision immutable-by-service; edit creates revision+1
agent origin step replay returns same revision
agent origin step + changed payload conflicts
verification is immutable/reused by unique semantic key
transient live repository change does not poison immutable Verification reuse:
  verify against clean bound Snapshot -> passed;
  live worktree changes -> approval readiness rejects;
  live worktree restored exactly -> same passed Verification may approve after fresh readiness check
failed verification cannot approve
rejected decision cannot materialize Version
approve atomically creates DesignDecision + Version
same approve retry returns same Version
two different verified revisions for one Experiment approved concurrently -> both succeed with consecutive distinct version_number values; no raw unique violation escapes
semantic mismatch cannot reuse decision/version
Version has no update operation and all binding hashes match the approved values
```

Also test rollback: force Version insert failure after decision staging and assert neither decision nor Version persists.

- [ ] **Step 8: Run integration tests and confirm RED**

```bash
uv run pytest tests/integration/experiments/test_proposal_service.py -q -m integration
```

- [ ] **Step 9: Implement `0007`, ORM rows, repository, and application service**

Use the exact schema in §3.1. Keep JSONB owner-local value objects; do not split Dataset/Environment/Validation into new tables.

`verify_revision` must never call live approval readiness. `approve_revision` performs the live repository readiness check outside its write UoW immediately before authorization. Inside UoW it performs no Git/model I/O, locks the owning Experiment row, rechecks existing decision/version, allocates the next `version_number`, and then inserts Decision + Version atomically as specified in §2.5/§4.

- [ ] **Step 10: Register models without weakening architecture rules**

Add `labpilot.experiments.proposal_models` to `db/model_registry.py` and to the model-registry path-specific allowlist. Do not grant `experiments` permission to import `context`, `tools_policy`, or `agent_runtime`.

- [ ] **Step 11: Run focused domain/migration regression**

```bash
uv run pytest tests/unit/experiments/test_proposal_types.py \
  tests/unit/experiments/test_proposal_verifier.py -q
uv run pytest tests/integration/experiments/test_proposal_service.py \
  tests/integration/experiments/test_project_service.py \
  tests/integration/db/test_migrations.py -q -m integration
uv run pytest tests/architecture -q
uv run ruff check src/labpilot/experiments migrations tests/unit/experiments tests/integration/experiments
uv run mypy src/labpilot/experiments
```

If the existing destructive migration round-trip suite is separate, run its existing command once because this task adds a migration.

- [ ] **Step 12: Deep review and commit**

After APPROVE:

```bash
git add src/labpilot/experiments/proposal_*.py \
  migrations/versions/0007_experiment_proposals.py \
  src/labpilot/db/model_registry.py \
  tests/architecture/dependency_rules.py \
  tests/architecture/test_model_registry.py \
  tests/unit/experiments/test_proposal_types.py \
  tests/unit/experiments/test_proposal_verifier.py \
  tests/integration/experiments/test_proposal_service.py
git commit -m "feat: add verified experiment proposals"
```

---

# Task 4: S1.2 Static Tool Safety + Bounded Agent Runtime

**Model:** GPT-5.6 Sol  
**Reasoning effort:** High  
**Risk tier:** Boundary-sensitive; path/secret/approval/egress subparts Safety-critical  
**Review:** One deep independent review; remediation review is prior findings + direct regression only.

**Files:**
- Create: `src/labpilot/tools_policy/__init__.py`
- Create: `src/labpilot/tools_policy/types.py`
- Create: `src/labpilot/tools_policy/models.py`
- Create: `src/labpilot/tools_policy/repository.py`
- Create: `src/labpilot/tools_policy/service.py`
- Create: `src/labpilot/tools_policy/repository_tools.py`
- Create: `src/labpilot/agent_runtime/__init__.py`
- Create: `src/labpilot/agent_runtime/types.py`
- Create: `src/labpilot/agent_runtime/models.py`
- Create: `src/labpilot/agent_runtime/repository.py`
- Create: `src/labpilot/agent_runtime/provider.py`
- Create: `src/labpilot/agent_runtime/opencode_go.py`
- Create: `src/labpilot/agent_runtime/context_compiler.py`
- Create: `src/labpilot/agent_runtime/service.py`
- Create: `migrations/versions/0008_agent_tool_runtime.py`
- Modify: `src/labpilot/settings.py`
- Modify: `.env.example`
- Modify: `src/labpilot/db/model_registry.py`
- Modify: `tests/architecture/dependency_rules.py`
- Modify: `tests/architecture/test_model_registry.py`
- Create: `tests/unit/tools_policy/test_tool_service.py`
- Create: `tests/integration/tools_policy/test_tool_persistence.py`
- Create: `tests/unit/agent_runtime/test_context_compiler.py`
- Create: `tests/unit/agent_runtime/test_opencode_go.py`
- Create: `tests/unit/agent_runtime/test_agent_service.py`
- Create: `tests/integration/agent_runtime/test_agent_service.py`
- Regression: `tests/integration/db/test_migrations.py`

**Interfaces:**
- Consumes: Repository source facade, ProposalApplicationService, existing CommandContext/UoW/idempotency helper.
- Produces: exact §2.6/§2.7 Tool/Agent contracts, §2.8 logging compliance, and migration `0008`.

- [ ] **Step 1: Write Tool service unit tests first**

Cover:

```text
unknown Tool rejected
invalid args rejected before persistence/execution
canonical arguments hash stable
bound_toolset_hash stable for same name/version/risk/side-effect/approval/schema semantics
Tool version or input_schema semantic change -> different bound_toolset_hash
read-only Repository tool executes without approval
approval-required synthetic Tool cannot execute without exact approval
approval with wrong args hash rejected
rejected Tool cannot execute
executor exception persists failed/error_code without secret/raw traceback payload
```

`ToolApproval` remains in this Slice because the current Scope keeps Tool Execution Approval. The synthetic Tool proves the narrow authority mechanism without adding a production side-effecting Tool.

- [ ] **Step 2: Run Tool unit tests and confirm RED**

```bash
uv run pytest tests/unit/tools_policy/test_tool_service.py -q
```

- [ ] **Step 3: Implement static ToolDefinition + ToolApproval persistence/service**

Use exact §2.6 plus `bound_toolset_hash` from §2.7. The registry is a Python mapping built at app composition time; no availability/manifest/freshness tables.

For `repository_tools.py`, bind every Tool executor to one `repository_snapshot_id` argument and the Repository facade. Schema rejects arbitrary filesystem root/path escape.

- [ ] **Step 4: Write migration persistence tests and confirm RED**

```bash
uv run pytest tests/integration/tools_policy/test_tool_persistence.py -q -m integration
```

Then implement the `tool_calls`/`tool_approvals` part of `0008` and ORM/repository rows.

- [ ] **Step 5: Add OpenCode Go provider settings with secret-safe types**

Add exactly:

```python
agent_provider_model: str | None = None
agent_provider_api_key: SecretStr | None = None
agent_provider_timeout_seconds: int = Field(default=60, ge=1, le=300)
agent_provider_max_response_bytes: int = Field(default=1024 * 1024, ge=1024)
```

Do **not** add `agent_provider_base_url`: the Slice 1 OpenCode Go chat endpoint is a fixed adapter contract. Do not read OpenCode CLI auth state or Codex/ChatGPT workspace credentials.

`.env.example` contains only empty names/placeholders:

```text
LABPILOT_AGENT_PROVIDER_MODEL=
LABPILOT_AGENT_PROVIDER_API_KEY=
```

Do not persist `SecretStr` values.

- [ ] **Step 6: Write exact AgentDecision/context-compiler tests first**

Tests prove all four §2.7 variants and reject malformed cross-variant payloads:

```text
tool_call requires tool_name + JSON arguments
request_human_input requires question
propose requires exact ProposalPayload
stop requires reason_code
extra fields rejected
rationale/reasoning/chain_of_thought rejected
summary UTF-8 byte limit enforced
only Project goal + bound Snapshot descriptor + bounded Tool schemas + prior Tool/Human results enter request
repository source content enters only through explicit read Tool results
secret settings never enter messages
local_only + remote provider rejects before provider call
output schema is deterministic
same Tool contract -> same bound_toolset_hash; changed schema/version -> different hash
canonical_request_hash binds output_schema_hash + bound_toolset_hash
```

- [ ] **Step 7: Run Agent type/context tests and confirm RED**

```bash
uv run pytest tests/unit/agent_runtime/test_context_compiler.py -q
```

- [ ] **Step 8: Write OpenCode Go adapter contract tests before implementation**

Use `httpx.MockTransport`; no real network. Cover the exact §2.7 wire contract:

```text
POST exact https://opencode.ai/zen/go/v1/chat/completions path
Authorization Bearer header created from SecretStr only at call boundary
request body contains exactly model + messages for Slice 1
non-empty semantic_parameters rejected as unsupported
no provider-native tools/response_format/session fields
choices[0].message.content string JSON-object extraction
usage object extraction / absent usage -> {}
non-2xx -> bounded provider_http_error without response-body/secret leak
timeout -> provider_timeout
oversized body -> provider_response_too_large
missing choices/message/content, non-string content, malformed JSON, non-object JSON -> provider_malformed_response
sentinel API key absent from exception strings and captured structured logs
```

- [ ] **Step 9: Implement Provider Protocol and `OpenCodeGoChatProvider`**

Use `httpx.AsyncClient`; no provider SDK dependency. Follow §2.7 literally. Do not add `/v1/responses`, `/v1/messages`, dynamic endpoint discovery, native provider Tool calling, streaming, or provider session state.

- [ ] **Step 10: Run provider contract tests**

```bash
uv run pytest tests/unit/agent_runtime/test_opencode_go.py -q
```

- [ ] **Step 11: Write Agent service unit tests before service implementation**

Use a `ScriptedModelProvider` fake and fake Tool/Proposal services. Required tests:

```text
create_run validates bound Snapshot
remote provider blocked by local_only before provider invocation
reserve step persists canonical_request_hash + bound_toolset_hash + output_schema_hash before call
no UoW active while provider.complete executes
interrupted/unknown model-call outcome is not automatically retried
request hash is stable for the exact reserved request semantics
tool_call persists ToolCall, executes read-only tool, continues
request_human_input pauses run
submit_human_input resumes with new human step
propose persists model step before Proposal materialization
crash/retry after propose step reuses proposal by origin_agent_step_id
max_steps/model_calls/tokens/wallclock terminates budget_exhausted
provider failure => failed with explicit termination reason
invalid AgentDecision never executes Tool or materializes Proposal
structured log records carry applicable request_id/project_id/agent_run_id/tool_call_id and never raw Prompt/ToolResult/secret
```

- [ ] **Step 12: Run Agent service tests and confirm RED**

```bash
uv run pytest tests/unit/agent_runtime/test_agent_service.py -q
```

- [ ] **Step 13: Implement Agent rows/repository/service and the remaining `0008` schema**

Follow §2.7, §2.8, §3.2 and §4 exactly. No Lease/Wakeup/DetachedResponse tables.

`create_run` stores effective egress policy, provider name/model and budget snapshot. It does not use CommandReceipt in Slice 1 and never invokes the provider during run creation.

Every reserved model-call AgentStep stores `canonical_request_hash`, `bound_toolset_hash`, `semantic_parameters_json`, and `output_schema_hash` before the external provider call.

- [ ] **Step 14: Write real PostgreSQL transaction-boundary tests**

Prove with injectable callbacks/failpoints:

```text
provider call happens after step reservation commit
provider failure leaves reserved/finalized failed step, not an open transaction
Tool executor happens outside UoW
proposal materialization retry after simulated crash does not duplicate ProposalRevision
concurrent advance with same expected_row_version has one winner and one conflict
persisted AgentStep exposes bound_toolset_hash independently of canonical_request_hash
```

- [ ] **Step 15: Register model imports / preserve dependency directions**

Add `tools_policy.models` and `agent_runtime.models` to model registry path-specific allowances. Do not broaden `experiments` allowed imports. `agent_runtime` may import experiments/context/tools as already permitted by the module rule.

- [ ] **Step 16: Run focused regression**

```bash
uv run pytest tests/unit/tools_policy tests/unit/agent_runtime -q
uv run pytest tests/integration/tools_policy tests/integration/agent_runtime \
  tests/integration/db/test_migrations.py -q -m integration
uv run pytest tests/architecture -q
uv run ruff check src/labpilot/tools_policy src/labpilot/agent_runtime migrations \
  tests/unit/tools_policy tests/unit/agent_runtime \
  tests/integration/tools_policy tests/integration/agent_runtime
uv run mypy src/labpilot/tools_policy src/labpilot/agent_runtime
```

Run the existing destructive migration round-trip once for migration `0008`.

- [ ] **Step 17: Deep review and commit**

After APPROVE:

```bash
git add src/labpilot/tools_policy src/labpilot/agent_runtime \
  migrations/versions/0008_agent_tool_runtime.py \
  src/labpilot/settings.py .env.example \
  src/labpilot/db/model_registry.py tests/architecture \
  tests/unit/tools_policy tests/integration/tools_policy \
  tests/unit/agent_runtime tests/integration/agent_runtime
git commit -m "feat: add bounded proposal agent runtime"
```

---

# Task 5: Slice 1 API Composition

**Model:** GPT-5.6 Terra  
**Reasoning effort:** High  
**Risk tier:** Routine / Boundary-sensitive transport and authority wiring  
**Review:** One focused implementation review; deep review only if the diff changes authority semantics.

**Files:**
- Create: `src/labpilot/api/schemas/agent_runs.py`
- Create: `src/labpilot/api/schemas/experiments.py`
- Create: `src/labpilot/api/routers/agent_runs.py`
- Create: `src/labpilot/api/routers/experiments.py`
- Modify: `src/labpilot/api/dependencies.py`
- Modify: `src/labpilot/api/app.py`
- Modify: `src/labpilot/api/errors.py`
- Modify: `src/labpilot/main.py`
- Create: `tests/integration/api/test_slice1_api.py`
- Regression: `tests/integration/api/test_projects.py`
- Regression: `tests/integration/api/test_readiness.py`

**Interfaces:**
- Consumes: Proposal/Agent services, Repository facade/adapter, Tool registry/provider composition.
- Produces: minimal REST surface for Slice 1 acceptance.

### Frozen endpoints

```text
POST /projects/{project_id}/agent-runs
GET  /agent-runs/{agent_run_id}
POST /agent-runs/{agent_run_id}/advance
POST /agent-runs/{agent_run_id}/human-input

GET  /proposal-revisions/{proposal_revision_id}
POST /experiments/{experiment_id}/proposal-revisions
POST /proposal-revisions/{proposal_revision_id}/verify
POST /proposal-revisions/{proposal_revision_id}/approve
POST /proposal-revisions/{proposal_revision_id}/reject

GET  /experiment-versions/{experiment_version_id}
```

No generic Tool admin API and no SSE endpoint in this Slice.

- [ ] **Step 1: Write API integration tests with injected ScriptedModelProvider**

Test status codes and exact authority behavior:

```text
create AgentRun -> 201 and no provider call yet
concurrent/stale advance with the same expected row version -> one winner / one 409 conflict
advance to human_input -> awaiting_human
human input with wrong row_version -> 409
advance to propose -> returns produced proposal id
verify invalid entrypoint -> passed=false
manual revision fixes payload -> revision+1
verify -> passed=true
approve -> returns immutable ExperimentVersion
repeat approve -> same Version
approve dirty/changed clean Snapshot -> 409 conflict
reject -> no Version
local_only + configured remote provider -> 403/409 policy denial before provider call
```

- [ ] **Step 2: Run API test and confirm RED**

```bash
uv run pytest tests/integration/api/test_slice1_api.py -q -m integration
```

- [ ] **Step 3: Add Pydantic request/response schemas**

Transport schemas mirror domain value objects; `extra="forbid"`. Do not expose Bundle/Entry/CAS identity or raw provider secret.

- [ ] **Step 4: Add dependency getters and compose services**

Extend `ApplicationServices` with:

```text
repository_source
proposals
agent_runs
```

Tool service remains an internal dependency of Agent service unless direct API needs it (it does not in Slice 1).

In `main.py` construct:

```text
Database
GitAdapter(existing settings)
ContentAddressedStore
RepositorySnapshotSourceService
ProposalRepositoryAdapter
ProposalVerifier
ProposalApplicationService
static Repository Tool registry/ToolApplicationService
configured or Unconfigured ModelProvider
AgentApplicationService
```

No network call occurs during app startup.

- [ ] **Step 5: Add explicit domain error mappings**

Map expected not-found/conflict/policy/validation errors to 404/409/422/403-like responses. Do not let expected Proposal/Agent conflicts fall through the generic 500 handler.

Error payload must not include raw exception traceback, secret, Prompt, or ToolResult content.

Any new API operational logs must use §2.8. For command routes, the structured `request_id` value is the existing `CommandContext.interaction_correlation_id`; do not add separate request-id persistence/middleware solely for this Slice.

- [ ] **Step 6: Implement routers**

API handlers translate Pydantic → domain DTOs and delegate. They do not duplicate verifier/policy logic.

No new Slice 1 endpoint requires `Idempotency-Key`. Historical Project/Repository endpoints keep their accepted Receipt behavior. New Slice 1 writes rely on the exact UoW/unique/CAS semantics frozen in this plan.

- [ ] **Step 7: Run focused API regression**

```bash
uv run pytest tests/integration/api/test_slice1_api.py \
  tests/integration/api/test_projects.py \
  tests/integration/api/test_readiness.py -q -m integration
uv run ruff check src/labpilot/api src/labpilot/main.py tests/integration/api
uv run mypy src/labpilot/api src/labpilot/main.py
```

- [ ] **Step 8: Focused review and commit**

After APPROVE:

```bash
git add src/labpilot/api src/labpilot/main.py tests/integration/api/test_slice1_api.py
git commit -m "feat: expose slice one proposal API"
```

---

# Task 6: S1.4 Vertical Slice Acceptance

**Model:** GPT-5.6 Sol  
**Reasoning effort:** High  
**Risk tier:** Slice acceptance; egress/secret and Approval boundary treated Safety-critical  
**Review:** One Slice-level acceptance review. No new broad architecture review.

**Files:**
- Create: `tests/integration/slice1/test_proposal_loop.py`
- Create: `tests/integration/slice1/test_opencode_go_smoke.py`
- Modify only if evidence requires a direct defect fix in Tasks 1–5.
- Do not update `DOCUMENT-STATUS.md` until this acceptance and review pass.

**Interfaces:**
- Consumes: all Tasks 1–5.
- Produces: executable evidence that Slice 1 outcome is real and that the plan can be closed.

- [ ] **Step 1: Build a deterministic real-repository fixture**

The test creates a temporary Git repository with committed files such as:

```text
train.py
eval.py
config.json
README.md
```

The repository contains no network dependency. Capture the Snapshot using the real Repository registration/capture services and real `GitAdapter`/CAS.

- [ ] **Step 2: Add the positive end-to-end test**

Using a ScriptedModelProvider (not a mocked repository), drive:

```text
Project
→ RepositoryRegistration
→ clean RepositorySnapshot
→ AgentRun
→ repository.list_tree
→ repository.read_text(train.py)
→ propose
→ ProposalRevision
→ deterministic verification PASS
→ Human Approval
→ immutable ExperimentVersion
```

Assert:

```text
Agent output did not directly create Version
ToolCall rows bind exact read-only arguments/results
AgentStep binds canonical_request_hash + bound_toolset_hash + output_schema_hash
Proposal payload binds the exact Snapshot id/identity
Verification result hash matches persisted result
DesignDecision binds exact revision/verifier/source/dataset/environment/actor/time
ExperimentVersion hashes/JSON match approved semantics
no Projection row/state is required for correctness
no Batch/Trial/Attempt exists
```

- [ ] **Step 3: Add negative source-authority scenarios**

One parametrized test proves:

```text
snapshot from another Project cannot enter this Experiment/Agent approval path
dirty captured Snapshot -> Agent may read/propose -> immutable verifier fails clean-source rule and approval cannot freeze Version
clean Snapshot verifies PASS -> worktree edit -> approval readiness fails without mutating/replacing the passed Verification
restore worktree exactly to the bound clean Snapshot -> same passed Verification can approve after a fresh readiness check
clean Snapshot then worktree edit -> approval readiness fails
clean Snapshot then untracked file -> fails
clean Snapshot then HEAD/branch change -> fails
bad Agent-proposed entrypoint absent from Snapshot -> deterministic verifier fails
```

- [ ] **Step 4: Add human-input and revision scenario**

Drive:

```text
Agent asks REQUEST_HUMAN_INPUT
→ run awaiting_human
→ user input stored
→ run resumes
→ initial Proposal fails verifier
→ user creates manual revision
→ new revision verifies
→ old revision/old failed verification cannot authorize new Version
```

- [ ] **Step 5: Add egress/secret scenario**

Prove:

```text
Settings.data_egress_policy = local_only
remote ModelProvider selected
→ provider.complete call count remains 0
→ no source Tool result appears in model request
```

With an injected sentinel API key, query AgentStep, ToolCall, RuntimeEvent and serialized API response text, and inspect captured structured log records; assert the sentinel never appears. Also assert applicable Agent/Tool logs contain only the bounded correlation fields from §2.8.

- [ ] **Step 6: Add retry/crash-boundary evidence**

Prove:

```text
create AgentRun performs no external side effect
concurrent/stale `advance` cannot reserve a second model step for the same run version
interrupted reserved model call is failed closed; Slice 1 does not blindly retry an unknown provider outcome
changed request semantics always produces a new step/request hash, never masquerades as a retry
simulated crash after propose AgentStep persisted but before run binds proposal
→ replay materializes/reuses exactly one ProposalRevision via origin_agent_step_id
repeat approve -> exactly one DesignDecision and one ExperimentVersion
two different verified revisions for the same Experiment approved concurrently
→ both valid approvals succeed
→ distinct consecutive version_number values
→ no raw unique-constraint failure escapes
```

- [ ] **Step 7: Record one opt-in real OpenCode Go provider smoke**

This is **not** part of ordinary CI and must never print/store the API key. It is required once before declaring Slice 1 Accepted because the product now has one real production provider adapter.

`tests/integration/slice1/test_opencode_go_smoke.py` is skipped unless all three are present:

```text
LABPILOT_RUN_EXTERNAL_PROVIDER_SMOKE=1
LABPILOT_AGENT_PROVIDER_MODEL=<a currently available OpenCode Go model served by /v1/chat/completions>
LABPILOT_AGENT_PROVIDER_API_KEY=<secret>
```

The smoke uses a small real Git repository/Snapshot and the real `OpenCodeGoChatProvider`, then proves at least:

```text
real provider call succeeds
AgentDecision JSON validates
at least one Repository Tool observation can participate in the loop
ProposalRevision is produced
provider never becomes Approval/Version authority
secret sentinel is absent from persisted rows/logs/errors
```

Do not hardcode a model list in the application; OpenCode Go's available models/protocol mapping can change. If the selected model is no longer served by `/v1/chat/completions`, choose another currently compatible Go model rather than adding another protocol adapter during Slice 1 acceptance.

Run without placing the secret in shell history:

```bash
read -rsp "OpenCode Go API key: " LABPILOT_AGENT_PROVIDER_API_KEY
printf '\n'
export LABPILOT_AGENT_PROVIDER_API_KEY
export LABPILOT_RUN_EXTERNAL_PROVIDER_SMOKE=1
export LABPILOT_AGENT_PROVIDER_MODEL='<current chat/completions Go model id>'
uv run pytest tests/integration/slice1/test_opencode_go_smoke.py -q -m integration
unset LABPILOT_AGENT_PROVIDER_API_KEY
```

Expected: PASS once, with the provider key absent from test output/log capture/persistence evidence.

- [ ] **Step 8: Run Slice 1 deterministic acceptance test**

```bash
uv run pytest tests/integration/slice1/test_proposal_loop.py -q -m integration
```

Expected: PASS.

- [ ] **Step 9: Run fresh PostgreSQL migration evidence**

From a fresh isolated PostgreSQL database run the existing migration-to-head test and destructive round-trip suite. Require `0007` and `0008` both up/down according to the repository's existing migration policy.

- [ ] **Step 10: Run final Slice gate**

Run, in this order:

```bash
uv lock --check
uv sync --locked --all-groups

bash scripts/verify_architecture_dependencies.sh \
  docs/labpilot/implementation/phase-0-v0.3.4.dependencies.sha256
bash scripts/verify_architecture_dependencies.sh \
  docs/labpilot/implementation/wp1-a-v0.2.dependencies.sha256

uv run ruff check src tests scripts
uv run mypy src scripts
uv run pytest tests/unit tests/architecture -v
uv run pytest tests/integration -v -m integration
```

Also run the repository's destructive migration test command separately if it is deselected by the normal integration command.

Expected: zero failures.

- [ ] **Step 11: Slice-level acceptance review**

Reviewer scope:

```text
Slice 1 approved plan
+ implementation diff since exact plan baseline
+ positive/negative deterministic E2E evidence
+ one recorded opt-in real OpenCode Go smoke evidence
+ direct regressions in Accepted Foundation
```

The reviewer must **not** reopen R0 architecture or invent Slice 2/3 requirements.

Blocking issues are only:

```text
security/secret/egress bypass
data/provenance corruption
Approval bypass
Agent inference becoming authority
formal Version from dirty/stale source
missing accepted Slice 1 requirement
direct regression of Accepted Foundation
```

Any unrelated non-critical improvement is a Deferred Finding.

- [ ] **Step 12: Commit acceptance test**

After acceptance review APPROVE:

```bash
git add tests/integration/slice1/test_proposal_loop.py \
  tests/integration/slice1/test_opencode_go_smoke.py
git commit -m "test: prove slice one proposal loop"
```

If a direct defect fix was required during acceptance, include only those exact files and document the finding-specific evidence.

- [ ] **Step 13: One Slice status closure only**

Only now update `DOCUMENT-STATUS.md` in a separate small docs commit:

```text
Slice 1 Detailed Implementation Plan: Approved / Executed
Slice 1: Accepted
Slice 2 Detailed Plan: Authorized for JIT drafting
Slice 2 implementation: Not authorized until its plan is approved
```

Record the Slice 1 acceptance commit SHA. Do not start Slice 2 in the same operation.

---

## 5. Plan review gate

The original focused plan review is complete. This r2 receives **one finding-specific plan re-review only** before implementation. Do not perform another broad plan/architecture review.

### Reviewer inputs

- Planning baseline: `032dd87d3366dc6edd66fb75451d44047e49f7dd`
- the two focused-review feedback documents that produced P1–P7;
- this r2 plan;
- R0 Scope/Architecture/Roadmap only as needed to verify the cited authority constraints.

### Finding-specific questions

1. **P1 Provider:** Is the only production remote adapter now `OpenCodeGoChatProvider` with a frozen `/v1/chat/completions` wire contract, MockTransport coverage, no OpenAI API credential assumption, and no second protocol family?
2. **P2 AgentDecision:** Are `tool_call`, `request_human_input`, `propose`, and `stop` exact discriminated variants with required payload fields, extra-field rejection, and no chain-of-thought field?
3. **P3 Currentness:** Is immutable Verification based only on Proposal + bound Snapshot semantics, with mutable live repository readiness checked only immediately at Approval?
4. **P4 FastAPI:** Is the FastAPI/SSE refresh removed from Slice 1 while `uv lock --check`, locked sync, loopback exposure, Projection non-default, and egress alignment remain?
5. **P5 Tool schema audit:** Does every model-call AgentStep persist `bound_toolset_hash`, with deterministic hash semantics and tests for Tool version/schema change?
6. **P6 Structured logs:** Is the applicable `request_id/project_id/agent_run_id/tool_call_id` contract frozen using existing `interaction_correlation_id`, with explicit secret/raw-content redaction and sentinel tests, without creating an observability platform?
7. **P7 Version concurrency:** Does approval lock the Experiment before allocating `version_number`, recheck retry state after lock, and test two different concurrent approvals?
8. **Direct regressions only:** Did these edits introduce a Critical/Important contradiction in ToolApproval authority, Repository facade, short-UoW rules, Proposal exact binding, egress/secret safety, or Slice containment?

### Explicitly out of re-review scope

Do not reopen areas already passed by the focused review:

```text
Repository facade/Task 10 semantics
Task 11 Freshness platform
Dataset/Environment object admission
ToolApproval existence (it remains required by current V1-FR-015)
Proposal/Version overall architecture
Slice 2/3 planning
Projection/Evidence/Claim platforms
manual revision retry ambiguity unless remediation directly worsened it
```

### Disposition

If P1–P7 all PASS, direct regressions are NONE, Critical = 0, and Important = 0:

```text
APPROVE
No second broad plan review is warranted.
Proceed to mechanical plan finalization/commit/push, then authorize Task 1 implementation.
```

Otherwise only a still-open P1–P7 blocker or a direct Critical/Important remediation regression may require another targeted edit.

---

## 6. Implementation branch / authorization protocol after plan approval

After this plan is approved and committed/pushed, implementation starts from the commit that contains the **Approved** plan and has R0 SHA `032dd87d...` as ancestor.

Recommended branch:

```text
feature/v1-slice1-proposal-loop
```

Do not create that branch before plan approval unless needed only to stage the proposed plan candidate.

Implementation execution is sequential Task 1 → 6. A task may be amended locally after its focused finding-specific review; rejected candidates do not require permanent tags unless the failure is safety-critical or the diff is difficult to reconstruct.

No per-task `DOCUMENT-STATUS.md` closure commits. The only status closure is Task 6 / Slice 1 acceptance.

---

## 7. Self-review checklist for this plan

### Spec coverage

- S1.0 foundation alignment without premature FastAPI/SSE refresh: Task 1.
- S1.1 narrow repository fact/source surface: Task 2.
- S1.2 bounded Agent + static Tool path/model audit, exact AgentDecision, OpenCode Go adapter contract, bound_toolset_hash: Task 4.
- S1.3 Proposal + deterministic verifier + Approval + Version: Task 3.
- S1.4 API/vertical acceptance: Tasks 5–6.
- Clean-only formal source: Tasks 2, 3, 6.
- Agent inference non-authoritative: Tasks 2, 3, 6.
- DatasetReference/EnvironmentExpectation: Task 3.
- Tool approval exact action semantics: Task 4 synthetic proof; no production side-effect Tool.
- Data egress/secret + structured operational logging boundary: Tasks 1, 4, 5, 6.
- No Task 11 Freshness workflow: Task 2 explicitly verifies no persistence mutation.
- Mutable live Repository currentness is excluded from immutable Verification and checked JIT at Approval: Tasks 3 and 6.
- Concurrent ExperimentVersion numbering is serialized under the Experiment row lock: Task 3 and Slice acceptance.
- One real OpenCode Go chat-completions smoke is recorded before Slice acceptance; ordinary CI remains ScriptedModelProvider/MockTransport based.
- No Slice 2/3 implementation: global constraint + all task file maps.

### Persisted-object admission

Every new table has a current Slice 1 reason:

- `experiments`: independent long-lived topic container across proposal revisions/versions.
- `experiment_proposal_revisions`: immutable user/Agent-edit history and approval target.
- `experiment_proposal_verifications`: immutable deterministic eligibility result; approval must bind exact result.
- `experiment_design_decisions`: independent authorization/audit history.
- `experiment_versions`: immutable formal experiment semantic boundary.
- `tool_calls`: independent execution/audit lifecycle.
- `tool_approvals`: independent human authorization lifecycle; only one per exact call.
- `agent_runs`: bounded Agent lifecycle.
- `agent_steps`: ordered durable step/model-call/human-input audit needed for crash-safe continuation.

No additional manifest/freshness/lease/wakeup/projection/evidence/result-processing tables are admitted.

### Type consistency

- `RepositorySnapshotSourceService` is adapted to `ProposalRepositoryFactPort`; experiments does not import context.
- Agent runtime may depend on experiments/context/tools under existing module direction rules.
- `origin_agent_step_id` is a UUID replay anchor without a reverse DB FK from experiments to agent runtime.
- `AgentRun.produced_proposal_revision_id` may FK experiments because agent runtime is downstream of experiments.
- ToolCall is independent; AgentStep may FK ToolCall because agent runtime is downstream of tools_policy.

### Placeholder scan

This plan intentionally contains no `TBD`, `TODO`, “similar to Task N”, generic “add validation”, or unresolved implementation decision. Slice 2 Runner/systemd decisions are out of scope rather than placeholders.
