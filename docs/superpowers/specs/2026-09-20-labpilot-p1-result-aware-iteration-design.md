# LabPilot P1 Result-Aware Experiment Iteration Design

**Date:** 2026-09-20  
**Status:** Proposed / focused review pending  
**Planning authority root:** `032dd87d3366dc6edd66fb75451d44047e49f7dd`  
**Current repository baseline:** `bf26f9ffcfed02542f4944e160226f3437355706`

## 1. Purpose

After the Core execution/result path and the competition browser Runtime/E2E path are accepted, the next high-value V1 capability is to let the bounded Agent consume trusted experiment results and help form the next experiment revision.

This tranche is deliberately narrower than an autonomous research loop:

```text
ExperimentVersion Vn
→ Batch / Trial / Attempt
→ deterministic Validation / Result Selection
→ BatchSummaryRevision
→ Follow-up AgentRun
→ Stop | Request Human Input | Proposal
→ deterministic Proposal Verification
→ Human Approval
→ ExperimentVersion Vn+1
```

The Agent interprets and proposes. Existing domain services remain authoritative for scientific facts, approval, version materialization, execution, recovery, validation, and result selection.

## 2. Why this tranche is admitted now

V1 Scope v0.3 and Master Roadmap v0.3 list `result interpretation` and richer follow-up proposal behavior as P1 candidates to be ordered after the Core Demo.

The competition workstream has since established and frozen:

- a fresh browser Golden Path from research goal through Batch Summary;
- a same-ExperimentVersion recovery replay with no duplicate physical launch observed;
- exact-authority full CI on the accepted competition product baseline.

The post-competition repository also carries the validated acceptance correction for the API's exact read-only Attempt workspace bind. This design therefore does not reopen the accepted execution/result planes. It adds one bounded consumer above them.

## 3. Normative authority and selectively reactivated historical invariants

Normative precedence remains:

```text
V1 Scope v0.3
> Architecture 10 v0.3
> Master Roadmap v0.3
> this design after approval
> detailed implementation plan after approval
```

The following historical Agent-Harness invariants are explicitly reactivated for this tranche because they are compatible with current v0.3 authority:

1. Agent code may read structured Batch/Trial/Attempt/result summaries but may not directly advance their state.
2. Result interpretation is grounded in selected/eligible result evidence and an explicitly versioned deterministic Batch Summary.
3. A follow-up Proposal may be created from results, but the Agent may not directly rerun a Trial, select the best Attempt, treat an invalid result as authoritative evidence, or approve the follow-up experiment.
4. Hidden chain-of-thought is not stored or required.

No other Architecture 02–07 v0.2 requirement becomes normative merely because this design cites these specific invariants.

## 4. Scope

### 4.1 P1-A — exact ResultContext + Follow-up AgentRun

Add a deterministic `ResultContext` bound to one exact `BatchSummaryRevision`.

Create a new Follow-up AgentRun that:

- derives the owning Experiment/ExperimentVersion/Project from the source summary lineage;
- binds the exact source `batch_summary_revision_id`;
- remains bound to a clean RepositorySnapshot;
- receives the ResultContext through ContextCompiler;
- may continue to use existing bounded repository-read tools;
- uses the existing Agent decision surface:
  `tool_call | request_human_input | propose | stop`.

No new result-reading Tool is required in P1-A.

### 4.2 P1-B — same-Experiment Follow-up Proposal

When a Follow-up AgentRun returns `propose`:

- the resulting ProposalRevision belongs to the Experiment that owns the bound source summary;
- it is assigned the next Proposal revision number in that Experiment;
- it preserves `origin_agent_step_id` replay/idempotency semantics;
- it passes the existing deterministic verification path;
- human approval remains mandatory;
- approval creates the next immutable ExperimentVersion number in the same Experiment;
- no Batch is created or started automatically.

### 4.3 Deferred P1-C — historical experiment comparison

Only after P1-A/B are accepted, add bounded structured history retrieval across earlier versions/batches in the same Experiment.

This later tranche may justify one or more read-only history Tools. It is intentionally excluded from P1-A/B so the first implementation does not simultaneously change Result access, Tool Registry behavior, Agent output semantics, and history ranking.

## 5. Explicit non-goals

This tranche does not implement:

- controlled or autonomous code modification;
- editable Worktree workflow;
- cross-Experiment semantic search;
- vector/long-term memory;
- multi-Agent orchestration;
- LLM semantic verification;
- automatic Batch creation/start after a proposal;
- automatic proposal approval;
- automatic repeated looping until a target is met;
- execution/recovery redesign;
- Result Plane redesign;
- competition UI rewrite;
- deferred Slice 2 production hardening.

