# LabPilot V1 Slice3 — Minimal Result Plane Design

```text
Status: Revised draft for focused re-review
Date: 2026-09-01
Planning baseline: 3e3cebf32877cfe8d65f76a393eefb20ac41d798
Scope: Slice3 only
```

## 1. Outcome

Slice3 closes the V1 result loop:

```text
terminal Attempt
→ Artifact
→ MetricObservation
→ deterministic ValidationReport
→ TrialResultSelection
→ BatchSummaryRevision
```

`TrialResultSelection` is not a fifth processing stage. It is the immutable binding that answers which valid Attempt became the formal result for a Trial.

The design must preserve:

```text
execution success != scientific validity
```

Required negative example:

```text
process exit code = 0
but score violates the approved validation rule
→ execution succeeded
→ Validation invalid
→ Trial ineligible
→ excluded from BatchSummary
```

This design intentionally implements only the V1 Core Result Plane. Historical Architecture 07 remains informative; it does not reintroduce generic ProcessingJob, LogicalRun, CAS, Projection Worker, or result-platform machinery.

## 2. Non-goals

Slice3 Core does not build:

- LLM result interpretation or automatic scientific conclusions;
- generic ProcessingJob / LogicalRun / ProcessingRevision infrastructure;
- dynamic extractor or validator plugins;
- full Artifact CAS/object storage;
- Projection Worker as a Core dependency;
- automatic best-result selection;
- historical result reselection/republication workflows;
- advanced statistics, hypothesis testing, or paper-claim governance;
- Slice2 production-hardening campaign work that is not required by the Slice3 MVP.

LLM result interpretation remains P1.

## 3. Hash budget

Do not add hashes merely for bookkeeping.

Rules:

1. Preserve existing ExperimentVersion / Proposal hashes owned by upstream contracts.
2. Reuse the existing `validation_protocol_hash` already frozen on ExperimentVersion.
3. Use SHA-256 for Artifact bytes because content integrity must be independently verifiable.
4. Do not add new `input_identity_hash`, `summary_hash`, `metric_hash`, `selection_hash`, or similar fields when IDs, semantic versions, immutable structured snapshots, row locks, and unique constraints are sufficient.
5. Validation inputs are stored as immutable structured facts in `input_snapshot_json`.
6. Summary inputs are stored as immutable included/excluded references and warnings.

Slice3 must not expand the historical hash graph.

## 4. Result domain model

### 4.1 Artifact

An Artifact is an immutable registration of one declared output slot from one Attempt.

Minimum fields:

```text
artifact_id
attempt_id
artifact_name
relative_path
content_sha256
byte_size
media_type
created_at
```

Constraints:

```text
UNIQUE(attempt_id, artifact_name)
UNIQUE(attempt_id, relative_path)
```

The uniqueness key intentionally does not contain the digest. If an already registered slot later contains different bytes, processing fails closed with a provenance conflict instead of creating a second formal Artifact.

No managed-copy/CAS requirement exists in Slice3 MVP. Registration proves the declared file was available and intact at registration time. Query code may derive current availability as `available`, `missing`, or `corrupted`; MVP does not persist a mutable availability projection.

The Slice3 Result API receives read-only visibility of the Attempt workspace at the same absolute path as the Coordinator solely to derive current Artifact availability. It receives neither the Runner socket nor execution-control credentials, so this data-plane visibility grants no Execution Plane authority. If the controlled output root or an Artifact path has been removed, historical Artifact registration remains authoritative and current availability is `missing`, not an internal error. This narrowly supersedes the older Slice2 deployment assumption that the API receives no Attempt-workspace visibility.

#### Safe-read contract

Artifact registration and later extraction must use the controlled Attempt output root and must not trust workload-created path topology.

Required behavior:

- anchor all declared paths under `<attempt workspace>/runtime/output`;
- reject absolute paths and traversal;
- reject symlink components and a symlink leaf;
- accept only the expected regular-file type;
- bound bytes read according to the extractor contract;
- compute integrity digest and parse/extract from the same stable opened file/byte snapshot;
- before extracting from an already registered Artifact, prove the bytes still match `content_sha256`.

A digest mismatch is a provenance error, never a new Artifact version for the same Attempt slot.

### 4.2 MetricObservation

A MetricObservation is an immutable deterministic extraction from an Artifact.

Minimum fields:

