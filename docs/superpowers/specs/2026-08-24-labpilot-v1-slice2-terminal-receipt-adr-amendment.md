# LabPilot V1 Slice 2 — Candidate B v2 / TerminalReceiptV1 ADR Amendment

**Status:** Accepted / Current Amendment

**Date:** 2026-08-24

**Documentation baseline:** `de7e426d69ee826ad79610bf702d1e3f3c9d666a`

**External architecture acceptance baseline:** `a1b3aaf8730fe14729bc4ef42b0b3f8a202b9f76`

**Original accepted ADR:** `docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-adr.md`

**Disposition:** `SELECT_CANDIDATE_B_V2`

**Selected physical evidence stack:** `systemd + persistent journald`

This document is a focused amendment to the original Candidate B Runner Supervision ADR. The original ADR remains Accepted. This Accepted amendment supersedes only its affected clauses concerning terminal-cause retention, the fixed Candidate B launch profile, the narrow privileged `TerminalReceiptV1` producer, receipt-related `UNKNOWN` semantics, and security review of the new root post-stop surface.

This amendment does not accept Slice 2, authorize implementation, authorize Task 5 native revalidation, authorize F18, or change G3/G4 status. It does not modify the Task 5 recovery plan or authorize an implementation plan. Historical F01 remains sealed and stopped.

Task 5 remains `AUTHORIZED / IN PROGRESS` under its existing governance. This architecture acceptance does not authorize Candidate B v2 implementation; a separate implementation plan must be written, reviewed, and explicitly authorized.

## 1. Problem statement

Candidate B v1 retains physical evidence through:

```text
current systemd unit state
+
persistent trusted PID1 manager journald
```

That stack can prove terminality after transient-unit garbage collection. It cannot always prove the terminal cause.

On the dedicated systemd 259 host, a natural process exit with status zero and a clean self-`SIGTERM` were observationally equivalent in retained trusted manager lifecycle records:

```text
Starting
Started
Deactivated successfully
```

The clean `SIGTERM` case contained no trusted PID1 process-exit record carrying `EXIT_CODE` / `EXIT_STATUS`. Therefore:

```text
Deactivated successfully
+ absence of stop evidence
+ absence of signal evidence
!= exit_code=0
```

The Candidate B v1 fallback that inferred exit code zero from those absences is an evidence-fabrication risk. Terminality and terminal cause are distinct physical facts. `Deactivated successfully` never proves `exit_code=0` by itself. Only trusted retained cause evidence may establish `PROCESS_EXITED(0)`.

## 2. Architecture spike result

A dedicated architecture spike proved the `TerminalReceipt R1` candidate viable on:

```text
systemd 259 (259.5-0ubuntu3.4)
```

The measured systemd-provided `ExecStopPost` values were:

| Scenario | `SERVICE_RESULT` | `EXIT_CODE` | `EXIT_STATUS` |
|---|---|---|---|
| natural exit 0 | `success` | `exited` | `0` |
| normal exit 7 | `exit-code` | `exited` | `7` |
| self `SIGTERM` | `success` | `killed` | `TERM` |
| self `SIGKILL` | `signal` | `killed` | `KILL` |
| explicit `systemctl stop` | `success` | `killed` | `TERM` |
| `RuntimeMaxSec` | `timeout` | `killed` | `TERM` |

The result establishes that systemd-provided `ExecStopPost` terminal variables preserve the main-process wait cause even when retained PID1 manager messages do not.

The measured physical signal after explicit stop does not by itself establish that workflow cancellation caused the exit. Independent workflow ordering is required for that causal statement. For `RuntimeMaxSec`, `timeout` is the stronger supervisor result; the `TERM` wait status does not justify fabricating a normal process exit code.

The authority probe also established:

```text
normal workload:
  UID/GID = labpilot-train
  NoNewPrivileges = 1
  bounding/effective/ambient capability sets = empty

fixed privileged ExecStopPost probe:
  UID/GID = root
  main workload identity and restrictions = unchanged
```

The workload emitted byte-identical receipt JSON but could not forge the journal-supplied root credentials used as receipt provenance. Message content is therefore data, not identity authority.

The sealed final R1 evidence manifest SHA-256 is:

```text
a5637190d7fca95a9e1d13e6a43e7fa540f21cdec2e9bf8daeff112a320ba626
```

### 2.1 Historical `%n` probe result

