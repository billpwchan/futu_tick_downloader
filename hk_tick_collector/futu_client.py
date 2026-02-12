from __future__ import annotations

import asyncio
import faulthandler
import logging
import os
import resource
import shutil
import sys
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Deque, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from futu import OpenQuoteContext, RET_OK, Session, SubType, TickerHandlerBase

from .collector import AsyncTickCollector
from .config import Config
from .db import PersistResult, SQLiteTickStore, db_path_for_trading_day
from .mapping import ticker_df_to_rows
from .models import TickRow
from .notifiers.telegram import (
    AlertEvent,
    HealthSnapshot,
    NotifySeverity,
    SymbolSnapshot,
    TelegramNotifier,
)
from .utils import ExponentialBackoff

logger = logging.getLogger(__name__)

POLL_SKIP_PUSH_SEC = 2
POLL_RECENT_KEY_LIMIT = 500
HEALTH_LOG_INTERVAL_SEC = 60
WATCHDOG_EXIT_CODE = 1
HK_TZ = ZoneInfo("Asia/Hong_Kong")


class FutuTickerHandler(TickerHandlerBase):
    def __init__(
        self, on_rows: Callable[[List[TickRow]], None], loop: asyncio.AbstractEventLoop
    ) -> None:
        super().__init__()
        self._on_rows = on_rows
        self._loop = loop

    def on_recv_rsp(self, rsp_pb):
        ret, data = super().on_recv_rsp(rsp_pb)
        if ret != RET_OK:
            logger.error("ticker push error: %s", data)
            return ret, data

        try:
            rows = ticker_df_to_rows(data, provider="futu", push_type="push")
        except Exception:
            logger.exception("failed to map ticker data")
            return ret, data

        if rows:
            self._loop.call_soon_threadsafe(self._on_rows, rows)
        return ret, data


