# LabPilot V1 Slice 2 — Runner Supervision Native PoC Evidence

**Evidence status:** Frozen bounded summary
**Final disposition supported:** `REJECT_A / SELECT_B`
**Final S2.0 code baseline:** `4109afa9f871ae45cf32c301040bcff6f109939d`
**Authoritative raw archive:** `labpilot-s2-final5-COMPLETE-20260816T160825Z.tar.gz`
**Raw archive SHA-256:** `cf6537da2eac2857175d5ccbae6ab07a7420c325c66e8154bef3897a72c90990`

This document is intentionally bounded. It records the facts required to reproduce and review the S2.0 decision without committing unbounded host journals. The frozen raw archive is the source of truth for the final campaign JSON/state/marker files.

## 1. Native host and principals

Authoritative S2.0 execution used a dedicated VMware Ubuntu test VM, not a physical experiment server.

Observed host baseline:

```text
Ubuntu 26.04 LTS
systemd 259
kernel 7.0.0-29-generic
persistent system journal available
```

Candidate B runtime principals:

```text
labpilot-controller
= non-root control identity
= member of labpilot-control
= no sudo authority

labpilot-train
= non-root workload identity
= not a member of labpilot-control
= no sudo authority
```

Runner boundary:

```text
/run/labpilot-s2-runner-poc/control.sock
root:labpilot-control 0660

/run/labpilot-s2-runner-poc
root:labpilot-control 0750

transport
AF_UNIX + SO_PEERCRED
```

The Runner service runs as root, while the supervised training workload runs as `labpilot-train`.

## 2. Candidate A decisive evidence

Candidate A was evaluated first on native systemd/polkit capability.

Ubuntu 24.04 / systemd 255 did not expose the unit/verb detail needed for the intended exact authorization experiment. Ubuntu 26.04 / systemd 259 did expose exact unit plus verb information.

A temporary exact-unit + `start` polkit authorization was then tested. The authorized unprivileged caller was still able to select privileged physical launch semantics, including root execution and an arbitrary shell command. The probe completed as UID 0.

Therefore the system-manager creation API remained too expressive for the intended controller authority boundary.

```text
Candidate A P12 privilege boundary = FAIL
Candidate A = REJECT_A
```

The temporary Candidate-A policy was removed before Candidate B native testing.

## 3. Candidate B fixed boundary

Candidate B replaced direct unprivileged systemd creation with a root-owned narrow Runner.

The PoC Runner accepts only fixed protocol operations and derives the host systemd launch profile itself. It does not expose arbitrary shell/exec, arbitrary unit names, arbitrary systemd properties, arbitrary PID/signal control, network transport, backend plugins, scheduler behavior, or a durable execution ledger.

The tested systemd profile included:

```text
User=labpilot-train
Group=labpilot-train
Type=exec
Restart=no
KillMode=control-group
NoNewPrivileges=yes
empty effective/bounding/ambient capabilities
stdout/stderr → journal
fixed X-derived unit identity
Description → exact H binding
```

## 4. Final native scenario matrix

`P0` is emitted by the `characterize` command. `P11` is a two-phase human reboot scenario.

| Scenario | What was proved | Final bounded observation | Result |
|---|---|---|---|
| P0 | Candidate B host, Runner boundary, persistent evidence prerequisites | characterization completed; Runner/systemd/journal prerequisites available | PASS |
| P1 | stable/collision-safe supervisor execution identity X | repeated fresh-controller derivation stable; namespace separation preserved | PASS |
| P2 | physical launch and exact live X/H binding | exact unit X + Description H reached `ACTIVE_MATCH`; one physical root | PASS |
| P3 | acknowledgement-loss recovery without blind replay | B-ACK-1 `CONTROL_UNKNOWN/UNKNOWN/root=0`; B-ACK-2 and B-ACK-3 recovered exact active X/H with `root=1`; automatic launch retries `0` | PASS |
| P4 | concurrent same-X/same-H duplicate safety | one request created; loser reconciled existing match; one independent physical root | PASS |
| P5 | same X with different H | `IDENTITY_CONFLICT`; no replacement/relaunch/destructive stop | PASS |
| P6 | very-short execution recovery | exact `TERMINAL_MATCH`; one physical root | PASS |
| P7 | terminal recovery after transient-unit GC/unload | current unit no longer authoritative; persistent journal recovered exact X/H terminal fact | PASS |
| P8 | positive non-acceptance distinct from ambiguous absence | `DEFINITE_NON_ACCEPTANCE` path separated from `CONTROL_UNKNOWN`; pre-dispatch supervisor count `0`; ambiguous replay count `0` | PASS |
| P9 | stop/natural-exit race convergence | stop-first, natural-exit-first, near-concurrent all converged to `TERMINAL_MATCH`; each root count `1` | PASS |
| P10 | fresh-controller recovery after controller-down exit | `TERMINAL_MATCH`; `evidence_source=journal`; exit code `29`; one physical root | PASS |
| P11 | host reboot fail-closed / no resurrection | boot changed; root count stayed `1`; no same-X relaunch; no verify launch; retained exact X/H journal evidence; corrected verification returned `TERMINAL_MATCH` | PASS |
| P12 | Candidate B privilege/security negative suite | all required abuse cases failed closed; workload remained non-root/capability-free | PASS |
| P13 | representative execution semantics | exact runtime/argv/workdir/env/user/group; stdout/stderr journal profile; terminal journal evidence; exit code `31` | PASS |

