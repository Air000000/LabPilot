# LabPilot V1 Slice 2 — Candidate B v2 TerminalReceiptV1 Mechanism Evidence

**Evidence status:** Accepted bounded Step 0 mechanism proof

**External mechanism review disposition:** `ACCEPT`

**Critical findings:** `0`

**Important findings:** `0`

**Reviewed Step 0 implementation baseline:** `5acc0e8a321c5ec68eef07b61bad3d9fd64d8b01`

**Accepted Candidate B v2 architecture:** `docs/superpowers/specs/2026-08-24-labpilot-v1-slice2-terminal-receipt-adr-amendment.md`

**Current Slice 2 implementation plan:** `docs/superpowers/plans/2026-08-13-labpilot-v1-slice2-recovery.md`

**Authoritative native evidence root:** `/var/lib/labpilot-s2-evidence/terminal-receipt-m1`

This document is a bounded review summary for Internal Step 0 only. It records the native systemd-259 mechanism evidence required before Candidate B v2 production semantics may depend on the selected `ExecStopPost` mechanism. It does not accept Task 5, G4, or Slice 2 and does not authorize Internal Step 1, production implementation, the V2 acceptance matrix, the formal F campaign, or F18.

The raw evidence directory and its generated `SHA256SUMS` file remain the source of truth for the retained native payloads. The final run retained 30 payload files, and the execution report records a successful `sha256sum -c` verification of the retained set. This bounded summary does not duplicate every full per-file digest.

---

## 1. Review scope

Step 0 exists to prove one narrow mechanism candidate, `M1`, on the dedicated native host before production code is allowed to depend on it.

The admitted mechanism is:

```text
main command:
/usr/bin/env -i <sorted KEY=VALUE workload pairs>
  <absolute admitted runtime> <absolute prepared entrypoint> <fixed argv>

unit properties:
User=labpilot-train
Group=labpilot-train
NoNewPrivileges=yes
AmbientCapabilities=
CapabilityBoundingSet=
UnsetEnvironment=LD_AUDIT LD_DEBUG LD_LIBRARY_PATH LD_PRELOAD PYTHONHOME PYTHONPATH

proof post-stop command:
ExecStopPost=-+/usr/bin/python3 -I -B
  /opt/labpilot-runner-mechanism-proof/libexec/terminal-receipt-probe.py
```

The proof path is test-admin-only and is distinct from the future production ABI path:

```text
/opt/labpilot-runner/libexec/labpilot-terminal-receipt-v1.py
```

The production path was not created or mutated by Step 0.

The reviewed repository commit adds exactly:

```text
tests/poc/s2_runner/terminal_receipt_v2_probe.py
tests/unit/s2_runner_poc/test_terminal_receipt_v2_probe.py
```

No production file, ADR, accepted plan, deployment file, or acceptance harness file is part of `5acc0e8a321c5ec68eef07b61bad3d9fd64d8b01`.

---

## 2. Native host

The final proof ran on the dedicated Candidate B host:

```text
SSH identity: air@192.168.88.130
hostname:     labpilot-s2
systemd:      systemd 259 (259.5-0ubuntu3.4)
```

The native command was:

```bash
sudo -- uv run python tests/poc/s2_runner/terminal_receipt_v2_probe.py \
  --evidence-dir /var/lib/labpilot-s2-evidence/terminal-receipt-m1
```

The proof used the already-authorized interactive administrator path. No credential is part of the repository or retained evidence.

---

## 3. TDD and focused regression evidence

The Step 0 implementation was developed through repeated RED/GREEN cycles. The final bounded redesign added tests covering:

- optional `systemd-run` invocation suffix;
- strict journal cursor parsing;
- fixed `--after-cursor` binding;
- exclusion of historical same-unit lifecycles before the barrier;
- bounded journal record count with overflow sentinel;
- trusted PID1 Started-record authority;
- manager Started-record cardinality;
- receipt inability to self-establish expected invocation;
- exact manager-to-reporter invocation correlation;
- boot UUID normalization with raw-value preservation;
- anti-spoof correlation under the same manager invocation.

