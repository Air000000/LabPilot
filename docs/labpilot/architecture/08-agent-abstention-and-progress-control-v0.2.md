# LabPilot Agent Abstention and Progress Control

```text
Document type: Architecture Supplement
Version: v0.2
Status: Approved Planning Input
Date: 2026-07-28
Applies to: WP1, WP2, WP3, WP4, WP5, and later P1 enhancements
Consumes: 03-agent-harness-v0.2-frozen, 04-context-and-repository-v0.2-frozen,
          05-tools-policy-and-workspace-v0.2-frozen,
          07-results-validation-and-agentops-v0.2-frozen
Does not reopen: WP0 or v0.1.0-foundation
Supersedes for planning: 08-agent-abstention-and-progress-control-v0.1
```

## 1. Purpose and authority

LabPilot already uses a native bounded Agent Harness, authoritative PostgreSQL state, typed Tools, deterministic Policy, explicit Approval, immutable decision persistence, structured RuntimeEvent/Outbox publication, and bounded budgets. This supplement does not replace those foundations. It refines the contracts needed to answer five questions deterministically:

1. When should the Agent ask the user instead of continuing?
2. When may the Agent intentionally abstain?
3. When must the Runtime stop the Run regardless of model preference?
4. How is progress computed without relying on free-form model self-evaluation?
5. How are those decisions persisted, evaluated, projected, and resumed safely?

The central decision remains:

> Keep the existing four top-level Agent decisions. Represent intentional abstention as a structured `FINAL_RESPONSE`, while retry, blocking, suspension, exhaustion, failure, and termination remain program-controlled state transitions.

This document is an approved input to WP1/WP2/WP4 planning. It is not added retroactively to the completed WP0 dependency manifest, does not change the `v0.1.0-foundation` tag, and does not edit the existing frozen architecture documents.

## 2. Revision summary from v0.1

Version v0.2 adds or clarifies:

1. the legal combinations of `status`, `outcome`, `pause_reason`, and `termination_reason`;
2. semantic separation between deliberate Runtime stops and unexpected failures without adding a conflicting `terminated` status;
3. the existing cross-Run continuation lineage and its idempotency contract;
4. `RuntimePolicyDecision.decision_phase` and phase-specific effects;
5. complete step, model-call, token, cost, active-time, and calendar-time budget snapshots;
6. versioned hash namespaces and exact Canonical JSON V1 inputs;
7. deterministic reason-selection precedence;
8. side-effect-aware retry safety;
9. persistence and database idempotency invariants.

## 3. Scope and non-goals

### 3.1 In scope

- A structured intentional-abstention result.
- An additive `abstained` Agent Run outcome.
- A legal Agent Run state-combination matrix.
- A structured human-input request and immutable continuation lineage.
- A deterministic, persisted `ProgressAssessment`.
- Versioned `NoProgressPolicy` and `TerminationPolicy` contracts.
- Phased, authoritative `RuntimePolicyDecision` records.
- Explicit retry ownership and side-effect safety.
- Paired should-act / should-ask / should-abstain / should-stop evaluation.
- AgentOps and UI consumption contracts.

### 3.2 Out of scope

- Reopening or modifying WP0.
- Adding an Agent Run status named `terminated`; the existing frozen status machine is retained.
- Adding a fifth top-level `ABSTAIN` Agent decision.
- Adding a generic model-controlled `RETRY` decision.
- Free-form or unbounded Agent loops.
- Multi-Agent conversation as the primary control plane.
- A learned success-probability model in V1 Core.
- Allowing AgentOps projections or dashboards to mutate authoritative runtime state.
- Replacing PostgreSQL domain state with framework thread, graph, checkpoint, or trace state.
- Freezing concrete SQL indexes before WP2 persistence models are finalized.

## 4. Existing invariants retained

The following frozen invariants remain unchanged:

1. Every Agent Step produces exactly one top-level decision:
   - `CALL_TOOL`
   - `PRODUCE_PROPOSAL`
   - `REQUEST_HUMAN_INPUT`
   - `FINAL_RESPONSE`
2. The Agent decision is persisted before the corresponding action is executed.
3. Models propose intent; program code validates Policy, authority, preconditions, Approval, and side-effect boundaries.
4. A side-effecting operation whose external state is unknown is not retried automatically.
5. Retry is governed by deterministic classification, idempotency evidence, and versioned policy.
6. Budgets, repetition limits, deadlines, and terminal transitions are enforced by the Harness.
7. AgentOps and Timeline are query and explanation layers, not authoritative domain state.
8. RuntimeEvent and Outbox reliably publish committed changes but do not make LabPilot event-sourced.
9. Ordinary clarification completes the current Run and creates a new linked Run after the user responds.
10. Approval waiting uses the existing `suspended` state and does not keep a long-lived Python stack, HTTP request, or LLM session.

