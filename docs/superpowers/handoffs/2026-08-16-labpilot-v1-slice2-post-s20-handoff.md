# LabPilot V1 Slice 2 — Post-S2.0 Handoff

**Handoff point:** S2.0 native supervision gate complete; production Tasks 2–5 not yet authorized
**Frozen Task 1 baseline:** `4109afa9f871ae45cf32c301040bcff6f109939d`
**Frozen disposition:** `Candidate A = REJECT_A`, `Candidate B = SELECT_B`

This document is the entry point for a new Slice 2 implementation window. Do not reconstruct the completed S2.0 decision from scratch.

## 1. Read order

Read these files in order before proposing or editing production code:

1. `docs/superpowers/handoffs/2026-08-16-labpilot-v1-slice2-post-s20-handoff.md`
2. `docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-adr.md`
3. `docs/superpowers/evidence/2026-08-13-labpilot-v1-slice2-runner-supervision-poc.md`
4. `docs/superpowers/plans/2026-08-13-labpilot-v1-slice2-recovery.md`
5. `docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-design.md`
6. `docs/labpilot/01-v1-scope-and-acceptance-v0.3.md`
7. `docs/labpilot/architecture/10-v1-core-architecture-rebaseline-v0.3.md`
8. `docs/labpilot/implementation/01-master-implementation-roadmap-v0.3.md`

Then inspect the implementation/evidence code that produced the decision:

```text
tests/poc/s2_runner/contract.py
tests/poc/s2_runner/durable_state.py
tests/poc/s2_runner/runner_protocol.py
tests/poc/s2_runner/runner_daemon.py
tests/poc/s2_runner/systemd_cli.py
tests/poc/s2_runner/scenario_runner.py
tests/poc/s2_runner/host/README.md
tests/unit/s2_runner_poc/**
```

Use Git history to verify evolution, not to replace the ADR/evidence report with a fresh redesign.

## 2. Authority and status

Normative precedence for remaining V1 work remains:

```text
V1 Scope v0.3
> Architecture 10 v0.3
> Master Roadmap v0.3
> approved Slice 2 detailed plan / accepted subordinate ADR
```

S2.0 is now an accepted subordinate supervision decision inside Slice 2.

Important status distinction:

```text
S2.0 Runner Supervision PoC + ADR
= complete / accepted

Slice 2 overall
= not accepted

Tasks 2–5 production implementation
= not yet authorized
```

The current detailed plan intentionally specified Task 1 at execution precision and reserved Tasks 2–5 as bounded future slots. The next step is a plan amendment, not production code.

## 3. Why `DOCUMENT-STATUS.md` is not the S2.0 handoff source

The approved Slice 2 plan explicitly says `DOCUMENT-STATUS.md` is updated once at full Slice 2 acceptance, not after S2.0. Therefore its Slice 2 execution-state wording may lag this interim Task 1 checkpoint by design.

Do not “repair” that file during the S2.0 handoff. Use this handoff + ADR + evidence report as the current Task 1 status until full Slice 2 acceptance.

## 4. Frozen S2.0 conclusions — do not reopen without contradictory evidence

The following conclusions are frozen for downstream Slice 2 planning:

```text
Candidate A = REJECT_A
Candidate B = SELECT_B
physical evidence stack = systemd + persistent journald
```

Selected topology:

```text
non-root controller
→ narrow AF_UNIX Runner protocol
→ root-owned Runner
→ fixed Runner-derived systemd profile
→ non-root labpilot-train execution
```

Do not revisit Candidate A merely because a different polkit expression looks convenient. Native P12 proved the relevant creation-capable systemd surface too expressive for the required controller privilege boundary.

## 5. Frozen downstream supervision semantics

Production Tasks 2–5 must preserve these semantic rules:

```text
Attempt.id → stable supervisor execution identity X
Frozen execution semantics → canonical versioned hash H

same X + same H → reconcile
same X + different H → fail closed
NOT_FOUND != NEVER_ACCEPTED_PROVEN
possible prior acceptance → no blind create
CONTROL_UNKNOWN → observe X/H, never blind retry
unknown physical state → no launch
stop/kill requires exact ACTIVE_MATCH X/H
PID/InvocationID → observations only
physical observation does not directly own Attempt state
```

The Runner remains a privileged adapter. It must not acquire domain launch authorization, scheduling, workflow state, a durable Attempt ledger, or independent retry policy.

## 6. Native evidence is frozen; do not rerun it as onboarding

Do not rerun P0–P13 merely to become familiar with the system.

The final native archive is:

```text
labpilot-s2-final5-COMPLETE-20260816T160825Z.tar.gz
SHA-256:
cf6537da2eac2857175d5ccbae6ab07a7420c325c66e8154bef3897a72c90990
```

The final code bundle is:

```text
labpilot-s2-candidate-b-4109afa.bundle
SHA-256:
cb63d84b2c3604b32fdf18905302a762aa5e5761a50fef44e4cd7814f2065186
```

A later native campaign is justified only if an approved production change materially changes a physical invariant that the current evidence no longer covers.

Do not clear the preserved journal/evidence archives as part of normal continuation.

Do not use the physical experiment server for this work. The authoritative S2.0 evidence came from the dedicated VMware Ubuntu VM.

## 7. Audit facts that must not be erased

Three classes of harness defect were discovered and corrected during native execution:

```text
P2 / P3-B-ACK-3 / P4
→ start-marker sampling race

P9
→ stop-induced non-cooperative termination lacked terminal journal reconstruction

P11 first verify
→ verifier incorrectly required direct non-root controller journal access
```

