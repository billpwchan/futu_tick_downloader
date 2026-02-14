#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from zipfile import ZIP_DEFLATED, ZipFile

try:
    from zoneinfo import ZoneInfo
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Python 3.9+ is required (zoneinfo unavailable).") from exc


FUTU_HEADERS = (
    "code",
    "name",
    "time",
    "price",
    "volume",
    "turnover",
    "ticker_direction",
    "sequence",
    "type",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a daily hk-tick-collector DB to a zip of per-symbol CSV files."
    )
    parser.add_argument("--db", required=True, help="SQLite DB path, e.g. /path/20260212.db")
    parser.add_argument("--out", required=True, help="Output zip path, e.g. /path/20260212.zip")
    parser.add_argument(
        "--tz",
        default="Asia/Hong_Kong",
        help="Timezone for rendered time column (default: Asia/Hong_Kong)",
    )
    parser.add_argument(
        "--compress-level",
        type=int,
        default=6,
        help="Zip deflate level 0-9 (default: 6)",
    )
    return parser.parse_args()


def list_symbols(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT symbol FROM ticks ORDER BY symbol;").fetchall()
    return [str(row[0]) for row in rows if row[0]]


def iter_symbol_rows(
    conn: sqlite3.Connection, symbol: str
) -> Iterable[
    tuple[str, int, float | None, int | None, float | None, str | None, int | None, str | None]
]:
    sql = (
        "SELECT symbol, ts_ms, price, volume, turnover, direction, seq, tick_type "
        "FROM ticks "
        "WHERE symbol = ? "
        "ORDER BY ts_ms ASC, COALESCE(seq, -1) ASC, rowid ASC;"
    )
    cursor = conn.execute(sql, (symbol,))
    while True:
        rows = cursor.fetchmany(5000)
        if not rows:
            break
        for row in rows:
            yield row  # type: ignore[misc]


def filename_from_symbol(symbol: str) -> str:
    stem = symbol
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
    if not stem:
        stem = "UNKNOWN"
    return f"{stem}.csv"


def format_time(ts_ms: int, tz: ZoneInfo) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).astimezone(tz)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def to_csv_cell(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def write_symbol_csv(
    conn: sqlite3.Connection,
    zf: ZipFile,
    symbol: str,
    arcname: str,
    tz: ZoneInfo,
) -> int:
    row_count = 0
    with zf.open(arcname, mode="w") as raw:
        with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text_writer:
            writer = csv.writer(text_writer)
            writer.writerow(FUTU_HEADERS)
            for code, ts_ms, price, volume, turnover, direction, seq, tick_type in iter_symbol_rows(
                conn, symbol
            ):
                writer.writerow(
                    [
                        to_csv_cell(code),
                        "",
                        format_time(int(ts_ms), tz),
                        to_csv_cell(price),
                        to_csv_cell(volume),
                        to_csv_cell(turnover),
                        to_csv_cell(direction),
                        to_csv_cell(seq),
                        to_csv_cell(tick_type),
                    ]
                )
                row_count += 1
            text_writer.flush()
    return row_count


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    out_path = Path(args.out)
    tz = ZoneInfo(args.tz)

    if not db_path.exists():
        raise SystemExit(f"db not found: {db_path}")

    compress_level = min(max(args.compress_level, 0), 9)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    try:
        symbols = list_symbols(conn)
        if not symbols:
            raise SystemExit("no symbols found in ticks table")

        used_names: set[str] = set()
        total_rows = 0
        with ZipFile(
            out_path, mode="w", compression=ZIP_DEFLATED, compresslevel=compress_level
        ) as zf:
            for idx, symbol in enumerate(symbols, start=1):
                arcname = filename_from_symbol(symbol)
                if arcname in used_names:
                    arcname = filename_from_symbol(symbol.replace(".", "_"))
                used_names.add(arcname)

                rows = write_symbol_csv(conn, zf, symbol, arcname, tz)
                total_rows += rows

                if idx % 100 == 0 or idx == len(symbols):
                    print(f"[{idx}/{len(symbols)}] {symbol} -> {arcname} rows={rows}")
    finally:
        conn.close()

    print(f"done zip={out_path} symbols={len(symbols)} rows={total_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
