# LabPilot V1 Slice3 Result Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:test-driven-development` for implementation tasks and `superpowers:verification-before-completion` before any completion claim. Execute this plan task-by-task; do not skip RED/GREEN gates.

**Goal:** Close the V1 result loop from a successful terminal Attempt through declared Artifact collection, deterministic Metric extraction and Validation, immutable first-eligible Trial selection, BatchSummary, authoritative read APIs, and a minimal SSE notification surface.

**Architecture:** Keep Execution Plane authority separate from scientific Result Plane authority. Add the already-admitted `labpilot.results_validation` owner with append-only result facts and deterministic rerun. Coordinator triggers processing but does not own scientific semantics. Execution continues to own retry/exhaustion/cancellation. Preserve existing Proposal/ExperimentVersion identities, use SHA-256 only for Artifact byte integrity, and do not add ProcessingJob, CAS, Projection Worker, generic result-processing infrastructure, or LLM scientific eligibility decisions.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy asyncio, PostgreSQL 16, Alembic, asyncpg, pytest, Ruff, Mypy, systemd-backed Slice2 Runner for native acceptance.

**Design spec:** `docs/superpowers/specs/2026-08-31-labpilot-v1-slice3-result-plane-design.md`

**Focused design review:** `docs/superpowers/specs/2026-09-01-labpilot-v1-slice3-result-plane-focused-rereview.md`

**Sequencing authority:** `docs/labpilot/implementation/01a-slice2-to-slice3-sequencing-amendment-v0.3.1.md`

**Planning baseline:** `ea3d5ced1c886e6484f1ab2a372c0dab547a4deb`

## Global Constraints

- The future Slice3 implementation branch/worktree must descend from the approved planning baseline after this plan passes focused review.
- Scientific result processing starts only for `Attempt.status == terminal`, `terminal_kind == process_exited`, `exit_code == 0`.
- Execution failure/retry/exhaustion/cancellation stays inside `labpilot.execution`; Result Plane must not duplicate `max_retries` or infrastructure-retry policy.
- `TrialResultSelection` is immutable first-eligible selection. Later extractor/validator semantic versions do not automatically withdraw or replace it.
- Preserve historical canonical JSON for `artifact_exists` and `metric_finite`. Adding `threshold=None` in memory must not alter existing Proposal payload hashes, validation protocol hashes, or ExperimentVersion identities.
- Do not add `input_identity_hash`, `summary_hash`, `metric_hash`, `selection_hash`, or similar bookkeeping hashes. Reuse existing upstream identities. The only new digest is `Artifact.content_sha256`, required for byte integrity.
- Artifact reads must be anchored under the controlled Attempt output root, reject traversal/symlinks, and bind digest plus extraction to one stable opened byte snapshot.
- `best_effort` Dataset semantics create an explicit reproducibility warning in Validation and Summary.
- Known required environment mismatch is `invalid`; insufficient observed environment is `inconclusive`; deterministic content/provenance contradiction is `error`.
- Transient operational failure commits no ValidationReport. Known deferrable filesystem errors raise a retryable processing signal; database/infrastructure exceptions remain visible and are not converted into scientific facts.
- Result processing uses deterministic rerun. Do not introduce ProcessingJob unless a demonstrated failure mode forces a later plan amendment.
- Use `src/labpilot/results_validation/`; do not create a parallel `labpilot.results` owner.
- Full Artifact CAS/object storage, Projection Worker, historical reselection, advanced statistics, and LLM result interpretation remain deferred.
- FastAPI/SSE dependency refresh is isolated late in the Slice and committed separately from the Result Plane core.
- JSONB fields never rely on Python `Decimal` serialization. Persist decimal values inside JSONB as canonical decimal text; the authoritative Metric value remains PostgreSQL `NUMERIC`.

---

## Planned File Map

### New production files

```text
src/labpilot/results_validation/__init__.py
src/labpilot/results_validation/models.py
src/labpilot/results_validation/types.py
src/labpilot/results_validation/artifact_reader.py
src/labpilot/results_validation/extractor.py
src/labpilot/results_validation/validator.py
src/labpilot/results_validation/service.py
src/labpilot/results_validation/summary_service.py
src/labpilot/results_validation/query_service.py
src/labpilot/execution/trial_outcome.py
src/labpilot/api/routers/results.py
src/labpilot/api/schemas/results.py
migrations/versions/0011_result_plane.py
```

### Existing production/support files expected to change

```text
src/labpilot/experiments/proposal_types.py
src/labpilot/experiments/proposal_service.py
src/labpilot/experiments/proposal_verifier.py
src/labpilot/api/schemas/experiments.py
src/labpilot/execution/batch_service.py
src/labpilot/coordinator/service.py
src/labpilot/coordinator/worker.py
src/labpilot/db/model_registry.py
src/labpilot/api/dependencies.py
src/labpilot/api/app.py
src/labpilot/api/errors.py
src/labpilot/main.py
tests/architecture/dependency_rules.py
tests/acceptance/slice2/training_fixture/train.py
pyproject.toml
uv.lock
```

---

### Task 1: Add threshold ValidationRule semantics without breaking historical bindings

**Risk:** Boundary-sensitive compatibility blocker.

**Files:**
- Modify: `src/labpilot/experiments/proposal_types.py`
- Modify: `src/labpilot/experiments/proposal_service.py`
- Modify: `src/labpilot/experiments/proposal_verifier.py`
- Modify: `src/labpilot/api/schemas/experiments.py`
- Modify tests: `tests/unit/experiments/test_proposal_types.py`
- Modify tests: `tests/unit/experiments/test_proposal_verifier.py`
- Modify tests: `tests/integration/experiments/test_proposal_service.py`
- Modify tests: `tests/unit/coordinator/test_execution_adapters.py`
- Modify tests: `tests/integration/api/test_slice1_api.py`

