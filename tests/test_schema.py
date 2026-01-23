import sqlite3

from hk_tick_collector.db import CREATE_TABLE_SQL, INDEX_SQLS, SQLiteTickStore


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
    finally:
        conn.close()


def _normalize_sql(value: str) -> str:
    return value.strip().rstrip(";")