The reported final verification for the Step 0 test file was:

```text
57 passed
```

The reported focused regression was:

```text
166 passed
```

The two Step 0 files also passed Ruff:

```text
All checks passed!
```

No GitHub status checks were attached to the Step 0 commit at review time; the test and native results above are execution evidence from the authorized Step 0 run, not CI-derived claims.

---

## 4. Evidence acquisition model proven by the final run

The final probe does not treat human-readable `systemd-run` text as lifecycle identity authority.

The evidence split is:

```text
systemd-run --wait synchronous report
= main physical terminal result used for failure-neutrality comparison

trusted PID1 Started ManagerEvidence
= expected systemd invocation + boot for the current lifecycle

root reporter journald record
= TerminalReceipt mechanism provenance + systemd-provided terminal variables

main-process evidence
= UID/GID + NoNewPrivs + capabilities + workload environment canaries

pre-launch journal cursor
= acquisition window only; not identity authority
```

Each control captures its own journal cursor before launch. Only records after that cursor are eligible for that control. This prevents historical records from earlier executions of the same fixed throwaway unit name from entering the current proof.

ManagerEvidence requires an exact bounded Started witness with trusted PID1/systemd provenance. The final implementation requires exactly one admitted Started record and takes the expected lifecycle invocation from its `INVOCATION_ID`.

ReceiptEvidence is then admitted only when its journal-supplied `_SYSTEMD_INVOCATION_ID` matches the manager invocation and its normalized boot matches the manager and current `/proc` boot. ReceiptEvidence therefore cannot provide its own expected correlation identity.

The final correlation remains:

```text
exact unit
+ exact trusted manager invocation
+ exact normalized boot
```

This matches the Accepted Candidate B v2 authority ordering: ManagerEvidence establishes lifecycle identity; ReceiptEvidence contributes terminal-cause evidence only after correlation.

---

## 5. Historical Step 0 probe defects retained as non-M1 findings

Three incomplete native attempts occurred before the accepted final acquisition model. They are retained as probe-design findings rather than rewritten as M1 failures.

### Attempt 1 — post-return transient-unit GC race

The initial probe required `systemctl show` after `systemd-run --wait`. The successful transient unit could already be unloaded by that point, so authoritative current-unit properties were unavailable.

The correction made the bounded synchronous `systemd-run --wait` terminal report authoritative for the Step 0 failure-neutrality comparison. Post-return unit properties became diagnostic only.

### Attempt 2 — boot UUID textual representation

The host exposed the same boot UUID as:

```text
/proc:
538aa523-d757-4732-811a-8e938b3bb450

journald _BOOT_ID:
538aa523d7574732811a8e938b3bb450
```

The probe originally compared raw strings. The correction strictly admits only hyphenated UUID or compact 32-hex input, validates with the standard UUID parser, normalizes to lowercase 32-hex for comparison, and preserves the raw values in evidence.

### Attempt 3 — optional human-readable invocation suffix

On one systemd-259 execution, stderr contained:

```text
Running as unit: labpilot-terminal-receipt-m1-exit0.service
```

instead of the previously observed optional form containing `; invocation ID: ...`.

The final redesign removed that presentation text from the identity path. The expected invocation now comes from trusted PID1 ManagerEvidence, and ReceiptEvidence matches it through `_SYSTEMD_INVOCATION_ID`.

The incomplete evidence lineages were preserved separately as:

```text
/var/lib/labpilot-s2-evidence/terminal-receipt-m1-attempt1-incomplete-d603c248
/var/lib/labpilot-s2-evidence/terminal-receipt-m1-attempt2-incomplete-d603c248
/var/lib/labpilot-s2-evidence/terminal-receipt-m1-attempt3-incomplete-d603c248
```

The execution report records byte-preserving archive moves for the retained payload sets.

---

## 6. Final Control A — reporter exit 0

Control A used:

```text
unit:
labpilot-terminal-receipt-m1-exit0.service

journal cursor:
s=4c208ddbd6ac40ebbf4a3aac7ba5b609;i=3c2c;b=538aa523d7574732811a8e938b3bb450;m=1687a16b40;t=659be4793b4c1;x=239f47332fc504aa

trusted manager INVOCATION_ID:
deddeb063f2a48bcbbba6b331841b799

root reporter _SYSTEMD_INVOCATION_ID:
deddeb063f2a48bcbbba6b331841b799
```

Boot evidence was:

```text
/proc raw:
538aa523-d757-4732-811a-8e938b3bb450

manager raw:
538aa523d7574732811a8e938b3bb450

reporter raw:
538aa523d7574732811a8e938b3bb450

normalized:
538aa523d7574732811a8e938b3bb450
```

The synchronous main result was:

```text
service result: success
main code:      exited
main status:    0
systemd-run rc: 0
```

Main security evidence was:

```text
UID/GID:   995/982
NoNewPrivs: 1
CapPrm:    0000000000000000
CapEff:    0000000000000000
CapAmb:    0000000000000000
```

The main process observed the admitted `LD_LIBRARY_PATH` and `PYTHONPATH` canaries. The root post-stop reporter did not observe a loader/import canary.

Trusted reporter provenance was:

```text
_UID=0
_GID=0
```

The byte-identical workload spoof carried non-root journal provenance:

```text
_UID=995
_GID=982
```

and was not admitted as authority.

The systemd-provided terminal variables at the root reporter were:

```text
SERVICE_RESULT=success
EXIT_CODE=exited
EXIT_STATUS=0
```

---

## 7. Final Control B — reporter exit 71

Control B used the same M1 mechanism but made the post-stop reporter return `71` after emitting its bounded record.

```text
unit:
labpilot-terminal-receipt-m1-exit71.service

journal cursor:
s=4c208ddbd6ac40ebbf4a3aac7ba5b609;i=3c32;b=538aa523d7574732811a8e938b3bb450;m=168802e89c;t=659be47f5321c;x=9b644f304b91e090

trusted manager INVOCATION_ID:
3f73b9a309a54c4cbb7cec5f3cccbafe

root reporter _SYSTEMD_INVOCATION_ID:
3f73b9a309a54c4cbb7cec5f3cccbafe
```

Boot evidence was:

```text
/proc raw:
538aa523-d757-4732-811a-8e938b3bb450

manager raw:
538aa523d7574732811a8e938b3bb450

reporter raw:
538aa523d7574732811a8e938b3bb450

normalized:
538aa523d7574732811a8e938b3bb450
```

The synchronous main result remained:

```text
service result: success
main code:      exited
main status:    0
systemd-run rc: 0
```

Main security evidence remained:

```text
UID/GID:   995/982
NoNewPrivs: 1
CapPrm:    0000000000000000
CapEff:    0000000000000000
CapAmb:    0000000000000000
```

The workload canaries remained available to the non-root main process, while the root reporter again observed no loader/import canary.

Trusted reporter provenance remained:

```text
_UID=0
_GID=0
```

The byte-identical workload spoof remained non-root and non-authoritative.

The systemd-provided terminal variables remained:

```text
SERVICE_RESULT=success
EXIT_CODE=exited
EXIT_STATUS=0
```

---

## 8. Failure-neutrality result

The decisive M1 comparison is:

| Fact | reporter exit 0 | reporter exit 71 |
|---|---|---|
| synchronous service Result | `success` | `success` |
| synchronous main code | `exited` | `exited` |
| synchronous main status | `0` | `0` |
| `systemd-run` return code | `0` | `0` |
| systemd `SERVICE_RESULT` | `success` | `success` |
| systemd `EXIT_CODE` | `exited` | `exited` |
| systemd `EXIT_STATUS` | `0` | `0` |
| root reporter provenance | yes | yes |
| loader/import canary seen by reporter | no | no |

Therefore the final native evidence establishes:

```text
reporter_failure_neutral = true
```

