import asyncio
import sqlite3
import time

from hk_tick_collector.collector import AsyncTickCollector
from hk_tick_collector.db import SQLiteTickStore, db_path_for_trading_day
from hk_tick_collector.models import TickRow


def _row(symbol: str, seq: int, trading_day: str = "20240102") -> TickRow:
    ts_ms = 1704161400000 + seq
    return TickRow(
        market="HK",
        symbol=symbol,
        ts_ms=ts_ms,
        price=320.0 + seq / 100.0,
        volume=100 + seq,
        turnover=32000.0 + seq,
        direction="BUY",
        seq=seq,
        tick_type="AUTO_MATCH",
        push_type="push",
        provider="futu",
        trading_day=trading_day,
        recv_ts_ms=ts_ms + 5,
        inserted_at_ms=ts_ms + 5,
    )


def test_smoke_async_collector_persists_rows(tmp_path):
    async def runner():
        store = SQLiteTickStore(tmp_path)
        collector = AsyncTickCollector(
            store,
            batch_size=3,
            max_wait_ms=20,
            max_queue_size=100,
            persist_retry_max_attempts=3,
            persist_retry_backoff_sec=0.01,
            persist_retry_backoff_max_sec=0.05,
            heartbeat_interval_sec=0.1,
        )
        await collector.start()
        collector.enqueue(
            [
                _row("HK.00700", 1),
                _row("HK.00700", 2),
                _row("HK.00981", 1),
                _row("HK.00700", 2),  # duplicate by (symbol, seq), should be ignored
            ]
        )

        deadline = time.monotonic() + 3.0
        while collector.queue_size() > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

        await collector.stop(timeout_sec=3)
        assert collector.fatal_error() is None

        db_path = db_path_for_trading_day(tmp_path, "20240102")
        conn = sqlite3.connect(db_path)
        try:
            total = conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
            latest = conn.execute(
                "SELECT symbol, MAX(seq) FROM ticks GROUP BY symbol ORDER BY symbol"
            ).fetchall()
        finally:
            conn.close()

        assert total == 3
        assert latest == [("HK.00700", 2), ("HK.00981", 1)]

    asyncio.run(runner())