## 5. P3 acknowledgement-loss detail

Final P3 evidence recorded:

```text
automatic_launch_retries = 0

B-ACK-1
  dispatch_outcome = control_unknown
  observation = unknown
  independent_root_count = 0
  runner_recovered = true
  status = PASS

B-ACK-2
  dispatch_outcome = control_unknown
  observation = active_match
  independent_root_count = 1
  runner_recovered = true
  status = PASS

B-ACK-3
  dispatch_outcome = control_unknown
  observation = active_match
  independent_root_count = 1
  status = PASS
```

The safety property is not “all unknown outcomes become active.” It is that possible acceptance never triggers an automatic second LAUNCH. Acceptance-crossed cases recover the original exact X/H with one physical root.

## 6. P9 stop-race detail

Final P9 evidence recorded:

```text
stop-first
  stop_outcome = control_accepted
  terminal_observation = terminal_match
  independent_root_count = 1

natural-exit-first
  stop_outcome = terminal_match
  terminal_observation = terminal_match
  independent_root_count = 1

near-concurrent
  stop_outcome = control_accepted
  terminal_observation = terminal_match
  independent_root_count = 1
```

The repair required recognizing trusted PID-1 systemd stop-job terminal evidence when the workload is terminated before it can emit its cooperative `probe_terminal` record. The implementation does not invent an exit code or fabricate cancel causality from the stop request itself.

## 7. P10 and P13 representative terminal evidence

Final P10:

```text
observed = terminal_match
evidence_source = journal
exit_code = 29
independent_root_count = 1
```

Final P13:

```text
observed = terminal_match
exit_code = 31
runtime_path_match = true
working_directory_match = true
argv_match = true
environment_match = true
stdout_journal_profile = true
stderr_journal_profile = true
terminal_journal_evidence = true
execution_user_match = true
execution_group_match = true
```

## 8. P11 reboot evidence and retained false negative

Pre-reboot evidence:

```text
phase = reboot_prepare
status = READY_FOR_HUMAN_REBOOT
observed = active_match
dispatch_outcome = created
independent_root_count = 1

prior_boot_id = 7c88af37-1ce9-4d06-a3df-481fba2d50ba
X = labpilot-poc-6ec390f5ede408c447e918fad6303624e6cf7d585037b4ed902ff45ebe635704-3053cb6ac76c521a9782c50d980eb0d7
H = 31ea1c29b0713786b25562233ce28b7bfafb1a3c9dd5ed0344276e27e92ccb8a
prior_root_count = 1
```

Before reboot the exact X/H unit was active and the independent start marker contained exactly one root.

After human reboot:

```text
current_boot_id = 538aa523-d757-4732-811a-8e938b3bb450
Runner = active
marker line count = 1
```

The first `reboot-verify` record is intentionally retained:

```text
boot_changed = true
independent_root_count_unchanged = true
automatic_relaunch = false
observation = terminal_match
reboot_verify_launch_count = 0
pre_reboot_journal_record_retained = false
status = FAIL
```

This was a verifier false negative. The verifier required the non-root controller process to invoke `journalctl` directly even though the selected Candidate B privilege model intentionally denies direct controller journal access.

The corrected verifier used the privileged Runner observation path for the same physical reboot, same X/H, same persisted metadata, and same one-root marker. It did not issue another LAUNCH or another reboot.

Corrected record:

```text
boot_changed = true
independent_root_count_unchanged = true
automatic_relaunch = false
observation = terminal_match
pre_reboot_journal_record_retained = true
reboot_verify_launch_count = 0
status = PASS
```

The first FAIL must remain in the audit trail; it is not to be deleted or rewritten.

## 9. P12 Candidate B negative security suite

The final clean native P12 campaign passed the required negative checks, including:

- unauthorized peer cannot use the Runner socket;
- `labpilot-train` cannot use the Runner socket;
- controller cannot directly perform privileged systemd creation;
- arbitrary root execution cannot be injected;
- arbitrary unit/property selection cannot be injected;
- path escape is rejected;
- supplied H/spec mismatch is rejected before supervisor interaction;
- arbitrary service stop is impossible through the protocol;
- wrong-H stop is non-destructive;
- malformed and oversized protocol input is rejected without wedging the Runner;
- acknowledgement loss does not create a duplicate physical root;
- capability injection is impossible under the fixed profile;
- Runner policy/code roots remain root-owned and not writable by controller/train identities.

