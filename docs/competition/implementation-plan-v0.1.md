# Competition MVP Implementation Plan v0.1

Status: Approved / Executing
Branch: `feat/competition-mvp`
Core baseline: `c71c25968e84ad9061ac2a7bd28bbfa4743f8183`
Target submission: 2026-09-19

## Principle

Build the smallest competition layer that proves the existing LabPilot Core as a usable AI research experiment operator. Do not reopen accepted Core semantics unless a concrete Golden Path blocker is demonstrated.

## Task 0 - Freeze the Windows/WSL2 demo runtime

Target topology:

```text
Windows browser
  -> localhost Mission Control
  -> WSL2 Ubuntu
       -> FastAPI / Coordinator / Runner
       -> PostgreSQL (Docker)
       -> prepared demo Git repository
       -> CPU training process
```

Constraints:

- use the WSL2 Linux filesystem for LabPilot, demo repository, and attempt workspaces;
- do not use `/mnt/c` or `/mnt/e` for authoritative execution workspaces;
- systemd must be available in WSL2 for the existing Runner path;
- no GPU is required;
- the primary demo must remain runnable on one laptop.

Acceptance:

- WSL2 Ubuntu can run systemd;
- Docker/PostgreSQL reachable from the LabPilot runtime;
- Python 3.12 and repository development commands work;
- Runner socket and attempt workspace can use native Linux permissions.

## Task 1 - Build a real deterministic CPU ML workload

Files live under a competition-only demo template in this branch and are later materialized as a separate clean Git repository for the visible demo.

Required workload contract:

- real binary-classification training, not a synthetic sleep/result fixture;
- pure Python standard library where practical;
- committed local dataset;
- deterministic train/validation split;
- configurable `learning_rate`, `epochs`, and `seed`;
- accepts `{dataset}` and `{output_dir}` through CLI arguments;
- writes `runtime/output/result.json` through the supplied output directory using Result Plane `result-json-v1`;
- writes optional `model.json` for visible provenance;
- finishes each Trial quickly on CPU;
- a four-Trial bounded sweep contains both target-passing and target-failing results so deterministic validation is visible.

Acceptance:

- repeated identical CLI invocations produce identical metric/output bytes;
- four recommended Trial configurations complete successfully;
- at least one Trial is below the final validation threshold and at least one is above it;
- no network access or heavyweight ML runtime is required.

## Task 2 - Prepare a standalone clean demo repository

Add a small preparation/bootstrap path that copies the workload template into a WSL2-local directory, initializes or verifies a clean Git repository, and records the dataset identity needed by the Proposal contract.

The visible demo starts after Project, repository registration, and clean RepositorySnapshot preparation. Do not add repository onboarding UI for the initial submission.

Acceptance:

- the prepared repository is clean;
- Agent repository tools can inspect README, training entrypoint, and dataset contract;
- snapshot is approval-ready.

## Task 3 - Prove the real-model Agent proposal path

Use the existing bounded Agent Runtime and a programmatically callable model provider.

Expected Agent sequence:

```text
Research Goal
-> repository.list_tree/read_text/search_text as needed
-> structured Proposal
```

Acceptance:

- real provider produces a schema-valid Proposal;
- Proposal uses only existing Core concepts;
- deterministic verifier passes without manual JSON editing;
- the run is stable enough to repeat.

If no callable provider credential is available, this is a blocking external dependency for the real live Agent path and must be solved explicitly rather than hidden behind a fake provider.

## Task 4 - Prove the complete backend Golden Path

Run:

```text
Agent Proposal
-> Verify
-> Human Approval
-> ExperimentVersion
-> Batch
-> Trial / Attempt
-> real CPU training
-> Artifact / Metric
-> Validation
-> Result Selection
-> Batch Summary
```

Acceptance:

- one complete run reaches Batch Summary;
- selected Trial is backed by a valid metric/artifact;
- invalid Trial remains visible but is not selected;
- existing Core recovery/result invariants are unchanged.

## Task 5 - Add only proven presentation/read gaps

Do not create a new Competition domain. Add only thin read models/adapters required by the Mission Control UI after Task 4 demonstrates the exact missing surfaces.

Likely allowed additions:

- competition-oriented read aggregation;
- Agent step/tool-call timeline query;
- convenience endpoint for the prepared demo context;
- CORS/static-serving configuration if required.

Acceptance:

- no new authoritative state machine;
- UI does not need to reconstruct domain state through fragile client logic;
- Core write semantics remain unchanged.

## Task 6 - Build one Mission Control page

Presentation states only:

```text
IDLE
ANALYZING
AWAITING_APPROVAL
RUNNING
VALIDATING
COMPLETED
FAILED
```

Page sections:

1. Research Goal
2. Agent activity timeline
3. Proposal + Verify/Approve/Reject
4. Trial execution table
5. SSE progress
6. selected result + validation evidence + artifact provenance

Acceptance:

- one page can drive and explain the whole Golden Path;
- no chain-of-thought is displayed;
- the page is usable at laptop presentation resolution;
- no login, multi-user system, generic dashboard, or design-system work.

## Task 7 - Demo hardening and fallback

Primary live demo uses the happy path. Reliability/no-duplicate-launch is secondary evidence using existing accepted evidence or a recorded recovery sequence.

Prepare:

- known-good live demo state;
- repeatable reset/bootstrap command;
- pre-recorded full demo;
- screenshots / architecture evidence;
- known-good result data only as fallback, never represented as a live run.

Acceptance:

- three consecutive Golden Path rehearsals succeed;
- one failed external provider/network call has an explicit presentation fallback;
- live demo does not deliberately inject Coordinator failure.

## Task 8 - Submission package

Complete by 2026-09-18; 2026-09-19 is submission buffer only.

Deliverables:

- competition-facing project description;
- architecture diagram;
- screenshots;
- demo video if useful/required by the submission form;
- concise differentiation from chatbots, experiment trackers, and unrestricted autonomous research agents;
- README/demo instructions appropriate for judges.

## Stop rules

Before the initial submission, do not add multi-agent orchestration, autonomous code modification, long-term memory, generic browser tools, multi-user/auth, cloud scheduling, full repository onboarding UI, or deferred Slice 2 production hardening unless a demonstrated Golden Path blocker requires it.