The spike's initial binding probe failed because systemd 259 retained `%n` literally in the tested transient `ExecStopPost` argument path. That failed result remains part of the sealed evidence and is not erased.

The final architecture must not rely on `%n` argument expansion. R1 subsequently passed with a fixed launcher/environment binding probe. The production design below removes the need for any receipt self-asserted X or H; trusted journal metadata and PID1 lifecycle evidence own physical correlation.

## 3. Decision: Candidate B v2

Select:

```text
Candidate B v2
=
Candidate B v1
+ fixed trusted TerminalReceiptV1 ExecStopPost producer
```

The authority split remains:

```text
PostgreSQL
= workflow / Attempt authority

systemd
= physical lifecycle authority

persistent journald
= retained physical evidence transport

TerminalReceiptV1
= terminal-cause evidence produced while systemd still has the main-process wait status

Runner
= narrow privileged adapter and verifier

Coordinator
= durable reconciliation / domain convergence owner
```

`TerminalReceiptV1` is not a PostgreSQL domain object, Runner execution ledger, retry scheduler, workflow authority, or generic hook/plugin interface.

## 4. TerminalReceiptV1 payload

The logical payload is exactly four fields:

```json
{
  "schema_version": "labpilot-terminal-receipt-v1",
  "service_result": null,
  "exit_code": null,
  "exit_status": null
}
```

The last three values are bounded strings or `null`. The fixed reporter records only the systemd-provided `SERVICE_RESULT`, `EXIT_CODE`, and `EXIT_STATUS` facts. A missing source value becomes `null`.

The implementation must define and test a bounded, canonical, single-record serialization of these four fields. Invalid or unbounded source values fail closed and do not become a valid receipt. An absent cause variable is the explicitly represented `null` case, not permission to invent a value.

The payload must not contain or self-assert:

```text
X
H
unit identity
invocation ID
boot ID
Attempt ID
PID
filesystem path
timestamp as an authority field
```

Receipt `MESSAGE` content is data, not identity authority. The root producer serializes physical facts; it does not interpret domain semantics. Unknown but bounded systemd result tokens may be preserved as evidence. Only explicitly admitted combinations receive known domain cause semantics.

## 5. Evidence authority and correlation

Candidate B v2 uses two separate trusted evidence streams.

### 5.1 ManagerEvidence

Trusted ManagerEvidence requires PID 1/systemd manager provenance and establishes:

- exact physical unit/X;
- H through the exact H-bearing `Started` lifecycle witness;
- systemd invocation;
- historical boot;
- physical lifecycle and terminality;
- trusted stop/timeout manager facts where present.

ManagerEvidence remains the terminality authority.

### 5.2 ReceiptEvidence

A matching receipt requires journal-trusted provenance with:

- the exact transient unit;
- `_UID=0`;
- `_GID=0`;
- the exact matching systemd invocation;
- the exact matching historical boot;
- the exact `TerminalReceiptV1` discriminator/schema.

`MESSAGE` claims, `_COMM`, `_EXE`, `SYSLOG_IDENTIFIER`, and reporter PID are not primary identity authority. The unprivileged workload may reproduce all message bytes.

The receipt correlation key is:

```text
exact unit + exact invocation + exact boot
```

H comes only from the trusted PID1 `Started` witness. A receipt never independently establishes H. Receipt cause evidence is admitted only after correlation with ManagerEvidence.

## 6. Correlation and cardinality rules

### 6.1 Identity and acceptance

```text
same X + different H
→ IDENTITY_CONFLICT
→ sticky / fail closed
→ no stop, relaunch, or replacement
```

An H-less receipt without a matching trusted `Started`/H witness cannot establish acceptance. Where physical traces exist, observation is `UNKNOWN`; the receipt is never creation authority.

A terminal receipt without independently proven systemd terminality does not replace manager terminality authority and fails closed.

An unrelated invocation or boot receipt must not complete the current lifecycle.

### 6.2 Matching receipt cardinality

For one exact `unit + invocation + boot`:

```text
0 matching receipts
+ trusted manager evidence proves terminality
→ TERMINAL_MATCH
→ no exit_code
→ no termination_signal
→ PROCESS_TERMINAL_UNCLASSIFIED
→ never infer exit 0

1 valid matching receipt
→ interpret only an explicitly admitted terminal-cause combination

>1 matching trusted receipts
→ evidence integrity violation
→ UNKNOWN / attention
→ do not choose first or latest
→ no retry or relaunch
```

