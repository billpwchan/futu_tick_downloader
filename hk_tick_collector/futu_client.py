from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Deque, Dict, List, Optional, Sequence

from futu import OpenQuoteContext, RET_OK, Session, SubType, TickerHandlerBase

from .collector import AsyncTickCollector
from .config import Config
from .db import PersistResult
from .mapping import ticker_df_to_rows
from .models import TickRow
from .utils import ExponentialBackoff

logger = logging.getLogger(__name__)

POLL_SKIP_PUSH_SEC = 2
POLL_RECENT_KEY_LIMIT = 500
HEALTH_LOG_INTERVAL_SEC = 60
WATCHDOG_EXIT_CODE = 2


class FutuTickerHandler(TickerHandlerBase):
    def __init__(self, on_rows: Callable[[List[TickRow]], None], loop: asyncio.AbstractEventLoop) -> None:
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
    ) -> None:
        self._config = config
        self._collector = collector
        self._loop = loop
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

    async def run_forever(self) -> None:
        backoff = ExponentialBackoff(self._config.reconnect_min_delay, self._config.reconnect_max_delay)
        try:
            while not self._stop_event.is_set():
                poll_task: asyncio.Task | None = None
                health_task: asyncio.Task | None = None
                try:
                    await self._connect_and_subscribe()
                    backoff.reset()
                    poll_task = asyncio.create_task(self._poll_loop())
                    health_task = asyncio.create_task(self._health_loop())
                    await self._monitor_connection()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("futu connection error: %s", exc)
                finally:
                    self._connected = False
                    for task in (poll_task, health_task):
                        if task is not None:
                            task.cancel()
                    for task in (poll_task, health_task):
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

        logger.info("futu_connecting host=%s port=%s", self._config.futu_host, self._config.futu_port)
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
        logger.info("futu_connected host=%s port=%s", self._config.futu_host, self._config.futu_port)

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
                    self._update_seq_max(self._last_accepted_seq, accepted_symbol, accepted_last_seq)

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

            self._check_watchdog(
                now=now,
                queue_size=queue_size,
                queue_maxsize=queue_maxsize,
                persisted_rows_per_min=persisted_rows_per_min,
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
            rows = ticker_df_to_rows(data, provider="futu", push_type="backfill", default_symbol=symbol)
            self._record_seen_rows(rows, source="backfill")
            if rows:
                accepted, accepted_max = self._handle_rows(rows, source="backfill")
                for accepted_symbol, accepted_last_seq in accepted_max.items():
                    self._update_seq_max(self._last_accepted_seq, accepted_symbol, accepted_last_seq)
                logger.info(
                    "backfill_stats symbol=%s fetched=%s enqueued=%s queue_size=%s queue_maxsize=%s",
                    symbol,
                    len(rows),
                    accepted,
                    self._collector.queue_size(),
                    self._collector.queue_maxsize(),
                )

    def _filter_polled_rows(self, symbol: str, rows: Sequence[TickRow]) -> tuple[List[TickRow], int, int]:
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
            self._last_ts_ms_by_symbol[symbol] = max(self._last_ts_ms_by_symbol.get(symbol, row.ts_ms), row.ts_ms)
            self._max_ts_ms_seen = row.ts_ms if self._max_ts_ms_seen is None else max(self._max_ts_ms_seen, row.ts_ms)
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

    def _check_watchdog(
        self,
        *,
        now: float,
        queue_size: int,
        queue_maxsize: int,
        persisted_rows_per_min: int,
    ) -> None:
        if self._stop_event.is_set():
            return

        poll_active = self._poll_fetched_since_report > 0 and self._poll_seq_advanced_since_report > 0
        recent_upstream = (
            self._last_upstream_active_at is not None
            and (now - self._last_upstream_active_at) <= self._config.watchdog_upstream_window_sec
        )
        upstream_active = recent_upstream and (self._push_rows_since_report > 0 or poll_active)
        last_persist_at = self._collector.get_last_persist_at()
        if last_persist_at is None:
            last_persist_at = self._started_at
        persist_stall_sec = now - last_persist_at

        if not upstream_active:
            return
        if persisted_rows_per_min > 0:
            return
        if persist_stall_sec < self._config.watchdog_stall_sec:
            return

        pipeline = self._collector.snapshot_pipeline_counters(reset=False)
        drift_sec = self._drift_sec()
        last_commit_age_sec = self._last_commit_age_sec(now)
        logger.error(
            "WATCHDOG persistent_stall upstream_active=%s poll_active=%s "
            "persist_stall_sec=%.1f queue=%s/%s push_rows_per_min=%s "
            "poll_fetched=%s poll_accepted=%s poll_enqueued=%s "
            "queue_in=%s queue_out=%s last_commit_monotonic_age_sec=%s db_write_rate=%s "
            "dropped_queue_full=%s dropped_duplicate=%s dropped_filter=%s "
            "max_seq_lag=%s ts_drift_sec=%s",
            upstream_active,
            poll_active,
            persist_stall_sec,
            queue_size,
            queue_maxsize,
            self._push_rows_since_report,
            self._poll_fetched_since_report,
            self._poll_accepted_since_report,
            self._poll_enqueued_since_report,
            pipeline["queue_in_rows"],
            pipeline["queue_out_rows"],
            f"{last_commit_age_sec:.1f}" if last_commit_age_sec is not None else "none",
            pipeline["persisted_rows"],
            self._dropped_queue_full_since_report,
            self._dropped_duplicate_since_report,
            self._dropped_filter_since_report,
            self._max_seq_lag(),
            f"{drift_sec:.1f}" if drift_sec is not None else "none",
        )
        os._exit(WATCHDOG_EXIT_CODE)

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
        accepted = self._last_accepted_seq.get(symbol)
        persisted = self._last_persisted_seq.get(symbol)
        if accepted is None:
            return persisted
        if persisted is None:
            return accepted
        return max(accepted, persisted)

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
        last_push = self._last_push_at.get(symbol)
        if last_push is None:
            return False
        return (self._loop.time() - last_push) < POLL_SKIP_PUSH_SEC

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
