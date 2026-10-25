"""Engine, sessions, transactions and per-Account locks."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from typing import Any, TypeVar

from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from fraud_audit_assist.persistence.tables import Base

T = TypeVar("T")

# Deadlock and serialisation failure: Postgres aborted one transaction so the other could proceed. Replaying the whole
# transaction is safe because every write in it is idempotent (ADR-0015).
_TRANSIENT_SQLSTATES = frozenset({"40P01", "40001"})


def is_transient(error: BaseException) -> bool:
    if not isinstance(error, DBAPIError):
        return False
    sqlstate = getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)
    return sqlstate in _TRANSIENT_SQLSTATES


class Database:
    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = url
        self.is_sqlite = url.startswith("sqlite")
        engine_args: dict[str, Any] = {"echo": echo}
        if self.is_sqlite:
            engine_args["connect_args"] = {"timeout": 30}
        else:
            engine_args |= {"pool_pre_ping": True, "pool_size": 10, "max_overflow": 10}
        self.engine = create_async_engine(url, **engine_args)
        if self.is_sqlite:
            event.listen(self.engine.sync_engine, "connect", _sqlite_pragmas)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        # SQLite allows one writer at a time; serialising writes in-process avoids "database is locked" errors.
        self._write_lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """One write transaction: committed when the block exits, rolled back if it raises."""
        guard: AbstractAsyncContextManager[Any] = self._write_lock if self.is_sqlite else nullcontext()
        async with guard, self.sessions() as session, session.begin():
            yield session

    @asynccontextmanager
    async def read(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session

    async def run_transaction(self, work: Callable[[AsyncSession], Awaitable[T]], attempts: int = 4) -> T:
        """Runs `work` in a transaction, replaying the whole transaction on a deadlock or serialisation failure."""
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(is_transient),
            stop=stop_after_attempt(attempts),
            wait=wait_exponential_jitter(multiplier=0.05, max=1.0, jitter=0.1),
            reraise=True,
        ):
            with attempt:
                async with self.transaction() as session:
                    return await work(session)
        raise AssertionError("unreachable")  # pragma: no cover

    async def lock_accounts(self, session: AsyncSession, account_ids: Iterable[str]) -> None:
        """Serialises work on the same Accounts across transactions, released at commit.

        Locks are taken in sorted order so two batches touching the same Accounts cannot deadlock on them.
        """
        if self.is_sqlite:
            return
        for account_id in sorted(set(account_ids)):
            await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(account_id, 0))))

    async def ping(self) -> None:
        async with self.sessions() as session:
            await session.execute(text("SELECT 1"))

    async def create_all(self) -> None:
        """Creates the schema directly. Tests and local dev only; deployments run Alembic migrations."""
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()


def _sqlite_pragmas(connection: Any, _: Any) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()
