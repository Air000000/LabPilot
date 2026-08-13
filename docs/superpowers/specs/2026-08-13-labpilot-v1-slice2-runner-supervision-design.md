# LabPilot V1 Slice 2 — S2.0 Runner Supervision PoC + ADR Design

```text
Status: Approved Design Spec
Date: 2026-08-13
Design scope: S2.0 Runner Supervision PoC + ADR only
Design baseline before this spec: ecf9d7e86803834363b8f4d6eabb84c4cf9fb54b
Accepted Slice 1 implementation: ff7909503c3b755bffb564423de3a56ce5bb9b4a
R0 authority root: 032dd87d3366dc6edd66fb75451d44047e49f7dd
```

## 0. Purpose and authority

S2.0 exists to remove the highest-risk uncertainty in Slice 2 before production `Batch` / `Trial` / `Attempt` execution semantics are frozen: whether LabPilot can bind durable control intent to one real Linux execution, survive API/Coordinator loss, rediscover the original execution, and avoid duplicate launch across acknowledgement-loss windows.

Authority remains:

```text
V1 Scope v0.3
> Architecture 10 v0.3
> Master Roadmap v0.3
> approved Slice 2 detailed implementation plan / ADR once approved
```

This spec does not authorize Slice 2 implementation. It defines the S2.0 design that the Slice 2 Detailed Implementation Plan must preserve.

The selected development/acceptance environment is:

```text
WSL2
→ ordinary development and deterministic tests
→ NOT authoritative for host reboot semantics

Native Linux + systemd
→ authoritative Runner supervision PoC
→ authoritative controller-death / rediscovery / host-reboot evidence
```

S2.0 is evidence-first. It must not become a generic Runner platform, scheduler, multi-host abstraction, or production Attempt schema exercise.

---

# 1. Supervisor topology and authority boundary

## 1.1 Candidate topology A — preferred

```text
Native Linux host
├── LabPilot API
├── Coordinator
├── narrow Runner/supervisor boundary
├── systemd
│   └── authoritative execution units
└── PostgreSQL
    └── may remain containerized
```

Candidate A is evaluated first. If it satisfies the complete S2.0 recovery and permission contract, experimentation stops and the ADR selects it.

For the native-Linux PoC, Candidate A uses the **system manager** boundary unless a later ADR explicitly proves a different manager lifecycle. Codex/implementation must not silently switch to `systemd --user` merely to bypass permissions. The PoC launch profile must make these properties explicit:

```text
service type = exec semantics
systemd environment expansion disabled
Restart = no
stdout = journal
stderr = journal
whole execution cgroup/process tree controlled as one unit
bounded stop behavior
explicit intended training execution Unix user
```

The control-plane Unix identity and the training execution Unix identity are distinct concerns. If the intended control-plane identity cannot exercise a sufficiently narrow system-manager permission boundary, that is P12/topology evidence. Running the PoC as root or switching to a user manager does not turn a permission failure into PASS.

## 1.2 Candidate topology B — fallback

```text
Native Linux host
├── systemd
│   ├── narrow LabPilot Runner daemon
│   └── authoritative execution units
└── containers
    ├── API
    ├── Coordinator
    └── PostgreSQL
```

Candidate B is evaluated only if Candidate A is fundamentally unsuitable because of host-control privilege, control-boundary, launch-serialization, or equivalent topology constraints. The containerized control plane must never receive the Docker socket, generic root shell access, unconstrained systemd control, or another broad privileged host surface.

The Runner daemon in B is a narrow host control authority. It does not own Experiment, Trial, Attempt, retry, or scheduling semantics.

## 1.3 Four authority layers

```text
PostgreSQL
= durable execution ledger
= what LabPilot expects / authorizes

systemd / selected supervisor
= physical observation authority
= what the host physically accepted / runs / observed

Runner boundary
= narrow physical command + observation mechanism
= no domain-state ownership

Coordinator
= reconciliation owner
= DB expected state + physical evidence → authoritative domain transition
```

The following are explicitly invalid authorities:

```text
Coordinator RAM
PID alone
systemd InvocationID alone
current process list
supervisor auto-restart policy
```

## 1.4 Controller and training lifetimes