**Interface:**

```python
type ValidationRuleKind = Literal[
    "artifact_exists",
    "metric_finite",
    "metric_gte",
    "metric_lte",
]

@dataclass(frozen=True, slots=True)
class ValidationRule:
    kind: ValidationRuleKind
    subject: str
    threshold: Decimal | None = None
```

- [ ] **Step 1: Write RED compatibility tests**

Keep the existing frozen Proposal hash assertion:

```python
assert proposal_payload_hash(payload) == (
    "de7b31452c083fa70fc986fc05938dd16f6595ae0427aa3f14f8f5a56d6d3ba1"
)
```

Add assertions that legacy stored JSON remains exactly:

```python
{"kind": "artifact_exists", "subject": "model"}
{"kind": "metric_finite", "subject": "accuracy"}
```

and that a pre-Slice3 stored Proposal still loads through `VersionExecutionSemanticsAdapter.load()` **without raising** `PROPOSAL_BINDING_MISMATCH`.

Add new-rule tests:

```python
rule = ValidationRule("metric_gte", "accuracy", Decimal("0.8"))
assert proposal_service._validation_to_json(rule) == {
    "kind": "metric_gte",
    "subject": "accuracy",
    "threshold": "0.8",
}
assert proposal_service._validation_from_json(
    {"kind": "metric_gte", "subject": "accuracy", "threshold": "0.8"}
).threshold == Decimal("0.8")
```

The stored Proposal/Version JSON uses canonical decimal text because the default SQLAlchemy/asyncpg JSON serializer is standard `json.dumps`, which does not serialize `Decimal`.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest \
  tests/unit/experiments/test_proposal_types.py \
  tests/unit/experiments/test_proposal_verifier.py \
  tests/integration/experiments/test_proposal_service.py \
  tests/unit/coordinator/test_execution_adapters.py -q
```

Expected: threshold tests fail before implementation; existing legacy tests still show the required old behavior.

- [ ] **Step 3: Implement domain and canonical-hash semantics**

In `proposal_types.py`, validate:

```text
artifact_exists / metric_finite -> threshold is None
metric_gte / metric_lte         -> threshold is finite Decimal
```

Update `_validation_protocol_value()` so legacy kinds still canonicalize to `{kind, subject}` and threshold kinds canonicalize to `{kind, subject, threshold: Decimal}`. This preserves old hashes while giving new rules numeric canonical semantics.

- [ ] **Step 4: Implement JSONB serialization compatibility**

In `proposal_service.py`:

```python
def _validation_to_json(rule: ValidationRule) -> dict[str, object]:
    value: dict[str, object] = {"kind": rule.kind, "subject": rule.subject}
    if rule.threshold is not None:
        value["threshold"] = format(rule.threshold, "f")
    return value


def _validation_from_json(value: dict[str, object]) -> ValidationRule:
    raw = value.get("threshold")
    threshold = Decimal(str(raw)) if raw is not None else None
    return ValidationRule(cast(ValidationRuleKind, value["kind"]), str(value["subject"]), threshold)
```

Do not emit `threshold: null` for legacy rules.

- [ ] **Step 5: Extend API schema and Proposal verifier**

`ValidationRuleRequest` accepts the four kinds and `threshold: Decimal | None = None`. Change verifier profile to `slice1-v2`. Add blocking checks for duplicate Artifact `relative_path` and threshold-rule subjects not naming a declared Metric. Keep independent `metric_finite` coverage mandatory.

- [ ] **Step 6: Run GREEN/regression gate**

```bash
uv run pytest \
  tests/unit/experiments/test_proposal_types.py \
  tests/unit/experiments/test_proposal_verifier.py \
  tests/integration/experiments/test_proposal_service.py \
  tests/integration/api/test_slice1_api.py \
  tests/unit/coordinator/test_execution_adapters.py -q
uv run ruff check src/labpilot/experiments src/labpilot/api/schemas/experiments.py tests/unit/experiments
```

- [ ] **Step 7: Commit**

```bash
git add src/labpilot/experiments src/labpilot/api/schemas/experiments.py \
  tests/unit/experiments tests/integration/experiments/test_proposal_service.py \
  tests/unit/coordinator/test_execution_adapters.py tests/integration/api/test_slice1_api.py
git commit -m "feat(slice3): add compatible threshold validation rules"
```

---

### Task 2: Expose one execution-owned Trial settlement contract

**Risk:** Boundary-sensitive; existing Slice2 retry semantics must remain exact.

**Files:**
- Create: `src/labpilot/execution/trial_outcome.py`
- Modify: `src/labpilot/execution/batch_service.py`
- Create tests: `tests/unit/execution/test_trial_outcome.py`
- Modify tests: `tests/integration/test_batch_commands.py`

**Interfaces:**

```python
class TrialExecutionState(StrEnum):
    UNRESOLVED = "unresolved"
    SUCCESSFUL = "successful"
    EXHAUSTED_EXECUTION_FAILURE = "exhausted_execution_failure"
    CANCELLED = "cancelled"

@dataclass(frozen=True, slots=True)
class TrialExecutionOutcome:
    trial_id: UUID
    state: TrialExecutionState
    successful_attempt_id: UUID | None = None
    reason_code: str | None = None

@dataclass(frozen=True, slots=True)
class BatchExecutionOutcomeSnapshot:
    batch_id: UUID
    batch_row_version: int
    attempt_ids: tuple[UUID, ...]
    outcomes: tuple[TrialExecutionOutcome, ...]

