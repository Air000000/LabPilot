# Slice3 Result Plane closeout

## Frozen implementation and CI metadata

```text
Product candidate: e8d267b070d204c089c24a37144bd1e462c04c07
DB head: 0012_version_identity_nonunique

Canonical CI run: r1-20260913T051049Z-13411
Ruff: PASS
Mypy: PASS
unit/architecture: 1163 passed
integration: 261 passed / 1 skipped / 5 deselected
dependency guard: PASS
Canonical log SHA256: b6d9b347dc3e318d5c001f6f5a115975ef5c03e5ec964f991c91df76a5f7de94
```

## Result and acceptance boundary

Slice3 Result Plane implementation is complete. Canonical CI is green and bounded
real-native validation exercised the core Result path.

B5 real native execution persisted the expected Result Plane facts:

```text
Artifact +4
MetricObservation +2
ValidationReport +2
TrialResultSelection +1
BatchSummaryRevision +1

0.82 -> valid -> selected
0.61 -> invalid -> METRIC_BELOW_THRESHOLD -> not selected
Batch summary: complete_with_invalid_results
```

Native testing found and drove fixes for the global ExperimentVersion semantic
identity uniqueness defect (`945cb3207859b8fa0162335bf2e7f3fa1ae6b9c4`,
`0012_version_identity_nonunique`) and the Result API/container artifact
availability boundary (`e8d267b070d204c089c24a37144bd1e462c04c07`). The
historical B5 projection regression is fixed: `500 -> HTTP 200 + missing` while
history is retained.

The final full native acceptance did not complete because the external acceptance
supervisor failed before starting the B6 native child. It is classified as
`ACCEPTANCE_INFRASTRUCTURE_PRE_CHILD_FAILURE`:

```text
B6 child_started_at: null
started.json: absent
native.log: absent
supervisor-result.json: absent
PRODUCT_CODE_REACHED_BY_B6=NO
RESULT_PLANE_REACHED_BY_B6=NO
B6_PRODUCT_FAILURE_PROVEN=NO
B7 required for merge=NO
B7_RUN=NO
```

No additional Product defect was established. Earlier B1, B2, and B4 failures
were harness/environment/launch-contract failures before the relevant Product
path; B3 was the now-fixed identity defect.

## Deferred and rollback boundary

Deferred beyond Slice3: acceptance supervisor durable-marker reliability. A
transient/systemd supervisor can report success without producing `started.json`,
`native.log`, or `supervisor-result.json`. This affects acceptance-evidence
reliability, not established Product behavior.

`DB_DOWNGRADE_AFTER_DUPLICATE_IDENTITY=PROHIBITED`: once multiple
ExperimentVersion rows have the same semantic identity under migration `0012`,
downgrading to the old global UNIQUE constraint is not generally valid.
Application rollback may leave the DB at `0012`.
