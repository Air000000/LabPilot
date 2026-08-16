# Slice 2 Runner supervision PoC

This directory is evidence-only infrastructure for the S2.0 supervision decision. It is not a
production Runner package.

## Candidate A rejection

Candidate A is rejected by native P12 evidence:

```text
Ubuntu 26.04 LTS
systemd 259
polkit manage-units exposed exact unit + verb
exact-unit + verb=start authorization still allowed caller-selected User=root / ExecStart
root probe completed as UID 0
Candidate A = REJECTED
Candidate B = SELECTED by the accepted S2.0 ADR
SELECT_B = frozen for downstream Slice 2 planning
```

Do not broaden polkit, add NOPASSWD sudo, use `systemd --user`, or run the controller as root to
make Candidate A pass.

## Candidate B PoC boundary

```text
labpilot-controller (non-root)
        |
        | AF_UNIX + SO_PEERCRED
        v
root-owned narrow Runner
        |
        | fixed Runner-derived systemd profile
        v
systemd system manager
        |
        v
labpilot-train workload (non-root, no capabilities)
```

`H` binds execution identity/integrity; it is not domain launch authorization. The Runner owns no
Experiment/Trial/Attempt authorization, scheduler, retry policy, durable Attempt ledger, or blind
launch-retry authority. Ambiguous launch outcomes recover through `OBSERVE(X,H)`, never automatic
LAUNCH replay.

The initial physical evidence stack is `systemd + persistent journald`. P3/P6/P7/P10 must not end
as `UNKNOWN`; P11 may be `UNKNOWN` only under the already-approved reboot rule and must never
resurrect the same X automatically.

## Native Ubuntu 26.04 runbook

The privileged setup is deliberately manual. Follow:

```text
tests/poc/s2_runner/host/README.md
```

That runbook installs only a root-owned fixed policy, a root-owned local Runner service, the
`labpilot-controller`/`labpilot-train` principals, and persistent PoC roots. The Python harness does
not invoke sudo, edit `/etc`, provision users, or reboot the host.

Run P0 and P12 first. If either fails, Candidate B stops. Only then run the remaining B-dependent
P1-P10/P13 campaign and the explicit human P11 reboot gate.

The completed native campaign disposition is:

```text
SELECT_B
```

The bounded evidence report and ADR are:

```text
docs/superpowers/evidence/2026-08-13-labpilot-v1-slice2-runner-supervision-poc.md
docs/superpowers/specs/2026-08-13-labpilot-v1-slice2-runner-supervision-adr.md
```

This does not authorize production Tasks 2-5. Do not add a Runner DB/receipt ledger, generic
HostPolicy framework, backend registry, network transport, scheduler, or production Task 2-5 code
to the PoC.
