# LabPilot V1 Slice3 — Minimal Result Plane Design

```text
Status: Draft for user review
Date: 2026-08-31
Planning baseline: 3e3cebf32877cfe8d65f76a393eefb20ac41d798
Scope: Slice3 only
```

## 1. Outcome

Slice3 closes the V1 result loop:

```text
terminal Attempt
→ Artifact
→ Metric
→ deterministic ValidationReport
→ Trial eligibility
→ BatchSummary
```

It must preserve the boundary:

```text
execution success != scientific validity
```

The required negative example is:

```text
process exit code = 0
but score violates the approved validation rule
→ execution succeeded
→ Validation invalid
→ Trial ineligible
→ excluded from BatchSummary
```

This design is intentionally smaller than the historical Architecture 07 processing platform. Current v0.3 Scope / Architecture / Roadmap are authoritative for V1 Core and explicitly allow a simplified Result Plane.

## 2. Non-goals

Slice3 Core does not build:

- LLM result interpretation or automatic scientific conclusions;
- generic ProcessingJob / LogicalRun / ProcessingRevision infrastructure;
- dynamic extractor or validator plugins;
- full Artifact CAS/object storage;
- Projection Worker as a Core dependency;
- automatic best-result selection;
- advanced statistics, hypothesis testing, or paper-claim governance.

LLM result interpretation remains P1.

## 3. Hash budget

Do not add hashes merely for bookkeeping.

Rules:

1. Keep existing ExperimentVersion / Proposal hashes already owned by upstream contracts; Slice3 does not expand that hash graph.
2. Reuse the existing `validation_protocol_hash` already frozen on ExperimentVersion.
3. Use SHA-256 for Artifact bytes because content integrity must be independently verifiable.
4. Do not add new `input_identity_hash`, `summary_hash`, `metric_hash`, or similar fields when IDs, versions, immutable structured snapshots, row locks, and unique constraints are sufficient.
5. Validation input identity is stored as an immutable structured snapshot, not as another digest.
6. Summary inputs are stored as immutable included/excluded references, not as another digest.

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

No managed-copy/CAS requirement in Slice3 MVP. The Artifact points to the controlled Attempt output path and records integrity metadata.

### 4.2 MetricObservation

A MetricObservation is an immutable deterministic extraction from an Artifact.

Minimum fields:

```text
metric_observation_id
artifact_id
attempt_id
metric_name
split nullable
numeric_value NUMERIC
extractor_version
created_at
```

MVP extractor:

```text
result-json-v1
```

Natural uniqueness:

```text
UNIQUE(artifact_id, metric_name, split, extractor_version)
```

### 4.3 ValidationReport

A ValidationReport is an immutable deterministic verdict over the frozen Version validation protocol and the observed Attempt result inputs.

Minimum fields:

```text
validation_report_id
attempt_id
validation_protocol_hash
validator_version
input_snapshot_json
verdict
reason_json
created_at
```

Natural uniqueness:

```text
UNIQUE(attempt_id, validation_protocol_hash, validator_version)
```

`input_snapshot_json` freezes the facts used by validation, including Artifact IDs/digests, MetricObservation IDs/values, and explicit missing markers. A later rerun must compare current observed inputs to the persisted snapshot. A mismatch is a provenance conflict; it does not silently create a new interpretation under the same validator/protocol.

Verdicts:

```text
valid
invalid
inconclusive
error
```

Semantics:

- `valid`: all required deterministic rules pass.
- `invalid`: inputs are known and a scientific/output requirement is violated.
- `inconclusive`: available deterministic facts are insufficient to decide, without evidence of corruption or validator failure.
- `error`: provenance conflict, corrupted input, internal validator failure, or inconsistent authoritative state prevents a reliable judgment.

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
- selection is first-eligible, never highest-metric;
- selection is immutable;
- Trial row lock plus `UNIQUE(trial_id)` serializes concurrent selection.

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
aggregates_json
created_at
```

Constraints:

```text
UNIQUE(batch_id, revision_number)
```

Idempotent refresh does not require an input hash. The service locks the Batch, derives the current structured summary inputs, compares them with the latest revision, and creates a new revision only when those structured inputs differ.

MVP aggregates:

```text
count
mean
```

`min` / `max` may be added if trivial, but are not acceptance requirements.

## 5. Experiment contract extension

Current Proposal semantics already declare Artifacts, Metrics, and Validation rules. Slice3 adds only threshold validation needed for meaningful scientific invalidity.

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
- Proposal verifier profile/version must change because verifier semantics change.

Example:

```json
[
  {"kind":"artifact_exists","subject":"result","threshold":null},
  {"kind":"metric_finite","subject":"score","threshold":null},
  {"kind":"metric_gte","subject":"score","threshold":0.8}
]
```

## 6. Training fixture contract

The existing execution oracle remains execution-only.

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

`workload-test-oracle.json` must never be treated as the scientific result Artifact.

Existing Slice2 calls that do not request a score keep their current behavior.

## 7. Processing flow

The Coordinator remains the trigger owner; result semantics stay inside ResultApplicationService.

Periodic scan order becomes conceptually:

```text
control existing work
→ reconcile physical work
→ process successful terminal results
→ prepare eligible retries/new work
→ admit launch-ready work
→ refresh settled Batch summaries
```

### 7.1 Eligibility for result processing

Only Attempts satisfying all of the following enter the scientific result pipeline:

```text
status = terminal
terminal_kind = process_exited
exit_code = 0
```

Non-zero exit, signal termination, launch failure, unknown state, or identity conflict remain execution facts and must not be rewritten as scientific `invalid`.

### 7.2 Artifact collection

Collection is declaration-driven, not directory-guessing.

For each Artifact declaration from the immutable approved Proposal:

1. resolve it under `<attempt workspace>/runtime/output`;
2. enforce safe relative-path containment;
3. inspect existence/type;
4. compute size and SHA-256 only when a file exists;
5. register the immutable Artifact slot;
6. if a previously registered slot now contains different bytes, fail closed with provenance conflict.

A missing required Artifact becomes a known validation input fact and can produce `invalid`.

### 7.3 Metric extraction

`result-json-v1` parses the declared source Artifact and extracts named metrics from `metrics{}` using Decimal-compatible numeric semantics.

Dynamic user code is not supported in Core.

### 7.4 Validation

Validation reads:

```text
ExperimentVersion.validation_protocol_json/hash
+ immutable Artifact registrations / missing facts
+ MetricObservations
```

It produces one immutable ValidationReport per Attempt/protocol/validator semantic version.

Examples:

```text
score=0.82, metric_gte(score,0.80) → valid
score=0.61, metric_gte(score,0.80) → invalid
required result Artifact missing → invalid
registered Artifact digest no longer matches bytes → error
```

### 7.5 First-eligible selection

When a report is valid:

```text
BEGIN
SELECT Trial ... FOR UPDATE
if no TrialResultSelection exists:
    insert TrialResultSelection
