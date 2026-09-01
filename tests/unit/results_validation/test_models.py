import importlib

from sqlalchemy import CheckConstraint, Numeric, UniqueConstraint


def _models():
    return importlib.import_module("labpilot.results_validation.models")


def test_result_plane_models_define_exact_authoritative_tables() -> None:
    models = _models()

    assert {
        "ArtifactRow": "artifacts",
        "MetricObservationRow": "metric_observations",
        "ValidationReportRow": "validation_reports",
        "TrialResultSelectionRow": "trial_result_selections",
        "BatchSummaryRevisionRow": "batch_summary_revisions",
    } == {
        name: getattr(models, name).__tablename__
        for name in (
            "ArtifactRow",
            "MetricObservationRow",
            "ValidationReportRow",
            "TrialResultSelectionRow",
            "BatchSummaryRevisionRow",
        )
    }


def test_artifact_contract_has_only_content_digest_and_natural_uniques() -> None:
    table = _models().ArtifactRow.__table__

    assert set(table.c.keys()) == {
        "artifact_id",
        "attempt_id",
        "artifact_name",
        "relative_path",
        "content_sha256",
        "byte_size",
        "media_type",
        "created_at",
    }
    assert {
        ("attempt_id", "artifact_name"),
        ("attempt_id", "relative_path"),
    } <= {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert any(
        isinstance(constraint, CheckConstraint) and "byte_size >= 0" in str(constraint.sqltext)
        for constraint in table.constraints
    )
    assert "artifact_hash" not in table.c and "updated_at" not in table.c


def test_metric_observation_is_numeric_and_provenanced_only_by_artifact() -> None:
    table = _models().MetricObservationRow.__table__

    assert set(table.c.keys()) == {
        "metric_observation_id",
        "artifact_id",
        "metric_name",
        "split",
        "numeric_value",
        "extractor_version",
        "created_at",
    }
    assert isinstance(table.c.numeric_value.type, Numeric)
    assert table.c.split.nullable is True
    assert "attempt_id" not in table.c
    assert ("artifact_id", "metric_name", "extractor_version") in {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_validation_report_verdict_and_semantic_key_are_constrained() -> None:
    table = _models().ValidationReportRow.__table__

    assert set(table.c.keys()) == {
        "validation_report_id",
        "attempt_id",
        "validation_protocol_hash",
        "extraction_profile_version",
        "validator_version",
        "input_snapshot_json",
        "verdict",
        "reason_json",
        "warning_json",
        "created_at",
    }
    assert (
        "attempt_id",
        "validation_protocol_hash",
        "extraction_profile_version",
        "validator_version",
    ) in {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert any(
        isinstance(constraint, CheckConstraint) and "valid" in str(constraint.sqltext)
        for constraint in table.constraints
    )


def test_selection_and_summary_contracts_are_append_only_and_natural() -> None:
    models = _models()
    selection = models.TrialResultSelectionRow.__table__
    summary = models.BatchSummaryRevisionRow.__table__

    assert list(selection.primary_key.columns.keys()) == ["trial_id"]
    assert ("validation_report_id",) in {
        tuple(constraint.columns.keys())
        for constraint in selection.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert set(summary.c.keys()) == {
        "batch_summary_revision_id",
        "batch_id",
        "revision_number",
        "summary_protocol_version",
        "included_result_refs_json",
        "excluded_reasons_json",
        "warning_json",
        "aggregates_json",
        "created_at",
    }
    assert ("batch_id", "revision_number") in {
        tuple(constraint.columns.keys())
        for constraint in summary.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert any(
        isinstance(constraint, CheckConstraint) and "revision_number > 0" in str(constraint.sqltext)
        for constraint in summary.constraints
    )
    for table in (selection, summary):
        assert "updated_at" not in table.c and "row_version" not in table.c


def test_validation_verdict_values_are_authoritative() -> None:
    types = importlib.import_module("labpilot.results_validation.types")

    assert {member.value for member in types.ValidationVerdict} == {
        "valid",
        "invalid",
        "inconclusive",
        "error",
    }
