import asyncio
import logging
from pathlib import Path
import sys
import types

import pytest

if "futu" not in sys.modules:

    class _TickerHandlerBase:
        def on_recv_rsp(self, rsp_pb):
            return 0, rsp_pb

    fake_futu = types.ModuleType("futu")
    fake_futu.RET_OK = 0
    fake_futu.Session = types.SimpleNamespace(ALL="ALL")
    fake_futu.SubType = types.SimpleNamespace(TICKER="TICKER")
    fake_futu.OpenQuoteContext = object
    fake_futu.TickerHandlerBase = _TickerHandlerBase
    sys.modules["futu"] = fake_futu

RET_OK = 0

from hk_tick_collector.config import Config
from hk_tick_collector.futu_client import FutuQuoteClient
from hk_tick_collector.models import TickRow


class DummyCollector:
    def __init__(self, accept: bool = True, queue_maxsize: int = 10) -> None:
        self.enqueued = []
        self.accept = accept
        self._queue_maxsize = queue_maxsize
        self._last_persist_at = None
        self.recovery_calls = 0
        self.recovery_result = True
        self._pipeline = {
            "persisted_rows": 0,
            "ignored_rows": 0,
            "queue_in_rows": 0,
            "queue_out_rows": 0,
            "db_commits": 0,
        }
        self._runtime = {
            "worker_alive": True,
            "last_progress_at": 0.0,
            "last_drain_at": 0.0,
            "last_commit_at": None,
            "last_dequeue_monotonic": None,
            "last_commit_monotonic": None,
            "last_commit_rows": 0,
            "last_exception_type": "none",
            "last_exception_count": 0,
            "last_exception_at": None,
            "last_exception_monotonic": None,
            "last_recovery_monotonic": None,
            "recovery_count": 0,
            "busy_locked_count": 0,
            "busy_backoff_count": 0,
            "last_backoff_sec": 0.0,
            "total_rows_dequeued": 0,
            "total_rows_committed": 0,
            "total_commits": 0,
        }

    def enqueue(self, rows) -> bool:
        if not self.accept:
            return False
        self.enqueued.append(rows)
        self._pipeline["queue_in_rows"] += len(rows)
        return True

    def queue_size(self) -> int:
        return len(self.enqueued)

    def queue_maxsize(self) -> int:
        return self._queue_maxsize

    def get_last_persist_at(self):
        return self._last_persist_at

    def snapshot_pipeline_counters(self, reset: bool = False):
        data = dict(self._pipeline)
        if reset:
            for key in self._pipeline:
                self._pipeline[key] = 0
        return data

    def snapshot_runtime_state(self):
        return dict(self._runtime)

    def request_writer_recovery(self, reason: str, join_timeout_sec: float = 3.0) -> bool:
        self.recovery_calls += 1
        return self.recovery_result


def build_config(**overrides) -> Config:
    data = dict(
        futu_host="127.0.0.1",
        futu_port=11111,
        symbols=["HK.00700"],
        data_root=Path("/tmp"),
        batch_size=1,
        max_wait_ms=100,
        max_queue_size=10,
        backfill_n=0,
        reconnect_min_delay=1,
        reconnect_max_delay=1,
        check_interval_sec=0.01,
        poll_enabled=False,
        poll_interval_sec=3,
        poll_num=100,
        poll_stale_sec=10,
        watchdog_stall_sec=180,
        watchdog_upstream_window_sec=60,
        drift_warn_sec=120,
        stop_flush_timeout_sec=60,
        seed_recent_db_days=3,
        persist_retry_max_attempts=5,
        persist_retry_backoff_sec=1.0,
        persist_retry_backoff_max_sec=2.0,
        persist_heartbeat_interval_sec=30.0,
        watchdog_queue_threshold_rows=1,
        watchdog_recovery_max_failures=3,
        watchdog_recovery_join_timeout_sec=0.1,
        sqlite_busy_timeout_ms=5000,
        sqlite_journal_mode="WAL",
        sqlite_synchronous="NORMAL",
        sqlite_wal_autocheckpoint=1000,
        telegram_enabled=False,
        telegram_bot_token="",
        telegram_chat_id="",
        telegram_thread_id=None,
        telegram_thread_health_id=None,
        telegram_thread_ops_id=None,
        telegram_mode_default="product",
        telegram_parse_mode="HTML",
        telegram_health_interval_sec=900,
        telegram_health_trading_interval_sec=900,
        telegram_health_offhours_interval_sec=1800,
        telegram_health_lunch_once=True,
        telegram_health_after_close_once=True,
        telegram_health_holiday_mode="daily",
        telegram_alert_cooldown_sec=600,
        telegram_alert_escalation_steps=[0, 600, 1800],
        telegram_rate_limit_per_min=18,
        telegram_include_system_metrics=True,
        telegram_digest_queue_change_pct=20.0,
        telegram_digest_last_tick_age_threshold_sec=60,
        telegram_digest_drift_threshold_sec=60,
        telegram_digest_send_alive_when_idle=False,
        telegram_sqlite_busy_alert_threshold=3,
        instance_id="",
        log_level="INFO",
    )
    data.update(overrides)
    return Config(**data)


