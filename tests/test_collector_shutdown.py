import asyncio

from hk_tick_collector.collector import AsyncTickCollector
from hk_tick_collector.models import TickRow


class FakeStore:
    def __init__(self) -> None:
        self.inserted = []

    def insert_ticks(self, trading_day, rows):
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

        row = TickRow(
            market="HK",
            symbol="HK.00700",
            ts_ms=1704161400000,
            price=10.0,
            volume=100,
            turnover=1000.0,
            direction="BUY",
            seq=1,
            tick_type="AUTO_MATCH",
            push_type="push",
            provider="futu",
            trading_day="20240102",
            inserted_at_ms=1704161400000,
        )
        collector.enqueue([row])

        await collector.stop()

        assert store.inserted
        assert store.inserted[0][1] == [row]

    asyncio.run(runner())
