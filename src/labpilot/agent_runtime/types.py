from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from math import isfinite
from typing import Annotated, Literal, cast, get_args
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError
from pydantic_core.core_schema import ErrorType

from labpilot.agent_runtime.source_binding import SourceBinding
from labpilot.agent_runtime.source_patch import MAX_PATCH_BYTES, SourcePatchContext
from labpilot.experiments.proposal_types import ProposalPayload

SUMMARY_MAX_BYTES = 1000
TOOL_NAME_MAX_BYTES = 100
QUESTION_MAX_BYTES = 4000
TEXT_ERROR_CODES = frozenset({"text_blank", "text_invalid_utf8", "text_too_large", "text_nul"})


class AgentRuntimeError(RuntimeError):
    __slots__ = ("reason_code",)

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _bounded_text(
    value: str,
    *,
    field: str,
    max_bytes: int,
    non_blank: bool,
) -> str:
    if non_blank and not value.strip():
        raise PydanticCustomError("text_blank", "Text must not be blank")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise PydanticCustomError("text_invalid_utf8", "Text must be valid UTF-8") from exc
    if size > max_bytes:
        raise PydanticCustomError("text_too_large", f"Text exceeds {max_bytes} UTF-8 bytes")
    return value


class _Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str | None = Field(
        None,
        description=(
            f"Optional display text; omit or use null. At most {SUMMARY_MAX_BYTES} UTF-8 bytes."
        ),
        json_schema_extra={"maxLength": SUMMARY_MAX_BYTES, "x-max-utf8-bytes": SUMMARY_MAX_BYTES},
    )

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_text(value, field="summary", max_bytes=SUMMARY_MAX_BYTES, non_blank=False)


def _require_canonical_json(value: object) -> None:
    if value is None or type(value) in {bool, int}:
        return
    if type(value) is float:
        if not isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return
    if type(value) is str:
        value.encode("utf-8", "strict")
        return
    if isinstance(value, UUID):
        _require_canonical_json(str(value))
        return
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            _require_canonical_json(getattr(value, field.name))
        return
    if isinstance(value, list | tuple):
        for item in value:
            _require_canonical_json(item)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("JSON object keys must be strings")
            key.encode("utf-8", "strict")
            _require_canonical_json(item)
        return
    raise ValueError(f"unsupported JSON value: {type(value).__name__}")


class ToolCallDecision(_Decision):
    kind: Literal["tool_call"]
    tool_name: str = Field(
        description=f"Non-blank text. At most {TOOL_NAME_MAX_BYTES} UTF-8 bytes.",
        json_schema_extra={
            "maxLength": TOOL_NAME_MAX_BYTES,
            "x-max-utf8-bytes": TOOL_NAME_MAX_BYTES,
        },
    )
    arguments: dict[str, JsonValue]

    @field_validator("tool_name")
    @classmethod
    def validate_tool_name(cls, value: str) -> str:
        return _bounded_text(
            value, field="tool_name", max_bytes=TOOL_NAME_MAX_BYTES, non_blank=True
        )

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _require_canonical_json(value)
        return value


class HumanInputDecision(_Decision):
    kind: Literal["request_human_input"]
    question: str = Field(
        description=f"Non-blank text. At most {QUESTION_MAX_BYTES} UTF-8 bytes.",
        json_schema_extra={"maxLength": QUESTION_MAX_BYTES, "x-max-utf8-bytes": QUESTION_MAX_BYTES},
    )

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        return _bounded_text(value, field="question", max_bytes=QUESTION_MAX_BYTES, non_blank=True)


class ProposeDecision(_Decision):
    kind: Literal["propose"]
    proposal: ProposalPayload

    @field_validator("proposal", mode="before")
    @classmethod
    def validate_proposal(cls, value: object) -> object:
        _require_canonical_json(value)
        return value


class SourcePatchDecision(_Decision):
    kind: Literal["source_patch"]
    patch: str = Field(
        description="Unverified standard Git patch for train.py; at most 32768 UTF-8 bytes.",
        json_schema_extra={"maxLength": MAX_PATCH_BYTES, "x-max-utf8-bytes": MAX_PATCH_BYTES},
    )

    @field_validator("patch")
    @classmethod
    def validate_patch(cls, value: str) -> str:
        _bounded_text(value, field="patch", max_bytes=MAX_PATCH_BYTES, non_blank=True)
        if "\0" in value:
            raise PydanticCustomError("text_nul", "Text must not contain NUL")
        return value


