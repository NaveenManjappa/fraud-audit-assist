"""Alembic environment: async engine, URL from the application's settings."""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from fraud_audit_assist.config import Settings
from fraud_audit_assist.persistence.tables import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    # An explicit -x url=... wins, so migrations can be generated against a scratch database.
    return context.get_x_argument(as_dictionary=True).get("url") or Settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    # Batch mode lets ALTER TABLE work on SQLite, which cannot alter columns in place.
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = async_engine_from_config(
        {"sqlalchemy.url": _url()}, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
