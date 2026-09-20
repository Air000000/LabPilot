# LabPilot P1 Result-Aware Experiment Iteration Detailed Implementation Plan

**Date:** 2026-09-20  
**Status:** Proposed / Focused Plan Review Pending  
**Planning baseline:** `eb09844618ae57a3be6400a1b2d0b5de7a88baa5`  
**Approved design:** `docs/superpowers/specs/2026-09-20-labpilot-p1-result-aware-iteration-design.md`  
**Design focused re-review:** `docs/superpowers/specs/2026-09-20-labpilot-p1-result-aware-iteration-focused-rereview.md`

## 0. Goal and execution rule

Implement the first bounded result-aware experiment iteration without reopening the execution/result authority chain:

```text
existing Experiment E
→ approved ExperimentVersion V1
→ completed Batch B1
→ exact BatchSummaryRevision S1
→ Follow-up AgentRun A2 bound to S1
→ deterministic ResultContext in model request
→ stop
   OR
→ propose full ProposalPayload
→ ProposalRevision R2 in Experiment E
→ existing deterministic verification
→ existing Human Approval
→ ExperimentVersion V2 in Experiment E
```

The implementation stops there. It does **not** automatically create or start a Batch for V2.

Implementation must follow red → green → focused regression for each task. Do not batch unrelated refactors into this tranche.

## 1. Authority and non-goals

Normative precedence:

```text
V1 Scope v0.3
> Architecture 10 v0.3
> Master Roadmap v0.3
> approved P1 Result-Aware Experiment Iteration Design
> this detailed plan after focused plan approval
```

Explicitly out of scope:

- controlled or autonomous code modification;
- editable Worktree;
- historical/cross-Experiment search;
- new Result Tool;
- new Agent decision kind;
- LLM semantic verifier;
- Multi-Agent;
- SSH work;
- Slice 2 production-hardening campaign;
- automatic Batch create/start;
- competition UI redesign;
- granular replacement for the existing binary data-egress policy.

## 2. Current code facts this plan preserves

The current implementation already provides the following useful contracts:

1. `AgentRunRow` and `AgentStepRow` own bounded Agent runtime state.
2. `AgentApplicationService.advance_run()` persists a model step before the provider call and can replay a persisted `propose` decision.
3. `AgentDecision` is exactly:
   `tool_call | request_human_input | propose | stop`.
4. `ContextCompiler` canonicalizes the complete rendered model request and hashes it.
5. `ProposalApplicationService.create_agent_revision()` creates a **new Experiment** for an initial Agent proposal.
6. `ProposalApplicationService.create_manual_revision()` already proves the same Experiment can safely allocate the next ProposalRevision number under an Experiment row lock.
7. approval already allocates consecutive ExperimentVersion numbers under the same Experiment row lock.
8. `BatchSummaryService.refresh_summary()` serializes summary revision creation with `BatchRow ... FOR UPDATE`.
9. `BatchSummaryRevisionRow` has no publish/status field; the highest persisted revision is the current summary.
10. the architecture dependency guard intentionally does not allow `agent_runtime` to import `results_validation`.
11. `main.py` is classified as API/composition and may wire both Agent and Result owners.

The implementation should extend these contracts rather than invent parallel state machines.

## 3. Target implementation shape

### 3.1 Agent-side contracts

Add one Agent-owned ResultContext contract without importing Result Plane models:

```text
agent_runtime/result_context.py
├─ ResultTrialCondition
├─ ResultContext
└─ bounded canonical rendering helper
```

Add two narrow ports to Agent application orchestration:

```text
FollowUpRunStore
    create_bound_run(run) -> AgentRunDetails

ResultContextSource
    load(source_batch_summary_revision_id) -> ResultContext
```

The Agent Runtime knows only those contracts.

### 3.2 Composition adapter

Add one adapter in the API/composition owner:

```text
api/result_aware_agent_adapter.py

SqlResultAwareAgentAdapter
├─ create_bound_run(...)
└─ load(...)
```

This adapter may import:

- Agent-owned DTOs/repository helper;
- `RepositorySnapshotRow` / `RepositoryRegistrationRow`;
- `ExperimentRow`, `ProposalRevisionRow`, `ExperimentVersionRow`;
- `BatchRow`, `TrialRow`;
- `BatchSummaryRevisionRow`.

