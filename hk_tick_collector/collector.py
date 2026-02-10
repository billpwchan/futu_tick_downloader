from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from .db import PersistResult, SQLiteTickStore, db_path_for_trading_day
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
        self._persist_observer: Optional[Callable[[List[TickRow], PersistResult], None]] = None
        self._persisted_rows_since_report = 0
        self._ignored_rows_since_report = 0
        self._last_persist_at: float | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._flush_loop())

    async def stop(self, timeout_sec: float = 12.0) -> None:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout_sec)
            except asyncio.TimeoutError:
                logger.error("collector_stop_timeout timeout_sec=%s, cancelling flush task", timeout_sec)
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)

    def set_persist_observer(self, observer: Optional[Callable[[List[TickRow], PersistResult], None]]) -> None:
        self._persist_observer = observer

    def enqueue(self, rows: List[TickRow]) -> bool:
        if not rows:
            return False
        try:
            self._queue.put_nowait(rows)
            return True
        except asyncio.QueueFull:
            logger.warning("queue full, dropping %s rows", len(rows))
            return False

    def queue_size(self) -> int:
        return self._queue.qsize()

    def queue_maxsize(self) -> int:
        return self._queue.maxsize

    def get_last_persist_at(self) -> float | None:
        return self._last_persist_at

    def snapshot_persist_counters(self, reset: bool = False) -> tuple[int, int]:
        persisted = self._persisted_rows_since_report
        ignored = self._ignored_rows_since_report
        if reset:
            self._persisted_rows_since_report = 0
            self._ignored_rows_since_report = 0
        return persisted, ignored

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
                result = await asyncio.to_thread(self._store.insert_ticks, trading_day, day_rows)
                persist_result = self._normalize_persist_result(trading_day, day_rows, result)
                self._persisted_rows_since_report += persist_result.inserted
                self._ignored_rows_since_report += persist_result.ignored
                self._last_persist_at = asyncio.get_running_loop().time()
                if self._persist_observer is not None:
                    try:
                        self._persist_observer(day_rows, persist_result)
                    except Exception:
                        logger.exception("persist observer failed")
            except Exception:
                logger.exception("failed to flush %s rows for %s", len(day_rows), trading_day)

    def _normalize_persist_result(
        self,
        trading_day: str,
        rows: List[TickRow],
        result: object,
    ) -> PersistResult:
        if isinstance(result, PersistResult):
            return result
        inserted = int(result) if isinstance(result, int) else 0
        ignored = max(0, len(rows) - inserted)
        data_root = Path(getattr(self._store, "_data_root", "."))
        return PersistResult(
            db_path=db_path_for_trading_day(data_root, trading_day),
            batch=len(rows),
            inserted=inserted,
            ignored=ignored,
            commit_latency_ms=0,
        )
