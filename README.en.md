# LabPilot — Research Agent Runtime / Harness

[中文](README.md) · [Architecture](docs/interview/research-runtime-preview.md) · [Capabilities and acceptance evidence](docs/superpowers/evidence/research-runtime-preview.md)

LabPilot is a research Agent Runtime / Harness for experiments and algorithm development. It constrains Agent decisions, governs source changes, reconciles long-running execution, and verifies results.

The runtime connects constrained model decisions, independent candidate checks, deterministic proposal verification, human approval, reliable execution, and result validation. This gives research intent, source revisions, execution attempts, and results an explicit chain of evidence.

LabPilot is currently an **Engineering Preview**.

## Capabilities

| Area | Mechanism |
| --- | --- |
| Decision governance | ContextCompiler binds source, tool contracts, and experiment facts. The runtime validates structured decisions, budgets, and data-egress policy. It records the request identity before the model call and stops when the outcome is unknown. |
| Source governance | The core Agent can record an unchecked patch draft in an explicit mode. A separate coding Executor edits a frozen task and validates the candidate in a separate image. An operator reviews, commits, captures, and adopts the resulting Snapshot. |
| Execution governance | A deterministic Verifier checks the proposal. Human approval freezes an ExperimentVersion. Batch expansion yields Trials by parameter and seed. Each Attempt materializes exact source and has a stable execution identity for Coordinator and Linux Runner reconciliation. |
| Result governance | Artifacts, MetricObservations, ValidationReports, TrialResultSelections, and BatchSummaryRevisions record distinct facts. A follow-up AgentRun pins an exact summary revision and can use frozen history and Trial facts. |

## Architecture and permissions

The research chain is **Research goal → ProposalRevision → Verification → Human approval → ExperimentVersion → Batch / Trial / Attempt → Artifacts, validation, selection, summary → Follow-up AgentRun**. PostgreSQL holds authoritative domain state. Process exit and result eligibility are recorded separately. Deterministic validation checks the declared result contract. Research hypotheses require independent evaluation. Infrastructure retry adds an Attempt under the same Trial.

An ordinary research Run has four snapshot-bound, read-only repository tools. Eligible new follow-ups can also read frozen same-experiment history. Ordinary Runs have no code write, shell, approval, or physical execution permissions. Model steps are reserved before calls. Persisted proposal decisions can resume materialization. One bounded correction is available for designated format errors when the response is known to be complete and reported usage can be accounted for. Remote model use requires explicit authorization for context egress, which may include source text.

The separate coding CLI uses [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) in a non-root Docker workspace without network access. Each task freezes the baseline, editable scope, budgets, and acceptance contract. Any remote model call from the worker host requires separate authorization for context egress. A separate container runs trusted, frozen tests against the candidate. Adoption requires human confirmation after acceptance. A core Agent patch draft needs a separate review and handoff. An adopted Snapshot can be used for a manual proposal or an explicit cross-source follow-up. New experiments still require deterministic verification and human approval. The cross-source mechanism passed offline acceptance. Two fixed live proposal attempts failed.

## Key engineering mechanisms

- **Bounded Agent Loop.** Each step checks a single structured decision. Tool contracts, budgets, bound context, and egress policy constrain what the model can read and do.
- **Persist-before-execute.** The runtime reserves a step and records the request identity before calling a model. Separately, it records launch intent and execution identity before starting a process. The first record prevents blind model retries after an unknown outcome. The second supports process reconciliation. Both fail closed on uncertainty. There is no global exactly-once guarantee.
- **Stable Execution Identity.** After a restart, the Coordinator uses the stable identity and available Runner observations to reconcile the original attempt.
- **Immutable ExperimentVersion.** Human approval freezes the exact proposal, source, data, and execution contract. Changes require another verification and approval.
- **Independent Candidate Verification.** A separate environment replays the candidate, checks its scope, and runs trusted, frozen tests. Tests written by the coding Agent provide development feedback.
- **Trial / Attempt Separation.** A parameter and seed combination defines a research condition. Infrastructure retries add physical attempts under that condition.
- **Exact Revision Binding.** Execution uses the approved source snapshot. A follow-up AgentRun stays pinned to its chosen summary revision as new summaries are produced.

