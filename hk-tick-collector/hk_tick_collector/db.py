from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Iterable, List

from .models import TickRow

logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = (
    "CREATE TABLE ticks (\n"
    "  market TEXT NOT NULL,\n"
    "  symbol TEXT NOT NULL,\n"
    "  ts_ms INTEGER NOT NULL,\n"
    "  price REAL,\n"
    "  volume INTEGER,\n"
    "  turnover REAL,\n"
    "  direction TEXT,\n"
    "  seq INTEGER,\n"
    "  tick_type TEXT,\n"
    "  push_type TEXT,\n"
    "  provider TEXT,\n"
    "  trading_day TEXT NOT NULL,\n"
    "  inserted_at_ms INTEGER NOT NULL\n"
    ");"
)

INDEX_SQLS = [
    (
        "idx_ticks_symbol_day_ts",
        "CREATE INDEX idx_ticks_symbol_day_ts ON ticks(symbol, trading_day, ts_ms);",
    ),
    (
        "idx_ticks_symbol_seq",
        "CREATE INDEX idx_ticks_symbol_seq ON ticks(symbol, seq);",
    ),
    (
        "uniq_ticks_symbol_seq",
        "CREATE UNIQUE INDEX uniq_ticks_symbol_seq ON ticks(symbol, seq) WHERE seq IS NOT NULL;",
    ),
    (
        "uniq_ticks_symbol_ts_price_vol_turnover",
        "CREATE UNIQUE INDEX uniq_ticks_symbol_ts_price_vol_turnover\n"
        "  ON ticks(symbol, ts_ms, price, volume, turnover) WHERE seq IS NULL;",
    ),
]

INSERT_SQL = (
    "INSERT OR IGNORE INTO ticks ("
    "market, symbol, ts_ms, price, volume, turnover, direction, seq, tick_type, "
    "push_type, provider, trading_day, inserted_at_ms"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);"
)


def db_path_for_trading_day(data_root: Path, trading_day: str) -> Path:
    return Path(data_root) / f"{trading_day}.db"


def _open_conn(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'index');"
        ).fetchall()
    }

    if "ticks" not in existing:
        conn.execute(CREATE_TABLE_SQL)

    for name, sql in INDEX_SQLS:
        if name not in existing:
            conn.execute(sql)

    conn.commit()


class SQLiteTickStore:
    def __init__(self, data_root: Path) -> None:
        self._data_root = Path(data_root)

    def ensure_db(self, trading_day: str) -> Path:
        db_path = db_path_for_trading_day(self._data_root, trading_day)
        conn = _open_conn(db_path)
        try:
            ensure_schema(conn)
        finally:
            conn.close()
        return db_path

    def insert_ticks(self, trading_day: str, rows: Iterable[TickRow]) -> int:
        rows_list = list(rows)
        if not rows_list:
            return 0
        db_path = db_path_for_trading_day(self._data_root, trading_day)
        conn = _open_conn(db_path)
        try:
            ensure_schema(conn)
            conn.executemany(INSERT_SQL, [row.as_tuple() for row in rows_list])
            conn.commit()
            return conn.total_changes
        finally:
            conn.close()
