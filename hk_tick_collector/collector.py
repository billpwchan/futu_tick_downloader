from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from .db import PersistResult, SQLiteTickStore, SQLiteTickWriter, db_path_for_trading_day, is_sqlite_busy_or_locked
from .models import TickRow

logger = logging.getLogger(__name__)


class _WorkerRestartRequested(RuntimeError):
    pass


class AsyncTickCollector:
    def __init__(
        self,
        store: SQLiteTickStore,
        batch_size: int,
        max_wait_ms: int,
        max_queue_size: int,
        persist_retry_max_attempts: int = 0,
        persist_retry_backoff_sec: float = 0.05,
        persist_retry_backoff_max_sec: float = 2.0,
        heartbeat_interval_sec: float = 30.0,
    ) -> None:
        self._store = store
        self._batch_size = max(1, batch_size)
        self._max_wait = max(1, max_wait_ms) / 1000.0
        self._persist_retry_max_attempts = max(0, int(persist_retry_max_attempts))
        self._persist_retry_backoff_sec = max(0.01, float(persist_retry_backoff_sec))
        self._persist_retry_backoff_max_sec = max(
            self._persist_retry_backoff_sec,
            float(persist_retry_backoff_max_sec),
        )
        self._heartbeat_interval_sec = max(1.0, float(heartbeat_interval_sec))

        self._queue: queue.Queue[List[TickRow]] = queue.Queue(maxsize=max_queue_size)
        self._stop_event = threading.Event()
        self._fatal_event = asyncio.Event()
        self._fatal_error: BaseException | None = None

        self._worker: threading.Thread | None = None
        self._worker_stop_event: threading.Event | None = None
        self._worker_generation = 0
        self._restart_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._persist_observer: Optional[Callable[[List[TickRow], PersistResult], None]] = None

        self._state_lock = threading.Lock()
        self._worker_alive = False
        self._persisted_rows_since_report = 0
        self._ignored_rows_since_report = 0
        self._queue_in_since_report = 0
        self._queue_out_since_report = 0
        self._commit_count_since_report = 0
        self._total_rows_dequeued = 0
        self._total_rows_committed = 0
        self._total_commits = 0
        self._last_progress_at: float | None = None
        self._last_drain_at: float | None = None
        self._last_commit_at: float | None = None
        self._last_dequeue_monotonic: float | None = None
        self._last_commit_monotonic: float | None = None
        self._last_commit_rows = 0
        self._last_exception_type = "none"
        self._last_exception_count = 0
        self._last_exception_at: float | None = None
        self._last_exception_monotonic: float | None = None
        self._last_recovery_monotonic: float | None = None
        self._recovery_count = 0
        self._busy_locked_count = 0
        self._busy_backoff_count = 0
        self._last_backoff_sec = 0.0

    async def start(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return

        self._loop = asyncio.get_running_loop()
        self._stop_event.clear()
        self._fatal_error = None
        with self._restart_lock:
            self._spawn_worker_locked(reason="startup")
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="tick-persist-heartbeat")

    async def stop(self, timeout_sec: float = 60.0, cancel_on_timeout: bool = False) -> None:
        self._stop_event.set()
        worker_stop_event = self._worker_stop_event
        if worker_stop_event is not None:
            worker_stop_event.set()

        worker = self._worker
        if worker is not None:
            await asyncio.to_thread(worker.join, timeout_sec)
            if worker.is_alive():
                logger.error(
                    "collector_stop_timeout timeout_sec=%s queue=%s/%s cancel_on_timeout=%s",
                    timeout_sec,
                    self._queue.qsize(),
                    self._queue.maxsize,
                    cancel_on_timeout,
                )
                raise asyncio.TimeoutError("persist worker thread did not stop within timeout")
            self._worker = None
            self._worker_stop_event = None

        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
            self._heartbeat_task = None

        if self._fatal_error is not None:
            raise RuntimeError("persist worker terminated with fatal error") from self._fatal_error

    def set_persist_observer(self, observer: Optional[Callable[[List[TickRow], PersistResult], None]]) -> None:
        self._persist_observer = observer

    def enqueue(self, rows: List[TickRow]) -> bool:
        if not rows:
            return False
        try:
            self._queue.put_nowait(rows)
            with self._state_lock:
                self._queue_in_since_report += len(rows)
            return True
        except queue.Full:
            logger.warning("queue full, dropping %s rows", len(rows))
            return False

    def queue_size(self) -> int:
        return self._queue.qsize()

    def queue_maxsize(self) -> int:
        return self._queue.maxsize

    def get_last_persist_at(self) -> float | None:
        with self._state_lock:
            return self._last_commit_at

    def snapshot_pipeline_counters(self, reset: bool = False) -> dict[str, int]:
        with self._state_lock:
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

    def snapshot_runtime_state(self) -> dict[str, object]:
        with self._state_lock:
            return {
                "worker_alive": self._worker_alive,
                "last_progress_at": self._last_progress_at,
                "last_drain_at": self._last_drain_at,
                "last_commit_at": self._last_commit_at,
                "last_dequeue_monotonic": self._last_dequeue_monotonic,
                "last_commit_monotonic": self._last_commit_monotonic,
                "last_commit_rows": self._last_commit_rows,
                "last_exception_type": self._last_exception_type,
                "last_exception_count": self._last_exception_count,
                "last_exception_at": self._last_exception_at,
                "last_exception_monotonic": self._last_exception_monotonic,
                "last_recovery_monotonic": self._last_recovery_monotonic,
                "recovery_count": self._recovery_count,
                "busy_locked_count": self._busy_locked_count,
                "busy_backoff_count": self._busy_backoff_count,
                "last_backoff_sec": self._last_backoff_sec,
                "total_rows_dequeued": self._total_rows_dequeued,
                "total_rows_committed": self._total_rows_committed,
                "total_commits": self._total_commits,
            }

    def request_writer_recovery(self, reason: str, join_timeout_sec: float = 3.0) -> bool:
        if self._stop_event.is_set():
            return False

        wait_sec = max(0.1, float(join_timeout_sec))
        with self._restart_lock:
            worker = self._worker
            worker_stop_event = self._worker_stop_event

            if worker is not None and worker.is_alive() and worker_stop_event is not None:
                logger.warning(
                    "persist_recovery_request reason=%s queue=%s/%s generation=%s",
                    reason,
                    self._queue.qsize(),
                    self._queue.maxsize,
                    self._worker_generation,
                )
                worker_stop_event.set()
                worker.join(wait_sec)
                if worker.is_alive():
                    logger.error(
                        "persist_recovery_failed reason=%s queue=%s/%s generation=%s",
                        reason,
                        self._queue.qsize(),
                        self._queue.maxsize,
                        self._worker_generation,
                    )
                    return False

            self._fatal_error = None
            self._spawn_worker_locked(reason=f"recovery:{reason}")
            now = time.monotonic()
            with self._state_lock:
                self._last_recovery_monotonic = now
                self._recovery_count += 1
                self._last_progress_at = now
            logger.warning(
                "persist_recovery_success reason=%s queue=%s/%s generation=%s",
                reason,
                self._queue.qsize(),
                self._queue.maxsize,
                self._worker_generation,
            )
            return True

    def fatal_error(self) -> BaseException | None:
        return self._fatal_error

    async def wait_fatal(self) -> None:
        await self._fatal_event.wait()

    def _spawn_worker_locked(self, *, reason: str) -> None:
        self._worker_generation += 1
        worker_stop_event = threading.Event()
        worker = threading.Thread(
            target=self._worker_loop,
            args=(worker_stop_event, self._worker_generation),
            name=f"tick-persist-worker-{self._worker_generation}",
            daemon=True,
        )
        self._worker_stop_event = worker_stop_event
        self._worker = worker
        worker.start()
        logger.info(
            "persist_worker_started reason=%s generation=%s queue=%s/%s",
            reason,
            self._worker_generation,
            self._queue.qsize(),
            self._queue.maxsize,
        )

    def _worker_loop(self, worker_stop_event: threading.Event, generation: int) -> None:
        writer = self._store.open_writer()
        buffer: List[TickRow] = []
        last_flush = time.monotonic()

        with self._state_lock:
            self._worker_alive = True
            self._last_progress_at = time.monotonic()

        try:
            while True:
                now = time.monotonic()
                should_stop = (
                    (self._stop_event.is_set() and self._queue.empty() and not buffer)
                    or (worker_stop_event.is_set() and not buffer)
                )
                if should_stop:
                    break

                if not worker_stop_event.is_set():
                    timeout = min(0.25, max(0.01, self._max_wait))
                    try:
                        batch = self._queue.get(timeout=timeout)
                        if batch:
                            buffer.extend(batch)
                            now = time.monotonic()
                            with self._state_lock:
                                self._queue_out_since_report += len(batch)
                                self._total_rows_dequeued += len(batch)
                                self._last_drain_at = now
                                self._last_dequeue_monotonic = now
                                self._last_progress_at = now
                    except queue.Empty:
                        pass

                now = time.monotonic()
                should_flush = bool(buffer) and (
                    len(buffer) >= self._batch_size
                    or (now - last_flush) >= self._max_wait
                    or (self._stop_event.is_set() and self._queue.empty())
                    or worker_stop_event.is_set()
                )
                if should_flush:
                    self._flush_buffer(writer, buffer, worker_stop_event=worker_stop_event)
                    buffer = []
                    last_flush = time.monotonic()

                with self._state_lock:
                    self._last_progress_at = time.monotonic()

            if buffer:
                self._flush_buffer(writer, buffer, worker_stop_event=worker_stop_event)
        except _WorkerRestartRequested:
            self._requeue_rows(buffer)
            logger.warning(
                "persist_worker_restart_requested queue=%s/%s generation=%s",
                self._queue.qsize(),
                self._queue.maxsize,
                generation,
            )
        except Exception as exc:
            self._fatal_error = exc
            self._record_exception(exc, backoff_sec=0.0, is_busy_locked=False)
            logger.error(
                "persist_worker_fatal queue=%s/%s",
                self._queue.qsize(),
                self._queue.maxsize,
                exc_info=True,
            )
            self._signal_fatal()
        finally:
            writer.close()
            with self._state_lock:
                self._worker_alive = False
                self._last_progress_at = time.monotonic()

            if not self._stop_event.is_set() and not worker_stop_event.is_set() and self._fatal_error is None:
                self._fatal_error = RuntimeError("persist worker exited unexpectedly")
                logger.error(
                    "persist_worker_exited_unexpectedly queue=%s/%s",
                    self._queue.qsize(),
                    self._queue.maxsize,
                )
                self._signal_fatal()

    def _flush_buffer(
        self,
        writer: SQLiteTickWriter,
        rows: Iterable[TickRow],
        *,
        worker_stop_event: threading.Event,
    ) -> None:
        grouped: dict[str, List[TickRow]] = defaultdict(list)
        for row in rows:
            grouped[row.trading_day].append(row)

        for trading_day, day_rows in grouped.items():
            self._flush_day_rows_with_retry(
                writer,
                trading_day,
                day_rows,
                worker_stop_event=worker_stop_event,
            )

    def _flush_day_rows_with_retry(
        self,
        writer: SQLiteTickWriter,
        trading_day: str,
        rows: List[TickRow],
        *,
        worker_stop_event: threading.Event,
    ) -> None:
        db_path = db_path_for_trading_day(Path(getattr(self._store, "_data_root", ".")), trading_day)
        last_seq = max((row.seq for row in rows if row.seq is not None), default=None)
        attempt = 0

        while True:
            if worker_stop_event.is_set() and not self._stop_event.is_set():
                raise _WorkerRestartRequested("writer recovery requested")

            attempt += 1
            with self._state_lock:
                self._last_progress_at = time.monotonic()

            try:
                result = writer.insert_ticks(trading_day, rows)
                persist_result = self._normalize_persist_result(trading_day, rows, result)
                now = time.monotonic()
                with self._state_lock:
                    self._persisted_rows_since_report += persist_result.inserted
                    self._ignored_rows_since_report += persist_result.ignored
                    self._commit_count_since_report += 1
                    self._total_rows_committed += persist_result.inserted
                    self._total_commits += 1
                    self._last_commit_at = now
                    self._last_commit_monotonic = now
                    self._last_commit_rows = persist_result.inserted
                    self._last_progress_at = now
                self._notify_observer(rows, persist_result)
                return
            except Exception as exc:
                is_busy_locked = is_sqlite_busy_or_locked(exc)
                backoff_sec = min(
                    self._persist_retry_backoff_sec * (2 ** min(attempt - 1, 10)),
                    self._persist_retry_backoff_max_sec,
                )
                self._record_exception(exc, backoff_sec=backoff_sec, is_busy_locked=is_busy_locked)
                writer.reset_connection(trading_day)

                if is_busy_locked:
                    logger.warning(
                        "sqlite_busy_backoff trading_day=%s db_path=%s batch=%s attempt=%s "
                        "sleep_sec=%.3f queue=%s/%s last_seq=%s",
                        trading_day,
                        db_path,
                        len(rows),
                        attempt,
                        backoff_sec,
                        self._queue.qsize(),
                        self._queue.maxsize,
                        last_seq if last_seq is not None else "none",
                        exc_info=True,
                    )
                else:
                    logger.error(
                        "persist_flush_failed trading_day=%s db_path=%s batch=%s attempt=%s "
                        "queue=%s/%s last_seq=%s",
                        trading_day,
                        db_path,
                        len(rows),
                        attempt,
                        self._queue.qsize(),
                        self._queue.maxsize,
                        last_seq if last_seq is not None else "none",
                        exc_info=True,
                    )

                if self._persist_retry_max_attempts > 0 and attempt >= self._persist_retry_max_attempts:
                    logger.error(
                        "persist_retry_budget_exhausted trading_day=%s batch=%s attempts=%s "
                        "queue=%s/%s continuing_with_backoff",
                        trading_day,
                        len(rows),
                        attempt,
                        self._queue.qsize(),
                        self._queue.maxsize,
                    )
                    attempt = 0

                if worker_stop_event.is_set() and not self._stop_event.is_set():
                    raise _WorkerRestartRequested("writer recovery requested") from exc
                self._sleep_backoff(backoff_sec)

    def _notify_observer(self, rows: List[TickRow], result: PersistResult) -> None:
        if self._persist_observer is None:
            return
        loop = self._loop
        if loop is None:
            return

        payload = list(rows)

        def _invoke() -> None:
            if self._persist_observer is None:
                return
            try:
                self._persist_observer(payload, result)
            except Exception:
                logger.exception("persist observer failed")

        loop.call_soon_threadsafe(_invoke)

    async def _heartbeat_loop(self) -> None:
        prev_dequeued = 0
        prev_committed = 0
        prev_at = time.monotonic()

        while True:
            if self._stop_event.is_set() and (self._worker is None or not self._worker.is_alive()):
                return
            await asyncio.sleep(self._heartbeat_interval_sec)

            now = time.monotonic()
            runtime = self.snapshot_runtime_state()
            elapsed = max(0.001, now - prev_at)
            dequeued = int(runtime["total_rows_dequeued"])
            committed = int(runtime["total_rows_committed"])
            drain_rate = (dequeued - prev_dequeued) / elapsed
            commit_rate = (committed - prev_committed) / elapsed

            last_drain_at = runtime["last_drain_at"]
            last_commit_at = runtime["last_commit_at"]
            drain_age = None if last_drain_at is None else max(0.0, now - float(last_drain_at))
            commit_age = None if last_commit_at is None else max(0.0, now - float(last_commit_at))
            wal_size_bytes = self._wal_size_bytes()

            logger.info(
                "persist_loop_heartbeat worker_alive=%s queue=%s/%s drain_rate_rows_per_sec=%.2f "
                "commit_rate_rows_per_sec=%.2f last_drain_ts_age_sec=%s last_commit_ts_age_sec=%s "
                "last_exception_type=%s last_exception_count=%s busy_locked_count=%s "
                "busy_backoff_count=%s last_backoff_sec=%.3f last_commit_rows=%s wal_bytes=%s recovery_count=%s",
                runtime["worker_alive"],
                self._queue.qsize(),
                self._queue.maxsize,
                drain_rate,
                commit_rate,
                f"{drain_age:.1f}" if drain_age is not None else "none",
                f"{commit_age:.1f}" if commit_age is not None else "none",
                runtime["last_exception_type"],
                runtime["last_exception_count"],
                runtime["busy_locked_count"],
                runtime["busy_backoff_count"],
                float(runtime["last_backoff_sec"]),
                runtime["last_commit_rows"],
                wal_size_bytes,
                runtime["recovery_count"],
            )

            prev_dequeued = dequeued
            prev_committed = committed
            prev_at = now

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

    def _record_exception(self, exc: BaseException, *, backoff_sec: float, is_busy_locked: bool) -> None:
        now = time.monotonic()
        with self._state_lock:
            exc_type = type(exc).__name__
            if exc_type == self._last_exception_type:
                self._last_exception_count += 1
            else:
                self._last_exception_type = exc_type
                self._last_exception_count = 1
            self._last_exception_at = now
            self._last_exception_monotonic = now
            self._last_backoff_sec = backoff_sec
            self._last_progress_at = now
            if is_busy_locked:
                self._busy_locked_count += 1
                self._busy_backoff_count += 1

    def _requeue_rows(self, rows: List[TickRow]) -> None:
        if not rows:
            return
        payload = list(rows)
        while not self._stop_event.is_set():
            try:
                self._queue.put(payload, timeout=0.1)
                logger.warning(
                    "persist_requeue_rows rows=%s queue=%s/%s",
                    len(payload),
                    self._queue.qsize(),
                    self._queue.maxsize,
                )
                return
            except queue.Full:
                continue

    def _wal_size_bytes(self) -> int:
        data_root = Path(getattr(self._store, "_data_root", "."))
        if not data_root.exists():
            return 0
        total = 0
        for wal_path in data_root.glob("*.db-wal"):
            try:
                total += wal_path.stat().st_size
            except OSError:
                continue
        return total

    def _sleep_backoff(self, delay_sec: float) -> None:
        if delay_sec <= 0:
            return
        deadline = time.monotonic() + delay_sec
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.1, remaining))

    def _signal_fatal(self) -> None:
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._fatal_event.set)
