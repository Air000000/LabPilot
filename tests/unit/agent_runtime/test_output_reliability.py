from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.unit.agent_runtime import test_agent_service as fixture

from labpilot.agent_runtime.deepseek import _decode_response
from labpilot.agent_runtime.provider import ProviderError
from labpilot.agent_runtime.types import AgentDecision, AgentRuntimeError


def _envelope(
    content: str, *, finish: str | None = "stop", usage: object = None, choices: int = 1
) -> bytes:
    return json.dumps(
        {
            "choices": [
                {"finish_reason": finish, "message": {"content": content}} for _ in range(choices)
            ],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
            if usage is None
            else usage,
        }
    ).encode()


@pytest.mark.parametrize(
    ("content", "finish", "reason", "completed"),
    [
        ("not-json", "stop", "provider_invalid_json", True),
        ("[]", "stop", "provider_output_not_object", True),
        ('{"kind":"stop","kind":"propose"}', "stop", "provider_ambiguous_output", False),
        ('{"kind":"stop"}', "length", "provider_output_truncated", False),
        ('{"kind":"stop"}', "content_filter", "provider_output_filtered", False),
        ('{"kind":"stop"}', None, "provider_completion_unknown", False),
        ('{"value":NaN}', "stop", "provider_ambiguous_output", False),
    ],
)
def test_provider_known_failure_retains_usage_without_raw_text(
    content: str,
    finish: str | None,
    reason: str,
    completed: bool,
) -> None:
    with pytest.raises(ProviderError) as caught:
        _decode_response(_envelope(content, finish=finish))
    assert caught.value.reason_code == reason
    assert caught.value.usage["total_tokens"] == 6
    assert caught.value.response_completed is completed
    assert content not in str(caught.value)


@pytest.mark.parametrize(
    ("usage", "reason", "lower_bound"),
    [
        ({}, "provider_usage_unknown", {}),
        ({"prompt_tokens": 4}, "provider_usage_unknown", {"prompt_tokens": 4}),
        ({"total_tokens": True}, "provider_usage_invalid", {}),
        ({"total_tokens": -1}, "provider_usage_invalid", {}),
        (
            {"total_tokens": 0, "prompt_tokens": 4, "completion_tokens": 2},
            "provider_usage_invalid",
            {"prompt_tokens": 4, "completion_tokens": 2},
        ),
    ],
)
def test_unknown_or_inconsistent_usage_is_terminal(
    usage: Mapping[str, object],
    reason: str,
    lower_bound: Mapping[str, object],
) -> None:
    with pytest.raises(ProviderError) as caught:
        _decode_response(_envelope('{"kind":"stop","reason_code":"done"}', usage=usage))
    assert caught.value.reason_code == reason
    assert caught.value.usage == lower_bound
    assert not caught.value.response_completed


def test_multiple_choices_are_rejected() -> None:
    with pytest.raises(ProviderError, match="provider_choices_invalid"):
        _decode_response(_envelope('{"kind":"stop"}', choices=2))


def test_usage_extensions_are_not_persisted() -> None:
    response = _decode_response(
        _envelope(
            '{"kind":"stop","reason_code":"done"}',
            usage={
                "total_tokens": 6,
                "sentinel-secret": "do not retain",
                "prompt_cache_hit_tokens": "bad",
            },
        )
    )
    assert response.usage == {"total_tokens": 6}
    assert response.response_completed


def test_text_contract_exposes_bytes_and_preserves_local_authority() -> None:
    adapter = TypeAdapter(AgentDecision)
    schema = adapter.json_schema()
    summary = schema["$defs"]["StopDecision"]["properties"]["summary"]
    assert summary["x-max-utf8-bytes"] == 1000
    assert "optional" in summary["description"].lower()
    assert adapter.validate_python({"kind": "stop", "reason_code": "done", "summary": "中" * 333})
    with pytest.raises(ValidationError) as caught:
        adapter.validate_python({"kind": "stop", "reason_code": "done", "summary": "中" * 334})
    assert caught.value.errors()[0]["type"] == "text_too_large"


