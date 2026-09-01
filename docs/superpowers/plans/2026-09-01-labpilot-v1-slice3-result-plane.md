# LabPilot V1 Slice3 Result Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the V1 result loop from a successful terminal Attempt through declared Artifact collection, deterministic Metric extraction and Validation, immutable Trial selection, BatchSummary, read APIs, and a minimal SSE update surface.

**Architecture:** Keep Execution Plane authority separate from scientific Result Plane authority. Add the already-admitted `labpilot.results_validation` owner with append-only result tables and deterministic rerun; Coordinator only triggers processing, while execution continues to own retry/exhaustion. Preserve existing Proposal/ExperimentVersion hashes, use SHA-256 only for Artifact byte integrity, and do not add ProcessingJob, CAS, Projection Worker, generic result platform, or LLM scientific eligibility decisions.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy asyncio, PostgreSQL 16, Alembic, asyncpg, pytest, Ruff, Mypy, systemd-backed Slice2 Runner for native acceptance.

**Spec:** `docs/superpowers/specs/2026-08-31-labpilot-v1-slice3-result-plane-design.md`

**Focused review:** `docs/superpowers/specs/2026-09-01-labpilot-v1-slice3-result-plane-focused-rereview.md`

**Sequencing authority:** `docs/labpilot/implementation/01a-slice2-to-slice3-sequencing-amendment-v0.3.1.md`

**Planning baseline:** `ea3d5ced1c886e6484f1ab2a372c0dab547a4deb`

## Global Constraints

- Slice3 implementation branch/worktree must descend from planning baseline `ea3d5ced1c886e6484f1ab2a372c0dab547a4deb` after this plan is approved/integrated into the implementation base.
- Scientific processing starts only for `Attempt.status == terminal`, `terminal_kind == process_exited`, `exit_code == 0`.
- Execution failure/retry/exhaustion/cancellation remains owned by `labpilot.execution`; Result Plane must not duplicate `max_retries` or retryability policy.
- `TrialResultSelection` is an immutable first-eligible binding; later extractor/validator semantic versions do not automatically reselect historical Trials.
- Preserve historical canonical JSON for `artifact_exists` and `metric_finite`; adding `threshold=None` in memory must not change old Proposal/Validation/ExperimentVersion hashes.
- Do not add `input_identity_hash`, `summary_hash`, `metric_hash`, `selection_hash`, or other bookkeeping hashes. Reuse existing upstream hashes; add only `Artifact.content_sha256` for physical content integrity.
- Artifact safe-read must reject traversal and symlinks and must hash/extract from one stable opened byte snapshot.
- `best_effort` Dataset semantics create an explicit reproducibility warning in Validation and Summary.
- Known required environment mismatch is `invalid`; insufficient observation is `inconclusive`; deterministic provenance/content contradiction is `error`; transient DB/filesystem/internal operational failure commits no ValidationReport.
- Result processing is deterministic-rerun based. Do not introduce ProcessingJob unless this plan is formally amended after a demonstrated failure mode.
- Use the existing `results_validation` architecture namespace. Do not create `labpilot.results`.
- Full Artifact CAS/object storage, Projection Worker, historical reselection, advanced statistics, and LLM result interpretation remain deferred.
- FastAPI SSE dependency refresh is isolated late in the Slice; do not mix it into the core Result Plane commits.
- Each task uses TDD: failing focused test → minimal implementation → focused pass → relevant regression → commit → review gate.

---

## File Structure Map

### New production files

```text
src/labpilot/results_validation/
  __init__.py                package marker
  models.py                  five authoritative SQLAlchemy result tables
  types.py                   enums/value objects/result contracts
  artifact_reader.py         anchored no-symlink bounded Artifact byte reads
  extractor.py               strict result-json-v1 parser and metric extraction
  validator.py               pure deterministic scientific/environment validation
  service.py                 Attempt processing, persistence, first-eligible selection
  summary_service.py         immutable BatchSummary revision generation
  query_service.py           read models, Artifact availability, result-event reads

src/labpilot/execution/trial_outcome.py
                              execution-owned retry/exhaustion derived contract

src/labpilot/api/routers/results.py
src/labpilot/api/schemas/results.py

migrations/versions/0011_result_plane.py
```

### Existing files expected to change

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

### Task 1: Extend ValidationRule without breaking historical Version bindings

**Risk:** Boundary-sensitive; historical compatibility blocker.

**Files:**
- Modify: `src/labpilot/experiments/proposal_types.py`
- Modify: `src/labpilot/experiments/proposal_service.py`
- Modify: `src/labpilot/experiments/proposal_verifier.py`
- Modify: `src/labpilot/api/schemas/experiments.py`
- Test: `tests/unit/experiments/test_proposal_types.py`
- Test: `tests/unit/experiments/test_proposal_verifier.py`
- Test: `tests/integration/experiments/test_proposal_service.py`
- Test: `tests/integration/api/test_slice1_api.py`

**Interfaces:**
- Produces: `ValidationRule(kind, subject, threshold: Decimal | None = None)`.
- Produces validation kinds: `artifact_exists`, `metric_finite`, `metric_gte`, `metric_lte`.
- Preserves legacy JSON/hash shape for the first two kinds.
- Produces a new Proposal verifier profile, `slice1-v2`.

- [ ] **Step 1: Add failing historical-compatibility and threshold-domain tests**

Add tests equivalent to:

```python
from decimal import Decimal


def test_legacy_validation_rules_keep_exact_json_and_payload_hash() -> None:
    payload = _payload()
    before = proposal_payload_hash(payload)
    stored = proposal_service._payload_to_json(payload)

    assert stored["validation_rules"] == [
        {"kind": "artifact_exists", "subject": "model"},
        {"kind": "metric_finite", "subject": "accuracy"},
    ]
    assert before == "de7b31452c083fa70fc986fc05938dd16f6595ae0427aa3f14f8f5a56d6d3ba1"

    loaded = proposal_service._payload_from_json(stored)
    assert all(rule.threshold is None for rule in loaded.validation_rules)
    assert proposal_payload_hash(loaded) == before


def test_threshold_rule_serializes_threshold_only_for_new_kind() -> None:
    rule = ValidationRule("metric_gte", "accuracy", Decimal("0.8"))
    payload = replace(_payload(), validation_rules=(*_rules(), rule))
    stored = proposal_service._payload_to_json(payload)
    assert stored["validation_rules"][-1] == {
        "kind": "metric_gte",
        "subject": "accuracy",
        "threshold": Decimal("0.8"),
    }


def test_threshold_rule_requires_finite_threshold() -> None:
    with pytest.raises(ValueError, match="threshold"):
        ValidationRule("metric_gte", "accuracy", None)
    with pytest.raises(ValueError, match="threshold"):
        ValidationRule("metric_gte", "accuracy", Decimal("NaN"))
```

