from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from labpilot.db.uow import UnitOfWork, UnitOfWorkError, UnitOfWorkOutcome, UnitOfWorkState


class FakeTransaction:
    def __init__(
        self,
        *,
        fail_commit: bool = False,
        fail_rollback: bool = False,
    ) -> None:
        self.fail_commit = fail_commit
        self.fail_rollback = fail_rollback
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("commit failed")

    async def rollback(self) -> None:
        self.rollbacks += 1
        if self.fail_rollback:
            raise RuntimeError("rollback failed")


class FakeSession:
    def __init__(
        self,
        transaction: FakeTransaction,
        *,
        fail_begin: bool = False,
        fail_close: bool = False,
    ) -> None:
        self.transaction = transaction
        self.fail_begin = fail_begin
        self.fail_close = fail_close
        self.closed = False

    async def begin(self) -> FakeTransaction:
        if self.fail_begin:
            raise RuntimeError("begin failed")
        return self.transaction

    async def close(self) -> None:
        self.closed = True
        if self.fail_close:
            raise RuntimeError("close failed")


@asynccontextmanager
async def fake_factory(session: FakeSession) -> AsyncIterator[FakeSession]:
    try:
        yield session
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_begin_failure_still_closes_session() -> None:
    session = FakeSession(FakeTransaction(), fail_begin=True)
    uow = UnitOfWork(lambda: fake_factory(session))
    with pytest.raises(RuntimeError, match="begin failed"):
        async with uow:
            pass
    assert session.closed is True
    assert uow.state is UnitOfWorkState.CLOSED
    assert uow.outcome is UnitOfWorkOutcome.FAILED
    with pytest.raises(UnitOfWorkError, match="active"):
        _ = uow.session


@pytest.mark.asyncio
async def test_begin_and_close_failures_are_both_preserved() -> None:
    session = FakeSession(
        FakeTransaction(),
        fail_begin=True,
        fail_close=True,
    )
    uow = UnitOfWork(lambda: fake_factory(session))
    with pytest.raises(BaseExceptionGroup) as captured:
        async with uow:
            pass
    messages = {str(error) for error in captured.value.exceptions}
    assert messages == {"begin failed", "close failed"}
    assert uow.state is UnitOfWorkState.CLOSED
    assert uow.outcome is UnitOfWorkOutcome.FAILED


@pytest.mark.asyncio
async def test_commit_can_only_happen_once() -> None:
    session = FakeSession(FakeTransaction())
    async with UnitOfWork(lambda: fake_factory(session)) as uow:
        await uow.commit()
        with pytest.raises(UnitOfWorkError, match="active"):
            await uow.commit()
    assert uow.state is UnitOfWorkState.CLOSED
    assert uow.outcome is UnitOfWorkOutcome.COMMITTED


@pytest.mark.asyncio
async def test_commit_then_session_access_is_rejected() -> None:
    session = FakeSession(FakeTransaction())
    async with UnitOfWork(lambda: fake_factory(session)) as uow:
        await uow.commit()
        with pytest.raises(UnitOfWorkError, match="active"):
            _ = uow.session


@pytest.mark.asyncio
async def test_rollback_then_commit_is_rejected() -> None:
    session = FakeSession(FakeTransaction())
    async with UnitOfWork(lambda: fake_factory(session)) as uow:
        await uow.rollback()
        with pytest.raises(UnitOfWorkError, match="active"):
            await uow.commit()
    assert uow.outcome is UnitOfWorkOutcome.ROLLED_BACK


@pytest.mark.asyncio
async def test_uncommitted_context_rolls_back_and_closes() -> None:
    transaction = FakeTransaction()
    session = FakeSession(transaction)
    async with UnitOfWork(lambda: fake_factory(session)):
        pass
    assert transaction.rollbacks == 1
    assert session.closed is True


@pytest.mark.asyncio
async def test_exception_after_work_rolls_back() -> None:
    transaction = FakeTransaction()
    session = FakeSession(transaction)
    with pytest.raises(RuntimeError, match="boom"):
        async with UnitOfWork(lambda: fake_factory(session)):
            raise RuntimeError("boom")
    assert transaction.rollbacks == 1
    assert session.closed is True


@pytest.mark.asyncio
async def test_commit_failure_is_not_reported_as_committed() -> None:
    session = FakeSession(FakeTransaction(fail_commit=True))
    uow = UnitOfWork(lambda: fake_factory(session))
    with pytest.raises(RuntimeError, match="commit failed"):
        async with uow:
            await uow.commit()
    assert uow.state is UnitOfWorkState.CLOSED
    assert uow.outcome is UnitOfWorkOutcome.FAILED
    assert session.closed is True


@pytest.mark.asyncio
async def test_rollback_failure_still_closes_session() -> None:
    session = FakeSession(FakeTransaction(fail_rollback=True))
    uow = UnitOfWork(lambda: fake_factory(session))
    with pytest.raises(RuntimeError, match="rollback failed"):
        async with uow:
            pass
    assert uow.state is UnitOfWorkState.CLOSED
    assert uow.outcome is UnitOfWorkOutcome.FAILED
    assert session.closed is True


@pytest.mark.asyncio
async def test_business_and_cleanup_errors_are_both_preserved() -> None:
    session = FakeSession(FakeTransaction(fail_rollback=True))
    uow = UnitOfWork(lambda: fake_factory(session))
    with pytest.raises(BaseExceptionGroup) as captured:
        async with uow:
            raise ValueError("business failed")
    messages = {str(error) for error in captured.value.exceptions}
    assert messages == {"business failed", "rollback failed"}
    assert session.closed is True
