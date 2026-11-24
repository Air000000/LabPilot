import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest
from tests.unit.agent_runtime.test_result_context import _descriptor, _result_context
from tests.unit.agent_runtime.test_result_history import _history

from labpilot.agent_runtime.context_compiler import ContextCompiler, PriorToolResult
from labpilot.agent_runtime.result_context import ResultTrialCondition
from labpilot.agent_runtime.result_history import (
    HistoryBatchEvidence,
    HistoryContext,
    history_tool_definition,
    validate_history_binding,
)
from labpilot.agent_runtime.trial_facts import TrialCounts, TrialFactView, freeze_trial_facts
from labpilot.agent_runtime.types import AgentRuntimeError
from labpilot.tools_policy.types import bound_toolset_hash


def _source(selected=2, excluded=4):
    source = _result_context(_descriptor())
    trials = tuple(
        ResultTrialCondition(
            trial_id=UUID(int=100 + i),
            trial_ordinal=i + 1,
            resolved_parameters={"epochs": 100},
            seed=7,
        )
        for i in range(selected + excluded)
    )
    refs = tuple(
        {
            "trial_id": str(t.trial_id),
            "trial_ordinal": t.trial_ordinal,
            "metrics": [{"numeric_value": "0.925"}] * 3,
        }
        for t in trials
    )
    return source.model_copy(
        update={
            "experiment_id": UUID(int=2),
            "source_batch_id": UUID(int=4),
            "source_batch_summary_revision_id": UUID(int=3),
            "source_version_number": 2,
            "trials": trials,
            "included_results": refs[:selected],
            "excluded_results": tuple(
                {
                    "trial_id": r["trial_id"],
                    "trial_ordinal": r["trial_ordinal"],
                    "exclusion_code": "VALIDATION_INVALID",
                }
                for r in refs[selected:]
            ),
        }
    )


def _batch(number, selected, excluded):
    value = deepcopy(_history()["batches"][0])
    value.update(
        batch_id=str(UUID(int=40 + number)),
        summary_revision_id=str(UUID(int=50 + number)),
        trial_count=selected + excluded,
    )
    template = value["trial_evidence"][0]
    trials = []
    for i in range(selected + excluded):
        trial = deepcopy(template)
        trial.update(trial_id=str(UUID(int=200 + number * 100 + i)), trial_ordinal=i + 1)
        if i >= selected:
            trial = {
                k: trial[k] for k in ("trial_id", "trial_ordinal", "resolved_parameters", "seed")
            }
            trial.update(outcome="excluded", exclusion_code="VALIDATION_INVALID")
        trials.append(trial)
    value["trial_evidence"] = trials
    return HistoryBatchEvidence.model_validate(value)


def _capture(source, batches):
    return HistoryContext.model_validate(
        {**_history(), "batches": batches, "trial_facts": freeze_trial_facts(source, batches)}
    )


def test_scopes_count_trials_and_union_seeds_not_metrics_or_same_conditions():
    source = _source()
    facts = freeze_trial_facts(source, (_batch(2, 8, 0), _batch(1, 1, 3)))
    assert [
        (c.total_trials, c.selected_valid, c.excluded, c.distinct_seeds)
        for c in (facts.source.counts, facts.included_history, facts.source_and_included_history)
    ] == [(6, 2, 4, 1), (12, 9, 3, 1), (18, 11, 7, 1)]
    reverse = source.model_copy(update={"trials": tuple(reversed(source.trials))})
    assert freeze_trial_facts(reverse, (_batch(2, 8, 0), _batch(1, 1, 3))) == facts
    assert "independent_replicate_count" not in facts.model_dump_json()
    different_seed = _batch(1, 1, 3).model_copy(
        update={
            "trial_evidence": tuple(
                t.model_copy(update={"seed": 9}) for t in _batch(1, 1, 3).trial_evidence
            )
        }
    )
    assert (
        freeze_trial_facts(source, (different_seed,)).source_and_included_history.distinct_seeds
        == 2
    )


def test_empty_legacy_all_excluded_and_omitted_ranges():
    source = _source(0, 6)
    empty = freeze_trial_facts(source, ())
    assert empty.included_history == TrialCounts(
        total_trials=0, selected_valid=0, excluded=0, distinct_seeds=0
    )
    assert empty.source.counts.excluded == 6
    batches = (_batch(2, 8, 0), _batch(1, 1, 3))
    assert freeze_trial_facts(source, batches[:1]).included_history.total_trials == 8
    legacy = HistoryContext.model_validate(_history())
    assert legacy.trial_facts is None and "trial_facts" not in legacy.model_dump(mode="json")