Also add a regression that round-trips a pre-Slice3 stored Proposal through `VersionExecutionSemanticsAdapter.load()` and still passes `PROPOSAL_BINDING_MISMATCH` checks.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
uv run pytest \
  tests/unit/experiments/test_proposal_types.py \
  tests/unit/experiments/test_proposal_verifier.py \
  tests/integration/experiments/test_proposal_service.py \
  tests/unit/coordinator/test_execution_adapters.py -q
```

Expected: failures because `ValidationRule` has no threshold kinds/field and legacy-aware serialization is absent.

- [ ] **Step 3: Implement the backward-compatible domain/canonicalization contract**

Implement the domain shape in `proposal_types.py`:

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

    def __post_init__(self) -> None:
        threshold_kind = self.kind in {"metric_gte", "metric_lte"}
        if threshold_kind:
            if self.threshold is None or not self.threshold.is_finite():
                raise ValueError("threshold rules require a finite threshold")
        elif self.threshold is not None:
            raise ValueError("legacy validation rules do not accept threshold")


def _validation_rule_value(rule: ValidationRule) -> dict[str, CanonicalValue]:
    value: dict[str, CanonicalValue] = {
        "kind": rule.kind,
        "subject": rule.subject,
    }
    if rule.kind in {"metric_gte", "metric_lte"}:
        assert rule.threshold is not None
        value["threshold"] = rule.threshold
    return value
```

Use `_validation_rule_value()` in `_validation_protocol_value()`.

In `proposal_service.py`, make `_validation_to_json()` use the same conditional key rule and `_validation_from_json()` use:

```python
threshold_raw = value.get("threshold")
threshold = Decimal(str(threshold_raw)) if threshold_raw is not None else None
```

Do not emit `"threshold": None` for legacy rules.

- [ ] **Step 4: Extend Proposal verification and API schema**

Update `ValidationRuleRequest` to:

```python
class ValidationRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["artifact_exists", "metric_finite", "metric_gte", "metric_lte"]
    subject: str
    threshold: Decimal | None = None
```

Construct `ValidationRule(item.kind, item.subject, item.threshold)` in `to_domain()`.

In `ProposalVerifier`:

```python
VERIFIER_PROFILE = "slice1-v2"
```

Add checks that:
- every Artifact `name` is unique;
- every Artifact `relative_path` is unique;
- every threshold rule subject names a declared Metric;
- every Metric still has separate `metric_finite` coverage;
- threshold rule validity comes from the domain object and is not used as a substitute for finite coverage.

- [ ] **Step 5: Run focused and Slice1 regression tests**

Run:

```bash
uv run pytest \
  tests/unit/experiments/test_proposal_types.py \
  tests/unit/experiments/test_proposal_verifier.py \
  tests/integration/experiments/test_proposal_service.py \
  tests/integration/api/test_slice1_api.py \
  tests/unit/coordinator/test_execution_adapters.py -q
```

Expected: PASS, including the frozen historical payload hash.

- [ ] **Step 6: Commit Task 1**

```bash
git add \
  src/labpilot/experiments/proposal_types.py \
  src/labpilot/experiments/proposal_service.py \
  src/labpilot/experiments/proposal_verifier.py \
  src/labpilot/api/schemas/experiments.py \
  tests/unit/experiments/test_proposal_types.py \
  tests/unit/experiments/test_proposal_verifier.py \
  tests/integration/experiments/test_proposal_service.py \
  tests/integration/api/test_slice1_api.py \
  tests/unit/coordinator/test_execution_adapters.py
git commit -m "feat(slice3): add compatible threshold validation rules"
```

---

### Task 2: Make Trial settlement a single execution-owned contract

**Risk:** Boundary-sensitive; must preserve Slice2 retry behavior exactly.

**Files:**
- Create: `src/labpilot/execution/trial_outcome.py`
- Modify: `src/labpilot/execution/batch_service.py`
- Test: `tests/unit/execution/test_trial_outcome.py`
- Test: `tests/integration/test_attempt_creation_service.py`

**Interfaces:**

```python
class TrialExecutionState(StrEnum):
    UNRESOLVED = "unresolved"
    SUCCESSFUL = "successful"
    EXHAUSTED_EXECUTION_FAILURE = "exhausted_execution_failure"
    CANCELLED = "cancelled"

@dataclass(frozen=True, slots=True)
class TrialExecutionOutcome:
    state: TrialExecutionState
    successful_attempt_id: UUID | None = None
    reason_code: str | None = None

class TrialExecutionOutcomeService:
    async def get(self, trial_id: UUID) -> TrialExecutionOutcome: ...
```

- [ ] **Step 1: Write RED tests for outcome and retry parity**

Cover exactly:

```python
# no Attempt, started Batch -> UNRESOLVED
# active Attempt -> UNRESOLVED
# UNKNOWN or IDENTITY_CONFLICT -> UNRESOLVED
# first process_exited/0 -> SUCCESSFUL(attempt_id)
# cancelled Batch without success -> CANCELLED
# retryable infra failure with budget -> UNRESOLVED
# retryable infra failure with exhausted budget -> EXHAUSTED_EXECUTION_FAILURE
# non-retryable terminal failure -> EXHAUSTED_EXECUTION_FAILURE
```

Add an integration regression proving `AttemptCreationService.create_attempt_for_trial()` still creates exactly the same retry set as before this refactor.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest \
  tests/unit/execution/test_trial_outcome.py \
  tests/integration/test_attempt_creation_service.py -q
```

Expected: new outcome module missing.

- [ ] **Step 3: Implement one shared retry/settlement decision**

Use a pure helper shared by `AttemptCreationService` and `TrialExecutionOutcomeService`:

```python
class _AttemptProjection(Protocol):
    attempt_id: UUID
    status: str
    terminal_at: datetime | None
    terminal_kind: str | None
    exit_code: int | None
    preparation_failure_code: str | None
    execution_issue_code: str | None