@pytest.mark.asyncio
async def test_failed_provider_response_usage_is_accounted() -> None:
    from test_agent_service import (
        MemoryAgentRepository,
        ScriptedProvider,
        _budget,
        _context,
        _descriptor,
        _service,
    )

    descriptor, repo = _descriptor(), MemoryAgentRepository()
    provider = ScriptedProvider(
        [ProviderError("provider_output_truncated", usage={"total_tokens": 7})]
    )
    service, tools, proposals = _service(repo, descriptor, provider)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="offline",
        budget=_budget(),
    )
    result = await service.advance_run(
        context=_context(), agent_run_id=run.agent_run_id, expected_row_version=run.row_version
    )
    assert result.status == "failed" and result.total_tokens == 7
    assert repo.steps[run.agent_run_id][0].token_usage == {"total_tokens": 7}
    assert not proposals.calls and tools.executions == 0


@pytest.mark.asyncio
async def test_known_usage_survives_post_response_deadline(monkeypatch) -> None:
    from test_agent_service import (
        MemoryAgentRepository,
        ScriptedProvider,
        _budget,
        _context,
        _descriptor,
        _service,
    )

    import labpilot.agent_runtime.service as module

    descriptor, repo = _descriptor(), MemoryAgentRepository()
    provider = ScriptedProvider([({"kind": "stop", "reason_code": "done"}, {"total_tokens": 7})])
    service, _, _ = _service(repo, descriptor, provider)
    run = await service.create_run(
        context=_context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="offline",
        budget=_budget(),
    )
    monkeypatch.setattr(
        module, "_remaining_wall_clock_seconds", lambda _: 600 if not provider.calls else 0
    )
    result = await service.advance_run(
        context=_context(), agent_run_id=run.agent_run_id, expected_row_version=run.row_version
    )
    assert result.termination_reason == "max_wall_clock_seconds"
    assert result.total_tokens == 7 and result.status == "budget_exhausted"
    assert repo.steps[run.agent_run_id][0].outcome == "succeeded"


def test_actual_output_cap_is_part_of_request_identity() -> None:
    from test_context_compiler import _descriptor

    from labpilot.agent_runtime.context_compiler import ContextCompiler

    compiler = ContextCompiler()
    kwargs = dict(
        provider_name="deepseek_chat",
        model="offline",
        goal="offline",
        snapshot=_descriptor(),
        definitions=(),
        prior_tool_results=(),
        prior_human_inputs=(),
    )
    first = compiler.compile(**kwargs, semantic_parameters={"max_tokens": 8192})
    second = compiler.compile(**kwargs, semantic_parameters={"max_tokens": 17})
    assert first.canonical_request_hash != second.canonical_request_hash
    assert second.request.semantic_parameters == {"max_tokens": 17}


@pytest.mark.asyncio
async def test_output_cap_is_sent_once_on_wire() -> None:
    import httpx
    from pydantic import SecretStr
    from test_deepseek import _request

    from labpilot.agent_runtime.deepseek import DeepSeekChatProvider

    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, content=_envelope('{"kind":"stop","reason_code":"done"}'))

    provider = DeepSeekChatProvider(
        model="deepseek-v4-flash",
        api_key=SecretStr("sentinel"),
        transport=httpx.MockTransport(handler),
    )
    response = await provider.complete(_request(semantic_parameters={"max_tokens": 17}))
    assert len(calls) == 1 and calls[0]["max_tokens"] == 17 and response.response_completed


class CompletedProvider(fixture.ScriptedProvider):
    async def complete(self, request):
        return replace(await super().complete(request), response_completed=True)


