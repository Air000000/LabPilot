# LabPilot V1 Slice 2 — Runner Supervision ADR

**Status:** Accepted / Current for the Slice 2 supervision boundary
**Planning baseline:** `4109afa9f871ae45cf32c301040bcff6f109939d`
**Design authority:** `docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-design.md`
**Detailed plan:** `docs/superpowers/plans/2026-08-13-labpilot-v1-slice2-recovery.md`
**Disposition:** `SELECT_B`
**Selected physical evidence stack:** `systemd + persistent journald`

This ADR freezes only the privileged physical-execution boundary proven by S2.0. It does not accept Slice 2 as a whole and does not authorize production Tasks 2–5 by itself.

## 1. Decision

Select Candidate B:

```text
non-root LabPilot controller
        |
        | narrow AF_UNIX protocol + SO_PEERCRED
        v
root-owned narrow Runner daemon
        |
        | fixed Runner-derived systemd service profile
        v
systemd system manager
        |
        v
non-root labpilot-train workload
```

The Runner is a privileged adapter, not a workflow engine or execution ledger.

The downstream authority split remains:

```text
PostgreSQL
= future durable execution intent / authoritative workflow ledger

systemd + persistent journald
= physical supervision and bounded physical observation evidence

Coordinator/Reconciler
= compare durable expectation with physical evidence and converge domain state
```

## 2. Candidate A disposition — REJECT_A

Candidate A attempted to preserve a direct unprivileged controller-to-systemd boundary through a narrow polkit authorization.

Native Ubuntu 26.04 / systemd 259 evidence showed that exact unit-name plus `start`-verb authorization still left caller-controlled launch semantics available to the authorized caller. The probe was able to select root execution and an arbitrary shell command through the creation-capable systemd interface. The resulting workload completed as UID 0.

That violates the required privilege boundary: authorization to create one LabPilot execution must not imply caller authority to choose arbitrary privileged `User=` / `ExecStart=` semantics.

Therefore:

```text
Candidate A = REJECT_A
```

The decision is not repaired by broadening polkit, NOPASSWD sudo, running the controller as root, switching to `systemd --user`, or exposing an unrestricted systemd control socket.

## 3. Candidate B authority boundary

The selected Candidate B contract is intentionally narrow.

### 3.1 Controller authority

The unprivileged controller may request only the fixed protocol operations required by S2.0:

```text
INFO
LAUNCH
OBSERVE
STOP
```

It does not receive a generic shell, exec, systemd property, unit-name, PID, signal, scheduler, or plugin API.

### 3.2 Runner authority

The root Runner owns the privileged translation from a validated fixed request into the systemd system-manager operation. It owns the fixed launch profile and fixed host paths/identities. It does not own domain launch authorization, retry policy, Experiment/Trial/Attempt state, or a durable execution ledger.

`execution_spec_hash H` proves semantic identity/integrity of the frozen physical launch request. H is not domain authorization.

### 3.3 Workload authority

Training executes as the dedicated non-root `labpilot-train` identity. The workload does not gain the Runner socket authority and does not receive ambient/effective/bounding capabilities through the selected profile.

## 4. Frozen X/H contract

A future production Attempt maps deterministically to one stable supervisor execution identity `X`.

A versioned frozen launch specification maps canonically to SHA-256 `H`.

The selected rules are:

```text
same X + same H
→ reconcile the existing physical execution
→ no duplicate create

same X + different H
→ IDENTITY_CONFLICT
→ fail closed
→ no stop, replacement, or relaunch

NOT_FOUND
!= NEVER_ACCEPTED_PROVEN

possible prior supervisor acceptance
→ never blind-create
→ OBSERVE(X, H)

unknown launch/control response
→ observation/reconciliation
→ never automatic LAUNCH retry
```

PID, process start time, InvocationID, journal cursor, and transient systemd object lifetime are observation facts only; none is LabPilot domain identity.

## 5. Positive creation authority

Physical creation remains mechanically separate from physical observation.

Production design must preserve the semantic distinction:

```text
FIRST_DISPATCH_PROVEN
NEVER_ACCEPTED_PROVEN
```

versus:

```text
ACTIVE_MATCH
TERMINAL_MATCH
IDENTITY_CONFLICT
UNKNOWN
```

Absence is not creation authority. Once dispatch may have crossed the supervisor acceptance boundary, the original execution must be reconciled rather than guessed away.

The Runner must not independently retry LAUNCH.

## 6. Selected systemd profile

The S2.0 implementation proves a fixed Runner-derived profile rather than caller-selected systemd properties. The tested profile includes the following invariants:

```text
system manager scope
Type=exec
User=labpilot-train
Group=labpilot-train
Restart=no
KillMode=control-group
NoNewPrivileges=yes
empty capability sets
stdout/stderr to journal
fixed X-derived unit naming
Description binds exact H
```

The exact PoC command syntax is not a production domain API. A later production adapter may reuse semantics only after the Slice 2 production plan explicitly admits that reuse.

## 7. Physical observation and terminal retention

The selected physical evidence stack is:

```text
current systemd unit state
+
persistent journald
```

Current unit state is preferred while the exact unit is loaded. Persistent journal evidence provides bounded X/H recovery after terminal collection/GC and after controller loss.

The implementation must accept trusted systemd PID-1 lifecycle evidence where a workload cannot cooperatively emit a terminal record, for example when an exact matched unit is stopped. It must not fabricate an exit code or claim that cancel caused exit when the ordering is not provable.

Mandatory normal recovery remains deterministic:

```text
P3  → ACTIVE_MATCH or TERMINAL_MATCH for acceptance-crossed subcases
P6  → TERMINAL_MATCH
P7  → TERMINAL_MATCH after unit GC/unload
P10 → TERMINAL_MATCH after controller-down exit
```

These scenarios may not be weakened to final `UNKNOWN` for normal selected-topology operation.

## 8. Finite allowed-UNKNOWN set

`UNKNOWN / needs_attention` is allowed only for bounded cases where the physical evidence cannot prove a stronger classification without violating safety:

1. A launch/control response is `CONTROL_UNKNOWN` and no exact X/H acceptance witness is yet available; no replay is allowed.
2. P11 host reboot has changed the boot, the same X has not relaunched, the independent-root count has not increased, and retained exact X/H journal evidence exists, but exact terminal classification cannot be proven.
3. The selected observation sources are temporarily unavailable such that exact X/H cannot be established; no create or destructive stop is allowed until observation recovers.
4. Physical evidence is contradictory or fails exact X/H integrity checks; fail closed and require bounded operator attention.

There is no generic “other errors” permission to continue.

`IDENTITY_CONFLICT` is never an allowed-UNKNOWN alias.

## 9. STOP / kill semantics

Destructive control requires exact `ACTIVE_MATCH` for X/H.

```text
ACTIVE_MATCH
→ bounded stop may be issued

TERMINAL_MATCH
→ no destructive action

IDENTITY_CONFLICT
→ MUST NOT stop

UNKNOWN
→ MUST NOT stop blindly
```

A successful stop command is not itself a domain terminal transition. Physical evidence is observed first; the future Coordinator owns the authoritative Attempt transition.

## 10. Reboot semantics

Host reboot does not require process survival.

The selected safety contract is:

```text
boot changes
same X is not automatically resurrected
no new LAUNCH occurs during reboot verification
independent physical-root count does not increase
pre-reboot exact X/H journal evidence remains queryable
post-reboot observation is TERMINAL_MATCH or bounded UNKNOWN
```

The final native campaign observed `TERMINAL_MATCH` after reboot with no same-X resurrection.

## 11. Security decision

Candidate B is selected only with the tested narrow-host boundary. The P12 native negative suite demonstrated fail-closed behavior for the required abuse cases, including unauthorized peers, workload access to the Runner socket, arbitrary unit/property injection, root/capability injection, path escape, H mismatch, arbitrary-service stop, wrong-H destructive stop, malformed/oversized protocol input, and acknowledgement-loss duplicate safety.

Any future production implementation that materially widens this authority surface invalidates the direct applicability of this ADR and requires focused re-review.

## 12. Rejected alternatives and forbidden shortcuts

The following are explicitly outside the selected design:

```text
controller runs as root
broad sudo/NOPASSWD systemd authority
unrestricted polkit creation authority
systemd --user as a permission workaround
Docker/systemd privileged socket mounted into API/Coordinator
generic shell/exec Runner protocol
arbitrary caller-selected systemd unit properties
Runner-owned retry scheduler
Runner-owned durable Attempt ledger
generic backend registry / remote-runner framework
Restart=always as experiment retry
```

## 13. Scope intentionally not frozen by S2.0

This ADR does not freeze or authorize:

```text
Batch production schema
Trial production schema
Attempt production schema/status enum
production migrations
source-materialization schema
Coordinator production reconciliation state machine
API routes
multi-host / SSH / remote execution
scheduler / worker pool / resource allocator
generic Runner interface hierarchy
```

Those belong to the remaining Slice 2 Tasks 2–5 and require an approved amendment of the current Slice 2 detailed plan before implementation.

## 14. Native evidence reference

Bounded evidence summary:

`docs/superpowers/evidence/2026-08-13-labpilot-v1-slice2-runner-supervision-poc.md`

Final S2.0 code baseline:

```text
4109afa9f871ae45cf32c301040bcff6f109939d
```

Final Candidate B code bundle SHA-256:

```text
cb63d84b2c3604b32fdf18905302a762aa5e5761a50fef44e4cd7814f2065186
```

Final native `final5` evidence archive SHA-256:

```text
cf6537da2eac2857175d5ccbae6ab07a7420c325c66e8154bef3897a72c90990
```

The archive was copied from the native VM to a separate Windows host and the SHA-256 matched byte-for-byte.

## 15. Governance record note

The historical r2 plan text says that implementing a Candidate B daemon required a focused plan amendment. The repository history at the final S2.0 baseline does not contain a separate pre-implementation Candidate-B plan-amendment document; Candidate B admission begins in commit `0365f79` and the narrow daemon implementation begins in `6040630`.

This ADR does not backdate authorization or rewrite that history. It records the supervision decision actually proven and accepted after the native campaign. Treat the missing pre-implementation amendment as a documentation/governance gap in the Task 1 audit trail, not as missing physical evidence. Do not repeat that process for Tasks 2–5: their execution-precision amendment must be reviewed and approved before production implementation starts.

## 16. Decision consequence

```text
S2.0 supervision gate = ACCEPTED
Candidate A             = REJECT_A
Candidate B             = SELECT_B
Slice 2 overall          = NOT YET ACCEPTED
Tasks 2–5 implementation = NOT YET AUTHORIZED
```

The next authorized Slice 2 action is to amend the existing detailed plan with execution-precision Tasks 2–5, binding those tasks to this ADR and the final Task 1 baseline. Production implementation starts only after that amendment is reviewed and approved.