def can_create_next_attempt(
    attempts: Sequence[_AttemptProjection], *, max_retries: int
) -> bool:
    if any(attempt.terminal_at is None for attempt in attempts):
        return False
    if any(
        attempt.terminal_kind == TerminalKind.PROCESS_EXITED.value
        and attempt.exit_code == 0
        for attempt in attempts
    ):
        return False
    if not attempts:
        return True
    if len(attempts) >= 1 + max_retries:
        return False
    return is_retryable_infrastructure_failure(attempts[-1])
```

`derive_trial_execution_outcome()` must use `can_create_next_attempt()` rather than copy retry rules.

Precedence:

```text
active/unknown/conflict -> unresolved
successful exit0        -> successful
cancelled batch         -> cancelled
can create next attempt -> unresolved
otherwise               -> exhausted_execution_failure
```

Use the last terminal failure code as `reason_code`, preferring `preparation_failure_code`, then `execution_issue_code`, then `terminal_kind`, else `retry_budget_exhausted`.

Refactor `AttemptCreationService` to call `can_create_next_attempt()` inside its existing Trial/Batch locks.

- [ ] **Step 4: Run focused plus Slice2 retry regressions**

```bash
uv run pytest \
  tests/unit/execution/test_trial_outcome.py \
  tests/unit/execution \
  tests/integration/test_attempt_creation_service.py -q
```

Expected: PASS with no behavior change in attempt creation.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/labpilot/execution/trial_outcome.py \
  src/labpilot/execution/batch_service.py \
  tests/unit/execution/test_trial_outcome.py \
  tests/integration/test_attempt_creation_service.py
git commit -m "refactor(execution): expose authoritative trial outcome"
```

---

### Task 3: Add the append-only Result Plane schema and architecture admission

**Risk:** Boundary-sensitive database migration.

**Files:**
- Create: `src/labpilot/results_validation/__init__.py`
- Create: `src/labpilot/results_validation/models.py`
- Create: `src/labpilot/results_validation/types.py`
- Create: `migrations/versions/0011_result_plane.py`
- Modify: `src/labpilot/db/model_registry.py`
- Modify: `tests/architecture/dependency_rules.py`
- Test: `tests/unit/results_validation/test_models.py`
- Test: `tests/integration/test_result_plane_migration.py`

**Interfaces:** Five persisted facts only: `ArtifactRow`, `MetricObservationRow`, `ValidationReportRow`, `TrialResultSelectionRow`, `BatchSummaryRevisionRow`.

- [ ] **Step 1: Write RED model/architecture/migration tests**

Assert table/constraint names explicitly, including:

```text
uq_artifacts_attempt_name
uq_artifacts_attempt_relative_path
uq_metric_observations_artifact_metric_extractor
uq_validation_reports_attempt_semantics
uq_trial_result_selections_validation_report
uq_batch_summary_revisions_batch_revision
ck_validation_reports_verdict
ck_batch_summary_revisions_revision_positive
```

Also assert the architecture guard permits only `labpilot.results_validation.models` as the new `model_registry.py` import; do not broaden unrelated edges.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest \
  tests/unit/results_validation/test_models.py \
  tests/integration/test_result_plane_migration.py \
  tests/architecture -q
```

- [ ] **Step 3: Implement SQLAlchemy rows**

Use these fields exactly:

```python
class ArtifactRow(Base):
    artifact_id: UUID
    attempt_id: UUID
    artifact_name: str
    relative_path: str
    content_sha256: str
    byte_size: int
    media_type: str
    created_at: datetime

class MetricObservationRow(Base):
    metric_observation_id: UUID
    artifact_id: UUID
    metric_name: str
    split: str | None
    numeric_value: Decimal
    extractor_version: str
    created_at: datetime

class ValidationReportRow(Base):
    validation_report_id: UUID
    attempt_id: UUID
    validation_protocol_hash: str
    extraction_profile_version: str
    validator_version: str
    input_snapshot_json: dict[str, object]
    verdict: str
    reason_json: dict[str, object]
    warning_json: dict[str, object]
    created_at: datetime

class TrialResultSelectionRow(Base):
    trial_id: UUID
    attempt_id: UUID
    validation_report_id: UUID
    selected_at: datetime

class BatchSummaryRevisionRow(Base):
    batch_summary_revision_id: UUID
    batch_id: UUID
    revision_number: int
    summary_protocol_version: str
    included_result_refs_json: list[dict[str, object]]
    excluded_reasons_json: list[dict[str, object]]
    warning_json: dict[str, object]
    aggregates_json: dict[str, object]
    created_at: datetime
```

Use PostgreSQL `NUMERIC` for `numeric_value`, not float. No `updated_at`. No additional digest columns.

- [ ] **Step 4: Implement Alembic revision `0011_result_plane`**

```python
revision = "0011_result_plane"
down_revision = "0010_attempt_physical_execution"
```

Create the five tables and exact FKs to `attempts`, `trials`, and `batches`. Add check `byte_size >= 0`, verdict in `('valid','invalid','inconclusive','error')`, and `revision_number > 0`.

- [ ] **Step 5: Register models and narrow architecture allowlist**

Add:

```python
import labpilot.results_validation.models  # noqa: F401
```

to `src/labpilot/db/model_registry.py`, and add exactly:

```python
"labpilot.results_validation.models"
```

to `MODEL_REGISTRY_ALLOWED_IMPORTS`.

- [ ] **Step 6: Run migration/architecture regression**

```bash
uv run pytest \
  tests/unit/results_validation/test_models.py \
  tests/integration/test_result_plane_migration.py \
  tests/architecture -q
```

Expected: PASS on a fresh PostgreSQL test database.

- [ ] **Step 7: Commit Task 3**

```bash
git add src/labpilot/results_validation migrations/versions/0011_result_plane.py \
  src/labpilot/db/model_registry.py tests/architecture/dependency_rules.py \
  tests/unit/results_validation/test_models.py \
  tests/integration/test_result_plane_migration.py
