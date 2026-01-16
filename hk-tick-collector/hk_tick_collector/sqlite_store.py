from __future__ import annotations

import os
from collections.abc import Iterable
import aiosqlite
import asyncio

from .models import TickRow
from .utils import now_ms

SCHEMA_SQL = """
CREATE TABLE ticks (
  market TEXT NOT NULL,
  symbol TEXT NOT NULL,
  ts_ms INTEGER NOT NULL,
  price REAL,
  volume INTEGER,
  turnover REAL,
  direction TEXT,
  seq INTEGER,
  tick_type TEXT,
  push_type TEXT,
  provider TEXT,
  trading_day TEXT NOT NULL,
  inserted_at_ms INTEGER NOT NULL
);
CREATE INDEX idx_ticks_symbol_day_ts ON ticks(symbol, trading_day, ts_ms);
CREATE INDEX idx_ticks_symbol_seq ON ticks(symbol, seq);
CREATE UNIQUE INDEX uniq_ticks_symbol_seq ON ticks(symbol, seq) WHERE seq IS NOT NULL;
CREATE UNIQUE INDEX uniq_ticks_symbol_ts_price_vol_turnover
  ON ticks(symbol, ts_ms, price, volume, turnover) WHERE seq IS NULL;
"""

INSERT_SQL = """
INSERT OR IGNORE INTO ticks (
  market,
  symbol,
  ts_ms,
  price,
  volume,
  turnover,
  direction,
  seq,
  tick_type,
  push_type,
  provider,
  trading_day,
  inserted_at_ms
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class SQLiteStore:
    def __init__(self, data_dir: str, journal_mode: str, synchronous: str, temp_store: str) -> None:
        self._data_dir = data_dir
        self._journal_mode = journal_mode
        self._synchronous = synchronous
        self._temp_store = temp_store
        self._connections: dict[str, aiosqlite.Connection] = {}
        self._initialized: set[str] = set()
        self._lock = asyncio.Lock()

    def db_path(self, market: str, trading_day: str) -> str:
        return os.path.join(self._data_dir, "sqlite", market, f"{trading_day}.db")

    async def _get_conn(self, path: str) -> aiosqlite.Connection:
        async with self._lock:
            if path in self._connections:
                return self._connections[path]
            os.makedirs(os.path.dirname(path), exist_ok=True)
            conn = await aiosqlite.connect(path)
            await conn.execute(f"PRAGMA journal_mode={self._journal_mode}")
            await conn.execute(f"PRAGMA synchronous={self._synchronous}")
            await conn.execute(f"PRAGMA temp_store={self._temp_store}")
            await conn.commit()
            self._connections[path] = conn
            return conn

    async def _ensure_schema(self, conn: aiosqlite.Connection, path: str) -> None:
        if path in self._initialized:
            return
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ticks'"
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            await conn.executescript(SCHEMA_SQL)
            await conn.commit()
        self._initialized.add(path)

    async def init_db(self, market: str, trading_day: str) -> str:
        path = self.db_path(market, trading_day)
        conn = await self._get_conn(path)
        await self._ensure_schema(conn, path)
        return path

    async def write_ticks(self, market: str, trading_day: str, ticks: Iterable[TickRow]) -> None:
        ticks_list = list(ticks)
        if not ticks_list:
            return
        path = self.db_path(market, trading_day)
        conn = await self._get_conn(path)
        await self._ensure_schema(conn, path)
        inserted_at = now_ms()
        rows = []
        for tick in ticks_list:
            if tick.inserted_at_ms is None:
                tick.inserted_at_ms = inserted_at
            rows.append(
                (
                    tick.market,
                    tick.symbol,
                    tick.ts_ms,
                    tick.price,
                    tick.volume,
                    tick.turnover,
                    tick.direction,
                    tick.seq,
                    tick.tick_type,
                    tick.push_type,
                    tick.provider,
                    tick.trading_day,
                    tick.inserted_at_ms,
                )
            )
        await conn.executemany(INSERT_SQL, rows)
        await conn.commit()

    async def close(self) -> None:
        for conn in self._connections.values():
            await conn.close()
        self._connections.clear()
        self._initialized.clear()