Receipt query failure or timeout yields `UNKNOWN`. A successful query with zero matches establishes known absence and, if manager terminality is proven, yields terminal-unclassified. A malformed matching trusted receipt yields `UNKNOWN / attention`.

A valid receipt with a currently unsupported but bounded result preserves the bounded evidence. Manager-proven terminality remains `TERMINAL_MATCH`; the process cause is `PROCESS_TERMINAL_UNCLASSIFIED`, with a supervisor result retained where the current model allows it.

## 7. Terminal semantic mapping

Only combinations measured by the R1 spike are admitted initially.

```text
success / exited / 0
→ TERMINAL_MATCH
→ exit_code=0
→ PROCESS_EXITED

exit-code / exited / N, where N > 0
→ TERMINAL_MATCH
→ exact exit_code=N
→ PROCESS_EXITED

success / killed / TERM
+ no stronger supervisor fact
→ TERMINAL_MATCH
→ termination_signal=15
→ PROCESS_SIGNALED

signal / killed / KILL
→ TERMINAL_MATCH
→ termination_signal=9
→ PROCESS_SIGNALED

timeout / killed / TERM
→ TERMINAL_MATCH
→ supervisor_result = "timeout"
→ exit_code = None
→ termination_signal = None
→ PROCESS_TERMINAL_UNCLASSIFIED
```

For a trusted `RuntimeMaxSec` terminal result, `SERVICE_RESULT=timeout` is the stronger physical supervisor classification. The `TERM` wait status is the mechanism systemd used to terminate the process; it must not classify the execution as an ordinary `PROCESS_SIGNALED` event at the current unchanged domain boundary.

For an explicit stop producing `success / killed / TERM`, the strongest physical wait-status truth may retain `termination_signal=15`. It must not claim that Batch cancellation caused the signal unless independent workflow ordering proves that causality.

`Deactivated successfully` never again proves `exit_code=0` by itself. Only trusted retained cause evidence may establish `PROCESS_EXITED(0)`.

### 7.1 Observation-consistency invariant

For the same trusted physical terminal facts, semantic classification must not depend on whether observation occurs while the exact systemd unit remains loaded or after unit GC through retained ManagerEvidence and ReceiptEvidence. The loaded-unit and post-GC paths must produce equivalent `PhysicalObservation` fields and domain mapping for:

- natural exit zero;
- normal nonzero exit;
- clean `SIGTERM`;
- `SIGKILL`;
- `RuntimeMaxSec` timeout;
- explicit-stop physical wait-status truth.

In particular, both paths apply timeout precedence for `RuntimeMaxSec`: `TERMINAL_MATCH`, `supervisor_result = "timeout"`, `exit_code = None`, `termination_signal = None`, and `PROCESS_TERMINAL_UNCLASSIFIED`. Observation timing or unit GC must not turn the same trusted timeout facts into `PROCESS_SIGNALED`.

## 8. Candidate B v1 compatibility

Historical Candidate B v1 executions remain observable. New v1 launches are not admitted after v2 rollout.

A v2 Runner may:

- `OBSERVE` historical v1 X/H;
- `STOP` a historical v1 execution only after a fresh exact `ACTIVE_MATCH`.

The Coordinator does not need v1/v2 profile awareness.

For a historical v1 execution that has been garbage-collected:

```text
manager terminality proven
+ no retained cause
+ no TerminalReceipt
→ TERMINAL_MATCH
→ PROCESS_TERMINAL_UNCLASSIFIED
```

If historical v1 current-unit or trusted journal evidence independently retained a real exit code or signal, that stronger fact may still be used. The system must not retroactively pretend that v1 produced a `TerminalReceiptV1`. Already-terminal PostgreSQL Attempt rows are not automatically reinterpreted.

## 9. Candidate B v2 launch profile

`candidate-b-v2` preserves the existing workload profile:

- system manager scope;
- `Type=exec`;
- `User=labpilot-train`;
- `Group=labpilot-train`;
- `Restart=no`;
- `KillMode=control-group`;
- `NoNewPrivileges=yes` for the main workload;
- empty main-workload capability sets;
- stdout/stderr to journal;
- fixed X-derived unit;
- `Description` binds exact H;
- existing `RuntimeMaxSec` / `TimeoutStopSec` semantics.

It adds exactly one privileged semantic: a fixed, failure-neutral `TerminalReceiptV1` `ExecStopPost` producer.

