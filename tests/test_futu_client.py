import asyncio
from pathlib import Path

import pytest

futu = pytest.importorskip("futu")
RET_OK = futu.RET_OK

from hk_tick_collector.config import Config
from hk_tick_collector.futu_client import FutuQuoteClient
from hk_tick_collector.models import TickRow


class DummyCollector:
    def __init__(self, accept: bool = True) -> None:
        self.enqueued = []
        self.accept = accept

    def enqueue(self, rows) -> bool:
        if not self.accept:
            return False
        self.enqueued.append(rows)
        return True


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


def test_push_handler_updates_last_seq():
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
        assert client._last_seq["HK.00700"] == 7

    asyncio.run(runner())


def test_poll_dedup_only_new_seq():
    async def runner():
        loop = asyncio.get_running_loop()
        collector = DummyCollector()
        client = FutuQuoteClient(
            build_config(),
            collector,
            loop,
            initial_last_seq={"HK.00700": 10},
        )
        row_old = make_row(9)
        row_new = make_row(11, ts_ms=1704161402000)
        row_dupe = make_row(11, ts_ms=1704161403000)
        row_no_seq = make_row(None, ts_ms=1704161404000, price=11.0)
        client._handle_push_rows([row_no_seq])

        filtered = client._filter_polled_rows("HK.00700", [row_old, row_new, row_dupe, row_no_seq])

        assert [row.seq for row in filtered] == [11]
        accepted_count, accepted_max = client._handle_rows(filtered, source="poll")
        accepted_last_seq = accepted_max.get("HK.00700")
        if accepted_last_seq is not None:
            client._last_seq["HK.00700"] = accepted_last_seq
        assert client._last_seq["HK.00700"] == 11

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


def test_last_seq_not_updated_when_enqueue_fails():
    async def runner():
        loop = asyncio.get_running_loop()
        collector = DummyCollector(accept=False)
        client = FutuQuoteClient(
            build_config(),
            collector,
            loop,
            initial_last_seq={"HK.00700": 5},
        )
        row = make_row(6)
        client._handle_push_rows([row])

        assert client._last_seq["HK.00700"] == 5
        assert collector.enqueued == []

    asyncio.run(runner())
