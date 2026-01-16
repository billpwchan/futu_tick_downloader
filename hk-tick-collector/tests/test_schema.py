import sqlite3

import pytest

from hk_tick_collector.sqlite_store import SQLiteStore


@pytest.mark.asyncio
async def test_schema_and_indexes_exist(tmp_path):
    store = SQLiteStore(
        data_dir=str(tmp_path),
        journal_mode="WAL",
        synchronous="NORMAL",
        temp_store="MEMORY",
    )
    db_path = await store.init_db("HK", "20240102")
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
        assert "ticks" in tables

        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in cur.fetchall()}
        assert "idx_ticks_symbol_day_ts" in indexes
        assert "idx_ticks_symbol_seq" in indexes
        assert "uniq_ticks_symbol_seq" in indexes
        assert "uniq_ticks_symbol_ts_price_vol_turnover" in indexes
    finally:
        conn.close()
        await store.close()