class FutuQuoteClient:
    def __init__(
        self,
        config: Config,
        collector: AsyncTickCollector,
        loop: asyncio.AbstractEventLoop,
        initial_last_seq: Optional[Dict[str, int]] = None,
        context_factory: Optional[Callable[..., OpenQuoteContext]] = None,
        store: Optional[SQLiteTickStore] = None,
        notifier: Optional[TelegramNotifier] = None,
    ) -> None:
        self._config = config
        self._collector = collector
        self._loop = loop
        self._store = store
        self._notifier = notifier
        self._ctx: OpenQuoteContext | None = None
        self._context_factory = context_factory or OpenQuoteContext
        self._handler = FutuTickerHandler(self._handle_push_rows, loop)
        self._stop_event = asyncio.Event()
        self._connected = False

        seed = dict(initial_last_seq or {})
        self._last_seen_seq: Dict[str, int] = {}
        self._last_accepted_seq: Dict[str, int] = dict(seed)
        self._last_persisted_seq: Dict[str, int] = dict(seed)

        self._last_tick_seen_at: Dict[str, float] = {}
        self._last_push_at: Dict[str, float] = {}
        self._recent_keys: Dict[str, Deque[tuple]] = {}
        self._recent_key_sets: Dict[str, set] = {}
        self._last_poll_fetched_seq: Dict[str, int] = {}

        self._started_at = self._loop.time()
        self._last_upstream_active_at: float | None = None
        self._max_ts_ms_seen: int | None = None
        self._last_ts_ms_by_symbol: Dict[str, int] = {}

        self._push_rows_since_report = 0
        self._poll_fetched_since_report = 0
        self._poll_accepted_since_report = 0
        self._poll_enqueued_since_report = 0
        self._poll_seq_advanced_since_report = 0
        self._dropped_queue_full_since_report = 0
        self._dropped_duplicate_since_report = 0
        self._dropped_filter_since_report = 0
        self._watchdog_last_queue_size = 0
        self._watchdog_last_check_at = self._started_at
        self._watchdog_heal_failures = 0
        self._watchdog_heal_attempts = 0
        self._watchdog_last_heal_at: float | None = None
        self._watchdog_dumped = False
        self._last_busy_backoff_count = 0

    async def run_forever(self) -> None:
        backoff = ExponentialBackoff(
            self._config.reconnect_min_delay, self._config.reconnect_max_delay
        )
        try:
            while not self._stop_event.is_set():
                poll_task: asyncio.Task | None = None
                health_task: asyncio.Task | None = None
                monitor_task: asyncio.Task | None = None
                try:
                    await self._connect_and_subscribe()
                    backoff.reset()
                    poll_task = asyncio.create_task(self._poll_loop())
                    health_task = asyncio.create_task(self._health_loop())
                    monitor_task = asyncio.create_task(self._monitor_connection())

                    done, pending = await asyncio.wait(
                        {poll_task, health_task, monitor_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    fatal: BaseException | None = None
                    for task in done:
                        if task.cancelled():
                            continue
                        exc = task.exception()
                        if exc is not None:
                            fatal = exc
                            break

                    if fatal is None and monitor_task not in done:
                        fatal = RuntimeError("background task exited unexpectedly")
                    if fatal is not None:
                        raise fatal
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("futu connection error: %s", exc)
                    if self._notifier is not None:
                        trading_day = self._current_trading_day()
                        err_name = type(exc).__name__
                        self._notifier.submit_alert(
                            AlertEvent(
                                created_at=datetime.now(tz=timezone.utc),
                                code="DISCONNECT",
                                key=f"DISCONNECT:{err_name}",
                                fingerprint=f"DISCONNECT:{err_name}",
                                trading_day=trading_day,
                                severity=NotifySeverity.WARN.value,
                                headline="注意：與 OpenD 連線中斷，系統正在嘗試重連",
                                impact="短時間內可能有資料缺口，重連成功後可恢復",
                                summary_lines=[
                                    f"error_type={err_name}",
                                    f"error={str(exc)[:200] if str(exc) else 'n/a'}",
                                    f"host={self._config.futu_host}:{self._config.futu_port}",
                                ],
                                suggestions=[
                                    "journalctl -u hk-tick-collector -n 120 --no-pager",
                                    "systemctl status futu-opend --no-pager",
                                ],
                            )
                        )
                finally:
                    self._connected = False
                    for task in (poll_task, health_task, monitor_task):
                        if task is not None:
                            task.cancel()
                    for task in (poll_task, health_task, monitor_task):
                        if task is not None:
                            await asyncio.gather(task, return_exceptions=True)
                    self._close_ctx()

                if self._stop_event.is_set():
                    break

                delay = backoff.next_delay()
                logger.info("reconnecting in %ss", delay)
                await self._sleep_with_stop(delay)
        finally:
            self._connected = False
            self._close_ctx()

    async def stop(self) -> None:
        self._stop_event.set()
        self._close_ctx()

    def handle_persist_result(self, rows: List[TickRow], result: PersistResult) -> None:
        if not rows:
            return

        for row in rows:
            if row.seq is None:
                continue
            self._update_seq_max(self._last_persisted_seq, row.symbol, row.seq)

    async def _connect_and_subscribe(self) -> None:
        if not self._config.symbols:
            raise RuntimeError("FUTU_SYMBOLS is empty")

        self._close_ctx()
        self._ctx = self._context_factory(host=self._config.futu_host, port=self._config.futu_port)
        self._ctx.set_handler(self._handler)

        logger.info(
            "futu_connecting host=%s port=%s", self._config.futu_host, self._config.futu_port
        )
        ret, data = self._ctx.subscribe(
            self._config.symbols,
            [SubType.TICKER],
            subscribe_push=True,
            session=Session.ALL,
        )
        logger.info("subscribe ret=%s msg=%s symbols=%s", ret, data, ",".join(self._config.symbols))
        if ret != RET_OK:
            raise RuntimeError(f"subscribe failed: {data}")

        self._connected = True
        logger.info(
            "futu_connected host=%s port=%s", self._config.futu_host, self._config.futu_port
        )

        if self._config.backfill_n > 0:
            await self._backfill_recent()

    async def _monitor_connection(self) -> None:
        while not self._stop_event.is_set():
            await self._sleep_with_stop(self._config.check_interval_sec)
            if self._stop_event.is_set():
                return
            if self._ctx is None:
                logger.warning("futu_disconnected reason=context_closed")
                raise RuntimeError("context closed")
            if hasattr(self._ctx, "get_global_state"):
                ret, data = self._ctx.get_global_state()
                if ret != RET_OK:
                    logger.warning("futu_disconnected reason=get_global_state_failed msg=%s", data)
                    raise RuntimeError(f"get_global_state failed: {data}")
                continue
            logger.debug("connection health check skipped: no supported method")

    async def _poll_loop(self) -> None:
        if not self._config.poll_enabled:
            logger.info("poll_disabled")
            await self._stop_event.wait()
            return

        while not self._stop_event.is_set():
            cycle_start = self._loop.time()
            if self._ctx is None:
                await self._sleep_with_stop(self._config.poll_interval_sec)
                continue

            for symbol in self._config.symbols:
                if self._stop_event.is_set():
                    break
                if self._ctx is None:
                    break
                if self._should_skip_poll(symbol):
                    continue

                try:
                    ret, data = self._ctx.get_rt_ticker(symbol, num=self._config.poll_num)
                except Exception as exc:
                    logger.warning("poll_error symbol=%s err=%s", symbol, exc)
                    continue
                if ret != RET_OK:
                    logger.warning("poll_failed symbol=%s msg=%s", symbol, data)
                    continue

                try:
                    rows = ticker_df_to_rows(
                        data,
                        provider="futu",
                        push_type="poll",
                        default_symbol=symbol,
                    )
                except Exception:
                    logger.exception("poll_map_failed symbol=%s", symbol)
                    continue

                self._record_seen_rows(rows, source="poll")
                fetched = len(rows)
                fetched_last_seq = self._max_seq(rows)
                self._poll_fetched_since_report += fetched
                self._record_poll_seq_advance(symbol, fetched_last_seq)

                new_rows, dropped_duplicate, dropped_filter = self._filter_polled_rows(symbol, rows)
                accepted = len(new_rows)
                self._poll_accepted_since_report += accepted
                self._dropped_duplicate_since_report += dropped_duplicate
                self._dropped_filter_since_report += dropped_filter

                if new_rows:
                    enqueued, accepted_max = self._handle_rows(new_rows, source="poll")
                else:
                    enqueued, accepted_max = 0, {}

                for accepted_symbol, accepted_last_seq in accepted_max.items():
                    self._update_seq_max(
                        self._last_accepted_seq, accepted_symbol, accepted_last_seq
                    )

                dropped_queue_full = max(0, accepted - enqueued)
                self._poll_enqueued_since_report += enqueued
                pipeline = self._collector.snapshot_pipeline_counters(reset=False)
                drift_sec = self._drift_sec()
                last_commit_age_sec = self._last_commit_age_sec(now=self._loop.time())

                logger.info(
                    "poll_stats symbol=%s fetched=%s accepted=%s enqueued=%s "
                    "dropped_queue_full=%s dropped_duplicate=%s dropped_filter=%s "
                    "queue_size=%s queue_maxsize=%s fetched_last_seq=%s "
                    "queue_in=%s queue_out=%s last_commit_monotonic_age_sec=%s "
                    "db_write_rate=%s ts_drift_sec=%s "
                    "last_seen_seq=%s last_accepted_seq=%s last_persisted_seq=%s",
                    symbol,
                    fetched,
                    accepted,
                    enqueued,
                    dropped_queue_full,
                    dropped_duplicate,
                    dropped_filter,
                    self._collector.queue_size(),
                    self._collector.queue_maxsize(),
                    fetched_last_seq,
                    pipeline["queue_in_rows"],
                    pipeline["queue_out_rows"],
                    f"{last_commit_age_sec:.1f}" if last_commit_age_sec is not None else "none",
                    pipeline["persisted_rows"],
                    f"{drift_sec:.1f}" if drift_sec is not None else "none",
                    self._last_seen_seq.get(symbol),
                    self._last_accepted_seq.get(symbol),
                    self._last_persisted_seq.get(symbol),
                )

                await self._sleep_with_stop(0.05)

            elapsed = self._loop.time() - cycle_start
            await self._sleep_with_stop(max(0.0, self._config.poll_interval_sec - elapsed))

    async def _health_loop(self) -> None:
        while not self._stop_event.is_set():
            await self._sleep_with_stop(HEALTH_LOG_INTERVAL_SEC)
            if self._stop_event.is_set():
                return

            now = self._loop.time()
            queue_size = self._collector.queue_size()
            queue_maxsize = self._collector.queue_maxsize()
            pipeline = self._collector.snapshot_pipeline_counters(reset=True)
            persisted_rows_per_min = pipeline["persisted_rows"]
            ignored_rows_per_min = pipeline["ignored_rows"]
            queue_in_rows_per_min = pipeline["queue_in_rows"]
            queue_out_rows_per_min = pipeline["queue_out_rows"]
            db_commits_per_min = pipeline["db_commits"]
            last_commit_age_sec = self._last_commit_age_sec(now=now)
            drift_sec = self._drift_sec()
            max_ts_utc = self._format_ts_ms_utc(self._max_ts_ms_seen)
            if drift_sec is not None and abs(drift_sec) > self._config.drift_warn_sec:
                logger.warning(
                    "ts_drift_warn drift_sec=%.1f now_utc_ms=%s max_ts_ms=%s max_ts_utc=%s",
                    drift_sec,
                    int(time.time() * 1000),
                    self._max_ts_ms_seen,
                    max_ts_utc,
                )

            parts = []
            for symbol in self._config.symbols:
                last_tick = self._last_tick_seen_at.get(symbol)
                age = None if last_tick is None else round(now - last_tick, 1)
                last_seen = self._last_seen_seq.get(symbol)
                last_accepted = self._last_accepted_seq.get(symbol)
                last_persisted = self._last_persisted_seq.get(symbol)
                parts.append(
                    f"{symbol}:last_seen_seq={last_seen if last_seen is not None else 'none'}"
                    f" last_accepted_seq={last_accepted if last_accepted is not None else 'none'}"
                    f" last_persisted_seq={last_persisted if last_persisted is not None else 'none'}"
                    f" last_tick_age_sec={age if age is not None else 'none'}"
                )

            logger.info(
                "health connected=%s queue=%s/%s push_rows_per_min=%s "
                "poll_fetched=%s poll_accepted=%s poll_enqueued=%s "
                "persisted_rows_per_min=%s ignored_rows_per_min=%s "
                "queue_in=%s queue_out=%s db_commits_per_min=%s db_write_rate=%s "
                "last_commit_monotonic_age_sec=%s ts_drift_sec=%s max_ts_utc=%s "
                "dropped_queue_full=%s dropped_duplicate=%s dropped_filter=%s symbols=%s",
                self._connected,
                queue_size,
                queue_maxsize,
                self._push_rows_since_report,
                self._poll_fetched_since_report,
                self._poll_accepted_since_report,
                self._poll_enqueued_since_report,
                persisted_rows_per_min,
                ignored_rows_per_min,
                queue_in_rows_per_min,
                queue_out_rows_per_min,
                db_commits_per_min,
                persisted_rows_per_min,
                f"{last_commit_age_sec:.1f}" if last_commit_age_sec is not None else "none",
                f"{drift_sec:.1f}" if drift_sec is not None else "none",
                max_ts_utc,
                self._dropped_queue_full_since_report,
                self._dropped_duplicate_since_report,
                self._dropped_filter_since_report,
                " | ".join(parts),
            )

            runtime = self._collector.snapshot_runtime_state()
            busy_backoff_count = int(runtime.get("busy_backoff_count", 0))
            busy_backoff_delta = max(0, busy_backoff_count - self._last_busy_backoff_count)
            self._last_busy_backoff_count = busy_backoff_count
            if (
                self._notifier is not None
                and busy_backoff_delta >= self._config.telegram_sqlite_busy_alert_threshold
            ):
                trading_day = self._current_trading_day()
                self._notifier.submit_alert(
                    AlertEvent(
                        created_at=datetime.now(tz=timezone.utc),
                        code="SQLITE_BUSY",
                        key=f"SQLITE_BUSY:{trading_day}",
                        fingerprint=f"SQLITE_BUSY:{trading_day}",
                        trading_day=trading_day,
                        severity=NotifySeverity.WARN.value,
                        headline="注意：SQLite 鎖競爭升高",
                        impact="目前仍可能持續寫入，但吞吐與延遲有退化風險",
                        summary_lines=[
                            f"busy_backoff_delta={busy_backoff_delta}/min threshold={self._config.telegram_sqlite_busy_alert_threshold}",
                            f"queue={queue_size}/{queue_maxsize} persisted_per_min={persisted_rows_per_min}",
                            f"last_exception_type={runtime.get('last_exception_type')}",
                        ],
                        suggestions=[
                            "journalctl -u hk-tick-collector -n 200 --no-pager",
                            f"sqlite3 {db_path_for_trading_day(self._config.data_root, trading_day)} 'select count(*) from ticks;'",
                        ],
                    )
                )

            if self._notifier is not None:
                snapshot = await self._build_health_snapshot(
                    now=now,
                    queue_size=queue_size,
                    queue_maxsize=queue_maxsize,
                    persisted_rows_per_min=persisted_rows_per_min,
                    drift_sec=drift_sec,
                    push_rows_per_min=self._push_rows_since_report,
                    poll_fetched=self._poll_fetched_since_report,
                    poll_accepted=self._poll_accepted_since_report,
                    dropped_duplicate=self._dropped_duplicate_since_report,
                )
                self._notifier.submit_health(snapshot)

            await self._check_watchdog(
                now=now,
                queue_size=queue_size,
                queue_maxsize=queue_maxsize,
                persisted_rows_per_min=persisted_rows_per_min,
                queue_in_rows_per_min=queue_in_rows_per_min,
                queue_out_rows_per_min=queue_out_rows_per_min,
            )

            self._push_rows_since_report = 0
            self._poll_fetched_since_report = 0
            self._poll_accepted_since_report = 0
            self._poll_enqueued_since_report = 0
            self._poll_seq_advanced_since_report = 0
            self._dropped_queue_full_since_report = 0
            self._dropped_duplicate_since_report = 0
            self._dropped_filter_since_report = 0

    async def _backfill_recent(self) -> None:
        if self._ctx is None:
            return
        for symbol in self._config.symbols:
            ret, data = self._ctx.get_rt_ticker(symbol, num=self._config.backfill_n)
            if ret != RET_OK:
                logger.warning("backfill failed for %s: %s", symbol, data)
                continue
            rows = ticker_df_to_rows(
                data, provider="futu", push_type="backfill", default_symbol=symbol
            )
            self._record_seen_rows(rows, source="backfill")
            if rows:
                accepted, accepted_max = self._handle_rows(rows, source="backfill")
                for accepted_symbol, accepted_last_seq in accepted_max.items():
                    self._update_seq_max(
                        self._last_accepted_seq, accepted_symbol, accepted_last_seq
                    )
                logger.info(
                    "backfill_stats symbol=%s fetched=%s enqueued=%s queue_size=%s queue_maxsize=%s",
                    symbol,
                    len(rows),
                    accepted,
                    self._collector.queue_size(),
                    self._collector.queue_maxsize(),
                )

    def _filter_polled_rows(
        self, symbol: str, rows: Sequence[TickRow]
    ) -> tuple[List[TickRow], int, int]:
        if not rows:
            return [], 0, 0

        baseline_seq = self._dedupe_baseline_seq(symbol)
        seen_seq = set()
        seen_keys = set()
        new_rows: List[TickRow] = []
        recent_keys = self._recent_key_sets.get(symbol, set())
        dropped_duplicate = 0
        dropped_filter = 0

        for row in rows:
            if row.symbol != symbol:
                dropped_filter += 1
                continue

            if row.seq is None:
                key = self._row_key(row)
                if key in recent_keys or key in seen_keys:
                    dropped_duplicate += 1
                    continue
                seen_keys.add(key)
                new_rows.append(row)
                continue

            if row.seq in seen_seq:
                dropped_duplicate += 1
                continue
            if baseline_seq is not None and row.seq <= baseline_seq:
                dropped_duplicate += 1
                continue

            seen_seq.add(row.seq)
            new_rows.append(row)

        return new_rows, dropped_duplicate, dropped_filter

    def _handle_push_rows(self, rows: List[TickRow]) -> None:
        self._record_seen_rows(rows, source="push")
        _, accepted_max = self._handle_rows(rows, source="push")
        for symbol, accepted_last_seq in accepted_max.items():
            self._update_seq_max(self._last_accepted_seq, symbol, accepted_last_seq)

    def _handle_rows(self, rows: List[TickRow], source: str) -> tuple[int, Dict[str, int]]:
        if not rows:
            return 0, {}

        accepted = self._collector.enqueue(rows)
        if not accepted:
            self._dropped_queue_full_since_report += len(rows)
            logger.warning(
                "enqueue_failed source=%s rows=%s queue_size=%s queue_maxsize=%s",
                source,
                len(rows),
                self._collector.queue_size(),
                self._collector.queue_maxsize(),
            )
            return 0, {}

        now = self._loop.time()
        accepted_max_seq: Dict[str, int] = {}
        for row in rows:
            symbol = row.symbol
            if source == "push":
                self._last_push_at[symbol] = now
            if row.seq is not None:
                current = accepted_max_seq.get(symbol)
                if current is None or row.seq > current:
                    accepted_max_seq[symbol] = row.seq
            else:
                self._remember_key(symbol, self._row_key(row))

        if source == "push":
            self._push_rows_since_report += len(rows)

        return len(rows), accepted_max_seq

    def _record_seen_rows(self, rows: Sequence[TickRow], source: str) -> None:
        if not rows:
            return

        now = self._loop.time()
        self._last_upstream_active_at = now

        for row in rows:
            symbol = row.symbol
            self._last_tick_seen_at[symbol] = now
            self._last_ts_ms_by_symbol[symbol] = max(
                self._last_ts_ms_by_symbol.get(symbol, row.ts_ms), row.ts_ms
            )
            self._max_ts_ms_seen = (
                row.ts_ms if self._max_ts_ms_seen is None else max(self._max_ts_ms_seen, row.ts_ms)
            )
            if source == "push":
                self._last_push_at[symbol] = now
            if row.seq is not None:
                self._update_seq_max(self._last_seen_seq, symbol, row.seq)

    def _record_poll_seq_advance(self, symbol: str, fetched_last_seq: Optional[int]) -> None:
        if fetched_last_seq is None:
            return

        prev = self._last_poll_fetched_seq.get(symbol)
        if prev is None or fetched_last_seq > prev:
            self._last_poll_fetched_seq[symbol] = fetched_last_seq
            self._poll_seq_advanced_since_report += 1
            self._last_upstream_active_at = self._loop.time()

    async def _check_watchdog(
        self,
        *,
        now: float,
        queue_size: int,
        queue_maxsize: int,
        persisted_rows_per_min: int,
        queue_in_rows_per_min: int,
        queue_out_rows_per_min: int,
    ) -> None:
        if self._stop_event.is_set():
            return

        queue_growth = queue_size - self._watchdog_last_queue_size
        check_elapsed_sec = max(0.0, now - self._watchdog_last_check_at)
        self._watchdog_last_queue_size = queue_size
        self._watchdog_last_check_at = now

        poll_active = (
            self._poll_fetched_since_report > 0 and self._poll_seq_advanced_since_report > 0
        )
        enqueued_in_window = max(
            queue_in_rows_per_min,
            self._push_rows_since_report + self._poll_enqueued_since_report,
        )
        recent_upstream = (
            self._last_upstream_active_at is not None
            and (now - self._last_upstream_active_at) <= self._config.watchdog_upstream_window_sec
        )
        upstream_active = recent_upstream and (
            enqueued_in_window > 0 or poll_active or queue_out_rows_per_min > 0
        )

        runtime = self._collector.snapshot_runtime_state()
        worker_alive = bool(runtime.get("worker_alive", False))
        last_dequeue = runtime.get("last_dequeue_monotonic")
        last_commit = runtime.get("last_commit_monotonic")
        last_exception = runtime.get("last_exception_monotonic")

        dequeue_age_sec = (
            (now - float(last_dequeue)) if last_dequeue is not None else (now - self._started_at)
        )
        commit_age_sec = (
            (now - float(last_commit)) if last_commit is not None else (now - self._started_at)
        )
        exception_age_sec = (now - float(last_exception)) if last_exception is not None else None
        queue_threshold = max(1, int(self._config.watchdog_queue_threshold_rows))
        backlog_or_enqueue = queue_size >= queue_threshold or enqueued_in_window > 0

        if not backlog_or_enqueue:
            self._watchdog_heal_failures = 0
            self._watchdog_dumped = False
            return

        consumer_dead = not worker_alive
        persist_quiet = persisted_rows_per_min <= 0
        commit_stalled = commit_age_sec >= self._config.watchdog_stall_sec
        persist_stalled = persist_quiet and commit_stalled

        if not (upstream_active or queue_size >= queue_threshold):
            self._watchdog_heal_failures = 0
            self._watchdog_dumped = False
            return
        if not (persist_stalled or consumer_dead):
            self._watchdog_heal_failures = 0
            self._watchdog_dumped = False
            return

        pipeline = self._collector.snapshot_pipeline_counters(reset=False)
        drift_sec = self._drift_sec()
        last_commit_age_sec = self._last_commit_age_sec(now)
        reason = "worker_dead" if consumer_dead else "commit_stalled_with_backlog"

        self._dump_threads_for_watchdog(
            reason=f"{reason}_pre_recovery",
            now=now,
            queue_size=queue_size,
            queue_maxsize=queue_maxsize,
            queue_growth=queue_growth,
            check_elapsed_sec=check_elapsed_sec,
            dequeue_age_sec=dequeue_age_sec,
            commit_age_sec=commit_age_sec,
            runtime=runtime,
        )

        self._watchdog_heal_attempts += 1
        self._watchdog_last_heal_at = now
        recovered = await asyncio.to_thread(
            self._collector.request_writer_recovery,
            f"watchdog_{reason}",
            float(self._config.watchdog_recovery_join_timeout_sec),
        )
        if recovered:
            self._watchdog_heal_failures = 0
            logger.warning(
                "WATCHDOG recovery_triggered reason=%s attempts=%s queue=%s/%s "
                "queue_threshold=%s dequeue_age_sec=%.1f commit_age_sec=%.1f "
                "last_exception_age_sec=%s queue_growth=%s "
                "check_elapsed_sec=%.1f worker_alive=%s",
                reason,
                self._watchdog_heal_attempts,
                queue_size,
                queue_maxsize,
                queue_threshold,
                dequeue_age_sec,
                commit_age_sec,
                f"{exception_age_sec:.1f}" if exception_age_sec is not None else "none",
                queue_growth,
                check_elapsed_sec,
                worker_alive,
            )
            self._watchdog_dumped = False
            return

        self._watchdog_heal_failures += 1
        logger.error(
            "WATCHDOG recovery_failed reason=%s failures=%s max_failures=%s queue=%s/%s "
            "queue_threshold=%s dequeue_age_sec=%.1f commit_age_sec=%.1f "
            "last_exception_type=%s last_exception_count=%s",
            reason,
            self._watchdog_heal_failures,
            self._config.watchdog_recovery_max_failures,
            queue_size,
            queue_maxsize,
            queue_threshold,
            dequeue_age_sec,
            commit_age_sec,
            runtime.get("last_exception_type"),
            runtime.get("last_exception_count"),
        )
        if self._watchdog_heal_failures < self._config.watchdog_recovery_max_failures:
            return

        self._dump_threads_for_watchdog(
            reason=f"{reason}_exit",
            now=now,
            queue_size=queue_size,
            queue_maxsize=queue_maxsize,
            queue_growth=queue_growth,
            check_elapsed_sec=check_elapsed_sec,
            dequeue_age_sec=dequeue_age_sec,
            commit_age_sec=commit_age_sec,
            runtime=runtime,
        )
        logger.error(
            "WATCHDOG persistent_stall reason=%s upstream_active=%s poll_active=%s "
            "queue=%s/%s queue_threshold=%s queue_growth=%s check_elapsed_sec=%.1f "
            "push_rows_per_min=%s poll_fetched=%s poll_accepted=%s poll_enqueued=%s "
            "queue_in=%s queue_out=%s persisted_rows_per_min=%s "
            "dequeue_age_sec=%.1f commit_age_sec=%.1f last_commit_monotonic_age_sec=%s "
            "worker_alive=%s last_exception_type=%s last_exception_count=%s "
            "dropped_queue_full=%s dropped_duplicate=%s dropped_filter=%s "
            "max_seq_lag=%s ts_drift_sec=%s recovery_failures=%s",
            reason,
            upstream_active,
            poll_active,
            queue_size,
            queue_maxsize,
            queue_threshold,
            queue_growth,
            check_elapsed_sec,
            self._push_rows_since_report,
            self._poll_fetched_since_report,
            self._poll_accepted_since_report,
            self._poll_enqueued_since_report,
            pipeline["queue_in_rows"],
            pipeline["queue_out_rows"],
            persisted_rows_per_min,
            dequeue_age_sec,
            commit_age_sec,
            f"{last_commit_age_sec:.1f}" if last_commit_age_sec is not None else "none",
            worker_alive,
            runtime.get("last_exception_type"),
            runtime.get("last_exception_count"),
            self._dropped_queue_full_since_report,
            self._dropped_duplicate_since_report,
            self._dropped_filter_since_report,
            self._max_seq_lag(),
            f"{drift_sec:.1f}" if drift_sec is not None else "none",
            self._watchdog_heal_failures,
        )
        if self._notifier is not None:
            trading_day = self._current_trading_day()
            persisted_parts = []
            for symbol in self._config.symbols:
                persisted_parts.append(f"{symbol}={self._last_persisted_seq.get(symbol, 'none')}")
            self._notifier.submit_alert(
                AlertEvent(
                    created_at=datetime.now(tz=timezone.utc),
                    code="PERSIST_STALL",
                    key=f"PERSIST_STALL:{trading_day}:{','.join(self._config.symbols)}",
                    fingerprint=f"PERSIST_STALL:{trading_day}:{','.join(self._config.symbols)}",
                    trading_day=trading_day,
                    severity=NotifySeverity.ALERT.value,
                    headline="異常：持久化停滯，疑似停止寫入",
                    impact="新資料可能未落庫，延遲與積壓將持續上升",
                    summary_lines=[
                        f"stall_sec={commit_age_sec:.1f}/{self._config.watchdog_stall_sec}",
                        f"queue={queue_size}/{queue_maxsize} max_seq_lag={self._max_seq_lag()} persisted_per_min={persisted_rows_per_min}",
                        f"last_persisted_seq: {' '.join(persisted_parts)}",
                    ],
                    suggestions=[
                        "journalctl -u hk-tick-collector -n 200 --no-pager",
                        f"sqlite3 {db_path_for_trading_day(self._config.data_root, trading_day)} 'select count(*) from ticks;'",
                    ],
                )
            )
        raise SystemExit(WATCHDOG_EXIT_CODE)

    def _dump_threads_for_watchdog(
        self,
        *,
        reason: str,
        now: float,
        queue_size: int,
        queue_maxsize: int,
        queue_growth: int,
        check_elapsed_sec: float,
        dequeue_age_sec: float,
        commit_age_sec: float,
        runtime: Dict[str, object],
    ) -> None:
        if self._watchdog_dumped:
            return
        self._watchdog_dumped = True
        logger.error(
            "WATCHDOG diagnostic_dump reason=%s now=%.1f queue=%s/%s queue_growth=%s "
            "check_elapsed_sec=%.1f dequeue_age_sec=%.1f "
            "commit_age_sec=%.1f runtime=%s",
            reason,
            now,
            queue_size,
            queue_maxsize,
            queue_growth,
            check_elapsed_sec,
            dequeue_age_sec,
            commit_age_sec,
            runtime,
        )
        try:
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        except Exception:
            logger.exception("watchdog_thread_dump_failed")

    def _max_seq_lag(self) -> int:
        max_lag = 0
        symbols = set(self._last_seen_seq.keys()) | set(self._last_persisted_seq.keys())
        for symbol in symbols:
            last_seen = self._last_seen_seq.get(symbol)
            if last_seen is None:
                continue
            last_persisted = self._last_persisted_seq.get(symbol, 0)
            max_lag = max(max_lag, last_seen - last_persisted)
        return max_lag

    def _dedupe_baseline_seq(self, symbol: str) -> Optional[int]:
        return self._last_persisted_seq.get(symbol)

    def _update_seq_max(self, target: Dict[str, int], symbol: str, seq: int) -> None:
        current = target.get(symbol)
        if current is None or seq > current:
            target[symbol] = seq

    def _remember_key(self, symbol: str, key: tuple) -> None:
        queue = self._recent_keys.setdefault(symbol, deque())
        key_set = self._recent_key_sets.setdefault(symbol, set())
        if key in key_set:
            return
        queue.append(key)
        key_set.add(key)
        if len(queue) > POLL_RECENT_KEY_LIMIT:
            old = queue.popleft()
            key_set.discard(old)

    def _row_key(self, row: TickRow) -> tuple:
        return (row.ts_ms, row.price, row.volume, row.turnover)

    def _max_seq(self, rows: Sequence[TickRow]) -> Optional[int]:
        seqs = [row.seq for row in rows if row.seq is not None]
        return max(seqs) if seqs else None

    def _should_skip_poll(self, symbol: str) -> bool:
        now = self._loop.time()
        stale_threshold_sec = max(float(self._config.poll_stale_sec), float(POLL_SKIP_PUSH_SEC))
        last_tick = self._last_tick_seen_at.get(symbol)
        if last_tick is None:
            return False
        if (now - last_tick) < stale_threshold_sec:
            return True

        last_push = self._last_push_at.get(symbol)
        if last_push is None:
            return False
        return (now - last_push) < stale_threshold_sec

    def _last_commit_age_sec(self, now: float) -> float | None:
        last_commit = self._collector.get_last_persist_at()
        if last_commit is None:
            return None
        return max(0.0, now - last_commit)

    def _drift_sec(self) -> float | None:
        if self._max_ts_ms_seen is None:
            return None
        return (int(time.time() * 1000) - self._max_ts_ms_seen) / 1000.0

    def _format_ts_ms_utc(self, ts_ms: int | None) -> str:
        if ts_ms is None:
            return "none"
        return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()

    def _current_trading_day(self) -> str:
        return datetime.now(tz=HK_TZ).strftime("%Y%m%d")

    def _fetch_db_snapshot(self, trading_day: str) -> tuple[int, int | None]:
        if self._store is None:
            return 0, self._max_ts_ms_seen
        return self._store.fetch_tick_stats(trading_day)

    def _collect_system_metrics(self) -> tuple[float | None, float | None, float | None]:
        if not self._config.telegram_include_system_metrics:
            return None, None, None

        load1: float | None = None
        rss_mb: float | None = None
        disk_free_gb: float | None = None
        try:
            load1 = os.getloadavg()[0]
        except (AttributeError, OSError):
            load1 = None

        try:
            usage = resource.getrusage(resource.RUSAGE_SELF)
            if sys.platform == "darwin":
                rss_mb = usage.ru_maxrss / (1024.0 * 1024.0)
            else:
                rss_mb = usage.ru_maxrss / 1024.0
        except Exception:
            rss_mb = None

        try:
            disk_free_gb = shutil.disk_usage(self._config.data_root).free / (1024.0**3)
        except OSError:
            disk_free_gb = None
        return load1, rss_mb, disk_free_gb

    async def _build_health_snapshot(
        self,
        *,
        now: float,
        queue_size: int,
        queue_maxsize: int,
        persisted_rows_per_min: int,
        drift_sec: float | None,
        push_rows_per_min: int,
        poll_fetched: int,
        poll_accepted: int,
        dropped_duplicate: int,
    ) -> HealthSnapshot:
        trading_day = self._current_trading_day()
        try:
            db_rows, db_max_ts = await asyncio.to_thread(self._fetch_db_snapshot, trading_day)
        except Exception:
            logger.exception("health_db_snapshot_failed trading_day=%s", trading_day)
            db_rows, db_max_ts = 0, self._max_ts_ms_seen

        if db_max_ts is not None:
            drift_from_db = (int(time.time() * 1000) - db_max_ts) / 1000.0
        else:
            drift_from_db = drift_sec

        symbols: List[SymbolSnapshot] = []
        for symbol in self._config.symbols:
            last_tick = self._last_tick_seen_at.get(symbol)
            age_sec = None if last_tick is None else max(0.0, now - last_tick)
            seen = self._last_seen_seq.get(symbol)
            persisted = self._last_persisted_seq.get(symbol)
            lag = 0
            if seen is not None:
                lag = max(0, seen - (persisted or 0))
            symbols.append(
                SymbolSnapshot(
                    symbol=symbol,
                    last_tick_age_sec=age_sec,
                    last_persisted_seq=persisted,
                    max_seq_lag=lag,
                )
            )

        load1, rss_mb, disk_free_gb = self._collect_system_metrics()
        return HealthSnapshot(
            created_at=datetime.now(tz=timezone.utc),
            pid=os.getpid(),
            uptime_sec=int(max(0.0, now - self._started_at)),
            trading_day=trading_day,
            db_path=db_path_for_trading_day(self._config.data_root, trading_day),
            db_rows=db_rows,
            db_max_ts_utc=self._format_ts_ms_utc(db_max_ts),
            drift_sec=drift_from_db,
            queue_size=queue_size,
            queue_maxsize=queue_maxsize,
            push_rows_per_min=push_rows_per_min,
            poll_fetched=poll_fetched,
            poll_accepted=poll_accepted,
            persisted_rows_per_min=persisted_rows_per_min,
            dropped_duplicate=dropped_duplicate,
            symbols=symbols,
            system_load1=load1,
            system_rss_mb=rss_mb,
            system_disk_free_gb=disk_free_gb,
        )

    async def _sleep_with_stop(self, delay: float) -> None:
        if delay <= 0:
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            return

    def _close_ctx(self) -> None:
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                logger.exception("failed to close futu context")
            self._ctx = None
