import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest

from labpilot.agent_runtime.context_compiler import ContextCompiler, PriorToolResult
from labpilot.agent_runtime.result_history import (
    HistoryContext,
    history_tool_definition,
    validate_history_binding,
)
from labpilot.agent_runtime.types import AgentRuntimeError
from labpilot.context.repository_source import RepositorySourceDescriptor
from labpilot.experiments.proposal_types import ValidationRule, validation_protocol_hash
from labpilot.tools_policy.service import _validate_arguments
from labpilot.tools_policy.types import bound_toolset_hash, tool_result_hash


def _id(number: int) -> str:
    return str(UUID(int=number))


def _history() -> dict:
    return {
        "project_id": _id(1),
        "experiment_id": _id(2),
        "source_batch_summary_revision_id": _id(3),
        "source_batch_id": _id(4),
        "source_version_number": 2,
        "source_batch_created_at": "2026-10-05T02:00:00Z",
        "batches": [
            {
                "project_id": _id(1),
                "experiment_id": _id(2),
                "experiment_version_id": _id(5),
                "version_number": 1,
                "proposal_revision_id": _id(6),
                "batch_id": _id(7),
                "batch_created_at": "2026-10-05T01:00:00Z",
                "summary_revision_id": _id(8),
                "summary_revision_number": 1,
                "summary_protocol_version": "slice3-summary-v1",
                "trial_count": 1,
                "design": {
                    "repository_snapshot_id": _id(9),
                    "repository_snapshot_identity": "a" * 64,
                    "dataset_reference_hash": "b" * 64,
                    "dataset_reference": {
                        "reference_type": "uri",
                        "path_or_uri": "dataset://v1",
                        "stable_identity": "dataset-v1",
                        "reproducibility_level": "strict",
                    },
                    "environment_expectation_hash": "c" * 64,
                    "environment_expectation": {
                        "python_profile": "python-3.12",
                        "framework_requirements": [],
                        "device_requirement": "cpu",
                        "cuda_requirement": None,
                    },
                    "validation_protocol_hash": "d" * 64,
                    "train_entrypoint": "train.py",
                    "eval_entrypoint": None,
                    "workdir": ".",
                    "train_argv_template": None,
                    "metrics": [],
                    "artifacts": [],
                },
                "trial_evidence": [
                    {
                        "trial_id": _id(10),
                        "trial_ordinal": 1,
                        "resolved_parameters": {"epochs": 100},
                        "seed": 7,
                        "outcome": "selected_valid",
                        "selected_attempt_id": _id(11),
                        "validation": {
                            "validation_report_id": _id(12),
                            "validation_protocol_hash": "d" * 64,
                            "extraction_profile_version": "slice3-extraction-v1",
                            "validator_version": "slice3-validator-v1",
                        },
                        "metrics": [
                            {
                                "metric_name": "accuracy",
                                "split": "validation",
                                "numeric_value": "0.94100000000000000001",
                                "artifact_id": _id(13),
                                "metric_observation_id": _id(14),
                                "extractor_version": "result-json-v1",
                            }
                        ],
                    }
                ],
                "warnings": [],
            }
        ],
    }


def _bindings():
    run = SimpleNamespace(
        project_id=UUID(int=1),
        source_batch_summary_revision_id=UUID(int=3),
        history_context=_history(),
    )
    source = SimpleNamespace(
        experiment_id=UUID(int=2),
        source_batch_summary_revision_id=UUID(int=3),
        source_batch_id=UUID(int=4),
        source_version_number=2,
    )
    return run, source


def test_history_preserves_exact_numeric_text_and_trial_conditions() -> None:
    context = HistoryContext.model_validate(_history())
    trial = context.batches[0].trial_evidence[0]
    assert trial.resolved_parameters == {"epochs": 100} and trial.seed == 7
    assert trial.metrics[0].numeric_value == "0.94100000000000000001"


def test_history_exposes_exact_validation_threshold_and_checks_protocol() -> None:
    from decimal import Decimal

    value = _history()
    batch = value["batches"][0]
    protocol_hash = validation_protocol_hash(
        (ValidationRule("metric_gte", "accuracy", Decimal("0.92000000000000000001")),)
    )
    batch["design"]["validation_protocol_hash"] = protocol_hash
    batch["trial_evidence"][0]["validation"]["validation_protocol_hash"] = protocol_hash
    batch["design"]["validation_rules"] = [
        {"kind": "metric_gte", "subject": "accuracy", "threshold": "0.92000000000000000001"}
    ]
    context = HistoryContext.model_validate(value)
    assert context.model_dump(mode="json")["batches"][0]["design"]["validation_rules"][0][
        "threshold"
    ] == "0.92000000000000000001"
    batch["design"]["validation_rules"][0]["threshold"] = "0.80"
    with pytest.raises(ValueError, match="history_validation_protocol_invalid"):
        HistoryContext.model_validate(value)