class TrialExecutionOutcomeService:
    def __init__(
        self,
        session_context_factory: SessionContextFactory,
        *,
        version_semantics: VersionExecutionSemanticsPort,
    ) -> None: ...

    async def get(self, trial_id: UUID) -> TrialExecutionOutcome: ...
    async def snapshot_batch(self, batch_id: UUID) -> BatchExecutionOutcomeSnapshot: ...
```

- [ ] **Step 1: Write RED tests for exact settlement/retry behavior**

Cover:

```text
no Attempt + started Batch                 -> unresolved
active/preparing/running Attempt           -> unresolved
UNKNOWN / IDENTITY_CONFLICT                -> unresolved
process_exited/0                           -> successful(attempt_id)
cancelled Batch without prior success      -> cancelled
retryable infra failure with budget        -> unresolved
retryable infra failure budget exhausted   -> exhausted_execution_failure
non-retryable terminal failure             -> exhausted_execution_failure
```

In `tests/integration/test_batch_commands.py`, preserve the existing `AttemptCreationService` retry cases and assert the refactor creates exactly the same Attempts as before.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest tests/unit/execution/test_trial_outcome.py \
  tests/integration/test_batch_commands.py -q
```

- [ ] **Step 3: Extract one pure retry predicate and reuse it**

Move the existing logic into a pure execution-owned helper:

```python
def can_create_next_attempt(
    attempts: Sequence[AttemptRow], *, max_retries: int
) -> bool:
    if any(row.terminal_at is None for row in attempts):
        return False
    if any(
        row.terminal_kind == TerminalKind.PROCESS_EXITED.value and row.exit_code == 0
        for row in attempts
    ):
        return False
    if not attempts:
        return True
    if len(attempts) >= 1 + max_retries:
        return False
    return is_retryable_infrastructure_failure(attempts[-1])
```

`AttemptCreationService` and outcome derivation must both call this helper; do not copy the retry codes into Result Plane.

Outcome precedence:

```text
active/unknown/conflict -> unresolved
successful exit0        -> successful
Batch cancelled         -> cancelled
can_create_next_attempt -> unresolved
otherwise               -> exhausted_execution_failure
```

- [ ] **Step 4: Implement `snapshot_batch()`**

Load Batch → Version semantics → Trials ordered by `trial_ordinal` → Attempts ordered by `(trial_id, attempt_number)`. Return all Attempt IDs with the derived outcomes. This is a read value object, not persistence.

- [ ] **Step 5: Run GREEN/regression gate and commit**

```bash
uv run pytest tests/unit/execution tests/integration/test_batch_commands.py -q

git add src/labpilot/execution/trial_outcome.py src/labpilot/execution/batch_service.py \
  tests/unit/execution/test_trial_outcome.py tests/integration/test_batch_commands.py
git commit -m "refactor(execution): expose authoritative trial outcome"
```

---

### Task 3: Add append-only Result Plane tables and the narrow architecture admission

**Risk:** Boundary-sensitive migration.

**Files:**
- Create: `src/labpilot/results_validation/__init__.py`
- Create: `src/labpilot/results_validation/models.py`
- Create: `src/labpilot/results_validation/types.py`
- Create: `migrations/versions/0011_result_plane.py`
- Modify: `src/labpilot/db/model_registry.py`
- Modify: `tests/architecture/dependency_rules.py`
- Modify: `tests/integration/db/test_migrations.py`
- Create: `tests/integration/test_result_plane_schema.py`
- Create: `tests/unit/results_validation/test_models.py`

- [ ] **Step 1: Write RED schema/architecture tests**

Freeze these five tables:

```text
artifacts
metric_observations
validation_reports
trial_result_selections
batch_summary_revisions
```

Use fields from the approved design. `MetricObservationRow` has no duplicate `attempt_id`; provenance is `MetricObservation -> Artifact -> Attempt`. Use PostgreSQL `NUMERIC` for `numeric_value`. JSONB decimal representations use text.

Natural constraints:

```text
UNIQUE artifacts(attempt_id, artifact_name)
UNIQUE artifacts(attempt_id, relative_path)
UNIQUE metric_observations(artifact_id, metric_name, extractor_version)
UNIQUE validation_reports(attempt_id, validation_protocol_hash,
                          extraction_profile_version, validator_version)
PRIMARY KEY trial_result_selections(trial_id)
UNIQUE trial_result_selections(validation_report_id)
UNIQUE batch_summary_revisions(batch_id, revision_number)
CHECK validation verdict in valid/invalid/inconclusive/error
CHECK byte_size >= 0
CHECK revision_number > 0
```

- [ ] **Step 2: Verify RED**

```bash
uv run pytest tests/unit/results_validation/test_models.py \
  tests/integration/test_result_plane_schema.py \
  tests/architecture/test_model_registry.py \
  tests/architecture/test_module_dependencies.py -q
```

- [ ] **Step 3: Implement rows and migration**

`0011_result_plane.py`:

```python
revision = "0011_result_plane"
down_revision = "0010_attempt_physical_execution"
```

All five scientific result tables are append-only and have `created_at`/`selected_at`, never `updated_at`. `warning_json` uses an object such as `{"warnings": [...]}` rather than persisting a Python tuple/Decimal.

- [ ] **Step 4: Register exactly one new model-registry import**

Add `labpilot.results_validation.models` to `src/labpilot/db/model_registry.py`, and add exactly that path to `MODEL_REGISTRY_ALLOWED_IMPORTS` in `tests/architecture/dependency_rules.py`. Do not broaden any unrelated dependency edge.

- [ ] **Step 5: Run fresh migration/architecture gate**

