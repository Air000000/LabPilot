# Competition Golden Path v0.1

Status: Proposed / requires user confirmation before implementation
Baseline: `c71c25968e84ad9061ac2a7bd28bbfa4743f8183`
Branch: `feat/competition-mvp`

## 1. Product story

LabPilot is presented as an AI research experiment operator: a user gives a research goal, the Agent inspects a bounded repository snapshot, proposes a reproducible experiment, the user approves it, LabPilot executes the real training workload, validates the declared result deterministically, and selects a trustworthy result.

The competition layer is a presentation and demo overlay. It must not redefine Core identities or execution/result semantics.

## 2. Golden Path scenario

Use one small real ML repository bundled for the competition demo. The repository should contain a deterministic CPU-only training task that finishes in seconds to tens of seconds and emits the existing Result Plane contract.

Recommended scenario:

- task: small binary classification;
- implementation: pure Python logistic regression or similarly small real optimizer, avoiding new heavyweight runtime dependencies;
- dataset: versioned local CSV committed with the demo workload;
- parameter sweep: `learning_rate` and `epochs`;
- seeds: two bounded seeds;
- maximum planned Trials: four;
- primary metric: `accuracy`;
- success rule: `accuracy >= 0.80`;
- artifact: `result.json` using Result Plane `result-json-v1` format;
- optional second artifact: lightweight `model.json` for presentation only.

The main demo must exercise a real training process, not the acceptance fixture under `tests/acceptance/slice2/training_fixture/`.

## 3. User-facing goal

Recommended fixed demonstration goal:

> Inspect this repository and propose a small parameter-sweep experiment that can improve validation accuracy to at least 0.80. Use at most four Trials, keep execution CPU-only, and define deterministic validation so only results meeting the target are eligible for selection.

The exact threshold may be adjusted only after the demo workload is measured locally. The competition UI may provide this sentence as a default editable goal.

## 4. Expected Agent behavior

The Agent remains bounded to the current read-only repository toolset:

1. `repository.list_tree`
2. `repository.read_text`
3. optionally `repository.search_text`
4. optionally `repository.path_fact`
5. produce exactly one structured Proposal decision

The demo repository README should document enough factual execution contract for a model to derive a valid Proposal without hidden operator knowledge:

- training entrypoint;
- CLI arguments;
- allowed parameter names and ranges;
- dataset path and stable identity;
- output directory contract;
- `result.json` schema;
- metric name;
- Python/runtime requirements;
- expected CPU execution;
- safe timeout and retry expectations.

## 5. Required Proposal shape

The generated Proposal should use existing Core concepts only:

- `experiment_type = parameter_sweep`;
- clean bound `RepositorySnapshot`;
- demo training entrypoint;
- repository-relative workdir;
- parameter matrix with no more than four resulting Trials in combination with seeds;
- strict local DatasetReference with a stable identity;
- Python 3.12 environment expectation;
- no GPU/CUDA requirement;
- artifact declaration for `result.json`;
- metric declaration for `accuracy` sourced from that artifact;
- validation rules:
  - artifact exists;
  - metric finite;
  - accuracy >= target threshold;
- bounded timeout;
- small retry budget;
- `train_argv_template` using existing `{parameter:*}`, `{seed}`, `{dataset}`, and `{output_dir}` placeholders.

## 6. End-to-end demo sequence

```text
Prepared Project + clean RepositorySnapshot
  -> Create AgentRun from natural-language Research Goal
  -> Agent inspects repository through bounded read-only tools
  -> Agent creates ProposalRevision
  -> deterministic Proposal verification
  -> Human Approval
  -> immutable ExperimentVersion
  -> create/start Batch
  -> expand Trials / Attempts
  -> real CPU training processes
  -> collect result artifact
  -> Metric extraction
  -> deterministic Validation
  -> TrialResultSelection
  -> BatchSummary
  -> Mission Control displays final result and evidence
```

Repository registration/snapshot onboarding is not part of the initial competition Golden Path. The demo Project and clean Snapshot are prepared before the visible demo starts because repository onboarding has no current public HTTP router and does not contribute enough scoring value to justify new Core surface before the deadline.

## 7. UI projection

The competition UI projects the existing domain into seven presentation states without adding a new persisted Competition domain:

```text
IDLE
ANALYZING
AWAITING_APPROVAL
RUNNING
VALIDATING
COMPLETED
FAILED
```

These are presentation states only. Core AgentRun / Proposal / Batch / Trial / Attempt / Result statuses remain authoritative.

One Mission Control page should show:

- editable Research Goal;
- Agent action timeline based on persisted tool/model decisions, without chain-of-thought;
- Proposal summary and Approve/Reject controls;
- Trial execution table;
- SSE-driven progress/events;
- selected result, metric, validation evidence, and artifact provenance.

## 8. Reliability story

The main live demo should favor the stable happy path. Recovery/no-duplicate-launch evidence should be presented as a secondary reliability story using existing accepted evidence or a pre-recorded clip, rather than deliberately crashing the live demo.

## 9. Explicit non-goals before initial submission

Do not add before the initial competition submission unless a Golden Path blocker is proven:

- multi-agent orchestration;
- autonomous code modification;
- generic browser tools;
- long-term memory;
- multi-user/authentication;
- full repository onboarding UI;
- generic workflow engine;
- cloud/distributed execution;
- Slice 2 production hardening;
- a new Competition aggregate/state machine;
- a large dashboard or design system.

## 10. Acceptance gate for moving to implementation

Before implementing the demo workload or frontend, confirm these four decisions:

1. The primary demo is a small real CPU ML training task, not the acceptance fixture.
2. The visible demo begins from a pre-registered Project + clean RepositorySnapshot.
3. The Agent remains repository-read-only and proposes experiments; execution starts only after deterministic verification and human approval.
4. The initial UI is one Mission Control page and the reliability crash/recovery sequence is secondary evidence, not the live happy-path requirement.

After confirmation, implementation order is:

1. add and measure the deterministic real ML demo workload;
2. prove one full backend Golden Path with a real model provider;
3. add only the minimum read/API adapter gaps proven by that run;
4. build the single-page Mission Control UI;
5. harden and record the demo.