git commit -m "feat(slice3): add result plane persistence"
```

---

### Task 4: Implement safe Artifact reads and strict `result-json-v1`

**Risk:** Safety/provenance critical.

**Files:**
- Create: `src/labpilot/results_validation/artifact_reader.py`
- Create: `src/labpilot/results_validation/extractor.py`
- Expand: `src/labpilot/results_validation/types.py`
- Test: `tests/unit/results_validation/test_artifact_reader.py`
- Test: `tests/unit/results_validation/test_extractor.py`

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


def read_artifact_snapshot(
    output_root: Path,
    relative_path: str,
    *,
    max_bytes: int,
) -> ArtifactSnapshot | MissingArtifact: ...


def extract_result_json_v1(
    content: bytes,
    *,
    metric_names: tuple[str, ...],
) -> dict[str, Decimal]: ...
```

- [ ] **Step 1: Write RED safe-read tests**

Create tests using real temp directories for:

```text
regular file -> bytes + SHA-256 from same snapshot
missing leaf -> MissingArtifact
../ traversal -> deterministic unsafe-path error
absolute path -> deterministic unsafe-path error
intermediate symlink -> deterministic unsafe-path error
leaf symlink -> deterministic unsafe-path error
directory leaf -> deterministic type error
oversized file -> deterministic content error
file changed between pre/post fstat -> fail closed
```

The mutation test may inject a read hook or monkeypatch the low-level reader so the implementation can deterministically observe a changed `fstat` rather than rely on timing.

- [ ] **Step 2: Write RED extractor grammar tests**

Cover:

```python
@pytest.mark.parametrize("raw", [
    b'{"schema_version":"v1","metrics":{"score":NaN}}',
    b'{"schema_version":"v1","metrics":{"score":Infinity}}',
    b'{"schema_version":"v1","metrics":{"score":true}}',
    b'{"schema_version":"v1","metrics":{"score":1,"score":2}}',
])
def test_result_json_rejects_non_deterministic_or_non_numeric_input(raw: bytes): ...
```

Also verify:
- invalid UTF-8 rejected;
- top-level must contain exactly `schema_version` and `metrics`;
- `schema_version == "v1"`;
- missing requested metric rejected;
- undeclared extra metric rejected;
- `0.82` becomes `Decimal("0.82")`.

- [ ] **Step 3: Verify RED**

```bash
uv run pytest tests/unit/results_validation/test_artifact_reader.py \
  tests/unit/results_validation/test_extractor.py -q
```

- [ ] **Step 4: Implement anchored descriptor-based reads**

Use `os.open(..., dir_fd=..., O_NOFOLLOW)` for each path component. Open intermediate components with `O_DIRECTORY`; open the leaf once; require `stat.S_ISREG(fstat.st_mode)`; check `st_size <= max_bytes`; read from that fd; compare stable identity/stat fields before and after the read; compute SHA-256 and parse from the captured `bytes`, never from a second pathname open.

Treat `ENOENT` as `MissingArtifact`. Treat traversal/symlink/type/oversize/content mutation as deterministic result-content errors. Let ordinary transient `OSError` categories surface as operational failures so the caller can retry without persisting a verdict.

- [ ] **Step 5: Implement strict JSON parsing**

Use `json.loads()` with:

```python
def reject_constant(value: str) -> NoReturn:
    raise ResultContentError(f"unsupported JSON constant: {value}")


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ResultContentError("duplicate JSON key")
        result[key] = value
    return result

value = json.loads(
    content.decode("utf-8", "strict"),
    parse_float=Decimal,
    parse_int=Decimal,
    parse_constant=reject_constant,
    object_pairs_hook=unique_object,
)
```

Reject bool explicitly because `bool` is an `int` subclass. Require every returned Decimal to be finite.

- [ ] **Step 6: Run focused tests and commit**

```bash
uv run pytest tests/unit/results_validation/test_artifact_reader.py \
  tests/unit/results_validation/test_extractor.py -q
uv run ruff check src/labpilot/results_validation tests/unit/results_validation

git add src/labpilot/results_validation tests/unit/results_validation
git commit -m "feat(slice3): add safe result artifact extraction"
```

---

### Task 5: Implement deterministic Dataset/Environment/Metric validation

**Risk:** Scientific correctness boundary.

**Files:**
- Create: `src/labpilot/results_validation/validator.py`
- Expand: `src/labpilot/results_validation/types.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Test: `tests/unit/results_validation/test_validator.py`

**Interfaces:**

```python
EXTRACTION_PROFILE_VERSION = "result-json-v1"
VALIDATOR_VERSION = "slice3-validator-v1"
SUMMARY_PROTOCOL_VERSION = "slice3-summary-v1"