```bash
uv run pytest tests/integration/db/test_migrations.py \
  tests/integration/test_result_plane_schema.py tests/architecture -q
```

- [ ] **Step 6: Commit**

```bash
git add src/labpilot/results_validation migrations/versions/0011_result_plane.py \
  src/labpilot/db/model_registry.py tests/architecture/dependency_rules.py \
  tests/architecture tests/integration/db/test_migrations.py \
  tests/integration/test_result_plane_schema.py tests/unit/results_validation/test_models.py
git commit -m "feat(slice3): add result plane persistence"
```

---

### Task 4: Implement safe Artifact snapshots and strict `result-json-v1`

**Risk:** Safety/provenance critical.

**Files:**
- Create: `src/labpilot/results_validation/artifact_reader.py`
- Create: `src/labpilot/results_validation/extractor.py`
- Expand: `src/labpilot/results_validation/types.py`
- Create tests: `tests/unit/results_validation/test_artifact_reader.py`
- Create tests: `tests/unit/results_validation/test_extractor.py`

**Interfaces:**

```python
MAX_RESULT_JSON_BYTES = 1024 * 1024

@dataclass(frozen=True, slots=True)
class ArtifactSnapshot:
    relative_path: str
    content: bytes
    content_sha256: str
    byte_size: int
    media_type: str

@dataclass(frozen=True, slots=True)
class MissingArtifact:
    relative_path: str
```

- [ ] **Step 1: Write RED safe-read tests**

Use real temp directories to cover regular file, missing leaf, `..`, absolute path, intermediate symlink, leaf symlink, directory leaf, oversize, and deterministic mutation between pre/post `fstat`.

- [ ] **Step 2: Write RED strict JSON tests**

Reject invalid UTF-8, duplicate object keys, `NaN`, `Infinity`, `-Infinity`, booleans as numbers, wrong schema version, wrong top-level keys, undeclared extra metrics, and missing requested metrics. Assert `0.82` extracts as `Decimal("0.82")`.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest tests/unit/results_validation/test_artifact_reader.py \
  tests/unit/results_validation/test_extractor.py -q
```

- [ ] **Step 4: Implement descriptor-based anchored reads**

Open the controlled output root and each child using `os.open(..., dir_fd=..., O_NOFOLLOW)`; intermediate components require `O_DIRECTORY`. Open the leaf once, require regular-file `fstat`, enforce byte limit, read bytes, re-`fstat`, fail closed if stable identity/size changed, and calculate SHA-256 from the captured bytes. Extraction consumes those same captured bytes, never a second pathname open.

`ENOENT` for the leaf means `MissingArtifact`. Unsafe path/symlink/type/oversize/stable-content contradiction is deterministic content/provenance error. Explicitly classified transient filesystem errors raise `ResultProcessingDeferred`; unexpected `OSError` is not silently converted to a scientific verdict.

- [ ] **Step 5: Implement strict parser**

Use strict UTF-8, `parse_float=Decimal`, `parse_int=Decimal`, a `parse_constant` rejector, and `object_pairs_hook` that rejects duplicate keys. Explicitly reject bool and non-finite Decimal.

- [ ] **Step 6: Run GREEN and commit**

```bash
uv run pytest tests/unit/results_validation/test_artifact_reader.py \
  tests/unit/results_validation/test_extractor.py -q
uv run ruff check src/labpilot/results_validation tests/unit/results_validation

git add src/labpilot/results_validation tests/unit/results_validation
git commit -m "feat(slice3): add safe result artifact extraction"
```

---

### Task 5: Implement deterministic scientific, Dataset, and Environment validation

**Risk:** Scientific correctness boundary.

**Files:**
- Create: `src/labpilot/results_validation/validator.py`
- Expand: `src/labpilot/results_validation/types.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create tests: `tests/unit/results_validation/test_validator.py`

**Constants/interfaces:**

```python
EXTRACTION_PROFILE_VERSION = "result-json-v1"
VALIDATOR_VERSION = "slice3-validator-v1"
SUMMARY_PROTOCOL_VERSION = "slice3-summary-v1"

class ValidationVerdict(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"
```

- [ ] **Step 1: Add one explicit runtime dependency for requirement parsing**

Add `packaging>=25,<26`, then:

```bash
uv lock
uv sync --locked --all-groups
```

Do not change FastAPI in this task.

- [ ] **Step 2: Write RED validator tests**

Cover:

```text
required Artifact missing                      -> invalid
metric_finite missing/non-finite               -> invalid
score 0.82 >= 0.80                             -> pass
score 0.61 >= 0.80                             -> invalid
metric_lte symmetric                           -> pass/invalid
best_effort Dataset without stable identity    -> warning only
environment_expectation_hash mismatch          -> error
python_profile known mismatch                  -> invalid
framework missing                              -> invalid
framework version violates Requirement         -> invalid
framework Requirement satisfied                -> pass
required CUDA + device_count=0                 -> invalid
required CUDA + insufficient observation       -> inconclusive
cuda_requirement 12.8 + observed 12080         -> pass
malformed frozen framework Requirement         -> error
```

- [ ] **Step 3: Implement deterministic rules**

Use `packaging.requirements.Requirement`, `canonicalize_name`, and `Version`. Convert observed CUDA integer `12080` to `12.8` using major/minor semantics already implied by the runtime probe.

Sort reason records by `(code, subject)` before persistence. Use stable reason codes including:

```text
REQUIRED_ARTIFACT_MISSING
METRIC_MISSING
METRIC_NONFINITE
METRIC_BELOW_THRESHOLD
METRIC_ABOVE_THRESHOLD
ENVIRONMENT_BINDING_CONFLICT
PYTHON_PROFILE_MISMATCH
FRAMEWORK_MISSING
FRAMEWORK_VERSION_MISMATCH
CUDA_DEVICE_MISMATCH
CUDA_RUNTIME_MISMATCH
ENVIRONMENT_OBSERVATION_INSUFFICIENT
VALIDATION_INPUT_INVALID
```

Warning code: `DATASET_BEST_EFFORT_REPRODUCIBILITY`.

- [ ] **Step 4: Run GREEN/dependency gate and commit**

```bash
uv lock --check
uv run pytest tests/unit/results_validation/test_validator.py -q
uv run ruff check src/labpilot/results_validation tests/unit/results_validation
uv run mypy src

git add pyproject.toml uv.lock src/labpilot/results_validation/validator.py \
  src/labpilot/results_validation/types.py tests/unit/results_validation/test_validator.py
git commit -m "feat(slice3): add deterministic scientific validation"
```

---

### Task 6: Persist Artifact → Metric → Validation → first-eligible selection idempotently

**Risk:** Boundary-sensitive/idempotency/provenance.

**Files:**
- Create: `src/labpilot/results_validation/service.py`
- Create tests: `tests/unit/results_validation/test_service.py`
- Create tests: `tests/integration/test_result_processing_service.py`

**Interfaces:**

```python
class ResultProcessingDeferred(RuntimeError):
    pass

@dataclass(frozen=True, slots=True)
class ProcessAttemptResult:
    attempt_id: UUID
    validation_report_id: UUID
    verdict: ValidationVerdict
    selected: bool

class ResultApplicationService:
    async def process_attempt(self, attempt_id: UUID) -> ProcessAttemptResult | None: ...
```

`None` means the Attempt is not eligible for scientific processing. A known deferrable filesystem condition raises `ResultProcessingDeferred`. Database/connectivity exceptions propagate; they are not swallowed or written as scientific `error`.

- [ ] **Step 1: Write RED integration scenarios**

Prove:

```text
nonzero exit -> no Result rows
exit0 + score .82 -> Artifact + Metric + valid report + selection
exit0 + score .61 threshold .80 -> invalid report, no selection
missing required result.json -> invalid report, no Artifact row
best_effort Dataset -> persisted warning
same process_attempt twice unchanged -> no duplicates
registered bytes mutate before report -> deterministic error
report exists then bytes mutate -> historical report/selection immutable; reprocess fails closed
known transient read error -> ResultProcessingDeferred, no report; later retry succeeds
later valid semantic interpretation -> existing TrialResultSelection unchanged
```

- [ ] **Step 2: Verify RED**

```bash
uv run pytest tests/unit/results_validation/test_service.py \
  tests/integration/test_result_processing_service.py -q
```

- [ ] **Step 3: Implement exact Result contract loading without new hashes**

Load `Attempt -> Trial -> Batch -> ExperimentVersion -> ProposalRevision`. Parse the frozen Proposal and recheck existing bindings with current helpers:

```text
revision.payload_hash == version.proposal_payload_hash
proposal_payload_hash(parsed Proposal) == version.proposal_payload_hash
Dataset parsed/frozen identity == version.dataset_reference_hash
Environment parsed/frozen identity == version.environment_expectation_hash
Validation parsed/frozen identity == version.validation_protocol_hash
```

Reuse these existing hashes only. Do not create a ResultContract hash.

- [ ] **Step 4: Observe declared files before persistence**

For each declared Artifact, read only its declared path beneath `<workspace>/runtime/output`. For each declared Metric, use its declared source Artifact and the captured bytes. Never scan the directory for guessed outputs.

- [ ] **Step 5: Persist under one UoW with rechecks**

Inside one UoW:

```text
SELECT Attempt FOR UPDATE
recheck terminal/process_exited/0
load Trial + Batch
reconcile Artifact natural slots
insert MetricObservation natural slots
build JSON-safe input_snapshot_json
run deterministic validator
reuse same-version report only if its structured input is identical
otherwise insert one immutable report
if valid: SELECT Trial FOR UPDATE and insert selection only if absent
append bounded RuntimeEvents
commit
```

All Decimal values placed in JSONB/event payloads are canonical decimal text. Artifact/Metric authoritative numeric data remains in digest/NUMERIC columns.

Emit Batch-scoped system RuntimeEvents with `enqueue_outbox=False`:

```text
result.artifact_registered
result.validation_completed
result.trial_selected
```

Payloads contain IDs, reason codes, metric names and decimal text only; never raw Artifact content, secret data, or host absolute paths.

- [ ] **Step 6: Run GREEN/idempotency gate and commit**

```bash
uv run pytest tests/unit/results_validation \
  tests/integration/test_result_processing_service.py -q

git add src/labpilot/results_validation/service.py src/labpilot/results_validation/types.py \
  tests/unit/results_validation/test_service.py tests/integration/test_result_processing_service.py
git commit -m "feat(slice3): process deterministic attempt results"
```

---

### Task 7: Generate immutable BatchSummary revisions from execution-owned settlement

**Risk:** Scientific summary correctness.

**Files:**
- Create: `src/labpilot/results_validation/summary_service.py`
- Create tests: `tests/unit/results_validation/test_summary_service.py`
- Create tests: `tests/integration/test_batch_summary_service.py`

**Interface:**

```python
@dataclass(frozen=True, slots=True)
class RefreshSummaryResult:
    batch_id: UUID
    revision_id: UUID | None
    created: bool
    presentation_status: Literal[
        "not_ready", "complete", "complete_with_invalid_results"
    ]
```

- [ ] **Step 1: Write RED summary tests**

