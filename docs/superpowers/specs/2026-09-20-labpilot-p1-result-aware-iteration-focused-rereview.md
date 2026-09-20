# P1 Result-Aware Experiment Iteration Focused Design Re-review

**Date:** 2026-09-20  
**Reviewed design SHA:** `60c0596f016a96fcf4a5d02114b7c5f23f5fcb65`  
**Main SHA at review:** `bf26f9ffcfed02542f4944e160226f3437355706`  
**Verdict:** APPROVE

## Review history

Initial focused review returned:

```text
CRITICAL_COUNT=0
IMPORTANT_COUNT=1
MINOR_COUNT=1
```

The single blocking finding was `P1-RESULT-001`: the original design required the supplied BatchSummaryRevision to be latest at Follow-up AgentRun creation, but did not define a concurrency linearization boundary against concurrent Summary refresh.

The remediation aligned Follow-up creation with the existing Summary refresh synchronization boundary:

```text
one UnitOfWork
→ load supplied BatchSummaryRevision to identify batch_id
→ SELECT owning BatchRow ... FOR UPDATE
→ re-read supplied revision under the lock
→ query highest persisted revision for the Batch
→ require supplied revision == highest persisted revision
→ perform remaining lineage/snapshot checks
→ insert AgentRun bound to the exact source summary revision
→ commit the same UnitOfWork
```

This gives a linearizable latest-at-create meaning:

- if Summary refresh obtains the BatchRow lock first and persists N+1, the waiting Follow-up creation observes N+1 and rejects supplied N;
- if Follow-up creation obtains the lock first, verifies N as current highest, persists the AgentRun, and commits, a later Summary refresh may create N+1 without invalidating the historical AgentRun binding to N.

The minor finding was also closed: the current BatchSummaryRevision domain has no independent publish/status field. "Latest" therefore means the highest persisted revision number for that Batch, and presentation status is derived from persisted summary contents.

## Final finding-specific re-review

```text
FINDING_SPECIFIC_REREVIEW=APPROVE
P1_RESULT_001=CLOSED
MINOR_001=CLOSED
DIRECT_REGRESSIONS=NONE
```

All previously passing design decisions remained unchanged. Detailed implementation planning is authorized. Product implementation remains gated on an approved detailed implementation plan.
