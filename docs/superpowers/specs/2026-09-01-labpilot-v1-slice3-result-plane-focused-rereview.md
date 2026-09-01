# LabPilot V1 Slice3 Result Plane — Focused Re-review

```text
Date: 2026-09-01
Reviewed branch baseline: a777377db2d4baa08575366c05e67ab245f0a769
Reviewed design:
  docs/superpowers/specs/2026-08-31-labpilot-v1-slice3-result-plane-design.md
Verdict: APPROVE FOR DETAILED IMPLEMENTATION PLANNING
Critical: 0
Blocking: 0
```

## 1. Review scope

This is the single focused re-review requested after the prior `CHANGES REQUIRED` result. It does not reopen historical Architecture 07 and does not authorize production code by itself.

The review checks the revised design against current V1 Scope v0.3, Architecture 10 v0.3, Master Roadmap v0.3, current Slice1/Slice2 contracts, and the actual dependency/runtime boundaries on `main`.

The revised design now closes the previously identified blockers:

```text
B1 Dataset / Environment validation semantics       PASS
B2 execution-owned TrialExecutionOutcome contract  PASS
B3 backward-compatible threshold canonicalization  PASS
B4 operational failure != scientific error         PASS
```

Important corrections are also represented in the design:

```text
Artifact safe-read / symlink containment            PASS
Artifact relative-path uniqueness at verification  PASS
strict result-json-v1 grammar                       PASS
results_validation namespace                        PASS
minimal SSE retained without Projection Worker      PASS
Core acceptance gate alignment                      PASS
minimal hash budget                                  PASS
```

## 2. Normative clarifications from focused re-review

Two precision points found during this final pass are normative for the implementation plan.

### 2.1 Post-validation Artifact mutation

`Artifact.content_sha256` records the bytes accepted for the formal Artifact slot. A later mutation of the workload-owned output path must never rewrite historical scientific facts.

Behavior is fixed as follows:

```text
Artifact registered
+ no ValidationReport committed yet
+ bytes no longer match registered digest
→ deterministic provenance contradiction
→ ValidationReport.error may be committed

Artifact registered
+ ValidationReport already committed
+ bytes later change/disappear
→ existing ValidationReport remains immutable
→ existing TrialResultSelection remains immutable
→ existing BatchSummaryRevision remains immutable historical fact
→ current Artifact availability is derived as corrupted/missing
→ explicit reprocessing fails closed with provenance conflict
→ do not append a second report under the same semantic versions
→ do not silently present the corrupted bytes as the validated result
```

The implementation therefore distinguishes historical validation truth from current local Artifact availability. Slice3 does not build an ArtifactAvailability table or historical reselection workflow.

### 2.2 SSE dependency baseline

Master Roadmap v0.3 already freezes the runtime alignment requirement: before implementing the Core SSE endpoint, FastAPI must be refreshed to a tested `>=0.135.0` baseline, or a later compatible baseline.

The implementation plan must therefore use a separate dependency-alignment tranche:

```text
Result Plane authoritative tables/query path working
→ upgrade FastAPI to tested >=0.135.0 compatible baseline
→ refresh lockfile
→ run focused API/Pydantic/integration regression
→ implement minimal SSE
```

Do not maintain custom SSE plumbing merely to preserve the historical `fastapi>=0.116,<0.117` pin.

## 3. Architecture guard precision

The current architecture graph already admits the `results_validation` owner for `coordinator` and `api`. Keep that namespace.

However, `tests/architecture/dependency_rules.py` has a path-specific `MODEL_REGISTRY_ALLOWED_IMPORTS` list that does not yet include:

```text
labpilot.results_validation.models
```

When `src/labpilot/db/model_registry.py` starts importing the new result models, the implementation plan must make the narrow corresponding architecture-test allowlist update. This is not an architecture rebaseline and must not broaden unrelated dependency edges.

## 4. Approved design boundary

The approved Slice3 design remains:

```text
Attempt
→ Artifact
→ MetricObservation
→ ValidationReport
→ TrialResultSelection
→ BatchSummaryRevision
```

with these fixed boundaries:

- only `terminal + process_exited + exit_code=0` enters scientific result processing;
- execution retry/exhaustion remains execution-owned;
- scientific validity never rewrites Attempt execution facts;
- first eligible valid Attempt is selected once and is not automatically replaced by later semantic interpretations;
- `best_effort` Dataset semantics produce explicit reproducibility warnings in Validation and Summary;
- EnvironmentExpectation is checked deterministically against Attempt observed environment;
- transient operational failure commits no scientific verdict and is retried later;
- old `artifact_exists` / `metric_finite` canonical JSON remains byte-semantically compatible for existing Proposal/Version hashes;
- new threshold rules add `threshold` only for `metric_gte` / `metric_lte`;
- Artifact content digest is retained because it protects provenance; Slice3 adds no bookkeeping hash graph;
- no ProcessingJob, CAS, Projection Worker, generic result platform, or LLM scientific eligibility judge is introduced.

## 5. Authorization result

Focused re-review result:

```text
Design direction: APPROVED
Design blockers: NONE
Detailed implementation planning: AUTHORIZED
Slice3 implementation code: NOT YET AUTHORIZED
```

The next step is to write the Slice3 Detailed Implementation Plan against a planning baseline that contains this review and the aligned `DOCUMENT-STATUS.md`. The plan must receive its focused review/approval before an implementation worktree is created.