Cover unresolved/no revision, included selection, invalid/inconclusive/error exclusions, exhausted execution failure, cancellation, Dataset warning propagation, deterministic `count/mean`, unchanged-input no-op, and revision increment when settled inputs change.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest tests/unit/results_validation/test_summary_service.py \
  tests/integration/test_batch_summary_service.py -q
```

- [ ] **Step 3: Consume a Batch execution snapshot rather than reimplementing retry rules**

Call `TrialExecutionOutcomeService.snapshot_batch(batch_id)`. If any outcome is unresolved, return `not_ready` without a formal revision.

Before inserting a formal revision, open a UoW, lock `BatchRow`, require its `row_version` to match the snapshot, and re-query all Attempt IDs for the Batch. If current Attempt IDs differ from `snapshot.attempt_ids`, abort this refresh as `not_ready` and let the next deterministic scan recompute. Do not introduce an input hash or a lease.

- [ ] **Step 4: Build structured summary content**

Trials are ordered by `trial_ordinal`. Included refs store Trial/selected Attempt/ValidationReport IDs and metric values as decimal text. Excluded entries store stable reason codes. Aggregates use Decimal internally and JSON text externally:

```json
{"score":{"count":1,"mean":"0.82"}}
```

Compare `(included, excluded, warnings, aggregates, SUMMARY_PROTOCOL_VERSION)` against the latest revision; insert only when structurally different. No summary hash.

Presentation:

```text
unresolved exists                    -> not_ready
settled and every Trial included     -> complete
settled and any Trial excluded       -> complete_with_invalid_results
```

Emit `result.summary_created` on the Batch aggregate in the same transaction.

- [ ] **Step 5: Run GREEN and commit**

```bash
uv run pytest tests/unit/results_validation/test_summary_service.py \
  tests/integration/test_batch_summary_service.py -q

git add src/labpilot/results_validation/summary_service.py \
  tests/unit/results_validation/test_summary_service.py \
  tests/integration/test_batch_summary_service.py
git commit -m "feat(slice3): add immutable batch summaries"
```

---

### Task 8: Trigger deterministic result recovery from Coordinator scans

**Risk:** Recovery boundary; Slice2 launch behavior must remain unchanged.

**Files:**
- Modify: `src/labpilot/coordinator/service.py`
- Modify: `src/labpilot/coordinator/worker.py`
- Modify tests: `tests/unit/coordinator/test_service.py`
- Modify tests: `tests/unit/coordinator/test_worker.py`
- Modify tests: `tests/integration/test_coordinator_reconciliation.py`
- Regression: `tests/integration/test_attempt_execution_service.py`

- [ ] **Step 1: Write RED ordering/restart tests**

Expected scan order:

```text
control existing
-> reconcile physical
-> process successful terminal results
-> prepare eligible retry/new Attempt
-> admit launch-ready
-> refresh candidate Batch summaries
```

Prove `ResultProcessingDeferred` for one Attempt is recorded/logged and retried on a later scan without changing the Attempt. Do not catch broad database exceptions as if they were deferrable scientific work.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest tests/unit/coordinator/test_service.py \
  tests/unit/coordinator/test_worker.py \
  tests/integration/test_coordinator_reconciliation.py -q
```

- [ ] **Step 3: Add narrow Result/Summary dependencies**

Construct one `VersionExecutionSemanticsAdapter`, one `TrialExecutionOutcomeService`, one `ResultApplicationService`, and one `BatchSummaryService` in `build_coordinator()`; no additional worker/job queue.

Coordinator selects only terminal `process_exited/0` Attempts that do not yet have a report under the active semantic versions. `ResultApplicationService` remains responsible for final eligibility/recheck.

- [ ] **Step 4: Run Slice2 regressions and commit**

```bash
uv run pytest tests/unit/coordinator tests/unit/execution \
  tests/integration/test_coordinator_reconciliation.py \
  tests/integration/test_attempt_execution_service.py -q

git add src/labpilot/coordinator/service.py src/labpilot/coordinator/worker.py \
  tests/unit/coordinator/test_service.py tests/unit/coordinator/test_worker.py \
  tests/integration/test_coordinator_reconciliation.py
git commit -m "feat(slice3): reconcile results in coordinator scans"
```

---

### Task 9: Expose authoritative Result Plane read APIs

**Risk:** Routine API boundary; read-only public surface.

**Files:**
- Create: `src/labpilot/results_validation/query_service.py`
- Create: `src/labpilot/api/schemas/results.py`
- Create: `src/labpilot/api/routers/results.py`
- Modify: `src/labpilot/api/dependencies.py`
- Modify: `src/labpilot/api/app.py`
- Modify: `src/labpilot/api/errors.py`
- Modify: `src/labpilot/main.py`
- Create tests: `tests/unit/results_validation/test_query_service.py`
- Create tests: `tests/integration/api/test_results_api.py`

**Endpoints:**

```text
GET /attempts/{attempt_id}/results
GET /trials/{trial_id}/result
GET /batches/{batch_id}/summary
```

- [ ] **Step 1: Write RED query/API tests**

Responses must show Artifact name/path/digest/size/current availability, Metric value/extractor version, Dataset/Environment warnings, Validation verdict/reasons, Trial selection, and latest Batch included/excluded/aggregates. Confirm there is no public `POST /process-results`, `/validate`, or `/generate-summary`.

- [ ] **Step 2: Implement historical truth plus derived Artifact availability**

Historical rows are never rewritten. Current availability is derived by the same safe reader:

```text
available  -> current bytes match registered digest
missing    -> registered leaf is absent
corrupted  -> unsafe path/type or digest mismatch
```

A transient filesystem error is surfaced as service unavailable/internal operational failure; it does not rewrite availability or scientific history.

