import asyncio
import sqlite3
import time

from hk_tick_collector.collector import AsyncTickCollector
from hk_tick_collector.db import SQLiteTickStore, SQLiteTickWriter, db_path_for_trading_day
from hk_tick_collector.models import TickRow


def _row(seq: int, trading_day: str = "20240102") -> TickRow:
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
        trading_day=trading_day,
        inserted_at_ms=1704161400000,
    )


class SlowWriter:
    def __init__(self, writer: SQLiteTickWriter, delay_sec: float) -> None:
        self._writer = writer
        self._delay_sec = delay_sec

    def insert_ticks(self, trading_day, rows):
        time.sleep(self._delay_sec)
        return self._writer.insert_ticks(trading_day, rows)

    def reset_connection(self, trading_day):
        self._writer.reset_connection(trading_day)

    def close(self):
        self._writer.close()


class SlowSQLiteStore:
    def __init__(self, base: SQLiteTickStore, delay_sec: float) -> None:
        self._base = base
        self._delay_sec = delay_sec
        self._data_root = base._data_root

    def open_writer(self) -> SlowWriter:
        return SlowWriter(self._base.open_writer(), self._delay_sec)


def test_fake_producer_with_slow_sqlite_drains_without_stall(tmp_path):
    async def runner():
        store = SlowSQLiteStore(
            SQLiteTickStore(
                tmp_path,
                busy_timeout_ms=5000,
                journal_mode="WAL",
                synchronous="NORMAL",
                wal_autocheckpoint=1000,
            ),
            delay_sec=0.01,
        )
        collector = AsyncTickCollector(
            store,
            batch_size=100,
            max_wait_ms=50,
            max_queue_size=20000,
            persist_retry_max_attempts=0,
            persist_retry_backoff_sec=0.01,
            persist_retry_backoff_max_sec=0.1,
            heartbeat_interval_sec=0.2,
        )
        await collector.start()

        max_queue_seen = 0
        seq = 1
        produce_start = time.monotonic()
        while time.monotonic() - produce_start < 2.0:
            batch = [_row(seq + i) for i in range(20)]
            seq += 20
            assert collector.enqueue(batch)
            max_queue_seen = max(max_queue_seen, collector.queue_size())
            await asyncio.sleep(0.003)

        wait_deadline = time.monotonic() + 15.0
        while collector.queue_size() > 0 and time.monotonic() < wait_deadline:
            await asyncio.sleep(0.05)

        runtime = collector.snapshot_runtime_state()
        assert collector.queue_size() == 0
        assert runtime["worker_alive"] is True
        assert int(runtime["total_rows_dequeued"]) >= (seq - 1)
        assert collector.fatal_error() is None
        assert max_queue_seen > 0

        await collector.stop(timeout_sec=8)
        assert collector.fatal_error() is None

    asyncio.run(runner())


def test_sqlite_busy_backoff_and_recovery(tmp_path):
    async def runner():
        trading_day = "20240102"
        store = SQLiteTickStore(
            tmp_path,
            busy_timeout_ms=50,
            journal_mode="WAL",
            synchronous="NORMAL",
            wal_autocheckpoint=1000,
        )
        db_path = store.ensure_db(trading_day)

        locker = sqlite3.connect(db_path)
        locker.execute("PRAGMA journal_mode=WAL;")
        locker.execute("BEGIN IMMEDIATE;")

        collector = AsyncTickCollector(
            store,
            batch_size=1,
            max_wait_ms=10,
            max_queue_size=100,
            persist_retry_max_attempts=0,
            persist_retry_backoff_sec=0.01,
            persist_retry_backoff_max_sec=0.1,
            heartbeat_interval_sec=0.2,
        )
        await collector.start()
        assert collector.enqueue([_row(1, trading_day=trading_day)])

        await asyncio.sleep(0.35)
        runtime_busy = collector.snapshot_runtime_state()
        assert int(runtime_busy["busy_locked_count"]) > 0
        assert int(runtime_busy["busy_backoff_count"]) > 0

        locker.commit()
        locker.close()

        wait_deadline = time.monotonic() + 10.0
        while collector.queue_size() > 0 and time.monotonic() < wait_deadline:
            await asyncio.sleep(0.05)

        await collector.stop(timeout_sec=8)
        assert collector.fatal_error() is None

        conn = sqlite3.connect(db_path_for_trading_day(tmp_path, trading_day))
        try:
            inserted = conn.execute("SELECT COUNT(1) FROM ticks WHERE symbol = ? AND seq = 1", ("HK.00700",)).fetchone()[0]
        finally:
            conn.close()
        assert inserted == 1

    asyncio.run(runner())
