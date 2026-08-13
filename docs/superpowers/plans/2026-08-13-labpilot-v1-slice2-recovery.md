# LabPilot V1 Slice 2 — Trial → Real Training → Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute this plan task-by-task. Do **not** dispatch implementation subagents for this repository. Stop at the review/authorization gates defined below.

**Planning/status baseline:** `3a02feeb3342b99269fd47f4e0514088060e9877`

**Accepted Slice 1 implementation baseline:** `ff7909503c3b755bffb564423de3a56ce5bb9b4a`

**R0 authority root:** `032dd87d3366dc6edd66fb75451d44047e49f7dd`

**S2.0 design spec:** `docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-design.md`

**Status:** Draft for focused plan review. In this revision, only **Task 1 / S2.0** is execution-ready. Approval of this revision authorizes Task 1 only. Tasks 2–5 remain named, bounded future task slots and require an ADR-guided amendment of this same plan before implementation.

**Goal:** Prove a real native-Linux supervision contract first, then use that evidence to build one controlled path from an approved `ExperimentVersion` through exact source materialization, real training, restart reconciliation, and no duplicate physical launch.

**Architecture:** Slice 2 is stage-gated at the physical-execution boundary. Task 1 is an evidence-only native-Linux/systemd PoC that must select one supervision topology through a short ADR before production Batch/Trial/Attempt execution semantics are frozen. Production implementation follows only after the ADR, with PostgreSQL as durable execution ledger, the selected OS supervisor as physical observation authority, and Coordinator reconciliation as the only owner that combines durable expectation with physical evidence.

## Refinement record

This revision incorporates focused review corrections:

- Task 1 remains one formal Codex task; internal batches are execution checkpoints, not governance tasks.
- Task 1 implementation batches are separated as: deterministic contracts, evidence/scenario infrastructure, and native-host evidence/ADR. They do not create additional commits or review cycles unless a real scope boundary is discovered.
- S2.0 design Section 2/5 refinements are authoritative: H covers all caller-controlled behavior-changing execution semantics; creation authority is mechanically enforced; A1 is a baseline experiment rather than final selectable topology; P3/P6/P7/P10 recoverability requirements remain hard gates.
- The plan does not authorize production implementation for Tasks 2–5.

## 1. Stage-gated plan policy and final Slice 2 task granularity

The final Slice 2 delivery shape remains five formal tasks:

```text
Task 1 — S2.0 Runner Supervision PoC + ADR
Task 2 — S2.1 Attempt Preparation + Exact Source Materialization
Task 3 — S2.2 Durable Launch / Observe / Stop
Task 4 — S2.3 Coordinator Reconciliation + S2.4 API Composition
Task 5 — S2.5 Real Recovery / Fault-Injection Acceptance
```

This revision fully specifies Task 1 only. Task 2–5 remain bounded future slots.

After Task 1 is accepted, amend this same plan with exact Task 2–5 files/interfaces/tests while preserving the five-task outer shape.

## 2. Task 1: S2.0 Runner Supervision PoC + ADR

Task 1 scope remains unchanged:

```text
PoC harness
bounded evidence report
short ADR

No production runtime code.
No migrations.
No Batch/Trial/Attempt production schema.
```

## 3. Task 1 execution checkpoints

Task 1 execution is grouped into three internal batches:

### Batch A — Deterministic contract foundation

Covers:

- X projection
- versioned H canonicalization
- durable PoC intent state
- local request validation
- deterministic unit tests

Must prove:

```text
same X + same H
same semantics after restart
no lossy identity projection
no secret values in H
```

### Batch B — Evidence and scenario infrastructure

Covers:

- probe process
- bounded evidence writer
- narrow systemd adapter
- scenario orchestration

Must preserve:

```text
NOT_FOUND != NEVER_ACCEPTED_PROVEN
same X + different H = conflict
stop/kill requires matching X/H
transport failure != physical outcome
```

The adapter must not become a Runner framework. No backend registry, plugin hierarchy, scheduler, or generic execution abstraction.

### Batch C — Native Linux evidence and ADR

Covers:

- P0–P13 execution
- evidence summary
- ADR disposition

No implementation of A3 witness or Candidate B daemon is authorized without plan amendment.

## 4. Additional Task 1 contract gates

### Frozen execution semantics

Any caller-controlled value that can change target process behavior or caller-selectable supervisor semantics must be inside `FrozenLaunchSpec` and covered by H.

The PoC must not accept behavior-changing launch parameters outside H.

Fixed invariants may remain outside H only when they cannot vary per request, for example:

```text
Type=exec
Restart=no
fixed evidence policy
```

### Physical creation gate

A creation-capable path must mechanically require positive creation evidence:

```text
FIRST_DISPATCH_PROVEN
or
NEVER_ACCEPTED_PROVEN
```

A low-level launch function that can create solely because it was called is out of contract.

### A1/A2/A3 selection

A1 is required as a baseline experiment but is not a final ADR disposition under the frozen P7 terminal-evidence requirement.

Final selectable topology dispositions are:

```text
SELECT_A2
SELECT_A3
SELECT_B
BLOCKED
```

### P11 reboot semantics

P11 is a scenario-level reboot safety test, not a new physical observation kind.

Allowed outcome:

```text
no automatic same-Attempt activation
no extra independent root
no mistaken new activation as old execution
PhysicalObservation may remain UNKNOWN when exact terminal evidence cannot be proven
```

## 5. Verification and review gate

Before Task 1 push:

- verify deterministic tests;
- verify native Linux evidence separately from WSL;
- verify P3/P6/P7/P10 are not UNKNOWN;
- verify no production files changed;
- complete focused S2.0 review.

The next authorized action after this plan receives approval is only:

```text
Execute Task 1 — S2.0 Runner Supervision PoC + ADR.

Do not start Task 2.
Do not modify production runtime/schema.
```