It does not add a wrapper process, sidecar, second supervisor daemon, generic hook API, arbitrary caller-selected `ExecStopPost`, caller-selected reporter executable, generic root exec API, or durable receipt-file ledger.

## 10. Root reporter deployment contract

The reporter is a standalone immutable deployment artifact. Its conceptual location is:

```text
/opt/labpilot-runner/libexec/labpilot-terminal-receipt-v1.py
```

Implementation review may select a stronger equivalent fixed path. The artifact must always be:

- referenced by a fixed absolute path;
- owned `root:root`;
- a regular file and not a symlink;
- not group/world writable;
- a versioned immutable semantic artifact;
- stdlib-only;
- independent of the LabPilot application package and workspace imports;
- free of shell, network, database, subprocess, Runner-socket, and workspace read/write behavior;
- free of caller-selected arguments.

### 10.1 Trusted filesystem path chain

The complete fixed reporter deployment path used by privileged `ExecStopPost` must reside in a trusted root-controlled filesystem namespace. This contract covers the path chain through which the artifact is resolved, not only the final file:

- the reporter deployment directory is root-owned;
- the reporter deployment directory is not group/world writable;
- reporter resolution does not traverse an untrusted symlink or another attacker-controlled redirectable path component;
- the final reporter remains a regular, non-symlink, root-owned artifact;
- the final reporter remains not group/world writable;
- the final reporter digest exactly matches the fixed `candidate-b-v2` profile manifest;
- no unprivileged workload or user can replace, rename, redirect, or substitute the reporter through writable ancestor-directory or path-component authority.

This invariant freezes trusted path immutability rather than an arbitrary numeric mode. It applies across the time separation between Runner readiness verification and later systemd `ExecStopPost` execution. Checking only the final file at readiness is insufficient when a mutable or untrusted ancestor could redirect the fixed path before delayed privileged execution.

The later implementation and TDD must prove an appropriate TOCTOU-resistant filesystem boundary; this ADR does not select the exact filesystem API and does not introduce a generic filesystem sandbox.

Execution uses an absolute trusted interpreter in isolated Python mode, conceptually equivalent to:

```text
/usr/bin/python3 -I -B <fixed-root-owned-reporter>
```

This is a required boundary, not yet a frozen production command line. Native systemd 259 TDD must verify the final exact construction.

Reporter resolution must not depend on Attempt cwd, Attempt workspace, `PYTHONPATH`, user site, caller `PATH`, or a caller-controlled module path.

The privileged producer's executable-loading environment must also be mechanically independent of mutable or untrusted main-workload environment values. No main-workload environment value may influence executable loading, dynamic-library resolution, interpreter code resolution, or privileged reporter code resolution. This prohibition explicitly includes `LD_LIBRARY_PATH` and any future equivalent loader-sensitive environment key admitted for the main workload.

Python `-I` isolated mode is necessary for Python import isolation but is not by itself proof of ELF or dynamic-loader isolation. This amendment does not prematurely select the implementation mechanism. A later implementation may use a sanitized per-hook environment or another equivalently strong fixed mechanism, but the selected boundary must be proven on the native systemd 259 host. It must not introduce shell execution, a generic environment bridge, a caller-controlled reporter environment, or a generic hook API.

Neither process environment nor a mutable filesystem namespace may redirect the privileged reporter's executable or code resolution. If that combined boundary cannot be enforced without widening authority, architecture review must reopen.

## 11. Immutable artifact rule

The reporter semantic version is an immutable deployment ABI.

```text
terminal-receipt-v1 path exists
+ bytes and metadata exactly match expected v1 artifact
→ reuse

same v1 path exists
+ bytes or required metadata differ
→ installer fails closed
→ never overwrite in place
```

New reporter semantics require both a new reporter version and a new Candidate B launch-profile version. This protects executions that may remain active while the Runner application is upgraded.

## 12. Profile identity and H binding

The current `FrozenLaunchSpec` schema and H algorithm remain unchanged. The existing H input fields remain:

```text
launch_profile_version
launch_policy_identity
```

For `candidate-b-v2`, derivation of `launch_policy_identity` must mechanically bind the fixed physical profile artifact:

```text
launch_policy_identity = hash(
    runner policy JSON,
    fixed candidate-b-v2 profile manifest,
)
```

The fixed profile manifest binds at least:

- `launch_profile_version = candidate-b-v2`;
- `terminal_receipt_schema = labpilot-terminal-receipt-v1`;
- fixed reporter path;
- exact reporter artifact SHA-256.

Therefore:

```text
reporter bytes change
→ profile identity changes
→ launch_policy_identity changes
→ FrozenLaunchSpec changes
→ H changes
```

The design does not depend only on humans remembering to bump a string version. Reporter path and digest are fixed implementation/profile-manifest facts, not administrator-selectable Runner policy fields.

## 13. Runner policy, protocol, and domain boundary

The Runner policy JSON schema gains no configurable hook or reporter field. New-launch admission under v2 fails closed unless the requested fixed launch profile is known and supported.

The Runner protocol remains exactly:

```text
INFO
LAUNCH
OBSERVE
STOP
```

The following remain unchanged:

- PostgreSQL schema;
- `FrozenLaunchSpec` schema;
- `TerminalObservation` and domain terminal kinds;
- Coordinator scheduling;
- Coordinator startup barrier;
- Task 4 retry policy.

`TerminalReceiptV1` remains below the domain boundary.

## 14. Runner readiness

A Runner advertising both:

```text
readiness_reason=ready
launch_profile_version=candidate-b-v2
```

must already have verified both the trusted reporter path chain and the final reporter artifact against the fixed deployment contract. At minimum, the Runner is not ready when:

- the reporter deployment directory is not root-owned;
- the reporter deployment directory is group/world writable;
- the reporter path traverses an untrusted symlink or another attacker-controlled redirection;
- the final reporter is absent, a symlink, or not a regular file;
- the final reporter has unsafe ownership or write permissions;
- the final reporter digest is wrong;
- the reporter disagrees with the fixed profile identity;
- the trusted interpreter is unavailable;
- isolated-execution sanity verification fails.

Installer and readiness verification fail closed for every condition above. Candidate B v2 launch authority must not be advertised from a path boundary that an unprivileged actor can redirect between readiness and delayed `ExecStopPost` execution.

A half-installed v2 Runner must not allow the Coordinator to freeze a `candidate-b-v2` `FrozenLaunchSpec`/H.

## 15. ExecStopPost privilege and failure neutrality

The R1 spike proved that a narrow privileged `ExecStopPost` can execute as root while the main workload remains non-root, NNP-restricted, and capability-empty.

Production requires both:

1. narrow privileged execution of the fixed reporter; and
2. reporter process failure must not redefine the main service result.

A systemd command-prefix construction combining privilege override and failure-ignore semantics is a candidate implementation mechanism. Exact `-+` behavior is not accepted as a production fact by this amendment. Dedicated native systemd 259 verification is mandatory before implementation acceptance.

If no narrow mechanism satisfies both trusted root production and failure neutrality without widening authority, architecture review must reopen. The reporter must not be forced to always return zero as a substitute for failure neutrality.

## 16. Upgrade and rollback semantics

Supported:

```text
candidate-b-v1 running/nonterminal executions
+ candidate-b-v2 Runner
→ new Runner observes and reconciles historical execution

new LAUNCH after v2 activation
→ candidate-b-v2 only
```

An automatic hot downgrade from active v2 semantics to an old v1 Runner is not claimed safe. Once v2 executions may exist, severe v2 deployment failure requires stopping new dispatch and performing external recovery review rather than blind downgrade.

The Coordinator gains no profile-version awareness.

## 17. Restart, garbage collection, and retained recovery

Receipt production is owned by the systemd lifecycle. It does not require Runner daemon survival after launch acceptance, Coordinator survival, or PostgreSQL availability.

Required recovery properties are:

```text
Runner down during execution
→ systemd still executes ExecStopPost
→ persistent journal retains the receipt
→ later OBSERVE recovers it

Coordinator down after physical terminal but before DB commit
→ startup reconciliation
→ OBSERVE
→ terminal domain state converges

unit GC
→ persistent journal retains ManagerEvidence + ReceiptEvidence

reboot
→ historical boot evidence remains queryable under accepted reboot semantics
```

## 18. Retry and failure policy

Reporter failure does not authorize re-running the scientific workload.

```text
successful receipt query
+ manager terminality proven
+ no matching receipt
→ PROCESS_TERMINAL_UNCLASSIFIED
→ terminal Attempt
→ no automatic retry solely because cause precision was lost

receipt query unavailable
→ UNKNOWN
→ no stop, relaunch, or retry

malformed or duplicate matching trusted receipt
→ UNKNOWN / attention
→ no retry or relaunch
```

