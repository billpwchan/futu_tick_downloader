from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Iterable
from typing import Optional

from .models import TickRow
from .sqlite_store import SQLiteStore


class TickPersistQueue:
    def __init__(self, store: SQLiteStore, batch_size: int, max_wait_ms: int) -> None:
        self._store = store
        self._batch_size = batch_size
        self._max_wait = max_wait_ms / 1000
        self._queue: asyncio.Queue[Optional[list[TickRow]]] = asyncio.Queue()
        self._task: Optional[asyncio.Task[None]] = None
        self._last_tick_ts_ms = 0

    @property
    def last_tick_ts_ms(self) -> int:
        return self._last_tick_ts_ms

    def queue_size(self) -> int:
        return self._queue.qsize()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        if self._task is None:
            return
        await self._queue.put(None)
        await self._task
        self._task = None

    async def enqueue(self, ticks: Iterable[TickRow]) -> None:
        batch = list(ticks)
        if not batch:
            return
        max_ts = max(tick.ts_ms for tick in batch)
        if max_ts > self._last_tick_ts_ms:
            self._last_tick_ts_ms = max_ts
        await self._queue.put(batch)

    async def _worker(self) -> None:
        batch: list[TickRow] = []
        last_flush = time.monotonic()
        while True:
            if not batch:
                item = await self._queue.get()
            else:
                timeout = max(self._max_wait - (time.monotonic() - last_flush), 0)
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    await self._flush(batch)
                    batch.clear()
                    last_flush = time.monotonic()
                    continue
            if item is None:
                break
            batch.extend(item)
            if len(batch) >= self._batch_size:
                await self._flush(batch)
                batch.clear()
                last_flush = time.monotonic()
        if batch:
            await self._flush(batch)

    async def _flush(self, ticks: list[TickRow]) -> None:
        buckets: dict[tuple[str, str], list[TickRow]] = defaultdict(list)
        for tick in ticks:
            buckets[(tick.market, tick.trading_day)].append(tick)
        for (market, trading_day), items in buckets.items():
            await self._store.write_ticks(market, trading_day, items)
