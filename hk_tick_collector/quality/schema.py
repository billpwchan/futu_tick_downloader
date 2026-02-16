from __future__ import annotations

import sqlite3

CREATE_GAPS_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS gaps (\n"
    "  trading_day TEXT NOT NULL,\n"
    "  symbol TEXT NOT NULL,\n"
    "  gap_start_ts_ms INTEGER NOT NULL,\n"
    "  gap_end_ts_ms INTEGER NOT NULL,\n"
    "  gap_sec REAL NOT NULL,\n"
    "  detected_at_ms INTEGER NOT NULL,\n"
    "  reason TEXT NOT NULL,\n"
    "  meta_json TEXT NOT NULL,\n"
    "  PRIMARY KEY (symbol, gap_start_ts_ms, gap_end_ts_ms)\n"
    ");"
)

CREATE_GAPS_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_gaps_day_symbol ON gaps(trading_day, symbol);"
)

INSERT_GAP_SQL = (
    "INSERT OR IGNORE INTO gaps ("
    "trading_day, symbol, gap_start_ts_ms, gap_end_ts_ms, gap_sec, detected_at_ms, reason, meta_json"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?);"
)

CREATE_DAILY_QUALITY_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS daily_quality (\n"
    "  trading_day TEXT PRIMARY KEY,\n"
    "  created_at_ms INTEGER NOT NULL,\n"
    "  host TEXT,\n"
    "  symbols_json TEXT NOT NULL,\n"
    "  summary_json TEXT NOT NULL\n"
    ");"
)

UPSERT_DAILY_QUALITY_SQL = (
    "INSERT INTO daily_quality (trading_day, created_at_ms, host, symbols_json, summary_json) "
    "VALUES (?, ?, ?, ?, ?) "
    "ON CONFLICT(trading_day) DO UPDATE SET "
    "created_at_ms=excluded.created_at_ms, "
    "host=excluded.host, "
    "symbols_json=excluded.symbols_json, "
    "summary_json=excluded.summary_json;"
)


def ensure_quality_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_GAPS_TABLE_SQL)
    conn.execute(CREATE_GAPS_INDEX_SQL)
    conn.execute(CREATE_DAILY_QUALITY_TABLE_SQL)
