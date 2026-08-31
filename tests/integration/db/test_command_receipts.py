from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from tests.integration.db_helpers import upgrade_database_to_head

from labpilot.db.session import Database
from labpilot.db.uow import UnitOfWork


@pytest.mark.integration
@pytest.mark.asyncio
async def test_command_receipts_have_the_required_columns(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    try:
        async with database.session() as session:
            result = await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'command_receipts'"
                )
            )
            columns = set(result.scalars())
    finally:
        await database.dispose()

    assert {
        "command_receipt_id",
        "idempotency_scope_identity",
        "command_name",
        "command_version",
        "idempotency_key",
        "normalized_request_hash",
        "outcome_kind",
        "primary_result_type",
        "primary_result_id",
        "result_envelope_schema_version",
        "result_envelope_json",
        "result_envelope_hash",
        "created_at",
    } <= columns


@pytest.mark.integration
@pytest.mark.asyncio
async def test_command_receipt_idempotency_key_is_unique_within_command_scope(
    isolated_database_url: str,
) -> None:
    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    statement = text(
        """
        INSERT INTO command_receipts (
            command_receipt_id,
            idempotency_scope_identity,
            command_name,
            command_version,
            idempotency_key,
            normalized_request_hash,
            outcome_kind,
            primary_result_type,
            primary_result_id,
            result_envelope_schema_version,
            result_envelope_json,
            result_envelope_hash
        ) VALUES (
            :command_receipt_id,
            :idempotency_scope_identity,
            :command_name,
            :command_version,
            :idempotency_key,
            :normalized_request_hash,
            :outcome_kind,
            :primary_result_type,
            :primary_result_id,
            :result_envelope_schema_version,
            CAST(:result_envelope_json AS JSONB),
            :result_envelope_hash
        )
        """
    )
    values = {
        "idempotency_scope_identity": "a" * 64,
        "command_name": "CreateProject",
        "command_version": 1,
        "idempotency_key": "request-1",
        "normalized_request_hash": "b" * 64,
        "outcome_kind": "project_created",
        "primary_result_type": "project",
        "primary_result_id": "project-1",
        "result_envelope_schema_version": 1,
        "result_envelope_json": "{}",
        "result_envelope_hash": "c" * 64,
    }
    try:
        with pytest.raises(IntegrityError):
            async with UnitOfWork(database.session) as uow:
                await uow.session.execute(
                    statement,
                    values | {"command_receipt_id": uuid4()},
                )
                await uow.session.execute(
                    statement,
                    values | {"command_receipt_id": uuid4()},
                )
                await uow.commit()
    finally:
        await database.dispose()


class _TrackingSessionFactory:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.sessions: list[object] = []
        self.active_by_task: dict[object, object] = {}

    def __call__(self) -> object:
        import asyncio
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def context() -> object:
            task = asyncio.current_task()
            assert task is not None
            async with self.database.session() as session:
                self.sessions.append(session)
                self.active_by_task[task] = session
                try:
                    yield session
                finally:
                    self.active_by_task.pop(task)

        return context()

    def assert_current_task_has_no_session(self) -> None:
        import asyncio

        task = asyncio.current_task()
        assert task is not None
        assert task not in self.active_by_task


def _task2_identity(
    *,
    key: str = "task2-key",
    request_hash: str = "b" * 64,
) -> object:
    from labpilot.db.idempotency import CommandIdentity

    return CommandIdentity(
        scope_identity="a" * 64,
        command_name="Task2Probe",
        command_version=1,
        idempotency_key=key,
        normalized_request_hash=request_hash,
    )


def _task2_envelope(marker: str) -> object:
    from labpilot.db.idempotency import CommandResultEnvelope

    return CommandResultEnvelope(
        outcome_kind="probe_created",
        primary_result_type="probe",
        primary_result_id=marker,
        primary_result_identity=None,
        related_result_refs=(),
        committed_state="created",
    )


async def _create_task2_probe_table(database: Database) -> None:
    async with UnitOfWork(database.session) as uow:
        await uow.session.execute(
            text(
                "CREATE TABLE task2_command_probe "
                "(marker TEXT CONSTRAINT task2_command_probe_pkey PRIMARY KEY)"
            )
        )
        await uow.commit()