class StopDecision(_Decision):
    kind: Literal["stop"]
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")


type AgentDecision = Annotated[
    ToolCallDecision | HumanInputDecision | ProposeDecision | StopDecision,
    Field(discriminator="kind"),
]


type SourcePatchAgentDecision = Annotated[
    ToolCallDecision | HumanInputDecision | SourcePatchDecision | StopDecision,
    Field(discriminator="kind"),
]


class AgentBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_steps: int = Field(ge=1, le=20)
    max_model_calls: int = Field(ge=1, le=20)
    max_wall_clock_seconds: int = Field(ge=1, le=600)
    max_tokens: int = Field(ge=1, le=200_000)


type AgentRunStatus = Literal[
    "ready",
    "running",
    "awaiting_human",
    "completed",
    "budget_exhausted",
    "failed",
]
type AgentStepKind = Literal["model_call", "human_input"]
type AgentStepOutcome = Literal["reserved", "succeeded", "failed"]


@dataclass(frozen=True, slots=True)
class AgentRunDetails:
    agent_run_id: UUID
    project_id: UUID
    repository_snapshot_id: UUID
    goal: str
    status: AgentRunStatus
    termination_reason: str | None
    egress_policy: Literal["local_only", "remote_provider_allowed"]
    provider_name: str
    model_name: str
    max_steps: int
    max_model_calls: int
    max_wall_clock_seconds: int
    max_tokens: int
    total_model_calls: int
    total_tokens: int
    next_step_ordinal: int
    produced_proposal_revision_id: UUID | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    row_version: int
    source_batch_summary_revision_id: UUID | None = None
    history_context: Mapping[str, object] | None = None
    source_adoption_id: UUID | None = None
    source_patch_context: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class AgentStepDetails:
    agent_step_id: UUID
    agent_run_id: UUID
    ordinal: int
    step_kind: AgentStepKind
    tool_call_id: UUID | None
    provider_name: str | None
    model_name: str | None
    canonical_request_hash: str | None
    bound_toolset_hash: str | None
    semantic_parameters: Mapping[str, object] | None
    output_schema_hash: str | None
    started_at: datetime
    completed_at: datetime | None
    outcome: AgentStepOutcome
    token_usage: Mapping[str, object] | None
    decision_kind: str | None
    decision_json: Mapping[str, object] | None
    human_input_text: str | None
    output_feedback: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class AgentRunTraceDetails:
    run: AgentRunDetails
    steps: tuple[AgentStepDetails, ...]
    source_binding: SourceBinding | None = None


