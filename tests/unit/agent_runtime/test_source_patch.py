import json
from dataclasses import replace
from hashlib import sha256
from unittest.mock import AsyncMock

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.unit.agent_runtime.test_agent_service import (
    CrashAfterSuccessfulFinalizeRepository,
    MemoryAgentRepository,
    ScriptedProvider,
    _budget,
    _descriptor,
    _proposal,
    _service,
)
from tests.unit.context.test_source_candidates import BASE, CLIPPED, patch

from labpilot.agent_runtime.provider import ModelResponse
from labpilot.agent_runtime.source_patch import SourcePatchContext, SourcePatchInput
from labpilot.agent_runtime.types import (
    AgentDecision,
    AgentRuntimeError,
    SourcePatchDecision,
    source_patch_bytes,
)
from labpilot.api.schemas.agent_runs import CreateAgentRunRequest
from labpilot.common.commands import CommandActor, CommandContext

CONTEXT = CommandContext("test", CommandActor("user", "human"), "source-patch")
PATCH = patch(CLIPPED).decode()


def setup(script, repository=None):
    repository = repository or MemoryAgentRepository()
    descriptor = _descriptor()
    provider = ScriptedProvider(script)
    service, tools, proposals = _service(repository, descriptor, provider)
    evidence = SourcePatchInput(
        context=SourcePatchContext(
            base_snapshot_identity=descriptor.repository_snapshot_identity,
            train_py_sha256=sha256(BASE).hexdigest(),
        ),
        train_py=BASE.decode(),
    )
    service._source_patch_loader = AsyncMock(return_value=evidence)
    return service, repository, descriptor, provider, tools, proposals


async def create(s, d, **kwargs):
    return await s.create_run(
        context=CONTEXT,
        project_id=d.project_id,
        repository_snapshot_id=d.repository_snapshot_id,
        goal="Draft clipping only; do not execute.",
        budget=_budget(),
        source_patch=True,
        **kwargs,
    )


async def advance(s, r):
    return await s.advance_run(
        context=CONTEXT, agent_run_id=r.agent_run_id, expected_row_version=r.row_version
    )


@pytest.mark.parametrize("value", ["", "  ", "\x00", "\ud800", "x" * 32769, "中" * 10923])
def test_patch_validates_bytes_without_echoing_them(value):
    with pytest.raises(ValidationError):
        SourcePatchDecision(kind="source_patch", patch=value)


@pytest.mark.parametrize("value", ["true", 1, None])
def test_source_patch_opt_in_is_a_strict_boolean(value):
    with pytest.raises(ValidationError):
        CreateAgentRunRequest.model_validate(
            dict(
                repository_snapshot_id=_descriptor().repository_snapshot_id,
                goal="g",
                budget=_budget().model_dump(),
                source_patch=value,
            )
        )


def test_ordinary_decision_contract_has_no_patch_branch():
    with pytest.raises(ValidationError):
        TypeAdapter(AgentDecision).validate_python({"kind": "source_patch", "patch": PATCH})


async def test_patch_is_persisted_atomically_exported_exactly_and_never_executed():
    s, repo, d, p, tools, proposals = setup([{"kind": "source_patch", "patch": PATCH}])
    r = await create(s, d)

    def before_call(request):
        assert repo.steps[r.agent_run_id][-1].outcome == "reserved"
        assert repo.runs[r.agent_run_id].status == "running"
        assert not repo.active

    p.before_call = before_call
    done = await advance(s, r)
    assert done.status == "completed" and done.termination_reason == "source_patch_drafted"
    assert done.produced_proposal_revision_id is None
    steps = await repo.list_steps(r.agent_run_id)
    assert source_patch_bytes(done, steps) == PATCH.encode()
    assert source_patch_bytes(done, steps) == PATCH.encode()
    user = json.loads(p.calls[0].messages[1]["content"])
    system = json.loads(p.calls[0].messages[0]["content"])
    assert user["source_patch"]["train_py"] == BASE.decode()
    assert system["agent_decision_schema"] == p.calls[0].output_schema
    assert "proposal_contract" not in system
    assert not tools.executions and not proposals.calls and len(p.calls) == 1


async def test_patch_mode_rejects_experiment_propose():
    s, repo, d, p, _, proposals = setup([])
    p.script = [{"kind": "propose", "proposal": _proposal(d)}]
    r = await advance(s, await create(s, d))
    assert r.status == "failed" and r.termination_reason == "invalid_agent_decision"
    assert not proposals.calls
    with pytest.raises(AgentRuntimeError):
        source_patch_bytes(r, await repo.list_steps(r.agent_run_id))


async def test_stop_cannot_impersonate_a_patch():
    s, repo, d, _, _, _ = setup([{"kind": "stop", "reason_code": "source_patch_drafted"}])
    r = await advance(s, await create(s, d))
    assert r.status == "completed"
    with pytest.raises(AgentRuntimeError, match="source_patch_not_ready"):
        source_patch_bytes(r, await repo.list_steps(r.agent_run_id))


async def test_response_budget_wins_over_patch_completion():
    s, repo, d, _, _, _ = setup(
        [({"kind": "source_patch", "patch": PATCH}, {"total_tokens": 1000})]
    )
    r = await advance(s, await create(s, d))
    assert r.status == "budget_exhausted" and r.total_tokens == 1000
    assert (await repo.list_steps(r.agent_run_id))[-1].decision_kind == "source_patch"
    with pytest.raises(AgentRuntimeError, match="source_patch_not_ready"):
        source_patch_bytes(r, await repo.list_steps(r.agent_run_id))


