from __future__ import annotations

import json
import sqlite3
import socket
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hk_tick_collector import __version__

from .config import QualityConfig, TradingSession

def quality_report_path(data_root: Path, trading_day: str, config: QualityConfig) -> Path:
    root = Path(data_root) / config.report_rel_dir
    return root / f"{trading_day}.json"


def generate_quality_report(
    *,
    data_root: Path,
    trading_day: str,
    quality_config: QualityConfig,
    db_path: Path | None = None,
    top_n: int = 20,
) -> dict[str, Any]:
    db = db_path or Path(data_root) / f"{trading_day}.db"
    now_ms = int(time.time() * 1000)
    warnings: list[str] = []
    rows_per_symbol: list[dict[str, Any]] = []
    gaps_by_symbol: list[dict[str, Any]] = []

    total_rows = 0
    start_ts_ms: int | None = None
    end_ts_ms: int | None = None
    hard_gaps_total = 0
    hard_gaps_total_sec = 0.0
    largest_gap_sec = 0.0

    if not db.exists():
        warnings.append("db_not_found")
    else:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            if not _table_exists(conn, "ticks"):
                warnings.append("ticks_table_missing")
            else:
                row = conn.execute(
                    "SELECT COUNT(*), MIN(ts_ms), MAX(ts_ms) FROM ticks WHERE trading_day=?",
                    (trading_day,),
                ).fetchone()
                total_rows = int(row[0] or 0)
                start_ts_ms = int(row[1]) if row[1] is not None else None
                end_ts_ms = int(row[2]) if row[2] is not None else None

                rows_per_symbol = [
                    {
                        "symbol": item[0],
                        "rows": int(item[1] or 0),
                        "latest_ts_ms": int(item[2]) if item[2] is not None else None,
                        "latest_hkt": _fmt_hkt_ms(
                            int(item[2]), tzinfo=quality_config.tzinfo
                        )
                        if item[2] is not None
                        else "n/a",
                    }
                    for item in conn.execute(
                        (
                            "SELECT symbol, COUNT(*) AS rows, MAX(ts_ms) AS latest_ts "
                            "FROM ticks WHERE trading_day=? GROUP BY symbol "
                            "ORDER BY rows DESC, symbol ASC LIMIT ?"
                        ),
                        (trading_day, max(1, int(top_n))),
                    ).fetchall()
                ]

            if not _table_exists(conn, "gaps"):
                warnings.append("gaps_table_missing")
            else:
                gap_row = conn.execute(
                    (
                        "SELECT COUNT(*), IFNULL(SUM(gap_sec), 0.0), IFNULL(MAX(gap_sec), 0.0) "
                        "FROM gaps WHERE trading_day=?"
                    ),
                    (trading_day,),
                ).fetchone()
                hard_gaps_total = int(gap_row[0] or 0)
                hard_gaps_total_sec = float(gap_row[1] or 0.0)
                largest_gap_sec = float(gap_row[2] or 0.0)

                gaps_by_symbol = [
                    {
                        "symbol": item[0],
                        "gaps": int(item[1] or 0),
                        "total_gap_sec": round(float(item[2] or 0.0), 3),
                        "largest_gap_sec": round(float(item[3] or 0.0), 3),
                    }
                    for item in conn.execute(
                        (
                            "SELECT symbol, COUNT(*) AS gaps, IFNULL(SUM(gap_sec),0.0), "
                            "IFNULL(MAX(gap_sec),0.0) FROM gaps WHERE trading_day=? "
                            "GROUP BY symbol ORDER BY gaps DESC, symbol ASC LIMIT ?"
                        ),
                        (trading_day, max(1, int(top_n))),
                    ).fetchall()
                ]

            soft_stall_stats = _compute_soft_stalls(
                conn=conn,
                trading_day=trading_day,
                quality_config=quality_config,
                top_n=top_n,
            )
        finally:
            conn.close()
    if not db.exists():
        soft_stall_stats = {
            "soft_stalls_total": 0,
            "soft_stalls_total_sec": 0.0,
            "largest_stall_sec": 0.0,
            "soft_stalls": [],
        }

    duration_sec = (
        round((end_ts_ms - start_ts_ms) / 1000.0, 3)
        if start_ts_ms is not None and end_ts_ms is not None and end_ts_ms >= start_ts_ms
        else 0.0
    )
    last_tick_age_sec = (
        round(max(0.0, (now_ms - end_ts_ms) / 1000.0), 3) if end_ts_ms is not None else None
    )
    grade, suggestions = _grade_quality(
        total_rows=total_rows,
        hard_gaps_total_sec=hard_gaps_total_sec,
        largest_gap_sec=largest_gap_sec,
        soft_stalls_total_sec=float(soft_stall_stats["soft_stalls_total_sec"]),
    )

    payload: dict[str, Any] = {
        "trading_day": _compact_to_dash(trading_day),
        "trading_day_compact": trading_day,
        "generated_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "generated_at_hkt": _fmt_hkt_ms(now_ms, tzinfo=quality_config.tzinfo),
        "host": socket.gethostname(),
        "collector_version": __version__,
        "db": {
            "path": str(db),
            "exists": db.exists(),
            "size_bytes": _file_size(db),
            "wal_size_bytes": _file_size(Path(f"{db}-wal")),
            "shm_size_bytes": _file_size(Path(f"{db}-shm")),
        },
        "coverage": {
            "start_ts_ms": start_ts_ms,
            "end_ts_ms": end_ts_ms,
            "start_hkt": _fmt_hkt_ms(start_ts_ms, tzinfo=quality_config.tzinfo)
            if start_ts_ms is not None
            else "n/a",
            "end_hkt": _fmt_hkt_ms(end_ts_ms, tzinfo=quality_config.tzinfo)
            if end_ts_ms is not None
            else "n/a",
            "duration_sec": duration_sec,
            "last_tick_age_sec": last_tick_age_sec,
        },
        "volume": {
            "total_rows": total_rows,
            "rows_per_symbol": rows_per_symbol,
        },
        "gaps": {
            "hard_gaps_total": hard_gaps_total,
            "hard_gaps_total_sec": round(hard_gaps_total_sec, 3),
            "largest_gap_sec": round(largest_gap_sec, 3),
            "gaps_by_symbol": gaps_by_symbol,
        },
        "observations": {
            "soft_stalls_total": int(soft_stall_stats["soft_stalls_total"]),
            "soft_stalls_total_sec": round(float(soft_stall_stats["soft_stalls_total_sec"]), 3),
            "largest_stall_sec": round(float(soft_stall_stats["largest_stall_sec"]), 3),
            "soft_stalls": soft_stall_stats["soft_stalls"],
            "warnings": warnings,
        },
        "conclusion": {
            "quality_grade": grade,
            "suggestions": suggestions,
        },
    }
    out_path = quality_report_path(Path(data_root), trading_day, quality_config)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _compute_soft_stalls(
    *,
    conn: sqlite3.Connection,
    trading_day: str,
    quality_config: QualityConfig,
    top_n: int,
) -> dict[str, Any]:
    if not _table_exists(conn, "ticks"):
        return {
            "soft_stalls_total": 0,
            "soft_stalls_total_sec": 0.0,
            "largest_stall_sec": 0.0,
            "soft_stalls": [],
        }

    active_window_ms = int(quality_config.gap_active_window_sec * 1000)
    sessions = quality_config.sessions
    tzinfo = quality_config.tzinfo

    current_symbol: str | None = None
    last_ts_ms: int | None = None
    recent: deque[int] = deque()
    total = 0
    total_sec = 0.0
    largest = 0.0
    top_hits: list[dict[str, Any]] = []

    cursor = conn.execute(
        "SELECT symbol, ts_ms FROM ticks WHERE trading_day=? ORDER BY symbol ASC, ts_ms ASC",
        (trading_day,),
    )
    for symbol, ts_value in cursor:
        ts_ms = int(ts_value)
        if current_symbol != symbol:
            current_symbol = symbol
            last_ts_ms = None
            recent = deque()

        min_ts = ts_ms - active_window_ms
        while recent and recent[0] < min_ts:
            recent.popleft()

        active = (len(recent) + 1) >= quality_config.gap_active_min_ticks
        if last_ts_ms is not None and ts_ms > last_ts_ms and active:
            prev_idx = _session_index(last_ts_ms, sessions=sessions, tzinfo=tzinfo)
            curr_idx = _session_index(ts_ms, sessions=sessions, tzinfo=tzinfo)
            if prev_idx is not None and curr_idx is not None and prev_idx == curr_idx:
                delta_sec = (ts_ms - last_ts_ms) / 1000.0
                if delta_sec > quality_config.gap_stall_warn_sec:
                    total += 1
                    total_sec += delta_sec
                    largest = max(largest, delta_sec)
                    top_hits.append(
                        {
                            "symbol": symbol,
                            "stall_sec": round(delta_sec, 3),
                            "stall_start_ts_ms": last_ts_ms,
                            "stall_end_ts_ms": ts_ms,
                            "stall_start_hkt": _fmt_hkt_ms(last_ts_ms, tzinfo=tzinfo),
                            "stall_end_hkt": _fmt_hkt_ms(ts_ms, tzinfo=tzinfo),
                        }
                    )

        if last_ts_ms is None or ts_ms > last_ts_ms:
            last_ts_ms = ts_ms
            recent.append(ts_ms)

    top_hits.sort(key=lambda item: item["stall_sec"], reverse=True)
    return {
        "soft_stalls_total": total,
        "soft_stalls_total_sec": round(total_sec, 3),
        "largest_stall_sec": round(largest, 3),
        "soft_stalls": top_hits[: max(1, int(top_n))],
    }