## 5. Agent Run end-state model

### 5.1 Existing lifecycle statuses

This supplement retains the frozen `AgentRun.status` values:

```text
created
compiling_context
running
suspended
completed
failed
cancelled
exhausted
```

Terminal statuses remain:

```text
completed
failed
cancelled
exhausted
```

No new `terminated` status is introduced. A deliberate Runtime stop maps to one of the existing terminal statuses according to the legal combination matrix below.

### 5.2 Business outcomes

The existing outcomes remain:

```text
proposal_ready
final_response_ready
human_input_required
no_deliverable
```

WP2 adds:

```text
abstained
```

`outcome` describes the business result. It does not describe why the Run ended or whether the infrastructure failed.

### 5.3 End-reason semantic classes

For compatibility, the existing persisted field remains named `termination_reason`. Version v0.2 does not require a separate database column named `failure_reason`. Instead, the versioned reason registry assigns every terminal reason to exactly one semantic class:

```text
completion_reason
runtime_stop_reason
failure_reason
cancellation_reason
```

This classification is program-owned and queryable. It prevents an unexpected internal error from being treated as a correct safety stop while avoiding an incompatible rewrite of the frozen AgentRun schema.

#### Completion reasons

```text
proposal_produced
final_response_produced
human_input_required
```

An intentional abstention uses:

```text
status             = completed
outcome            = abstained
termination_reason = final_response_produced
```

The detailed abstention cause is stored in `AbstentionReport.reason_code`, not duplicated as a Runtime termination reason.

#### Runtime stop reasons

```text
no_progress
policy_blocked
approval_rejected
approval_expired
tool_state_unknown
verifier_blocked
max_steps_exceeded
max_model_calls_exceeded
token_budget_exceeded
cost_budget_exceeded
active_time_exceeded
calendar_deadline_exceeded
```

#### Failure reasons

```text
context_compile_failed
model_provider_failed
invalid_model_output
tool_failed
internal_consistency_error
```

`internal_error` from v0.1 is removed. The existing frozen `internal_consistency_error` value is the authoritative internal-failure code unless a later architecture revision introduces more specific failure codes such as persistence or invariant failure.

#### Cancellation reason

```text
user_cancelled
```

### 5.4 Legal state-combination matrix

The following combinations are normative for WP2. `null` means the field must be absent.

| Scenario | `status` | `outcome` | `pause_reason` | `termination_reason` | Semantic class |
|---|---|---|---|---|---|
| Run is executing | `running` | `null` | `null` | `null` | non-terminal |
| Tool Approval is pending | `suspended` | `null` | `tool_approval_pending` | `null` | non-terminal |
| Proposal produced | `completed` | `proposal_ready` | `null` | `proposal_produced` | completion |
| Normal final answer produced | `completed` | `final_response_ready` | `null` | `final_response_produced` | completion |
| Human input required | `completed` | `human_input_required` | `null` | `human_input_required` | completion |
| Intentional abstention | `completed` | `abstained` | `null` | `final_response_produced` | completion |
| Terminal policy denial with no safe alternative | `completed` | `no_deliverable` | `null` | `policy_blocked` | runtime stop |
| Approval rejected/expired and no safe alternative exists | `completed` | `no_deliverable` | `null` | `approval_rejected` or `approval_expired` | runtime stop |
| External side effect remains unknown and Run cannot continue safely | `completed` | `no_deliverable` | `null` | `tool_state_unknown` | runtime stop |
| Deterministic Verifier leaves no deliverable and no repair path | `completed` | `no_deliverable` | `null` | `verifier_blocked` | runtime stop |
| No-progress threshold reached | `exhausted` | `no_deliverable` | `null` | `no_progress` | runtime stop |
| Step/model-call/token/cost/active-time/calendar budget exhausted | `exhausted` | `no_deliverable` | `null` | matching budget reason | runtime stop |
| Context compile, provider, Tool, schema, or internal consistency failure | `failed` | `no_deliverable` | `null` | matching failure reason | failure |
| User cancelled | `cancelled` | `no_deliverable` | `null` | `user_cancelled` | cancellation |

Additional rules:

1. Terminal statuses are irreversible.
2. `suspended` never carries a terminal outcome or terminal reason.
3. Approval pending is not `policy_blocked` and is not a completed Run.
4. Approval rejection may first become an Observation and allow a safe alternative. It becomes terminal only when no safe alternative is available or remaining budget cannot support one.
5. Repeated invalid model output ends as `failed + invalid_model_output` when the schema-recovery allowance is exhausted. If the versioned no-progress policy reaches its threshold first, it ends as `exhausted + no_progress`. The policy version fixes this precedence.
6. A budget reason takes precedence over a weaker no-progress conclusion when the budget is already exhausted at the same guard point.