`TerminalReceiptV1` failures do not join the current infrastructure retry allowlist.

## 19. Error model

### 19.1 `IDENTITY_CONFLICT`

- exact X with wrong H;
- sticky;
- never an `UNKNOWN` alias;
- no destructive stop, relaunch, or replacement.

### 19.2 `UNKNOWN`

- evidence source unavailable;
- receipt query failed;
- matching trusted receipt malformed or duplicated;
- contradictory invocation, boot, or lifecycle evidence;
- physical traces exist but exact H acceptance cannot be safely established.

`UNKNOWN` authorizes no blind create, stop, relaunch, or retry.

### 19.3 `TERMINAL_MATCH` / unclassified

Terminality is proven, cause is absent or unsupported, and evidence is not contradictory. The terminal Attempt remains terminal, with `PROCESS_TERMINAL_UNCLASSIFIED` cause precision.

## 20. Required TDD and verification boundary

Production implementation must be TDD-driven across all six layers.

### T1 — Reporter contract

- exact four-field schema;
- canonical bounded serialization;
- missing source fields become explicit `null`;
- invalid/unbounded environment values fail closed;
- no LabPilot or workspace import.

### T2 — Supervisor evidence

- H authority;
- exact unit/invocation/boot join;
- root provenance;
- cardinality;
- natural zero, exit 7, `SIGTERM`, `SIGKILL`, timeout, and explicit stop;
- equivalent `PhysicalObservation` and domain semantics for the same trusted facts while loaded and after unit GC, including timeout precedence on both paths;
- missing, malformed, duplicate, unavailable-query, and unsupported-valid receipt semantics.

### T3 — Launch/profile

- exact fixed hook;
- caller cannot select hook, path, or argv;
- caller and main-workload environment cannot influence privileged executable, dynamic-library, interpreter-code, or reporter-code resolution;
- main UID/GID/NNP/capabilities remain unchanged;
- v2 profile participates mechanically in H.

### T4 — Policy/readiness

- `candidate-b-v2` admission;
- reporter/profile artifact identity participates in `launch_policy_identity`;
- invalid reporter makes Runner not ready.

### T5 — Installer

- immutable install or exact reuse;
- byte drift rejection;
- trusted path-chain validation, including root-controlled, non-group/world-writable deployment-directory metadata;
- rejection of untrusted symlink or attacker-controlled path redirection;
- final regular non-symlink artifact ownership, write-safety, and digest verification;
- proof that unprivileged ancestor/path authority cannot replace, rename, redirect, or substitute the reporter;
- application upgrades do not replace an in-use reporter semantic artifact.

### T6 — Native systemd 259

- privileged fixed hook;
- failure-neutral hook;
- anti-spoof behavior;
- adversarial loader-sensitive unit environment, including `LD_LIBRARY_PATH`, cannot alter the trusted interpreter, dynamic libraries, or fixed reporter code selected for the root producer;
- post-GC recovery;
- Runner restart;
- Coordinator restart;
- v1 live-upgrade compatibility.

## 21. Negative acceptance boundary

Implementation cannot be accepted unless all of these negative properties hold:

- workload emits byte-identical receipt: not trusted;
- root receipt with wrong invocation: not correlated;
- root receipt with wrong boot: not correlated;
- same X with wrong H: sticky `IDENTITY_CONFLICT`;
- duplicate matching trusted receipts: `UNKNOWN`;
- malformed matching trusted receipt: `UNKNOWN`;
- receipt journal unavailable: `UNKNOWN`;
- successful query with no receipt: terminal-unclassified when manager terminality is proven;
- reporter-v1 byte drift: installer fails closed;
- reporter invalid at startup: Runner not ready;
- caller cannot choose `ExecStopPost`;
- caller cannot choose privileged reporter argv;
- malicious workspace Python or LabPilot module cannot affect the root reporter;
- loader-sensitive main-workload environment cannot affect privileged reporter executable or code resolution;
- an unprivileged actor controlling an unsafe ancestor or path component cannot substitute or redirect the fixed privileged reporter; unsafe parent/path metadata makes the installer fail closed or the Runner remain not ready;
- neither process environment nor a mutable filesystem namespace can redirect privileged reporter executable/code resolution;
- loaded-unit and post-GC observation of the same trusted terminal facts cannot yield different semantics, including timeout precedence;
- a running v1 execution survives v2 Runner deployment;
- v2-to-v1 hot downgrade is not claimed safe.

