from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from hk_tick_collector.archive import archiver
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


@pytest.mark.parametrize("damage", ["archive", "checksum", "manifest"])
def test_retention_keeps_source_when_archive_metadata_is_damaged(
    tmp_path: Path, damage: str
) -> None:
    old_day, new_day = "20260215", "20260216"
    old_db = _prepare_db(tmp_path, old_day)
    _prepare_db(tmp_path, new_day)
    archive_dir = tmp_path / "archive"
    old = archive_daily_db(
        trading_day=old_day,
        data_root=tmp_path,
        archive_dir=archive_dir,
        verify=True,
        quality_config=_quality_cfg(),
        compression="none",
    )
    if damage == "archive":
        old.archive_file.write_bytes(b"damaged")
    elif damage == "checksum":
        old.checksum_file.write_text("incorrect checksum\n", encoding="utf-8")
    else:
        old.manifest_file.write_text("{}", encoding="utf-8")

    archive_daily_db(
        trading_day=new_day,
        data_root=tmp_path,
        archive_dir=archive_dir,
        keep_days=1,
        delete_original=True,
        verify=True,
        quality_config=_quality_cfg(),
        compression="none",
    )
    assert old_db.exists()


def test_archive_rejects_deletion_without_verification(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires verify"):
        archive_daily_db(
            trading_day="20260216",
            data_root=tmp_path,
            archive_dir=tmp_path / "archive",
            delete_original=True,
            verify=False,
        )


def test_failed_rerun_preserves_previous_archive(tmp_path: Path, monkeypatch) -> None:
    day = "20260216"
    _prepare_db(tmp_path, day)
    archive_dir = tmp_path / "archive"
    first = archive_daily_db(
        trading_day=day,
        data_root=tmp_path,
        archive_dir=archive_dir,
        verify=True,
        quality_config=_quality_cfg(),
        compression="none",
    )
    before = tuple(
        path.read_bytes() for path in (first.archive_file, first.checksum_file, first.manifest_file)
    )
    monkeypatch.setattr(archiver, "_verify_archive", lambda **kwargs: (False, {"error": "test"}))

    with pytest.raises(RuntimeError, match="archive verify failed"):
        archive_daily_db(
            trading_day=day,
            data_root=tmp_path,
            archive_dir=archive_dir,
            verify=True,
            quality_config=_quality_cfg(),
            compression="none",
        )
    assert before == tuple(
        path.read_bytes() for path in (first.archive_file, first.checksum_file, first.manifest_file)
    )
