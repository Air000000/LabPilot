# Slice 2 MVP Smoke Evidence — 2026-08-31

## Scope

This is a Slice 2 MVP validation, not production acceptance. It used the isolated Compose project `labpilot-s2-mvp`, the existing Candidate-B Runner, and the branch `feat/slice2-mvp-closeout`.

## Results

- M1 stack: PASS — API ready, PostgreSQL healthy, resident Coordinator running, Runner `candidate-b-v2` ready.
- M2 real execution: PASS — native F01 workload executed through Runner and reached terminal with one physical start.
- M3 recovery/no duplicate: PASS — Coordinator restart reconciled the same Attempt without another physical launch.
- M4 public cancel: PASS — F17 batch `02a7e05f-ebee-4ee0-9a10-0bac9e806710` was started and cancelled through the public API.

### M4 evidence

The resident Coordinator was the only process calling `CoordinatorService.run_scan()`.

The F17 primary Attempt was `a82826bd-ad5f-44c7-9337-cb4ad1fa0c85`.

- X: `labpilot-v1-240a0a8780f1eb25cb4a617081588455fccbf57774041df3bbc64f2b73d41efc-a82826bdad5f44c79337cb4ad1fa0c85`
- H: `0ad8ba6117b55eceb9a4bc39b5dede46f03723c0597f43d63640f998c24e5aea`
- Before cancellation, its exact systemd unit was active/running with MainPID `69124`; immutable `README.md` and `train.py` existed.
- Public start returned `started`; public cancel returned `cancel_requested` at `2026-08-31 06:49:24.168208+00`.
- Four running Attempts reached `terminal/process_signaled/SIGTERM`; two prelaunch Attempts reached `cancelled_pre_launch`.
- All four launched exact-X units became inactive/dead with MainPID `0` and no ControlGroup. No matching process remained.
- Each F17 Trial had exactly one Attempt. The primary workload oracle retained `start_count: 1`.

## Earlier temporary blocker

The earlier F10/F13 `SOURCE_TAMPERED` failures were caused by the MVP helper invoking a second Coordinator scan while the resident Coordinator was also preparing the same Attempt. The concurrent preparations shared `<workspace>.staging`; staging cleanup removed `source/README.md`. The final F17 run used only the resident Coordinator and source integrity succeeded.

## Deferred hardening

- **MULTI_COORDINATOR_PREPARATION_RACE:** Attempt preparation needs exclusive claim/serialization for multi-Coordinator deployments. The one-Coordinator MVP topology avoids this race.
- **FORMAL_PRODUCTION_ACCEPTANCE:** N1-N7 recertification, V2-A matrix, F01-F22 formal campaign, and F18 reboot remain deferred.

Slice 2 MVP validated.