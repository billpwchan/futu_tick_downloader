from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Iterable, List

from .db import SQLiteTickStore
from .models import TickRow

logger = logging.getLogger(__name__)


class AsyncTickCollector:
    def __init__(
        self,
        store: SQLiteTickStore,
        batch_size: int,
        max_wait_ms: int,
        max_queue_size: int,
    ) -> None:
        self._store = store
        self._batch_size = max(1, batch_size)
        self._max_wait = max(1, max_wait_ms) / 1000.0
        self._queue: asyncio.Queue[List[TickRow]] = asyncio.Queue(maxsize=max_queue_size)
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task

    def enqueue(self, rows: List[TickRow]) -> None:
        if not rows:
            return
        try:
            self._queue.put_nowait(rows)
        except asyncio.QueueFull:
            logger.warning("queue full, dropping %s rows", len(rows))

    async def _flush_loop(self) -> None:
        buffer: List[TickRow] = []
        loop = asyncio.get_running_loop()
        last_flush = loop.time()

        while True:
            if self._stop_event.is_set() and self._queue.empty():
                break

            timeout = max(0.0, self._max_wait - (loop.time() - last_flush))
            try:
                batch = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                buffer.extend(batch)
            except asyncio.TimeoutError:
                pass

            if buffer and (len(buffer) >= self._batch_size or (loop.time() - last_flush) >= self._max_wait):
                await self._flush(buffer)
                buffer = []
                last_flush = loop.time()

        if buffer:
            await self._flush(buffer)

    async def _flush(self, rows: Iterable[TickRow]) -> None:
        grouped: dict[str, List[TickRow]] = defaultdict(list)
        for row in rows:
            grouped[row.trading_day].append(row)

        for trading_day, day_rows in grouped.items():
            try:
                await asyncio.to_thread(self._store.insert_ticks, trading_day, day_rows)
            except Exception:
                logger.exception("failed to flush %s rows for %s", len(day_rows), trading_day)