from __future__ import annotations

import sqlite3
from pathlib import Path

from hk_tick_collector.db import SQLiteTickStore
from hk_tick_collector.quality.config import QualityConfig
from hk_tick_collector.quality.report import generate_quality_report, quality_report_path


def test_generate_quality_report_has_required_fields(tmp_path: Path) -> None:
    day = "20260216"
    store = SQLiteTickStore(tmp_path)
    db_path = store.ensure_db(day)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            (
                "INSERT INTO ticks (market,symbol,ts_ms,price,volume,turnover,direction,seq,tick_type,"
                "push_type,provider,trading_day,recv_ts_ms,inserted_at_ms) "
                "VALUES ('HK','HK.00700',1708056600000,300.0,100,30000.0,'BUY',1,'AUTO_MATCH',"
                "'push','futu',?,1708056600001,1708056600001)"
            ),
            (day,),
        )
        conn.execute(
            (
                "INSERT INTO ticks (market,symbol,ts_ms,price,volume,turnover,direction,seq,tick_type,"
                "push_type,provider,trading_day,recv_ts_ms,inserted_at_ms) "
                "VALUES ('HK','HK.00700',1708056605000,301.0,200,60200.0,'BUY',2,'AUTO_MATCH',"
                "'push','futu',?,1708056605001,1708056605001)"
            ),
            (day,),
        )
        conn.execute(
            (
                "INSERT INTO gaps (trading_day,symbol,gap_start_ts_ms,gap_end_ts_ms,gap_sec,"
                "detected_at_ms,reason,meta_json) VALUES (?,?,?,?,?,?,?,?)"
            ),
            (day, "HK.00700", 1708056600000, 1708056605000, 5.0, 1708056605002, "hard_gap", "{}"),
        )
        conn.commit()
    finally:
        conn.close()

    cfg = QualityConfig(
        gap_enabled=True,
        gap_threshold_sec=10.0,
        gap_active_window_sec=300,
        gap_active_min_ticks=2,
        gap_stall_warn_sec=3.0,
        trading_tz="Asia/Hong_Kong",
        trading_sessions_text="09:30-12:00,13:00-16:00",
        report_rel_dir="_reports/quality",
    )
    payload = generate_quality_report(
        data_root=tmp_path,
        trading_day=day,
        quality_config=cfg,
        db_path=db_path,
    )
    out_path = quality_report_path(tmp_path, day, cfg)

    assert out_path.exists()
    assert payload["trading_day_compact"] == day
    assert payload["volume"]["total_rows"] == 2
    assert payload["gaps"]["hard_gaps_total"] == 1
    assert "quality_grade" in payload["conclusion"]