def _postgres_constraint_name(error: BaseException) -> str | None:
    pending: list[object] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        name = getattr(current, "constraint_name", None)
        if isinstance(name, str):
            return name
        for attribute in ("orig", "__cause__", "__context__"):
            nested = getattr(current, attribute, None)
            if nested is not None:
                pending.append(nested)
    return None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_same_key_rolls_back_loser_and_reloads_winner(
    isolated_database_url: str,
) -> None:
    import asyncio

    from labpilot.db.idempotency import execute_idempotent_command

    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    sessions = _TrackingSessionFactory(database)
    barrier = asyncio.Barrier(2)
    prepared_markers: list[str] = []

    async def prepare() -> str:
        sessions.assert_current_task_has_no_session()
        marker = f"prepared-{len(prepared_markers) + 1}"
        prepared_markers.append(marker)
        return marker

    async def operation(uow: UnitOfWork, prepared: str) -> object:
        await uow.session.execute(
            text("INSERT INTO task2_command_probe (marker) VALUES (:marker)"),
            {"marker": prepared},
        )
        await barrier.wait()
        return _task2_envelope(prepared)

    try:
        await _create_task2_probe_table(database)
        first_task = asyncio.create_task(
            execute_idempotent_command(
                session_context_factory=sessions,  # type: ignore[arg-type]
                identity=_task2_identity(),  # type: ignore[arg-type]
                prepare=prepare,
                operation=operation,  # type: ignore[arg-type]
            )
        )
        second_task = asyncio.create_task(
            execute_idempotent_command(
                session_context_factory=sessions,  # type: ignore[arg-type]
                identity=_task2_identity(),  # type: ignore[arg-type]
                prepare=prepare,
                operation=operation,  # type: ignore[arg-type]
            )
        )

        outcomes = await asyncio.gather(first_task, second_task)

        assert prepared_markers == ["prepared-1", "prepared-2"]
        assert sorted(outcome.replayed for outcome in outcomes) == [False, True]
        winner = next(outcome for outcome in outcomes if not outcome.replayed)
        loser = next(outcome for outcome in outcomes if outcome.replayed)
        assert loser.envelope == winner.envelope
        assert len(sessions.sessions) == 5
        assert len({id(session) for session in sessions.sessions}) == 5

        async with database.session() as session:
            probes = (
                await session.execute(
                    text("SELECT marker FROM task2_command_probe ORDER BY marker")
                )
            ).scalars().all()
            receipt_count = (
                await session.execute(text("SELECT count(*) FROM command_receipts"))
            ).scalar_one()
        assert probes == [winner.envelope.primary_result_id]
        assert receipt_count == 1
    finally:
        await database.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_receipt_hit_replays_when_external_resource_is_unavailable(
    isolated_database_url: str,
) -> None:
    from labpilot.db.idempotency import (
        IdempotencyKeyConflict,
        execute_idempotent_command,
    )

    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    sessions = _TrackingSessionFactory(database)
    available = True
    prepare_calls = 0
    operation_calls = 0

    async def prepare() -> str:
        nonlocal prepare_calls
        sessions.assert_current_task_has_no_session()
        prepare_calls += 1
        if not available:
            raise RuntimeError("external resource unavailable")
        return "stable-result"

    async def operation(uow: UnitOfWork, prepared: str) -> object:
        nonlocal operation_calls
        operation_calls += 1
        return _task2_envelope(prepared)

    try:
        first = await execute_idempotent_command(
            session_context_factory=sessions,  # type: ignore[arg-type]
            identity=_task2_identity(key="external-replay"),  # type: ignore[arg-type]
            prepare=prepare,
            operation=operation,  # type: ignore[arg-type]
        )
        available = False
        replay = await execute_idempotent_command(
            session_context_factory=sessions,  # type: ignore[arg-type]
            identity=_task2_identity(key="external-replay"),  # type: ignore[arg-type]
            prepare=prepare,
            operation=operation,  # type: ignore[arg-type]
        )

        assert first.replayed is False
        assert replay.replayed is True
        assert replay.envelope == first.envelope
        assert prepare_calls == 1
        assert operation_calls == 1

        with pytest.raises(IdempotencyKeyConflict):
            await execute_idempotent_command(
                session_context_factory=sessions,  # type: ignore[arg-type]
                identity=_task2_identity(
                    key="external-replay",
                    request_hash="c" * 64,
                ),  # type: ignore[arg-type]
                prepare=prepare,
                operation=operation,  # type: ignore[arg-type]
            )
        assert prepare_calls == 1
        assert operation_calls == 1
    finally:
        await database.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_non_receipt_integrity_error_is_reraised_without_winner_reload(
    isolated_database_url: str,
) -> None:
    from labpilot.db.idempotency import execute_idempotent_command

    upgrade_database_to_head(isolated_database_url)
    database = Database(isolated_database_url)
    sessions = _TrackingSessionFactory(database)

    async def prepare() -> str:
        sessions.assert_current_task_has_no_session()
        return "duplicate"

    async def operation(uow: UnitOfWork, prepared: str) -> object:
        await uow.session.execute(
            text("INSERT INTO task2_command_probe (marker) VALUES (:marker)"),
            {"marker": prepared},
        )
        return _task2_envelope(prepared)

    try:
        await _create_task2_probe_table(database)
        async with UnitOfWork(database.session) as uow:
            await uow.session.execute(
                text("INSERT INTO task2_command_probe (marker) VALUES ('duplicate')")
            )
            await uow.commit()

        with pytest.raises(IntegrityError) as captured:
            await execute_idempotent_command(
                session_context_factory=sessions,  # type: ignore[arg-type]
                identity=_task2_identity(key="other-integrity"),  # type: ignore[arg-type]
                prepare=prepare,
                operation=operation,  # type: ignore[arg-type]
            )

        assert _postgres_constraint_name(captured.value) == "task2_command_probe_pkey"
        assert len(sessions.sessions) == 2
        async with database.session() as session:
            receipt_count = (
                await session.execute(text("SELECT count(*) FROM command_receipts"))
            ).scalar_one()
        assert receipt_count == 0
    finally:
        await database.dispose()