After the supervisor accepts a valid launch, API or Coordinator death must not kill the training execution merely because the controller disappeared. A fresh controller must rediscover and reconcile the original execution.

Host reboot is a different failure class. The original process does not have to survive host reboot. The same Attempt must not be silently resurrected or blindly relaunched after reboot. Reconciliation may classify retained evidence as terminal or, only where explicitly permitted by the ADR, `UNKNOWN / needs_attention`. A genuine retry creates a new Attempt; the host supervisor must not implement experiment retry through `Restart=always` or equivalent implicit resurrection.

## 1.5 Physical duplicate invariant

For one Attempt physical launch:

```text
active authoritative execution-unit count <= 1

AND

there must not be a second independently launched target process tree
for the same SupervisorExecutionIdentity.
```

Child processes, wrappers, dataloader workers, and similar descendants do not count as duplicate training merely because multiple OS PIDs exist.

---

# 2. Execution identity and acknowledgement-loss contract

## 2.1 Stable execution identity

The preferred identity model is:

```text
Attempt.id
→ deterministic projection
→ SupervisorExecutionIdentity X
```

No independent production `launch_generation` or `execution_id` is justified by S2.0. One Attempt has one authoritative supervisor execution identity. A true execution retry creates a new Attempt and therefore a new X.

The projection must be:

```text
- deterministic
- stable across process/service restart
- collision-safe in the selected supervisor namespace
- independent of PID, timestamp, RAM state, and InvocationID
- free of lossy truncation
```

If Attempt IDs alone do not isolate multiple LabPilot installations on the same host, the projection may include a stable installation/deployment namespace. This is supervisor namespace construction, not a new domain execution identity.

PID, process start time, and systemd InvocationID are observation facts only.

## 2.2 Versioned execution-spec identity

A launch is semantically identified by both X and an execution-spec hash H:

```text
H = SHA-256(
      canonical_execution_spec(
        execution_spec_schema_version,
        semantic FrozenLaunchSpec fields
      )
    )
```

The canonicalization version is explicit. The same semantic `FrozenLaunchSpec` under the same schema/canonicalization version must reproduce the same H after controller restart.

Every caller-controlled input that can change target process behavior or caller-selectable supervisor execution semantics must be inside the frozen spec covered by H, or be a fixed invariant that the caller cannot vary. The PoC must not accept behavior-changing launch arguments outside H. For the probe this includes, as applicable, runtime/argv/workdir, non-secret environment, start-marker/evidence path, sleep/exit behavior, child-process behavior, and caller-selectable collection mode. Fixed PoC invariants such as `Type=exec` and `Restart=no` need not be duplicated in H if they cannot vary per request.

Secret values are excluded from X, H, ordinary supervisor metadata, and normal logs. A non-secret credential slot/reference may remain part of execution semantics when it affects the frozen launch contract.

For any launch/control request, the supplied or asserted H must equal the versioned canonical hash of the supplied `FrozenLaunchSpec`. A local mismatch is a validation failure and causes zero supervisor interaction.

## 2.3 Core acknowledgement-loss window

S2.0 must prove the real failure window:

```text
durable launch authorization committed
        ↓
controller dispatches launch(X, H)
        ↓
supervisor may accept and start execution X
        ↓
CONTROLLER DIES BEFORE durable launch-success acknowledgement
```

Recovery cannot infer non-execution from missing acknowledgement or missing RAM state. It must query physical evidence for X/H and reconcile the original execution.

The core rule is:

```text
Once supervisor acceptance may have occurred,
controller death cannot cause a second physical launch
unless non-acceptance is positively proven.
```

## 2.4 Absence is not proof of non-execution

`NOT_FOUND` alone never authorizes launch replay.

```text
ACTIVE + matching X/H
→ reconcile active execution
→ no create

TERMINAL + matching X/H
→ reconcile terminal execution
→ no create

existing/evidenced X + mismatching H
→ identity conflict
→ fail closed
→ no create

NOT_FOUND + positive proof acceptance was never crossed
→ the same launch request may be attempted

NOT_FOUND without such proof
→ UNKNOWN
→ fail closed / needs_attention
→ no create

observation error / contradictory evidence
→ UNKNOWN
→ fail closed
→ no create
```

