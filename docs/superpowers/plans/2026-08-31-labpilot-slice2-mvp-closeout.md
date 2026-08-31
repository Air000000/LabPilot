# Slice 2 MVP Closeout Implementation Plan

**Goal:** Demonstrate a native Slice 2 MVP stack, real execution, coordinator recovery without duplicate launch, and cancellation.

**Architecture:** Use an isolated worktree and Compose project `labpilot-s2-mvp` with the existing Candidate-B Runner. Reuse product services and existing native helpers; fix only bugs blocking MVP behavior.

**Tech Stack:** Python, FastAPI, PostgreSQL, Docker Compose, systemd, pytest, Ruff.

## Completed

- [x] Task 1 — bring up native MVP stack
- [x] Task 2 — real happy-path execution smoke
- [x] Task 3 — Coordinator restart/no-duplicate smoke
- [x] Task 4 — public-product cancel smoke
- [x] Task 5 — MVP evidence report

## Deferred

- Multi-Coordinator Attempt preparation needs an exclusive claim/serialization lease. Concurrent scans share `<workspace>.staging`; the MVP topology uses one resident Coordinator.
- Full N1-N7 recertification
- V2-A1-V2-A20
- F01-F22 formal campaign
- F18 reboot campaign
- Exhaustive evidence sealing
- Historical command provenance reconstruction
- Admin-directory governance cleanup
- .env.example identity cleanup unless it blocks MVP execution