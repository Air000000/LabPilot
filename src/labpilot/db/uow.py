from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from enum import StrEnum
from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction


class UnitOfWorkError(RuntimeError):
    pass


class UnitOfWorkState(StrEnum):
    NEW = "new"
    ACTIVE = "active"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    CLOSED = "closed"


class UnitOfWorkOutcome(StrEnum):
    NONE = "none"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


SessionContextFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class UnitOfWork:
    def __init__(self, session_context_factory: SessionContextFactory) -> None:
        self._session_context_factory = session_context_factory
        self._session_context: AbstractAsyncContextManager[AsyncSession] | None = None
        self._session: AsyncSession | None = None
        self._transaction: AsyncSessionTransaction | None = None
        self.state = UnitOfWorkState.NEW
        self.outcome = UnitOfWorkOutcome.NONE

    @property
    def session(self) -> AsyncSession:
        if self.state is not UnitOfWorkState.ACTIVE or self._session is None:
            raise UnitOfWorkError("UnitOfWork session is only available while active")
        return self._session

    async def __aenter__(self) -> "UnitOfWork":
        if self.state is not UnitOfWorkState.NEW:
            raise UnitOfWorkError("UnitOfWork cannot be entered more than once")
        self._session_context = self._session_context_factory()
        try:
            self._session = await self._session_context.__aenter__()
        except BaseException:
            self._clear_after_failed_enter()
            raise

        try:
            self._transaction = await self._session.begin()
        except BaseException as begin_error:
            cleanup_errors: list[BaseException] = []
            try:
                await self._session_context.__aexit__(
                    type(begin_error),
                    begin_error,
                    begin_error.__traceback__,
                )
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
            self._clear_after_failed_enter()
            if cleanup_errors:
                raise BaseExceptionGroup(
                    "UnitOfWork begin and cleanup failed",
                    [begin_error, *cleanup_errors],
                ) from None
            raise

        self.state = UnitOfWorkState.ACTIVE
        return self

    def _clear_after_failed_enter(self) -> None:
        self._session_context = None
        self._session = None
        self._transaction = None
        self.state = UnitOfWorkState.CLOSED
        self.outcome = UnitOfWorkOutcome.FAILED

    async def commit(self) -> None:
        self._require_active()
        assert self._transaction is not None
        try:
            await self._transaction.commit()
        except BaseException:
            self.state = UnitOfWorkState.FAILED
            self.outcome = UnitOfWorkOutcome.FAILED
            raise
        self.state = UnitOfWorkState.COMMITTED
        self.outcome = UnitOfWorkOutcome.COMMITTED

    async def rollback(self) -> None:
        self._require_active()
        assert self._transaction is not None
        try:
            await self._transaction.rollback()
        except BaseException:
            self.state = UnitOfWorkState.FAILED
            self.outcome = UnitOfWorkOutcome.FAILED
            raise
        self.state = UnitOfWorkState.ROLLED_BACK
        self.outcome = UnitOfWorkOutcome.ROLLED_BACK

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        cleanup_errors: list[BaseException] = []
        try:
            if self.state in {UnitOfWorkState.ACTIVE, UnitOfWorkState.FAILED}:
                assert self._transaction is not None
                try:
                    await self._transaction.rollback()
                    if self.state is UnitOfWorkState.ACTIVE:
                        self.outcome = UnitOfWorkOutcome.ROLLED_BACK
                except BaseException as cleanup_error:
                    self.outcome = UnitOfWorkOutcome.FAILED
                    cleanup_errors.append(cleanup_error)
        finally:
            if self._session_context is not None:
                try:
                    await self._session_context.__aexit__(None, None, None)
                except BaseException as cleanup_error:
                    cleanup_errors.append(cleanup_error)
            self._session_context = None
            self._session = None
            self._transaction = None
            self.state = UnitOfWorkState.CLOSED

        if cleanup_errors:
            if exc is not None:
                raise BaseExceptionGroup(
                    "UnitOfWork body and cleanup failed",
                    [exc, *cleanup_errors],
                )
            if len(cleanup_errors) == 1:
                raise cleanup_errors[0]
            raise BaseExceptionGroup("UnitOfWork cleanup failed", cleanup_errors)
        return False

    def _require_active(self) -> None:
        if self.state is not UnitOfWorkState.ACTIVE:
            raise UnitOfWorkError(
                f"UnitOfWork must be active; current state is {self.state.value}"
            )
