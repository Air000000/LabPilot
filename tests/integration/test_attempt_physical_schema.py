import pytest
from sqlalchemy import text

from labpilot.db.session import Database
from tests.integration.db_helpers import upgrade_database_to_head


@pytest.mark.integration
async def test_migration_0010_adds_exact_attempt_physical_columns(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        async with database.session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT column_name, data_type, character_maximum_length, "
                        "is_nullable FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'attempts' "
                        "ORDER BY ordinal_position"
                    )
                )
            ).all()
            revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
    finally:
        await database.dispose()

    physical = {
        name: (data_type, maximum_length, nullable)
        for name, data_type, maximum_length, nullable in rows
        if name
        in {
            "frozen_launch_spec_json",
            "execution_spec_hash",
            "launch_intent_committed_at",
            "observed_environment_json",
            "observed_environment_recorded_at",
            "physical_started_at",
            "physical_ended_at",
            "exit_code",
            "termination_signal",
            "supervisor_result",
            "terminal_kind",
            "unknown_since",
            "attention_required_at",
            "execution_issue_code",
            "supervisor_invocation_id",
            "physical_boot_id",
        }
    }
    assert revision == "0010_attempt_physical_execution"
    assert physical == {
        "frozen_launch_spec_json": ("jsonb", None, "YES"),
        "execution_spec_hash": ("character", 64, "YES"),
        "launch_intent_committed_at": ("timestamp with time zone", None, "YES"),
        "observed_environment_json": ("jsonb", None, "YES"),
        "observed_environment_recorded_at": ("timestamp with time zone", None, "YES"),
        "physical_started_at": ("timestamp with time zone", None, "YES"),
        "physical_ended_at": ("timestamp with time zone", None, "YES"),
        "exit_code": ("integer", None, "YES"),
        "termination_signal": ("integer", None, "YES"),
        "supervisor_result": ("character varying", 100, "YES"),
        "terminal_kind": ("character varying", 60, "YES"),
        "unknown_since": ("timestamp with time zone", None, "YES"),
        "attention_required_at": ("timestamp with time zone", None, "YES"),
        "execution_issue_code": ("character varying", 100, "YES"),
        "supervisor_invocation_id": ("character varying", 200, "YES"),
        "physical_boot_id": ("character varying", 200, "YES"),
    }


@pytest.mark.integration
async def test_migration_0010_installs_physical_consistency_constraints(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        async with database.session() as session:
            constraints = dict(
                (
                    await session.execute(
                        text(
                            "SELECT conname, pg_get_constraintdef(oid) "
                            "FROM pg_constraint "
                            "WHERE conrelid = 'attempts'::regclass "
                            "AND conname LIKE 'ck_attempts_physical_%' "
                            "ORDER BY conname"
                        )
                    )
                ).all()
            )
    finally:
        await database.dispose()

    assert set(constraints) == {
        "ck_attempts_physical_identity_conflict_nonterminal",
        "ck_attempts_physical_identity_pair",
        "ck_attempts_physical_observed_environment_pair",
        "ck_attempts_physical_terminal_causality",
        "ck_attempts_physical_terminal_kind",
    }
    assert "frozen_launch_spec_json IS NULL" in constraints[
        "ck_attempts_physical_identity_pair"
    ]
    assert "identity_conflict" in constraints[
        "ck_attempts_physical_identity_conflict_nonterminal"
    ]
    assert "observed_environment_recorded_at IS NULL" in constraints[
        "ck_attempts_physical_observed_environment_pair"
    ]
    assert "process_terminal_unclassified" in constraints[
        "ck_attempts_physical_terminal_causality"
    ]
    assert "status" in constraints["ck_attempts_physical_terminal_kind"]