def make_row(seq, ts_ms=1704161400000, price=10.0, volume=100, turnover=1000.0) -> TickRow:
    return TickRow(
        market="HK",
        symbol="HK.00700",
        ts_ms=ts_ms,
        price=price,
        volume=volume,
        turnover=turnover,
        direction="BUY",
        seq=seq,
        tick_type="AUTO_MATCH",
        push_type="push",
        provider="futu",
        trading_day="20240102",
        recv_ts_ms=1704161400000,
        inserted_at_ms=1704161400000,
    )


def test_push_handler_updates_seen_and_accepted_seq():
    async def runner():
        loop = asyncio.get_running_loop()
        collector = DummyCollector()
        client = FutuQuoteClient(
            build_config(),
            collector,
            loop,
            initial_last_seq={"HK.00700": 5},
        )
        row1 = make_row(6)
        row2 = make_row(7, ts_ms=1704161401000)
        client._handle_push_rows([row1, row2])

        assert collector.enqueued == [[row1, row2]]
        assert client._last_seen_seq["HK.00700"] == 7
        assert client._last_accepted_seq["HK.00700"] == 7

    asyncio.run(runner())


def test_poll_dedup_uses_accepted_not_seen_when_push_enqueue_fails():
    async def runner():
        loop = asyncio.get_running_loop()
        collector = DummyCollector()
        client = FutuQuoteClient(
            build_config(),
            collector,
            loop,
            initial_last_seq={"HK.00700": 10},
        )

        collector.accept = False
        client._handle_push_rows([make_row(20)])
        assert client._last_seen_seq["HK.00700"] == 20
        assert client._last_accepted_seq["HK.00700"] == 10

        collector.accept = True
        row_old = make_row(10)
        row_new = make_row(11, ts_ms=1704161402000)
        filtered, dropped_duplicate, dropped_filter = client._filter_polled_rows(
            "HK.00700", [row_old, row_new]
        )

        assert [row.seq for row in filtered] == [11]
        assert dropped_duplicate == 1
        assert dropped_filter == 0

        enqueued, accepted_max = client._handle_rows(filtered, source="poll")
        for symbol, seq in accepted_max.items():
            client._update_seq_max(client._last_accepted_seq, symbol, seq)

        assert enqueued == 1
        assert client._last_accepted_seq["HK.00700"] == 11

    asyncio.run(runner())


def test_enqueue_failure_does_not_advance_accepted_or_persisted_seq():
    async def runner():
        loop = asyncio.get_running_loop()
        collector = DummyCollector(accept=False)
        client = FutuQuoteClient(
            build_config(),
            collector,
            loop,
            initial_last_seq={"HK.00700": 5},
        )
        client._handle_push_rows([make_row(6)])

        assert client._last_seen_seq["HK.00700"] == 6
        assert client._last_accepted_seq["HK.00700"] == 5
        assert client._last_persisted_seq["HK.00700"] == 5
        assert collector.enqueued == []

    asyncio.run(runner())


def test_poll_dedupe_baseline_uses_last_persisted_seq_only():
    async def runner():
        loop = asyncio.get_running_loop()
        collector = DummyCollector()
        client = FutuQuoteClient(
            build_config(),
            collector,
            loop,
            initial_last_seq={"HK.00700": 10},
        )
        client._last_accepted_seq["HK.00700"] = 20
        client._last_persisted_seq["HK.00700"] = 10

        rows = [
            make_row(9),
            make_row(11, ts_ms=1704161401000),
            make_row(20, ts_ms=1704161402000),
        ]
        filtered, dropped_duplicate, dropped_filter = client._filter_polled_rows("HK.00700", rows)
        assert [row.seq for row in filtered] == [11, 20]
        assert dropped_duplicate == 1
        assert dropped_filter == 0

    asyncio.run(runner())