## 22. Formal design acceptance criteria

### TR-D1 — Timely cause capture

Terminal cause is frozen while systemd still holds the authoritative main-process wait cause.

### TR-D2 — Fixed producer

The reporter is a fixed root-owned producer, not a hook API.

### TR-D3 — Unforgeable authority

The workload cannot forge receipt authority.

### TR-D4 — Trusted physical identity

Physical identity is established through trusted unit + H witness + invocation + boot.

### TR-D5 — Cause-only receipt

The receipt provides cause only; it does not self-establish X or H.

### TR-D6 — Mechanical distinction

Natural exit zero and clean `SIGTERM` are mechanically distinguishable.

### TR-D7 — No guessed zero

A missing receipt can never become a guessed exit zero.

### TR-D8 — Failure neutrality

Reporter failure neither changes main physical truth nor authorizes automatic scientific re-execution.

### TR-D9 — Artifact-bound H

Reporter artifact identity is mechanically bound into `launch_policy_identity` and H.

### TR-D10 — v1 compatibility / v2 admission

Historical v1 observation compatibility remains, while new launches are v2 only.

### TR-D11 — No domain expansion

DB schema, Runner protocol, and Coordinator scheduling/retry state machine do not expand.

### TR-D12 — Retained proven cause

GC, reboot, or Runner loss can retain and recover every terminal cause actually proven by the selected evidence stack. For the same trusted physical facts, loaded-unit and retained post-GC observation produce equivalent semantics.

## 23. Relationship to the original ADR

The original document:

```text
docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-adr.md
```

remains Accepted. This Accepted amendment supersedes only affected clauses concerning:

- the fixed systemd launch profile;
- terminal-cause retention;
- the narrow privileged `TerminalReceiptV1` producer;
- receipt-related `UNKNOWN` semantics;
- security review of the new root post-stop surface.

It preserves the original decisions on:

- the narrow AF_UNIX + `SO_PEERCRED` Runner boundary;
- Runner not being a workflow engine or ledger;
- the X/H contract;
- positive creation authority;
- no blind launch replay;
- STOP only after exact `ACTIVE_MATCH`;
- sticky identity conflict;
- PostgreSQL workflow authority;
- systemd + persistent journald as the physical evidence stack;
- no generic shell or root execution;
- the reboot safety model.

TerminalReceiptV1 is evidence placed into persistent journald, not a second durable evidence database.

## 24. Explicitly out of scope

This amendment does not admit:

- a new Runner protocol operation;
- a `TerminalReceipt` database table;
- persistent receipt files;
- a Runner durable execution ledger;
- a wrapper supervisor process;
- a generic hook registry or plugin system;
- generic privileged `ExecStopPost` configuration;
- Coordinator receipt awareness;
- a new automatic retry class;
- automatic historical Attempt repair;
- automatic v2-to-v1 downgrade machinery;
- Slice 3 changes.

If implementation later proves any such expansion necessary, architecture review must reopen.

## 25. Task 5 closure order

This is a governance sequence, not an implementation plan:

```text
architecture amendment accepted
→ implementation plan later
→ candidate-b-v2 implementation
→ external production code review
→ separate deferred native-harness H1/H2 remediation
→ exact-head verification
→ one final Task 5 SHA
→ fresh Runner install / Phase H
→ fresh production stack / Phase I
→ fresh N1–N7
→ Candidate B v2 profile acceptance
→ fresh formal F01 onward
→ F18 remains separately authorized
```

Historical F01 remains sealed and is not repaired or reused.

## 26. Governance consequence

```text
Original Candidate B Runner Supervision ADR = ACCEPTED / UNCHANGED
This Candidate B v2 amendment             = ACCEPTED / CURRENT AMENDMENT
TerminalReceipt R1 architecture spike     = PASSED
Task 5                                     = AUTHORIZED / IN PROGRESS
Slice 2 overall                           = NOT ACCEPTED BY THIS AMENDMENT
Task 5 implementation                     = NOT AUTHORIZED BY THIS AMENDMENT
Task 5 native revalidation                = NOT AUTHORIZED
F18                                       = NOT AUTHORIZED
G3 / G4 status                            = UNCHANGED
```

External architecture review has accepted this amendment. Candidate B v2 implementation remains not authorized until a separate implementation plan is written, reviewed, and explicitly authorized.