def test_legacy_history_does_not_infer_missing_validation_rules() -> None:
    history = HistoryContext.model_validate(_history())
    assert history.batches[0].design.validation_rules is None
    assert "validation_rules" not in history.model_dump(mode="json")["batches"][0]["design"]


def test_history_report_must_match_its_frozen_design_protocol() -> None:
    value = _history()
    value["batches"][0]["trial_evidence"][0]["validation"]["validation_protocol_hash"] = "e" * 64
    with pytest.raises(ValueError, match="history_validation_protocol_invalid"):
        HistoryContext.model_validate(value)


@pytest.mark.parametrize(
    "mutation", ["coverage", "duplicate", "extra", "infinite", "scope", "later", "source"]
)
def test_invalid_history_is_rejected(mutation: str) -> None:
    value = _history()
    batch = value["batches"][0]
    if mutation == "coverage":
        batch["trial_count"] = 2
    elif mutation == "duplicate":
        batch["trial_count"] = 2
        batch["trial_evidence"].append(deepcopy(batch["trial_evidence"][0]))
    elif mutation == "extra":
        value["raw_logs"] = "SECRET"
    elif mutation == "infinite":
        batch["trial_evidence"][0]["resolved_parameters"]["epochs"] = float("inf")
    elif mutation == "scope":
        batch["experiment_id"] = _id(90)
    elif mutation == "later":
        batch["version_number"] = 3
    else:
        batch["batch_id"] = value["source_batch_id"]
    with pytest.raises(ValueError):
        HistoryContext.model_validate(value)


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "not-a-number"])
def test_history_numeric_values_must_be_finite(number: str) -> None:
    value = _history()
    value["batches"][0]["trial_evidence"][0]["metrics"][0]["numeric_value"] = number
    with pytest.raises(ValueError):
        HistoryContext.model_validate(value)


def test_excluded_history_cannot_include_raw_metrics() -> None:
    value = _history()
    value["batches"][0]["trial_evidence"][0] = {
        "trial_id": _id(10),
        "trial_ordinal": 1,
        "resolved_parameters": {"epochs": 100},
        "seed": 7,
        "outcome": "excluded",
        "exclusion_code": "VALIDATION_INVALID",
        "validation_reason_codes": ["METRIC_BELOW_THRESHOLD"],
    }
    context = HistoryContext.model_validate(value)
    assert context.batches[0].trial_evidence[0].outcome == "excluded"
    value["batches"][0]["trial_evidence"][0]["metrics"] = [{"numeric_value": "0.01"}]
    with pytest.raises(ValueError):
        HistoryContext.model_validate(value)


def test_history_binding_is_exact_and_legacy_null_is_not_recaptured() -> None:
    run, source = _bindings()
    assert validate_history_binding(run, source).experiment_id == UUID(int=2)
    run.history_context = None
    assert validate_history_binding(run, source) is None
    run.history_context = {}
    with pytest.raises(AgentRuntimeError, match="history_context_invalid"):
        validate_history_binding(run, source)


@pytest.mark.parametrize("field", ["project_id", "source_batch_summary_revision_id"])
def test_history_cannot_change_run_binding(field: str) -> None:
    run, source = _bindings()
    setattr(run, field, UUID(int=99))
    with pytest.raises(AgentRuntimeError, match="history_context_binding_invalid"):
        validate_history_binding(run, source)


def test_history_cannot_change_source_experiment() -> None:
    run, source = _bindings()
    source.experiment_id = UUID(int=99)
    with pytest.raises(AgentRuntimeError, match="history_context_binding_invalid"):
        validate_history_binding(run, source)


def test_empty_history_and_limited_history_are_distinct() -> None:
    value = _history()
    value["batches"] = []
    assert HistoryContext.model_validate(value).coverage_limited is False
    value["has_more_candidates"] = True
    value["coverage_limited"] = True
    assert HistoryContext.model_validate(value).coverage_limited is True
    value["coverage_limited"] = False
    with pytest.raises(ValueError):
        HistoryContext.model_validate(value)


def test_history_canonical_byte_limit_rejects_large_envelope() -> None:
    value = _history()
    value["batches"][0]["trial_evidence"][0]["resolved_parameters"]["note"] = "界" * 12000
    with pytest.raises(ValueError, match="history_context_too_large"):
        HistoryContext.model_validate(value)