The first P11 `FAIL` is intentionally retained beside the corrected `PASS`. The corrected verification used the same physical reboot, same X/H, same marker count, and issued no new LAUNCH.

Do not rewrite history to make the campaign look cleaner.

## 8. Governance gap that must remain visible

There is one repository-history inconsistency to preserve rather than silently normalize: the r2 plan says Candidate B implementation required a focused plan amendment, but the final Git history does not contain a separate committed pre-implementation Candidate-B plan-amendment document before commits `0365f79` / `6040630`.

The current ADR/evidence package records the accepted post-execution decision; it does not pretend that a missing historical approval document existed. If project governance wants an explicit ratification record, add it prospectively and label it retrospective. Do not backdate it.

For Tasks 2–5, this gap must not recur: the execution-precision amendment is a hard pre-implementation gate.

## 9. Next authorized work

Amend the existing plan:

`docs/superpowers/plans/2026-08-13-labpilot-v1-slice2-recovery.md`

The amendment must keep the five-task outer structure:

```text
Task 1 — S2.0 Runner Supervision PoC + ADR                 COMPLETE
Task 2 — S2.1 Attempt Preparation + Exact Source Materialization
Task 3 — S2.2 Durable Launch / Observe / Stop
Task 4 — S2.3 Coordinator Reconciliation + S2.4 API Composition
Task 5 — S2.5 Real Recovery / Fault-Injection Acceptance
```

The amendment must bind its planning baseline to the accepted Task 1 state and ADR, then specify Tasks 2–5 at execution precision: exact files, ownership boundaries, interfaces, migrations if genuinely required, tests, acceptance commands, and review/stop gates.

Do not implement Tasks 2–5 until that amendment is reviewed and approved.

## 10. Required Task 2 boundary

Task 2 remains preparation-only. Its contract is:

```text
approved immutable ExperimentVersion
→ clean source identity
→ exact materialized Attempt source
→ verified source manifest
→ Trial identity
→ non-launchable Attempt preparation
→ versioned frozen execution spec
→ launch-ready boundary
```

No physical training process is launched in Task 2.

Roadmap semantics to preserve:

```text
Trial identity = hash(experiment_version_identity, resolved_parameters, seed)
Infrastructure retry → new Attempt
semantic change → new Trial / new Version
```

The plan amendment should inspect the accepted Slice 1 domain/repository implementation before naming exact production files. Do not invent an abstraction layer from the PoC code.

## 11. Required Task 3 boundary

Task 3 is the first production external-side-effect boundary:

```text
committed durable launch intent
→ positive creation authority
→ selected narrow Runner boundary
→ launch / observe / stop
→ terminal physical observation
```

PostgreSQL is the durable execution ledger. The Runner is not.

The production API must make positive creation evidence mechanically unavoidable and must preserve acknowledgement-loss semantics proven by S2.0.

## 12. Required Task 4 boundary

Task 4 owns deterministic reconciliation:

```text
DB expected state
+
physical observation
→ Coordinator reconciliation
→ authoritative Attempt transition
```

It must reattach/reconcile exact existing execution, fail closed on identity uncertainty, never relaunch because RAM state was lost, and create a new Attempt for a true infrastructure retry.

Locking/lease/fencing is introduced only if a real ownership race proves it necessary.

## 13. Required Task 5 boundary

Task 5 is real recovery/fault-injection acceptance for the production path, not another generic supervision framework.

At minimum it must cover the Roadmap recovery chain:

```text
start real training
kill API
training remains when expected
restart API
kill Coordinator
training remains
restart Coordinator
reconcile original execution
physical training process count = 1
```

Additional tests should be derived from the accepted Task 2–4 interfaces and the S2.0 ADR, not from speculative future platform requirements.

## 14. Explicit non-goals for the next window

Do not introduce during Slice 2 continuation unless a new approved requirement proves necessity:

```text
generic Runner backend registry
plugin framework
remote/SSH runner
multi-host scheduler
worker pool/resource allocator
generic workflow engine
Runner database/receipt ledger
Docker/systemd privileged socket exposure
generic shell/exec control protocol
production dependency on tests/poc/s2_runner/**
```

PoC code is evidence infrastructure. Production reuse must be explicit and justified in the plan amendment.

## 15. How to use GitHub/Git history

The next window should read the repository branch containing this handoff commit. It should not rely only on a pasted summary.

Git is used for three purposes:

1. verify the exact accepted S2.0 baseline and subsequent documentation-only handoff commit;
2. inspect the accepted Slice 1 implementation before drafting Task 2–5 file/interface changes;
3. review S2.0 commit history when a specific implementation rationale is unclear.

Useful S2.0 history:

```text
29fce4c  initial Slice 2 runner supervision PoC
0365f79  admit Candidate B
6040630  add narrow Candidate B Runner
34aeb50  privilege and acknowledgement-loss safety
63f7949  Candidate B journal capability gate fix
a6b2478  security evidence shape fix
bd5bde7  start-marker wait
51e3a50  ACK marker wait
8cf87a6  stabilize remaining start-marker evidence
2fa81fb  recover stopped terminal evidence
4109afa  fix reboot journal gate
```

Use those commits as an audit trail. Do not treat each historical harness defect as a reason to redesign the selected topology.

## 16. Conflict rule for the next window

If the handoff, ADR, evidence report, current code, and Git history appear inconsistent:

```text
STOP
identify the exact conflicting file/commit/field
report the conflict with evidence
```

Do not silently reconcile the conflict by changing architecture or rerunning native experiments.

If no contradiction exists, the first deliverable of the next window is the execution-precision Tasks 2–5 plan amendment for review — not production code.
