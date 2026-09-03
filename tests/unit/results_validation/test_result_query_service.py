from __future__ import annotations

import importlib
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from labpilot.execution.models import AttemptRow, BatchRow, TrialRow
from labpilot.results_validation.models import (
    ArtifactRow,
    BatchSummaryRevisionRow,
    MetricObservationRow,
    TrialResultSelectionRow,
    ValidationReportRow,
)
from labpilot.results_validation.types import (
    ArtifactIdentity,
    MissingArtifact,
    ResultProcessingDeferred,
)
from labpilot.settings import Settings


class _ScalarRows:
    def __init__(self, rows: tuple[object, ...]) -> None:
        self._rows = rows

    def all(self) -> tuple[object, ...]:
        return self._rows


class _Session:
    def __init__(self, rows: dict[type[object], tuple[object, ...]]) -> None:
        self._rows = rows

    async def get(self, model: type[object], identity: UUID) -> object | None:
        return next(
            (
                row
                for row in self._rows.get(model, ())
                if getattr(row, _id_field(model)) == identity
            ),
            None,
        )

    async def scalars(self, statement: object) -> _ScalarRows:
        model = statement.column_descriptions[0]["entity"]
        return _ScalarRows(self._rows.get(model, ()))

    async def scalar(self, statement: object) -> object | None:
        model = statement.column_descriptions[0]["entity"]
        return next(iter(self._rows.get(model, ())), None)


def _id_field(model: type[object]) -> str:
    return {
        AttemptRow: "attempt_id",
        BatchRow: "batch_id",
        TrialRow: "trial_id",
    }[model]


def _session_context(rows: dict[type[object], tuple[object, ...]]):
    @asynccontextmanager
    async def context():
        yield _Session(rows)

    return context


def _query_module():
    return importlib.import_module("labpilot.results_validation.query_service")


def _service(rows: dict[type[object], tuple[object, ...]], monkeypatch: pytest.MonkeyPatch):
    module = _query_module()
    monkeypatch.setattr(module, "read_artifact_identity", _available_reader)
    return module.ResultQueryService(_session_context(rows))


def _available_reader(
    _: object, relative_path: str, media_type: str
) -> ArtifactIdentity:
    return ArtifactIdentity(
        relative_path=relative_path,
        content_sha256="a" * 64,
        byte_size=7,
        media_type=media_type,
    )


def _attempt_rows() -> tuple[dict[type[object], tuple[object, ...]], UUID, UUID, UUID]:
    attempt_id, trial_id, artifact_id = uuid4(), uuid4(), uuid4()
    report_current_id, report_historical_id = uuid4(), uuid4()
    now = datetime(2026, 9, 3, tzinfo=UTC)
    rows: dict[type[object], tuple[object, ...]] = {
        AttemptRow: (
            SimpleNamespace(
                attempt_id=attempt_id,
                trial_id=trial_id,
                workspace_path="/private/workspace",
            ),
        ),
        ArtifactRow: (
            SimpleNamespace(
                artifact_id=artifact_id,
                attempt_id=attempt_id,
                artifact_name="result",
                relative_path="result.json",
                content_sha256="a" * 64,
                byte_size=7,
                media_type="application/json",
            ),
        ),
        MetricObservationRow: (
            SimpleNamespace(
                metric_observation_id=uuid4(),
                artifact_id=artifact_id,
                metric_name="score",
                split="test",
                numeric_value=Decimal("0.8200"),
                extractor_version="result-json-v1",
            ),
            SimpleNamespace(
                metric_observation_id=uuid4(),
                artifact_id=artifact_id,
                metric_name="loss",
                split=None,
                numeric_value=Decimal("0.1000"),
                extractor_version="result-json-v1",
            ),
        ),
        ValidationReportRow: (
            SimpleNamespace(
                validation_report_id=report_current_id,
                attempt_id=attempt_id,
                validation_protocol_hash="b" * 64,
                extraction_profile_version="result-json-v1",
                validator_version="slice3-validator-v1",
                verdict="valid",
                reason_json={"reasons": []},
                warning_json={
                    "warnings": [
                        {"code": "DATASET_BEST_EFFORT_REPRODUCIBILITY"}
                    ]
                },
                created_at=now,
            ),
            SimpleNamespace(
                validation_report_id=report_historical_id,
                attempt_id=attempt_id,
                validation_protocol_hash="b" * 64,
                extraction_profile_version="result-json-v0",
                validator_version="slice3-validator-v0",
                verdict="invalid",
                reason_json={"reasons": [{"code": "METRIC_BELOW_THRESHOLD"}]},
                warning_json={"warnings": []},
                created_at=now,
            ),
        ),
    }
    return rows, attempt_id, report_current_id, report_historical_id