Do **not** add `results_validation` to `agent_runtime` allowed imports.

### 3.3 Durable provenance

Add exactly one nullable AgentRun provenance field:

```text
agent_runs.source_batch_summary_revision_id UUID NULL
    FK → batch_summary_revisions.batch_summary_revision_id
    ON DELETE RESTRICT
```

Do not add `bound_experiment_id`.

Do not add an index unless a concrete query introduced in this tranche needs reverse lookup by summary revision. The current API reads AgentRun by primary key, so no reverse index is required.

## 4. ResultContext contract

Create `src/labpilot/agent_runtime/result_context.py`.

Use Agent-owned Pydantic/frozen DTOs so ContextCompiler receives a typed, JSON-canonicalizable contract without importing Result Plane classes.

Required logical fields:

```text
ResultContext
- experiment_id
- source_experiment_version_id
- source_version_number
- source_proposal_revision_id
- source_batch_id
- source_batch_summary_revision_id
- source_summary_revision_number
- presentation_status
- source_proposal: ProposalPayload
- trials: tuple[ResultTrialCondition, ...]
- included_results: tuple[dict[str, JsonValue], ...]
- excluded_results: tuple[dict[str, JsonValue], ...]
- warnings: tuple[dict[str, JsonValue], ...]
- aggregates: dict[str, JsonValue]
```

`ResultTrialCondition`:

```text
- trial_id
- trial_ordinal
- resolved_parameters
- seed
```

Rules:

- trial conditions sort by `trial_ordinal`;
- included/excluded/warning arrays preserve the deterministic order already produced by Summary;
- the adapter must validate that included/excluded Trial IDs belong to the bound Batch and do not introduce unknown Trials;
- included + excluded Trial IDs must cover the Batch exactly once for a completed summary;
- source Proposal payload is parsed from the exact `ProposalRevisionRow` bound by the source ExperimentVersion;
- no Artifact file read occurs;
- no stdout/stderr read occurs;
- no invalid raw metric lookup occurs.

Canonical rendering:

- render with `model_dump(mode="json")`;
- normalize through the existing canonical JSON machinery before adding to the request;
- set an individual ResultContext maximum of **128 KiB** canonical JSON;
- preserve the existing global `_MAX_MESSAGE_BYTES = 256 KiB` check in ContextCompiler.

If ResultContext exceeds the per-context bound, fail before provider invocation with a stable Agent error such as `result_context_too_large`.

The 128 KiB bound is a V1 implementation bound, not a persistence schema limit. It leaves explicit headroom under the existing 256 KiB total compiled-message cap.

## 5. Follow-up creation transaction contract

`SqlResultAwareAgentAdapter.create_bound_run()` owns the cross-domain transaction required by the approved design.

Input:

- fully constructed `AgentRunDetails` with non-null `source_batch_summary_revision_id`;
- the adapter holds the shared `SessionContextFactory`;
- the adapter holds the same `SqlAgentRepository` instance used by AgentApplicationService.

Required transaction:

```text
BEGIN UnitOfWork

1. SELECT supplied summary's batch_id
   - if missing → source_summary_not_found

2. SELECT BatchRow WHERE batch_id = ... FOR UPDATE
   - if missing → source_batch_not_found

3. Re-read the exact supplied BatchSummaryRevisionRow under the locked transaction

4. SELECT max(revision_number)
   WHERE batch_id = locked Batch

5. require supplied revision_number == max revision_number
   - else → source_summary_not_latest

6. load source ExperimentVersionRow from BatchRow.experiment_version_id

7. load source ProposalRevisionRow from ExperimentVersionRow.proposal_revision_id

8. load owning ExperimentRow

9. load requested RepositorySnapshotRow and its RepositoryRegistrationRow

10. require all Project bindings agree:
    Batch.project_id
    Experiment.project_id
    RepositoryRegistration.project_id
    AgentRun.project_id

11. require RepositorySnapshot.is_clean == True

12. first-tranche same-source rule:
    AgentRun.repository_snapshot_id
      == ExperimentVersion.repository_snapshot_id

    RepositorySnapshot.repository_snapshot_identity
      == ExperimentVersion.repository_snapshot_identity

13. require ProposalRevision.experiment_id == Experiment.id
    and ExperimentVersion.experiment_id == Experiment.id

14. insert AgentRun through an Agent-owned repository helper
    inside this same UoW

15. COMMIT same UoW
```