The design therefore separates normal first creation from replay after uncertainty.

## 2.5 Positive creation authority

Physical creation requires positive creation authority from one of two sources:

```text
FIRST_DISPATCH_PROVEN
= durable domain/control evidence proves this is the first dispatch
  and the supervisor acceptance boundary has not yet been attempted

NEVER_ACCEPTED_PROVEN
= positive control evidence from a prior attempt proves the supervisor
  acceptance boundary was not crossed
```

`FIRST_DISPATCH_PROVEN` is a semantic requirement, not a production enum or column required by this spec.

Once dispatch may have occurred, first-dispatch proof is consumed/lost for safety purposes. Absence is then insufficient to authorize creation.

Any concrete API capable of creating a physical execution must either accept positive creation evidence explicitly or be reachable only through one orchestration gate that validates the evidence before dispatch. A low-level `launch()` path that can create solely because it was called is outside this contract.

## 2.6 Terminal evidence requirement

After supervisor acceptance, enough durable physical evidence must survive controller restart and supervisor unit GC to distinguish:

```text
never accepted
accepted and active
accepted and terminal
observation unknown
```

A `TERMINAL_MATCH` must bind both X and H. Evidence that only proves a unit named X once existed is insufficient if it cannot establish association with H.

Whether evidence must also survive host reboot is determined by the ADR according to the frozen host-reboot semantics. The normal controller-loss recovery scenarios listed later may not degrade to `UNKNOWN` merely because the selected evidence stack is weak.

Possible evidence stacks are evaluated progressively:

```text
A1: systemd current/transient unit observable state baseline
A2: systemd + explicitly verified persistent journald
A3: systemd + minimal bounded host-side physical witness
```

A1 remains a required baseline experiment, but **is not a selectable final disposition** under the frozen P7 requirement. P7 explicitly requires `TERMINAL_MATCH` after unit GC/unload; current/transient unit state alone cannot be the durable evidence source once that state has been collected. The first selectable Candidate-A result is therefore A2.

A host witness is an acceptance/terminal observation witness, never a second domain execution ledger.

## 2.7 A3 receipt ordering requirement

A3 is not admitted merely because it persists a receipt. Its crash ordering relative to supervisor acceptance must itself be proven, including failure:

```text
1. before supervisor acceptance
2. after supervisor acceptance but before witness persistence
3. after witness persistence
4. during terminal-observation persistence
```

If A3 creates another unresolved `supervisor accepted but receipt absent` window, it has not solved the problem. `UNKNOWN` remains fail-closed.

A3 may add only a minimal physical witness. If satisfying A3 requires a long-lived host service that owns launch serialization, accepts controller commands, arbitrates supervisor operations, or is required for launch/query/stop, the design has crossed into Candidate B and must be evaluated as B.

## 2.8 S2.0 identity invariants

```text
E1. Attempt identity precedes physical launch.
E2. One Attempt maps to one stable authoritative supervisor execution identity.
E3. Replaying the same launch request derives the same X.
E4. A true execution retry creates a new Attempt.
E5. PID/start time/InvocationID are observations, not durable LabPilot identity.
E6. Possible supervisor acceptance followed by controller death cannot cause
    a second launch unless non-acceptance is positively proven.
E7. Existing X + matching H → reconcile existing execution.
E8. Existing X + mismatching H → identity conflict → fail closed.
E9. NOT_FOUND or ambiguous observation alone never authorizes launch.
E10. Acceptance leaves enough evidence across controller restart and unit GC
     to distinguish never-accepted / active / terminal / unknown.
E11. Secret values are excluded from execution identity, H, and ordinary
     supervisor metadata.
E12. S2.0 proves the supervision contract without freezing production
     Attempt schema.
```

---

# 3. Real native-Linux PoC matrix

## 3.1 PoC roles and durability

The PoC contains only evidence infrastructure:

```text
PoC durable intent state
= test representation of what LabPilot expects

Controller harness
= launch / observe / stop client that can be intentionally killed

Probe execution unit
= real supervised host process tree
```

The PoC durable intent state must have durability appropriate to each injected failure. Controller-death scenarios require state that survives controller process death. Host-reboot scenarios require state that survives host reboot. A recovery test is invalid if its own supposedly durable pre-failure state was volatile or not committed.

