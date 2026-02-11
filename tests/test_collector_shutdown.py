import asyncio

from hk_tick_collector.collector import AsyncTickCollector
from hk_tick_collector.models import TickRow


def _row(seq: int = 1) -> TickRow:
    return TickRow(
        market="HK",
        symbol="HK.00700",
        ts_ms=1704161400000 + seq,
        price=10.0,
        volume=100,
        turnover=1000.0,
        direction="BUY",
        seq=seq,
        tick_type="AUTO_MATCH",
        push_type="push",
        provider="futu",
        trading_day="20240102",
        inserted_at_ms=1704161400000,
    )


class FakeStore:
    def __init__(self) -> None:
        self.inserted = []

    def open_writer(self):
        return self

    def close(self):
        return None

    def reset_connection(self, trading_day):
        return None

    def insert_ticks(self, trading_day, rows):
        rows_list = list(rows)
        self.inserted.append((trading_day, rows_list))
        return len(rows_list)


class FlakyStore:
    def __init__(self) -> None:
        self.calls = 0
        self.inserted = []

    def open_writer(self):
        return self

    def close(self):
        return None

    def reset_connection(self, trading_day):
        return None

    def insert_ticks(self, trading_day, rows):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient sqlite busy")
        rows_list = list(rows)
        self.inserted.append((trading_day, rows_list))
        return len(rows_list)


class RecoveringStore:
    def __init__(self) -> None:
        self.calls = 0
        self.inserted = []

    def open_writer(self):
        return self

    def close(self):
        return None

    def reset_connection(self, trading_day):
        return None

    def insert_ticks(self, trading_day, rows):
        self.calls += 1
        if self.calls <= 3:
            raise RuntimeError("db write failed")
        rows_list = list(rows)
        self.inserted.append((trading_day, rows_list))
        return len(rows_list)


def test_collector_flushes_on_stop():
    async def runner():
        store = FakeStore()
        collector = AsyncTickCollector(
            store,
            batch_size=10,
            max_wait_ms=1000,
            max_queue_size=10,
        )
        await collector.start()
        collector.enqueue([_row(1)])
        await collector.stop(timeout_sec=2)
        assert store.inserted
        assert store.inserted[0][1] == [_row(1)]

    asyncio.run(runner())


def test_collector_retries_transient_persist_failure_and_recovers():
    async def runner():
        store = FlakyStore()
        collector = AsyncTickCollector(
            store,
            batch_size=1,
            max_wait_ms=10,
            max_queue_size=10,
            persist_retry_max_attempts=3,
            persist_retry_backoff_sec=0.01,
        )
        await collector.start()
        collector.enqueue([_row(2)])
        await asyncio.sleep(0.2)
        await collector.stop(timeout_sec=2)

        assert store.calls >= 2
        assert store.inserted
        assert collector.fatal_error() is None

    asyncio.run(runner())


def test_collector_sets_fatal_on_persist_loop_failure():
    async def runner():
        store = RecoveringStore()
        collector = AsyncTickCollector(
            store,
            batch_size=1,
            max_wait_ms=10,
            max_queue_size=10,
            persist_retry_max_attempts=0,
            persist_retry_backoff_sec=0.01,
        )
        await collector.start()
        collector.enqueue([_row(3)])

        await asyncio.sleep(0.4)
        await collector.stop(timeout_sec=1, cancel_on_timeout=True)
        assert store.calls >= 4
        assert store.inserted
        assert collector.fatal_error() is None

    asyncio.run(runner())
