# LabPilot V1 Slice3 Result Plane — Focused Plan Review

```text
Date: 2026-09-01
Reviewed plan commit: 3f4b925c33c64d7006613dbfdb4a964c69ac9d71
Reviewed plan:
  docs/superpowers/plans/2026-09-01-labpilot-v1-slice3-result-plane.md
Planning authority baseline:
  ea3d5ced1c886e6484f1ab2a372c0dab547a4deb
Verdict: APPROVE FOR IMPLEMENTATION WITH BINDING CLARIFICATIONS BELOW
Critical: 0
Blocking: 0
Unresolved Important: 0
```

## 1. Review scope

This is the one focused implementation-plan review required by the approved Slice3 design. It checks the Detailed Implementation Plan against:

```text
V1 Scope v0.3
Architecture 10 v0.3
Master Roadmap v0.3
S2→S3 Sequencing Amendment v0.3.1
approved Slice3 Result Plane design
focused Slice3 design re-review
actual main@3e3cebf code/test/dependency boundaries
```

It does not reopen Architecture 07, does not claim Slice2 production acceptance, and does not add production-hardening work unrelated to the Slice3 MVP.

## 2. Coverage result

The plan covers the approved Result Plane end to end:

```text
terminal successful Attempt
→ declared Artifact provenance
→ MetricObservation with extractor semantic version
→ Dataset / Environment / scientific ValidationReport
→ immutable first-eligible TrialResultSelection
→ BatchSummaryRevision
→ read APIs
→ minimal SSE notification
→ real native valid/invalid acceptance
```

It also covers the previously required guards:

```text
historical Proposal/Version hash compatibility
execution-owned retry/exhaustion
transient operational failure != scientific verdict
safe path/symlink handling
post-validation Artifact mutation cannot rewrite history
best_effort Dataset reproducibility warning
observed EnvironmentExpectation validation
Coordinator restart/idempotency
fault/provenance/security/data-egress candidate gates
minimal hash budget
```

The implementation is split into bounded TDD tasks and keeps the FastAPI/SSE dependency refresh separate from Result Plane core changes.

## 3. Repository-path and placeholder audit

The repository tree at the reviewed branch was checked against every pre-existing path named by the plan. Existing source/test/support paths used by the plan are present, including:

```text
src/labpilot/experiments/proposal_types.py
src/labpilot/experiments/proposal_service.py
src/labpilot/experiments/proposal_verifier.py
src/labpilot/execution/batch_service.py
src/labpilot/coordinator/service.py
src/labpilot/coordinator/worker.py
src/labpilot/observability/events.py
tests/integration/test_batch_commands.py
tests/integration/test_coordinator_reconciliation.py
tests/integration/test_attempt_execution_service.py
tests/integration/db/test_migrations.py
tests/acceptance/slice2/test_native_slice2.py
tests/acceptance/slice2/training_fixture/train.py
scripts/check_ci.sh
tests/scripts/test_verify_architecture_dependencies.sh
```

Paths under `results_validation`, Slice3-specific tests, migration `0011`, and Slice3 acceptance files are intentionally new.

Placeholder scan found no `TBD`, `TODO`, or unresolved “similar to” implementation placeholder in the reviewed plan.

## 4. Binding clarification C1 — generic Artifact identity vs metric-source capture

The plan's Task 4 uses `MAX_RESULT_JSON_BYTES = 1 MiB`. That limit applies to the `result-json-v1` extraction input, not to every declared Artifact.

Implementation must distinguish:

```text
all declared regular-file Artifacts
→ stable descriptor-based stream
→ byte_size + SHA-256 identity
→ no requirement to retain the full file in RAM

Artifact used as result-json-v1 Metric source
→ same stable descriptor/read generation
→ must be <= MAX_RESULT_JSON_BYTES
→ capture exact bytes used for both SHA-256 and JSON extraction
```

For non-metric Artifacts, stream SHA-256 in fixed-size chunks and compare the opening/closing `fstat` identity/size before accepting the registration. Do not impose the 1 MiB JSON extraction limit on arbitrary declared binary Artifacts merely because they also need provenance identity.

This clarification preserves the approved safe-read invariant without accidentally turning Slice3 into an Artifact-size policy change.

## 5. Binding clarification C2 — successful execution without a current ValidationReport is not summary-ready

