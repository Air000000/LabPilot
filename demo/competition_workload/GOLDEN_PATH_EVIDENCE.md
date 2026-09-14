# LabPilot Competition Golden Path Evidence

Validated on 2026-09-14.

## Authority

- Product branch: `feat/competition-mvp`
- Product SHA: `3557116a6363fadd646207a30198c9a591bfdc3a`
- Runner profile: `candidate-b-v2`
- Runner policy identity: `f8e53ccb18551d266c4c90a69b80dc88deb97665249ef0d6e42cbdd4511fac08`
- Execution environment: Python 3.12.3, CPU-only

## Golden Path Identity

- Project: `0dfc6c15-799c-4df7-bd58-e506708af7d7`
- Agent run: `c6d80472-31f7-4c6c-9e56-6f00d3cde1ed`
- Proposal revision: `29fb3e99-e74e-47f3-849a-75a398c4d307`
- Proposal verification: `b4871e14-fd3e-4a94-aa9d-3d81aecece31`
- ExperimentVersion: `6e19ac98-8f6c-4c8b-b698-8c35b7a9cd2d`
- Batch: `42c60d3c-ca98-47ac-8a9f-273fad87f44f`

## Execution Result

| Trial | learning_rate | epochs | seed | Attempts | Accuracy | Validation |
|---|---:|---:|---:|---:|---:|---|
| 1 | 0.0005 | 100 | 7 | 1 | 0.650 | invalid |
| 2 | 0.005 | 100 | 7 | 1 | 0.895 | invalid |
| 3 | 0.0005 | 1000 | 7 | 1 | 0.895 | invalid |
| 4 | 0.005 | 1000 | 7 | 1 | 0.941 | valid |

All four physical executions:

- reached terminal state;
- exited normally with exit code 0;
- used exactly Attempt #1;
- required no retry;
- produced no duplicate physical launch.

## Result Plane

- Artifacts: 8
- Metric observations: 4
- Validation reports: 4
- Valid result selections: 1
- Batch summary revisions: 1
- Summary status: `complete_with_invalid_results`

Selected result:

- Trial ordinal: 4
- Attempt: `386fbada-3602-4b7c-9722-32d964b08904`
- Validation report: `40d3cb13-c8de-4f9c-ad02-4fc0ec5414af`
- Validation accuracy: `0.941`
- Target threshold: `>= 0.92`

## Demonstrated Invariant

This Golden Path demonstrates the bounded competition claim:

> LabPilot can turn an AI-generated experiment proposal into a verified,
> human-approved, immutable experiment version, execute its Trials through
> the persistent execution runtime, and deterministically validate and select
> the resulting evidence without duplicate physical execution in this run.

This evidence does **not** claim general exactly-once execution or full
production acceptance.