@pytest.mark.parametrize(
    "damage", ["duplicate", "missing", "ordinal", "cross_batch", "cross_source", "unknown_outcome"]
)
def test_bad_trial_coverage_rejected(damage):
    source, batches = _source(), (_batch(2, 8, 0), _batch(1, 1, 3))
    if damage == "duplicate":
        source = source.model_copy(update={"included_results": source.included_results * 2})
    elif damage == "missing":
        source = source.model_copy(update={"excluded_results": ()})
    elif damage == "unknown_outcome":
        source = source.model_copy(
            update={
                "excluded_results": (
                    {**source.excluded_results[0], "exclusion_code": "UNKNOWN"},
                    *source.excluded_results[1:],
                )
            }
        )
    elif damage == "ordinal":
        source = source.model_copy(
            update={
                "trials": (
                    source.trials[0].model_copy(update={"trial_ordinal": 99}),
                    *source.trials[1:],
                )
            }
        )
    else:
        bad_id = (
            batches[0].trial_evidence[0].trial_id
            if damage == "cross_batch"
            else source.trials[0].trial_id
        )
        last = batches[-1]
        batches = (
            batches[0],
            last.model_copy(
                update={
                    "trial_evidence": (
                        last.trial_evidence[0].model_copy(update={"trial_id": bad_id}),
                        *last.trial_evidence[1:],
                    )
                }
            ),
        )
    with pytest.raises(ValueError):
        freeze_trial_facts(source, batches)


@pytest.mark.parametrize(
    "damage", ["count", "profile", "source", "summary", "seed_union", "bool", "float", "extra"]
)
def test_tampered_capture_fails_before_provider(damage):
    source = _source()
    history = _capture(source, (_batch(2, 8, 0), _batch(1, 1, 3)))
    value = history.model_dump(mode="json")
    facts = value["trial_facts"]
    if damage == "count":
        facts["included_history"]["total_trials"] = 13
    elif damage == "profile":
        facts["profile"] = "invented"
    elif damage == "source":
        facts["source"]["batch_id"] = str(UUID(int=999))
    elif damage == "summary":
        facts["historical_batches"][0]["summary_revision_id"] = str(UUID(int=999))
    elif damage == "seed_union":
        facts["source_and_included_history"]["distinct_seeds"] = 2
    elif damage == "bool":
        facts["source"]["counts"]["distinct_seeds"] = True
    elif damage == "float":
        facts["source"]["counts"]["total_trials"] = 6.0
    else:
        facts["independent_replicate_count"] = 18
    run = SimpleNamespace(
        project_id=history.project_id,
        source_batch_summary_revision_id=UUID(int=3),
        history_context=value,
    )
    with pytest.raises(AgentRuntimeError):
        validate_history_binding(run, source)


@pytest.mark.asyncio
async def test_compiler_sends_source_only_until_history_tool_and_keeps_legacy_path():
    source = _source()
    history = _capture(source, (_batch(2, 8, 0), _batch(1, 1, 3)))
    definition = history_tool_definition(history)
    assert definition.version == "v2"
    legacy = history.model_copy(update={"trial_facts": None})
    old_definition = history_tool_definition(legacy)
    assert old_definition.version == "v1"
    assert bound_toolset_hash((definition,)) != bound_toolset_hash((old_definition,))
    kwargs = dict(
        provider_name="scripted",
        model="test",
        goal="Compare pinned facts",
        snapshot=_descriptor(),
        definitions=(definition,),
        prior_tool_results=(),
        prior_human_inputs=(),
        result_context=source,
    )
    compiler = ContextCompiler()
    first = compiler.compile(**kwargs, trial_facts=history.trial_facts.source)
    user = json.loads(first.request.messages[1]["content"])
    assert user["result_context"]["trial_facts"]["counts"]["total_trials"] == 6
    assert "included_history" not in first.request.messages[1]["content"]
    payload = await definition.executor({})
    second = compiler.compile(
        **{**kwargs, "prior_tool_results": (PriorToolResult(definition.name, payload),)},
        trial_facts=history.trial_facts.source,
    )
    later = json.loads(second.request.messages[1]["content"])
    assert (
        later["prior_tool_results"][0]["result"]["trial_facts"]["included_history"]["total_trials"]
        == 12
    )
    assert first.canonical_request_hash != second.canonical_request_hash
    implicit = compiler.compile(**{**kwargs, "definitions": (old_definition,)})
    explicit = compiler.compile(**{**kwargs, "definitions": (old_definition,)}, trial_facts=None)
    assert (
        implicit.request == explicit.request
        and implicit.canonical_request_hash == explicit.canonical_request_hash
    )
    assert "trial_facts" not in explicit.request.messages[1]["content"]
    assert (
        TrialFactView.model_validate_json(history.trial_facts.model_dump_json())
        == history.trial_facts
    )