def test_watchdog_recovers_before_exit():
    async def runner():
        loop = asyncio.get_running_loop()
        collector = DummyCollector()
        client = FutuQuoteClient(
            build_config(watchdog_stall_sec=1),
            collector,
            loop,
            initial_last_seq={"HK.00700": 5},
        )

        client._push_rows_since_report = 5
        client._poll_fetched_since_report = 10
        client._poll_seq_advanced_since_report = 10
        client._poll_accepted_since_report = 10
        client._poll_enqueued_since_report = 0
        client._dropped_queue_full_since_report = 10
        collector._runtime["last_dequeue_monotonic"] = loop.time() - 5
        collector._runtime["last_commit_monotonic"] = loop.time() - 5
        collector._runtime["worker_alive"] = False
        client._last_upstream_active_at = loop.time()
        collector.recovery_result = True

        await client._check_watchdog(
            now=loop.time(),
            queue_size=10,
            queue_maxsize=10,
            persisted_rows_per_min=0,
            queue_in_rows_per_min=10,
            queue_out_rows_per_min=0,
        )
        assert collector.recovery_calls == 1

    asyncio.run(runner())


def test_watchdog_exits_after_recovery_failures():
    async def runner():
        loop = asyncio.get_running_loop()
        collector = DummyCollector()
        client = FutuQuoteClient(
            build_config(watchdog_stall_sec=1, watchdog_recovery_max_failures=2),
            collector,
            loop,
            initial_last_seq={"HK.00700": 5},
        )

        client._push_rows_since_report = 10
        client._poll_fetched_since_report = 10
        client._poll_seq_advanced_since_report = 10
        client._last_upstream_active_at = loop.time()
        collector._runtime["last_dequeue_monotonic"] = loop.time() - 10
        collector._runtime["last_commit_monotonic"] = loop.time() - 10
        collector._runtime["worker_alive"] = True
        collector.recovery_result = False
        await client._check_watchdog(
            now=loop.time(),
            queue_size=12,
            queue_maxsize=100,
            persisted_rows_per_min=0,
            queue_in_rows_per_min=200,
            queue_out_rows_per_min=0,
        )
        assert collector.recovery_calls == 1

        with pytest.raises(SystemExit) as exc:
            await client._check_watchdog(
                now=loop.time() + 1.1,
                queue_size=12,
                queue_maxsize=100,
                persisted_rows_per_min=0,
                queue_in_rows_per_min=200,
                queue_out_rows_per_min=0,
            )
        assert exc.value.code == 1

    asyncio.run(runner())


def test_watchdog_does_not_recover_when_consumer_is_draining():
    async def runner():
        loop = asyncio.get_running_loop()
        collector = DummyCollector()
        client = FutuQuoteClient(
            build_config(watchdog_stall_sec=1),
            collector,
            loop,
            initial_last_seq={"HK.00700": 5},
        )

        client._push_rows_since_report = 10
        client._poll_fetched_since_report = 10
        client._poll_seq_advanced_since_report = 10
        client._last_upstream_active_at = loop.time()
        collector._runtime["last_dequeue_monotonic"] = loop.time()
        collector._runtime["last_commit_monotonic"] = loop.time()
        collector._runtime["worker_alive"] = True

        await client._check_watchdog(
            now=loop.time(),
            queue_size=12,
            queue_maxsize=100,
            persisted_rows_per_min=0,
            queue_in_rows_per_min=200,
            queue_out_rows_per_min=180,
        )
        assert collector.recovery_calls == 0

    asyncio.run(runner())


def test_watchdog_ignores_duplicate_only_window_without_backlog():
    async def runner():
        loop = asyncio.get_running_loop()
        collector = DummyCollector()
        client = FutuQuoteClient(
            build_config(watchdog_stall_sec=1),
            collector,
            loop,
            initial_last_seq={"HK.00700": 5},
        )

        client._poll_fetched_since_report = 200
        client._poll_seq_advanced_since_report = 100
        client._poll_accepted_since_report = 0
        client._poll_enqueued_since_report = 0
        client._dropped_duplicate_since_report = 200
        client._last_upstream_active_at = loop.time()
        collector._runtime["last_dequeue_monotonic"] = loop.time() - 60
        collector._runtime["last_commit_monotonic"] = loop.time() - 60
        collector._runtime["worker_alive"] = True

        await client._check_watchdog(
            now=loop.time(),
            queue_size=0,
            queue_maxsize=100,
            persisted_rows_per_min=0,
            queue_in_rows_per_min=0,
            queue_out_rows_per_min=0,
        )
        assert collector.recovery_calls == 0

    asyncio.run(runner())


def test_watchdog_honors_queue_threshold():
    async def runner():
        loop = asyncio.get_running_loop()
        collector = DummyCollector()
        client = FutuQuoteClient(
            build_config(watchdog_stall_sec=1, watchdog_queue_threshold_rows=20),
            collector,
            loop,
            initial_last_seq={"HK.00700": 5},
        )

        client._push_rows_since_report = 0
        client._last_upstream_active_at = loop.time()
        collector._runtime["last_dequeue_monotonic"] = loop.time() - 5
        collector._runtime["last_commit_monotonic"] = loop.time() - 5
        collector._runtime["worker_alive"] = False

        await client._check_watchdog(
            now=loop.time(),
            queue_size=5,
            queue_maxsize=100,
            persisted_rows_per_min=0,
            queue_in_rows_per_min=0,
            queue_out_rows_per_min=0,
        )
        assert collector.recovery_calls == 0

    asyncio.run(runner())


