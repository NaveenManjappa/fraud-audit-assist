"""API-key authentication with two roles: `ingest` for source systems, `analyst` for people working Cases.

Least privilege: a source system's key can post events and nothing else, so a leaked integration key cannot read
Cases or decide them. Keys are compared in constant time.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

Role = Literal["ingest", "analyst"]

_api_key = APIKeyHeader(name="X-API-Key", auto_error=False)


def require(role: Role) -> Callable[..., Awaitable[None]]:
    async def check(request: Request, key: str | None = Security(_api_key)) -> None:
        settings = request.app.state.container.settings
        if not settings.auth_enabled:
            return
        expected = settings.ingest_api_key if role == "ingest" else settings.analyst_api_key
        if key is None or expected is None or not secrets.compare_digest(key, expected.get_secret_value()):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "missing or invalid API key", {"WWW-Authenticate": "ApiKey"}
            )

    return check