The privileged `ExecStopPost` reporter may return nonzero without changing the authoritative natural-zero main workload result under the tested systemd-259 M1 mechanism.

Post-return, both throwaway units were already collectible/unloaded:

```text
not-found / inactive / dead
```

That does not invalidate the proof because synchronous terminal evidence and bounded post-cursor ManagerEvidence/ReceiptEvidence were complete before review.

---

## 9. Production non-mutation

The production Runner fingerprint before and after the final native proof was identical.

The full reported fingerprint SHA-256 was:

```text
3fe28f3932be58d31a7b6d1b0ad906ea5ab9dc59f392b755e5967e0f89128b54
```

before and after the proof.

The production Runner remained:

```text
active / running
InvocationID=eac53d5c8a9a48c18533f2d7dbee62e6
```

The proof root was removed during cleanup, the raw evidence was retained, and the production `/opt/labpilot-runner` tree was not replaced or reconfigured.

---

## 10. Raw evidence lineage

The final evidence root is:

```text
/var/lib/labpilot-s2-evidence/terminal-receipt-m1
```

The retained set includes, for both controls, bounded payloads for:

- journal cursor;
- synchronous `systemd-run` stdout/stderr/return metadata;
- parsed wait report;
- main-process security/environment evidence;
- trusted manager records and selected lifecycle;
- reporter records and correlated reporter evidence;
- correlation facts;
- control summary.

It also includes:

- systemd version;
- proof artifact metadata;
- production before/after fingerprints;
- cleanup record;
- final probe result;
- generated `SHA256SUMS`.

The execution report records that all 30 retained payload files passed `sha256sum -c` verification.

Representative reported full digests include:

```text
production-before.json
3fe28f3932be58d31a7b6d1b0ad906ea5ab9dc59f392b755e5967e0f89128b54

production-after.json
3fe28f3932be58d31a7b6d1b0ad906ea5ab9dc59f392b755e5967e0f89128b54
```

The raw directory, not this Markdown summary, is authoritative for the complete per-file manifest.

---

## 11. External mechanism review

Independent repository review of `d603c248bd0f9b29cfa470ed60eed7a2a55aac15..5acc0e8a321c5ec68eef07b61bad3d9fd64d8b01` found exactly two added files, both inside the authorized Step 0 test/probe scope.

The reviewed implementation mechanically preserves the required boundaries:

- M1 command/profile remains fixed rather than caller-selected;
- workload environment enters through non-root `/usr/bin/env -i`;
- the privileged post-stop command remains fixed;
- current-unit GC is not used as a terminal-truth dependency;
- human-readable stderr invocation text is not lifecycle authority;
- trusted PID1 Started evidence establishes invocation;
- receipt invocation must match ManagerEvidence and cannot self-authorize;
- boot IDs are strictly normalized for comparison while raw forms remain preserved;
- zero/multiple manager Started witnesses fail closed;
- journal reads are bounded and cursor-scoped;
- root provenance, non-root spoof rejection, terminal variables, security restrictions, and production non-mutation are all part of the completed native proof.

Review disposition:

```text
Critical:  0
Important: 0
Disposition: ACCEPT
```

No finding requires a change to Candidate B v2 or TerminalReceiptV1 architecture.

---

## 12. Governance consequence

Internal Step 0 is accepted as the bounded native mechanism proof required by the current Candidate B v2 implementation plan.

This acceptance establishes only that production implementation may rely on the tested M1 mechanism subject to the remaining plan steps and later production review.

It does **not** imply any of the following:

```text
Internal Step 1 executed
Candidate B v2 production implementation accepted
H1/H2 complete
Phase H complete
Phase I complete
N1-N7 accepted
V2-A1-V2-A20 accepted
F01-F22 accepted
F18 authorized
G4 accepted
Task 5 accepted
Slice 2 accepted
DOCUMENT-STATUS.md updated
```

The next implementation action, if separately authorized, is Internal Step 1 under the already Accepted Candidate B v2 implementation plan.