async def test_human_input_then_patch_keeps_mode():
    s, repo, d, p, _, _ = setup(
        [
            {"kind": "request_human_input", "question": "clip threshold?"},
            {"kind": "source_patch", "patch": PATCH},
        ]
    )
    r = await advance(s, await create(s, d))
    assert r.status == "awaiting_human"
    r = await s.submit_human_input(
        context=CONTEXT, agent_run_id=r.agent_run_id, expected_row_version=r.row_version, text="1.0"
    )
    r = await advance(s, r)
    assert source_patch_bytes(r, await repo.list_steps(r.agent_run_id)) == PATCH.encode()
    assert len(p.calls) == 2


async def test_reserved_unknown_precedes_missing_source():
    s, repo, d, p, _, _ = setup([])
    r = repo.inject_reserved((await create(s, d)).agent_run_id)
    s._source_patch_loader.side_effect = AgentRuntimeError("source_patch_source_invalid")
    r = await advance(s, r)
    assert r.termination_reason == "model_call_outcome_unknown" and not p.calls


async def test_source_loss_fails_before_new_provider_call():
    s, _, d, p, _, _ = setup([])
    r = await create(s, d)
    s._source_patch_loader.side_effect = AgentRuntimeError("source_patch_source_invalid")
    r = await advance(s, r)
    assert r.status == "failed" and r.termination_reason == "source_patch_source_invalid"
    assert not p.calls


async def test_crash_after_commit_leaves_exportable_patch_without_resampling():
    s, repo, d, p, _, _ = setup(
        [{"kind": "source_patch", "patch": PATCH}], CrashAfterSuccessfulFinalizeRepository()
    )
    r = await create(s, d)
    with pytest.raises(RuntimeError, match="crash after committed"):
        await advance(s, r)
    r = await s.get_run(r.agent_run_id)
    assert source_patch_bytes(r, await repo.list_steps(r.agent_run_id)) == PATCH.encode()
    assert len(p.calls) == 1


async def test_patch_requires_known_human_and_configured_loader():
    s, repo, d, _, _, _ = setup([])
    with pytest.raises(AgentRuntimeError, match="source_patch_user_required"):
        await s.create_run(
            context=replace(CONTEXT, actor=CommandActor("agent", "model")),
            project_id=d.project_id,
            repository_snapshot_id=d.repository_snapshot_id,
            goal="g",
            budget=_budget(),
            source_patch=True,
        )
    s._source_patch_loader = None
    with pytest.raises(AgentRuntimeError, match="source_patch_unconfigured"):
        await create(s, d)
    assert not repo.runs


async def test_completed_output_can_use_one_bounded_patch_correction():
    s, repo, d, p, _, _ = setup(
        [{"kind": "source_patch"}, {"kind": "source_patch", "patch": PATCH}]
    )
    original = p.complete

    async def complete(request):
        response = await original(request)
        return ModelResponse(response.payload, {"total_tokens": 1}, response_completed=True)

    p.complete = complete
    r = await advance(s, await create(s, d))
    steps = await repo.list_steps(r.agent_run_id)
    assert r.status == "completed" and len(p.calls) == 2
    assert steps[0].output_feedback["errors"][0]["loc"] == ["source_patch", "patch"]
    assert steps[1].output_feedback["correction_of_step_id"] == str(steps[0].agent_step_id)
    assert source_patch_bytes(r, steps) == PATCH.encode()


async def test_bad_summary_is_omitted_without_losing_patch_or_an_extra_call():
    s, repo, d, p, _, _ = setup([{"kind": "source_patch", "patch": PATCH, "summary": "x" * 1001}])
    original = p.complete

    async def complete(request):
        response = await original(request)
        return ModelResponse(response.payload, {"total_tokens": 1}, response_completed=True)

    p.complete = complete
    r = await advance(s, await create(s, d))
    assert source_patch_bytes(r, await repo.list_steps(r.agent_run_id)) == PATCH.encode()
    assert len(p.calls) == 1


async def test_ordinary_runtime_rejects_source_patch_output():
    s, repo, d, p, _, proposals = setup([{"kind": "source_patch", "patch": PATCH}])
    r = await s.create_run(
        context=CONTEXT,
        project_id=d.project_id,
        repository_snapshot_id=d.repository_snapshot_id,
        goal="ordinary experiment",
        budget=_budget(),
    )
    assert r.source_patch_context is None
    r = await advance(s, r)
    assert r.status == "failed" and r.termination_reason == "invalid_agent_decision"
    assert not proposals.calls and len(p.calls) == 1
    user = json.loads(p.calls[0].messages[1]["content"])
    assert "source_patch" not in user


async def test_invalid_patch_diagnostics_never_echo_source_or_dynamic_keys(caplog):
    s, repo, d, p, _, _ = setup([{"kind": "source_patch", "patch": "PRIVATE_SOURCE" + "\0"}])
    r = await advance(s, await create(s, d))
    assert r.status == "failed"
    assert "PRIVATE_SOURCE" not in caplog.text
    assert "source_patch" in caplog.text and "text_nul" in caplog.text
