# LabPilot P1-C — Bounded Historical Experiment Comparison

> 历史设计记录。当前实现及验收边界见[公开能力与验收证据](../evidence/research-runtime-preview.md)。

Date: 2026-10-03 (Asia/Shanghai)
Status: **APPROVED by the user on 2026-10-05 / implementation accepted locally**
Planning baseline: `719134c7d66c02877aa4d30d6cfb72b4c1ef4157`
Scope: same-Experiment historical evidence for Follow-up AgentRuns.
Approved planning commit: `a80cb7e042b412bddf252fea282a0c105248b6f9`.
Implementation: **COMPLETE**, `1758fc8b0cb552384e4adb2053f1629562bb88d7`;
isolated `feature/p1-c-history-comparison` worktree; no merge,
push, interview database migration, paid provider call or host training authorized.
The approval is the user's explicit “批准”; the prior author consistency review
is not represented as an independent review.
The original P1-C local closeout and subsequent standards self-review remain in the development archive. See the [public history-comparison acceptance summary](../evidence/research-runtime-preview.md#history-comparison-and-trial-facts) for the published scope and observed follow-ups.

## 1. Problem and outcome

P1-A/B already provide exact ResultContext binding and same-Experiment follow-up
proposals. Post-P1 maintenance joins parameters/seed with each source Trial's
outcome and rejects proposals that only repeat that source Batch's invalid
conditions. These are accepted capabilities, not gaps to implement again.

A Follow-up currently receives one BatchSummaryRevision. It cannot query earlier
Batches/Versions in that Experiment through the registered Agent tools. P1-C
supplies a bounded historical comparison surface: which recorded conditions ran,
which eligible evidence was selected, why other results were excluded, and which
experimental semantics differ. It does not certify that a new condition will
reach the target or that all historical conditions have been searched.

Example acceptance scenario: V1 ran epochs 100 and 1000; V2 ran 300 and 500.
A Follow-up from V2 can inspect the corresponding exact historical evidence from
V1 alongside its source ResultContext before proposing V3, asking a human, or
stopping. The values are fixture examples, not hard-coded search recommendations.

## 2. Authority and existing code

This draft is subordinate to [V1 Scope v0.3](../../labpilot/01-v1-scope-and-acceptance-v0.3.md),
[Architecture 10](../../labpilot/architecture/10-v1-core-architecture-rebaseline-v0.3.md)
and [Roadmap v0.3](../../labpilot/implementation/01-master-implementation-roadmap-v0.3.md).
The [accepted P1 design](2026-09-20-labpilot-p1-result-aware-iteration-design.md)
§4.3 explicitly defers P1-C until P1-A/B acceptance. The user's 2026-10-03
instruction authorizes planning/review; this draft does not authorize implementation.

Observed at the planning baseline:

| Existing code | Constraint to preserve |
|---|---|
| `api/result_aware_agent_adapter.py:create_bound_run` | Source Batch lock, latest-summary check, clean snapshot and lineage checks; transactional run creation |
| `agent_runtime/result_context.py` and `context_compiler.py` | Exact source context; per-Trial evidence; 128 KiB source-context / 64 KiB per-tool-result / 256 KiB compiled-message limits |
| `agent_runtime/service.py` | Factory currently accepts snapshot UUID; four decision variants; persisted model steps and ToolCall results |
| `main.py:tool_service_for_snapshot` | Four statically registered repository tools |
| `tools_policy/service.py` | Arguments validation, requested call persistence, execution, canonical result/hash persistence |
| `AgentRunRow` | Only a source-summary FK; no historical snapshot field |
| Architecture dependency tests | Agent runtime cannot import execution/results modules; Tools Policy cannot import Agent Runtime |

No weakening of dependency allowlists is proposed. The SQL adapter remains in
API composition, as in accepted P1. The pure history DTO/tool definition belongs
in Agent Runtime, which may depend on ToolDefinition.

## 3. Narrow design choice

Capture one bounded **HistoryContext** during new Follow-up creation and expose
it through one static tool, `experiment.read_history` version `v1`.

```text
validate source summary + source lineage + bound clean snapshot
→ select bounded earlier history in the existing run-creation UoW
→ persist frozen HistoryContext with AgentRun
→ Agent optionally calls experiment.read_history({})
→ existing ToolCall audit/result-hash path
→ existing prior_tool_results in the next model request
→ existing stop / human input / proposal path
```

The full history is not injected into every initial prompt. It is read on demand
through the existing tool-result path. No new Agent action, provider session,
worker, generic retrieval registry or background processing is introduced.

The source summary remains the sole owner of Follow-up lineage and the existing
invalid-repeat guard. History does not replace it or expand that guard to all
versions. Repeating a condition may be legitimate under changed experimental
semantics; P1-C is evidence access, not a new experiment-eligibility algorithm.

## 4. Selection policy: `same-experiment-history-v1`

Derive Project and Experiment from the locked source lineage, never from model
arguments. Exclude the source Batch; it is already available in ResultContext.

Eligible history Batches:

- belong to that exact Project and Experiment;
- have an ExperimentVersion number no greater than the source version;
- precede the source Batch by `(Batch.created_at, Batch.batch_id)`;
- have at least one persisted BatchSummaryRevision.

For each eligible Batch, choose the highest persisted revision number visible in
the selection statement. Order candidates by version number descending, Batch
creation time descending, and Batch UUID descending. Fetch at most four candidate
references; inspect only the first three. The fourth indicates that more history
exists. A missing-summary Batch is outside the eligible set, not proof of absence.

Limits are constants, not model-supplied or new settings:

| Limit | Value |
|---|---|
| Candidate references inspected / look-ahead | 3 / 1 |
| Trial conditions per returned Batch | 20 |
| Total returned conditions | 60 |
| Canonical HistoryContext UTF-8 bytes | 32 KiB |
| Full JSON input precheck per candidate | 128 KiB |

Candidate discovery selects IDs and scalar metadata, not entire result JSON.
Apply Project/Experiment/version/cutoff filters before ranking summary revisions;
the candidate limit bounds returned references, not database query work.
Precheck Trial count and the PostgreSQL text-byte size of the required summary /
proposal JSON before materializing it. These checks bound application loading;
the final canonical envelope must still satisfy the stricter 32 KiB limit.

Return only complete Batch evidence blocks. Omit a whole candidate if its Trial
count, input bytes or remaining envelope budget exceed the limits; record its
Batch/revision IDs and an omission code. Do not silently slice its Trials, round
metrics, or search arbitrarily far back to fill the quota. Record
`has_more_candidates` and `coverage_limited` separately. An empty captured context
is valid and may still have `coverage_limited=true`.

Selection fixes exact revision IDs. Later revisions may be observed by a new
Follow-up, but cannot change the captured context of an existing run. No global
database snapshot, exhaustive history or result freshness guarantee is claimed.

## 5. Evidence contract and scientific boundaries

HistoryContext uses an allowlisted schema, `extra="forbid"`, with:

- `schema_version`, `selection_policy_version`, owning `project_id` / `experiment_id`, exact
  `source_batch_summary_revision_id`;
- `batches`: exact version/proposal/Batch/summary IDs and revision numbers,
  repository snapshot identity, design metadata and Trial evidence;
- `omitted_batches` with bounded reason codes, `has_more_candidates`,
  `coverage_limited` and effective limits.

Each Batch design includes frozen source identity, dataset reference/hash,
environment expectation/hash, train/eval entrypoint, workdir, argv template,
metric/artifact declarations and validation protocol hash. Parameter values and
seed are carried per Trial, not inferred from proposal text or summary averages.
New captures also expose the approved Version's frozen validation rules and exact
decimal thresholds; recompute their existing protocol hash and match it to the
design and referenced reports. Older captures without rules remain readable with
unknown criteria; do not reconstruct or backfill them. The optional additive
field is omitted from their serialization, preserving the older evidence output.

Each Trial includes `trial_id`, ordinal, exact resolved parameters and seed:

- `selected_valid`: selected Attempt/report references and numeric metrics copied
  **from the chosen persisted summary**, with metric observation / Artifact IDs.
- `excluded`: persisted exclusion code and validation reason codes; no invalid
  raw numeric values. Execution errors are not rewritten as validation failures.

Read immutable extractor version and validation-report protocol / extraction /
validator versions via the references selected by that summary. Validate ownership
and exact Trial coverage; do not fetch the latest selection or latest raw numeric
values. Missing/mismatched referenced evidence is a lineage error, not empty
history. Warnings remain part of each Batch block.

Code, dataset identity, environment, metric split/definitions, validation threshold
and processing versions can differ across versions of the same Experiment. Keep
these differences visible. This tranche adds no automatic global ranking, merged
mean, statistical significance claim or "best Trial" decision across Batches.
Best-effort datasets do not become strictly reproducible merely because a URI
matches. Missing from bounded history does not mean "never run".
Protocol-valid is not proof of generalization or statistical significance. Repeated
tuning on the same validation split can overfit it; independent test performance
cannot be inferred from those metrics.

No raw Artifact contents, stdout/stderr, checkpoints, host environment, provider
credentials or arbitrary filesystem reads enter HistoryContext. Existing
data-egress checks still run before every provider call. Approved design metadata
is exposed only through the same Project/Experiment boundary as its source.
Evidence free text and repository guidance are data within the system/user
instruction boundary. They cannot override the user's goal or authorize actions.
Prompt separation is a mitigation, not an enforcement boundary or injection-immunity
claim; deterministic tool/verification/approval controls remain in force.

## 6. Persistence, tool and API contracts

Add one nullable JSONB column `agent_runs.history_context_json`, via a new migration
after `0013_result_aware_agent_runs`. No new table, independent aggregate, identity
hash family or result-processing job is required. The column is justified by
run-specific immutable input provenance: the source FK alone cannot reconstruct
the earlier revisions visible when a run was created.

Within `create_bound_run`, after existing checks, select and validate history,
serialize canonically, enrich AgentRunDetails, then persist it in the same UoW.
Failure rolls back run creation. Do not change the source Batch lock or UoW
isolation level; historical immutable evidence requires no extra Batch locks.
The selected references, rather than a cross-table "latest" re-read, define the
captured evidence. Never update a created run's history in state transitions.

Ordinary runs and pre-migration Follow-ups keep NULL. Do not backfill or lazily
capture history on their next advance. They retain four repository tools.
New Follow-ups store a non-NULL envelope even when history is empty, and receive
those four tools plus the history tool. State transitions retain its exact bytes.

The tool input schema is an empty object with `additionalProperties=false`.
The model cannot supply an Experiment/Project/summary ID, SQL, path, filters or
larger limits. Risk=green, side_effect=read_only, approval_required=false.
Bind source and historical revision IDs into deterministic `x-labpilot-*`
schema metadata so the existing bound-toolset hash records the selected tool
contract. Reuse existing hash functions; do not add a new hash namespace.
Its executor returns a fresh serialization of the persisted context and performs
no SQL or business mutations. Tool audit writes are handled by the existing
ToolApplicationService. Repeated calls return the same evidence; existing runtime
budgets still apply.
Byte bounds do not guarantee that a provider's token budget is sufficient.
History access consumes the existing model-step/call budget and subsequent
requests include its tokens; P1-C does not raise budgets or promise to eliminate
`max_tokens` termination.

Validate the persisted envelope's Project/source IDs against AgentRunDetails and
its Experiment/source IDs against the loaded ResultContext before a provider
call. A malformed non-NULL envelope fails closed; it must not become a legacy
run or be recaptured from current history.

Change the internal factory to accept persisted AgentRunDetails instead of only
snapshot UUID, so composition can bind both repository and history evidence.
Do not resolve the factory while creating a run or on terminal/reserved-step
paths. Preserve one service instance per advance and source-snapshot isolation.

Expose an optional `history_context` on the existing GET Agent trace response for
human audit; return NULL for legacy/ordinary runs. No new public command, history
endpoint, or operator UI redesign. Add a small system policy describing the
tool's bounded coverage and comparison caveats, without injecting its payload.

Migration upgrade is additive. Empty-history downgrade may be tested; downgrade
must refuse once a non-NULL frozen context exists rather than erase provenance.
Do not downgrade or migrate the interview database during planning.

## 7. Risks, gates and exclusions

Risk tier: **Boundary-sensitive**, with source/Project isolation and evidence
integrity treated as safety-sensitive. Critical failures are cross-Experiment
leakage, mutable/reconstructed history, invalid metric promotion, partial Trial
coverage presented as complete, or approval/launch bypass.

Acceptance requires real PostgreSQL migration/lineage tests and a scripted-provider
multi-version history path. Scripted responses prove control flow, not LLM research
quality. No paid provider request or new host training campaign is necessary for
this read-only feature; existing P1 two-iteration and architecture regressions
remain mandatory, followed by the full CI-equivalent gate at the feature boundary.

Out of scope: controlled code edits, cross-Experiment search, semantic ranking,
historical repeat rejection, Context Compaction/Projection, Memory, Multi-Agent,
MCP/plugins, distributed scheduling, Runner changes and Slice 2 production
hardening. No P2 implementation is authorized.

Approval sequence: review this concrete design → human design approval → approve
the detailed implementation plan (retained in the development archive)
→ separate implementation branch. This sequence records the design-time approval path. Accepted P1 and maintenance records remain
historical and unchanged.