### 5.5 Reason-selection precedence

The Runtime applies the following order. Later branches cannot overwrite an earlier authoritative conclusion:

1. **Existing terminal state or explicit cancellation**: idempotently return the committed result.
2. **Calendar deadline and hard budget guard**: end as `exhausted` before issuing another model or Tool request.
3. **Pre-action Policy, authority, Approval, and safety checks**: route to Approval, block, or terminate according to the existing Tool Policy contract.
4. **User-resolvable missing information**: use `REQUEST_HUMAN_INPUT` when the user can reasonably supply the fact and the task can then continue.
5. **Available safe action**: continue when a feasible, authorized, non-repeated action remains.
6. **Authoritatively confirmed infeasibility or unavailability**: permit structured abstention only when the required capability, environment, or evidence cannot be obtained within the current authorized scope and no reasonable recovery path exists.
7. **Post-observation no-progress rule**: end as `exhausted + no_progress` when the versioned threshold is reached.
8. **Unexpected execution failure**: end as `failed` with a failure-class reason; never report it as abstention or a correct Runtime safety stop.

## 6. Three distinct non-success continuation paths

### 6.1 Recoverable information gap

Use:

```text
REQUEST_HUMAN_INPUT
```

Appropriate reasons include:

```text
missing_required_information
ambiguous_goal
conflicting_requirements_resolvable_by_user
confirmation_required
```

Run semantics:

```text
status             = completed
outcome            = human_input_required
termination_reason = human_input_required
```

Use this path only when:

1. the missing information can reasonably be supplied by the user;
2. the answer can enable a new Run to continue safely;
3. asking the question does not conceal an already-unknown side effect;
4. the requested input has a typed contract.

### 6.2 Intentional Agent abstention

Use:

```text
FINAL_RESPONSE
response_kind = abstention
```

Appropriate reasons include:

```text
goal_infeasible
required_capability_unavailable
unsupported_environment
required_evidence_unavailable
constraints_unresolvable
```

Run semantics:

```text
status             = completed
outcome            = abstained
termination_reason = final_response_produced
```

Correctly identifying that execution should not continue is a successful business outcome. It is not a `failed` Run and is not interchangeable with a Policy block, unknown side effect, budget exhaustion, or internal failure.

### 6.3 Runtime-forced stop

The Harness stops the Run without relying on a model-proposed abstention when an authoritative guard applies. The concrete status is determined by the legal state-combination matrix:

- expected terminal denial or unresolved external-state risk: `completed + no_deliverable`;
- budget or no-progress exhaustion: `exhausted + no_deliverable`;
- unexpected infrastructure/protocol/invariant failure: `failed + no_deliverable`;
- explicit user cancellation: `cancelled + no_deliverable`.

## 7. Human-input request and immutable continuation

### 7.1 HumanInputRequest contract

A `REQUEST_HUMAN_INPUT` decision must persist a typed request rather than only prose:

```text
HumanInputRequest
- human_input_request_id
- agent_run_id
- agent_step_id
- schema_version
- reason_code
- question
- requested_fields[]
- validation_contract
- evidence_refs[]
- expires_at nullable
- created_at
```

`requested_fields` and `validation_contract` define what a valid user response must contain. Free-form prose may accompany the contract but cannot replace it.

### 7.2 Existing AgentRun lineage

The frozen Agent Harness already defines the continuation lineage fields. WP2 must use them rather than inventing a second lineage model:

```text
root_agent_run_id
parent_agent_run_id nullable
continuation_reason nullable
user_response_to_step_id nullable
interaction_correlation_id
```

Required semantics:

1. The first Run sets `root_agent_run_id = agent_run_id` and `parent_agent_run_id = null`.
2. A continuation creates a new `agent_run_id`.
3. The new Run sets `parent_agent_run_id` to the completed prior Run.
4. The new Run keeps the original `root_agent_run_id` and `interaction_correlation_id`.
5. `continuation_reason = human_clarification` for a user response to `REQUEST_HUMAN_INPUT`.
6. `user_response_to_step_id` points to the Step that persisted the corresponding `HumanInputRequest`.
7. The prior Run remains immutable and terminal; it is never reopened.
8. The UI may render the chain as one interaction, but PostgreSQL stores separate Runs.

### 7.3 Continuation context

The continuation Run must:

- allocate a new Run Budget;
- compile a new Context Snapshot;
- include the current user answer as `current_user_request`;
- reference the prior Run's typed unresolved questions, structured deliverables, and evidence;
- avoid concatenating the prior full prompt, hidden reasoning, provider-native response, or unbounded transcript.

### 7.4 Continuation idempotency

A duplicate submission of the same logical user response must not create unbounded duplicate Runs.

Conceptual identity:

```text
continuation_request_identity = SHA-256(
  "labpilot-agent-continuation-request:v1\n"
  + canonical_json({
      "parent_agent_run_id": ...,
      "user_response_to_step_id": ...,
      "human_input_request_id": ...,
      "canonical_user_response": ...,
      "continuation_reason": "human_clarification"
  })
)
```

The application service must use this identity as an idempotency boundary. The exact unique index is frozen in the WP2 persistence plan after table ownership and tenant keys are finalized.

## 8. Agent decision and abstention contracts

### 8.1 Top-level decision schema

The top-level decision set remains:

```text
CALL_TOOL
PRODUCE_PROPOSAL
REQUEST_HUMAN_INPUT
FINAL_RESPONSE
```

No `AgentDecision.RETRY` or `AgentDecision.ABSTAIN` is added in V1 Core.

### 8.2 Final response kinds

`FINAL_RESPONSE` gains:

```text
FinalResponseKind
- answer
- abstention
```

Conceptual payload:

```text
FinalResponsePayload
- response_kind
- message
- evidence_refs[]
- abstention nullable
```

When `response_kind = abstention`, the `abstention` object is required. When `response_kind = answer`, the object must be null.

### 8.3 AbstentionReport

```text
AbstentionReport
- reason_code
- evidence_refs[]
- unmet_preconditions[]
- actions_not_taken[]
- recovery_conditions[]
- recommended_next_step nullable
```

Rules:

1. `reason_code` comes from a versioned allowlist.
2. `evidence_refs` resolve to persisted, Run-visible Evidence or authoritative Capability/Policy facts.
3. The model cannot establish Policy denial, Approval state, Tool availability, trusted path status, or authority by self-report.
4. `actions_not_taken` identifies material actions intentionally avoided, especially side-effecting actions.
5. `recovery_conditions` are concrete and testable when recovery is possible.
6. Unsupported, contradictory, or ungrounded abstention output is invalid model output, not a successful abstention.
7. An abstention cannot erase or reinterpret ToolCalls that already occurred.

## 9. Reason-code ownership and classification

### 9.1 Human-input reasons

Proposed by the model, validated by the Harness:

```text
missing_required_information
ambiguous_goal
conflicting_requirements_resolvable_by_user
confirmation_required
```

### 9.2 Intentional-abstention reasons

Proposed by the model and validated against authoritative context:

```text
goal_infeasible
required_capability_unavailable
unsupported_environment
required_evidence_unavailable
constraints_unresolvable
```

The following distinction is normative:

```text
The user can reasonably provide the missing fact and the task can continue
→ REQUEST_HUMAN_INPUT

The required fact/evidence/capability is unavailable within the current
trusted environment, Tool registry, authority, and recovery scope
→ structured abstention
```

`required_capability_unavailable` must be validated against the Capability Registry and Tool exposure state. The model's failure to notice an available Tool is not evidence that the capability is unavailable.

### 9.3 Runtime-owned reasons

Computed by program code and never accepted solely from model output:

```text
no_progress
policy_blocked
approval_rejected
approval_expired
tool_state_unknown
verifier_blocked
max_steps_exceeded
max_model_calls_exceeded
token_budget_exceeded
cost_budget_exceeded
active_time_exceeded
calendar_deadline_exceeded
context_compile_failed
model_provider_failed
invalid_model_output
tool_failed
internal_consistency_error
user_cancelled
```

The versioned reason registry records each code's semantic class, legal statuses/outcomes, retryability, and required evidence.

## 10. Execution preconditions supplied by WP1

WP1 does not implement abstention or termination policy. It supplies typed facts that WP2 consumes.

The Context Compiler output must distinguish:

```text
confirmed_fact
unverified_claim
missing_fact
unverifiable_fact
```

It must expose, directly or through typed references:

```text
required_information
required_capabilities
required_authority
environment_assumptions
unresolved_questions
```

Each item carries:

```text
identity
fact_state
provenance
source_authority
freshness_or_snapshot_reference
visibility_scope
```

Model-generated prose is not sufficient evidence that a capability, authority, trusted environment property, or policy state exists.

## 11. Canonical progress identities

### 11.1 Hash construction

All identities use the existing Canonical JSON V1 rules and independent namespaces:

```text
action_identity = SHA-256(
  "labpilot-agent-action:v1\n"
  + canonical_json(action_payload)
)

result_identity = SHA-256(
  "labpilot-agent-result:v1\n"
  + canonical_json(result_payload)
)

input_state_hash = SHA-256(
  "labpilot-agent-input-state:v1\n"
  + canonical_json(input_state_payload)
)

observation_identity = SHA-256(
  "labpilot-agent-observation:v1\n"
  + canonical_json(observation_payload)
)
```

Direct string concatenation of domain fields is prohibited.

### 11.2 Action payload

