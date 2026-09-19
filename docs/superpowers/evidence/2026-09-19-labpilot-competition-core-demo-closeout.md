# LabPilot Competition Core Demo — Runtime/E2E Closeout

**Date:** 2026-09-19

**Status:** Accepted / Frozen

**Product evidence baseline:** `c92bf45b63053b1ae00209660ed351cf309f91c8`

This document records the final competition-specific Runtime/E2E evidence. It closes the competition Core Demo runtime path only; it does not broaden V1 product claims or supersede the existing authority hierarchy.

## 1. Authority

- Product SHA: `c92bf45b63053b1ae00209660ed351cf309f91c8`
- Final image: `sha256:c0718d855df3e77f3b9c8aa8036bdc7d98bcda98ae34271eaa64cbc522a9cf56`
- Demo repository HEAD: `151551808185b76230bc10ac8951031d7f8e0439`

## 2. Fresh browser Golden Path

The fresh browser run completed:

```text
research goal
→ DeepSeek Agent
→ bounded repository tools
→ Proposal
→ deterministic verification
→ human approval
→ immutable ExperimentVersion
→ Batch / Trial / Attempt
→ real training
→ Artifact / Metric / Validation
→ Result Selection
→ Batch Summary
```

Persisted identities:

- AgentRun: `abaeee0f-df6c-43c7-970d-57b6dbddcd87`
- ExperimentVersion: `fe9382e5-5d0b-46e0-a415-2a3fa5ef4c9b`
- Batch: `8aa8d851-2546-4388-a07e-479dbc6a85d5`

Agent evidence:

- Provider: `deepseek_chat`
- Model: `deepseek-v4-flash`
- Model calls: `3`
- Total tokens: `10209`
- Repository tool calls: `repository.list_tree`, `repository.read_text`

Trial results:

| Trial | epochs | learning_rate | seed | accuracy | validation | selected |
|---|---:|---:|---:|---:|---|---|
| 1 | 100 | 0.0005 | 7 | 0.650 | invalid | no |
| 2 | 100 | 0.005 | 7 | 0.895 | invalid | no |
| 3 | 1000 | 0.0005 | 7 | 0.895 | invalid | no |
| 4 | 1000 | 0.005 | 7 | 0.941 | valid | yes |

Result closure:

- Attempts: `4`
- Retries: `0`
- Artifacts: `8`
- Metric observations: `4`
- Validation reports: `4`
- Result selections: `1`
- Batch summary revisions: `1`
- Summary accuracy mean: `0.941`
- Declared target: accuracy `>= 0.92`
- Target met: yes

```text
FINAL_FRESH_BROWSER_GOLDEN_PATH=PASS
```

## 3. Same immutable ExperimentVersion recovery replay

A separate recovery replay reused the exact approved ExperimentVersion from the browser run. No new AgentRun was created and DeepSeek was not called for the replay.

- Recovery Batch: `a6b9bc35-ff4d-40fd-82c7-2267fb12f5ab`
- ExperimentVersion: `fe9382e5-5d0b-46e0-a415-2a3fa5ef4c9b`
- Target Attempt: `22d7f60d-97de-4f67-9c7d-9962f95e5c45`
- Execution identity: `labpilot-v1-a21f68847388e09ccf66c1e80ffd8e419547366a8dd652982657a033bf661664-22d7f60d97de4f679c7d9962f95e5c45`
- systemd InvocationID: `4c709f0906f84f25bcad26b113f4a54c`
- Physical boot ID: `eb4a2c06-759c-4d26-952b-b8ef538568f7`

Fault-injection evidence:

```text
pre-restart Attempt status             = running
post-restart Attempt status            = running
pre/post Attempt ID                    = identical
pre/post execution identity            = identical
pre/post systemd InvocationID          = identical
startup physical observation           = active_match
physical start count before restart    = 1
physical start count after restart     = 1
```

Only the Coordinator was restarted. API, PostgreSQL, and Runner were not restarted. The durable event recorded `attempt.reconciled_after_restart` with `running → active_match → running`.

After reconciliation the original workload resumed and the Batch completed naturally:

- Trials: `4`
- Attempts: `4`
- Retries: `0`
- All Attempts: `terminal / process_exited / exit_code=0`
- All four physical start counts: `1`
- Scientific results: identical to the fresh browser Batch
- Result facts: artifacts `8`, metrics `4`, validations `4`, selections `1`, summaries `1`
- Summary: revision `1`, included `1`, excluded `3`, accuracy mean `0.941`

```text
FINAL_RECOVERY_REPLAY_AUDIT=PASS
NO_DUPLICATE_PHYSICAL_LAUNCH=PASS
```

This supports the bounded invariant that, after a Coordinator restart, LabPilot reconciled to the original running physical execution instead of launching a second physical process.

## 4. Exact-authority full CI

The exact product baseline `c92bf45b63053b1ae00209660ed351cf309f91c8` was verified with:

```text
./scripts/check_ci.sh
```

The final run used an isolated ephemeral PostgreSQL 16 instance and did not touch the competition database.

```text
264 passed
1 skipped   # external OpenCode Go smoke is opt-in
5 deselected
architecture dependency guard tests: PASS
SOURCE_CLEAN_AFTER=YES
EXACT_AUTHORITY_FULL_CI=PASS
```

## 5. Claim boundary

Accepted competition wording:

> **Fresh browser Golden Path PASS + same immutable ExperimentVersion recovery replay PASS.**

Not established by this closeout:

- The fresh browser Batch itself did not undergo the Coordinator restart; recovery was proven in a separate Batch using the same immutable ExperimentVersion.
- LabPilot does not claim global exactly-once execution.
- This does not complete Slice 2 production hardening or change Slice 2 production acceptance to yes.
- This does not establish production-scale scheduling or general production readiness.
- LabPilot is not claimed to be an autonomous research agent.
- The competition capability remains a **bounded Agent-assisted Experiment Runtime / Harness** with controlled Agent planning, deterministic verification, human approval, reliable execution, reconciliation, and deterministic result closure.

## 6. Competition closure consequence

Competition Core Demo Runtime/E2E is accepted and frozen.

```text
UI visual polish
→ recording
→ architecture diagram
→ PPT
```

Before competition closure, do not reopen Slice 4, Multi-Agent, result-aware replanning, historical experiment comparison, or a new Runtime-hardening tranche unless a concrete competition-blocking defect is discovered.