def _session_index(ts_ms: int, *, sessions: tuple[TradingSession, ...], tzinfo) -> int | None:
    local = datetime.fromtimestamp(ts_ms / 1000.0, tz=tzinfo)
    if local.weekday() >= 5:
        return None
    now = local.time().replace(tzinfo=None)
    for idx, session in enumerate(sessions):
        if session.start <= now < session.end:
            return idx
    return None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _grade_quality(
    *,
    total_rows: int,
    hard_gaps_total_sec: float,
    largest_gap_sec: float,
    soft_stalls_total_sec: float,
) -> tuple[str, list[str]]:
    if total_rows <= 0:
        return "D", ["ticks 為 0，請先確認採集服務與交易時段"]
    if largest_gap_sec > 120 or hard_gaps_total_sec > 900:
        return "D", ["存在嚴重缺口，建議盤後回補並重新驗證"]
    if largest_gap_sec > 60 or hard_gaps_total_sec > 300:
        return "C", ["存在 >60 秒缺口，建議執行回補或重拉指定 symbol"]
    if hard_gaps_total_sec > 0 or soft_stalls_total_sec > 120:
        return "B", ["有短暫停滯/缺口，回測前請先抽查主要 symbol"]
    return "A", ["資料連續性良好，可進入下游分析流程"]


def _compact_to_dash(day: str) -> str:
    text = str(day)
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _fmt_hkt_ms(ts_ms: int | None, *, tzinfo) -> str:
    if ts_ms is None:
        return "n/a"
    return datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc).astimezone(tzinfo).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0