```text
metric_observation_id
artifact_id
metric_name
split nullable
numeric_value NUMERIC
extractor_version
created_at
```

`attempt_id` is intentionally not duplicated here. Attempt provenance is derived through:

```text
MetricObservation → Artifact → Attempt
```

MVP extractor:

```text
result-json-v1
```

Natural uniqueness:

```text
UNIQUE(artifact_id, metric_name, extractor_version)
```

Metric names are unique inside an approved Proposal, so nullable `split` does not participate in the MVP uniqueness key.

### 4.3 ValidationReport

A ValidationReport is an immutable deterministic verdict over the frozen Version semantics and observed Attempt result inputs.

Minimum fields:

```text
validation_report_id
attempt_id
validation_protocol_hash
extraction_profile_version
validator_version
input_snapshot_json
verdict
reason_json
warning_json
created_at
```

Natural uniqueness:

```text
UNIQUE(
  attempt_id,
  validation_protocol_hash,
  extraction_profile_version,
  validator_version
)
```

`input_snapshot_json` freezes the authoritative facts actually used by validation, including:

```text
ExperimentVersion validation rules
DatasetReference reproducibility semantics
EnvironmentExpectation
Attempt observed_environment
Artifact IDs / digests / explicit missing markers
MetricObservation IDs / values / extractor versions
```

A later rerun under the same semantic versions must compare current deterministic inputs with the persisted snapshot. A mismatch is a provenance conflict; it does not silently create a second interpretation under the same semantic versions.

An intentional future extractor/validator semantic change must bump its semantic version and may append a new ValidationReport. It must not mutate an old report.

Verdicts:

```text
valid
invalid
inconclusive
error
```

Semantics:

- `valid`: all required deterministic scientific/output and environment rules pass.
- `invalid`: inputs are known and a required scientific/output/environment condition is violated.
- `inconclusive`: deterministic facts are insufficient to decide, without evidence of provenance corruption or validator failure.
- `error`: a deterministic content/provenance contradiction or malformed authoritative input prevents a reliable judgment.

Operational/transient failures are not verdicts. Database unavailability, transient filesystem I/O, Coordinator termination, or an unexpected internal exception must produce no immutable ValidationReport; processing is deferred and retried on a later deterministic scan.

#### Dataset and environment semantics

Validation must preserve upstream reproducibility/environment contracts:

- `best_effort` / mutable DatasetReference produces an explicit reproducibility warning;
- that warning is preserved in ValidationReport and propagated to BatchSummary;
- known mismatch against a required EnvironmentExpectation → `invalid`;
- insufficient observed environment to evaluate a required expectation → `inconclusive`;
- binding/provenance contradiction between frozen expectation and Attempt observation → `error`.

No new Dataset or Environment persistence family is introduced.

### 4.4 TrialResultSelection

A TrialResultSelection records the first eligible valid Attempt for a Trial.

Minimum fields:

```text
trial_id PK/FK
attempt_id
validation_report_id
selected_at
```

Rules:

- only `valid` reports may be selected;
- selection is first-eligible, never highest-metric or latest-attempt;
- selection is immutable;
- Trial row lock plus `UNIQUE(trial_id)` serializes concurrent selection;
- later extractor/validator semantic versions may append new interpretation, but Slice3 Core does not automatically withdraw, replace, or republish an existing TrialResultSelection.

Historical reselection is a future explicit workflow, not an implicit side effect of revalidation.

### 4.5 BatchSummaryRevision

A BatchSummaryRevision is an immutable deterministic summary over settled Trial result state.

Minimum fields:

```text
batch_summary_revision_id
batch_id
revision_number
summary_protocol_version
included_result_refs_json
excluded_reasons_json
warning_json
aggregates_json
created_at
```

Constraints:

```text
UNIQUE(batch_id, revision_number)
```

Idempotent refresh does not require an input hash. The service locks the Batch, derives current structured summary inputs in deterministic Trial order, compares those inputs plus `summary_protocol_version` with the latest revision, and creates a new revision only when they differ.

MVP aggregates:

```text
count
mean
```

`min` / `max` may be added if trivial, but are not acceptance requirements.

## 5. Experiment contract extension

Current Proposal semantics already declare Artifacts, Metrics, and Validation rules. Slice3 adds threshold validation needed for a meaningful scientific-invalid demonstration.

Validation kinds become:

```text
artifact_exists
metric_finite
metric_gte
metric_lte
```

`ValidationRule` gains:

```text
threshold: Decimal | None
```

Rules:

- `artifact_exists` and `metric_finite` require `threshold=None`;
- `metric_gte` and `metric_lte` require a finite threshold;
- threshold rules do not replace `metric_finite` coverage;
- rule subjects must resolve to declared Artifact/Metric names;
- Proposal verifier profile/version must change because verifier semantics change;
- Artifact declaration names and relative paths must both be unique before approval.

### 5.1 Backward-compatible canonicalization

Existing Slice1/Slice2 Proposal and ExperimentVersion hashes must remain valid.

Legacy rules keep their exact historical canonical representation:

```json
{"kind":"artifact_exists","subject":"result"}
{"kind":"metric_finite","subject":"score"}
```

New threshold rules use:

```json
{"kind":"metric_gte","subject":"score","threshold":0.8}
{"kind":"metric_lte","subject":"loss","threshold":1.0}
```

Compatibility rule:

- parsing historical JSON with no `threshold` yields `threshold=None` in memory;
- canonicalizing `artifact_exists` or `metric_finite` must continue to omit the `threshold` key;
- canonicalizing `metric_gte` / `metric_lte` includes `threshold`;
- no historical Proposal payload hash, Validation protocol hash, or ExperimentVersion identity may change solely because Slice3 adds the optional field in the in-memory type.

This compatibility contract is required because Slice2 execution reconstructs Proposal semantics and verifies the existing frozen hashes.

## 6. Training fixture and result-json-v1

The existing execution oracle remains execution-only:

```text
runtime/output/workload-test-oracle.json
```

Slice3 extends the fixture with an optional score argument. When present, it atomically writes:

```text
runtime/output/result.json
```

Schema:

```json
{
  "schema_version": "v1",
  "metrics": {
    "score": 0.82
  }
}
```

`workload-test-oracle.json` must never be treated as the scientific result Artifact. Existing Slice2 calls that do not request a score keep their current behavior.

### 6.1 Strict result-json-v1 grammar

The extractor contract must be deterministic:

- bounded input size;
- strict UTF-8;
- top-level JSON object;
- exact supported `schema_version`;
- `metrics` must be an object;
- duplicate JSON keys rejected;
- `NaN`, `Infinity`, and `-Infinity` rejected;
- booleans are not numeric metrics;
- numbers convert deterministically to Decimal-compatible finite values;
- undeclared or missing requested metrics are handled explicitly and never guessed.

Malformed content is a deterministic extraction/provenance failure and may produce Validation `error`. A transient read/operational failure produces no report and is retried.

## 7. Execution-owned Trial outcome contract

Result processing must not duplicate execution retry/exhaustion logic.

Execution remains authoritative for whether a Trial can still produce another Attempt. Slice3 adds or exposes one narrow read contract owned by the execution module, conceptually:

```text
TrialExecutionOutcome

unresolved
successful(attempt_id)
exhausted_execution_failure(reason)
cancelled
```

This is a derived query/value object, not a new table or lifecycle aggregate.

Rules:

```text
Execution Plane
→ owns Attempt retryability, max_retries, cancellation, unknown/conflict, and exhaustion

Result Plane
→ owns Artifact / Metric / Validation for a successful exit=0 Attempt
```

Result Plane and BatchSummary must consume this contract rather than reimplementing `max_retries`, retry reason, terminal-kind, or attempt-count rules.

## 8. Processing flow

The Coordinator remains the trigger owner; result semantics stay inside `results_validation` services.

Periodic scan order becomes conceptually:

```text
control existing work
→ reconcile physical work
→ process successful terminal results
→ prepare eligible retries/new work
→ admit launch-ready work
→ refresh settled Batch summaries
```

### 8.1 Eligibility for scientific result processing

Only Attempts satisfying all of the following enter the Result Plane:

```text
status = terminal
terminal_kind = process_exited
exit_code = 0
```

Non-zero exit, signal termination, launch failure, unknown state, identity conflict, and cancellation remain execution facts and must not be rewritten as scientific `invalid`.

### 8.2 Artifact collection

Collection is declaration-driven, not directory-guessing.

For each Artifact declaration from the immutable approved Proposal:

1. resolve the declared slot under the controlled output root;
2. apply the safe-read contract in §4.1;
3. register the immutable Artifact slot if present;
4. record an explicit missing fact for validation when a required Artifact is absent;
5. fail closed if an already registered slot no longer matches its registered bytes.

