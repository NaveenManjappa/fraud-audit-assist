"""The Case queue: in-process now, behind a protocol so a durable broker can replace it (ADR-0004)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Protocol


class CaseQueue(Protocol):
    async def put(self, case_id: uuid.UUID) -> None: ...

    async def get(self) -> uuid.UUID: ...

    def task_done(self) -> None: ...


class InProcessCaseQueue:
    """A bounded asyncio queue. When full, ingestion waits rather than letting memory grow.

    The database, not this queue, is the durable record: on startup the worker re-queues unfinished Cases.
    """

    def __init__(self, capacity: int = 1000) -> None:
        self._queue: asyncio.Queue[uuid.UUID] = asyncio.Queue(maxsize=capacity)

    async def put(self, case_id: uuid.UUID) -> None:
        await self._queue.put(case_id)

    async def get(self) -> uuid.UUID:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    async def join(self) -> None:
        await self._queue.join()

    def qsize(self) -> int:
        return self._queue.qsize()