No provider call, Tool resolution, or ResultContext rendering occurs in this transaction.

### 5.1 Agent repository helper

Extend `SqlAgentRepository` with a small transaction-aware helper, for example:

```python
async def add_run_in_uow(
    self,
    uow: UnitOfWork,
    run: AgentRunDetails,
) -> AgentRunDetails:
    ...
```

`add_run()` should delegate to this helper inside its existing self-owned UoW.

Do not move cross-domain Result/Batch queries into `agent_runtime.repository`.

### 5.2 Concurrency proof

Add an integration test that proves the stale-summary race is closed:

```text
summary N exists

transaction A:
    lock BatchRow
    persist N+1
    hold lock before commit

concurrent Follow-up create with supplied N:
    must block on the same BatchRow lock

transaction A commits
Follow-up resumes
    observes max revision N+1
    rejects supplied N
    creates no AgentRun
```

Also prove the opposite ordering semantically:

```text
Follow-up binds N and commits successfully
later Summary refresh persists N+1
existing AgentRun still reads source_batch_summary_revision_id = N
```

Do not add test-only production hooks merely to coordinate this race; use PostgreSQL row locking/barriers in the integration test.

## 6. Exact ResultContext loading

`SqlResultAwareAgentAdapter.load(summary_revision_id)` is a read path.

It must use the exact pinned summary revision, not "latest".

Load:

```text
BatchSummaryRevisionRow
→ BatchRow
→ ExperimentVersionRow
→ ProposalRevisionRow
→ ExperimentRow
→ ordered TrialRows for the Batch
```

Fail closed on broken lineage.

Build trial conditions from persisted `TrialRow.resolved_parameters_json` and `seed`.

Use only the pinned summary row for:

- included result refs;
- excluded reason refs;
- warnings;
- aggregates.

Do not re-query all MetricObservation rows and reconstruct "better" results. The deterministic summary is the source of interpretation evidence.

Derive:

```text
presentation_status =
    "complete_with_invalid_results" if excluded_results else "complete"
```

ResultContext loading does **not** require the pinned summary to still be current latest. Historical AgentRun replay must remain possible after N+1 exists.

## 7. Task sequence

---

# Task 1 — Add exact AgentRun result provenance

### Files

Create:

- `migrations/versions/0013_result_aware_agent_runs.py`

Modify:

- `src/labpilot/agent_runtime/models.py`
- `src/labpilot/agent_runtime/types.py`
- `src/labpilot/agent_runtime/repository.py`
- `src/labpilot/api/schemas/agent_runs.py`
- `tests/integration/agent_runtime/test_agent_service.py`
- `tests/integration/db/test_migrations.py`
- exact-head assertions in:
  - `tests/integration/test_execution_schema.py`
  - `tests/integration/test_attempt_physical_schema.py`
  - `tests/integration/tools_policy/test_tool_persistence.py`

### Red tests first

Add assertions that:

1. migration head becomes `0013_result_aware_agent_runs`;
2. `agent_runs.source_batch_summary_revision_id` exists and is nullable;
3. it has the exact FK to `batch_summary_revisions.batch_summary_revision_id` with RESTRICT semantics;
4. existing initial AgentRun persists `NULL`;
5. AgentRun response/trace returns `source_batch_summary_revision_id: null`;
6. repository row/detail round-trip preserves a non-null source ID;
7. there is no new duplicate Experiment column.

### Migration

`upgrade()`:

- add nullable UUID column;
- add named FK:
  `fk_agent_runs_source_summary_revision_id_batch_summary_revisions`.

`downgrade()`:

- drop FK;
- drop column.

This migration is structurally reversible. It does not alter the existing 0012 ExperimentVersion downgrade caveat.

### Focused command

```bash
uv run pytest   tests/integration/db/test_migrations.py   tests/integration/agent_runtime/test_agent_service.py   tests/integration/test_execution_schema.py   tests/integration/test_attempt_physical_schema.py   tests/integration/tools_policy/test_tool_persistence.py -q
```

