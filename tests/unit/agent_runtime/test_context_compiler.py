from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import cast
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from labpilot.agent_runtime.context_compiler import (
    ContextCompiler,
    PriorToolResult,
    canonical_request_hash,
)
from labpilot.agent_runtime.provider import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderPolicyError,
    assert_provider_egress,
)
from labpilot.agent_runtime.types import AgentDecision, ProposeDecision
from labpilot.context.repository_source import RepositorySourceDescriptor
from labpilot.tools_policy.types import ToolDefinition


def _proposal() -> dict[str, object]:
    snapshot_id = uuid4()
    return {
        "goal": "compare optimizers",
        "experiment_type": "comparison",
        "repository_snapshot_id": str(snapshot_id),
        "repository_snapshot_identity": "a" * 64,
        "train_entrypoint": "train.py",
        "eval_entrypoint": None,
        "workdir": ".",
        "parameter_matrix": {"optimizer": ["adam", "sgd"]},
        "seeds": [1, 2],
        "dataset_reference": {
            "reference_type": "uri",
            "path_or_uri": "s3://example/dataset",
            "stable_identity": "dataset-v1",
            "reproducibility_level": "strict",
        },
        "environment_expectation": {
            "python_profile": "3.12",
            "framework_requirements": ["torch"],
            "device_requirement": "gpu",
            "cuda_requirement": "12.4",
        },
        "metrics": [{"name": "accuracy", "source_artifact": "metrics", "split": "test"}],
        "artifacts": [{"name": "metrics", "relative_path": "metrics.json", "required": True}],
        "validation_rules": [{"kind": "metric_finite", "subject": "accuracy"}],
        "timeout_seconds": 3600,
        "max_retries": 0,
    }


def _proposal_with_extra() -> dict[str, object]:
    proposal = _proposal()
    proposal["unexpected"] = True
    return proposal


@pytest.mark.parametrize(
    ("payload", "kind"),
    [
        (
            {
                "kind": "tool_call",
                "tool_name": "repository.read_text",
                "arguments": {"relative_path": "train.py"},
            },
            "tool_call",
        ),
        (
            {"kind": "request_human_input", "question": "Which metric?"},
            "request_human_input",
        ),
        ({"kind": "propose", "proposal": _proposal()}, "propose"),
        ({"kind": "stop", "reason_code": "goal_satisfied"}, "stop"),
    ],
)
def test_agent_decision_accepts_exactly_the_four_variants(
    payload: dict[str, object], kind: str
) -> None:
    decision = TypeAdapter(AgentDecision).validate_python(payload)
    assert decision.kind == kind


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "tool_call", "tool_name": "x", "arguments": {}, "question": "x"},
        {"kind": "request_human_input", "question": "x", "rationale": "secret"},
        {"kind": "stop", "reason_code": "done", "reasoning": "secret"},
        {"kind": "stop", "reason_code": "done", "chain_of_thought": "secret"},
        {"kind": "stop", "reason_code": "INVALID-HYPHEN"},
        {"kind": "tool_call", "tool_name": "x", "arguments": []},
        {"kind": "propose", "proposal": _proposal_with_extra()},
    ],
)
def test_agent_decision_rejects_cross_variant_and_reasoning_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(AgentDecision).validate_python(payload)


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf"), "\ud800"],
    ids=["nan", "positive_infinity", "negative_infinity", "isolated_surrogate"],
)
def test_agent_decision_rejects_noncanonical_nested_json_values(value: object) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(AgentDecision).validate_python(
            {
                "kind": "tool_call",
                "tool_name": "repository.read_text",
                "arguments": {"nested": [value]},
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("parameter_matrix", {"learning_rate": [float("nan")]}),
        ("repository_snapshot_identity", "\ud800"),
    ],
    ids=["proposal_non_finite", "proposal_isolated_surrogate"],
)
def test_propose_decision_rejects_noncanonical_json_values(
    field: str, value: object
) -> None:
    proposal = _proposal()
    proposal[field] = value

    with pytest.raises(ValidationError):
        TypeAdapter(AgentDecision).validate_python(
            {"kind": "propose", "proposal": proposal}
        )


def _typed_proposal():
    decision = TypeAdapter(AgentDecision).validate_python(
        {"kind": "propose", "proposal": _proposal()}
    )
    assert isinstance(decision, ProposeDecision)
    return decision.proposal


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "positive_infinity", "negative_infinity"],
)
def test_typed_proposal_rejects_non_finite_parameter_values(value: float) -> None:
    proposal = replace(
        _typed_proposal(),
        parameter_matrix={"learning_rate": (value,)},
    )

    with pytest.raises(ValidationError):
        ProposeDecision(kind="propose", proposal=proposal)


@pytest.mark.parametrize("location", ["value", "mapping_key"])
def test_typed_proposal_rejects_invalid_utf8_semantics(location: str) -> None:
    proposal = _typed_proposal()
    if location == "value":
        proposal = replace(
            proposal,
            environment_expectation=replace(
                proposal.environment_expectation,
                device_requirement="\ud800",
            ),
        )
    else:
        proposal = replace(proposal, parameter_matrix={"\ud800": ("value",)})

    with pytest.raises(ValidationError):
        ProposeDecision(kind="propose", proposal=proposal)


def test_typed_proposal_accepts_valid_semantics() -> None:
    proposal = _typed_proposal()

    decision = ProposeDecision(kind="propose", proposal=proposal)

    assert decision.proposal == proposal


