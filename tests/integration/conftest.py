from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from fraud_audit_assist.persistence.db import Database
from fraud_audit_assist.persistence.tables import Base

POSTGRES_URL = os.environ.get("FAA_TEST_POSTGRES_URL")

BACKENDS = [
    pytest.param("sqlite", id="sqlite"),
    pytest.param(
        "postgres",
        id="postgres",
        marks=[
            pytest.mark.postgres,
            pytest.mark.skipif(POSTGRES_URL is None, reason="FAA_TEST_POSTGRES_URL is not set"),
        ],
    ),
]


async def fresh_database(backend: str, tmp_path: Path) -> Database:
    if backend == "sqlite":
        db = Database(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    else:
        assert POSTGRES_URL is not None
        db = Database(POSTGRES_URL)
        async with db.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
    await db.create_all()
    return db


@pytest.fixture(params=BACKENDS)
async def database(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Database]:
    db = await fresh_database(request.param, tmp_path)
    yield db
    await db.dispose()