Acceptance: green before Task 2.

---

# Task 2 — Add Agent-owned ResultContext contract and compiler support

### Files

Create:

- `src/labpilot/agent_runtime/result_context.py`
- `tests/unit/agent_runtime/test_result_context.py`
- `tests/unit/agent_runtime/test_context_compiler.py` if no focused compiler file exists at implementation time; otherwise extend the existing focused compiler test file.

Modify:

- `src/labpilot/agent_runtime/context_compiler.py`

### Red tests first

Prove:

1. initial run compilation with `result_context=None` is byte-for-byte/canonical-hash compatible with the old path;
2. a Follow-up ResultContext appears as a distinct bounded `result_context` object in the user message;
3. exact summary revision identity participates in `canonical_request_hash`;
4. changing one selected metric/result value changes request hash;
5. ResultContext >128 KiB fails before ModelRequest creation;
6. total message >256 KiB still fails through the existing global guard;
7. system policy for follow-up context explicitly states:
   - selected/eligible evidence is authoritative input;
   - excluded reason codes may guide diagnosis;
   - invalid raw results are not accepted scientific evidence;
   - `stop` is valid if goal is satisfied;
   - `propose` must emit full ProposalPayload;
   - Agent never starts/approves execution.

### Implementation

Extend:

```python
ContextCompiler.compile(
    ...,
    result_context: ResultContext | None = None,
)
```

Initial path must render the same logical request as before; do not add a noisy `"result_context": null` field if that changes existing request hashes unnecessarily.

Follow-up path adds the ResultContext and follow-up-specific policy.

Do not change `AgentDecision`.

### Focused command

```bash
uv run pytest tests/unit/agent_runtime -q
```

Acceptance: green before Task 3.

---

# Task 3 — Implement composition adapter and latest-at-create linearization

### Files

Create:

- `src/labpilot/api/result_aware_agent_adapter.py`
- `tests/integration/agent_runtime/test_follow_up_run_binding.py`

Modify:

- `src/labpilot/agent_runtime/repository.py`
- `tests/architecture/test_module_dependencies.py` only if a focused positive assertion is useful; do not weaken rules.

### Agent-side ports

Define narrow Protocols in the Agent application layer:

```python
class FollowUpRunStore(Protocol):
    async def create_bound_run(
        self,
        run: AgentRunDetails,
    ) -> AgentRunDetails: ...

class ResultContextSource(Protocol):
    async def load(
        self,
        source_batch_summary_revision_id: UUID,
    ) -> ResultContext: ...
```

The concrete API adapter implements both.

### Red integration tests

Required:

- nonexistent summary → fail, zero AgentRun rows;
- supplied N when N+1 already exists → fail, zero AgentRun rows;
- concurrent N→N+1 lock race → stale supplied N rejected after wait;
- wrong Project lineage → fail;
- dirty RepositorySnapshot → fail;
- different RepositorySnapshot than source Version → fail;
- snapshot identity mismatch → fail;
- valid latest summary + clean exact snapshot → one AgentRun row with exact source summary ID;
- after successful creation, later N+1 creation does not rewrite the existing AgentRun binding.

### ResultContext builder tests

Using a deterministic summary fixture, prove:

- source lineage IDs are exact;
- source ProposalPayload equals the source Version's exact proposal payload;
- Trial conditions are ordered and exact;
- selected included metrics/validation_report_id are present;
- exclusion codes are present;
- invalid raw metric rows not represented by the pinned summary are not exposed;
- no Artifact file bytes/path read is required.

Prefer reuse of existing Result Plane fixture helpers from:

- `tests/integration/test_result_processing_service.py`
- `tests/integration/test_batch_summary_service.py`

Do not rerun physical training for these tests.

### Architecture guard

Run:

```bash
uv run pytest tests/architecture/test_module_dependencies.py -q
```

and verify no `agent_runtime -> results_validation` allowance was added.

Acceptance: P1-A storage/context source primitives are green before Task 4.

---

# Task 4 — Add Follow-up AgentRun service/API and prove read-only P1-A loop

### Files

