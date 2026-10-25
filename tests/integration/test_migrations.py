"""Migrations build the same schema as the models, on every supported database."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from tests.integration.conftest import BACKENDS, POSTGRES_URL


@pytest.mark.parametrize("backend", BACKENDS)
async def test_migrations_match_the_models(backend: str, tmp_path: Path) -> None:
    if backend == "sqlite":
        url = f"sqlite+aiosqlite:///{tmp_path / 'migrated.db'}"
    else:
        from fraud_audit_assist.persistence.tables import Base
        from tests.integration.conftest import fresh_database

        db = await fresh_database("postgres", tmp_path)
        async with db.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
        await db.dispose()
        assert POSTGRES_URL is not None
        url = POSTGRES_URL

    def alembic(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed arguments, test only
            [sys.executable, "-m", "alembic", "-x", f"url={url}", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    upgrade = alembic("upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr
    check = alembic("check")
    assert check.returncode == 0, check.stdout + check.stderr
    downgrade = alembic("downgrade", "base")
    assert downgrade.returncode == 0, downgrade.stderr
