# Slice 2 runner supervision PoC

This harness is evidence-only test infrastructure. Run deterministic tests anywhere supported by
the project; run P0–P13 only on the intended native Linux + systemd host. WSL2 cannot supply the
authoritative reboot evidence.

Use persistent, non-secret roots. Do not use `/tmp` or `/run` for P11:

```bash
export LABPILOT_POC_STATE="$HOME/.local/state/labpilot-s2-poc/state"
export LABPILOT_POC_EVIDENCE="$HOME/.local/state/labpilot-s2-poc/evidence"
export LABPILOT_POC_NAMESPACE="labpilot-s2-poc-native"
mkdir -p "$LABPILOT_POC_STATE" "$LABPILOT_POC_EVIDENCE"
```

The harness always targets the system manager. It never invokes `sudo`, changes host policy,
switches to `systemd --user`, or prompts for a password. Run P0 first:

```bash
uv run python -m tests.poc.s2_runner.scenario_runner \
  --state-root "$LABPILOT_POC_STATE" \
  --evidence-root "$LABPILOT_POC_EVIDENCE" \
  --installation-namespace "$LABPILOT_POC_NAMESPACE" \
  --evidence-stack systemd characterize
```

Stop if P0 prints `HOST_CAPABILITY_GAP`. After P0 passes, run the A1 baseline:

```bash
for scenario in p1 p2 p12 p13 p3 p4 p5 p8 p6 p7 p10; do
  uv run python -m tests.poc.s2_runner.scenario_runner \
    --state-root "$LABPILOT_POC_STATE" \
    --evidence-root "$LABPILOT_POC_EVIDENCE" \
    --installation-namespace "$LABPILOT_POC_NAMESPACE" \
    --evidence-stack systemd "$scenario" || break
done
```

Proceed to A2 only if A1's sole gap is terminal X/H retention after exit/unit GC. Verify persistent
journald on the target host, then run:

```bash
for scenario in p6 p7 p10 p9; do
  uv run python -m tests.poc.s2_runner.scenario_runner \
    --state-root "$LABPILOT_POC_STATE" \
    --evidence-root "$LABPILOT_POC_EVIDENCE" \
    --installation-namespace "$LABPILOT_POC_NAMESPACE" \
    --evidence-stack systemd+journal "$scenario" || break
done
```

P11 is deliberately split. Prepare and stop at the printed evidence path:

```bash
uv run python -m tests.poc.s2_runner.scenario_runner \
  --state-root "$LABPILOT_POC_STATE" \
  --evidence-root "$LABPILOT_POC_EVIDENCE" \
  --installation-namespace "$LABPILOT_POC_NAMESPACE" \
  --evidence-stack systemd+journal reboot-prepare
```

The human operator now reboots the native host. After login, run only:

```bash
uv run python -m tests.poc.s2_runner.scenario_runner \
  --state-root "$LABPILOT_POC_STATE" \
  --evidence-root "$LABPILOT_POC_EVIDENCE" \
  --installation-namespace "$LABPILOT_POC_NAMESPACE" \
  --evidence-stack systemd+journal reboot-verify
```

Finally print the bounded evidence table:

```bash
uv run python -m tests.poc.s2_runner.scenario_runner \
  --state-root "$LABPILOT_POC_STATE" \
  --evidence-root "$LABPILOT_POC_EVIDENCE" \
  --installation-namespace "$LABPILOT_POC_NAMESPACE" \
  --evidence-stack systemd+journal summarize
```

Any duplicate independent root, exact X/different H replacement, P3/P6/P7/P10 `UNKNOWN`, unsafe
privilege requirement, or A2 terminal-retention failure is a stop condition. Do not add A3 or
Candidate B without a focused plan amendment.