Modify:

- `src/labpilot/agent_runtime/service.py`
- `src/labpilot/api/schemas/agent_runs.py`
- `src/labpilot/api/routers/agent_runs.py`
- `src/labpilot/main.py`
- `tests/unit/agent_runtime/test_agent_service.py`
- `tests/integration/agent_runtime/test_agent_service.py`

Create:

- `tests/integration/api/test_result_aware_agent_api.py`

### Service constructor and initial-path compatibility

Add the two Follow-up ports as **optional keyword-only dependencies with defaults of `None`**:

```python
AgentApplicationService(
    ...,
    follow_up_run_store: FollowUpRunStore | None = None,
    result_context_source: ResultContextSource | None = None,
)
```

This is a compatibility requirement, not a convenience:

- existing initial AgentRun construction must remain valid without either Follow-up port;
- `create_run()`, initial `advance_run()`, Tool use, Human Input, initial `propose`, and persisted initial-proposal replay must not consult these ports;
- `create_follow_up_run()` must fail closed with a stable error such as `follow_up_runtime_unconfigured` unless **both** ports are configured, so the service cannot persist a Follow-up AgentRun that it is structurally unable to advance;
- `advance_run()` for a persisted run with non-null `source_batch_summary_revision_id` must also fail closed with the same stable error if `ResultContextSource` is unavailable;
- `advance_run()` for an initial run with null `source_batch_summary_revision_id` must remain independent of the Follow-up ports.

In production `main.py`, configure both ports with the same concrete `SqlResultAwareAgentAdapter` instance.

Keep `SqlAgentRepository` instantiated once in `main.py` so the adapter and Agent service share its transaction-aware insert helper.

Existing direct construction sites are intentionally **not required to add dummy Follow-up dependencies** because the defaults preserve the pre-P1 constructor contract. The implementation must explicitly regression-check the current construction sites in:

- `tests/unit/agent_runtime/test_agent_service.py`;
- `tests/integration/agent_runtime/test_agent_service.py`;
- `tests/integration/api/test_slice1_api.py`;
- `tests/integration/slice1/test_proposal_loop.py`;
- `tests/integration/slice1/test_opencode_go_smoke.py` (constructor/import compatibility; external execution remains opt-in).

Add a focused unit regression that constructs `AgentApplicationService` **without** either Follow-up port and proves the initial create/advance path still completes normally.

### New command

Add:

```python
AgentApplicationService.create_follow_up_run(
    *,
    context,
    project_id,
    source_batch_summary_revision_id,
    repository_snapshot_id,
    goal,
    budget,
) -> AgentRunDetails
```

Behavior:

- validate goal/budget exactly like initial creation;
- require both Follow-up ports to be configured; otherwise fail closed before persistence with `follow_up_runtime_unconfigured`;
- construct AgentRun with exact source summary ID;
- call `FollowUpRunStore.create_bound_run()`;
- no Tool factory resolution;
- no provider call;
- no Proposal creation.

### Advance behavior

In `advance_run()`:

```text
load persisted AgentRun
→ if source summary ID is non-null:
     require ResultContextSource configured
     else fail follow_up_runtime_unconfigured
→ assert provider egress
→ describe bound RepositorySnapshot
→ if source summary ID:
     load exact ResultContext
→ load prior Tool/Human history
→ ContextCompiler.compile(... result_context=...)
→ existing persist-before-provider loop
```

For an initial run, the null source-summary branch must skip all Follow-up-port checks and preserve the existing control flow/request semantics.

Do not re-run latest-summary admission during advance.

### Data-egress test

With a remote provider and `local_only`:

- Follow-up run may be created;
- `advance_run` must raise `remote_provider_blocked` before provider call;
- provider call count remains zero;
- preferably use a ResultContextSource sentinel to prove no provider-visible context path is traversed after the egress failure.

With `remote_provider_allowed`:

- deterministic provider receives the bounded ResultContext in the compiled request.

No new policy engine.

### API

Add:

```text
POST /projects/{project_id}/agent-runs/follow-up
```

Request:

```json
{
  "source_batch_summary_revision_id": "...",
  "repository_snapshot_id": "...",
  "goal": "...",
  "budget": { ...existing AgentBudgetRequest... }
}
```

