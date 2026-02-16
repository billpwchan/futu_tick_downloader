import sqlite3

from hk_tick_collector.db import (
    CREATE_TABLE_SQL,
    INDEX_SQLS,
    SCHEMA_VERSION,
    SQLiteTickStore,
    db_path_for_trading_day,
)
from hk_tick_collector.quality.schema import CREATE_DAILY_QUALITY_TABLE_SQL, CREATE_GAPS_TABLE_SQL


def test_schema_and_indexes(tmp_path):
    store = SQLiteTickStore(tmp_path)
    db_path = store.ensure_db("20240102")

    conn = sqlite3.connect(db_path)
    try:
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='ticks'"
        ).fetchone()[0]
        assert _normalize_sql(table_sql) == _normalize_sql(CREATE_TABLE_SQL)

        for name, sql in INDEX_SQLS:
            idx_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                (name,),
            ).fetchone()[0]
            assert _normalize_sql(idx_sql) == _normalize_sql(sql)

        gaps_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='gaps'"
        ).fetchone()[0]
        daily_quality_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='daily_quality'"
        ).fetchone()[0]
        assert _normalize_sql(gaps_sql) == _normalize_sql(
            CREATE_GAPS_TABLE_SQL.replace(" IF NOT EXISTS", "")
        )
        assert _normalize_sql(daily_quality_sql) == _normalize_sql(
            CREATE_DAILY_QUALITY_TABLE_SQL.replace(" IF NOT EXISTS", "")
        )

        version = conn.execute("PRAGMA user_version;").fetchone()[0]
        assert version == SCHEMA_VERSION
    finally:
        conn.close()


def test_connect_sets_temp_store_memory(tmp_path):
    store = SQLiteTickStore(tmp_path)
    db_path = db_path_for_trading_day(tmp_path, "20240102")
    conn = store._connect(db_path)  # noqa: SLF001
    try:
        temp_store = int(conn.execute("PRAGMA temp_store;").fetchone()[0])
    finally:
        conn.close()
    assert temp_store == 2


def test_connect_applies_sqlite_pragmas(tmp_path):
    store = SQLiteTickStore(
        tmp_path,
        busy_timeout_ms=7000,
        journal_mode="WAL",
        synchronous="FULL",
        wal_autocheckpoint=2048,
    )
    db_path = db_path_for_trading_day(tmp_path, "20240102")
    conn = store._connect(db_path)  # noqa: SLF001
    try:
        journal_mode = str(conn.execute("PRAGMA journal_mode;").fetchone()[0]).upper()
        synchronous = int(conn.execute("PRAGMA synchronous;").fetchone()[0])
        busy_timeout = int(conn.execute("PRAGMA busy_timeout;").fetchone()[0])
        wal_autocheckpoint = int(conn.execute("PRAGMA wal_autocheckpoint;").fetchone()[0])
    finally:
        conn.close()

    assert journal_mode == "WAL"
    assert synchronous == 2  # FULL
    assert busy_timeout == 7000
    assert wal_autocheckpoint == 2048


def _normalize_sql(value: str) -> str:
    return value.strip().rstrip(";")