class OutputValidationError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    loc: tuple[str, ...] = Field(max_length=8)
    type: str

    @field_validator("loc")
    @classmethod
    def safe_loc(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        fields = {
            "tool_call",
            "request_human_input",
            "propose",
            "stop",
            "[]",
            "*",
            "reasoning",
            "rationale",
            "chain_of_thought",
            "source_patch",
            "patch",
        }
        for definition in TypeAdapter(AgentDecision).json_schema().get("$defs", {}).values():
            fields.update(definition.get("properties", {}))
        if any(part not in fields for part in value):
            raise ValueError("unsafe feedback location")
        return value

    @field_validator("type")
    @classmethod
    def safe_type(cls, value: str) -> str:
        if value not in {*get_args(ErrorType), *TEXT_ERROR_CODES, "other"}:
            raise ValueError("unsafe feedback type")
        return value


class OutputFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    category: Literal[
        "invalid_decision", "summary_omitted", "provider_invalid_output", "correction"
    ]
    code: Literal[
        "invalid_agent_decision",
        "summary_omitted",
        "provider_invalid_json",
        "provider_output_not_object",
        "correction_reserved",
    ]
    correction_of_step_id: UUID | None = None
    correction_allowed: bool = False
    error_count: int = Field(ge=0, le=1_000_000)
    errors: tuple[OutputValidationError, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def consistent(self) -> OutputFeedback:
        codes = {
            "invalid_decision": {"invalid_agent_decision"},
            "summary_omitted": {"summary_omitted"},
            "provider_invalid_output": {"provider_invalid_json", "provider_output_not_object"},
            "correction": {"correction_reserved"},
        }
        if self.code not in codes[self.category] or self.error_count < len(self.errors):
            raise ValueError("inconsistent output feedback")
        if self.category in {"invalid_decision", "summary_omitted"}:
            if not self.errors or self.error_count == 0:
                raise ValueError("missing validation feedback")
        elif self.error_count or self.errors:
            raise ValueError("unexpected validation feedback")
        if self.category == "correction" and self.correction_of_step_id is None:
            raise ValueError("missing correction link")
        if self.correction_allowed and (
            self.category not in {"invalid_decision", "provider_invalid_output"}
            or self.error_count > 8
            or self.error_count != len(self.errors)
        ):
            raise ValueError("inconsistent correction eligibility")
        return self


def feedback_json(value: OutputFeedback) -> dict[str, object]:
    return cast(dict[str, object], value.model_dump(mode="json"))


def read_feedback(value: Mapping[str, object] | None) -> OutputFeedback | None:
    if value is None:
        return None
    try:
        encoded = json.dumps(dict(value), ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > 8192:
            raise ValueError
        return OutputFeedback.model_validate_json(encoded)
    except (ValueError, TypeError, RecursionError):
        raise AgentRuntimeError("output_feedback_invalid") from None


def check_correction_reservation(
    run: AgentRunDetails,
    step: AgentStepDetails,
    steps: tuple[AgentStepDetails, ...],
) -> None:
    feedback = read_feedback(step.output_feedback)
    # ponytail: max_steps <= 20; existing Steps bound the scan, no quota table.
    previous = [read_feedback(item.output_feedback) for item in steps]
    if feedback is None:
        if steps and steps[-1].outcome == "failed":
            raise AgentRuntimeError("output_feedback_invalid")
        return
    if (
        feedback.category != "correction"
        or feedback.code != "correction_reserved"
        or feedback.correction_allowed
        or feedback.error_count
        or feedback.errors
        or feedback.correction_of_step_id is None
    ):
        raise AgentRuntimeError("output_feedback_invalid")
    source = steps[-1] if steps else None
    source_feedback = previous[-1] if previous else None
    if (
        run.status != "ready"
        or source is None
        or source_feedback is None
        or source.step_kind != "model_call"
        or source.outcome != "failed"
        or source.agent_step_id != feedback.correction_of_step_id
        or not source_feedback.correction_allowed
        or source_feedback.category not in {"invalid_decision", "provider_invalid_output"}
        or any(item and item.correction_of_step_id is not None for item in previous)
    ):
        raise AgentRuntimeError("output_correction_conflict")


def finalized_feedback(
    step: AgentStepDetails,
    value: Mapping[str, object] | None,
) -> dict[str, object] | None:
    original = read_feedback(step.output_feedback)
    feedback = read_feedback(value)
    if feedback is None:
        return None if original is None else feedback_json(original)
    link = None if original is None else original.correction_of_step_id
    if feedback.correction_of_step_id not in {None, link}:
        raise AgentRuntimeError("output_correction_conflict")
    return feedback_json(feedback.model_copy(update={"correction_of_step_id": link}))


def source_patch_bytes(run: AgentRunDetails, steps: tuple[AgentStepDetails, ...]) -> bytes:
    if (
        run.source_patch_context is None
        or run.source_adoption_id is not None
        or run.status != "completed"
        or run.termination_reason != "source_patch_drafted"
        or run.produced_proposal_revision_id is not None
        or not steps
    ):
        raise AgentRuntimeError("source_patch_not_ready")
    try:
        SourcePatchContext.model_validate(dict(run.source_patch_context))
        step = steps[-1]
        if (
            step.agent_run_id != run.agent_run_id
            or step.outcome != "succeeded"
            or step.step_kind != "model_call"
            or step.decision_kind != "source_patch"
            or step.completed_at is None
            or step.ordinal != run.next_step_ordinal - 1
        ):
            raise AgentRuntimeError("source_patch_not_ready")
        decision = SourcePatchDecision.model_validate(step.decision_json)
        return decision.patch.encode("utf-8", "strict")
    except (ValueError, TypeError):
        raise AgentRuntimeError("source_patch_context_invalid") from None
