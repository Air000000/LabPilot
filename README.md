# LabPilot — Competition Submission Package

LabPilot is a bounded Agent-assisted experiment runtime / harness for turning a research goal into a traceable, recoverable and verifiable experiment lineage.

## Demonstrated competition flow

```text
Research Goal
→ bounded repository-aware Agent planning
→ structured Proposal
→ deterministic verification
→ human approval
→ immutable ExperimentVersion
→ Batch / Trial / Attempt
→ real CPU training
→ Artifact / Metric / Validation
→ Result Selection
→ Batch Summary
```

The demo workload is included under `demo/competition_workload/repo_template/`.

Reference four-Trial result:

| Trial | epochs | learning_rate | seed | accuracy | verdict |
|---|---:|---:|---:|---:|---|
| 1 | 100 | 0.0005 | 7 | 0.650 | invalid |
| 2 | 100 | 0.005 | 7 | 0.895 | invalid |
| 3 | 1000 | 0.0005 | 7 | 0.895 | invalid |
| 4 | 1000 | 0.005 | 7 | 0.941 | valid |

Target accuracy: `>= 0.92`.

## Reliability evidence boundary

A separate replay of the same immutable ExperimentVersion injected a Coordinator restart while an Attempt was running. LabPilot reconciled to the original physical execution; the Attempt identity, execution identity and systemd invocation remained stable, and the physical start count remained one.

This package does not claim global exactly-once execution or unrestricted autonomous research.

## Runtime

- Python 3.12
- FastAPI
- PostgreSQL 16
- SQLAlchemy / Alembic
- Docker Compose
- Linux systemd Runner
- Server-Sent Events
- configurable remote model provider

## Configuration and privacy

No API key, access token, password, private key, personal email, personal phone number, private host address, local user home path, runtime database dump, log archive or Git metadata is included in this package.

Provider credentials are intentionally absent. Configure them at runtime through environment variables only. The supplied `.env.example` contains placeholders and an empty provider API-key field.

## Quick start

For the API control plane:

```bash
docker compose up --build postgres migrate api
```

For the full physical execution path, a Linux/systemd Runner host is required. See `scripts/install_labpilot_runner_host.sh` and `deploy/systemd/labpilot-runner.service`.

The competition helper scripts are under `scripts/competition/`.
