from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from hk_tick_collector.archive.archiver import archive_daily_db
from hk_tick_collector.db import SQLiteTickStore
from hk_tick_collector.quality.config import QualityConfig


def _prepare_db(root: Path, day: str) -> Path:
    store = SQLiteTickStore(root)
    db = store.ensure_db(day)
    conn = sqlite3.connect(db)
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
        conn.commit()
    finally:
        conn.close()
    return db


def _quality_cfg() -> QualityConfig:
    return QualityConfig(
        gap_enabled=True,
        gap_threshold_sec=10.0,
        gap_active_window_sec=300,
        gap_active_min_ticks=2,
        gap_stall_warn_sec=5.0,
        trading_tz="Asia/Hong_Kong",
        trading_sessions_text="09:30-12:00,13:00-16:00",
        report_rel_dir="_reports/quality",
    )


def test_archive_generates_artifacts_with_checksum_and_manifest(tmp_path: Path) -> None:
    day = "20260216"
    _prepare_db(tmp_path, day)
    archive_dir = tmp_path / "archive"

    result = archive_daily_db(
        trading_day=day,
        data_root=tmp_path,
        archive_dir=archive_dir,
        keep_days=7,
        delete_original=False,
        verify=True,
        quality_config=_quality_cfg(),
        compression="none",
    )

    assert result.archive_file.exists()
    assert result.checksum_file.exists()
    assert result.manifest_file.exists()
    manifest = json.loads(result.manifest_file.read_text(encoding="utf-8"))
    assert manifest["trading_day"] == day
    assert manifest["verify_ok"] is True


def test_archive_retention_delete_original_when_verified(tmp_path: Path) -> None:
    day_old = "20260215"
    day_new = "20260216"
    old_db = _prepare_db(tmp_path, day_old)
    _prepare_db(tmp_path, day_new)
    archive_dir = tmp_path / "archive"

    archive_daily_db(
        trading_day=day_old,
        data_root=tmp_path,
        archive_dir=archive_dir,
        keep_days=1,
        delete_original=False,
        verify=True,
        quality_config=_quality_cfg(),
        compression="none",
    )
    archive_daily_db(
        trading_day=day_new,
        data_root=tmp_path,
        archive_dir=archive_dir,
        keep_days=1,
        delete_original=True,
        verify=True,
        quality_config=_quality_cfg(),
        compression="none",
    )

    assert not old_db.exists()