class ValidationVerdict(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"

@dataclass(frozen=True, slots=True)
class ValidationDecision:
    verdict: ValidationVerdict
    reasons: tuple[dict[str, object], ...]
    warnings: tuple[dict[str, object], ...]
```

- [ ] **Step 1: Add `packaging` as an explicit runtime dependency**

Add:

```toml
"packaging>=25,<26",
```

to project dependencies, then run:

```bash
uv lock
uv sync --locked --all-groups
```

This dependency is used only for deterministic PEP 440 framework requirement evaluation; do not combine the FastAPI SSE upgrade into this task.

- [ ] **Step 2: Write RED validation tests**

Cover:

```text
artifact_exists missing required Artifact -> invalid
metric_finite missing metric -> invalid
metric_gte 0.82 >= 0.80 -> pass
metric_gte 0.61 >= 0.80 -> invalid
metric_lte behaves symmetrically
best_effort Dataset without stable identity -> warning, not invalid by itself
observed environment hash != Version hash -> error
python_profile known mismatch -> invalid
framework distribution missing -> invalid
framework installed version violates Requirement specifier -> invalid
framework Requirement satisfied -> pass
required cuda with device_count == 0 -> invalid
required cuda with runtime/device observation unavailable -> inconclusive
cuda_requirement "12.8" matches observed 12080 -> pass
insufficient observed_environment -> inconclusive
malformed frozen framework Requirement -> error
```

- [ ] **Step 3: Verify RED**

```bash
uv run pytest tests/unit/results_validation/test_validator.py -q
```

- [ ] **Step 4: Implement deterministic validation**

Use `packaging.requirements.Requirement`, `packaging.utils.canonicalize_name`, and `packaging.version.Version` for framework checks.

For CUDA runtime integer encoding, compare a requirement like `12.8` against:

```python
def cuda_runtime_text(value: int) -> str:
    major = value // 1000
    minor = (value % 1000) // 10
    return f"{major}.{minor}"
```

Validation ordering must be deterministic and reason entries must be sorted by `(code, subject)` before persistence.

Use explicit reason codes, including:

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

Use warning code `DATASET_BEST_EFFORT_REPRODUCIBILITY`.

- [ ] **Step 5: Run validator/dependency regressions and commit**

```bash
uv lock --check
uv run pytest tests/unit/results_validation/test_validator.py -q
uv run ruff check src/labpilot/results_validation tests/unit/results_validation
uv run mypy src

git add pyproject.toml uv.lock src/labpilot/results_validation/validator.py \
  src/labpilot/results_validation/types.py \
  tests/unit/results_validation/test_validator.py
git commit -m "feat(slice3): add deterministic scientific validation"
```

---

### Task 6: Persist Artifact → Metric → Validation → first-eligible selection atomically

**Risk:** Boundary-sensitive/idempotency/provenance.

**Files:**
- Create: `src/labpilot/results_validation/service.py`
- Test: `tests/unit/results_validation/test_service.py`
- Test: `tests/integration/test_result_processing_service.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ProcessAttemptResult:
    attempt_id: UUID
    validation_report_id: UUID
    verdict: ValidationVerdict
    selected: bool

class ResultApplicationService:
    async def process_attempt(self, attempt_id: UUID) -> ProcessAttemptResult | None: ...
```

`None` means the Attempt is not an eligible successful terminal Attempt or a transient operational failure deferred processing before any scientific verdict was committed.

- [ ] **Step 1: Write RED service tests**

Integration scenarios:

```text
nonzero exit -> None and zero Result Plane rows
exit0 + result.json score .82 -> one Artifact, one Metric, valid report, one selection
exit0 + score .61 threshold .80 -> invalid report, no selection
missing required result.json -> invalid report, no Artifact row for missing file
best_effort Dataset -> report warning persisted
same process_attempt twice unchanged -> same authoritative rows, no duplicates
Artifact row exists + bytes mutate before report -> error report
report already exists + bytes later mutate -> historical report unchanged; explicit process fails closed
transient read failure -> no ValidationReport and later retry succeeds
same Trial has later valid interpretation -> existing TrialResultSelection unchanged
```

- [ ] **Step 2: Verify RED**

```bash
uv run pytest \
  tests/unit/results_validation/test_service.py \
  tests/integration/test_result_processing_service.py -q
```

- [ ] **Step 3: Implement the exact Version result-contract loader**

Inside `service.py`, isolate a private `ResultContractLoader` that loads `BatchRow → ExperimentVersionRow → ProposalRevisionRow`, parses the Proposal, and rechecks existing frozen bindings using the existing helpers:

```text
revision.payload_hash == version.proposal_payload_hash
proposal_payload_hash(parsed_payload) == version.proposal_payload_hash
dataset_reference_hash(parsed/frozen) == version.dataset_reference_hash
environment_expectation_hash(parsed/frozen) == version.environment_expectation_hash
validation_protocol_hash(parsed/frozen) == version.validation_protocol_hash
```

Do not create a new result-contract hash.

Return a `ResultContract` containing Artifact declarations, Metric declarations, DatasetReference, EnvironmentExpectation, validation rules, and the already-existing validation protocol hash.

- [ ] **Step 4: Implement pre-transaction deterministic observation**

For each declared Artifact, call `read_artifact_snapshot()` under `<workspace>/runtime/output`. For each declared Metric, extract from its declared source Artifact using the captured bytes and `result-json-v1`.

Never scan arbitrary output files.

- [ ] **Step 5: Implement the transactional persistence/selection path**

In one `UnitOfWork`:

```text
SELECT Attempt FOR UPDATE
recheck terminal/process_exited/0 and workspace identity
load Trial and Batch
reconcile existing Artifact slots by (attempt_id, artifact_name)
insert present Artifact rows with uuid4 ids
insert MetricObservation rows by natural key
build deterministic input_snapshot_json from frozen contract + observed env + Artifact/Metrics/missing facts
validate
reuse exact existing report for same semantic versions only when structured input matches
otherwise insert one ValidationReport
if verdict valid:
    SELECT Trial FOR UPDATE
    insert TrialResultSelection only if absent
append RuntimeEvents
commit
```

If an existing Artifact slot has a different digest, fail closed. If a report already exists, do not append a second report under the same semantic versions.

Emit internal events as `actor_type="system"`, `enqueue_outbox=False`. Use Batch as the result-stream aggregate so one ordered SSE cursor can cover the result lifecycle:

```text
result.artifact_registered
result.validation_completed
result.trial_selected
```

Payloads contain IDs, verdict/reason codes, and metric names/values needed for UI notification, but never Artifact file content or secrets.

- [ ] **Step 6: Run service/integration regressions**

```bash
uv run pytest \
  tests/unit/results_validation \
  tests/integration/test_result_processing_service.py -q
```

Expected: all scenarios PASS, including retry after transient failure and immutable selection.

- [ ] **Step 7: Commit Task 6**

```bash
git add src/labpilot/results_validation/service.py \
  src/labpilot/results_validation/types.py \
  tests/unit/results_validation/test_service.py \
  tests/integration/test_result_processing_service.py
git commit -m "feat(slice3): process deterministic attempt results"
```

---

### Task 7: Generate immutable BatchSummary revisions from execution-owned settlement

**Risk:** Scientific summary correctness.

**Files:**
- Create: `src/labpilot/results_validation/summary_service.py`
- Test: `tests/unit/results_validation/test_summary_service.py`
- Test: `tests/integration/test_batch_summary_service.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class RefreshSummaryResult:
    batch_id: UUID
    revision_id: UUID | None
    created: bool
    presentation_status: str

class BatchSummaryService:
    async def refresh_batch(self, batch_id: UUID) -> RefreshSummaryResult: ...
```

- [ ] **Step 1: Write RED summary tests**

Cover:

```text
any TrialExecutionOutcome unresolved -> no formal revision, not_ready
selected valid Trial -> included
invalid report -> excluded validation_invalid
inconclusive -> excluded validation_inconclusive
deterministic error -> excluded validation_error
exhausted execution failure -> excluded with execution-owned reason
cancelled -> excluded cancelled
best_effort warning -> propagated
same structured input + same summary protocol -> no new revision
new selection/settled exclusion -> next revision_number
mean/count computed with Decimal, deterministic Trial ordinal order
```

- [ ] **Step 2: Verify RED**

```bash
uv run pytest tests/unit/results_validation/test_summary_service.py \
  tests/integration/test_batch_summary_service.py -q
```

- [ ] **Step 3: Implement structured summary generation**

Lock `BatchRow`, load Trials ordered by `trial_ordinal`, then call `TrialExecutionOutcomeService.get()` for each Trial. Do not infer retry exhaustion inside the summary module.

Build:

```python
included = [
    {
        "trial_id": str(trial_id),
        "attempt_id": str(attempt_id),
        "validation_report_id": str(report_id),
        "metrics": {"score": "0.82"},
    },
]
excluded = [
    {"trial_id": str(trial_id), "reason": "validation_invalid"},
]
aggregates = {
    "score": {"count": 1, "mean": "0.82"},
}
```

Compare `(included, excluded, warnings, aggregates, SUMMARY_PROTOCOL_VERSION)` with the latest revision. Insert only if different. Do not compute a summary hash.

Presentation:

```text
not_ready                       unresolved Trial exists
complete                        all settled Trials included
complete_with_invalid_results   settled and at least one Trial excluded
```

- [ ] **Step 4: Emit summary event in the same transaction**

Emit `result.summary_created` on Batch aggregate with revision id/number and presentation status; do not include raw Artifact bytes.

- [ ] **Step 5: Run tests and commit**

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

**Risk:** Recovery boundary; must not disturb Slice2 launch behavior.

**Files:**
- Modify: `src/labpilot/coordinator/service.py`
- Modify: `src/labpilot/coordinator/worker.py`
- Test: `tests/unit/coordinator/test_service.py`
- Test: `tests/integration/test_coordinator_service.py`

**Interfaces:** Coordinator receives `ResultApplicationService` and `BatchSummaryService` dependencies.

- [ ] **Step 1: Write RED Coordinator ordering/recovery tests**

Assert call order:

```text
_control_existing_work
_reconcile_physical_work
_process_successful_terminal_results
_prepare_eligible_work
_admit_launch_ready
_refresh_settled_summaries
```

Also test:
- only terminal/process_exited/0 Attempts without a report are selected for normal processing;
- one transient processing failure does not mark the Attempt failed and does not block future scans;
- Coordinator restart followed by another scan converges without duplicate result rows;
- existing physical launch/reconciliation tests remain unchanged.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest tests/unit/coordinator/test_service.py \
  tests/integration/test_coordinator_service.py -q
```

- [ ] **Step 3: Add narrow Coordinator hooks**

Add constructor dependencies:

```python
result_processing: ResultApplicationService
batch_summaries: BatchSummaryService
```

Add `_successful_terminal_attempts_without_report()` as a DB query; do not call a public process endpoint.

After processing attempts, refresh only Batches that either had processed results or are candidates for settlement. `BatchSummaryService` remains responsible for final readiness.

- [ ] **Step 4: Compose services in `build_coordinator()`**

Create one shared `TrialExecutionOutcomeService`; pass it to `BatchSummaryService`. Construct Result/Summary services from `database.session`. Do not create a second Coordinator worker or generic job queue.

- [ ] **Step 5: Run Slice2 Coordinator regressions plus new tests**

```bash
uv run pytest \
  tests/unit/coordinator \
  tests/unit/execution \
  tests/integration/test_coordinator_service.py \
  tests/integration/test_attempt_execution_service.py -q
```

- [ ] **Step 6: Commit Task 8**

```bash
git add src/labpilot/coordinator/service.py src/labpilot/coordinator/worker.py \
  tests/unit/coordinator/test_service.py tests/integration/test_coordinator_service.py
git commit -m "feat(slice3): reconcile results in coordinator scans"
```

---

### Task 9: Expose authoritative result query APIs

**Risk:** Routine API boundary; no mutation endpoints.

**Files:**
- Create: `src/labpilot/results_validation/query_service.py`
- Create: `src/labpilot/api/schemas/results.py`
- Create: `src/labpilot/api/routers/results.py`
- Modify: `src/labpilot/api/dependencies.py`
- Modify: `src/labpilot/api/app.py`
- Modify: `src/labpilot/api/errors.py`
- Modify: `src/labpilot/main.py`
- Test: `tests/unit/results_validation/test_query_service.py`
- Test: `tests/integration/api/test_results_api.py`

**Public endpoints:**

```text
GET /attempts/{attempt_id}/results
GET /trials/{trial_id}/result
GET /batches/{batch_id}/summary
```

- [ ] **Step 1: Write RED query/API tests**

Verify:
- 404 uses stable aggregate-specific error response;
- Attempt response includes Artifact name/path/digest/size/current availability, Metrics, Validation verdict/reasons/warnings;
- current file changed after historical validation returns `corrupted` availability without rewriting historical report;
- Trial response identifies selected Attempt/report or explicit ineligible state;
- Batch response returns latest revision, included/excluded refs, warnings, aggregates, and presentation status;
- no POST processing/validate/summary route exists.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest tests/unit/results_validation/test_query_service.py \
  tests/integration/api/test_results_api.py -q
```

- [ ] **Step 3: Implement query details and current availability**

Use immutable rows as historical truth. For Artifact availability only, safely inspect the current controlled output path and report:

```text
available   digest still matches
missing     registered path no longer exists
corrupted   current bytes/type/path safety fail against registered Artifact
```

Never replace historical Artifact/Metric/Validation data with current bytes.

- [ ] **Step 4: Add Pydantic responses and router composition**

Add `result_queries: ResultQueryService | None` to `ApplicationServices` and `get_result_query_service()` dependency. Include `results_router` in `create_app()` and instantiate `ResultQueryService(database.session)` in `main.py`.

Register result not-found errors in `api/errors.py`; do not expose internal filesystem paths beyond the declared Artifact `relative_path`.

- [ ] **Step 5: Run API regressions and commit**

```bash
uv run pytest tests/integration/api tests/unit/results_validation/test_query_service.py -q
uv run ruff check src/labpilot/api src/labpilot/results_validation tests/integration/api

git add src/labpilot/results_validation/query_service.py \
  src/labpilot/api/schemas/results.py src/labpilot/api/routers/results.py \
  src/labpilot/api/dependencies.py src/labpilot/api/app.py src/labpilot/api/errors.py \
  src/labpilot/main.py tests/unit/results_validation/test_query_service.py \
  tests/integration/api/test_results_api.py
git commit -m "feat(slice3): expose result provenance queries"
```

---

### Task 10: Extend the real training fixture and prove the Slice3 result loop in PostgreSQL integration

**Risk:** Acceptance fixture compatibility.

**Files:**
- Modify: `tests/acceptance/slice2/training_fixture/train.py`
- Create: `tests/unit/results_validation/test_training_fixture.py`
- Create: `tests/integration/slice3/test_result_loop.py`

- [ ] **Step 1: Write RED fixture tests**

Run fixture main against a temp `runtime/output` and assert:

```text
without --score -> existing workload-test-oracle.json behavior only
with --score 0.82 -> existing oracle + result.json
result.json exact shape:
{"schema_version":"v1","metrics":{"score":0.82}}
```

Also prove invalid/non-finite score CLI input is rejected and Slice2 existing fixture callers still pass unchanged.

- [ ] **Step 2: Implement optional `--score`**

Add:

```python
parser.add_argument("--score", type=float)
```

Reject non-finite values. On natural completion and before return, atomically write `result.json` only when score is supplied. Use a sibling temp file, `fsync`, then `os.replace`; do not repurpose `workload-test-oracle.json`.

- [ ] **Step 3: Write the PostgreSQL Slice3 integration scenario**

Build two Trials using the approved Proposal semantics:

```text
Artifact: result -> result.json required
Metric: score -> result
Rules:
  artifact_exists(result)
  metric_finite(score)
  metric_gte(score, 0.80)

Trial A result.json score = 0.82
Trial B result.json score = 0.61
```

Create terminal successful Attempts with controlled workspaces and observed environments, run `ResultApplicationService` + `BatchSummaryService`, and assert:

```text
A -> valid -> TrialResultSelection -> included
B -> invalid METRIC_BELOW_THRESHOLD -> no selection -> excluded
summary presentation -> complete_with_invalid_results
```

- [ ] **Step 4: Add restart/idempotency integration to the same scenario**

Reconstruct service instances between processing passes to simulate control-plane restart. Assert row counts remain exactly:

```text
Artifact rows: expected present slots only
Metric rows: one per extracted declared metric
ValidationReport: one per Attempt/semantic version
TrialResultSelection: one for Trial A only
unchanged BatchSummaryRevision: one latest revision, not duplicated by rerun
```

- [ ] **Step 5: Run fixture/Slice2/integration regressions and commit**

```bash
uv run pytest \
  tests/unit/results_validation/test_training_fixture.py \
  tests/integration/slice3/test_result_loop.py \
  tests/acceptance/slice2/test_native_slice2.py -q

git add tests/acceptance/slice2/training_fixture/train.py \
  tests/unit/results_validation/test_training_fixture.py \
  tests/integration/slice3/test_result_loop.py
git commit -m "test(slice3): add deterministic result loop fixture"
```

---

### Task 11: Upgrade FastAPI in isolation and add minimal Batch result SSE

**Risk:** Dependency/API compatibility; keep separate from core Result Plane.

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Expand: `src/labpilot/results_validation/query_service.py`
- Modify: `src/labpilot/api/routers/results.py`
- Modify: `src/labpilot/api/schemas/results.py`
- Test: `tests/integration/api/test_result_events_sse.py`
- Test: existing API/Pydantic integration suite

**Public endpoint:**

```text
GET /batches/{batch_id}/events
```

Use official FastAPI SSE support introduced in 0.135.0:

```python
from fastapi.sse import EventSourceResponse, ServerSentEvent
```

- [ ] **Step 1: Upgrade FastAPI dependency only and run compatibility gate before SSE code**

Change:

```toml
"fastapi>=0.135,<0.136",
```

then:

```bash
uv lock
uv sync --locked --all-groups
uv run pytest tests/unit tests/integration/api -q
uv run mypy src scripts
```

If this dependency-only gate fails, debug the compatibility regression before adding SSE code. Do not work around a failing supported API by reimplementing custom SSE framing.

- [ ] **Step 2: Commit the dependency alignment separately**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: align FastAPI for native SSE"
```

- [ ] **Step 3: Write RED SSE tests**

Add events to the existing `runtime_events` Batch aggregate and test:
- response content type is `text/event-stream`;
- event `id` equals Batch aggregate sequence;
- event name equals RuntimeEvent `event_type`;
- JSON data contains bounded notification payload;
- `Last-Event-ID` resumes after the provided sequence;
- no duplicate previously acknowledged event;
- endpoint does not read an event projection table.

- [ ] **Step 4: Add batch-event query and official SSE generator**

In `ResultQueryService`:

```python
async def list_batch_events_after(
    self,
    batch_id: UUID,
    *,
    after_sequence: int,
    limit: int = 100,
) -> tuple[ResultEventDetails, ...]: ...
```

Query `RuntimeEventRow` by:

```text
aggregate_type = batch
aggregate_id = str(batch_id)
aggregate_sequence > after_sequence
ORDER BY aggregate_sequence
LIMIT 100
```

Endpoint shape:

```python
@router.get("/batches/{batch_id}/events", response_class=EventSourceResponse)
async def stream_batch_result_events(...) -> AsyncIterator[ServerSentEvent]:
    cursor = parsed_last_event_id
    while True:
        rows = await service.list_batch_events_after(
            batch_id, after_sequence=cursor, limit=100
        )
        for row in rows:
            cursor = row.aggregate_sequence
            yield ServerSentEvent(
                id=str(cursor),
                event=row.event_type,
                data=row.payload,
            )
        if await request.is_disconnected():
            return
        await asyncio.sleep(0.5)
```

Use FastAPI's own keepalive behavior; do not add custom ping framing.

- [ ] **Step 5: Run API/SSE gate and commit**

```bash
uv run pytest tests/integration/api/test_result_events_sse.py tests/integration/api -q
uv run ruff check src tests
uv run mypy src scripts

git add src/labpilot/results_validation/query_service.py \
  src/labpilot/api/routers/results.py src/labpilot/api/schemas/results.py \
  tests/integration/api/test_result_events_sse.py
git commit -m "feat(slice3): stream batch result events over SSE"
```

---

### Task 12: Run native Slice3 acceptance, fault/provenance gates, and freeze candidate evidence

**Risk:** Slice boundary acceptance. This task validates; it does not add production-hardening platforms.

**Files:**
- Create: `tests/acceptance/slice3/__init__.py`
- Create: `tests/acceptance/slice3/native_smoke.py`
- Create: `tests/acceptance/slice3/test_native_slice3.py`
- Create after successful run: `docs/superpowers/evidence/2026-09-01-labpilot-v1-slice3-mvp-smoke.md`
- Modify only after all gates pass: `DOCUMENT-STATUS.md`

- [ ] **Step 1: Write the native acceptance test before running the host**

The acceptance driver must use the real API/Coordinator/PostgreSQL/Candidate-B Runner path and create a real two-Trial Batch whose training argv supplies `--score` from a parameter dimension.

Use two parameter values that deterministically produce:

```text
Trial A score 0.82
Trial B score 0.61
threshold score >= 0.80
```

The driver must assert through public APIs:

```text
both Attempts terminal with process_exited/0
Trial A Validation valid and selected
Trial B Validation invalid and not selected
Batch summary complete_with_invalid_results
included A, excluded B with METRIC_BELOW_THRESHOLD
Artifact digest/Metric extractor version visible
```

- [ ] **Step 2: Add bounded fault/recovery assertions**

During a real successful workload/result cycle:

```text
start real workload
restart Coordinator while the workload is active
observe original physical execution recovered
wait for terminal success
restart Coordinator during/after result processing
assert no duplicate Artifact/Metric/Validation/selection/unchanged summary
```

Do not rerun the historical N1-N7/V2/F01-F22 production-hardening campaign.

- [ ] **Step 3: Add provenance/security acceptance assertions**

On a controlled extra Attempt/workspace, prove:

```text
symlink result path rejected
post-registration/pre-validation byte mutation -> deterministic error/fail closed
post-validation byte mutation -> historical report/selection/summary unchanged and query availability corrupted
no raw result bytes or secrets appear in RuntimeEvent payload
```

- [ ] **Step 4: Perform the data-egress review**

Record in the evidence doc:

```text
Demo Repository classification
Demo Dataset reference/classification
result.json contains synthetic numeric metric only
no provider call is required by Slice3 result processing
no secret enters Artifact manifest, RuntimeEvent, ValidationReport, Summary, or SSE payload
API remains loopback/controlled-network according to existing deployment policy
```

- [ ] **Step 5: Run the full candidate gate**

Run locally/VM where each environment requirement is available:

```bash
uv lock --check
uv run ruff check src tests scripts
uv run mypy src scripts
uv run pytest tests/unit tests/architecture -v
uv run pytest tests/integration -v -m integration
./tests/scripts/test_verify_architecture_dependencies.sh
```

Then run the dedicated native Slice3 acceptance against the real host topology.

Finally run the repository's canonical gate:

```bash
./scripts/check_ci.sh
```

Push the candidate branch and require exact-HEAD GitHub Actions `ci` success before claiming the Slice candidate passes CI.

- [ ] **Step 6: Freeze evidence with bounded wording**

Evidence must say:

```text
Slice3 MVP / Result Loop validated
```

It must not say:

```text
production accepted
full Slice2 hardening complete
```

Record exact candidate commit, commands, pass/fail counts, native Batch/Trial/Attempt IDs needed to reproduce the evidence, the valid/invalid outcomes, Coordinator restart observation, and no-duplicate row/cardinality checks. Do not add new document SHA manifests.

- [ ] **Step 7: Update status only after fresh evidence exists**

Set `DOCUMENT-STATUS.md` to:

```text
Slice3 implementation: Validated / candidate ready
V1 Core Demo next action: authorized
Slice2 production hardening: still deferred
```

Do not mark V1 Core Release accepted in this task.

- [ ] **Step 8: Commit the acceptance harness/evidence/status**

```bash
git add tests/acceptance/slice3 \
  docs/superpowers/evidence/2026-09-01-labpilot-v1-slice3-mvp-smoke.md \
  DOCUMENT-STATUS.md
git commit -m "docs(slice3): close out result loop MVP evidence"
```

---

## Plan Self-Review Checklist

Before implementation authorization, confirm all of the following against the design spec and focused re-review:

```text
[ ] Artifact provenance/availability has an implementation task
[ ] Metric extractor semantic version is persisted
[ ] Dataset best_effort warning reaches Validation + Summary
[ ] EnvironmentExpectation consumes Attempt observed_environment
[ ] exit!=0 never becomes scientific invalid
[ ] Execution owns retry/exhaustion through one shared rule
[ ] Legacy validation JSON/hash shape is preserved
[ ] Threshold scientific-invalid negative case exists
[ ] transient operational failure commits no verdict
[ ] first-eligible selection is immutable
[ ] post-validation Artifact mutation cannot rewrite history
[ ] BatchSummary only uses eligible selections and execution-owned settlement
[ ] deterministic rerun covers Coordinator restart without ProcessingJob
[ ] safe-read rejects traversal/symlinks and binds digest+parse to one snapshot
[ ] results_validation namespace and model-registry guard are aligned
[ ] public GET queries expose provenance and exclusions
[ ] FastAPI dependency refresh is isolated before SSE
[ ] SSE is notification-only and authoritative tables remain truth
[ ] native real-training valid+invalid E2E is included
[ ] fault injection/security/provenance/data-egress candidate gates are included
[ ] no full CAS/Projection/Processing platform/LLM result judge is introduced
[ ] no new bookkeeping hash graph is introduced
```

## Implementation Authorization Gate

This plan itself does **not** authorize production code. The next governance step is one focused plan review against:

```text
Planning baseline ea3d5ced1c886e6484f1ab2a372c0dab547a4deb
+ approved Slice3 design
+ focused design re-review
+ S2→S3 Sequencing Amendment v0.3.1
+ this Detailed Implementation Plan
```

If that focused plan review has `Critical=0`, `Blocking=0`, and no unresolved Important issue, record the approval and only then create the isolated Slice3 implementation worktree/branch.
