from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from hk_tick_collector.db import SQLiteTickStore

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "hk-tickctl"


def _prepare_db(root: Path, day: str) -> None:
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


def test_script_db_stats(tmp_path: Path) -> None:
    day = "20260216"
    _prepare_db(tmp_path, day)
    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "db",
            "stats",
            "--data-root",
            str(tmp_path),
            "--day",
            day,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "=== DB 統計 ===" in result.stdout
    assert "rows=1" in result.stdout


def test_script_status_and_export_report(tmp_path: Path) -> None:
    day = "20260216"
    _prepare_db(tmp_path, day)
    out = tmp_path / "quality.json"

    status = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "status",
            "--data-root",
            str(tmp_path),
            "--day",
            day,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert status.returncode == 0
    assert "=== HK Tick 狀態 ===" in status.stdout
    assert "ticks_total=1" in status.stdout

    export_report = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "export",
            "report",
            "--data-root",
            str(tmp_path),
            "--day",
            day,
            "--out",
            str(out),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert export_report.returncode == 0
    assert out.exists()


def test_script_export_gaps(tmp_path: Path) -> None:
    day = "20260216"
    _prepare_db(tmp_path, day)
    out = tmp_path / "gaps.csv"
    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "export",
            "gaps",
            "--data-root",
            str(tmp_path),
            "--day",
            day,
            "--out",
            str(out),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert out.exists()


def test_script_legacy_export_tar(tmp_path: Path) -> None:
    day = "20260216"
    _prepare_db(tmp_path, day)
    db_path = tmp_path / f"{day}.db"
    out = tmp_path / "legacy.tar.gz"
    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "export",
            "--db",
            str(db_path),
            "--out",
            str(out),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert out.exists()
    assert Path(f"{out}.sha256").exists()