```text
action_payload
- decision_schema_version
- action_type
- tool_definition_id nullable
- tool_definition_version nullable
- canonical_arguments nullable
- proposal_payload_identity nullable
- final_response_kind nullable
- relevant_precondition_identities[]
```

### 11.3 Result payload

```text
result_payload
- execution_status
- normalized_outcome_code
- normalized_error_code nullable
- bounded_structured_result_identity nullable
- external_state_classification
- authoritative_effect_refs[]
```

Unbounded raw stdout, provider prose, stack traces, timestamps without semantic meaning, secrets, credentials, and unstable absolute paths do not enter the identity payload.

### 11.4 Input-state payload

```text
input_state_payload
- context_snapshot_id
- previous_progress_assessment_id nullable
- unresolved_blocker_identities[]
- available_capability_identities[]
- authority_fact_identities[]
- budget_snapshot_identity
- relevant_evidence_identities[]
```

### 11.5 Observation payload

```text
observation_payload
- observation_schema_version
- observation_type
- action_identity nullable
- result_identity nullable
- evidence_identities[]
- blocker_identities[]
```

Namespace or payload changes require a new version. Historical identities are never silently recalculated under a newer version.

## 12. ProgressAssessment

### 12.1 Purpose and timing

`ProgressAssessment` is an authoritative Agent Runtime record. It is produced after a Step Observation is persisted and before the next model call or terminal transition. It answers whether the committed Run state changed enough to justify continued execution.

It is not a free-form model critique and is not owned by AgentOps.

### 12.2 Required fields

```text
ProgressAssessment
- progress_assessment_id
- agent_run_id
- agent_step_id
- policy_version
- previous_assessment_id nullable
- input_state_hash
- action_identity nullable
- result_identity nullable
- observation_identity
- new_evidence_count
- resolved_blocking_findings
- new_blocking_findings
- unresolved_blocking_findings
- repeated_action_identity nullable
- repeated_result_identity nullable
- repeated_error_code nullable
- remaining_step_budget
- remaining_model_call_budget
- remaining_token_budget
- remaining_cost_budget_microunits
- remaining_active_time_budget_ms
- remaining_calendar_time_budget_ms
- classification
- reason_codes[]
- created_at
```

Budget units are integers:

- tokens: integer token count;
- cost: integer microunits in the Run Budget's configured currency/accounting unit;
- active time: integer milliseconds of execution time;
- calendar time: integer milliseconds until the immutable Run deadline.

Floating-point currency is prohibited in authoritative budget comparisons.

### 12.3 Classification

```text
meaningful_progress
partial_progress
no_progress
blocked
```

`regression` remains deferred until a deterministic WP2 use case and transition rule are frozen.

### 12.4 Minimum deterministic rules

WP2 must implement and test at least:

1. Consecutive identical `action_identity` and `result_identity` consume the no-progress budget.
2. Repeated schema-invalid model output consumes schema-recovery and no-progress allowances according to the versioned policy.
3. A side-effecting ToolCall with `external_state = unknown` is `blocked`; automatic retry is prohibited.
4. Information only the user can provide leads to `REQUEST_HUMAN_INPUT`, not blind Tool retries.
5. Approval pending uses `suspended`; terminal Policy/Approval denial remains Runtime-owned.
6. No new Evidence, no reduction in blockers, and repeated action/result identity is `no_progress`.
7. New Evidence may be only `partial_progress` when no blocker is reduced and no newly feasible action appears.
8. Any exhausted hard budget overrides a model request to continue.
9. A blocked action does not count as progress merely because a new log or denial record exists.
10. A changed error message with the same normalized error code and same authoritative external state is not automatically progress.

Thresholds and counters belong to a versioned `NoProgressPolicy`.

## 13. RuntimePolicyDecision

### 13.1 Purpose

`RuntimePolicyDecision` records the authoritative program decision that governs whether the Runtime may execute a persisted action, enter another Step, or end the Run.

### 13.2 Required fields

```text
RuntimePolicyDecision
- runtime_policy_decision_id
- agent_run_id
- agent_step_id nullable
- progress_assessment_id nullable
- policy_version
- decision_phase
- action
- reason_code
- policy_input_hash
- proposed_action_identity nullable
- evidence_refs[]
- resulting_status nullable
- resulting_outcome nullable
- resulting_pause_reason nullable
- resulting_termination_reason nullable
- created_at
```

### 13.3 Decision phases

```text
pre_action
post_observation
budget_guard
```

- `pre_action`: after the Agent decision is persisted and before Tool/Proposal/final-response dispatch.
- `post_observation`: after the Observation and `ProgressAssessment` exist, before another model call.
- `budget_guard`: before a model call or action whenever budget/deadline enforcement is required.

### 13.4 Actions and legal phase combinations

The V1 action set remains:

```text
allow_next_step
block_current_action
terminate_run
```