Response reuses `AgentRunResponse` and exposes:

- `source_batch_summary_revision_id`.

Initial AgentRun API remains unchanged except response adds nullable provenance.

### P1-A deterministic acceptance

Use a scripted/fake provider returning:

```json
{
  "kind": "stop",
  "reason_code": "goal_satisfied",
  "summary": "..."
}
```

Prove:

```text
exact completed summary
→ Follow-up AgentRun
→ compiled request contains exact ResultContext
→ stop(goal_satisfied)
→ AgentRun completed
→ no ProposalRevision created
→ no ExperimentVersion created
→ no Batch created
```

### Focused command

```bash
uv run pytest   tests/unit/agent_runtime   tests/integration/agent_runtime/test_follow_up_run_binding.py   tests/integration/agent_runtime/test_agent_service.py   tests/integration/api/test_result_aware_agent_api.py   tests/architecture/test_module_dependencies.py -q
```

**P1-A gate:** must be green before Task 5.

---

# Task 5 — Add explicit same-Experiment Agent Proposal service

### Files

Modify:

- `src/labpilot/experiments/proposal_service.py`
- `tests/integration/experiments/test_proposal_service.py`

`proposal_repository.py` already has:

- `get_experiment_for_update()`;
- `max_revision_number()`;
- `get_revision_by_origin_step()`.

Do not add a second revision-number allocator.

### New explicit method

Add a separate method; do not silently change initial `create_agent_revision()` semantics:

```python
create_follow_up_agent_revision(
    *,
    context,
    project_id,
    experiment_id,
    origin_agent_step_id,
    payload,
)
```

Algorithm:

1. compute payload hash;
2. pre-check existing `origin_agent_step_id`;
3. exact replay requires:
   - same payload hash;
   - same Project;
   - same Experiment;
4. run existing snapshot binding check;
5. open UoW;
6. re-check origin step;
7. lock the target Experiment row with `FOR UPDATE`;
8. require Experiment.project_id == supplied project;
9. allocate `max_revision_number(experiment_id) + 1`;
10. insert ProposalRevision with:
    - target `experiment_id`;
    - next revision number;
    - exact `origin_agent_step_id`;
11. commit;
12. retain the existing unique-origin-step conflict recovery path.

Extend `_require_exact_agent_replay()` with an optional/explicit expected Experiment binding rather than duplicating replay logic.

### Red tests first

Prove:

- initial `create_agent_revision()` still creates Experiment + revision #1;
- follow-up method on that Experiment creates revision #2;
- another follow-up creates #3;
- replay of the same origin step + same payload + same Experiment returns the same revision;
- same origin step + different payload fails;
- same origin step + same payload + different Experiment fails;
- concurrent distinct follow-up Agent steps serialize to consecutive revision numbers under Experiment lock;
- wrong Project fails;
- no new Experiment row is created by follow-up method.

### Focused command

```bash
uv run pytest tests/integration/experiments/test_proposal_service.py -q
```

Acceptance: green before Task 6.

---

# Task 6 — Dispatch follow-up propose through same-Experiment path and prove replay

### Files

Modify:

- `src/labpilot/agent_runtime/service.py`
- `tests/unit/agent_runtime/test_agent_service.py`
- `tests/integration/agent_runtime/test_agent_service.py`
- `tests/integration/api/test_result_aware_agent_api.py`

### AgentProposalService protocol

Keep explicit methods:

```text
create_agent_revision(...)             # initial run → new Experiment
create_follow_up_agent_revision(...)   # follow-up run → existing Experiment
```

### Materialization

Change `_materialize_proposal()`:

Initial run:

```text
source_batch_summary_revision_id is None
→ existing create_agent_revision()
```

Follow-up run:

1. require Proposal payload `repository_snapshot_id == run.repository_snapshot_id`;
2. load exact pinned ResultContext;
3. derive `experiment_id` from ResultContext;
4. call `create_follow_up_agent_revision()`;
5. link returned ProposalRevision to AgentRun;
6. mark AgentRun completed with existing `proposal_created` reason.

Do not derive Experiment from caller input or model output.

Repository snapshot identity/payload consistency remains enforced by ProposalApplicationService's current snapshot-binding validation.