Representative workload security evidence observed:

```text
euid = labpilot-train identity
egid = labpilot-train identity
CapEff = 0
CapBnd = 0
CapAmb = 0
direct privileged systemd action = denied
Runner socket access from workload = denied
Runner policy write = denied
```

## 10. Harness defects discovered during native execution

The audit history intentionally preserves several failures that were defects in evidence collection rather than violations of the target physical invariant.

### 10.1 Start-marker sampling race

P2, P3/B-ACK-3, and P4 exposed the same timing class: systemd could report `ACTIVE_MATCH` before the independent userspace marker had been sampled by the harness. Immediate marker counting could therefore report `0`, after which cleanup killed the execution; later inspection showed exactly one fsynced marker.

The fix was bounded waiting for the first marker where a start witness was required. The final count remained capable of detecting `>1` duplicate roots.

### 10.2 P9 non-cooperative stop terminal evidence

The original journal adapter recognized only the workload's cooperative `probe_terminal` record. `systemctl stop` can terminate the workload before that record is emitted. Native journal evidence showed exact PID-1 `Stopping` / `Stopped` lifecycle records bound to X/H. The adapter was corrected to treat trusted exact systemd stop completion as terminal physical evidence without inventing an exit code.

### 10.3 P11 verifier privilege-model violation

The original reboot verifier separately attempted direct controller-side journal access after the privileged Runner had already recovered the exact terminal observation. That contradicted Candidate B's deliberate privilege split and caused a false negative. The corrected verifier evaluates retained journal evidence through the Runner observation result.

## 11. Campaign chronology

Physical-dispatch scenarios were never blindly rerun in the same namespace after a failure. A new campaign namespace/state/evidence root was created whenever a harness change required a fresh physical execution.

```text
final1
  P0/P12/P1 passed
  P2 exposed start-marker sampling race

final2
  P0/P12/P1/P2 passed
  P3/B-ACK-3 exposed same timing class

final3
  P0/P12/P1/P2/P3 passed
  P4 exposed same timing class

final4
  P0-P8 passed
  P9 exposed stop terminal-evidence model gap

final5
  P0-P10/P12/P13 passed
  P11 completed one human reboot
  first reboot verifier false-negative retained
  corrected verifier passed on the same reboot without new launch
```

Persistent journal evidence was not cleared as part of these campaigns.

## 12. Final code/evidence identity

Final repository baseline used for the corrected P11 verifier:

```text
4109afa9f871ae45cf32c301040bcff6f109939d
```

Final Candidate B Git bundle SHA-256:

```text
cb63d84b2c3604b32fdf18905302a762aa5e5761a50fef44e4cd7814f2065186
```

Final native evidence archive:

```text
file:
labpilot-s2-final5-COMPLETE-20260816T160825Z.tar.gz

SHA-256:
cf6537da2eac2857175d5ccbae6ab07a7420c325c66e8154bef3897a72c90990
```

The archive was copied to a separate Windows machine and independently hashed there. The Windows SHA-256 matched the VM hash exactly.

## 13. Local deterministic verification boundary

For final commit `4109afa`, fresh local verification in the available assistant environment recorded:

```text
S2 Runner PoC unit tests: 109 passed
unit + architecture subset: 640 passed
compileall: PASS
git diff --check: PASS
bundle verification: complete history / okay
```

The assistant environment used Python 3.13 while the project declares Python `<3.13`, and lacked the repository's DB drivers plus Ruff/mypy executables. Therefore this report does not claim a fresh full-repository Ruff/mypy/full-unit certification for `4109afa` from that environment. Native S2.0 acceptance is based on the dedicated VM evidence above, not on extrapolating unsupported local tooling claims.

## 14. Governance/documentation gap

The frozen r2 plan states that Candidate B daemon implementation required a focused plan amendment. Git history at the final S2.0 baseline does not show a separate committed pre-implementation Candidate-B plan-amendment document before Candidate B admission/implementation.

This report does not conceal or retroactively repair that fact. The native evidence remains valid evidence of the physical/security properties that were actually exercised, while the missing pre-implementation amendment remains an audit/governance gap. The post-S2.0 handoff therefore requires strict pre-approval of the Tasks 2–5 execution-precision amendment before any production code is changed.

## 15. Evidence conclusion

The native evidence supports the eight S2.0 facts required by the design:

```text
F1 stable execution identity                         PASS
F2 safe create/query/stop control                   PASS
F3 controller-death independence                    PASS
F4 fresh-controller rediscovery                     PASS
F5 ack-loss/replay/conflict duplicate safety        PASS
F6 terminal X/H retention across restart + unit GC  PASS
F7 stop/natural-exit convergence                    PASS
F8 reboot fail-closed without same-X resurrection   PASS
permission boundary                                 PASS
environment/runtime propagation                     PASS
```

Disposition supported by this evidence:

```text
Candidate A = REJECT_A
Candidate B = SELECT_B
```
