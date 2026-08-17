from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from labpilot.db.session import Database
from tests.integration.db_helpers import upgrade_database_to_head
from tests.integration.experiments.test_proposal_service import (
    _agent_revision,
    _context,
    _payload,
    _proposal_system,
)


@pytest.mark.integration
async def test_migration_0009_registers_execution_preparation_schema(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        async with database.session() as session:
            tables = set(
                (
                    await session.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'public'"
                        )
                    )
                ).scalars()
            )
            index_definition = await session.scalar(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' "
                    "AND indexname = 'uq_attempts_active_trial'"
                )
            )
            revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
    finally:
        await database.dispose()

    assert {"batches", "trials", "attempts"} <= tables
    assert revision == "0010_attempt_physical_execution"
    assert "UNIQUE INDEX" in index_definition
    assert "(trial_id)" in index_definition
    assert "terminal_at IS NULL" in index_definition


@pytest.mark.integration
async def test_database_rejects_second_active_attempt_for_trial(
    isolated_database_url: str,
    tmp_path: Path,
) -> None:
    async with _proposal_system(isolated_database_url, tmp_path) as system:
        proposal = await _agent_revision(system, payload=_payload(system))
        verification = await system.service.verify_revision(
            context=_context(),
            proposal_revision_id=proposal.proposal_revision_id,
        )
        version = await system.service.approve_revision(
            context=_context(),
            proposal_revision_id=proposal.proposal_revision_id,
            verification_id=verification.proposal_verification_id,
        )
        batch_id = uuid4()
        trial_id = uuid4()
        first_attempt_id = uuid4()
        second_attempt_id = uuid4()

        async with system.database.session() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT INTO batches "
                        "(batch_id, project_id, experiment_version_id, row_version, created_at) "
                        "VALUES (:batch_id, :project_id, :version_id, 1, clock_timestamp())"
                    ),
                    {
                        "batch_id": batch_id,
                        "project_id": UUID("10000000-0000-4000-8000-000000000020"),
                        "version_id": version.experiment_version_id,
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO trials "
                        "(trial_id, batch_id, trial_ordinal, trial_identity, "
                        "resolved_parameters_json, seed, created_at) "
                        "VALUES (:trial_id, :batch_id, 1, :identity, '{}'::jsonb, 7, "
                        "clock_timestamp())"
                    ),
                    {"trial_id": trial_id, "batch_id": batch_id, "identity": "a" * 64},
                )
                await session.execute(
                    text(
                        "INSERT INTO attempts "
                        "(attempt_id, trial_id, attempt_number, status, "
                        "supervisor_execution_identity, workspace_path, created_at) "
                        "VALUES (:attempt_id, :trial_id, 1, 'preparing', :x, :path, "
                        "clock_timestamp())"
                    ),
                    {
                        "attempt_id": first_attempt_id,
                        "trial_id": trial_id,
                        "x": f"labpilot-v1-{'b' * 64}-{first_attempt_id.hex}",
                        "path": f"/tmp/{first_attempt_id.hex}",
                    },
                )

        with pytest.raises(IntegrityError, match="uq_attempts_active_trial"):
            async with system.database.session() as session:
                async with session.begin():
                    await session.execute(
                        text(
                            "INSERT INTO attempts "
                            "(attempt_id, trial_id, attempt_number, status, "
                            "supervisor_execution_identity, workspace_path, created_at) "
                            "VALUES (:attempt_id, :trial_id, 2, 'preparing', :x, :path, "
                            "clock_timestamp())"
                        ),
                        {
                            "attempt_id": second_attempt_id,
                            "trial_id": trial_id,
                            "x": f"labpilot-v1-{'b' * 64}-{second_attempt_id.hex}",
                            "path": f"/tmp/{second_attempt_id.hex}",
                        },
                    )