The probe must support long-running mode, very-short exit, configurable exit code, stdout/stderr, visible non-secret argv/env, optional child processes, and a test-only independent-root start marker. The marker exists only to prove duplicate creation and is not a production receipt design.

## 3.2 Physical evidence classifications

`observe(X, H)` returns a physical observation from this closed set:

```text
ACTIVE_MATCH
TERMINAL_MATCH
IDENTITY_CONFLICT
UNKNOWN
```

`NEVER_ACCEPTED_PROVEN` is not a physical observation. It is separate positive non-acceptance control evidence.

Semantics:

```text
ACTIVE_MATCH
= authoritative execution X is active and physical evidence binds it to H

TERMINAL_MATCH
= supervisor accepted X, evidence binds it to H, and terminal result is known

IDENTITY_CONFLICT
= X exists/is evidenced but its semantic execution identity does not match H

UNKNOWN
= available evidence cannot safely prove one of the above
```

Raw `NOT_FOUND` maps to `UNKNOWN` unless separate positive non-acceptance evidence exists.

## 3.3 Mandatory scenarios

The PoC must execute and retain bounded evidence for the following scenarios.

| ID | Scenario | Required result |
|---|---|---|
| P0 | Record native Linux, kernel, systemd, boot identity, journald effective storage, users/permissions | Reproducible host characterization |
| P1 | Recompute X across fresh controller processes/restarts and namespace boundaries | Stable, collision-safe, no lossy truncation |
| P2 | Normal long-running launch and fresh-controller query | `ACTIVE_MATCH`; exact argv/workdir/non-secret env; logs/status observable |
| P3 | Supervisor accepts launch; controller killed before durable success ack | `ACTIVE_MATCH` or `TERMINAL_MATCH`; independent target-root start count remains 1 |
| P4 | Concurrent same-X/same-H launch attempts | At most one authoritative execution and one independently launched target root |
| P5 | Same X, different H | `IDENTITY_CONFLICT`; no second launch; original execution not destroyed |
| P6 | Very-short process exits before recovery after uncertain acknowledgement | `TERMINAL_MATCH`, including X/H binding and terminal result |
| P7 | Terminal execution after supervisor unit GC/unload | `TERMINAL_MATCH`; distinguish ran-and-exited from never accepted |
| P8 | Deterministic failure before any supervisor dispatch | `NEVER_ACCEPTED_PROVEN`; supervisor dispatch count 0; contrast with ambiguous NOT_FOUND→UNKNOWN |
| P9 | stop-before-exit, exit-before-stop, near-concurrent stop/exit | One converged physical result; no duplicate; no fabricated causal certainty |
| P10 | Controller down while process exits, then fresh controller | `TERMINAL_MATCH`; recovered exit evidence; no relaunch |
| P11 | Native Linux host reboot during active execution | Scenario-level proof: boot changed, no automatic same-X activation, no extra independent root, no new activation mistaken for old execution; `PhysicalObservation` may be an explicitly allowed `UNKNOWN` |
| P12 | Host permission boundary | No Docker socket, generic root shell, or unconstrained systemd control; least narrow privilege demonstrated |
| P13 | Representative execution environment propagation | Exact argv/workdir/non-secret env/runtime path/stdout/stderr/exit result as applicable |

P3, P6, P7, and P10 are mandatory normal recovery scenarios. A topology cannot be selected if any of them resolves only to `UNKNOWN`.

P11 is different: host reboot does not require the original process to survive. P11 is a scenario-level reboot safety result, not a fifth `PhysicalObservationKind`. After reboot, `PhysicalObservation` may remain `UNKNOWN / needs_attention` when retained evidence cannot prove exact terminal state, but the same Attempt still must not be automatically or blindly relaunched.

## 3.4 F5 duplicate-safety coverage

The S2.0 duplicate/ack-loss proof explicitly covers all three:

```text
- concurrent same-X / same-H replay
- accepted-before-ack replay after controller death
- same-X / different-H identity conflict
```

All must demonstrate that no unauthorized second authoritative execution is created.

## 3.5 stop / natural-exit race

