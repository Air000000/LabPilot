from __future__ import annotations

import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from labpilot.agent_runtime.context_compiler import ContextCompilationError, ContextCompiler
from labpilot.agent_runtime.result_context import ResultContext, ResultTrialCondition
from labpilot.context.repository_source import RepositorySourceDescriptor
from labpilot.experiments.proposal_types import (
    ArtifactDeclaration,
    DatasetReference,
    EnvironmentExpectation,
    MetricDeclaration,
    ProposalPayload,
    ValidationRule,
)


def _descriptor() -> RepositorySourceDescriptor:
    return RepositorySourceDescriptor(
        repository_snapshot_id=uuid4(),
        repository_snapshot_identity="b" * 64,
        repository_registration_id=uuid4(),
        project_id=uuid4(),
        head_commit="c" * 40,
        current_branch="main",
        is_detached_head=False,
        is_clean=True,
    )


def _proposal(snapshot: RepositorySourceDescriptor) -> ProposalPayload:
    return ProposalPayload(
        goal="Improve validation accuracy",
        experiment_type="parameter_sweep",
        repository_snapshot_id=snapshot.repository_snapshot_id,
        repository_snapshot_identity=snapshot.repository_snapshot_identity,
        train_entrypoint="train.py",
        eval_entrypoint=None,
        workdir=".",
        parameter_matrix={"learning_rate": (0.001, 0.01)},
        seeds=(7,),
        dataset_reference=DatasetReference(
            reference_type="uri",
            path_or_uri="s3://datasets/example",
            stable_identity="dataset:v1",
            reproducibility_level="strict",
        ),
        environment_expectation=EnvironmentExpectation(
            python_profile="python-3.12",
            framework_requirements=(),
            device_requirement=None,
            cuda_requirement=None,
        ),
        metrics=(MetricDeclaration("accuracy", "result", "test"),),
        artifacts=(ArtifactDeclaration("result", "result.json", True),),
        validation_rules=(
            ValidationRule("artifact_exists", "result"),
            ValidationRule("metric_finite", "accuracy"),
        ),
        timeout_seconds=30,
        max_retries=1,
    )


def _result_context(
    snapshot: RepositorySourceDescriptor,
    *,
    summary_revision_id=None,
    numeric_value: str = "0.941",
    warning_blob: str | None = None,
) -> ResultContext:
    trial_id = uuid4()
    included = {
        "trial_id": str(trial_id),
        "trial_ordinal": 1,
        "selected_attempt_id": str(uuid4()),
        "validation_report_id": str(uuid4()),
        "metrics": [
            {
                "metric_observation_id": str(uuid4()),
                "artifact_id": str(uuid4()),
                "metric_name": "accuracy",
                "split": "test",
                "numeric_value": numeric_value,
            }
        ],
    }
    warnings = ()
    if warning_blob is not None:
        warnings = (
            {
                "code": "SYNTHETIC",
                "subject": "accuracy",
                "details": {"blob": warning_blob},
            },
        )
    return ResultContext(
        experiment_id=uuid4(),
        source_experiment_version_id=uuid4(),
        source_version_number=1,
        source_proposal_revision_id=uuid4(),
        source_batch_id=uuid4(),
        source_batch_summary_revision_id=summary_revision_id or uuid4(),
        source_summary_revision_number=1,
        presentation_status="complete",
        source_proposal=_proposal(snapshot),
        trials=(
            ResultTrialCondition(
                trial_id=trial_id,
                trial_ordinal=1,
                resolved_parameters={"learning_rate": 0.01},
                seed=7,
            ),
        ),
        included_results=(included,),
        excluded_results=(),
        warnings=warnings,
        aggregates={"accuracy": {"count": 1, "mean": numeric_value}},
    )


