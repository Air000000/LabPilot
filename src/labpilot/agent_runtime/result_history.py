"""Frozen, bounded evidence; execution reads no database or ambient state."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from labpilot.agent_runtime.result_context import ResultContext, ResultTrialCondition
from labpilot.agent_runtime.trial_facts import (
    TrialFactView,
    freeze_trial_facts,
    historical_trial_facts,
)
from labpilot.agent_runtime.types import AgentRunDetails, AgentRuntimeError, _require_canonical_json
from labpilot.common.canonical_json import canonical_json_bytes
from labpilot.experiments.proposal_types import (
    ArtifactDeclaration,
    DatasetReference,
    EnvironmentExpectation,
    MetricDeclaration,
    ValidationRule,
    validation_protocol_hash,
)
from labpilot.tools_policy.types import ToolDefinition, _canonical_value

MAX_HISTORY_BYTES = 32 * 1024
MAX_HISTORY_INPUT_BYTES = 128 * 1024
MAX_HISTORY_BATCHES = 3
MAX_HISTORY_TRIALS = 20


class _Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HistoryValidation(_Evidence):
    validation_report_id: UUID
    validation_protocol_hash: str
    extraction_profile_version: str
    validator_version: str


class HistoryMetric(_Evidence):
    metric_name: str
    split: str | None
    numeric_value: str
    artifact_id: UUID
    metric_observation_id: UUID
    extractor_version: str

    @field_validator("numeric_value")
    @classmethod
    def finite_number(cls, value: str) -> str:
        try:
            if not Decimal(value).is_finite():
                raise ValueError("history metric must be finite")
        except InvalidOperation as exc:
            raise ValueError("history metric must be numeric") from exc
        return value


class _HistoryTrial(ResultTrialCondition):
    @field_validator("resolved_parameters")
    @classmethod
    def finite_parameters(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _require_canonical_json(value)
        return value


class HistorySelectedTrial(_HistoryTrial):
    outcome: Literal["selected_valid"]
    selected_attempt_id: UUID
    validation: HistoryValidation
    metrics: tuple[HistoryMetric, ...]


class HistoryExcludedTrial(_HistoryTrial):
    outcome: Literal["excluded"]
    exclusion_code: Literal[
        "VALIDATION_INVALID",
        "VALIDATION_INCONCLUSIVE",
        "VALIDATION_ERROR",
        "EXECUTION_EXHAUSTED",
        "EXECUTION_CANCELLED",
    ]
    validation_reason_codes: tuple[str, ...] = ()
    validation: HistoryValidation | None = None


class HistoryDesign(_Evidence):
    repository_snapshot_id: UUID
    repository_snapshot_identity: str
    dataset_reference_hash: str
    dataset_reference: DatasetReference
    environment_expectation_hash: str
    environment_expectation: EnvironmentExpectation
    validation_protocol_hash: str
    train_entrypoint: str
    eval_entrypoint: str | None
    workdir: str
    train_argv_template: tuple[str, ...] | None
    metrics: tuple[MetricDeclaration, ...]
    artifacts: tuple[ArtifactDeclaration, ...]
    # Missing in older captures: preserve their output; never reconstruct from current state.
    validation_rules: tuple[ValidationRule, ...] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def exact_validation_protocol(self) -> Self:
        if (
            self.validation_rules is not None
            and validation_protocol_hash(self.validation_rules) != self.validation_protocol_hash
        ):
            raise ValueError("history_validation_protocol_invalid")
        return self


class HistoryWarning(_Evidence):
    trial_id: UUID
    trial_ordinal: int = Field(gt=0)
    attempt_id: UUID
    validation_report_id: UUID
    code: str
    subject: str
    details: dict[str, JsonValue]


class HistoryBatchEvidence(_Evidence):
    project_id: UUID
    experiment_id: UUID
    experiment_version_id: UUID
    version_number: int = Field(gt=0)
    proposal_revision_id: UUID
    batch_id: UUID
    batch_created_at: datetime
    summary_revision_id: UUID
    summary_revision_number: int = Field(gt=0)
    summary_protocol_version: str
    trial_count: int = Field(ge=1, le=MAX_HISTORY_TRIALS)
    design: HistoryDesign
    trial_evidence: tuple[
        Annotated[HistorySelectedTrial | HistoryExcludedTrial, Field(discriminator="outcome")], ...
    ]
    warnings: tuple[HistoryWarning, ...]

    @model_validator(mode="after")
    def complete_trials(self) -> Self:
        if (
            len(self.trial_evidence) != self.trial_count
            or len({trial.trial_id for trial in self.trial_evidence}) != self.trial_count
            or {trial.trial_ordinal for trial in self.trial_evidence}
            != set(range(1, self.trial_count + 1))
            or self.batch_created_at.tzinfo is None
        ):
            raise ValueError("history_trial_coverage_invalid")
        by_id = {trial.trial_id: trial for trial in self.trial_evidence}
        for condition in self.trial_evidence:
            if (
                condition.validation is not None
                and condition.validation.validation_protocol_hash
                != self.design.validation_protocol_hash
            ):
                raise ValueError("history_validation_protocol_invalid")
        for warning in self.warnings:
            trial = by_id.get(warning.trial_id)
            if (
                trial is None
                or trial.trial_ordinal != warning.trial_ordinal
                or trial.validation is None
                or trial.validation.validation_report_id != warning.validation_report_id
                or (
                    isinstance(trial, HistorySelectedTrial)
                    and trial.selected_attempt_id != warning.attempt_id
                )
            ):
                raise ValueError("history_warning_lineage_invalid")
        return self


class HistoryOmission(_Evidence):
    batch_id: UUID
    summary_revision_id: UUID
    reason: Literal["trial_limit", "input_bytes_limit", "history_bytes_limit"]


class HistoryContext(_Evidence):
    schema_version: Literal["history-context-v1"] = "history-context-v1"
    selection_policy_version: Literal["same-experiment-history-v1"] = "same-experiment-history-v1"
    project_id: UUID
    experiment_id: UUID
    source_batch_summary_revision_id: UUID
    source_batch_id: UUID
    source_version_number: int = Field(gt=0)
    source_batch_created_at: datetime
    batches: tuple[HistoryBatchEvidence, ...] = Field(default=(), max_length=MAX_HISTORY_BATCHES)
    omissions: tuple[HistoryOmission, ...] = Field(default=(), max_length=MAX_HISTORY_BATCHES)
    has_more_candidates: bool = False
    coverage_limited: bool = False
    # Absent on legacy captures: retain their serialized value and request hashes.
    trial_facts: TrialFactView | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def validate_envelope(self) -> Self:
        if self.source_batch_created_at.tzinfo is None:
            raise ValueError("history_cutoff_invalid")
        if self.coverage_limited != bool(self.has_more_candidates or self.omissions):
            raise ValueError("history_coverage_metadata_invalid")
        ids = [batch.batch_id for batch in self.batches] + [
            item.batch_id for item in self.omissions
        ]
        if len(ids) > MAX_HISTORY_BATCHES or len(set(ids)) != len(ids):
            raise ValueError("history_candidates_invalid")
        for batch in self.batches:
            if (
                batch.project_id != self.project_id
                or batch.experiment_id != self.experiment_id
                or batch.version_number > self.source_version_number
                or batch.batch_id == self.source_batch_id
                or (batch.batch_created_at, batch.batch_id)
                >= (self.source_batch_created_at, self.source_batch_id)
            ):
                raise ValueError("history_scope_invalid")
        order = [(b.version_number, b.batch_created_at, b.batch_id) for b in self.batches]
        if order != sorted(order, reverse=True):
            raise ValueError("history_order_invalid")
        if self.trial_facts is not None:
            facts = self.trial_facts
            if (
                facts.source.batch_id != self.source_batch_id
                or facts.source.summary_revision_id != self.source_batch_summary_revision_id
                or facts.historical_batches
                != tuple(historical_trial_facts(b) for b in self.batches)
            ):
                raise ValueError("trial_facts_binding_invalid")
            records = [t for b in self.batches for t in b.trial_evidence]
            if (
                len({t.trial_id for t in records}) != len(records)
                or facts.included_history.total_trials != len(records)
                or facts.included_history.selected_valid
                != sum(t.outcome == "selected_valid" for t in records)
                or facts.included_history.distinct_seeds != len({t.seed for t in records})
            ):
                raise ValueError("trial_facts_binding_invalid")
            for field in ("total_trials", "selected_valid", "excluded"):
                if getattr(facts.source_and_included_history, field) != (
                    getattr(facts.source.counts, field) + getattr(facts.included_history, field)
                ):
                    raise ValueError("trial_facts_binding_invalid")
        value = self.model_dump(mode="json")
        _require_canonical_json(value)
        if len(canonical_json_bytes(_canonical_value(value))) > MAX_HISTORY_BYTES:
            raise ValueError("history_context_too_large")
        return self


def validate_history_binding(
    run: AgentRunDetails, source: ResultContext | None
) -> HistoryContext | None:
    if run.history_context is None:
        return None
    try:
        history = HistoryContext.model_validate(run.history_context)
    except (TypeError, ValueError) as exc:
        raise AgentRuntimeError("history_context_invalid") from exc
    if (
        source is None
        or history.project_id != run.project_id
        or history.source_batch_summary_revision_id != run.source_batch_summary_revision_id
        or history.source_batch_summary_revision_id != source.source_batch_summary_revision_id
        or history.experiment_id != source.experiment_id
        or history.source_batch_id != source.source_batch_id
        or history.source_version_number != source.source_version_number
    ):
        raise AgentRuntimeError("history_context_binding_invalid")
    if history.trial_facts is not None:
        try:
            if history.trial_facts != freeze_trial_facts(source, history.batches):
                raise ValueError("trial_facts_binding_invalid")
        except (KeyError, TypeError, ValueError) as exc:
            raise AgentRuntimeError("trial_facts_binding_invalid") from exc
    return history


def history_tool_definition(history: HistoryContext) -> ToolDefinition:
    # Keep an immutable serialized value; callers receive independent nested objects.
    payload = history.model_dump_json()

    async def execute(arguments: Mapping[str, object]) -> Mapping[str, object]:
        if arguments:
            raise ValueError("history tool accepts only {}")
        return HistoryContext.model_validate_json(payload).model_dump(mode="json")

    return ToolDefinition(
        name="experiment.read_history",
        version="v2" if history.trial_facts is not None else "v1",
        risk="green",
        side_effect="read_only",
        approval_required=False,
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
            "x-labpilot-source-summary-revision-id": str(history.source_batch_summary_revision_id),
            "x-labpilot-history-summary-revision-ids": [
                str(b.summary_revision_id) for b in history.batches
            ],
        },
        executor=execute,
    )