`allow_next_step` means “allow Runtime execution to continue from the current phase”; in `pre_action` it authorizes dispatch of the already-persisted Agent decision, while in later phases it authorizes another Step.

| `decision_phase` | Legal actions |
|---|---|
| `pre_action` | `allow_next_step`, `block_current_action`, `terminate_run` |
| `post_observation` | `allow_next_step`, `terminate_run` |
| `budget_guard` | `allow_next_step`, `terminate_run` |

Illegal phase/action combinations are invariant violations.

### 13.5 `block_current_action` state flow

A pre-action block means the proposed action is not dispatched.

The Runtime must atomically:

1. persist the authoritative Policy/Approval/authority result;
2. persist a structured non-execution Observation for the current Step;
3. preserve `ToolCall.execution_status = not_executed` where a ToolCall intent exists;
4. create the Step's `ProgressAssessment` with `classification = blocked`;
5. select exactly one continuation path:
   - Approval is required and pending: enter existing `suspended + tool_approval_pending` flow;
   - denial is non-terminal and a safe alternative may exist: allow a later Step with the denial Observation;
   - denial is terminal with no safe alternative: end according to the legal state matrix.

A Dashboard cannot perform these writes directly. The existing application/Runtime services own the transition.

### 13.6 Transaction boundary

A `RuntimePolicyDecision`, the state transition it governs, and the corresponding RuntimeEvent/Outbox row are committed in one Unit of Work. Projection failure cannot roll back or reinterpret the authoritative decision.

## 14. Retry ownership and side-effect safety

Retry is not one operation and remains separated by layer.

### 14.1 Tool adapter retry

Adapter retry applies to the same logical ToolCall only when **both** are true:

1. the failure class is versioned as retryable; and
2. duplicate side effects are proven impossible or controlled.

At least one of the following evidence conditions is required:

```text
request_not_delivered
provider_contract_confirms_not_accepted
verified_idempotency_key
read_only_operation
transaction_rolled_back_before_external_commit
```

Potential retryable classes may include:

```text
temporary_network_failure
provider_rate_limit
retryable_database_conflict
```

The class name alone is insufficient. A timeout after a side-effecting request was sent is `external_state_unknown` unless authoritative reconciliation proves otherwise.

### 14.2 Agent continuation

After a failed ToolCall, the model may propose a different action. This is not permission to repeat the original ToolCall. The new action receives a new decision identity and remains subject to Policy, ProgressAssessment, and budget guards.

### 14.3 Workflow Attempt retry

WP3 may create a new Attempt according to workflow-specific policy. It records:

```text
failure_classification
retry_policy_version
retry_reason
previous_attempt_id
idempotency_or_reconciliation_evidence
```

### 14.4 Prohibited automatic retry classes

At minimum:

```text
permission_denied
invalid_arguments
policy_blocked
approval_rejected
approval_expired
external_state_unknown
unknown_executor_state
```

A model request cannot override these classifications.

## 15. Persistence and database invariants

The following are architecture invariants. Exact SQL constraints are specified in the WP2 persistence plan.

1. A completed Agent Step has at most one effective final `ProgressAssessment` per `policy_version`.
2. An Agent Run/Step has at most one effective `RuntimePolicyDecision` for a given `decision_phase` and `policy_version`.
3. An Agent Run performs at most one successful transition into a terminal status.
4. Terminal transition retries are idempotent and return the already-committed terminal state.
5. A continuation request identity creates at most one continuation Agent Run.
6. A `RuntimePolicyDecision` that declares a resulting transition must match the committed AgentRun fields.
7. `ProgressAssessment.agent_step_id` references the Step whose Observation was assessed.
8. A `post_observation` policy decision that references a ProgressAssessment must reference the same Run and Step.
9. RuntimeEvent/Outbox publication is written in the same Unit of Work as each authoritative record.
10. AgentOps projection reprocessing is idempotent and cannot create a second authoritative decision.

Candidate constraints such as the following are illustrative, not yet frozen SQL:

```text
UNIQUE(agent_step_id, policy_version)                           # final assessment
UNIQUE(agent_run_id, agent_step_id, decision_phase, policy_version)
UNIQUE(continuation_request_identity)
```

Tenant keys, nullable-Step behavior, supersession, and partial-index details are finalized in WP2.

## 16. AgentOps and UI consumption

### 16.1 Ownership boundary

```text
Agent Runtime
  → authoritative ProgressAssessment / RuntimePolicyDecision / AgentRun transition
  → RuntimeEvent + Outbox
  → AgentOps Projection
  → API / UI
```

AgentOps does not terminate Runs, change ToolCall state, grant Approval, or edit Trial/Attempt state.

### 16.2 Projected information

AgentOps projects:

```text
ProgressAssessment
RuntimePolicyDecision
AbstentionReport
HumanInputRequest
Run continuation lineage
```

### 16.3 UI requirements

For human-input, abstention, Runtime stop, exhaustion, failure, or cancellation, the UI displays structured fields rather than inferring from logs:

```text
lifecycle status
business outcome
end-reason class
reason code
confirmed facts
missing or unmet preconditions
actions not taken
recovery conditions
evidence references
parent/root Run lineage
```

The UI may present linked Runs as one interaction, but must preserve Run boundaries and never imply that a completed Run was reopened.

## 17. Evaluation design

### 17.1 Required paired case families

| Case | Expected behavior |
|---|---|
| Complete repository, parameters, authority, and data | Continue and produce the authorized deliverable |
| Missing user-supplied dataset location | `REQUEST_HUMAN_INPUT` |
| Required Tool is absent from the authoritative Capability Registry | Structured abstention or Proposal revision |
| A Tool exists but is not exposed or Policy-allowed | Runtime block; not model abstention |
| Side-effecting Tool requires pending Approval | `suspended + tool_approval_pending` |
| Approval is rejected and a safe alternative exists | Record denial Observation and continue with a different action |
| Approval is rejected and no safe alternative exists | `completed + no_deliverable + approval_rejected` |
| Previous side effect has unknown external state | No automatic retry; require reconciliation or safe terminal stop |
| Requirements can be clarified by the user | `REQUEST_HUMAN_INPUT` |
| Requirements are intrinsically unsatisfiable | Structured abstention |
| Repeated action/result identities add no Evidence | `exhausted + no_deliverable + no_progress` |
| Required Artifact is missing | Validation invalid; no fabricated conclusion |
| No eligible Trial exists | No research conclusion is generated |
| Internal persistence/provider/invariant failure | `failed`, never abstained |

### 17.2 Required metrics

```text
correct_action_rate
correct_abstention_rate
correct_human_input_request_rate
correct_runtime_block_rate
erroneous_action_rate
premature_abstention_rate
irreversible_action_before_stop_rate
tool_calls_before_abstention
tokens_before_abstention
steps_before_abstention
first_blocker_to_stop_steps
no_progress_termination_rate
continuation_duplicate_prevention_rate
```

Metrics distinguish:

- intentional abstention;
- human-input request;
- Policy/Approval block;
- budget/no-progress exhaustion;
- unexpected failure;
- user cancellation.

### 17.3 Release-gate principles

WP2 does not pass solely on ordinary task-completion success. It must demonstrate:

1. correct behavior on paired should-act / should-ask / should-abstain / should-stop cases;
2. prevention of irreversible action in blocked scenarios;
3. deterministic termination of repeated no-progress trajectories;
4. no blind retry after unknown side effects;
5. complete persistence of reason, evidence, lineage, and recovery semantics;
6. idempotent duplicate continuation handling;
7. state-combination validation for all terminal paths.

Exact numeric thresholds are frozen in the WP2 implementation plan after the benchmark set is fixed.

## 18. Work-package impact

### 18.1 WP0 Foundation

No change. WP0 remains complete; the `v0.1.0-foundation` tag and acceptance evidence are not moved or regenerated.

### 18.2 WP1 Domain and Context

WP1 adds only the typed facts needed by later Runtime decisions:

- execution preconditions;
- required information/capability/authority;
- environment assumptions;
- unresolved questions;
- fact state and provenance;
- stable identities and visibility scope.

WP1 does not implement Agent abstention, no-progress policy, or Runtime termination.

### 18.3 WP2 Agent and Tool

Primary implementation scope:

- `FinalResponseKind.abstention`;
- `AgentRunOutcome.abstained`;
- state-combination validation;
- `HumanInputRequest` and continuation idempotency;
- `AbstentionReport`;
- reason registry and precedence;
- canonical progress identities;
- `ProgressAssessment`;
- `NoProgressPolicy`;
- `TerminationPolicy`;
- phased `RuntimePolicyDecision`;
- side-effect-aware Tool adapter retry;
- should-act / should-ask / should-abstain / should-stop Eval.

### 18.4 WP3 Workflow and Runner

Retain the existing unknown-state and reconciliation model. Ensure every workflow retry records classification, policy version, reason, previous Attempt, and idempotency/reconciliation evidence.

### 18.5 WP4 Result Plane and AgentOps

Add projections, timelines, filters, aggregates, and metrics for:

- progress classifications;
- abstentions;
- human-input requests;
- policy decisions;
- continuation lineage;
- blockers-to-stop latency;
- actions deliberately not taken.

WP4 adds no direct authoritative writes.

### 18.6 WP5 UI

Display structured stopping semantics, continuation lineage, and recovery conditions. UI text is derived from persisted fields, not inferred from unstructured logs.

### 18.7 P1 predictive early stopping