## 6. Domain ownership

### 6.1 Experiment remains the scientific iteration aggregate

The same research objective remains one Experiment across follow-up iterations.

```text
Experiment E
├─ ProposalRevision R1
├─ ExperimentVersion V1
│  └─ Batch B1
│     └─ BatchSummaryRevision S1
├─ Follow-up AgentRun A2  [bound to S1]
├─ ProposalRevision R2
└─ ExperimentVersion V2
```

A different Experiment is created only when the researcher intentionally starts a different research objective. The follow-up Agent does not implicitly split the lineage.

### 6.2 AgentRun remains non-authoritative for execution and results

A Follow-up AgentRun may produce interpretation text and a candidate Proposal. It does not own:

- Batch status;
- Trial eligibility;
- Attempt status;
- Validation verdict;
- Result selection;
- Batch summary;
- approval state;
- ExperimentVersion materialization.

### 6.3 ResultContext is a read model, not a new scientific authority

`ResultContext` is deterministically assembled from existing authoritative records. It is not persisted as a second result ledger unless implementation evidence later proves a durable independent lifecycle is required.

The exact `source_batch_summary_revision_id` is persisted on the Follow-up AgentRun so the model request can always be traced back to the source facts.

## 7. Follow-up AgentRun binding

The current AgentRun has no Experiment/result binding. P1-A adds the minimum durable provenance required for follow-up planning.

Conceptually:

```text
AgentRun
- agent_run_id
- project_id
- repository_snapshot_id
- source_batch_summary_revision_id?    # null for initial planning runs
- goal
- existing budget/provider/state fields
```

Do not duplicate `bound_experiment_id` on AgentRun. The current schema already provides an immutable/restricted lineage:

```text
BatchSummaryRevision
→ Batch
→ ExperimentVersion
→ Experiment
```

Those relationships are protected by foreign keys with `ON DELETE RESTRICT`. The public follow-up creation command therefore accepts the exact source summary revision and derives the owning Experiment instead of persisting or trusting a second Experiment identifier.

Required creation checks:

1. source BatchSummaryRevision exists;
2. it belongs to a Batch whose ExperimentVersion and Experiment exist;
3. the Experiment belongs to the same Project as the requested RepositorySnapshot;
4. the source summary was the latest published revision for that Batch at Follow-up AgentRun creation time;
5. the source summary is in a complete presentation state;
6. the RepositorySnapshot is clean;
7. for the first P1-A/B acceptance path, the follow-up RepositorySnapshot identity equals the source ExperimentVersion RepositorySnapshot identity.

Rule 7 deliberately keeps the first result-aware loop parameter/configuration-only. Editable code changes and a new RepositorySnapshot are deferred to the later controlled-code-modification tranche.

After creation, the AgentRun remains pinned to the exact source summary revision even if a later BatchSummaryRevision is published. A later semantic/result revision requires a new Follow-up AgentRun rather than mutating the existing request provenance.

## 8. Deterministic ResultContext

### 8.1 Contents

The ResultContext builder should expose only bounded structured facts needed for next-iteration reasoning. The approved-design portion uses the exact canonical source ProposalPayload referenced by the source ExperimentVersion; a narrower hand-maintained projection would create a second proposal contract and make revision generation lossy.

```text
source lineage
- experiment_id
- source_experiment_version_id
- source_version_number
- source_proposal_revision_id
- source_batch_id
- source_batch_summary_revision_id
- source_summary_revision_number

approved design
- source ProposalPayload / equivalent bounded approved design facts

trial conditions
- trial_id
- trial_ordinal
- parameter values
- seed

deterministic result facts
- summary presentation status
- included selected result refs
- selected metric observations
- selected validation_report_id
- excluded trial refs + deterministic exclusion/reason codes
- deterministic warnings
- aggregate metrics
```

### 8.2 What is deliberately not included

P1-A ResultContext excludes:

- raw stdout/stderr;
- arbitrary Artifact bytes;
- model checkpoints;
- raw unvalidated metric files;
- invalid Trial metric values as if they were trusted scientific evidence;
- hidden model reasoning;
- free-form history across unrelated Experiments.

Excluded Trial conditions and deterministic exclusion reason codes may be shown because they explain which approved configurations failed eligibility; their rejected/raw scientific outputs do not become authoritative result evidence.

### 8.3 Bounds and canonical request identity

The ResultContext must have an explicit byte/count bound. V1 Batch size is already bounded to at most 20 Trials, so the implementation plan should define a deterministic serialization and maximum ResultContext byte size.

The exact ResultContext participates in the Agent model request canonical hash. A replay must therefore be tied to the same result facts, not merely the same natural-language goal.

## 8.4 Module dependency boundary

