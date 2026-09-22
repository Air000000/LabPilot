# LabPilot P1 Result-Aware Experiment Iteration — Implementation Closeout

Date: 2026-09-22

Status: Accepted

## 1. Authority

- Design: `docs/superpowers/specs/2026-09-20-labpilot-p1-result-aware-iteration-design.md`
- Implementation plan: `docs/superpowers/plans/2026-09-20-labpilot-p1-result-aware-iteration.md`
- PR: #9
- Last production-code commit: `ae079be252fd62eff415ef040af4228ab45e2d61`
- Final pre-closeout acceptance/test baseline: `e8caaf668fed2b5e5878393fa0c47c001ba218e9`

## 2. Implemented loop

```text
Experiment E
→ ProposalRevision R1
→ deterministic verification
→ Human Approval
→ ExperimentVersion V1
→ Batch B1
→ deterministic Result processing
→ exact BatchSummaryRevision S1
→ Follow-up AgentRun A2
→ Stop
  OR
→ Follow-up ProposalRevision R2 in the same Experiment
→ deterministic verification
→ Human Approval
→ ExperimentVersion V2
```

No automatic Batch is created for V2.

## 3. Accepted invariants

- Follow-up AgentRun pins the exact BatchSummaryRevision; admission accepts only latest-at-create under the BatchRow lock.
- A run already created against S(N) remains pinned when S(N+1) is later persisted.
- ResultContext loads the exact pinned summary and exposes selected valid evidence, exclusions, and Trial conditions.
- Raw file contents do not enter ResultContext; later raw MetricObservation mutation does not override the pinned summary.
- Follow-up Proposal stays in the same Experiment; R2 and V2 numbers increment.
- Proposal crash/retry returns the same R2 and does not create R3.
- Agent cannot auto-verify, auto-approve, or auto-create/start a Batch.
- The initial Agent path remains compatible and remote provider local-only egress remains blocked.

## 4. Test and CI evidence

- Task 7 deterministic acceptance: PASS.
- Task 8 focused regression: PASS.
- Task 8 concurrency barrier repeated 5x: PASS.
- Task 8 GitHub CI #312: SUCCESS.
- `./scripts/check_ci.sh`: repository-equivalent full gate PASS.
- Ruff: PASS; Mypy: PASS; unit/architecture: PASS; integration: PASS; dependency shell guard: PASS.

## 5. Not established

This closeout does NOT establish:

- autonomous research Agent;
- automatic approval or next-Batch execution;
- controlled/autonomous code modification or editable Agent Worktree;
- Multi-Agent or cross-Experiment historical search;
- global exactly-once execution;
- Slice 2 production acceptance or production-scale scheduling/readiness;
- measured researcher productivity improvement.

The product claim remains: bounded Agent-assisted Experiment Runtime / Harness.