`TrialExecutionOutcome.successful(attempt_id)` means the Execution Plane is settled; it does **not** mean Result Plane processing is settled.

`BatchSummaryService` must therefore apply:

```text
Execution outcome successful
+ no ValidationReport for active protocol/extractor/validator semantics
→ BatchSummary not_ready
→ no formal revision

ValidationReport valid
+ expected TrialResultSelection exists and binds that report/Attempt
→ included

ValidationReport valid
+ selection absent or inconsistent
→ fail closed / result-state conflict
→ no formal revision

ValidationReport invalid | inconclusive | deterministic error
→ settled scientific exclusion with explicit reason
```

A transient result-processing failure must never cause a successful execution to be prematurely frozen as an excluded scientific result.

Task 7 tests must include the no-report and valid-with-missing-selection cases.

## 6. Binding clarification C3 — one exact decimal-text renderer

Do not duplicate or approximate decimal normalization with ad-hoc `format(..., "f")` rules.

For places that require canonical decimal **text** inside JSONB/event/fixture JSON, use the existing public canonical encoder:

```python
from decimal import Decimal
from labpilot.common.canonical_json import canonical_json_bytes


def canonical_decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("decimal must be finite")
    return canonical_json_bytes(value).decode("utf-8")
```

Apply this to:

```text
new threshold values persisted in Proposal/Version JSON
Validation input/reason/event decimal text
BatchSummary metric/aggregate decimal text
training fixture --score JSON number rendering
```

The in-memory/canonical hash representation for threshold rules remains `Decimal`; legacy validation rules still omit the threshold key entirely.

This resolves the plan's remaining ambiguity around “canonical decimal rendering” without adding any new hash field.

## 7. Binding clarification C4 — use a maintained FastAPI SSE baseline

The plan correctly isolates FastAPI refresh before SSE, but must not intentionally pin to the first `0.135.x` SSE release line.

Official FastAPI release notes show:

```text
0.135.0   native SSE introduced
0.136.2   SSE related fix
0.140.12  SSE line-splitting compliance fix
0.140.13  SSE EventSourceResponse status_code support
0.141.1   current stable release observed during this review
```

For Task 11 use:

```toml
"fastapi>=0.140.13,<0.142",
```

then let `uv lock` choose the compatible release and run the dependency-only API/Pydantic/Mypy gate before any SSE code is added.

If the compatible resolver result has changed by implementation time, use the latest tested compatible release satisfying the Roadmap's `>=0.135.0` requirement, but do not downgrade below the SSE fixes above merely to preserve an old pin.

## 8. Binding clarification C5 — reuse the existing RuntimeEvent write contract

Result Plane must not invent a second event writer.

Task 6/7 events must use the existing observability API:

```python
from labpilot.observability.events import RuntimeEventEnvelope, append_runtime_event
from labpilot.observability.payloads import prepare_runtime_event_payload
```

with the existing Batch aggregate convention:

```text
aggregate_type = "batch"
aggregate_id = str(batch_id)
topic = "runtime-events"
partition_key = f"batch:{batch_id}"
actor_type = "system"
enqueue_outbox = False
```

The same aggregate sequence is the cursor later exposed through Batch result SSE.

## 9. Hash-budget check

The plan is compliant with the explicit minimal-hash constraint:

```text
new Artifact.content_sha256       REQUIRED physical-content integrity
existing validation/proposal hashes REUSED
new input/metric/summary/selection hashes NOT ADDED
new document hash manifests       NOT ADDED
```

No additional identity graph is authorized by this review.

## 10. Final plan verdict

All findings discovered during focused plan review are resolved by the binding clarifications in this document; there is no unresolved Critical, Blocking, or Important issue.

```text
Detailed Implementation Plan: APPROVED
Critical: 0
Blocking: 0
Unresolved Important: 0
Slice3 implementation: AUTHORIZED after the status/baseline closure commit
Slice2 production acceptance: NO
Slice2 production hardening: DEFERRED
```

The implementation agent must read **both** the Detailed Implementation Plan and this focused review before coding. Where the two differ on C1-C5, this review is the later, narrower authority.

The next action is one small status/baseline closure, then creation of an isolated implementation worktree/branch. No production code is to be written on the documentation branch.