### Persisted decision replay

The current crash path:

```text
model decision persisted as propose
→ ProposalRevision commit
→ process fails before AgentRun links produced_proposal_revision_id
→ next advance detects pending propose
→ materializes/replays exact ProposalRevision
```

must continue to work for follow-up proposals.

Add an integration test using the existing crash-after-create style to prove:

- one follow-up ProposalRevision physically exists;
- retry does not create revision #3;
- AgentRun eventually links the same revision #2.

### Focused command

```bash
uv run pytest   tests/unit/agent_runtime/test_agent_service.py   tests/integration/agent_runtime/test_agent_service.py   tests/integration/experiments/test_proposal_service.py   tests/integration/api/test_result_aware_agent_api.py -q
```

Acceptance: green before Task 7.

---

# Task 7 — Deterministic two-iteration P1 acceptance

### Files

Create:

- `tests/integration/p1/test_result_aware_iteration.py`

Reuse existing helpers where sensible; do not couple acceptance to live DeepSeek/OpenCode.

### Positive propose path

Build one real database lineage:

```text
Experiment E
→ ProposalRevision R1
→ verification PASS
→ approval
→ ExperimentVersion V1
→ Batch B1
→ deterministic Result processing
→ BatchSummaryRevision S1
→ Follow-up AgentRun A2
→ scripted provider returns propose
→ ProposalRevision R2
→ deterministic verification PASS
→ human approval
→ ExperimentVersion V2
```

Assertions:

- `R2.experiment_id == R1.experiment_id`;
- `R2.revision_number == R1.revision_number + 1`;
- `V2.experiment_id == V1.experiment_id`;
- `V2.version_number == V1.version_number + 1`;
- `A2.source_batch_summary_revision_id == S1.id`;
- compiled provider request contains S1 exact identity;
- no second Experiment row was created;
- no Batch exists for V2 unless the test explicitly invokes existing BatchApplicationService after the asserted stopping point;
- Agent never calls approval itself.

### Stop path

Exact source summary → scripted `stop(goal_satisfied)`:

- no new ProposalRevision;
- no V2;
- no Batch.

### Evidence-boundary case

Create/seed a Batch summary with at least:

- one included selected valid result;
- one excluded Trial/reason, or a separate focused fixture that proves exclusion behavior.

Assert provider-visible ResultContext contains:

- selected valid metric values;
- selected validation report ID;
- excluded Trial identity and deterministic reason code;
- Trial conditions.

Assert it does not contain:

- raw Artifact file contents;
- stdout/stderr;
- invalid raw metric values not present in the deterministic summary.

### Focused command

```bash
uv run pytest tests/integration/p1/test_result_aware_iteration.py -q
```

This is the tranche-level semantic acceptance.

---

# Task 8 — Architecture, egress, migration, and regression gate

Run focused architecture/data boundary regressions:

```bash
uv run pytest \
  tests/architecture \
  tests/integration/db/test_migrations.py \
  tests/unit/agent_runtime \
  tests/integration/agent_runtime \
  tests/integration/experiments/test_proposal_service.py \
  tests/integration/api/test_slice1_api.py \
  tests/integration/slice1/test_proposal_loop.py \
  tests/integration/api/test_result_aware_agent_api.py \
  tests/integration/test_batch_summary_service.py \
  tests/integration/p1/test_result_aware_iteration.py -q
```

Also run the OpenCode-Go test module in collection/normal mode so its constructor remains compatible; the external smoke itself may remain skipped unless explicitly enabled:

```bash
uv run pytest tests/integration/slice1/test_opencode_go_smoke.py -q
```

Required assertions:

- architecture dependency guard unchanged for `agent_runtime`;
- `AgentApplicationService` can still be constructed with the pre-P1 argument set and no Follow-up ports;
- existing initial Agent proposal loop still passes;
- Slice 1 API path still passes without injecting dummy Follow-up ports;
- OpenCode-Go smoke module still constructs the service with the existing argument set when enabled;
- Result Plane summary tests still pass;
- migration head is 0013;
- local-only remote provider remains blocked;
- initial Agent model request semantics remain unchanged when ResultContext is absent.

Do not require a real external provider for acceptance.

