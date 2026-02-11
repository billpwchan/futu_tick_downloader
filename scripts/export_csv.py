#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path


DEFAULT_QUERY = (
    "SELECT market, symbol, ts_ms, price, volume, turnover, direction, seq, tick_type, "
    "push_type, provider, trading_day, recv_ts_ms, inserted_at_ms FROM ticks ORDER BY ts_ms"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export hk-tick-collector SQLite ticks to CSV")
    parser.add_argument("--db", required=True, help="SQLite DB path")
    parser.add_argument("--out", required=True, help="Output CSV path")
    parser.add_argument("--where", default="", help="Optional SQL WHERE clause body")
    parser.add_argument("--limit", type=int, default=0, help="Optional LIMIT (0 means no limit)")
    return parser.parse_args()


def build_query(where: str, limit: int) -> str:
    query = DEFAULT_QUERY
    if where.strip():
        query = query.replace(" FROM ticks ", f" FROM ticks WHERE {where} ")
    if limit > 0:
        query = f"{query} LIMIT {limit}"
    return query


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    out_path = Path(args.out)

    if not db_path.exists():
        raise SystemExit(f"db not found: {db_path}")

    query = build_query(args.where, args.limit)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cursor = conn.execute(query)
        columns = [desc[0] for desc in cursor.description]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            writer.writerows(cursor.fetchall())
    finally:
        conn.close()

    print(f"exported csv: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
