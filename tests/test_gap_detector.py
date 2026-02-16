from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from hk_tick_collector.db import SQLiteTickStore
from hk_tick_collector.models import TickRow
from hk_tick_collector.quality.config import QualityConfig
from hk_tick_collector.quality.gap_detector import GapDetector

HK_TZ = ZoneInfo("Asia/Hong_Kong")


def _ts_ms(day: str, hhmmss: str) -> int:
    dt = datetime.strptime(f"{day} {hhmmss}", "%Y%m%d %H:%M:%S").replace(tzinfo=HK_TZ)
    return int(dt.timestamp() * 1000)


def _row(symbol: str, day: str, hhmmss: str, seq: int) -> TickRow:
    ts_ms = _ts_ms(day, hhmmss)
    return TickRow(
        market="HK",
        symbol=symbol,
        ts_ms=ts_ms,
        price=100.0,
        volume=100,
        turnover=10000.0,
        direction="BUY",
        seq=seq,
        tick_type="AUTO_MATCH",
        push_type="push",
        provider="futu",
        trading_day=day,
        recv_ts_ms=ts_ms + 3,
        inserted_at_ms=ts_ms + 3,
    )


def _cfg(**overrides) -> QualityConfig:
    data = dict(
        gap_enabled=True,
        gap_threshold_sec=10.0,
        gap_active_window_sec=300,
        gap_active_min_ticks=3,
        gap_stall_warn_sec=30.0,
        trading_tz="Asia/Hong_Kong",
        trading_sessions_text="09:30-12:00,13:00-16:00",
        report_rel_dir="_reports/quality",
    )
    data.update(overrides)
    return QualityConfig(**data)


def test_hard_gap_detected_for_active_symbol() -> None:
    detector = GapDetector(_cfg())
    rows = [
        _row("HK.00700", "20260216", "09:30:00", 1),
        _row("HK.00700", "20260216", "09:30:01", 2),
        _row("HK.00700", "20260216", "09:30:02", 3),
        _row("HK.00700", "20260216", "09:30:20", 4),
    ]

    plan = detector.build_plan(rows)
    assert len(plan.hard_gaps) == 1
    assert plan.hard_gaps[0].symbol == "HK.00700"
    assert int(plan.hard_gaps[0].gap_sec) == 18


def test_no_hard_gap_for_inactive_symbol() -> None:
    detector = GapDetector(_cfg(gap_active_min_ticks=10))
    rows = [
        _row("HK.00981", "20260216", "09:30:00", 1),
        _row("HK.00981", "20260216", "09:30:25", 2),
    ]

    plan = detector.build_plan(rows)
    assert len(plan.hard_gaps) == 0


def test_no_gap_outside_trading_session() -> None:
    detector = GapDetector(_cfg(gap_active_min_ticks=2))
    rows = [
        _row("HK.00700", "20260216", "08:00:00", 1),
        _row("HK.00700", "20260216", "08:00:25", 2),
    ]

    plan = detector.build_plan(rows)
    assert len(plan.hard_gaps) == 0


def test_store_writes_gaps_with_single_writer(tmp_path) -> None:
    day = "20260216"
    detector = GapDetector(_cfg(gap_active_min_ticks=2, gap_threshold_sec=10.0))
    store = SQLiteTickStore(tmp_path, gap_detector=detector)
    rows = [
        _row("HK.00700", day, "09:30:00", 1),
        _row("HK.00700", day, "09:30:01", 2),
        _row("HK.00700", day, "09:30:20", 3),
    ]

    result = store.insert_ticks(day, rows)
    assert result.inserted == 3

    import sqlite3

    conn = sqlite3.connect(tmp_path / f"{day}.db")
    try:
        gap_count = conn.execute("SELECT COUNT(*) FROM gaps WHERE trading_day=?", (day,)).fetchone()[0]
    finally:
        conn.close()
    assert gap_count == 1
