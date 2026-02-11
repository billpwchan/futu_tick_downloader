#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo


def _utc_iso(ts_ms: int | None) -> str:
    if ts_ms is None:
        return "none"
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()


def _iter_target_dbs(data_root: Path, days: list[str], db_paths: list[Path], all_days: bool) -> Iterable[Path]:
    yielded: set[Path] = set()
    for path in db_paths:
        resolved = path.resolve()
        if resolved.exists() and resolved not in yielded:
            yielded.add(resolved)
            yield resolved

    if all_days:
        for path in sorted(data_root.glob("*.db")):
            resolved = path.resolve()
            if resolved not in yielded:
                yielded.add(resolved)
                yield resolved
        return

    for day in days:
        candidate = (data_root / f"{day}.db").resolve()
        if candidate.exists() and candidate not in yielded:
            yielded.add(candidate)
            yield candidate


def _repair_one_db(
    db_path: Path,
    *,
    now_ms: int,
    future_threshold_ms: int,
    shift_ms: int,
    sample_size: int,
    dry_run: bool,
) -> tuple[int, int]:
    if not db_path.exists():
        print(f"[SKIP] db not found: {db_path}")
        return 0, 0

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000;")
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ticks'"
        ).fetchone()
        if table is None:
            print(f"[SKIP] ticks table missing: {db_path}")
            return 0, 0

        bad_count = conn.execute(
            "SELECT COUNT(*) FROM ticks WHERE ts_ms > ?",
            (future_threshold_ms,),
        ).fetchone()[0]
        if bad_count == 0:
            print(f"[OK] {db_path} no future ts_ms > now+threshold")
            return 0, 0

        sample_before = conn.execute(
            """
            SELECT rowid, symbol, ts_ms, trading_day
            FROM ticks
            WHERE ts_ms > ?
            ORDER BY ts_ms DESC
            LIMIT ?
            """,
            (future_threshold_ms, sample_size),
        ).fetchall()
        sample_rowids = [int(row["rowid"]) for row in sample_before]

        print(
            f"[PLAN] {db_path} rows_to_fix={bad_count} "
            f"now_utc={_utc_iso(now_ms)} threshold_utc={_utc_iso(future_threshold_ms)} "
            f"shift_ms={shift_ms} dry_run={dry_run}"
        )
        for row in sample_before:
            print(
                "[BEFORE] rowid={rowid} symbol={symbol} trading_day={trading_day} ts_ms={ts_ms} ts_utc={ts_utc}".format(
                    rowid=row["rowid"],
                    symbol=row["symbol"],
                    trading_day=row["trading_day"],
                    ts_ms=row["ts_ms"],
                    ts_utc=_utc_iso(int(row["ts_ms"])),
                )
            )

        if not dry_run:
            conn.execute(
                """
                UPDATE ticks
                SET
                  ts_ms = ts_ms - ?,
                  trading_day = strftime('%Y%m%d', (ts_ms - ?) / 1000.0, 'unixepoch', '+8 hours')
                WHERE ts_ms > ?
                """,
                (shift_ms, shift_ms, future_threshold_ms),
            )
            conn.commit()

        if sample_rowids:
            placeholders = ",".join("?" for _ in sample_rowids)
            sample_after = conn.execute(
                f"""
                SELECT rowid, symbol, ts_ms, trading_day
                FROM ticks
                WHERE rowid IN ({placeholders})
                ORDER BY ts_ms DESC
                """,
                tuple(sample_rowids),
            ).fetchall()
            for row in sample_after:
                print(
                    "[AFTER] rowid={rowid} symbol={symbol} trading_day={trading_day} ts_ms={ts_ms} ts_utc={ts_utc}".format(
                        rowid=row["rowid"],
                        symbol=row["symbol"],
                        trading_day=row["trading_day"],
                        ts_ms=row["ts_ms"],
                        ts_utc=_utc_iso(int(row["ts_ms"])),
                    )
                )
        return bad_count, bad_count if not dry_run else 0
    finally:
        conn.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair clearly-future ticks.ts_ms values (default: subtract 8 hours)."
    )
    parser.add_argument("--data-root", default="/data/sqlite/HK", help="SQLite directory, default /data/sqlite/HK")
    parser.add_argument("--day", action="append", default=[], help="Trading day YYYYMMDD (repeatable)")
    parser.add_argument("--db", action="append", default=[], help="Explicit DB path (repeatable)")
    parser.add_argument("--all-days", action="store_true", help="Scan all *.db under data root")
    parser.add_argument(
        "--future-threshold-hours",
        type=float,
        default=2.0,
        help="Only fix rows where ts_ms > now + this many hours (default 2)",
    )
    parser.add_argument(
        "--shift-hours",
        type=float,
        default=8.0,
        help="Subtract this many hours from future ts_ms rows (default 8)",
    )
    parser.add_argument("--sample-size", type=int, default=5, help="Sample rows before/after (default 5)")
    parser.add_argument("--dry-run", action="store_true", help="Only print plan/samples, no UPDATE")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    data_root = Path(args.data_root)
    db_paths = [Path(item) for item in args.db]

    now_ms = int(time.time() * 1000)
    future_threshold_ms = now_ms + int(args.future_threshold_hours * 3600 * 1000)
    shift_ms = int(args.shift_hours * 3600 * 1000)
    if shift_ms <= 0:
        print("[FAIL] shift-hours must be > 0", file=sys.stderr)
        return 2

    days = [str(day).strip() for day in args.day if str(day).strip()]
    if not args.all_days and not days and not db_paths:
        today_hk = datetime.now(tz=ZoneInfo("Asia/Hong_Kong")).strftime("%Y%m%d")
        days = [today_hk]

    total_to_fix = 0
    total_fixed = 0
    found_any = False
    for db_path in _iter_target_dbs(data_root, days, db_paths, args.all_days):
        found_any = True
        to_fix, fixed = _repair_one_db(
            db_path,
            now_ms=now_ms,
            future_threshold_ms=future_threshold_ms,
            shift_ms=shift_ms,
            sample_size=max(1, int(args.sample_size)),
            dry_run=bool(args.dry_run),
        )
        total_to_fix += to_fix
        total_fixed += fixed

    if not found_any:
        print(
            f"[SKIP] no target DB found under data_root={data_root} "
            f"days={days} db={args.db} all_days={args.all_days}"
        )
        return 0

    print(
        f"[SUMMARY] candidate_rows={total_to_fix} fixed_rows={total_fixed} "
        f"dry_run={args.dry_run} now_utc={_utc_iso(now_ms)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
