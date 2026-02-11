#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from zoneinfo import ZoneInfo


def _iso_utc(ts_ms: int | None) -> str:
    if ts_ms is None:
        return "none"
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()


def _resolve_db_path(args: argparse.Namespace) -> Path:
    if args.db:
        return Path(args.db)
    day = args.day
    if not day:
        day = datetime.now(tz=ZoneInfo("Asia/Hong_Kong")).strftime("%Y%m%d")
    return Path(args.data_root) / f"{day}.db"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate ticks.ts_ms as UTC epoch ms by checking max(ts_ms)-now_utc drift.",
    )
    parser.add_argument("--db", help="path to sqlite db file")
    parser.add_argument("--data-root", default="/data/sqlite/HK", help="sqlite directory, default /data/sqlite/HK")
    parser.add_argument("--day", help="HK trading day (YYYYMMDD), default today in Asia/Hong_Kong")
    parser.add_argument("--tolerance-sec", type=float, default=5.0, help="absolute drift tolerance in seconds")
    args = parser.parse_args()

    db_path = _resolve_db_path(args)
    if not db_path.exists():
        print(f"status=FAIL reason=db_not_found db={db_path}")
        return 1

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT COUNT(*), MAX(ts_ms) FROM ticks").fetchone()
        rows = int(row[0] or 0)
        max_ts_ms = int(row[1]) if row[1] is not None else None
    finally:
        conn.close()

    now_ms = int(time.time() * 1000)
    max_minus_now_sec = 0.0 if max_ts_ms is None else (max_ts_ms - now_ms) / 1000.0

    print(f"db={db_path}")
    print(f"now_utc={_iso_utc(now_ms)}")
    print(f"max_ts_utc={_iso_utc(max_ts_ms)}")
    print(f"max_minus_now_sec={max_minus_now_sec:.3f}")
    print(f"rows={rows}")

    if rows <= 0:
        print("status=FAIL reason=no_rows")
        return 1
    if abs(max_minus_now_sec) > float(args.tolerance_sec):
        print(
            f"status=FAIL reason=ts_drift_exceeded tolerance_sec={float(args.tolerance_sec):.3f}"
        )
        return 1
    print(
        f"status=PASS tolerance_sec={float(args.tolerance_sec):.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