def test_watchdog_triggers_when_enqueued_window_positive_even_below_threshold():
    async def runner():
        loop = asyncio.get_running_loop()
        collector = DummyCollector()
        client = FutuQuoteClient(
            build_config(watchdog_stall_sec=1, watchdog_queue_threshold_rows=20),
            collector,
            loop,
            initial_last_seq={"HK.00700": 5},
        )

        client._push_rows_since_report = 10
        client._last_upstream_active_at = loop.time()
        collector._runtime["last_dequeue_monotonic"] = loop.time() - 5
        collector._runtime["last_commit_monotonic"] = loop.time() - 5
        collector._runtime["worker_alive"] = False

        await client._check_watchdog(
            now=loop.time(),
            queue_size=5,
            queue_maxsize=100,
            persisted_rows_per_min=0,
            queue_in_rows_per_min=10,
            queue_out_rows_per_min=0,
        )
        assert collector.recovery_calls == 1

    asyncio.run(runner())


def test_watchdog_uses_fake_monotonic_time_for_commit_stall_detection():
    async def runner():
        loop = asyncio.get_running_loop()
        collector = DummyCollector()
        client = FutuQuoteClient(
            build_config(watchdog_stall_sec=30, watchdog_queue_threshold_rows=5),
            collector,
            loop,
            initial_last_seq={"HK.00700": 5},
        )

        fake_now = 10_000.0
        client._last_upstream_active_at = fake_now
        collector._runtime["last_dequeue_monotonic"] = fake_now - 80
        collector._runtime["last_commit_monotonic"] = fake_now - 80
        collector._runtime["worker_alive"] = True
        collector.recovery_result = True

        await client._check_watchdog(
            now=fake_now,
            queue_size=10,
            queue_maxsize=100,
            persisted_rows_per_min=0,
            queue_in_rows_per_min=200,
            queue_out_rows_per_min=0,
        )
        assert collector.recovery_calls == 1

    asyncio.run(runner())


def test_reconnect_triggers_resubscribe_and_close():
    async def runner():
        loop = asyncio.get_running_loop()
        collector = DummyCollector()
        calls = {"subscribe": 0}
        contexts = []

        class FakeContext:
            def __init__(self, host, port):
                self.host = host
                self.port = port
                self.closed = False
                self._remaining_checks = 1

            def set_handler(self, handler):
                self.handler = handler

            def subscribe(self, symbols, subtypes, subscribe_push=True, session=None):
                calls["subscribe"] += 1
                return RET_OK, "ok"

            def get_global_state(self):
                if self._remaining_checks <= 0:
                    return 1, "disconnected"
                self._remaining_checks -= 1
                return RET_OK, "ok"

            def close(self):
                self.closed = True

        def factory(host, port):
            ctx = FakeContext(host, port)
            contexts.append(ctx)
            return ctx

        client = FutuQuoteClient(
            build_config(reconnect_min_delay=1, reconnect_max_delay=1),
            collector,
            loop,
            context_factory=factory,
        )
        task = asyncio.create_task(client.run_forever())

        await asyncio.sleep(1.3)
        await client.stop()
        await asyncio.wait_for(task, timeout=2)

        assert calls["subscribe"] >= 2
        assert any(ctx.closed for ctx in contexts)

    asyncio.run(runner())


def test_health_log_info_is_compact_and_debug_has_rollup(caplog):
    async def runner() -> None:
        caplog.set_level(logging.DEBUG)
        loop = asyncio.get_running_loop()
        collector = DummyCollector()
        collector._pipeline = {
            "persisted_rows": 1200,
            "ignored_rows": 10,
            "queue_in_rows": 1400,
            "queue_out_rows": 1300,
            "db_commits": 22,
        }
        client = FutuQuoteClient(
            build_config(),
            collector,
            loop,
            initial_last_seq={"HK.00700": 5},
        )

        sleep_calls = {"count": 0}

        async def fake_sleep(_: float) -> None:
            sleep_calls["count"] += 1
            if sleep_calls["count"] >= 2:
                client._stop_event.set()

        client._sleep_with_stop = fake_sleep  # type: ignore[assignment]
        await client._health_loop()

    asyncio.run(runner())
    health_lines = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("health ")
    ]
    assert health_lines
    assert "symbols=1" in health_lines[0]
    assert "HK.00700:last_seen_seq=" not in health_lines[0]
    assert any("health_symbols_rollup" in record.getMessage() for record in caplog.records)
