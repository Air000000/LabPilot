from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from labpilot.coordinator.execution_adapters import VersionExecutionSemanticsAdapter
from labpilot.execution.batch_service import BatchApplicationService, CreateBatch
from labpilot.execution.models import AttemptRow, TrialRow
from tests.integration.db_helpers import upgrade_database_to_head
from tests.integration.test_batch_commands import _approved_version, _context

RESULT_TABLES = {
    "artifacts",
    "metric_observations",
    "validation_reports",
    "trial_result_selections",
    "batch_summary_revisions",
}


async def _seed_attempts(
    isolated_database_url: str,
    tmp_path,
) -> tuple[str, str, str, str, str]:
    async with _approved_version(isolated_database_url, tmp_path) as (system, version):
        commands = BatchApplicationService(
            system.database.session,
            version_semantics=VersionExecutionSemanticsAdapter(system.database.session),
        )
        created = await commands.create_batch(
            context=_context(),
            idempotency_key="result-plane-schema",
            command=CreateBatch(version.experiment_version_id),
        )
        batch_id = created.envelope.primary_result_id
        async with system.database.session() as session:
            trials = tuple(
                await session.scalars(
                    select(TrialRow)
                    .where(TrialRow.batch_id == batch_id)
                    .order_by(TrialRow.trial_ordinal)
                )
            )
            first_trial, second_trial = trials[:2]
            first_attempt_id, second_attempt_id = uuid4(), uuid4()
            for trial, attempt_id in (
                (first_trial, first_attempt_id),
                (second_trial, second_attempt_id),
            ):
                session.add(
                    AttemptRow(
                        attempt_id=attempt_id,
                        trial_id=trial.trial_id,
                        attempt_number=1,
                        status="preparing",
                        supervisor_execution_identity=f"result-plane-{attempt_id}",
                        workspace_path="/tmp/result-plane",
                    )
                )
            await session.commit()
        return (
            batch_id,
            str(first_trial.trial_id),
            str(first_attempt_id),
            str(second_trial.trial_id),
            str(second_attempt_id),
        )


