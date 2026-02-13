#!/usr/bin/env python3
from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Allow running directly from repo without pip install -e .
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hk_tick_collector.db import SQLiteTickStore  # noqa: E402
from hk_tick_collector.models import TickRow  # noqa: E402

HK_TZ = ZoneInfo("Asia/Hong_Kong")
_STOP = False


def _handle_stop(_signum: int, _frame: object) -> None:
    global _STOP
    _STOP = True


def _now_ms() -> int:
    return int(time.time() * 1000)


def _trading_day_hk() -> str:
    return datetime.now(tz=HK_TZ).strftime("%Y%m%d")


def _build_rows(symbols: list[str], seq_map: dict[str, int], batch_size: int) -> list[TickRow]:
    rows: list[TickRow] = []
    trading_day = _trading_day_hk()
    base_ms = _now_ms()

    for idx in range(batch_size):
        symbol = symbols[idx % len(symbols)]
        seq_map[symbol] += 1
        seq = seq_map[symbol]
        ts_ms = base_ms + idx
        price = 100.0 + (seq % 500) * 0.01
        volume = 100 + (seq % 20) * 10
        turnover = round(price * volume, 2)
        rows.append(
            TickRow(
                market="HK",
                symbol=symbol,
                ts_ms=ts_ms,
                price=price,
                volume=volume,
                turnover=turnover,
                direction="NEUTRAL",
                seq=seq,
                tick_type="MOCK",
                push_type="mock",
                provider="mock-replay",
                trading_day=trading_day,
                recv_ts_ms=ts_ms,
                inserted_at_ms=_now_ms(),
            )
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate mock HK tick rows into SQLite")
    parser.add_argument("--data-root", default="/data/sqlite/HK")
    parser.add_argument("--symbols", default="HK.00700,HK.00981")
    parser.add_argument("--interval-ms", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--busy-timeout-ms", type=int, default=5000)
    parser.add_argument("--wal-autocheckpoint", type=int, default=1000)
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        raise SystemExit("--symbols cannot be empty")

    store = SQLiteTickStore(
        data_root=Path(args.data_root),
        busy_timeout_ms=args.busy_timeout_ms,
        journal_mode="WAL",
        synchronous="NORMAL",
        wal_autocheckpoint=args.wal_autocheckpoint,
    )
    writer = store.open_writer()
    seq_map = {symbol: 0 for symbol in symbols}
    written_total = 0

    print(
        "mock_replay_started"
        f" data_root={args.data_root} symbols={','.join(symbols)}"
        f" interval_ms={args.interval_ms} batch_size={args.batch_size}",
        flush=True,
    )

    try:
        while not _STOP:
            rows = _build_rows(symbols, seq_map, max(1, args.batch_size))
            trading_day = rows[0].trading_day
            result = writer.insert_ticks(trading_day, rows)
            written_total += result.inserted
            print(
                f"mock_replay_tick trading_day={trading_day} inserted={result.inserted} total={written_total}",
                flush=True,
            )
            time.sleep(max(0.05, args.interval_ms / 1000.0))
    finally:
        writer.close()
        print(f"mock_replay_stopped total_inserted={written_total}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