---

# Task 9 — Final repository gate and closeout

Run the repository-equivalent full gate:

```bash
./scripts/check_ci.sh
```

Then verify production image/build-context hygiene through the normal GitHub CI workflow.

Do not merge if either gate fails.

After implementation acceptance, update only status/closeout docs:

- `DOCUMENT-STATUS.md`;
- a short P1 Result-Aware implementation closeout/evidence document.

Do not reopen global architecture.

## 8. Error-code contract

Use stable, narrow errors for follow-up admission. Exact HTTP mapping follows existing API error conventions.

Required conceptual errors:

```text
source_summary_not_found
source_batch_not_found
source_summary_not_latest
source_result_lineage_invalid
source_project_mismatch
source_snapshot_not_clean
source_snapshot_mismatch
result_context_too_large
follow_up_runtime_unconfigured
```

Do not expose raw SQL/constraint errors.

## 9. API and compatibility contract

Existing routes remain compatible:

```text
POST /projects/{project_id}/agent-runs
POST /agent-runs/{id}/advance
POST /agent-runs/{id}/human-input
GET  /agent-runs/{id}
GET  /agent-runs/{id}/trace
```

New route:

```text
POST /projects/{project_id}/agent-runs/follow-up
```

Existing responses gain one nullable field:

```text
source_batch_summary_revision_id
```

This is additive.

No existing decision JSON schema changes.

## 10. Data-egress contract

No new policy enum.

At advance time, preserve current order:

```text
assert_provider_egress(run.egress_policy, provider)
→ only if allowed, compile/load provider-visible context
→ provider.complete(...)
```

ResultContext is provider-visible under `remote_provider_allowed`.

Never put into ResultContext:

- secrets/API keys;
- raw workspace files;
- raw Artifact bytes;
- stdout/stderr;
- checkpoint content.

Tests must prove `local_only + remote provider` sends zero requests.

## 11. No-automatic-execution invariant

A successful P1-B path ends at:

```text
ExperimentVersion V2 created by existing human approval command
```

The P1 Agent path itself must not call:

- `BatchApplicationService.create_batch()`;
- Batch start;
- Coordinator;
- Runner.

The acceptance test should count/query Batch rows and make this boundary explicit.

## 12. Review gates

### P1-A implementation review

After Tasks 1–4, review only:

- schema/provenance;
- Batch lock linearization;
- ResultContext evidence boundary;
- dependency direction;
- egress;
- stop path.

Do not review same-Experiment proposal code yet.

### P1-B implementation review

After Tasks 5–7, review only:

- same-Experiment Proposal revision allocation;
- origin-step replay/idempotency;
- existing verifier/approval reuse;
- V2 numbering;
- no automatic Batch.

### Final candidate

Task 8 focused regressions + Task 9 full CI.

## 13. Explicitly deferred next tranche

After P1-A/B acceptance, the next separately planned capability may be P1-C historical comparison:

```text
same Experiment
→ prior ExperimentVersions / Batches / summaries
→ bounded structured comparison
```

That future tranche may introduce read-only history Tool(s).

It must not be smuggled into this implementation merely because ResultContext exists.

Controlled code modification remains later still.

## 14. Plan-review questions

Focused plan review should check:

1. whether the composition-level `SqlResultAwareAgentAdapter` is the narrowest correct way to share a UoW across the Batch lock and AgentRun insert without reversing module dependencies;
2. whether `SqlAgentRepository.add_run_in_uow()` is a sufficiently narrow owner-preserving transaction helper;
3. whether ResultContext can safely use the pinned BatchSummaryRevision JSON plus Trial conditions without re-querying raw metric/artifact evidence;
4. whether the 128 KiB ResultContext bound is reasonable under the existing 256 KiB compiled-message cap;
5. whether `create_follow_up_agent_revision()` correctly reuses existing Experiment row locking and origin-step replay semantics;
6. whether the proposed integration acceptance is sufficient to prove the two-iteration closure without live LLM nondeterminism;
7. whether optional Follow-up ports preserve every existing initial-Agent construction path while still failing closed for Follow-up creation/advance when the ports are absent.

Until focused plan review returns APPROVE, Product implementation is not authorized.
