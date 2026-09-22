from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from math import isfinite
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from labpilot.experiments.proposal_types import ProposalPayload


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
        raise ValueError(f"{field} must not be blank")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} must be valid UTF-8") from exc
    if size > max_bytes:
        raise ValueError(f"{field} exceeds {max_bytes} UTF-8 bytes")
    return value


class _Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str | None = None

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_text(value, field="summary", max_bytes=1000, non_blank=False)


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
    tool_name: str
    arguments: dict[str, JsonValue]

    @field_validator("tool_name")
    @classmethod
    def validate_tool_name(cls, value: str) -> str:
        return _bounded_text(value, field="tool_name", max_bytes=100, non_blank=True)

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _require_canonical_json(value)
        return value


class HumanInputDecision(_Decision):
    kind: Literal["request_human_input"]
    question: str

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        return _bounded_text(value, field="question", max_bytes=4000, non_blank=True)


class ProposeDecision(_Decision):
    kind: Literal["propose"]
    proposal: ProposalPayload

    @field_validator("proposal", mode="before")
    @classmethod
    def validate_proposal(cls, value: object) -> object:
        _require_canonical_json(value)
        return value


class StopDecision(_Decision):
    kind: Literal["stop"]
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")


type AgentDecision = Annotated[
    ToolCallDecision | HumanInputDecision | ProposeDecision | StopDecision,
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


@dataclass(frozen=True, slots=True)
class AgentRunTraceDetails:
    run: AgentRunDetails
    steps: tuple[AgentStepDetails, ...]
