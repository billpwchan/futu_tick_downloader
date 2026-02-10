from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

from .models import TickRow

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

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

_ALTER_COLUMN_SQL = {
    "direction": "ALTER TABLE ticks ADD COLUMN direction TEXT;",
    "seq": "ALTER TABLE ticks ADD COLUMN seq INTEGER;",
    "tick_type": "ALTER TABLE ticks ADD COLUMN tick_type TEXT;",
    "push_type": "ALTER TABLE ticks ADD COLUMN push_type TEXT;",
    "provider": "ALTER TABLE ticks ADD COLUMN provider TEXT;",
    "trading_day": "ALTER TABLE ticks ADD COLUMN trading_day TEXT NOT NULL DEFAULT '';",
    "inserted_at_ms": "ALTER TABLE ticks ADD COLUMN inserted_at_ms INTEGER NOT NULL DEFAULT 0;",
}

_ALLOWED_UNIQUE_INDEXES = {"uniq_ticks_symbol_seq", "uniq_ticks_symbol_ts_price_vol_turnover"}
_VALID_JOURNAL_MODES = {"DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"}
_VALID_SYNCHRONOUS = {"OFF", "NORMAL", "FULL", "EXTRA"}


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


def _sanitize_journal_mode(value: str) -> str:
    mode = str(value or "WAL").strip().upper()
    return mode if mode in _VALID_JOURNAL_MODES else "WAL"


def _sanitize_synchronous(value: str) -> str:
    level = str(value or "NORMAL").strip().upper()
    return level if level in _VALID_SYNCHRONOUS else "NORMAL"


def _open_conn(
    db_path: Path,
    *,
    busy_timeout_ms: int,
    journal_mode: str,
    synchronous: str,
) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA journal_mode={_sanitize_journal_mode(journal_mode)};")
    conn.execute(f"PRAGMA synchronous={_sanitize_synchronous(synchronous)};")
    conn.execute(f"PRAGMA busy_timeout={max(1, int(busy_timeout_ms))};")
    return conn


def _existing_schema_objects(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'index');"
        ).fetchall()
    }


def _existing_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(ticks);").fetchall()}


def _index_columns(conn: sqlite3.Connection, index_name: str) -> list[str]:
    escaped = index_name.replace("'", "''")
    rows = conn.execute(f"PRAGMA index_info('{escaped}');").fetchall()
    return [row[2] for row in rows]


def _drop_legacy_unique_indexes(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA index_list('ticks');").fetchall()
    for _, index_name, is_unique, _, _ in rows:
        if not is_unique:
            continue
        if index_name in _ALLOWED_UNIQUE_INDEXES:
            continue
        columns = _index_columns(conn, index_name)
        if columns[:2] == ["symbol", "ts_ms"] and "seq" not in columns:
            logger.warning("schema_migration dropping_legacy_unique_index index=%s columns=%s", index_name, columns)
            escaped = index_name.replace('"', '""')
            try:
                conn.execute(f'DROP INDEX IF EXISTS "{escaped}";')
            except sqlite3.OperationalError:
                logger.exception("schema_migration_failed_drop_index index=%s", index_name)


def ensure_schema(conn: sqlite3.Connection) -> None:
    existing = _existing_schema_objects(conn)
    if "ticks" not in existing:
        conn.execute(CREATE_TABLE_SQL)
    else:
        columns = _existing_columns(conn)
        for col, alter_sql in _ALTER_COLUMN_SQL.items():
            if col not in columns:
                logger.warning("schema_migration add_column=%s", col)
                conn.execute(alter_sql)

    _drop_legacy_unique_indexes(conn)

    existing = _existing_schema_objects(conn)
    for name, sql in INDEX_SQLS:
        if name not in existing:
            conn.execute(sql)

    version = conn.execute("PRAGMA user_version;").fetchone()[0]
    if version < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION};")
    conn.commit()


class SQLiteTickStore:
    def __init__(
        self,
        data_root: Path,
        *,
        busy_timeout_ms: int = 5000,
        journal_mode: str = "WAL",
        synchronous: str = "NORMAL",
    ) -> None:
        self._data_root = Path(data_root)
        self._busy_timeout_ms = max(1, int(busy_timeout_ms))
        self._journal_mode = _sanitize_journal_mode(journal_mode)
        self._synchronous = _sanitize_synchronous(synchronous)

    def _connect(self, db_path: Path) -> sqlite3.Connection:
        return _open_conn(
            db_path,
            busy_timeout_ms=self._busy_timeout_ms,
            journal_mode=self._journal_mode,
            synchronous=self._synchronous,
        )

    def ensure_db(self, trading_day: str) -> Path:
        db_path = db_path_for_trading_day(self._data_root, trading_day)
        conn = self._connect(db_path)
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
        conn = self._connect(db_path)
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
        conn = self._connect(db_path)
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