- [ ] **Step 3: Compose API services and stable errors**

Add `ResultQueryService` to `ApplicationServices`, router composition, and aggregate-specific 404 handling. Do not expose absolute workspace paths or raw Artifact bytes.

- [ ] **Step 4: Run GREEN/API gate and commit**

```bash
uv run pytest tests/unit/results_validation/test_query_service.py \
  tests/integration/api/test_results_api.py tests/integration/api/test_slice1_api.py -q
uv run ruff check src/labpilot/api src/labpilot/results_validation

git add src/labpilot/results_validation/query_service.py src/labpilot/api \
  src/labpilot/main.py tests/unit/results_validation/test_query_service.py \
  tests/integration/api/test_results_api.py
git commit -m "feat(slice3): expose result provenance queries"
```

---

### Task 10: Extend the real training fixture and prove the PostgreSQL Result Loop

**Risk:** Acceptance-fixture compatibility.

**Files:**
- Modify: `tests/acceptance/slice2/training_fixture/train.py`
- Create: `tests/unit/results_validation/test_training_fixture.py`
- Create: `tests/integration/slice3/__init__.py`
- Create: `tests/integration/slice3/test_result_loop.py`

- [ ] **Step 1: Write RED fixture tests**

Without `--score`, existing `workload-test-oracle.json` behavior is unchanged and no `result.json` is written. With `--score 0.82`, write a separate:

```json
{"schema_version":"v1","metrics":{"score":0.82}}
```

Reject non-finite score input.

- [ ] **Step 2: Implement optional score without changing Slice2 oracle semantics**

Parse `--score` as a finite decimal text value, validate it with `Decimal`, then write the JSON number using a deterministic helper rather than serializing the `Decimal` object directly. For the bounded fixture, generate exact UTF-8 bytes:

```python
payload = (
    '{"schema_version":"v1","metrics":{"score":' + normalized_score + '}}\n'
).encode("utf-8")
```

where `normalized_score` is produced by the same canonical finite-decimal rendering rule used by project canonicalization. Atomically write sibling temp file → `fsync` → `os.replace`. Existing calls without `--score` do not change.

- [ ] **Step 3: Write the real PostgreSQL two-Trial integration**

Proposal contract:

```text
Artifact result -> result.json required
Metric score -> result
artifact_exists(result)
metric_finite(score)
metric_gte(score, 0.80)
```

Trial A writes `0.82`; Trial B writes `0.61`. Create successful terminal Attempts with observed environments, then run Result + Summary services:

```text
A -> valid -> selected -> included
B -> invalid METRIC_BELOW_THRESHOLD -> no selection -> excluded
summary -> complete_with_invalid_results
```

Reconstruct service objects and run again to prove no duplicate Artifact/Metric/Validation/selection/unchanged Summary revision.

- [ ] **Step 4: Run GREEN plus Slice2 fixture regression and commit**

```bash
uv run pytest tests/unit/results_validation/test_training_fixture.py \
  tests/integration/slice3/test_result_loop.py \
  tests/acceptance/slice2/test_native_slice2.py -q

git add tests/acceptance/slice2/training_fixture/train.py \
  tests/unit/results_validation/test_training_fixture.py tests/integration/slice3
git commit -m "test(slice3): add deterministic result loop fixture"
```

---

### Task 11: Upgrade FastAPI separately and add minimal Batch result SSE

**Risk:** Dependency/API compatibility; separate tranche.

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Expand: `src/labpilot/results_validation/query_service.py`
- Modify: `src/labpilot/api/routers/results.py`
- Modify: `src/labpilot/api/schemas/results.py`
- Create tests: `tests/integration/api/test_result_events_sse.py`

**Endpoint:** `GET /batches/{batch_id}/events`

- [ ] **Step 1: Upgrade dependency only**

Set:

```toml
"fastapi>=0.135,<0.136",
```

then run before writing SSE code:

```bash
uv lock
uv sync --locked --all-groups
uv run pytest tests/unit tests/integration/api -q
uv run mypy src scripts
```

If compatibility fails, debug that regression before SSE. Do not maintain custom SSE framing merely to preserve the historical FastAPI pin.

- [ ] **Step 2: Commit dependency alignment separately**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: align FastAPI for native SSE"
```

- [ ] **Step 3: Write RED SSE resume tests**

Using existing Batch `RuntimeEventRow` entries, prove `text/event-stream`, event ID = `aggregate_sequence`, event name = `event_type`, bounded JSON data, and `Last-Event-ID` resumes strictly after the acknowledged sequence.

- [ ] **Step 4: Implement official FastAPI SSE path**

Use:

```python
from fastapi.sse import EventSourceResponse, ServerSentEvent
```

`ResultQueryService.list_batch_events_after()` queries authoritative `runtime_events` by Batch aggregate, sequence `> after_sequence`, ordered ascending, bounded to 100.

The SSE generator polls this query, yields `ServerSentEvent(id=..., event=..., data=...)`, checks `request.is_disconnected()`, and sleeps 0.5 seconds between empty polls. No Projection Worker or event projection table is introduced.

- [ ] **Step 5: Run GREEN/API gate and commit**

```bash
uv run pytest tests/integration/api/test_result_events_sse.py tests/integration/api -q
uv run ruff check src tests
uv run mypy src scripts

git add src/labpilot/results_validation/query_service.py src/labpilot/api/routers/results.py \
  src/labpilot/api/schemas/results.py tests/integration/api/test_result_events_sse.py