The PoC requires one converged physical result, not fabricated causal certainty. It is valid to retain separately:

```text
stop was requested
terminal execution was observed
```

without claiming that stop caused the terminal event when ordering cannot be proven.

---

# 4. ADR mechanical decision gate and scope boundary

## 4.1 Allowed dispositions

The S2.0 ADR has exactly four final dispositions:

```text
SELECT_A2
SELECT_A3
SELECT_B
BLOCKED
```

A1 is a required baseline experiment, not a selectable final disposition, because P7 requires exact terminal X/H evidence after unit GC. There is no `PARTIAL_PASS`, `GOOD_ENOUGH`, `IMPLEMENT_AND_HARDEN_LATER`, or additional candidate disposition.

## 4.2 Candidate A escalation

Evaluation begins with A1 as a baseline experiment.

### A1 baseline — not selectable

A1 must still prove live launch identity, duplicate safety, controller independence, and the other facts for which current unit state is sufficient. Its expected failure at P7 is used to determine whether the only missing property is durable X/H terminal retention/correlation. A1 cannot be the final ADR disposition.

### A1 → A2

A1 may escalate to A2 only when live X/H launch identity, dedupe, permissions, and control semantics are sound, but durable physical evidence cannot preserve or recover the X+H binding across controller restart, terminal execution, or unit GC.

A2 is not allowed to repair bad execution identity, duplicate launch, unsafe privilege, or broken control semantics.

If persistent journald is proven unavailable or incompatible with the target deployment, record A2 as `not applicable` in candidate evaluation and proceed to A3. This is not a final disposition.

### A2 → SELECT_A2

A2 must satisfy the complete contract and explicitly document which journal evidence binds X, which evidence binds H, how terminal state is recovered, and which tested lifecycles retain that evidence.

### A2 → A3

Escalate to A3 only when the remaining problem is bounded physical acceptance/terminal witness retention or correlation. A3 may not repair unsafe host-control topology, duplicate-launch failure, or unstable identity.

### A3 → SELECT_A3

A3 must satisfy the complete contract plus the witness crash-ordering proof in Section 2.7. It remains Candidate A topology plus a minimal physical witness; if the witness grows into launch-control authority, evaluate Candidate B instead.

## 4.3 Candidate B admission and selection

Move from A to B when the A-family is fundamentally unsuitable because, for example:

```text
- A requires overbroad host privilege;
- API/Coordinator cannot safely exercise required supervisor control;
- same-X creation cannot be made safely idempotent;
- supervisor control/query cannot provide a stable X/H contract; or
- host-native controller lifecycle cannot be cleanly separated from execution.
```

Candidate B must prove the same semantic F1–F8 and R1–R4 contract; adding a daemon does not lower recovery requirements. A daemon acknowledgement-loss window must itself be tested.

`SELECT_B` records two independent ADR fields:

```text
Selected topology: B
Selected physical evidence stack:
  systemd
  OR systemd + persistent journald
  OR systemd + bounded host witness
```

Topology choice is not physical-evidence choice.

## 4.4 Mandatory recovery versus allowed UNKNOWN

A selected topology must deterministically recover:

```text
P3 → ACTIVE_MATCH or TERMINAL_MATCH
P6 → TERMINAL_MATCH
P7 → TERMINAL_MATCH
P10 → TERMINAL_MATCH
```

The ADR must separately enumerate a finite set of cases in which final `UNKNOWN / needs_attention` is allowed. A generic `other errors` category is forbidden. Candidate examples that may be admitted if evidence supports them include contradictory/corrupted physical evidence, supervisor state unavailable beyond verified retention guarantees, and the P11 host-reboot case when evidence is insufficient to prove exact terminal state but reboot safety invariants are still proven.

## 4.5 BLOCKED hard gate

If the A-family fails and B also fails:

```text
S2.0 = BLOCKED
S2.1 = unauthorized
```

Do not weaken F1–F8, add a generic Runner framework, or freeze production Batch/Trial/Attempt schema to work around a physical execution uncertainty. Failure evidence must trigger a focused architecture decision before any alternative topology is admitted.

## 4.6 PoC artifact boundary

S2.0 may produce only:

```text
1. reproducible PoC harness
2. bounded evidence report
3. short ADR
```