@pytest.mark.integration
async def test_result_plane_schema_enforces_authoritative_constraints(
    isolated_database_url: str,
    tmp_path,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    engine = create_engine(isolated_database_url.replace("+asyncpg", "+psycopg"))
    try:
        with engine.connect() as connection:
            tables = set(
                connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' AND tablename = ANY(:tables)"
                    ),
                    {"tables": list(RESULT_TABLES)},
                ).scalars()
            )
        assert tables == RESULT_TABLES

        batch_id, trial_id, attempt_id, second_trial_id, second_attempt_id = await _seed_attempts(
            isolated_database_url, tmp_path
        )
        artifact_id, report_id = str(uuid4()), str(uuid4())
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO artifacts(artifact_id, attempt_id, artifact_name, relative_path, "
                    "content_sha256, byte_size, media_type) VALUES "
                    "(:id, :attempt, 'metrics', 'metrics.json', :digest, 3, 'application/json')"
                ),
                {"id": artifact_id, "attempt": attempt_id, "digest": "a" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO metric_observations("
                    "metric_observation_id, artifact_id, metric_name, "
                    "split, numeric_value, extractor_version) VALUES "
                    "(:id, :artifact, 'accuracy', NULL, :value, 'v1')"
                ),
                {"id": str(uuid4()), "artifact": artifact_id, "value": Decimal("0.125")},
            )
            assert connection.scalar(
                text("SELECT numeric_value FROM metric_observations WHERE artifact_id = :artifact"),
                {"artifact": artifact_id},
            ) == Decimal("0.125")
            connection.execute(
                text(
                    "INSERT INTO validation_reports(validation_report_id, attempt_id, "
                    "validation_protocol_hash, extraction_profile_version, validator_version, "
                    "input_snapshot_json, verdict, reason_json, warning_json) VALUES "
                    "(:id, :attempt, :protocol, 'extract-v1', 'validator-v1', '{}'::jsonb, "
                    "'valid', '{}'::jsonb, '[]'::jsonb)"
                ),
                {"id": report_id, "attempt": attempt_id, "protocol": "b" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO trial_result_selections("
                    "trial_id, attempt_id, validation_report_id) "
                    "VALUES (:trial, :attempt, :report)"
                ),
                {"trial": trial_id, "attempt": attempt_id, "report": report_id},
            )
            connection.execute(
                text(
                    "INSERT INTO batch_summary_revisions(batch_summary_revision_id, batch_id, "
                    "revision_number, summary_protocol_version, included_result_refs_json, "
                    "excluded_reasons_json, warning_json, aggregates_json) VALUES "
                    "(:id, :batch, 1, 'summary-v1', '[]'::jsonb, '[]'::jsonb, "
                    "'[]'::jsonb, '{}'::jsonb)"
                ),
                {"id": str(uuid4()), "batch": batch_id},
            )

            statements = (
                (
                    "INSERT INTO artifacts(artifact_id, attempt_id, artifact_name, relative_path, "
                    "content_sha256, byte_size, media_type) VALUES "
                    "(:id, :attempt, 'metrics', 'other.json', :digest, 3, 'application/json')",
                    {"id": str(uuid4()), "attempt": attempt_id, "digest": "c" * 64},
                ),
                (
                    "INSERT INTO artifacts(artifact_id, attempt_id, artifact_name, relative_path, "
                    "content_sha256, byte_size, media_type) VALUES "
                    "(:id, :attempt, 'other', 'metrics.json', :digest, 3, 'application/json')",
                    {"id": str(uuid4()), "attempt": attempt_id, "digest": "d" * 64},
                ),
                (
                    "INSERT INTO artifacts(artifact_id, attempt_id, artifact_name, relative_path, "
                    "content_sha256, byte_size, media_type) VALUES "
                    "(:id, :attempt, 'negative', 'negative.json', :digest, -1, 'application/json')",
                    {"id": str(uuid4()), "attempt": attempt_id, "digest": "e" * 64},
                ),
                (
                    "INSERT INTO metric_observations("
                    "metric_observation_id, artifact_id, metric_name, "
                    "split, numeric_value, extractor_version) VALUES "
                    "(:id, :artifact, 'accuracy', 'test', 1.0, 'v1')",
                    {"id": str(uuid4()), "artifact": artifact_id},
                ),
                (
                    "INSERT INTO validation_reports(validation_report_id, attempt_id, "
                    "validation_protocol_hash, extraction_profile_version, validator_version, "
                    "input_snapshot_json, verdict, reason_json, warning_json) VALUES "
                    "(:id, :attempt, :protocol, 'extract-v1', 'validator-v1', '{}'::jsonb, "
                    "'valid', '{}'::jsonb, '[]'::jsonb)",
                    {"id": str(uuid4()), "attempt": attempt_id, "protocol": "b" * 64},
                ),
                (
                    "INSERT INTO validation_reports(validation_report_id, attempt_id, "
                    "validation_protocol_hash, extraction_profile_version, validator_version, "
                    "input_snapshot_json, verdict, reason_json, warning_json) VALUES "
                    "(:id, :attempt, :protocol, 'extract-v2', 'validator-v1', '{}'::jsonb, "
                    "'other', '{}'::jsonb, '[]'::jsonb)",
                    {"id": str(uuid4()), "attempt": attempt_id, "protocol": "f" * 64},
                ),
                (
                    "INSERT INTO trial_result_selections("
                    "trial_id, attempt_id, validation_report_id) "
                    "VALUES (:trial, :attempt, :report)",
                    {"trial": trial_id, "attempt": attempt_id, "report": report_id},
                ),
                (
                    "INSERT INTO trial_result_selections("
                    "trial_id, attempt_id, validation_report_id) "
                    "VALUES (:trial, :attempt, :report)",
                    {
                        "trial": second_trial_id,
                        "attempt": second_attempt_id,
                        "report": report_id,
                    },
                ),
                (
                    "INSERT INTO batch_summary_revisions(batch_summary_revision_id, batch_id, "
                    "revision_number, summary_protocol_version, included_result_refs_json, "
                    "excluded_reasons_json, warning_json, aggregates_json) VALUES "
                    "(:id, :batch, 1, 'summary-v1', '[]'::jsonb, '[]'::jsonb, "
                    "'[]'::jsonb, '{}'::jsonb)",
                    {"id": str(uuid4()), "batch": batch_id},
                ),
                (
                    "INSERT INTO batch_summary_revisions(batch_summary_revision_id, batch_id, "
                    "revision_number, summary_protocol_version, included_result_refs_json, "
                    "excluded_reasons_json, warning_json, aggregates_json) VALUES "
                    "(:id, :batch, 0, 'summary-v1', '[]'::jsonb, '[]'::jsonb, "
                    "'[]'::jsonb, '{}'::jsonb)",
                    {"id": str(uuid4()), "batch": batch_id},
                ),
            )
            for sql, values in statements:
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(text(sql), values)
    finally:
        engine.dispose()