A missing required Artifact is a known scientific/output fact and can produce `invalid`.

### 8.3 Metric extraction

`result-json-v1` parses the registered declared source Artifact according to §6.1 and extracts named metrics deterministically.

Dynamic user code is not supported in Core.

### 8.4 Validation

Validation consumes:

```text
ExperimentVersion validation protocol/hash
+ DatasetReference reproducibility semantics
+ EnvironmentExpectation
+ Attempt observed_environment
+ immutable Artifact registrations / missing facts
+ MetricObservations
```

It produces an immutable ValidationReport only when a deterministic verdict can be committed without transient operational failure.

Examples:

```text
score=0.82, metric_gte(score,0.80) → valid
score=0.61, metric_gte(score,0.80) → invalid
required result Artifact missing → invalid
required environment known mismatch → invalid
required environment not observed sufficiently → inconclusive
registered Artifact digest no longer matches bytes → error
malformed result-json-v1 content → error
DB temporarily unavailable → no report; retry later
```

### 8.5 First-eligible selection

When a report is valid:

```text
BEGIN
SELECT Trial ... FOR UPDATE
if no TrialResultSelection exists:
    insert TrialResultSelection
COMMIT
```

Later valid Attempts cannot replace the selected result because of a better metric. Later semantic interpretations also do not automatically alter the historical selection.

### 8.6 Summary generation

A formal Batch summary is generated only when execution-owned Trial outcomes show that no Trial remains unresolved.

For each Trial:

- selected valid result → included;
- scientifically invalid → excluded with reason;
- inconclusive → excluded with reason;
- deterministic validation/provenance error → excluded with explicit error reason;
- exhausted execution failure → excluded with execution-owned reason;
- cancelled → excluded with cancellation reason;
- unresolved / unknown / identity-conflict / retry still possible → block formal summary.

Reproducibility warnings from Dataset semantics are propagated into the summary.

Result presentation status:

```text
complete
complete_with_invalid_results
not_ready
```

`complete_with_invalid_results` means the Batch is settled but at least one Trial is excluded; exact excluded reasons remain explicit.

## 9. Idempotency and recovery

Slice3 does not introduce ProcessingJob because deterministic rerun is sufficient for the MVP.

Convergence mechanisms:

- Artifact natural unique slot per Attempt;
- Metric natural unique extraction identity;
- Validation natural unique Attempt/protocol/extraction-profile/validator identity plus frozen structured-input comparison;
- one immutable TrialResultSelection per Trial;
- Batch row lock plus deterministic latest structured-summary comparison before creating a new revision.

Therefore:

```text
process_attempt(A)
process_attempt(A)
Coordinator restart
process_attempt(A)
```

converges to the same authoritative result facts and does not duplicate selection or unchanged summary revisions.

Transient operational failure creates no permanent scientific verdict and is retried on a later scan.

Multi-Coordinator production hardening remains outside Slice3 MVP unless a concrete race blocks the Core demo.

## 10. API, events, and minimal SSE

Add read-only result queries:

```text
GET /attempts/{attempt_id}/results
GET /trials/{trial_id}/result
GET /batches/{batch_id}/summary
```

They must answer:

- which Artifact and digest came from the Attempt and whether it is currently available/intact;
- which Metric value and extractor version were used;
- which Dataset/environment warnings or mismatches affected Validation;
- why Validation was valid/invalid/inconclusive/error;
- which Attempt became the Trial result;
- which Trials were included/excluded from BatchSummary and why.

Do not add public commands such as:

```text
POST /process-results
POST /validate
POST /generate-summary
```

These are internal deterministic lifecycle steps.

Minimal RuntimeEvents may be emitted for:

```text
artifact.registered
validation.completed
trial.result_selected
batch.summary_created
```

Authoritative truth remains the Result Plane tables, not a projection.

### 10.1 SSE scope

Roadmap/Core architecture require an SSE-capable UI update surface. Slice3 satisfies this late in the Slice after authoritative tables/query services are working.

The implementation plan must include a small isolated dependency-alignment tranche before SSE if the current FastAPI baseline does not support the chosen tested path.

SSE must be minimal:

- expose result/batch update notifications needed by Core UI semantics;
- consume existing RuntimeEvent/query truth where practical;
- no Projection Worker;
- no generic event platform;
- no requirement that SSE itself become authoritative state.

## 11. Expected implementation areas