def _compile(
    compiler: ContextCompiler,
    snapshot: RepositorySourceDescriptor,
    *,
    result_context: ResultContext | None,
):
    return compiler.compile(
        provider_name="scripted",
        model="test-model",
        goal="decide the next experiment",
        snapshot=snapshot,
        definitions=(),
        prior_tool_results=(),
        prior_human_inputs=(),
        result_context=result_context,
    )


def test_initial_context_none_preserves_existing_request_shape_and_hash() -> None:
    compiler = ContextCompiler()
    snapshot = _descriptor()

    implicit = compiler.compile(
        provider_name="scripted",
        model="test-model",
        goal="decide the next experiment",
        snapshot=snapshot,
        definitions=(),
        prior_tool_results=(),
        prior_human_inputs=(),
    )
    explicit = _compile(compiler, snapshot, result_context=None)

    assert explicit.request == implicit.request
    assert explicit.canonical_request_hash == implicit.canonical_request_hash
    user = json.loads(explicit.request.messages[1]["content"])
    assert "result_context" not in user


def test_follow_up_context_is_bounded_owned_input_and_hashes_exact_result_facts() -> None:
    compiler = ContextCompiler()
    snapshot = _descriptor()
    summary_id = uuid4()
    first_context = _result_context(snapshot, summary_revision_id=summary_id)
    changed_metric = first_context.model_copy(
        update={
            "included_results": (
                {
                    **first_context.included_results[0],
                    "metrics": [
                        {
                            **first_context.included_results[0]["metrics"][0],
                            "numeric_value": "0.950",
                        }
                    ],
                },
            )
        }
    )
    changed_summary = first_context.model_copy(
        update={"source_batch_summary_revision_id": uuid4()}
    )

    first = _compile(compiler, snapshot, result_context=first_context)
    metric_changed = _compile(compiler, snapshot, result_context=changed_metric)
    summary_changed = _compile(compiler, snapshot, result_context=changed_summary)

    user = json.loads(first.request.messages[1]["content"])
    assert user["result_context"]["source_batch_summary_revision_id"] == str(summary_id)
    assert user["result_context"]["included_results"][0]["metrics"][0]["numeric_value"] == "0.941"
    assert first.canonical_request_hash != metric_changed.canonical_request_hash
    assert first.canonical_request_hash != summary_changed.canonical_request_hash


def test_follow_up_system_policy_preserves_result_authority_and_governance() -> None:
    compiler = ContextCompiler()
    snapshot = _descriptor()

    compiled = _compile(compiler, snapshot, result_context=_result_context(snapshot))
    system = json.loads(compiled.request.messages[0]["content"])
    policy = "\n".join(system["result_context_policy"])

    assert "selected" in policy and "eligible" in policy
    assert "exclusion" in policy
    assert "invalid raw" in policy
    assert "stop" in policy
    assert "full ProposalPayload" in policy
    assert "start" in policy and "approve" in policy


def test_result_context_has_individual_128_kib_bound_before_model_request() -> None:
    compiler = ContextCompiler()
    snapshot = _descriptor()
    context = _result_context(snapshot, warning_blob="x" * (129 * 1024))

    with pytest.raises(ContextCompilationError, match="result_context_too_large"):
        _compile(compiler, snapshot, result_context=context)


def test_total_compiled_message_bound_still_applies_with_result_context() -> None:
    compiler = ContextCompiler()
    snapshot = _descriptor()

    with pytest.raises(ContextCompilationError, match="compiled_messages_too_large"):
        compiler.compile(
            provider_name="scripted",
            model="test-model",
            goal="decide the next experiment",
            snapshot=snapshot,
            definitions=(),
            prior_tool_results=(),
            prior_human_inputs=("h" * 4000,) * 70,
            result_context=_result_context(snapshot),
        )


def test_result_context_models_reject_extra_fields() -> None:
    snapshot = _descriptor()
    context = _result_context(snapshot)

    with pytest.raises(ValidationError):
        ResultContext.model_validate(
            {**context.model_dump(mode="json"), "unexpected": True}
        )
