"""A small API client for the simulator. Posting is safe to retry: events are idempotent on their ids."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from types import TracebackType
from typing import Any, Self

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from fraud_audit_assist.domain.events import Event


def _transient(error: BaseException) -> bool:
    if isinstance(error, httpx.TransportError):
        return True
    return isinstance(error, httpx.HTTPStatusError) and error.response.status_code in (429, 502, 503, 504)


class ApiClient:
    """Sends events with the ingest key and seeds with the analyst key: the API keeps the two roles apart."""

    def __init__(
        self, base_url: str, api_key: str | None, analyst_key: str | None = None, timeout: float = 30.0
    ) -> None:
        self._ingest_headers = {"X-API-Key": api_key} if api_key else {}
        self._analyst_headers = {"X-API-Key": analyst_key} if analyst_key else {}
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: type[BaseException] | BaseException | TracebackType | None) -> None:
        await self._http.aclose()

    async def post_events(self, events: Sequence[Event]) -> dict[str, Any]:
        result: dict[str, Any] = await self._send(
            "POST",
            "/v1/events",
            self._ingest_headers,
            {"events": [e.model_dump(mode="json") for e in events]},
        )
        return result

    async def confirm_fraud(self, account_id: str, decided_at: datetime) -> dict[str, Any]:
        result: dict[str, Any] = await self._send(
            "POST",
            f"/v1/accounts/{account_id}/confirmed-fraud",
            self._analyst_headers,
            {"decided_at": decided_at.isoformat()},
        )
        return result

    async def _send(self, method: str, path: str, headers: dict[str, str], body: Any = None) -> Any:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_transient),
            stop=stop_after_attempt(5),
            wait=wait_exponential_jitter(multiplier=0.5, max=10, jitter=0.5),
            reraise=True,
        ):
            with attempt:
                response = await self._http.request(method, path, json=body, headers=headers)
                response.raise_for_status()
                return response.json()
        raise AssertionError("unreachable")  # pragma: no cover
