from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

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


@dataclass(frozen=True)
class PersistResult:
    db_path: Path
    batch: int
    inserted: int
    ignored: int
    commit_latency_ms: int
    checkpoint: str = "none"


def db_path_for_trading_day(data_root: Path, trading_day: str) -> Path:
    return Path(data_root) / f"{trading_day}.db"


def _open_conn(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
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

    def insert_ticks(self, trading_day: str, rows: Iterable[TickRow]) -> PersistResult:
        rows_list = list(rows)
        if not rows_list:
            db_path = db_path_for_trading_day(self._data_root, trading_day)
            return PersistResult(
                db_path=db_path,
                batch=0,
                inserted=0,
                ignored=0,
                commit_latency_ms=0,
            )
        db_path = db_path_for_trading_day(self._data_root, trading_day)
        conn = _open_conn(db_path)
        try:
            ensure_schema(conn)
            before = conn.total_changes
            start = time.perf_counter()
            conn.executemany(INSERT_SQL, [row.as_tuple() for row in rows_list])
            conn.commit()
            latency_ms = int((time.perf_counter() - start) * 1000)
            inserted = conn.total_changes - before
            ignored = max(0, len(rows_list) - inserted)
            logger.info(
                "persist_ticks db_path=%s batch=%s inserted=%s ignored=%s commit_latency_ms=%s checkpoint=%s",
                db_path,
                len(rows_list),
                inserted,
                ignored,
                latency_ms,
                "none",
            )
            return PersistResult(
                db_path=db_path,
                batch=len(rows_list),
                inserted=inserted,
                ignored=ignored,
                commit_latency_ms=latency_ms,
            )
        finally:
            conn.close()

    def fetch_max_seq_by_symbol(self, trading_day: str, symbols: Sequence[str]) -> Dict[str, int]:
        if not symbols:
            return {}
        db_path = db_path_for_trading_day(self._data_root, trading_day)
        if not db_path.exists():
            return {}
        conn = _open_conn(db_path)
        try:
            ensure_schema(conn)
            placeholders = ",".join("?" for _ in symbols)
            rows = conn.execute(
                (
                    "SELECT symbol, MAX(seq) "
                    "FROM ticks WHERE trading_day = ? AND seq IS NOT NULL "
                    f"AND symbol IN ({placeholders}) GROUP BY symbol"
                ),
                (trading_day, *symbols),
            ).fetchall()
            return {symbol: seq for symbol, seq in rows if seq is not None}
        finally:
            conn.close()
