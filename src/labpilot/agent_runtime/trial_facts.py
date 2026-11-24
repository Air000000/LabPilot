"""Counts of pinned Trial records; no statistical inference or ambient queries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from labpilot.agent_runtime.result_context import ResultContext

if TYPE_CHECKING:
    from labpilot.agent_runtime.result_history import HistoryBatchEvidence


class TrialCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total_trials: int = Field(ge=0, strict=True)
    selected_valid: int = Field(ge=0, strict=True)
    excluded: int = Field(ge=0, strict=True)
    distinct_seeds: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def consistent_counts(self) -> Self:
        if (
            self.selected_valid + self.excluded != self.total_trials
            or self.distinct_seeds > self.total_trials
            or bool(self.total_trials) != bool(self.distinct_seeds)
        ):
            raise ValueError("trial_facts_counts_invalid")
        return self


class BatchTrialFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: UUID
    summary_revision_id: UUID
    counts: TrialCounts


class TrialFactView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: Literal["trial-facts-v1"] = "trial-facts-v1"
    source: BatchTrialFacts
    historical_batches: tuple[BatchTrialFacts, ...]
    included_history: TrialCounts
    source_and_included_history: TrialCounts


# ponytail: at most three bounded history batches; no general facts registry.
def _counts(records: tuple[tuple[UUID, int, bool], ...]) -> TrialCounts:
    if len({row[0] for row in records}) != len(records):
        raise ValueError("trial_facts_duplicate_trial")
    selected = sum(row[2] for row in records)
    return TrialCounts(
        total_trials=len(records),
        selected_valid=selected,
        excluded=len(records) - selected,
        distinct_seeds=len({row[1] for row in records}),
    )


def _source_records(source: ResultContext) -> tuple[tuple[UUID, int, bool], ...]:
    outcomes: dict[UUID, tuple[object, bool]] = {}
    for selected, items in ((True, source.included_results), (False, source.excluded_results)):
        for item in items:
            if not selected and item.get("exclusion_code") not in {
                "VALIDATION_INVALID",
                "VALIDATION_INCONCLUSIVE",
                "VALIDATION_ERROR",
                "EXECUTION_EXHAUSTED",
                "EXECUTION_CANCELLED",
            }:
                raise ValueError("trial_facts_outcome_invalid")
            trial_id = UUID(str(item["trial_id"]))
            if trial_id in outcomes:
                raise ValueError("trial_facts_duplicate_trial")
            outcomes[trial_id] = (item["trial_ordinal"], selected)
    if (
        len(source.trials) != len(outcomes)
        or {trial.trial_id for trial in source.trials} != outcomes.keys()
        or {trial.trial_ordinal for trial in source.trials} != set(range(1, len(source.trials) + 1))
    ):
        raise ValueError("trial_facts_coverage_invalid")
    if any(outcomes[trial.trial_id][0] != trial.trial_ordinal for trial in source.trials):
        raise ValueError("trial_facts_coverage_invalid")
    return tuple((t.trial_id, t.seed, outcomes[t.trial_id][1]) for t in source.trials)


def source_trial_facts(source: ResultContext) -> BatchTrialFacts:
    return BatchTrialFacts(
        batch_id=source.source_batch_id,
        summary_revision_id=source.source_batch_summary_revision_id,
        counts=_counts(_source_records(source)),
    )


def historical_trial_facts(batch: HistoryBatchEvidence) -> BatchTrialFacts:
    return BatchTrialFacts(
        batch_id=batch.batch_id,
        summary_revision_id=batch.summary_revision_id,
        counts=_counts(
            tuple((t.trial_id, t.seed, t.outcome == "selected_valid") for t in batch.trial_evidence)
        ),
    )


def freeze_trial_facts(
    source: ResultContext, batches: tuple[HistoryBatchEvidence, ...]
) -> TrialFactView:
    source_records = _source_records(source)
    history_records = tuple(
        (t.trial_id, t.seed, t.outcome == "selected_valid")
        for batch in batches
        for t in batch.trial_evidence
    )
    batch_ids = [source.source_batch_id, *(batch.batch_id for batch in batches)]
    summary_ids = [
        source.source_batch_summary_revision_id,
        *(batch.summary_revision_id for batch in batches),
    ]
    if len(set(batch_ids)) != len(batch_ids) or len(set(summary_ids)) != len(summary_ids):
        raise ValueError("trial_facts_scope_invalid")
    return TrialFactView(
        source=source_trial_facts(source),
        historical_batches=tuple(historical_trial_facts(b) for b in batches),
        included_history=_counts(history_records),
        source_and_included_history=_counts(source_records + history_records),
    )