@pytest.mark.asyncio
async def test_attempt_query_shapes_sorted_historical_facts_without_workspace_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, attempt_id, current_id, historical_id = _attempt_rows()

    result = await _service(rows, monkeypatch).get_attempt_results(attempt_id)

    assert result.attempt_id == attempt_id
    assert [artifact.artifact_name for artifact in result.artifacts] == ["result"]
    assert result.artifacts[0].availability == "available"
    assert not hasattr(result.artifacts[0], "workspace_path")
    assert [metric.metric_name for metric in result.metrics] == ["loss", "score"]
    assert [metric.numeric_value for metric in result.metrics] == ["0.1", "0.82"]
    assert [report.validation_report_id for report in result.validation_reports] == [
        current_id,
        historical_id,
    ]
    assert result.validation_reports[0].warnings == [
        {"code": "DATASET_BEST_EFFORT_REPRODUCIBILITY"}
    ]
    assert result.validation_reports[1].reasons == [
        {"code": "METRIC_BELOW_THRESHOLD"}
    ]

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reader", "expected"),
    (
        (lambda *_: MissingArtifact("result.json"), "missing"),
        (
            lambda *_: ArtifactIdentity("result.json", "c" * 64, 7, "application/json"),
            "corrupted",
        ),
    ),
)
async def test_attempt_query_derives_missing_and_corrupted_availability(
    monkeypatch: pytest.MonkeyPatch,
    reader: object,
    expected: str,
) -> None:
    rows, attempt_id, _, _ = _attempt_rows()
    module = _query_module()
    monkeypatch.setattr(module, "read_artifact_identity", reader)

    result = await module.ResultQueryService(_session_context(rows)).get_attempt_results(
        attempt_id
    )

    assert result.artifacts[0].availability == expected


@pytest.mark.asyncio
async def test_attempt_query_propagates_transient_safe_reader_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, attempt_id, _, _ = _attempt_rows()
    module = _query_module()

    def deferred_reader(*_: object) -> ArtifactIdentity:
        raise ResultProcessingDeferred("EAGAIN")

    monkeypatch.setattr(module, "read_artifact_identity", deferred_reader)
    service = module.ResultQueryService(_session_context(rows))

    with pytest.raises(ResultProcessingDeferred):
        await service.get_attempt_results(attempt_id)


@pytest.mark.asyncio
async def test_trial_query_returns_only_persisted_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, attempt_id, current_id, _ = _attempt_rows()
    trial_id = rows[AttemptRow][0].trial_id
    rows[TrialRow] = (SimpleNamespace(trial_id=trial_id),)
    rows[TrialResultSelectionRow] = (
        SimpleNamespace(
            trial_id=trial_id,
            attempt_id=attempt_id,
            validation_report_id=current_id,
        ),
    )

    result = await _service(rows, monkeypatch).get_trial_result(trial_id)

    assert result.trial_id == trial_id
    assert result.selection.attempt_id == attempt_id
    assert result.selection.validation_report_id == current_id


@pytest.mark.asyncio
async def test_batch_query_returns_highest_persisted_revision_without_reaggregation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_id = uuid4()
    latest_id, prior_id = uuid4(), uuid4()
    rows: dict[type[object], tuple[object, ...]] = {
        BatchRow: (SimpleNamespace(batch_id=batch_id),),
        BatchSummaryRevisionRow: (
            SimpleNamespace(
                batch_summary_revision_id=latest_id,
                batch_id=batch_id,
                revision_number=2,
                presentation_status="complete",
                summary_protocol_version="slice3-summary-v1",
                included_result_refs_json=[{"trial_id": "included"}],
                excluded_reasons_json=[],
                warning_json={"warnings": []},
                aggregates_json={"score": {"count": 1, "mean": "0.82"}},
            ),
            SimpleNamespace(
                batch_summary_revision_id=prior_id,
                batch_id=batch_id,
                revision_number=1,
                presentation_status="complete",
                summary_protocol_version="slice3-summary-v1",
                included_result_refs_json=[],
                excluded_reasons_json=[],
                warning_json={"warnings": []},
                aggregates_json={},
            ),
        ),
    }

    result = await _service(rows, monkeypatch).get_batch_summary(batch_id)

    assert result.batch_id == batch_id
    assert result.latest_summary.revision_id == latest_id
    assert result.latest_summary.revision_number == 2
    assert result.latest_summary.aggregates == {"score": {"count": 1, "mean": "0.82"}}


def test_main_composes_result_queries_with_its_database_session_factory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import labpilot.settings as settings_module

    settings = Settings(
        environment="test",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:55432/labpilot_main",
        installation_identity="task9-main",
        local_actor_id="task9-main",
        repository_allowed_roots=(),
        repository_content_store_root=tmp_path / "content",
    )
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)
    monkeypatch.delitem(sys.modules, "labpilot.main", raising=False)
    try:
        main = importlib.import_module("labpilot.main")
        assert hasattr(main.services, "result_queries")
        query_module = importlib.import_module("labpilot.results_validation.query_service")
        assert isinstance(main.services.result_queries, query_module.ResultQueryService)
        assert main.services.result_queries._session_context_factory == main.database.session
    finally:
        sys.modules.pop("labpilot.main", None)