The current architecture guard intentionally does **not** allow `agent_runtime` to import `results_validation`. This tranche must preserve that direction.

Define the minimal ResultContext DTO/port on the Agent side, for example:

```text
agent_runtime
  ResultContext
  ResultContextSource protocol
        ↑
        | implemented in composition/adapter code
        |
api/composition adapter
  may depend on both agent_runtime and results_validation
        ↓
results_validation / execution / experiments query services
```

Do not add `results_validation` to `agent_runtime`'s allowed-import set merely to implement this feature.

The nullable AgentRun foreign-key column may reference `batch_summary_revisions` by table name without importing Result Plane ORM types. Result lineage loading and ResultContext assembly belong behind the injected port/adapter.

## 9. ContextCompiler behavior

Initial planning run:

```text
goal
+ RepositorySnapshot descriptor
+ repository Tool definitions
+ prior Tool results
+ human inputs
```

Follow-up run:

```text
goal
+ RepositorySnapshot descriptor
+ exact ResultContext
+ repository Tool definitions
+ prior Tool results
+ human inputs
```

The system instruction for a Follow-up AgentRun must state:

- ResultContext is authoritative only for the deterministic facts it contains;
- selected/eligible evidence may support scientific claims;
- exclusions/reason codes may guide diagnosis but invalid raw results are not promoted to accepted evidence;
- if the declared research goal is already satisfied, `stop` is valid;
- if another experiment is warranted, `propose` must provide a full existing ProposalPayload;
- the Agent never starts or approves the next experiment.

## 9.1 ResultContext data-egress boundary

A Follow-up AgentRun sends ResultContext inside the compiled model request. Structured experiment results are therefore provider-visible data, not merely an internal DB read.

P1-A/B keeps the existing binary Agent egress authority:

```text
data_egress_policy = local_only
→ remote provider blocked before provider call

data_egress_policy = remote_provider_allowed
→ remote provider may receive the compiled Agent context,
  including the bounded ResultContext
```

This semantic expansion must be explicit in user/operator documentation and tests. Do not silently treat ResultContext as exempt from data-egress policy.

To keep the first tranche narrow:

- do not introduce a second granular egress-policy engine;
- never include raw Artifact bytes, stdout/stderr, secrets, credentials, or arbitrary workspace files in ResultContext;
- keep selected metrics, approved design facts, Trial conditions, exclusion reason codes, warnings, and aggregates bounded and deterministic;
- preserve the existing guarantee that `local_only + remote provider` fails before a provider request is sent.

If later use cases require allowing repository egress while forbidding result egress (or vice versa), that is a separate policy tranche rather than an implicit behavior in P1-A/B.

## 10. Agent decision semantics stay unchanged

No fifth top-level decision type is introduced.

### Goal already satisfied

```json
{
  "kind": "stop",
  "reason_code": "goal_satisfied",
  "summary": "..."
}
```

The bounded `summary` field is the user-visible interpretation; no hidden reasoning is requested or stored.

### More evidence/experiment needed

```text
kind = propose
summary = bounded result interpretation
proposal = full ProposalPayload
```

The existing Proposal schema remains the contract for the next experiment.

### Insufficient information

The Agent may use existing repository tools or `request_human_input`. It must not invent missing experimental facts.

## 11. Follow-up Proposal materialization

The current initial Agent proposal path creates a new Experiment. P1-B must not silently change that behavior.

Instead, implementation should preserve two explicit paths:

```text
initial AgentRun + propose
→ create new Experiment
→ ProposalRevision #1

Follow-up AgentRun + propose
→ use bound existing Experiment
→ ProposalRevision #(N+1)
```

Both paths must preserve exact `origin_agent_step_id` replay behavior.

Follow-up materialization fails closed if:

- the bound Experiment no longer exists;
- the result binding and Experiment lineage disagree;
- the Proposal payload binds a RepositorySnapshot inconsistent with the Follow-up AgentRun;
- the same origin AgentStep already materialized a non-identical Proposal.

## 12. Approval and execution boundary

The follow-up path reuses the existing governance chain:

```text
Follow-up ProposalRevision
→ deterministic ProposalVerification
→ Human Approval
→ immutable ExperimentVersion
```

The Agent may not:

- auto-approve;
- invoke Batch creation as a consequence of `propose`;
- call the Coordinator directly;
- use a model judgment as a ValidationReport;
- choose a replacement TrialResultSelection.

A later UI action or explicit API command may create/start a Batch from the approved Version exactly as it does today.

## 13. Failure and stale-state behavior

Follow-up creation must fail before a provider call when:

- the summary revision does not exist;
- the summary is not complete;
- the source lineage crosses Project boundaries;
- the requested snapshot is dirty;
- the first-tranche same-snapshot constraint is violated.

After a Follow-up AgentRun is created, later publication of a new summary revision does not mutate or invalidate its historical trace. The UI/API may label it as based on an older revision, but model request provenance remains exact.

Provider failure, invalid model output, budget exhaustion, and human-input handling retain the current AgentRun semantics.

## 14. API shape

Exact URLs are implementation-plan details. The required concept commands are:

```text
CreateFollowUpAgentRun(source_batch_summary_revision_id, repository_snapshot_id, goal, budget)
AdvanceAgentRun(existing behavior)
GetAgentRun / GetAgentRunTrace(existing behavior)
```

The response for a Follow-up AgentRun must expose enough provenance to identify:

- bound Experiment;
- source BatchSummaryRevision;
- produced ProposalRevision if any.

No generic "patch AgentRun context" command is introduced.

## 15. UI boundary

Do not redesign the competition console during P1-A/B.

After the backend loop is accepted, the existing Result/Summary area may add a thin section showing:

- exact source summary revision;
- Agent interpretation summary;
- Stop vs proposed-next-experiment outcome;
- Proposal/verification state;
- existing Approve/Reject action.

The UI remains a projection of authoritative backend state.

## 16. Acceptance proof for P1-A/B

The first accepted implementation must prove one deterministic two-iteration path without relying on a live external model for the acceptance verdict.

### Positive path

```text
existing Experiment E
→ approved ExperimentVersion V1
→ completed Batch B1
→ exact BatchSummaryRevision S1
→ create Follow-up AgentRun A2 bound to S1
→ compiled request contains deterministic ResultContext
→ deterministic/fake provider returns propose
→ ProposalRevision R2 belongs to Experiment E
→ revision number advances
→ deterministic verification passes
→ human approval
→ ExperimentVersion V2 belongs to Experiment E
→ version number advances
→ no Batch automatically created
```

### Stop path

A bound ResultContext representing a satisfied goal leads to:

```text
Follow-up AgentRun
→ stop(goal_satisfied)
→ no ProposalRevision
→ no ExperimentVersion
→ no Batch
```

### Negative cases

At minimum:

- nonexistent summary revision;
- summary/Project lineage mismatch;
- non-latest summary at run creation;
- dirty or mismatched RepositorySnapshot;
- model attempts an invalid decision payload;
- follow-up Proposal tries to bind another snapshot during the parameter-only tranche;
- duplicate/replayed origin AgentStep cannot create divergent revisions.

### Evidence boundary tests

Acceptance must prove that the compiled ResultContext:

- contains the exact summary revision identity;
- contains selected eligible metric evidence;
- contains Trial conditions and exclusion reason codes;
- does not include raw Artifact content;
- does not expose invalid Trial raw metric values as accepted scientific evidence.

## 17. Review and implementation sequencing

This design is not implementation authority until focused review closes blocking findings.

After design approval:

1. write a detailed implementation plan from the actual current `main` file structure;
2. focused plan review;
3. implement P1-A ResultContext + Follow-up AgentRun first;
4. run focused unit/integration tests;
5. implement P1-B same-Experiment proposal materialization;
6. run the deterministic two-iteration acceptance;
7. final full CI;
8. only then consider P1-C historical comparison.

Do not mix controlled code modification, cross-Experiment history, UI redesign, or Runtime hardening into the P1-A/B implementation branch.

## 18. Focused review gate

Focused review should verify the proposed boundaries against the current implementation and report only concrete contradictions, missing invariants, or unsafe ownership transitions.

Decisions already frozen in this proposed design:

- persist only `source_batch_summary_revision_id` on AgentRun; derive Experiment through the restricted summary→batch→version→experiment lineage;
- require that source summary to be the latest published revision at Follow-up AgentRun creation, then pin the exact revision for the lifetime of the run;
- include the exact canonical source ProposalPayload in ResultContext;
- keep the first P1-A/B acceptance path on the same RepositorySnapshot before any controlled-code-modification tranche;
- preserve the architecture dependency guard: `agent_runtime` must not gain a direct `results_validation` dependency;
- reuse the existing same-Experiment ProposalRevision/ExperimentVersion numbering semantics;
- keep invalid/raw scientific outputs outside authoritative ResultContext evidence;
- treat ResultContext as provider-visible under the existing `data_egress_policy`; `remote_provider_allowed` explicitly covers the bounded ResultContext while `local_only` must block the remote call before transmission.

The review should especially test whether the current services and transaction boundaries can implement these decisions without weakening existing replay, approval, or Result Plane invariants.

Until focused review closes any blocking findings, implementation is not authorized.
