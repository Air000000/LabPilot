# LabPilot Phase 0 Acceptance

- Tested commit: `d36a1427d0d795649fccc647a20efc0fc3ec65d7`
- Tested tree: `cf74f0219a7e55d2f96ff82346c5cb478a0bb094`
- Verified at: `2026-07-27T15:20:48Z`
- WP0 plan SHA-256: `34fe5e7ea95451aeb360b77dc0d68338058f4296f39e3c5f5085de4a034a4b1b`
- Dependency manifest SHA-256: `1ad14a2287971ce0069618eca94286e58839b66220ac8d184234f7b3e3b32c6f`
- Acceptance script SHA-256: `141d59da4ee55e6dd1a3bd614b11ba27d4720b9b312938a40704047512ea7353`
- Command identity: `LABPILOT_TEST_ADMIN_DATABASE_URL=<redacted> ./scripts/check_foundation.sh`
- Compose project: `labpilot_wp0_acceptance_14796`
- API port: `48181`
- Architecture dependencies: PASS
- Canonical JSON V1 unit tests: PASS
- Strict UnitOfWork lifecycle: PASS
- Empty-database Alembic migration: PASS
- RuntimeEvent/Outbox atomicity and concurrent sequence: PASS
- Module dependency allowlist: PASS
- PostgreSQL SKIP LOCKED capability: PASS
- API liveness/readiness: PASS
- API, Coordinator shell, Projection shell topology: PASS
- Coordinator/Projection SIGTERM exit code 0 within 10 seconds: PASS
- Worker Database.dispose() unit tests: PASS

This record does not claim Durable Coordinator Job processing, projection delivery,
Runner execution, Agent Runtime, Tool execution, or Result Plane capabilities.
