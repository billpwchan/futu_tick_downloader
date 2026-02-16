from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from hk_tick_collector.db import SQLiteTickStore


def _create_db_with_ticks(root: Path, day: str) -> Path:
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


def test_cli_validate_generates_report_and_passes(tmp_path: Path) -> None:
    day = "20260216"
    _create_db_with_ticks(tmp_path, day)
    report_path = tmp_path / "_reports" / "quality" / f"{day}.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hk_tick_collector.cli.main",
            "validate",
            "--data-root",
            str(tmp_path),
            "--day",
            day,
            "--regen-report",
            "1",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "VALIDATE" in result.stdout
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["validate"]["status"] in {"PASS", "WARN"}


def test_cli_validate_fails_when_db_missing(tmp_path: Path) -> None:
    day = "20260216"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hk_tick_collector.cli.main",
            "validate",
            "--data-root",
            str(tmp_path),
            "--day",
            day,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "FAIL" in result.stdout
