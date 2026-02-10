import sqlite3

from hk_tick_collector.db import CREATE_TABLE_SQL, INDEX_SQLS, SCHEMA_VERSION, SQLiteTickStore, ensure_schema


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

        version = conn.execute("PRAGMA user_version;").fetchone()[0]
        assert version == SCHEMA_VERSION
    finally:
        conn.close()


def test_schema_migration_drops_legacy_symbol_ts_unique_index(tmp_path):
    db_path = tmp_path / "20240102.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE ticks ("
            "market TEXT, symbol TEXT, ts_ms INTEGER, price REAL, volume INTEGER, turnover REAL, "
            "trading_day TEXT, inserted_at_ms INTEGER)"
        )
        conn.execute("CREATE UNIQUE INDEX uniq_ticks_symbol_ts_ms ON ticks(symbol, ts_ms)")
        conn.commit()

        ensure_schema(conn)

        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='uniq_ticks_symbol_ts_ms'"
        ).fetchone()
        assert idx is None
        for name, _ in INDEX_SQLS:
            assert conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
                (name,),
            ).fetchone()
    finally:
        conn.close()


def _normalize_sql(value: str) -> str:
    return value.strip().rstrip(";")