def test_agent_decision_enforces_utf8_byte_limits() -> None:
    adapter = TypeAdapter(AgentDecision)
    accepted = adapter.validate_python(
        {"kind": "stop", "reason_code": "done", "summary": "é" * 500}
    )
    assert accepted.summary == "é" * 500

    with pytest.raises(ValidationError, match="1000 UTF-8 bytes"):
        adapter.validate_python({"kind": "stop", "reason_code": "done", "summary": "é" * 501})


def _descriptor() -> RepositorySourceDescriptor:
    return RepositorySourceDescriptor(
        repository_snapshot_id=uuid4(),
        repository_snapshot_identity="b" * 64,
        repository_registration_id=uuid4(),
        project_id=uuid4(),
        head_commit="c" * 40,
        current_branch="feature/test",
        is_detached_head=False,
        is_clean=True,
    )


async def _executor(arguments: Mapping[str, object]) -> Mapping[str, object]:
    return arguments


def _tool(*, version: str = "v1", max_length: int = 1000) -> ToolDefinition:
    return ToolDefinition(
        name="repository.read_text",
        version=version,
        risk="green",
        side_effect="read_only",
        input_schema={
            "type": "object",
            "properties": {"relative_path": {"type": "string", "maxLength": max_length}},
            "required": ["relative_path"],
            "additionalProperties": False,
        },
        approval_required=False,
        executor=_executor,
    )


def test_context_contains_only_bounded_owned_inputs_and_explicit_tool_results() -> None:
    sentinel_secret = "sentinel-provider-secret"
    raw_source = "RAW_REPOSITORY_SOURCE_ONLY_FROM_TOOL"
    compiler = ContextCompiler()
    descriptor = _descriptor()

    without_tool_result = compiler.compile(
        provider_name="scripted",
        model="test-model",
        goal="inspect the repository",
        snapshot=descriptor,
        definitions=(_tool(),),
        prior_tool_results=(),
        prior_human_inputs=("prefer accuracy",),
    )
    rendered = "\n".join(
        cast(str, message["content"]) for message in without_tool_result.request.messages
    )
    assert "inspect the repository" in rendered
    assert str(descriptor.repository_snapshot_id) in rendered
    assert "repository.read_text" in rendered
    assert "prefer accuracy" in rendered
    assert raw_source not in rendered
    assert sentinel_secret not in rendered

    with_tool_result = compiler.compile(
        provider_name="scripted",
        model="test-model",
        goal="inspect the repository",
        snapshot=descriptor,
        definitions=(_tool(),),
        prior_tool_results=(PriorToolResult("repository.read_text", {"text": raw_source}),),
        prior_human_inputs=(),
    )
    rendered = "\n".join(
        cast(str, message["content"]) for message in with_tool_result.request.messages
    )
    assert raw_source in rendered
    assert all(set(message) == {"role", "content"} for message in with_tool_result.request.messages)


def test_context_hashes_are_deterministic_and_bind_schema_and_tool_contract() -> None:
    compiler = ContextCompiler()
    descriptor = _descriptor()
    first = compiler.compile(
        provider_name="scripted",
        model="test-model",
        goal="goal",
        snapshot=descriptor,
        definitions=(_tool(),),
        prior_tool_results=(),
        prior_human_inputs=(),
    )
    same = compiler.compile(
        provider_name="scripted",
        model="test-model",
        goal="goal",
        snapshot=descriptor,
        definitions=(_tool(),),
        prior_tool_results=(),
        prior_human_inputs=(),
    )
    version_changed = compiler.compile(
        provider_name="scripted",
        model="test-model",
        goal="goal",
        snapshot=descriptor,
        definitions=(_tool(version="v2"),),
        prior_tool_results=(),
        prior_human_inputs=(),
    )
    schema_changed = compiler.compile(
        provider_name="scripted",
        model="test-model",
        goal="goal",
        snapshot=descriptor,
        definitions=(_tool(max_length=999),),
        prior_tool_results=(),
        prior_human_inputs=(),
    )

    assert first.output_schema_hash == same.output_schema_hash
    assert first.request.bound_toolset_hash == same.request.bound_toolset_hash
    assert first.canonical_request_hash == same.canonical_request_hash
    assert first.request.bound_toolset_hash != version_changed.request.bound_toolset_hash
    assert first.request.bound_toolset_hash != schema_changed.request.bound_toolset_hash
    assert first.canonical_request_hash != version_changed.canonical_request_hash
    assert first.canonical_request_hash != schema_changed.canonical_request_hash

    schema_hash_changed = canonical_request_hash(
        provider_name="scripted",
        request=first.request,
        output_schema_hash="f" * 64,
    )
    toolset_hash_changed = canonical_request_hash(
        provider_name="scripted",
        request=replace(first.request, bound_toolset_hash="e" * 64),
        output_schema_hash=first.output_schema_hash,
    )
    assert schema_hash_changed != first.canonical_request_hash
    assert toolset_hash_changed != first.canonical_request_hash


@pytest.mark.asyncio
async def test_local_only_rejects_remote_provider_before_invocation() -> None:
    class RemoteProvider:
        provider_name = "remote-test"
        egress = "remote"

        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, request: ModelRequest) -> ModelResponse:
            self.calls += 1
            return ModelResponse(payload={"kind": "stop", "reason_code": "done"}, usage={})

    provider = RemoteProvider()
    typed_provider: ModelProvider = provider

    with pytest.raises(ProviderPolicyError, match="remote_provider_blocked"):
        assert_provider_egress("local_only", typed_provider)

    assert provider.calls == 0
