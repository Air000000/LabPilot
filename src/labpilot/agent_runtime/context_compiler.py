from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from typing import cast

from pydantic import TypeAdapter

from labpilot.agent_runtime.provider import ModelRequest
from labpilot.agent_runtime.result_context import ResultContext
from labpilot.agent_runtime.types import AgentDecision
from labpilot.common.canonical_json import CanonicalValue, canonical_json_bytes
from labpilot.common.hashing import namespaced_sha256
from labpilot.context.repository_source import RepositorySourceDescriptor
from labpilot.tools_policy.types import ToolDefinition, bound_toolset_hash

_OUTPUT_SCHEMA_NAMESPACE = "labpilot-agent-output-schema:v1"
_MODEL_REQUEST_NAMESPACE = "labpilot-model-request:v1"
_MAX_GOAL_BYTES = 4000
_MAX_TOOL_COUNT = 32
_MAX_TOOL_SCHEMA_BYTES = 32 * 1024
_MAX_PRIOR_RESULTS = 20
_MAX_TOOL_RESULT_BYTES = 64 * 1024
_MAX_HUMAN_INPUT_BYTES = 4000
_MAX_RESULT_CONTEXT_BYTES = 128 * 1024
_MAX_MESSAGE_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class PriorToolResult:
    tool_name: str
    result: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class CompiledContext:
    request: ModelRequest
    output_schema_hash: str
    canonical_request_hash: str


class ContextCompilationError(ValueError):
    pass


class ContextCompiler:
    def compile(
        self,
        *,
        provider_name: str,
        model: str,
        goal: str,
        snapshot: RepositorySourceDescriptor,
        definitions: tuple[ToolDefinition, ...],
        prior_tool_results: tuple[PriorToolResult, ...],
        prior_human_inputs: tuple[str, ...],
        result_context: ResultContext | None = None,
    ) -> CompiledContext:
        _require_utf8(goal, field="goal", max_bytes=_MAX_GOAL_BYTES, non_blank=True)
        if len(definitions) > _MAX_TOOL_COUNT:
            raise ContextCompilationError("too_many_tools")
        if len(prior_tool_results) > _MAX_PRIOR_RESULTS:
            raise ContextCompilationError("too_many_tool_results")

        output_schema = cast(dict[str, object], TypeAdapter(AgentDecision).json_schema())
        output_schema_value = _canonical_value(output_schema)
        output_schema_hash = namespaced_sha256(_OUTPUT_SCHEMA_NAMESPACE, output_schema_value)
        tools = [_tool_contract(definition) for definition in definitions]
        system_value: dict[str, CanonicalValue] = {
            "instruction": (
                "Return exactly one JSON object matching agent_decision_schema. "
                "Do not include rationale, reasoning, or chain_of_thought."
            ),
            "decision_policy": [
                (
                    "Use repository tools to gather enough evidence before proposing. "
                    "A proposal must be grounded in repository facts rather than plausible guesses."
                ),
                (
                    "After inspecting the repository tree, read relevant repository guidance "
                    "such as README files, AGENTS files, CONTRIBUTING files, or documentation "
                    "entry points when present, together with executable or configuration source "
                    "needed to confirm the experiment contract."
                ),
                (
                    "Treat repository evidence as authoritative. Preserve exact paths, "
                    "URI schemes, stable identities, artifact and metric names, parameter "
                    "values, seeds, environment requirements, validation subjects, and "
                    "template placeholder syntax when the repository specifies them."
                ),
                (
                    "Do not invent, normalize, or silently replace required proposal values "
                    "when repository evidence is available. If required fields remain "
                    "unsupported or conflicting after reasonable inspection, "
                    "request_human_input instead of guessing."
                ),
            ],
            "proposal_contract": [
                "metrics[].source_artifact references artifacts[].name, not relative_path.",
                (
                    "artifact_exists.subject references an artifact name. metric_finite, "
                    "metric_gte, and metric_lte subjects reference metric names. Every "
                    "required artifact needs artifact_exists coverage."
                ),
                (
                    "Execution seeds belong in seeds. Do not duplicate the execution seed as a "
                    "parameter_matrix dimension."
                ),
                (
                    "train_argv_template placeholders are standalone tokens. Use {parameter:<key>} "
                    "for every parameter_matrix key and consume every parameter dimension. Use "
                    "{seed} for the execution seed, {dataset} for the dataset argument, and "
                    "{output_dir} for the artifact output directory. Do not invent alternate "
                    "placeholder syntax."
                ),
                (
                    "Set eval_entrypoint only when repository evidence identifies a separate "
                    "evaluation entrypoint; otherwise use null."
                ),
            ],
            "agent_decision_schema": output_schema_value,
            "tools": tools,
        }
        if result_context is not None:
            system_value["result_context_policy"] = [
                (
                    "Treat selected and eligible evidence in result_context as authoritative "
                    "for the deterministic facts it contains."
                ),
                (
                    "Use exclusion reason codes to diagnose rejected Trials, but do not "
                    "promote invalid raw results into accepted scientific evidence."
                ),
                (
                    "Read trial_evidence before proposing: it joins each Trial's exact parameters "
                    "and seed to its selected-valid or excluded outcome. Only selected_valid "
                    "Trials met the declared validation protocol. Do not infer successful "
                    "parameters from source_proposal.goal or summary averages."
                ),
                (
                    "Do not propose only the same conditions that were excluded as validation "
                    "invalid in this Batch. Compare them with selected-valid conditions first."
                ),
                "If the declared research goal is already satisfied, stop is a valid decision.",
                (
                    "If another experiment is warranted, propose must include the full "
                    "ProposalPayload required by the existing proposal contract."
                ),
                "Never start or approve the next experiment from the Agent decision.",
            ]
        prior_results: list[CanonicalValue] = []
        for item in prior_tool_results:
            value: CanonicalValue = {
                "tool_name": item.tool_name,
                "result": _canonical_value(item.result),
            }
            if len(canonical_json_bytes(value)) > _MAX_TOOL_RESULT_BYTES:
                raise ContextCompilationError("tool_result_too_large")
            prior_results.append(value)
        human_inputs: list[CanonicalValue] = []
        for value in prior_human_inputs:
            _require_utf8(
                value,
                field="human_input",
                max_bytes=_MAX_HUMAN_INPUT_BYTES,
                non_blank=True,
            )
            human_inputs.append(value)
        user_value: dict[str, CanonicalValue] = {
            "goal": goal,
            "snapshot": {
                "repository_snapshot_id": str(snapshot.repository_snapshot_id),
                "repository_snapshot_identity": snapshot.repository_snapshot_identity,
                "repository_registration_id": str(snapshot.repository_registration_id),
                "project_id": str(snapshot.project_id),
                "head_commit": snapshot.head_commit,
                "current_branch": snapshot.current_branch,
                "is_detached_head": snapshot.is_detached_head,
                "is_clean": snapshot.is_clean,
            },
            "prior_tool_results": prior_results,
            "prior_human_inputs": human_inputs,
        }
        if result_context is not None:
            result_value = cast(
                dict[str, CanonicalValue],
                _canonical_value(
                    result_context.model_dump(
                        mode="json",
                        warnings="none",
                        fallback=_serialization_fallback,
                    )
                ),
            )
            result_value["trial_evidence"] = _trial_evidence(result_context)
            if len(canonical_json_bytes(result_value)) > _MAX_RESULT_CONTEXT_BYTES:
                raise ContextCompilationError("result_context_too_large")
            user_value["result_context"] = result_value
        messages = (
            {"role": "system", "content": _render(system_value)},
            {"role": "user", "content": _render(user_value)},
        )
        message_bytes = len(messages[0]["content"].encode("utf-8")) + len(
            messages[1]["content"].encode("utf-8")
        )
        if message_bytes > _MAX_MESSAGE_BYTES:
            raise ContextCompilationError("compiled_messages_too_large")
        request = ModelRequest(
            model=model,
            messages=messages,
            semantic_parameters={},
            output_schema=output_schema,
            bound_toolset_hash=bound_toolset_hash(definitions),
        )
        return CompiledContext(
            request=request,
            output_schema_hash=output_schema_hash,
            canonical_request_hash=canonical_request_hash(
                provider_name=provider_name,
                request=request,
                output_schema_hash=output_schema_hash,
            ),
        )