The PoC harness may contain controller/probe utilities, systemd interaction helpers, failure injection, and test-only durable state. It must not become a Runner SDK, backend registry, scheduler, remote-runner API, or generic supervision framework.

S2.0 PoC code is test/evidence infrastructure. S2.1/S2.2 production runtime must not depend on the PoC harness by default. Any implementation reuse must be explicitly admitted during later Slice 2 implementation design/review.

The evidence report records only bounded, secret-free material needed to reproduce the decision: host characterization, scenario ID, X, H, expected/observed classification, injected failure point, physical identity/fingerprint where useful, terminal evidence, duplicate-root count, permission result, and reboot result.

## 4.7 What S2.0 deliberately does not freeze

S2.0 does not freeze or implement:

```text
Batch production schema
Trial production schema
Attempt production schema or status enum
production migrations
Coordinator production reconciliation state machine
API routes
multi-host / SSH / remote execution
generic Runner interface hierarchy
scheduler / worker pool / resource allocator
```

Its output is one proven supervision contract that S2.1/S2.2 may depend on.

---

# 5. Minimal downstream supervision contract

## 5.1 Identity and execution semantics

Downstream code may depend semantically on:

```text
Attempt.id
→ stable SupervisorExecutionIdentity X

FrozenLaunchSpec
→ versioned canonical execution_spec_hash H
```

It must not depend on systemd command syntax, unit-object lifetime, PID, InvocationID, journal cursor, or PoC-specific receipt format as domain identity.

## 5.2 Physical observation versus positive creation evidence

`observe(X, H)` yields only:

```text
PhysicalObservation:
  ACTIVE_MATCH
  TERMINAL_MATCH
  IDENTITY_CONFLICT
  UNKNOWN
```

Creation authority is separate:

```text
PositiveCreationEvidence:
  FIRST_DISPATCH_PROVEN
  NEVER_ACCEPTED_PROVEN
```

This separation prevents an absent unit from being interpreted as proof that no prior launch occurred.

## 5.3 Conceptual launch contract

The conceptual launch request contains X plus a `FrozenLaunchSpec`; H is derived or asserted-and-verified from that spec. The exact Python signature is deliberately not frozen.

Launch preconditions are:

```text
- source already materialized and verified;
- execution inputs already frozen;
- durable domain launch intent/authority already committed;
- request X/H/spec is locally self-consistent;
- positive creation evidence is available.
```

The Runner/supervisor boundary does not decide whether a Trial or Attempt should be run. It only executes a physical command already authorized by durable domain state.

Same-X launch semantics are:

```text
ACTIVE_MATCH
→ reconcile existing execution; no create

TERMINAL_MATCH
→ reconcile terminal execution; no create

IDENTITY_CONFLICT
→ fail closed; no create

UNKNOWN
→ fail closed / needs_attention; no create

FIRST_DISPATCH_PROVEN or NEVER_ACCEPTED_PROVEN
→ physical creation may be attempted, subject to all other preconditions
```

An uncertain launch/control response never authorizes blind retry. Observe X/H first.

Any concrete creation-capable interface must make the positive-creation gate mechanically unavoidable: either carry the evidence explicitly or be private behind one orchestration function that validates it before the supervisor call.

## 5.4 Conceptual observe result

Physical observation may expose bounded facts sufficient to classify X/H, such as:

```text
classification
execution_identity X
expected_spec_hash H
observed_spec_hash when evidence permits
bounded physical state
optional PID
optional InvocationID
optional start/end timestamps
optional authoritative exit code/result
optional bounded log reference
bounded evidence-source descriptor
```

Not all fields are required to become production persistent fields. The ADR freezes only the evidence guarantees required for correct classification and reconciliation.

Physical observation never owns Attempt state transition:

```text
physical evidence
+ durable expected state
→ Coordinator reconciliation
→ authoritative Attempt transition
```

`systemd active` is not by itself `Attempt running`, and `systemd inactive` is not by itself `Attempt failed`.

## 5.5 stop/kill identity protection

`stop/kill(X, H)` is also an external physical side effect and must protect X/H identity:

```text
ACTIVE_MATCH
→ bounded stop/kill may be issued

TERMINAL_MATCH
→ no destructive action; reconcile terminal

IDENTITY_CONFLICT
→ fail closed; MUST NOT stop the observed execution

UNKNOWN
→ MUST NOT issue destructive control blindly
→ re-observe or move to bounded needs_attention behavior
```

A stop request is not itself proof of a domain `cancelled` state. The normal sequence is control request → observe → reconciliation. When stop and natural exit race, retain the observed physical result without inventing unprovable causal ordering.

## 5.6 Semantic control/error categories

Backend-specific systemd/journal/daemon errors must not leak throughout Coordinator/domain code. S2.0 uses semantic categories only; it does not require production enum/exception shapes:

```text
CONTROL_ACCEPTED
IDENTITY_CONFLICT
DEFINITE_NON_ACCEPTANCE
OBSERVATION_UNKNOWN
CONTROL_REJECTED
CONTROL_UNKNOWN
```

A transport timeout is not proof that the physical command failed. In particular:

```text
launch CONTROL_UNKNOWN
→ observe X/H
→ never blind retry
```

`CONTROL_REJECTED` or `DEFINITE_NON_ACCEPTANCE` may authorize stronger conclusions only when the selected ADR evidence proves the supervisor acceptance boundary was not crossed.

## 5.7 Frozen downstream core rules

```text
same X + same H → reconcile
same X + different H → fail closed
possible prior acceptance → never blind create
NOT_FOUND != NEVER_ACCEPTED_PROVEN
unknown launch/control outcome → observe
unknown physical state → no launch
stop/kill requires matching X/H
PID/InvocationID → observation only
physical observation → never directly owns Attempt state
P3/P6/P7/P10 → deterministic recovery, not UNKNOWN
allowed UNKNOWN → explicit finite ADR cases only
```

---

# 6. S2.0 acceptance facts and ADR output

The scenario matrix is experimental detail. The ADR finally answers eight facts:

```text
F1. Stable execution identity
F2. Safe create/query/stop control
F3. Physical execution survives controller death where expected
F4. Fresh controller rediscovery of original X/H
F5. Ack-loss/concurrent-replay/conflict safety with no duplicate launch
F6. Terminal X/H evidence survives controller restart + unit GC
F7. Stop/natural-exit race converges without duplicate or fabricated causality
F8. Host reboot fails closed without same-Attempt silent resurrection
```

S2.0 may select a topology only when:

```text
F1–F8 PASS
R1–R4 PASS
permission boundary PASS
representative environment propagation PASS
P3/P6/P7/P10 deterministic recovery PASS
no unresolved duplicate-launch ambiguity
```

The ADR records at minimum:

```text
Selected topology: A or B
Selected physical evidence stack
SupervisorExecutionIdentity projection and namespace rule
X/H binding mechanism
Ack-loss behavior
Meaning of NOT_FOUND
Terminal evidence retention contract
Required recovery classifications for P3/P6/P7/P10
Explicit finite allowed-UNKNOWN classifications
Host reboot behavior
Minimum privilege boundary
Rejected/escalated candidate rationale
Intentionally deferred production concerns
```

If no candidate satisfies the contract, the only valid disposition is `BLOCKED`, and S2.1 remains unauthorized.

---

# 7. Relationship to Slice 2 task granularity

The five design sections above are reasoning/specification sections, not five Codex tasks. S2.0 is one implementation task in the future Slice 2 Detailed Implementation Plan:

```text
Task 1 — S2.0 Runner Supervision PoC + ADR
```

Internal scenario/checkpoint structure must not create separate governance/commit/review cycles unless execution exposes a real defect requiring scope freeze.

The intended Slice 2 implementation-plan granularity is approximately five formal tasks:

```text
Task 1 — S2.0 Runner Supervision PoC + ADR
Task 2 — S2.1 Attempt Preparation + Exact Source Materialization
Task 3 — S2.2 Durable Launch / Observe / Stop
Task 4 — S2.3 Coordinator Reconciliation + S2.4 API Composition
Task 5 — S2.5 Real Recovery / Fault-Injection Acceptance
```

This task decomposition is planning guidance, not implementation authorization from this S2.0 spec. A later approved Slice 2 Detailed Implementation Plan remains the execution authority.
