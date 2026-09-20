# LabPilot Experiment Iteration Context

This glossary records the project-specific language used by the post-Core Result-Aware iteration work. It is descriptive vocabulary, not a replacement for the V1 Scope, Architecture 10, or an approved implementation plan.

## Language

**Experiment**:
The durable lineage for one research objective. Proposal revisions and approved ExperimentVersions for later iterations of the same objective remain within the same Experiment.
_Avoid_: treating every follow-up run as a new Experiment

**ExperimentVersion**:
An immutable, human-approved experimental design inside an Experiment. A later version does not mutate or revoke earlier versions.

**BatchSummaryRevision**:
A versioned deterministic summary of one Batch after execution, validation, and result selection have settled sufficiently for summary generation.

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