A learned `SuccessProbabilityAssessment` may be explored only after V1 has sufficient real trajectories. It may recommend:

```text
continue_recommended
ask_human_recommended
abstain_recommended
terminate_recommended
```

It cannot directly mutate authoritative state. Runtime Policy remains the final decision-maker. Predictive stopping is not a V1 release dependency.

## 19. Compatibility and migration rules

1. Existing top-level Agent decision values remain valid.
2. Existing `FINAL_RESPONSE` records migrate semantically to `response_kind = answer`.
3. `outcome = abstained` is additive and is introduced in WP2 migrations, not WP0.
4. The frozen AgentRun status enum is retained; no `terminated` value is added.
5. Existing `termination_reason` remains the persisted field. The reason registry adds semantic classes without rewriting historical rows.
6. Existing `root_agent_run_id`, `parent_agent_run_id`, `continuation_reason`, `user_response_to_step_id`, and `interaction_correlation_id` remain the only AgentRun lineage fields.
7. No historical failed, exhausted, or no-deliverable Run is retroactively reclassified as abstained without explicit evidence and a separate migration policy.
8. AgentOps consumers tolerate absent progress, policy, abstention, or human-input records for historical Runs.
9. Provider adapters do not expose framework-native retry, thread, graph, checkpoint, or stop state as LabPilot authoritative state.
10. Identity namespace changes create a new version; historical v1 identities remain stable.

## 20. Security and safety constraints

1. The model cannot claim that Approval was granted, a Tool exists, a Tool is exposed, a path is trusted, or authority is present unless the Harness supplied that authoritative fact.
2. An abstention cannot hide an already-executed or unknown side effect.
3. A side-effecting ToolCall is blocked before execution when required Approval or authority is absent.
4. Unknown external state is surfaced explicitly and cannot be converted into a clean abstention.
5. Automatic retry requires both a retryable failure class and evidence of side-effect safety or verified idempotency.
6. Evidence references are tenant- and authorization-scoped through API and UI.
7. Canonical identities exclude secrets, credentials, unbounded outputs, unstable timestamps, and irrelevant absolute paths.
8. A repeated policy denial, log entry, or error-message variation is not itself meaningful progress.
9. Human-input continuation validates the response against the persisted input contract before creating the new Run.
10. Runtime decisions remain lease-, row-version-, and transaction-protected according to the frozen Agent Harness.

## 21. Acceptance criteria for this supplement

This supplement is satisfied when the relevant WP plans and implementations demonstrate all of the following:

1. No fifth top-level `ABSTAIN` decision exists in V1 Core.
2. No generic model-controlled `RETRY` decision exists.
3. Intentional abstention is `FINAL_RESPONSE(response_kind=abstention)`.
4. `status = completed, outcome = abstained, termination_reason = final_response_produced` is supported.
5. Legal state combinations are validated and illegal combinations are rejected.
6. Unexpected failures are distinguishable from deliberate Runtime stops.
7. Human-input continuation uses the existing immutable Run lineage and an idempotency boundary.
8. Runtime-owned reason codes cannot be asserted solely by model output.
9. `RuntimePolicyDecision.decision_phase` is persisted and phase/action compatibility is enforced.
10. `block_current_action` prevents dispatch and records the resulting Observation and continuation path.
11. Deterministic no-progress rules are versioned and covered by tests.
12. Cost and time budgets use stable integer units.
13. Action, result, state, observation, and continuation identities use versioned Canonical JSON V1 namespaces.
14. Unknown or potentially duplicated side effects are never retried automatically.
15. `ProgressAssessment` and `RuntimePolicyDecision` are authoritative Agent Runtime records.
16. AgentOps projects these records without becoming a direct state writer.
17. Paired should-act / should-ask / should-abstain / should-stop cases are part of the WP2 release gate.
18. Learned predictive stopping remains optional and deferred beyond V1 Core.
19. WP0 and `v0.1.0-foundation` remain unchanged.

## 22. Final architectural decision

LabPilot continues with:

```text
native Python state machine
+ PostgreSQL authoritative state
+ immutable linked Agent Runs
+ typed Tools and typed human-input contracts
+ deterministic Policy and Approval
+ bounded execution budgets
+ structured abstention
+ namespaced deterministic progress identities
+ phased Runtime Policy decisions
+ side-effect-aware retry control
+ Runtime-owned terminal transitions
+ observable, non-authoritative AgentOps projections
```

The differentiating capability is not merely that the Agent can call Tools or stop. It is that LabPilot can prove:

- whether acting was authorized and useful;
- why the Agent asked, abstained, failed, exhausted its budget, or was blocked;
- what irreversible actions were avoided;
- whether a retry was safe;
- how a user response created a new immutable continuation Run;
- which authoritative program decision caused each state transition.