async def _system(script, *, repo=None, budget=None):
    descriptor = fixture._descriptor()
    repo = repo or fixture.MemoryAgentRepository()
    provider = CompletedProvider(script)
    service, tools, proposals = fixture._service(repo, descriptor, provider)
    run = await service.create_run(
        context=fixture._context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="offline bounded output",
        budget=budget or fixture._budget(),
    )
    return repo, provider, service, tools, proposals, run


async def _advance(service, run):
    return await service.advance_run(
        context=fixture._context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=run.row_version,
    )


@pytest.mark.asyncio
async def test_only_summary_failure_keeps_fully_valid_action():
    repo, provider, service, _, _, run = await _system(
        [
            ({"kind": "stop", "reason_code": "done", "summary": "中" * 334}, {"total_tokens": 7}),
        ]
    )
    result = await _advance(service, run)
    assert result.status == "completed" and len(provider.calls) == 1
    (step,) = repo.steps[run.agent_run_id]
    assert step.decision_json["summary"] is None
    assert step.output_feedback["category"] == "summary_omitted"
    assert step.outcome == "succeeded" and result.total_tokens == 7


@pytest.mark.asyncio
async def test_invalid_then_valid_has_one_durable_correction():
    repo, provider, service, _, _, run = await _system(
        [
            ({"kind": "stop"}, {"total_tokens": 7}),
            ({"kind": "stop", "reason_code": "done"}, {"total_tokens": 11}),
        ]
    )
    result = await _advance(service, run)
    assert result.status == "completed" and result.total_tokens == 18
    first, second = repo.steps[run.agent_run_id]
    assert first.outcome == "failed" and first.decision_json is None
    assert first.output_feedback["correction_allowed"] is True
    assert second.output_feedback["correction_of_step_id"] == str(first.agent_step_id)
    assert "output_feedback" in provider.calls[1].messages[0]["content"]
    assert first.canonical_request_hash != second.canonical_request_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_kind", ["stop", "propose"])
async def test_invalid_twice_is_terminal_without_third_call(initial_kind):
    repo, provider, service, _, _, run = await _system(
        [
            ({"kind": initial_kind}, {"total_tokens": 7}),
            ({"kind": "stop"}, {"total_tokens": 11}),
            ({"kind": "stop", "reason_code": "must_not_run"}, {"total_tokens": 1}),
        ]
    )
    result = await _advance(service, run)
    assert result.status == "failed" and result.termination_reason == "invalid_agent_decision"
    assert result.total_tokens == 18 and len(provider.calls) == 2
    assert repo.steps[run.agent_run_id][1].output_feedback["correction_of_step_id"]


@pytest.mark.asyncio
async def test_mixed_summary_and_action_is_regenerated_not_silently_fixed():
    repo, provider, service, _, _, run = await _system(
        [
            ({"kind": "stop", "summary": "中" * 334}, {"total_tokens": 7}),
            ({"kind": "stop", "reason_code": "done"}, {"total_tokens": 11}),
        ]
    )
    result = await _advance(service, run)
    assert result.status == "completed" and len(provider.calls) == 2
    assert repo.steps[run.agent_run_id][0].decision_json is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "budget",
    [
        fixture._budget(max_steps=1),
        fixture._budget(max_model_calls=1),
        fixture._budget(max_tokens=7),
    ],
)
@pytest.mark.parametrize("initial_kind", ["stop", "propose"])
async def test_invalid_output_cannot_get_correction_without_budget(budget, initial_kind):
    repo, provider, service, _, _, run = await _system(
        [
            ({"kind": initial_kind}, {"total_tokens": 7}),
            ({"kind": "stop", "reason_code": "done"}, {"total_tokens": 1}),
        ],
        budget=budget,
    )
    result = await _advance(service, run)
    assert result.status == "budget_exhausted" and len(provider.calls) == 1
    assert repo.steps[run.agent_run_id][0].output_feedback["category"] == "invalid_decision"


