import asyncio
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
        self._pipeline = {
            "persisted_rows": 0,
            "ignored_rows": 0,
            "queue_in_rows": 0,
            "queue_out_rows": 0,
            "db_commits": 0,
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
        watchdog_stall_sec=180,
        watchdog_upstream_window_sec=60,
        drift_warn_sec=120,
        stop_flush_timeout_sec=60,
        persist_retry_max_attempts=5,
        persist_retry_backoff_sec=1.0,
        sqlite_busy_timeout_ms=5000,
        sqlite_journal_mode="WAL",
        sqlite_synchronous="NORMAL",
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
        filtered, dropped_duplicate, dropped_filter = client._filter_polled_rows("HK.00700", [row_old, row_new])

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


def test_watchdog_exits_on_upstream_active_and_persist_stalled(monkeypatch):
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
        client._poll_accepted_since_report = 10
        client._poll_enqueued_since_report = 0
        client._dropped_queue_full_since_report = 10
        collector._last_persist_at = loop.time() - 5
        client._last_upstream_active_at = loop.time()

        class ExitTriggered(Exception):
            pass

        def fake_exit(code: int):
            raise ExitTriggered(code)

        monkeypatch.setattr("hk_tick_collector.futu_client.os._exit", fake_exit)

        with pytest.raises(ExitTriggered):
            client._check_watchdog(
                now=loop.time(),
                queue_size=10,
                queue_maxsize=10,
                persisted_rows_per_min=0,
            )

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