Use the already-admitted architecture namespace:

```text
src/labpilot/results_validation/
  __init__.py
  models.py
  types.py
  service.py
  query_service.py
```

Expected integration changes include:

```text
src/labpilot/db/model_registry.py
src/labpilot/experiments/proposal_types.py
src/labpilot/experiments/proposal_verifier.py
src/labpilot/api/schemas/experiments.py
src/labpilot/execution/... narrow TrialExecutionOutcome read contract
src/labpilot/coordinator/service.py
src/labpilot/coordinator/worker.py
src/labpilot/api/dependencies.py
src/labpilot/api/app.py
src/labpilot/api/routers/results.py
src/labpilot/api/schemas/results.py
src/labpilot/main.py
migrations/versions/0011_result_plane.py
```

Architecture dependency guard should be reused rather than renamed for a new `results` owner.

Tests are planned separately in the implementation plan; this design does not freeze exact test filenames.

## 12. Slice3 acceptance

The implementation plan must prove at least these scenarios.

### A. Valid result

```text
exit 0
→ declared result.json
→ safe Artifact registration with integrity digest
→ score extracted
→ Dataset/environment validation evaluated
→ Validation valid
→ Trial selected
→ BatchSummary includes Trial
```

### B. Scientific invalid

```text
exit 0
score below approved threshold
→ execution succeeded
→ Validation invalid
→ no TrialResultSelection
→ BatchSummary excludes Trial with explicit reason
```

### C. Execution failure separation

```text
exit != 0
→ no fabricated scientific-invalid report
→ existing execution/retry semantics remain authoritative
```

### D. Environment and reproducibility semantics

Prove at least:

```text
known required EnvironmentExpectation mismatch → invalid
insufficient required environment observation → inconclusive
best_effort mutable DatasetReference → warning in Validation and Summary
```

### E. Recovery / idempotency

Repeated result processing and Coordinator restart must not duplicate Artifact, MetricObservation, ValidationReport, TrialResultSelection, or unchanged BatchSummaryRevision.

A transient operational failure must create no immutable scientific verdict and a later scan must be able to retry.

### F. Provenance/security regression

Prove at least:

- changing bytes after Artifact registration fails closed;
- symlink/traversal output paths are rejected;
- digest and extraction operate on the same stable content snapshot;
- duplicate Artifact relative paths are rejected by Proposal verification;
- malformed/oversized/duplicate-key/non-finite result JSON is handled deterministically.

### G. Historical hash compatibility

Existing pre-Slice3 Proposal/ExperimentVersion fixtures must retain their historical hashes and continue to load through Slice2 execution binding checks after the optional threshold field is introduced.

### H. Real Slice E2E

Use a small real Batch:

```text
Trial A: score=0.82 → valid → included
Trial B: score=0.61 → invalid → excluded
rule: score >= 0.80
```

The public query surface must show a settled Batch with the valid Trial included and the invalid Trial excluded for the threshold reason.

### I. Core release alignment

Slice3 candidate gate includes:

```text
focused unit tests
+ fresh PostgreSQL integration/migration verification
+ one real host E2E
+ targeted fault injection
+ key security/provenance regressions
+ minimal SSE verification
+ data-egress review for Demo Repository/Dataset/result content
+ Ruff
+ Mypy
+ full GitHub CI at the exact Slice candidate
```

No historical full Architecture-07 processing campaign is required for Slice3 MVP.

## 13. Governance alignment

Slice2 MVP has been validated and integrated into `main` at the planning baseline used by this spec. Slice2 production hardening remains explicitly deferred and is not claimed complete.

That MVP closeout is sufficient to authorize Slice3 JIT design/planning under the current v0.3 Scope/Architecture/Roadmap. Slice3 production code remains unauthorized until this design passes focused re-review and a detailed implementation plan is written and approved.

`DOCUMENT-STATUS.md` is updated on this planning branch to reflect that state; this is governance alignment, not retroactive Slice2 production acceptance.

## 14. Deferred

Explicitly deferred beyond Slice3 Core:

- LLM result interpretation;
- managed Artifact copy / CAS / object store;
- generic result-processing jobs;
- arbitrary extractor plugins;
- advanced summary statistics;
- Projection Worker / richer Timeline;
- automatic historical result reselection/republication;
- full production multi-Coordinator result-processing hardening;
- P1/P2 result platform capabilities from historical Architecture 07.