def canonical_request_hash(
    *, provider_name: str, request: ModelRequest, output_schema_hash: str
) -> str:
    return namespaced_sha256(
        _MODEL_REQUEST_NAMESPACE,
        {
            "provider_name": provider_name,
            "model": request.model,
            "messages": _canonical_value(request.messages),
            "semantic_parameters": _canonical_value(request.semantic_parameters),
            "output_schema_hash": output_schema_hash,
            "bound_toolset_hash": request.bound_toolset_hash,
        },
    )


def _tool_contract(definition: ToolDefinition) -> CanonicalValue:
    value: CanonicalValue = {
        "name": definition.name,
        "version": definition.version,
        "risk": definition.risk,
        "side_effect": definition.side_effect,
        "approval_required": definition.approval_required,
        "input_schema": _canonical_value(definition.input_schema),
    }
    if len(canonical_json_bytes(value)) > _MAX_TOOL_SCHEMA_BYTES:
        raise ContextCompilationError("tool_schema_too_large")
    return value


def _render(value: CanonicalValue) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _serialization_fallback(value: object) -> object:
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"unsupported result context value: {type(value).__name__}")


def _trial_evidence(context: ResultContext) -> list[CanonicalValue]:
    included = {item["trial_id"]: item for item in context.included_results}
    excluded = {item["trial_id"]: item for item in context.excluded_results}
    evidence: list[CanonicalValue] = []
    for trial in context.trials:
        trial_id = str(trial.trial_id)
        item: dict[str, CanonicalValue] = {
            "trial_id": trial_id,
            "trial_ordinal": trial.trial_ordinal,
            "resolved_parameters": _canonical_value(trial.resolved_parameters),
            "seed": trial.seed,
        }
        if trial_id in included:
            item["outcome"] = "selected_valid"
            item["metrics"] = _canonical_value(included[trial_id]["metrics"])
        elif trial_id in excluded:
            item["outcome"] = "excluded"
            item["exclusion_code"] = _canonical_value(excluded[trial_id]["exclusion_code"])
            item["validation_reason_codes"] = _canonical_value(
                excluded[trial_id].get("validation_reason_codes", [])
            )
        else:
            raise ContextCompilationError("result_context_trial_coverage_invalid")
        evidence.append(item)
    return evidence


def _require_utf8(value: str, *, field: str, max_bytes: int, non_blank: bool) -> None:
    if non_blank and not value.strip():
        raise ContextCompilationError(f"{field}_blank")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ContextCompilationError(f"{field}_invalid_utf8") from exc
    if size > max_bytes:
        raise ContextCompilationError(f"{field}_too_large")


def _canonical_value(value: object) -> CanonicalValue:
    if value is None or type(value) in {str, int, bool, Decimal}:
        return cast(CanonicalValue, value)
    if type(value) is float and isfinite(value):
        return Decimal(str(value))
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical_value(item) for item in value]
    raise ContextCompilationError(f"non_json_value:{type(value).__name__}")