@pytest.mark.asyncio
async def test_history_tool_is_bound_read_only_and_returns_independent_values() -> None:
    definition = history_tool_definition(HistoryContext.model_validate(_history()))
    assert (definition.name, definition.version) == ("experiment.read_history", "v1")
    assert definition.risk == "green" and definition.side_effect == "read_only"
    assert definition.approval_required is False
    assert _validate_arguments({}, definition.input_schema) == {}
    for arguments in ({"experiment_id": _id(99)}, {"max_batches": 100}, {"sql": "SELECT *"}):
        with pytest.raises(ValueError):
            _validate_arguments(arguments, definition.input_schema)
    first, second = await definition.executor({}), await definition.executor({})
    assert tool_result_hash(first) == tool_result_hash(second)
    first["batches"][0]["trial_evidence"][0]["resolved_parameters"]["epochs"] = 999
    assert second["batches"][0]["trial_evidence"][0]["resolved_parameters"]["epochs"] == 100
    changed = _history()
    changed["batches"][0]["summary_revision_id"] = _id(99)
    other = history_tool_definition(HistoryContext.model_validate(changed))
    assert bound_toolset_hash((definition,)) != bound_toolset_hash((other,))


@pytest.mark.asyncio
async def test_history_is_only_injected_by_tool_and_changes_request_hash() -> None:
    definition = history_tool_definition(HistoryContext.model_validate(_history()))
    common = dict(
        provider_name="scripted",
        model="fixture",
        goal="compare history",
        snapshot=RepositorySourceDescriptor(
            UUID(int=9), "a" * 64, UUID(int=90), UUID(int=1), "b" * 40, "main", False, True
        ),
        prior_human_inputs=(),
    )
    compiler = ContextCompiler()
    initial = compiler.compile(**common, definitions=(definition,), prior_tool_results=())
    ordinary = compiler.compile(**common, definitions=(), prior_tool_results=())
    assert "history_context_policy" in json.loads(initial.request.messages[0]["content"])
    system = json.loads(initial.request.messages[0]["content"])
    assert "evidence_trust_policy" in system
    assert "validation_rules" in " ".join(system["history_context_policy"])
    history_policy = " ".join(system["history_context_policy"])
    assert "summary" in history_policy and "question" in history_policy
    assert "source_batch_id" in history_policy and "batch_id" in history_policy
    assert "combined included" in history_policy and "unique Trial IDs" in history_policy
    assert "independent replications" in history_policy
    assert "history_context_policy" not in json.loads(ordinary.request.messages[0]["content"])
    assert "history_context" not in json.loads(initial.request.messages[1]["content"])
    result = await definition.executor({})
    second = compiler.compile(
        **common,
        definitions=(definition,),
        prior_tool_results=(PriorToolResult(definition.name, result),),
    )
    replay = compiler.compile(
        **common,
        definitions=(definition,),
        prior_tool_results=(PriorToolResult(definition.name, result),),
    )
    assert second.canonical_request_hash == replay.canonical_request_hash
    assert second.canonical_request_hash != initial.canonical_request_hash
    assert second.request.bound_toolset_hash == initial.request.bound_toolset_hash
    assert second.output_schema_hash == initial.output_schema_hash
    user = json.loads(second.request.messages[1]["content"])
    assert user["prior_tool_results"] == [{"tool_name": definition.name, "result": result}]


def test_nested_design_fields_cannot_smuggle_ambient_content() -> None:
    value = _history()
    value["batches"][0]["design"]["dataset_reference"]["raw_logs"] = "SECRET"
    with pytest.raises(ValueError):
        HistoryContext.model_validate(value)


@pytest.mark.parametrize("mutation", ["trial", "report", "extra"])
def test_history_warnings_have_exact_lineage_and_fields(mutation):
    value = _history()
    warning = {
        "trial_id": _id(10),
        "trial_ordinal": 1,
        "attempt_id": _id(11),
        "validation_report_id": _id(12),
        "code": "DATASET_BEST_EFFORT_REPRODUCIBILITY",
        "subject": "dataset",
        "details": {},
    }
    value["batches"][0]["warnings"] = [warning]
    assert HistoryContext.model_validate(value).batches[0].warnings[0].trial_id == UUID(int=10)
    if mutation == "trial":
        warning["trial_id"] = _id(99)
    elif mutation == "report":
        warning["validation_report_id"] = _id(99)
    else:
        warning["raw_logs"] = "SECRET"
    with pytest.raises(ValueError):
        HistoryContext.model_validate(value)
