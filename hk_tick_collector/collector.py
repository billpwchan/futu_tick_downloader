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
        persist_retry_max_attempts: int = 5,
        persist_retry_backoff_sec: float = 1.0,
    ) -> None:
        self._store = store
        self._batch_size = max(1, batch_size)
        self._max_wait = max(1, max_wait_ms) / 1000.0
        self._persist_retry_max_attempts = max(1, persist_retry_max_attempts)
        self._persist_retry_backoff_sec = max(0.1, persist_retry_backoff_sec)

        self._queue: asyncio.Queue[List[TickRow]] = asyncio.Queue(maxsize=max_queue_size)
        self._stop_event = asyncio.Event()
        self._fatal_event = asyncio.Event()
        self._fatal_error: BaseException | None = None
        self._task: asyncio.Task | None = None
        self._persist_observer: Optional[Callable[[List[TickRow], PersistResult], None]] = None

        self._persisted_rows_since_report = 0
        self._ignored_rows_since_report = 0
        self._queue_in_since_report = 0
        self._queue_out_since_report = 0
        self._commit_count_since_report = 0
        self._last_persist_at: float | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._flush_loop(), name="tick-persist-loop")
            self._task.add_done_callback(self._on_flush_task_done)

    async def stop(self, timeout_sec: float = 60.0, cancel_on_timeout: bool = False) -> None:
        self._stop_event.set()
        if self._task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout_sec)
        except asyncio.TimeoutError:
            logger.error(
                "collector_stop_timeout timeout_sec=%s queue=%s/%s cancel_on_timeout=%s",
                timeout_sec,
                self._queue.qsize(),
                self._queue.maxsize,
                cancel_on_timeout,
            )
            if cancel_on_timeout:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
            else:
                raise

    def set_persist_observer(self, observer: Optional[Callable[[List[TickRow], PersistResult], None]]) -> None:
        self._persist_observer = observer

    def enqueue(self, rows: List[TickRow]) -> bool:
        if not rows:
            return False
        try:
            self._queue.put_nowait(rows)
            self._queue_in_since_report += len(rows)
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

    def snapshot_pipeline_counters(self, reset: bool = False) -> dict[str, int]:
        counters = {
            "persisted_rows": self._persisted_rows_since_report,
            "ignored_rows": self._ignored_rows_since_report,
            "queue_in_rows": self._queue_in_since_report,
            "queue_out_rows": self._queue_out_since_report,
            "db_commits": self._commit_count_since_report,
        }
        if reset:
            self._persisted_rows_since_report = 0
            self._ignored_rows_since_report = 0
            self._queue_in_since_report = 0
            self._queue_out_since_report = 0
            self._commit_count_since_report = 0
        return counters

    def fatal_error(self) -> BaseException | None:
        return self._fatal_error

    async def wait_fatal(self) -> None:
        await self._fatal_event.wait()

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
            db_path = db_path_for_trading_day(Path(getattr(self._store, "_data_root", ".")), trading_day)
            last_seq = max((row.seq for row in day_rows if row.seq is not None), default=None)
            attempt = 0
            while True:
                attempt += 1
                try:
                    result = await asyncio.to_thread(self._store.insert_ticks, trading_day, day_rows)
                    persist_result = self._normalize_persist_result(trading_day, day_rows, result)
                    self._persisted_rows_since_report += persist_result.inserted
                    self._ignored_rows_since_report += persist_result.ignored
                    self._queue_out_since_report += len(day_rows)
                    self._commit_count_since_report += 1
                    self._last_persist_at = asyncio.get_running_loop().time()
                    if self._persist_observer is not None:
                        try:
                            self._persist_observer(day_rows, persist_result)
                        except Exception:
                            logger.exception("persist observer failed")
                    break
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "persist_flush_failed trading_day=%s db_path=%s batch=%s "
                        "attempt=%s/%s queue=%s/%s last_seq=%s",
                        trading_day,
                        db_path,
                        len(day_rows),
                        attempt,
                        self._persist_retry_max_attempts,
                        self._queue.qsize(),
                        self._queue.maxsize,
                        last_seq if last_seq is not None else "none",
                    )
                    if attempt >= self._persist_retry_max_attempts:
                        raise RuntimeError(
                            f"persist loop exhausted retries trading_day={trading_day} "
                            f"batch={len(day_rows)} queue={self._queue.qsize()}/{self._queue.maxsize}"
                        )
                    await asyncio.sleep(self._persist_retry_backoff_sec * attempt)

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

    def _on_flush_task_done(self, task: asyncio.Task) -> None:
        if self._stop_event.is_set():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError as exc:
            error = exc
        if error is None:
            error = RuntimeError("persist loop exited unexpectedly without exception")
            logger.error("persist_loop_exited reason=unexpected_normal_exit queue=%s/%s", self._queue.qsize(), self._queue.maxsize)
        else:
            exc_info = (type(error), error, error.__traceback__)
            logger.error(
                "persist_loop_exited reason=exception queue=%s/%s",
                self._queue.qsize(),
                self._queue.maxsize,
                exc_info=exc_info,
            )
        self._fatal_error = error
        self._fatal_event.set()