COMMIT
```

Later valid Attempts cannot replace the selected result because of a better metric.

### 7.6 Summary generation

A formal Batch summary is generated only when no Trial remains in unresolved execution/reconciliation state.

For each Trial:

- selected valid result → included;
- scientifically invalid → excluded with reason;
- inconclusive → excluded with reason;
- exhausted execution failure → excluded with reason;
- unknown / identity-conflict / unresolved reconciliation → block formal summary.

Result presentation status:

```text
complete
complete_with_invalid_results
not_ready
```

`complete_with_invalid_results` means the Batch is settled but at least one Trial is excluded; exact excluded reason remains explicit in the summary.

## 8. Idempotency and recovery

Slice3 does not introduce ProcessingJob because deterministic rerun is sufficient.

Convergence mechanisms:

- Artifact natural unique slot per Attempt;
- Metric natural unique extraction identity;
- Validation natural unique Attempt/protocol/validator identity plus frozen `input_snapshot_json` comparison;
- one immutable TrialResultSelection per Trial;
- Batch row lock plus latest structured-summary comparison before creating a new revision.

Therefore:

```text
process_attempt(A)
process_attempt(A)
Coordinator restart
process_attempt(A)
```

converges to the same authoritative result facts and does not duplicate selection or summary revisions.

Multi-Coordinator production hardening remains outside Slice3 MVP unless a concrete race blocks the Core demo.

## 9. API surface

Add read-only result queries:

```text
GET /attempts/{attempt_id}/results
GET /trials/{trial_id}/result
GET /batches/{batch_id}/summary
```

They must answer:

- which Artifact and digest came from the Attempt;
- which Metric value and extractor version were used;
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

Authoritative query truth remains the Result Plane tables, not a projection.

## 10. Expected implementation areas

New owner:

```text
src/labpilot/results/
  __init__.py
  models.py
  types.py
  service.py
  query_service.py
```

Expected integration changes:

```text
src/labpilot/db/model_registry.py
src/labpilot/experiments/proposal_types.py
src/labpilot/experiments/proposal_verifier.py
src/labpilot/api/schemas/experiments.py
src/labpilot/coordinator/service.py
src/labpilot/coordinator/worker.py
src/labpilot/api/dependencies.py
src/labpilot/api/app.py
src/labpilot/api/routers/results.py
src/labpilot/api/schemas/results.py
src/labpilot/main.py
migrations/versions/0011_result_plane.py
```

Tests are planned separately in the implementation plan; this design does not freeze exact test filenames.

## 11. Slice3 acceptance

The implementation plan must prove at least these scenarios.

### A. Valid result

```text
exit 0
→ declared result.json
→ Artifact registered with integrity digest
→ score extracted
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

### D. Recovery / idempotency

Repeated result processing and Coordinator restart must not duplicate Artifact, Metric, ValidationReport, Trial selection, or unchanged BatchSummary revision.

### E. Provenance corruption

After an Artifact slot is registered, changing the bytes at that slot must fail closed as provenance conflict; it must not create a second formal Artifact for the same Attempt declaration.

### F. Real Slice E2E

Use a small real Batch:

```text
Trial A: score=0.82 → valid → included
Trial B: score=0.61 → invalid → excluded
rule: score >= 0.80
```

The public query surface must show a settled Batch with the valid Trial included and the invalid Trial excluded for the threshold reason.

Gate:

```text
focused unit tests
+ PostgreSQL integration
+ one real host E2E
+ Ruff
+ Mypy
+ full GitHub CI at Slice candidate
```

No historical full Architecture-07 processing campaign is required for Slice3 MVP.

## 12. Deferred

Explicitly deferred beyond Slice3 Core:

- LLM result interpretation;
- managed Artifact copy / CAS / object store;
- generic result-processing jobs;
- arbitrary extractor plugins;
- advanced summary statistics;
- Projection Worker / richer Timeline;
- full production multi-Coordinator result-processing hardening;
- P1/P2 result platform capabilities from historical Architecture 07.