git commit -m "feat(slice3): stream batch result events over SSE"
```

---

### Task 12: Validate the native Slice3 MVP and freeze bounded evidence

**Risk:** Slice-boundary acceptance. This task validates the implemented MVP; it does not reopen production-hardening platforms.

**Files:**
- Create: `tests/acceptance/slice3/__init__.py`
- Create: `tests/acceptance/slice3/native_smoke.py`
- Create: `tests/acceptance/slice3/test_native_slice3.py`
- Create only after a successful run: `docs/superpowers/evidence/2026-09-01-labpilot-v1-slice3-mvp-smoke.md`
- Modify only after fresh evidence: `DOCUMENT-STATUS.md`

- [ ] **Step 1: Write native acceptance before running the host**

Use the real API + Coordinator + PostgreSQL + Candidate-B Runner topology. Create a real Batch with two Trials whose argv passes score as a parameter:

```text
Trial A score 0.82
Trial B score 0.61
rule score >= 0.80
```

Public API assertions:

```text
both Attempts process_exited/0
A Validation valid, selected, included
B Validation invalid, not selected, excluded with METRIC_BELOW_THRESHOLD
Batch summary complete_with_invalid_results
Artifact digest and extractor version visible
```

- [ ] **Step 2: Add bounded recovery/fault assertions**

During real training/result lifecycle:

```text
restart Coordinator while workload active
-> reconcile original physical execution
-> no duplicate physical launch
wait terminal
restart Coordinator during/after deterministic result processing
-> no duplicate Artifact/Metric/Validation/selection/unchanged Summary
```

Do not rerun the historical N1-N7/V2/F01-F22 campaign.

- [ ] **Step 3: Add provenance/security assertions**

On controlled extra workspaces prove:

```text
symlink/traversal result path rejected
post-registration/pre-validation mutation -> deterministic fail-closed/error
post-validation mutation -> historical report/selection/summary unchanged; query shows corrupted
RuntimeEvent/SSE payload contains no raw result bytes or secrets
```

- [ ] **Step 4: Record data-egress review**

Evidence states Demo Repository/Dataset classification, `result.json` contains synthetic numeric metrics only, Slice3 Result Plane makes no provider call, secrets do not enter Artifact/Validation/Summary/RuntimeEvent/SSE payload, and API exposure remains loopback/controlled-network according to deployment policy.

- [ ] **Step 5: Run full candidate gate**

```bash
uv lock --check
uv run ruff check src tests scripts
uv run mypy src scripts
uv run pytest tests/unit tests/architecture -v
uv run pytest tests/integration -v -m integration
./tests/scripts/test_verify_architecture_dependencies.sh
./scripts/check_ci.sh
```

Then run dedicated native Slice3 acceptance. Push the candidate branch and require exact-HEAD GitHub Actions `ci` success before claiming the candidate passes CI.

- [ ] **Step 6: Freeze bounded evidence and status**

Evidence wording:

```text
Slice3 MVP / Result Loop validated
```

Never claim Slice2 production acceptance or full production hardening. Record exact candidate commit, commands, pass/fail counts, Batch/Trial/Attempt identifiers needed for reproduction, valid/invalid outcomes, restart observation, and no-duplicate cardinalities. Do not add document SHA manifests.

Only after fresh evidence exists, update `DOCUMENT-STATUS.md` to:

```text
Slice3 implementation: Validated / candidate ready
V1 Core Demo next action: authorized
Slice2 production hardening: still deferred
```

- [ ] **Step 7: Commit acceptance artifacts**

```bash
git add tests/acceptance/slice3 \
  docs/superpowers/evidence/2026-09-01-labpilot-v1-slice3-mvp-smoke.md \
  DOCUMENT-STATUS.md
git commit -m "docs(slice3): close out result loop MVP evidence"
```

---

## Plan Self-Review Checklist

Before implementation authorization, the focused plan review must verify:

```text
[ ] Artifact provenance and current availability are covered
[ ] Metric extractor semantic version is persisted
[ ] Dataset best_effort warning reaches Validation + Summary
[ ] EnvironmentExpectation consumes Attempt observed_environment
[ ] exit!=0 never becomes scientific invalid
[ ] Execution owns retry/exhaustion through one shared predicate/service
[ ] Existing validation JSON/hash shape is preserved
[ ] New threshold is stored JSON-safely and canonicalized deterministically
[ ] transient operational failure commits no scientific verdict
[ ] first-eligible TrialResultSelection is immutable
[ ] post-validation Artifact mutation cannot rewrite history
[ ] BatchSummary consumes execution-owned settlement and checks snapshot staleness
[ ] deterministic rerun covers Coordinator restart without ProcessingJob
[ ] safe-read rejects traversal/symlinks and binds digest+parse to one snapshot
[ ] results_validation namespace and model-registry guard remain narrow
[ ] public read APIs expose provenance and exclusion reasons
[ ] FastAPI dependency refresh is isolated before SSE
[ ] SSE is notification-only; authoritative tables remain truth
[ ] real-training valid+invalid E2E is included
[ ] recovery/fault/security/provenance/data-egress candidate gates are included
[ ] no CAS/Projection/Processing platform/LLM result judge is introduced
[ ] no new bookkeeping hash graph is introduced
[ ] every referenced pre-existing test/file path exists on planning baseline
```

## Implementation Authorization Gate

This plan does **not** authorize production code. Run one focused plan review against:

```text
planning baseline ea3d5ced1c886e6484f1ab2a372c0dab547a4deb
+ approved Slice3 design
+ focused design re-review
+ S2→S3 Sequencing Amendment v0.3.1
+ this Detailed Implementation Plan
```

Only a focused-plan verdict with `Critical=0`, `Blocking=0`, and no unresolved Important issue may authorize creation of the isolated Slice3 implementation branch/worktree.