## Accepted cases

- **Core Demo.** Real model calls and real CPU logistic-regression training. One of four Trials met the declared threshold. The selected accuracy was 0.941.
- **Deterministic loop and recovery.** A separate two-iteration result-driven test used ScriptedProvider. A live Coordinator restart test reconciled the same Attempt with one physical launch. Live cancellation has its own MVP acceptance record.
- **Controlled coding.** NOTEARS-CPU-001 R6 autonomously made the fixed changes, passed independent checks, and handed off its candidate. R6 was not adopted. The earlier R4 model draft was handed off with human assistance and adopted as `f113119`. NOTEARS-CPU-002 autonomously edited that baseline and passed independent checks. After human confirmation, it was adopted as `dd55cb3`.
- **NOTEARS native experiment.** A human-authored adapter was adopted as `0444d2d`. The formal experiment was human-proposed, deterministically verified, and human-approved. It used one fixed synthetic dataset, one seed, two lambda values, and one Attempt for each of two Trials. There were zero model calls and zero retries. Both conditions had SHD 0 on the fixed workload. This repository does not ship the complete research checkout, data, or original outputs.

The [shared evidence index](docs/superpowers/evidence/research-runtime-preview.md) records the baselines, environments, tests, and limits for these cases.

## Running LabPilot

The stack uses Python 3.12, FastAPI, PostgreSQL 16, SQLAlchemy, Pydantic, Alembic, systemd, and Docker Compose.

| Purpose | Entry point |
| --- | --- |
| Control-plane readiness | Follow the [control-plane commands](README.md#启动与演示) to start PostgreSQL, migration, and the API. The commands isolate the Compose project, ignore the repository `.env`, disable model access, and bind a dynamic API port on `127.0.0.1`. No Runner is required. |
| Full execution environment | Prepare a Linux/systemd host, dedicated identities, paths, socket, and admission policy using the [installer](scripts/install_labpilot_runner_host.sh) and [configuration example](.env.example). Then start the Coordinator. Accepted workloads run on CPU. GPU requirements depend on the workload. |
| Preconfigured Operator demo | The [demo script](scripts/start_interview_operator_demo.sh) serves a prepared developer host. It defaults to the checkout owner and that account’s configuration directory. `LABPILOT_INTERVIEW_USER`, `LABPILOT_INTERVIEW_CONFIG_DIR`, and `LABPILOT_REPOSITORY_ALLOWED_ROOTS` can be set explicitly. `/operator` is bound to a fixed demo project and snapshot. `/ops` can open existing records. |
| Separate coding worker | Prepare Docker, locked dependencies, frozen images, the task, and acceptance configuration. See the [worker guide](tools/coding_worker/README.md). |
| Fixed NOTEARS experiment | Requires the registered 23-file research Snapshot, frozen data and dependencies, and an admitted native profile. See the [proposal builder](scripts/build_notears_proposal.py) and [fixed-case evidence](docs/superpowers/evidence/research-runtime-preview.md#notears-native). |

Use a local Docker engine and a Compose version that supports `--wait`. The Operator uses the configured server-side operator identity and is intended for trusted local access. Keep its loopback binding. The setup and cleanup commands operate on the same temporary project.

[Source](src/labpilot) · [Migrations](migrations) · [Tests](tests) · [Local CI gate](scripts/check_ci.sh) · [CI configuration](.github/workflows/ci.yml)

## Limits

- One Coordinator and one Linux Runner host. There is no global exactly-once guarantee or distributed scheduling. The observed recovery reconciles identity and state after Coordinator restart. Training checkpoint resumption and the full production failure matrix remain unaccepted.
- The Operator is a fixed, single-operator demo. Coding, adoption, and formal NOTEARS admission have separate CLI, API, or host configuration entry points.
- The cases establish bounded engineering workflows. There are no generalizable quantitative conclusions about autonomous research, causal discovery quality, hyperparameter superiority, sustained user adoption, proposal quality, or research productivity.

[Documentation status](DOCUMENT-STATUS.md) · [Domain terminology](CONTEXT.md) · [Domain architecture](docs/labpilot/architecture)

## License

LabPilot currently has no project-level LICENSE or declared open-source license. Third-party components retain their own license terms.