@pytest.mark.asyncio
async def test_extra_fields_and_dynamic_keys_do_not_retry_or_leak(caplog):
    sentinel = "private-key-and-input"
    repo, provider, service, _, _, run = await _system(
        [
            ({"kind": "stop", "reason_code": "done", sentinel: sentinel}, {"total_tokens": 7}),
        ]
    )
    result = await _advance(service, run)
    assert result.status == "failed" and len(provider.calls) == 1
    assert sentinel not in json.dumps(dict(repo.steps[run.agent_run_id][0].output_feedback))
    assert sentinel not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_kind", ["stop", "propose"])
async def test_correction_opportunity_survives_feedback_commit_crash(initial_kind):
    class CrashAfterFeedback(fixture.MemoryAgentRepository):
        crash = True

        async def finalize_model_step(self, *args, **kwargs):
            run = await super().finalize_model_step(*args, **kwargs)
            if run.status == "ready" and self.crash:
                self.crash = False
                raise RuntimeError("offline crash after commit")
            return run

    repo, provider, service, _, _, run = await _system(
        [
            ({"kind": initial_kind}, {"total_tokens": 7}),
            ({"kind": "stop", "reason_code": "done"}, {"total_tokens": 11}),
        ],
        repo=CrashAfterFeedback(),
    )
    with pytest.raises(RuntimeError, match="after commit"):
        await _advance(service, run)
    result = await _advance(service, repo.runs[run.agent_run_id])
    assert result.status == "completed" and len(provider.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_kind", ["stop", "propose"])
async def test_reserved_correction_crash_is_unknown_without_new_call(initial_kind):
    class CrashAfterReserve(fixture.MemoryAgentRepository):
        async def reserve_model_step(self, *args, **kwargs):
            run = await super().reserve_model_step(*args, **kwargs)
            if kwargs["step"].output_feedback is not None:
                raise RuntimeError("offline crash after correction reserve")
            return run

    repo, provider, service, _, _, run = await _system(
        [
            ({"kind": initial_kind}, {"total_tokens": 7}),
            ({"kind": "stop", "reason_code": "done"}, {"total_tokens": 11}),
        ],
        repo=CrashAfterReserve(),
    )
    with pytest.raises(RuntimeError, match="after correction reserve"):
        await _advance(service, run)
    result = await _advance(service, repo.runs[run.agent_run_id])
    assert result.termination_reason == "model_call_outcome_unknown"
    assert len(provider.calls) == 1 and repo.steps[run.agent_run_id][1].output_feedback


@pytest.mark.asyncio
async def test_correction_is_not_reset_by_human_input():
    repo, provider, service, _, _, run = await _system(
        [
            ({"kind": "stop"}, {"total_tokens": 7}),
            (
                {"kind": "request_human_input", "question": "Which bounded condition?"},
                {"total_tokens": 11},
            ),
            ({"kind": "stop"}, {"total_tokens": 3}),
        ]
    )
    first = await _advance(service, run)
    resumed = await service.submit_human_input(
        context=fixture._context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=first.row_version,
        text="stop",
    )
    result = await _advance(service, resumed)
    assert result.status == "failed" and len(provider.calls) == 3
    assert (
        sum(
            bool(item.output_feedback and item.output_feedback.get("correction_of_step_id"))
            for item in repo.steps[run.agent_run_id]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_known_json_syntax_failure_can_correct_once():
    repo, provider, service, _, _, run = await _system(
        [
            ProviderError(
                "provider_invalid_json", usage={"total_tokens": 7}, response_completed=True
            ),
            ({"kind": "stop", "reason_code": "done"}, {"total_tokens": 11}),
        ]
    )
    result = await _advance(service, run)
    assert result.status == "completed" and result.total_tokens == 18
    assert repo.steps[run.agent_run_id][0].output_feedback["code"] == "provider_invalid_json"


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_kind", ["stop", "propose"])
async def test_input_drift_after_feedback_commit_is_terminal(initial_kind):
    class CrashAfterFeedback(fixture.MemoryAgentRepository):
        async def finalize_model_step(self, *args, **kwargs):
            run = await super().finalize_model_step(*args, **kwargs)
            if run.status == "ready":
                raise RuntimeError("offline commit crash")
            return run

    repo, provider, service, _, _, run = await _system(
        [
            ({"kind": initial_kind}, {"total_tokens": 7}),
            ({"kind": "stop", "reason_code": "must_not_run"}, {"total_tokens": 11}),
        ],
        repo=CrashAfterFeedback(),
    )
    with pytest.raises(RuntimeError):
        await _advance(service, run)
    source = service._repository_source
    source.descriptor = replace(source.descriptor, head_commit="d" * 40)
    result = await _advance(service, repo.runs[run.agent_run_id])
    assert result.termination_reason == "correction_context_changed" and len(provider.calls) == 1


@pytest.mark.asyncio
async def test_duplicate_finalize_cannot_double_count():
    repo, provider, service, _, _, run = await _system(
        [
            ({"kind": "stop", "reason_code": "done"}, {"total_tokens": 7}),
        ]
    )
    result = await _advance(service, run)
    (step,) = repo.steps[run.agent_run_id]
    with pytest.raises(AgentRuntimeError, match="row_version_conflict"):
        await repo.finalize_model_step(
            run.agent_run_id,
            expected_row_version=result.row_version,
            agent_step_id=step.agent_step_id,
            outcome="succeeded",
            token_usage={"total_tokens": 7},
            decision_kind="stop",
            decision_json=step.decision_json,
            total_tokens=7,
            run_status="completed",
            termination_reason="done",
            completed_at=step.completed_at,
        )
    assert repo.runs[run.agent_run_id].total_tokens == 7 and len(provider.calls) == 1


@pytest.mark.parametrize(
    "feedback",
    [
        {"category": "invalid_decision", "code": "invalid_agent_decision", "error_count": True},
        {"category": "correction", "code": "correction_reserved", "error_count": 0},
        {
            "category": "invalid_decision",
            "code": "invalid_agent_decision",
            "error_count": 1,
            "correction_allowed": True,
            "errors": [],
        },
        {"category": "summary_omitted", "code": "invalid_agent_decision", "error_count": 1},
        {
            "category": "provider_invalid_output",
            "code": "provider_invalid_json",
            "error_count": 0,
            "secret-key": "sentinel",
        },
    ],
)
def test_corrupt_feedback_never_defaults_to_new_correction(feedback):
    from labpilot.agent_runtime.types import read_feedback

    with pytest.raises(AgentRuntimeError, match="output_feedback_invalid"):
        read_feedback(feedback)


@pytest.mark.asyncio
async def test_more_than_eight_errors_cannot_hide_extra_field():
    payload = {
        "kind": "stop",
        "reason_code": "done",
        "summary": "中" * 334,
        **{"private_extra_" + str(i): "sentinel" for i in range(9)},
    }
    repo, provider, service, _, _, run = await _system([(payload, {"total_tokens": 7})])
    result = await _advance(service, run)
    (step,) = repo.steps[run.agent_run_id]
    assert result.status == "failed" and len(provider.calls) == 1
    assert step.output_feedback["error_count"] == 10
    assert len(step.output_feedback["errors"]) == 8
    assert not step.output_feedback["correction_allowed"] and step.decision_json is None
    assert "sentinel" not in json.dumps(dict(step.output_feedback))


@pytest.mark.asyncio
async def test_legacy_provider_valid_output_and_trace_remain_compatible():
    from labpilot.api.schemas.agent_runs import AgentRunTraceResponse

    descriptor, repo = fixture._descriptor(), fixture.MemoryAgentRepository()
    provider = fixture.ScriptedProvider([{"kind": "stop", "reason_code": "done"}])
    service, _, _ = fixture._service(repo, descriptor, provider)
    run = await service.create_run(
        context=fixture._context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        goal="legacy",
        budget=fixture._budget(),
    )
    result = await _advance(service, run)
    response = AgentRunTraceResponse.from_details(await service.get_trace(run.agent_run_id))
    assert result.status == "completed" and response.steps[0].output_feedback is None
    assert response.model_dump(mode="json")["steps"][0]["output_feedback"] is None


@pytest.mark.asyncio
async def test_summary_omission_does_not_consume_later_correction():
    repo, provider, service, _, _, run = await _system(
        [
            (
                {
                    "kind": "request_human_input",
                    "question": "Choose a condition",
                    "summary": "中" * 334,
                },
                {"total_tokens": 7},
            ),
            ({"kind": "stop"}, {"total_tokens": 3}),
            ({"kind": "stop", "reason_code": "done"}, {"total_tokens": 4}),
        ]
    )
    first = await _advance(service, run)
    resumed = await service.submit_human_input(
        context=fixture._context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=first.row_version,
        text="stop",
    )
    result = await _advance(service, resumed)
    assert result.status == "completed" and len(provider.calls) == 3
    assert (
        sum(
            bool(step.output_feedback and step.output_feedback.get("correction_of_step_id"))
            for step in repo.steps[run.agent_run_id]
        )
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        {"kind": "stop"},
        {"kind": "propose"},
        {"kind": "propose", "proposal": []},
        {"kind": "propose", "summary": "中" * 334},
    ],
)
async def test_correction_can_propose_full_explicit_conditions_without_replacing_values(invalid):
    repo, provider, service, _, proposals, run = await _system(
        [(invalid, {"total_tokens": 3})]
    )
    payload = fixture._proposal(service._repository_source.descriptor)
    payload["parameter_matrix"] = {"learning_rate": [0.0005, 0.005], "epochs": [100, 1000]}
    payload["seeds"] = [7]
    payload["timeout_seconds"] = 30
    payload["max_retries"] = 1
    payload["train_argv_template"] = [
        "--lr",
        "{parameter:learning_rate}",
        "--epochs",
        "{parameter:epochs}",
        "--seed",
        "{seed}",
        "--dataset",
        "{dataset}",
        "--output",
        "{output_dir}",
    ]
    provider.script.append(({"kind": "propose", "proposal": payload}, {"total_tokens": 5}))
    result = await _advance(service, run)
    assert result.termination_reason == "proposal_created" and len(proposals.calls) == 1
    decision = repo.steps[run.agent_run_id][-1].decision_json["proposal"]
    assert decision["parameter_matrix"] == payload["parameter_matrix"] and decision["seeds"] == [7]
    assert decision["timeout_seconds"] == 30 and decision["max_retries"] == 1
    assert result.total_model_calls == 2 and result.total_tokens == 8
    first, second = repo.steps[run.agent_run_id]
    assert first.outcome == "failed" and first.decision_json is None
    assert first.output_feedback["correction_allowed"] is True
    assert second.output_feedback["correction_of_step_id"] == str(first.agent_step_id)


@pytest.mark.parametrize("json_mode", [False, True])
def test_actual_validation_locations_match_correction_whitelist(json_mode):
    from labpilot.agent_runtime.service import _correctable_error

    adapter = TypeAdapter(AgentDecision)
    descriptor = fixture._descriptor()
    validate = (
        (lambda value: adapter.validate_json(json.dumps(value)))
        if json_mode
        else adapter.validate_python
    )
    for payload in [{"kind": "propose"}, {"kind": "propose", "proposal": []}]:
        with pytest.raises(ValidationError) as caught:
            validate(payload)
        assert all(_correctable_error(item) for item in caught.value.errors())
    for field, value in [
        ("parameter_matrix", []),
        ("seeds", "bad"),
        ("seeds", ["bad"]),
        ("timeout_seconds", "bad"),
        ("max_retries", "bad"),
    ]:
        proposal = fixture._proposal(descriptor)
        proposal[field] = value
        with pytest.raises(ValidationError) as caught:
            validate({"kind": "propose", "proposal": proposal})
        assert all(
            _correctable_error(item)
            for item in caught.value.errors(
                include_input=False, include_context=False, include_url=False
            )
        )


@pytest.mark.parametrize(
    "content",
    [
        b'{"usage":{"total_tokens":6},"usage":{"total_tokens":0},"choices":[]}',
        b'{"choices":[],"choices":[]}',
    ],
)
def test_ambiguous_envelope_cannot_prove_completion(content):
    with pytest.raises(ProviderError, match="provider_malformed_response") as caught:
        _decode_response(content)
    assert not caught.value.response_completed and not caught.value.usage


def test_content_parser_recursion_error_retains_usage_and_cannot_retry(monkeypatch):
    import labpilot.agent_runtime.deepseek as module

    parse = module._json

    def failing_parser(value):
        if isinstance(value, str):
            raise RecursionError
        return parse(value)

    # ponytail: inject the parser failure; native recursion thresholds vary across Python builds.
    monkeypatch.setattr(module, "_json", failing_parser)
    with pytest.raises(ProviderError, match="provider_ambiguous_output") as caught:
        _decode_response(_envelope('{"kind":"stop","reason_code":"done"}'))
    assert caught.value.usage["total_tokens"] == 6 and not caught.value.response_completed


@pytest.mark.parametrize("field", ["tool_calls", "function_call"])
def test_uncontracted_wire_actions_are_not_decisions(field):
    envelope = json.loads(_envelope('{"kind":"stop","reason_code":"done"}'))
    envelope["choices"][0]["message"][field] = {"sentinel": "do not retain"}
    with pytest.raises(ProviderError, match="provider_wire_action_invalid") as caught:
        _decode_response(json.dumps(envelope).encode())
    assert caught.value.usage["total_tokens"] == 6 and not caught.value.response_completed


def test_unknown_or_nested_summary_error_cannot_be_omitted():
    from labpilot.agent_runtime.service import _summary_only

    assert not _summary_only([{"loc": ("stop", "summary"), "type": "value_error"}])
    assert not _summary_only(
        [{"loc": ("propose", "proposal", "summary"), "type": "text_too_large"}]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workdir", "../private"),
        ("repository_snapshot_id", "invalid-uuid"),
        ("dataset_reference", {}),
        ("environment_expectation", {}),
        ("sentinel_private_field", "sentinel_private_input"),
    ],
)
async def test_invalid_proposal_fields_cannot_hide_behind_correctable_error(field, value, caplog):
    repo, provider, service, tools, proposals, run = await _system([])
    payload = fixture._proposal(service._repository_source.descriptor)
    payload[field] = value
    provider.script.append(
        ({"kind": "propose", "proposal": payload, "summary": "中" * 334}, {"total_tokens": 7})
    )
    result = await _advance(service, run)
    assert result.status == "failed" and len(provider.calls) == 1
    assert not proposals.calls and tools.executions == 0
    (step,) = repo.steps[run.agent_run_id]
    assert not step.output_feedback["correction_allowed"] and step.decision_json is None
    assert "sentinel_private" not in json.dumps(dict(step.output_feedback)) + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("container", [{}, {"proposal": []}])
async def test_missing_proposal_with_unknown_decision_key_is_terminal(container, caplog):
    payload = {"kind": "propose", **container, "sentinel_private_field": "sentinel_private_input"}
    repo, provider, service, tools, proposals, run = await _system([(payload, {"total_tokens": 7})])
    result = await _advance(service, run)
    assert result.status == "failed" and len(provider.calls) == 1
    assert not proposals.calls and tools.executions == 0
    (step,) = repo.steps[run.agent_run_id]
    assert not step.output_feedback["correction_allowed"] and step.decision_json is None
    assert "sentinel_private" not in json.dumps(dict(step.output_feedback)) + caplog.text
