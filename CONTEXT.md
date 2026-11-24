# LabPilot Experiment Iteration Context

This glossary defines the language used for experiment iteration and governed source changes. The V1 Scope, Architecture 10, and applicable approved plans define the normative contracts.

## Language

**Experiment**:
The durable lineage for one research objective. Proposal revisions and approved ExperimentVersions for later iterations of the same objective remain within the same Experiment.
_Avoid_: treating every follow-up run as a new Experiment

**ExperimentVersion**:
An immutable, human-approved experimental design inside an Experiment. A later version does not mutate or revoke earlier versions.

**Trial**:
A recorded research condition within an experiment design. Matching parameters and seed do not establish equivalence across source, dataset, or execution environment, and an Attempt retry does not add a Trial.
_Avoid_: physical attempt, statistically independent sample

**BatchSummaryRevision**:
A persisted, versioned deterministic summary of one Batch after execution, validation, and result selection have settled sufficiently for summary generation. There is no separate publish state in the current domain; a persisted revision is already presentable, while "latest" means the highest persisted revision number for that Batch.

**ResultContext**:
A bounded deterministic read model pinned to one exact BatchSummaryRevision and its owning ExperimentVersion. It may include approved design facts, Trial conditions, selected eligible result evidence, exclusions, warnings, and aggregates; it does not turn raw logs or invalid metric values into scientific authority.
_Avoid_: raw result dump, LLM-generated result facts

**Follow-up AgentRun**:
A new bounded AgentRun created from an exact ResultContext to interpret the completed iteration and either stop, request human input, or propose the next revision. It is not a resumed training process and does not directly advance execution state.
_Avoid_: autonomous experiment loop, long-lived execution Agent

**Follow-up Proposal**:
A ProposalRevision produced by a Follow-up AgentRun and attached to the same Experiment as the source result. It must pass the existing deterministic verification and human-approval path before a new ExperimentVersion can exist.
_Avoid_: auto-approved next experiment, new Experiment per iteration

**Result-Aware Iteration**:
The controlled cycle from an approved ExperimentVersion through validated Batch results into a Follow-up AgentRun and, when warranted, a new ProposalRevision and ExperimentVersion in the same Experiment.
_Avoid_: self-running autonomous research loop

## P1-C vocabulary

**Historical Experiment Comparison**:
Comparison of recorded Trial conditions and eligible results across earlier Batches and ExperimentVersions within the same Experiment, while retaining differences in source, dataset, environment and validation semantics.
_Avoid_: cross-Experiment search, automatic ranking of scientific conclusions

**HistoryContext**:
A bounded set of historical experiment evidence fixed for one Follow-up AgentRun, preserving exact summary revisions, Trial conditions, eligible result references and exclusions. Omission from this set does not mean a condition has never been run.
_Avoid_: global memory, exhaustive experiment history

## Scoped experiment facts

**TrialFactView**:
A frozen view of counts within the current source Batch, included historical Batches, or their named union, tied to exact summary revisions. It counts Trial identities and recorded outcomes without making scientific independence claims.
_Avoid_: LLM-generated experiment facts, complete Experiment census

**Recorded Condition Repetition**:
The appearance of the same recorded experiment condition in different Trials under matching design semantics. It differs from infrastructure retries of one Trial and does not establish statistical independence.
_Avoid_: independent replication, one new sample per Attempt

**Independent Replication**:
A repetition whose experimental design supports an independence assumption relevant to the scientific comparison. Neither the number of Trials nor the number of distinct seed values alone establishes it.
_Avoid_: Trial count as effective statistical sample size

## Source-change vocabulary

**SourceChangeRequest**:
A proposed change to one exact source snapshot, retaining its baseline identity and the requested source difference. It is neither an approved experiment nor an execution authorization.
_Avoid_: approved code, next ExperimentVersion

**CandidateSource**:
An unapproved source content derived from a SourceChangeRequest and its exact baseline. Its content identity remains distinct from the source of prior experimental results.
_Avoid_: silently updated Snapshot, approved source

**SourceCheckReport**:
A recorded technical observation about one CandidateSource under a specified check procedure, distinguishing checks actually performed from checks not performed. It does not establish result eligibility, scientific improvement or human approval.
_Avoid_: ValidationReport, scientifically valid result

**Static candidate**:
A CandidateSource derived under a static source-change contract. Its static check report covers the declared static checks.
_Avoid_: dynamically verified candidate, adopted Snapshot

**Frozen coding task**:
The independent worker's task contract binding the baseline, objective, allowed candidate changes, budgets, and acceptance checks. It is a worker contract outside the Core domain model.
_Avoid_: Core CodingTask entity, Experiment Proposal

**ProjectSourceRequest**:
The recorded source-change request for a worker candidate, tied to a project, a baseline Snapshot, and the frozen task and change evidence.
_Avoid_: adopted source, Experiment Approval

**ProjectCheckReport**:
The Core report recording static checks and the accepted local operator receipt for the worker's dynamic checks. Scientific validation and human approval remain separate.
_Avoid_: Docker rerun by Core, scientific ValidationReport

**ProjectCandidatePackage**:
The Core package that binds the request, candidate manifest, diff, and check report. An independently checked coding candidate retains that evidence while it awaits a human adoption decision.
_Avoid_: static-only candidate, adopted Snapshot

**Operator attestation**:
The trusted local operator's evidence of independent checks for an exact task and candidate. Content hashes support integrity checks, with no cryptographic signer or remote execution attestation.
_Avoid_: third-party signature, unforgeable test evidence

## Source-adoption vocabulary

**SourceAdoption**:
A human record selecting a candidate whose contents match a new RepositorySnapshot, with the baseline and candidate identities preserved. Experiment Approval separately authorizes the experimental design.
_Avoid_: approved ExperimentVersion, safe code, automated source generation


## Cross-source roles

**Result Source**:
The exact source snapshot of the approved experimental design that produced a pinned result summary. Its recorded outcomes retain that source even when a later proposal concerns different code.
_Avoid_: current worktree, proposal target

**Proposal Target Source**:
The exact source snapshot inspected for a prospective experimental design. A different Result Source does not establish outcomes or approval for this target.
_Avoid_: source of earlier metrics, latest source

## Source-patch draft vocabulary

**SourcePatchDraft**:
An unverified suggestion for changing one exact source snapshot. It is distinct from a submitted SourceChangeRequest, a checked CandidateSource and a human SourceAdoption; its existence establishes none of their guarantees.
_Avoid_: checked candidate, approved code, scientific improvement
