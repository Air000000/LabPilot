# Keep follow-up proposals in the same Experiment

Status: Proposed for P1 Result-Aware Iteration review

Result-aware iteration refines one research objective across approved versions. A Follow-up AgentRun therefore creates its ProposalRevision in the Experiment that owns the source BatchSummaryRevision, rather than silently creating a new Experiment for every loop. After the existing deterministic verification and human approval gates pass, the follow-up becomes the next immutable ExperimentVersion of that Experiment.

Creating a new Experiment for each iteration was rejected because it fragments version history and makes historical comparison depend on reconstructing cross-Experiment relationships. Reusing the same Experiment was preferred because the current domain already numbers ProposalRevisions and ExperimentVersions within an Experiment and preserves all prior approved versions.

A new Experiment remains appropriate when the researcher intentionally starts a different research objective. The Follow-up AgentRun itself never decides that ownership boundary implicitly; the result-aware creation path derives the owning Experiment from the exact source BatchSummaryRevision lineage.