def test_legacy_request_hashes_match_pre_feature_baseline():
    from unittest.mock import patch

    # Captured from the unchanged 93281b0 compiler, normal and correction input.
    with patch(
        "tests.unit.agent_runtime.test_result_context.uuid4",
        side_effect=(UUID(int=n) for n in range(2000, 2100)),
    ):
        source, snapshot = _source(), _descriptor()
    kwargs = dict(
        provider_name="scripted",
        model="test",
        goal="Compare pinned facts",
        snapshot=snapshot,
        definitions=(history_tool_definition(HistoryContext.model_validate(_history())),),
        prior_tool_results=(),
        prior_human_inputs=(),
        result_context=source,
    )
    normal = ContextCompiler().compile(**kwargs)
    feedback = dict(
        category="provider_invalid_output",
        code="provider_invalid_json",
        correction_allowed=True,
        error_count=0,
    )
    correction = ContextCompiler().compile(**kwargs, output_feedback=feedback)
    assert (
        normal.canonical_request_hash
        == "947200a00f1765510d3b14b8cdf6595de679282c9e740a1d2ce96832f5ad0227"
    )
    assert (
        correction.canonical_request_hash
        == "82685b31a41840553553a8f9dc784e40b68e9746613bcf9178b69a6a15c86017"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("crash", ["feedback", "reserved"])
async def test_new_profile_correction_recovery_keeps_capture(crash):
    from dataclasses import replace

    from tests.unit.agent_runtime import test_agent_service as fixture
    from tests.unit.agent_runtime.test_output_reliability import CompletedProvider

    source = _source()
    descriptor = fixture._descriptor()

    class CrashRepository(fixture.MemoryAgentRepository):
        pending_crash = True

        async def finalize_model_step(self, *args, **kwargs):
            run = await super().finalize_model_step(*args, **kwargs)
            if crash == "feedback" and run.status == "ready" and self.pending_crash:
                self.pending_crash = False
                raise RuntimeError("interrupt after feedback")
            return run

        async def reserve_model_step(self, *args, **kwargs):
            run = await super().reserve_model_step(*args, **kwargs)
            if (
                crash == "reserved"
                and kwargs["step"].output_feedback is not None
                and self.pending_crash
            ):
                self.pending_crash = False
                raise RuntimeError("interrupt after reserved")
            return run

    repository = CrashRepository()
    provider = CompletedProvider(
        [
            ({"kind": "stop"}, {"total_tokens": 3}),
            ({"kind": "stop", "reason_code": "done"}, {"total_tokens": 4}),
        ]
    )
    store = fixture.MemoryFollowUpRunStore(repository)
    service, _, _ = fixture._follow_up_service(
        repository,
        descriptor,
        provider,
        store=store,
        source=fixture.MemoryResultContextSource(source),
    )
    run = await service.create_follow_up_run(
        context=fixture._context(),
        project_id=descriptor.project_id,
        repository_snapshot_id=descriptor.repository_snapshot_id,
        source_batch_summary_revision_id=source.source_batch_summary_revision_id,
        goal="offline",
        budget=fixture._budget(),
    )
    history = _capture(source, (_batch(2, 8, 0), _batch(1, 1, 3)))
    history = history.model_copy(update={"project_id": descriptor.project_id})
    # Fixtures use a memory tool service; the production factory supplies the v2 tool.
    history = history.model_copy(
        update={"batches": (), "trial_facts": freeze_trial_facts(source, ())}
    )
    run = replace(run, history_context=history.model_dump(mode="json"))
    repository.runs[run.agent_run_id] = run

    with pytest.raises(RuntimeError, match="interrupt"):
        await service.advance_run(
            context=fixture._context(),
            agent_run_id=run.agent_run_id,
            expected_row_version=run.row_version,
        )
    recorded = repository.runs[run.agent_run_id]
    assert repository.steps[run.agent_run_id][0].output_feedback["correction_allowed"]
    resumed, _, _ = fixture._follow_up_service(
        repository,
        descriptor,
        provider,
        store=store,
        source=fixture.MemoryResultContextSource(source),
    )
    completed = await resumed.advance_run(
        context=fixture._context(),
        agent_run_id=run.agent_run_id,
        expected_row_version=recorded.row_version,
    )
    assert completed.history_context == run.history_context
    if crash == "reserved":
        assert completed.termination_reason == "model_call_outcome_unknown"
        assert len(provider.calls) == 1 and completed.total_tokens == 3
    else:
        assert (
            completed.status == "completed"
            and completed.total_model_calls == 2
            and completed.total_tokens == 7
        )
        assert len(provider.calls) == 2
    assert all(
        json.loads(request.messages[1]["content"])["result_context"]["trial_facts"]["counts"][
            "total_trials"
        ]
        == 6
        for request in provider.calls
    )
