# LabPilot V1 Slice 2 — Production Acceptance Evidence

**Status:** Task 5 harness implementation in progress; native evidence is not complete.

This document is intentionally incomplete until bounded native F01–F22 evidence,
the C1–C17 scan, the fresh-database gate, and exact-HEAD remote CI are complete.
It does not mark Task 5, G4, or Slice 2 accepted. `DOCUMENT-STATUS.md` remains
unchanged.

## Historical N4 remediation evidence semantics

The historical N4 remediation evidence whose SHA-256 is
`c99f01fcd6207304a90d03129d1c09b32ddf5ba3e3a282d50d64b945760f69b7`
predates the exact scenario verdict schema. Its truthful failure state is
represented by `n4.status="STOPPED"`; it contains neither a top-level `verdict`
nor a top-level `passing_verdict`. The historical file remains byte-for-byte
immutable and is not retroactively migrated. Every F01-F22 `verdict.json`
created after the verdict-contract amendment uses schema v1.
