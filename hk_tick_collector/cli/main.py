from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from hk_tick_collector.archive.archiver import archive_daily_db, backup_sqlite_db
from hk_tick_collector.quality.config import QualityConfig
from hk_tick_collector.quality.report import generate_quality_report, quality_report_path

HK_TZ = ZoneInfo("Asia/Hong_Kong")


def _today_day() -> str:
    return datetime.now(tz=HK_TZ).strftime("%Y%m%d")


def _parse_symbols(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _db_path(*, data_root: Path, day: str, db_arg: str | None = None) -> Path:
    if db_arg:
        return Path(db_arg)
    return Path(data_root) / f"{day}.db"


def _size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _fmt_hkt(ts_ms: int | None, tzinfo: ZoneInfo) -> str:
    if ts_ms is None:
        return "n/a"
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).astimezone(tzinfo).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _systemd_active(service: str) -> str:
    try:
        completed = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return "n/a"
    output = (completed.stdout or completed.stderr or "").strip()
    return output or "unknown"


def _service_name() -> str:
    return (os.getenv("SERVICE_NAME", "hk-tick-collector") or "hk-tick-collector").strip()


def _journal_lines(*, service: str, since: str) -> list[str]:
    try:
        completed = subprocess.run(
            ["journalctl", "-u", service, "--since", since, "--no-pager"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    output = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
    return [line for line in output.splitlines() if line.strip()]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_status(args: argparse.Namespace) -> int:
    day = args.day or _today_day()
    data_root = Path(args.data_root)
    db_path = _db_path(data_root=data_root, day=day)
    qcfg = QualityConfig.from_env()
    service_name = _service_name()

    print("=== HK Tick 狀態 ===")
    print(f"day={day}")
    print(f"db_path={db_path}")
    print(f"db_size={_size(db_path)}")
    print(f"wal_size={_size(Path(f'{db_path}-wal'))}")
    print(f"service.{service_name}={_systemd_active(service_name)}")
    print(f"service.futu-opend={_systemd_active('futu-opend')}")
    logs = _journal_lines(service=service_name, since=args.since)
    health_line = next((line for line in reversed(logs) if "health sid=" in line), "")
    alert_line = next(
        (
            line
            for line in reversed(logs)
            if re.search(r"severity=(WARN|ALERT)|WATCHDOG|alert_event code=", line)
        ),
        "",
    )
    print(f"[健康] {health_line if health_line else '選定時間內沒有 health 訊息'}")
    print(f"[告警] {alert_line if alert_line else '選定時間內無 WARN/ALERT/WATCHDOG'}")

    if not db_path.exists():
        print("result=FAIL reason=db_not_found")
        return 2

    symbols_filter = set(_parse_symbols(args.symbols))
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if not _table_exists(conn, "ticks"):
            print("result=FAIL reason=ticks_table_missing")
            return 2

        total_rows, min_ts, max_ts = conn.execute(
            "SELECT COUNT(*), MIN(ts_ms), MAX(ts_ms) FROM ticks WHERE trading_day=?",
            (day,),
        ).fetchone()
        now_ms = int(time.time() * 1000)
        age_sec = (
            round(max(0.0, (now_ms - int(max_ts)) / 1000.0), 3) if max_ts is not None else None
        )
        print(f"ticks_total={int(total_rows or 0)}")
        print(f"start_hkt={_fmt_hkt(int(min_ts) if min_ts is not None else None, qcfg.tzinfo)}")
        print(f"end_hkt={_fmt_hkt(int(max_ts) if max_ts is not None else None, qcfg.tzinfo)}")
        print(f"last_tick_age_sec={age_sec if age_sec is not None else 'n/a'}")

        rows = conn.execute(
            (
                "SELECT symbol, COUNT(*) AS rows, MAX(ts_ms) AS latest_ts "
                "FROM ticks WHERE trading_day=? GROUP BY symbol ORDER BY rows DESC, symbol ASC LIMIT 20"
            ),
            (day,),
        ).fetchall()
        print("rows_by_symbol_top:")
        for symbol, count, latest in rows:
            if symbols_filter and symbol not in symbols_filter:
                continue
            lag = (
                round(max(0.0, (now_ms - int(latest)) / 1000.0), 3)
                if latest is not None
                else None
            )
            print(
                f"  {symbol} rows={int(count or 0)} latest_hkt={_fmt_hkt(int(latest) if latest else None, qcfg.tzinfo)} "
                f"last_tick_age_sec={lag if lag is not None else 'n/a'}"
            )

        if _table_exists(conn, "gaps"):
            gaps_total, max_gap = conn.execute(
                "SELECT COUNT(*), IFNULL(MAX(gap_sec),0.0) FROM gaps WHERE trading_day=?",
                (day,),
            ).fetchone()
            print(f"gaps_total={int(gaps_total or 0)}")
            print(f"largest_gap_sec={round(float(max_gap or 0.0), 3)}")
        else:
            print("gaps_total=n/a")
            print("largest_gap_sec=n/a")
    finally:
        conn.close()
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    day = args.day or _today_day()
    strict = bool(int(args.strict))
    regen_report = bool(int(args.regen_report))
    data_root = Path(args.data_root)
    qcfg = QualityConfig.from_env()
    db_path = _db_path(data_root=data_root, day=day)
    report_path = quality_report_path(data_root, day, qcfg)

    reasons: list[str] = []
    warnings: list[str] = []
    status = "PASS"
    now_ms = int(time.time() * 1000)
    hard_gaps_total_sec = 0.0
    largest_gap_sec = 0.0

    if not db_path.exists():
        reasons.append(f"db_not_found:{db_path}")
        status = "FAIL"
    else:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            if not _table_exists(conn, "ticks"):
                reasons.append("ticks_table_missing")
                status = "FAIL"
            else:
                row_count, max_ts = conn.execute(
                    "SELECT COUNT(*), MAX(ts_ms) FROM ticks WHERE trading_day=?",
                    (day,),
                ).fetchone()
                row_count = int(row_count or 0)
                max_ts_value = int(max_ts) if max_ts is not None else None
                if row_count <= 0:
                    reasons.append("ticks_count_is_zero")
                    status = "FAIL"
                if max_ts_value is None:
                    reasons.append("ticks_max_ts_missing")
                    status = "FAIL"
                elif max_ts_value > now_ms + 300_000:
                    reasons.append("ticks_max_ts_too_far_in_future")
                    status = "FAIL"
                elif max_ts_value > now_ms + 60_000:
                    warnings.append("ticks_max_ts_slightly_in_future")

            if _table_exists(conn, "gaps"):
                hard_gaps_total_sec, largest_gap_sec = conn.execute(
                    "SELECT IFNULL(SUM(gap_sec),0.0), IFNULL(MAX(gap_sec),0.0) FROM gaps WHERE trading_day=?",
                    (day,),
                ).fetchone()
                hard_gaps_total_sec = float(hard_gaps_total_sec or 0.0)
                largest_gap_sec = float(largest_gap_sec or 0.0)
            else:
                warnings.append("gaps_table_missing")
        finally:
            conn.close()

    if strict and status != "FAIL":
        if largest_gap_sec > 60.0 or hard_gaps_total_sec > 300.0:
            reasons.append("strict_mode_gap_threshold_exceeded")
            status = "FAIL"
        elif hard_gaps_total_sec > 0:
            warnings.append("strict_mode_gap_observed")

    need_report = regen_report or not report_path.exists()
    if need_report and db_path.exists():
        report = generate_quality_report(
            data_root=data_root,
            trading_day=day,
            quality_config=qcfg,
            db_path=db_path,
        )
    elif report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    else:
        report = {}
        warnings.append("quality_report_not_generated")

    if status != "FAIL" and warnings:
        status = "WARN"

    report["validate"] = {
        "status": status,
        "strict": strict,
        "checked_at_utc": datetime.now(tz=timezone.utc).isoformat(),
        "reasons": reasons,
        "warnings": warnings,
        "details": {
            "largest_gap_sec": round(largest_gap_sec, 3),
            "hard_gaps_total_sec": round(hard_gaps_total_sec, 3),
        },
    }
    _write_json(report_path, report)

    print(f"VALIDATE {status} day={day}")
    print(f"db={db_path}")
    print(f"report={report_path}")
    for item in reasons:
        print(f"reason={item}")
    for item in warnings:
        print(f"warning={item}")
    if status != "FAIL":
        print(f"next=scripts/hk-tickctl export report --day {day} --out quality_{day}.json")
    else:
        print(f"next=scripts/hk-tickctl status --day {day}")
    return 2 if status == "FAIL" else 0


def cmd_logs(args: argparse.Namespace) -> int:
    service_name = _service_name()
    logs = _journal_lines(service=service_name, since=args.since)
    if not logs:
        return 0
    pattern = (
        re.compile(
            r"health|health_symbols_rollup|persist_summary|WATCHDOG|ERROR|Traceback|telegram_|sqlite_busy|poll_stats|persist_ticks",
            re.IGNORECASE,
        )
        if bool(int(args.ops))
        else re.compile(
            r"health|persist_summary|WATCHDOG|ERROR|Traceback|telegram_send_failed|telegram_alert_suppressed",
            re.IGNORECASE,
        )
    )
    selected = [line for line in logs if pattern.search(line)]
    for line in selected:
        print(line)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    service_name = _service_name()
    logs = _journal_lines(service=service_name, since=args.since)
    schema_line = next(
        (line for line in reversed(logs) if "telegram_notifier_started" in line and "notify_schema=" in line),
        "",
    )
    health_enqueue = next(
        (line for line in reversed(logs) if "telegram_enqueue kind=HEALTH" in line),
        "",
    )
    warn_enqueue = next(
        (
            line
            for line in reversed(logs)
            if re.search(r"telegram_enqueue kind=.* severity=(WARN|ALERT)", line)
        ),
        "",
    )
    print("=== HK Tick 版本診斷 ===")
    print(f"service={service_name} status={_systemd_active(service_name)} since={args.since}")
    if schema_line:
        print("[完成] 找到 notify schema 訊息")
        print(schema_line)
    else:
        print("[資訊] 沒找到 notify schema（可能未啟用 TG 或版本較舊）")
    if health_enqueue:
        print("[資訊] 最近 HEALTH enqueue")
        print(health_enqueue)
    if warn_enqueue:
        print("[資訊] 最近 WARN/ALERT enqueue")
        print(warn_enqueue)
    return 0


def cmd_tg_test(args: argparse.Namespace) -> int:
    token = (args.token or os.getenv("TG_TOKEN") or os.getenv("TG_BOT_TOKEN") or "").strip()
    chat_id = (args.chat_id or os.getenv("TG_CHAT_ID") or "").strip()
    thread_id = (args.thread_id or os.getenv("TG_MESSAGE_THREAD_ID") or "").strip()
    text = (
        args.text
        or "✅ HK Tick Collector 測試通知\n結論：Telegram 設定可用\n建議：可開始驗證 health / alert 訊息"
    )
    if not token:
        print("FAIL 缺少 TG_TOKEN（可用 env 或 --token）")
        return 2
    if not chat_id:
        print("FAIL 缺少 TG_CHAT_ID（可用 env 或 --chat-id）")
        return 2

    payload: dict[str, object] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if thread_id:
        try:
            payload["message_thread_id"] = int(thread_id)
        except ValueError:
            print("FAIL thread_id 需為整數")
            return 2

    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL Telegram 呼叫失敗: {exc}")
        return 1

    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        print("FAIL Telegram 回傳非 JSON")
        return 1
    if not result.get("ok"):
        print(f"FAIL Telegram API 回應: {body}")
        return 1
    message_id = result.get("result", {}).get("message_id", "n/a")
    print(f"OK Telegram 測試訊息已送出 message_id={message_id}")
    return 0


def cmd_export_db(args: argparse.Namespace) -> int:
    day = args.day or _today_day()
    db_path = _db_path(data_root=Path(args.data_root), day=day)
    if not db_path.exists():
        print(f"FAIL db_not_found {db_path}")
        return 2

    out = Path(args.out) if args.out else Path.cwd() / f"{day}.backup.db"
    if out.exists() and out.is_dir():
        out = out / f"{day}.backup.db"
    if not out.suffix:
        out = out / f"{day}.backup.db"
    backup_sqlite_db(db_path, out)
    print(f"OK export_db={out}")
    print(f"verify=sqlite3 {out} \"SELECT COUNT(*) FROM ticks;\"")
    return 0


def cmd_export_legacy(args: argparse.Namespace) -> int:
    day = args.day or _today_day()
    db_path = _db_path(data_root=Path(args.data_root), day=day, db_arg=args.db)
    if not db_path.exists():
        print(f"FAIL db_not_found {db_path}")
        return 2

    out = Path(args.out) if args.out else Path("/tmp") / f"hk-{day}.tar.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    files = [db_path]
    wal = Path(f"{db_path}-wal")
    shm = Path(f"{db_path}-shm")
    if wal.exists():
        files.append(wal)
    if shm.exists():
        files.append(shm)
    with tarfile.open(out, "w:gz") as tar:
        for file in files:
            tar.add(file, arcname=file.name)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    checksum_path = Path(f"{out}.sha256")
    checksum_path.write_text(f"{digest}  {out.name}\n", encoding="utf-8")
    print(f"OK legacy_export={out}")
    print(f"sha256={digest}")
    print(f"checksum_file={checksum_path}")
    return 0


def cmd_export_gaps(args: argparse.Namespace) -> int:
    day = args.day or _today_day()
    db_path = _db_path(data_root=Path(args.data_root), day=day)
    if not db_path.exists():
        print(f"FAIL db_not_found {db_path}")
        return 2

    out = Path(args.out) if args.out else Path.cwd() / f"gaps_{day}.csv"
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if not _table_exists(conn, "gaps"):
            print("FAIL gaps_table_missing")
            return 2
        rows = conn.execute(
            (
                "SELECT trading_day, symbol, gap_start_ts_ms, gap_end_ts_ms, gap_sec, detected_at_ms, reason, meta_json "
                "FROM gaps WHERE trading_day=? ORDER BY symbol, gap_start_ts_ms"
            ),
            (day,),
        ).fetchall()
    finally:
        conn.close()

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "trading_day",
                "symbol",
                "gap_start_ts_ms",
                "gap_end_ts_ms",
                "gap_sec",
                "detected_at_ms",
                "reason",
                "meta_json",
            ]
        )
        writer.writerows(rows)
    print(f"OK export_gaps={out}")
    return 0


def cmd_export_report(args: argparse.Namespace) -> int:
    day = args.day or _today_day()
    data_root = Path(args.data_root)
    qcfg = QualityConfig.from_env()
    report_path = quality_report_path(data_root, day, qcfg)
    if not report_path.exists():
        db_path = _db_path(data_root=data_root, day=day)
        if not db_path.exists():
            print(f"FAIL db_not_found {db_path}")
            return 2
        generate_quality_report(
            data_root=data_root,
            trading_day=day,
            quality_config=qcfg,
            db_path=db_path,
        )
    out = Path(args.out) if args.out else Path.cwd() / f"quality_{day}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(report_path, out)
    print(f"OK export_report={out}")
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    day = args.day or _today_day()
    data_root = Path(args.data_root)
    archive_dir = Path(args.archive_dir) if args.archive_dir else data_root / "_archive"
    qcfg = QualityConfig.from_env()
    result = archive_daily_db(
        trading_day=day,
        data_root=data_root,
        archive_dir=archive_dir,
        keep_days=int(args.keep_days),
        delete_original=bool(int(args.delete_original)),
        verify=bool(int(args.verify)),
        quality_config=qcfg,
    )
    print("ARCHIVE OK")
    print(f"archive={result.archive_file}")
    print(f"checksum={result.checksum_file}")
    print(f"manifest={result.manifest_file}")
    print(f"report={result.report_file}")
    print(f"verified={int(result.verified)}")
    print(f"deleted_original={int(result.deleted_original)}")
    return 0


def cmd_db_stats(args: argparse.Namespace) -> int:
    day = args.day or _today_day()
    db_path = _db_path(data_root=Path(args.data_root), day=day, db_arg=args.db)
    if not db_path.exists():
        print(f"FAIL db_not_found {db_path}")
        return 2
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if not _table_exists(conn, "ticks"):
            print("FAIL ticks_table_missing")
            return 2
        rows, max_ts = conn.execute("SELECT COUNT(*), MAX(ts_ms) FROM ticks").fetchone()
        index_count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND tbl_name='ticks'"
        ).fetchone()[0]
        page_bytes = conn.execute(
            "SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()"
        ).fetchone()[0]
    finally:
        conn.close()

    print("=== DB 統計 ===")
    print(f"db={db_path}")
    print(f"rows={int(rows or 0)}")
    print(f"max_ts_ms={int(max_ts) if max_ts is not None else 0}")
    print(f"indices={int(index_count or 0)}")
    print(f"approx_bytes_sqlite={int(page_bytes or 0)}")
    print(f"file_bytes_db={_size(db_path)}")
    print(f"file_bytes_wal={_size(Path(f'{db_path}-wal'))}")
    print(f"file_bytes_shm={_size(Path(f'{db_path}-shm'))}")
    return 0


def cmd_db_symbols(args: argparse.Namespace) -> int:
    day = args.day or _today_day()
    db_path = _db_path(data_root=Path(args.data_root), day=day, db_arg=args.db)
    if not db_path.exists():
        print(f"FAIL db_not_found {db_path}")
        return 2
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            (
                "SELECT symbol, COUNT(*) AS rows, MAX(ts_ms) AS latest_ts "
                "FROM ticks GROUP BY symbol ORDER BY symbol"
            )
        ).fetchall()
    finally:
        conn.close()
    now_ms = int(time.time() * 1000)
    print("symbol,rows,latest_ts_hkt,last_tick_age_sec")
    for symbol, count, latest in rows:
        lag = round(max(0.0, (now_ms - int(latest)) / 1000.0), 3) if latest is not None else None
        print(f"{symbol},{int(count or 0)},{_fmt_hkt(int(latest) if latest else None, HK_TZ)},{lag}")
    return 0


def cmd_db_symbol(args: argparse.Namespace) -> int:
    day = args.day or _today_day()
    db_path = _db_path(data_root=Path(args.data_root), day=day, db_arg=args.db)
    if not db_path.exists():
        print(f"FAIL db_not_found {db_path}")
        return 2
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            (
                "SELECT symbol, ts_ms, price, volume, turnover, seq FROM ticks "
                "WHERE symbol=? ORDER BY ts_ms DESC LIMIT ?"
            ),
            (args.symbol, int(args.last)),
        ).fetchall()
    finally:
        conn.close()
    print("symbol,ts_hkt,price,volume,turnover,seq")
    for symbol, ts_ms, price, volume, turnover, seq in rows:
        print(f"{symbol},{_fmt_hkt(int(ts_ms), HK_TZ)},{price},{volume},{turnover},{seq}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hk-tickctl", description="HK Tick Collector CLI")

    sub = parser.add_subparsers(dest="command", required=True)

    def add_data_root_arg(target: argparse.ArgumentParser) -> None:
        target.add_argument("--data-root", default="/data/sqlite/HK", help="SQLite daily DB 目錄")

    p_status = sub.add_parser("status", help="快速查看資料可用性")
    add_data_root_arg(p_status)
    p_status.add_argument("--day", default=None)
    p_status.add_argument("--symbols", default="")
    p_status.add_argument("--since", default="20 minutes ago")
    p_status.set_defaults(func=cmd_status)

    p_logs = sub.add_parser("logs", help="查看關鍵 journald")
    p_logs.add_argument("--ops", default="0", choices=["0", "1"])
    p_logs.add_argument("--since", default="15 minutes ago")
    p_logs.add_argument("--no-follow", default="1", choices=["0", "1"])
    p_logs.set_defaults(func=cmd_logs)

    p_doctor = sub.add_parser("doctor", help="診斷 Telegram 與服務訊號")
    p_doctor.add_argument("--since", default="6 hours ago")
    p_doctor.set_defaults(func=cmd_doctor)

    p_validate = sub.add_parser("validate", help="驗證今日資料是否可用")
    add_data_root_arg(p_validate)
    p_validate.add_argument("--day", default=None)
    p_validate.add_argument("--regen-report", default="0", choices=["0", "1"])
    p_validate.add_argument("--strict", default="0", choices=["0", "1"])
    p_validate.set_defaults(func=cmd_validate)

    p_export = sub.add_parser("export", help="匯出資料")
    add_data_root_arg(p_export)
    p_export.add_argument("--db", default=None, help="legacy: 指定 DB 路徑")
    p_export.add_argument("--day", default=None, help="legacy: 指定 YYYYMMDD")
    p_export.add_argument("--out", default=None, help="legacy: 輸出 tar.gz")
    p_export.set_defaults(func=cmd_export_legacy)
    export_sub = p_export.add_subparsers(dest="export_kind")

    p_export_db = export_sub.add_parser("db", help="匯出一致性 backup DB")
    p_export_db.add_argument("--day", default=None)
    p_export_db.add_argument("--out", default=None)
    p_export_db.set_defaults(func=cmd_export_db)

    p_export_gaps = export_sub.add_parser("gaps", help="匯出 gaps CSV")
    p_export_gaps.add_argument("--day", default=None)
    p_export_gaps.add_argument("--out", default=None)
    p_export_gaps.set_defaults(func=cmd_export_gaps)

    p_export_report = export_sub.add_parser("report", help="匯出 quality report JSON")
    p_export_report.add_argument("--day", default=None)
    p_export_report.add_argument("--out", default=None)
    p_export_report.set_defaults(func=cmd_export_report)

    p_archive = sub.add_parser("archive", help="盤後歸檔")
    add_data_root_arg(p_archive)
    p_archive.add_argument("--day", default=None)
    p_archive.add_argument("--archive-dir", default=None)
    p_archive.add_argument("--keep-days", default=14, type=int)
    p_archive.add_argument("--delete-original", default="0", choices=["0", "1"])
    p_archive.add_argument("--verify", default="1", choices=["0", "1"])
    p_archive.set_defaults(func=cmd_archive)

    p_db = sub.add_parser("db", help="相容舊版 db 查詢命令")
    db_sub = p_db.add_subparsers(dest="db_command", required=True)

    p_db_stats = db_sub.add_parser("stats", help="DB 總體統計")
    add_data_root_arg(p_db_stats)
    p_db_stats.add_argument("--db", default=None)
    p_db_stats.add_argument("--day", default=None)
    p_db_stats.set_defaults(func=cmd_db_stats)

    p_db_symbols = db_sub.add_parser("symbols", help="各 symbol 統計")
    add_data_root_arg(p_db_symbols)
    p_db_symbols.add_argument("--db", default=None)
    p_db_symbols.add_argument("--day", default=None)
    p_db_symbols.add_argument("--minutes", default=10, type=int)
    p_db_symbols.set_defaults(func=cmd_db_symbols)

    p_db_symbol = db_sub.add_parser("symbol", help="symbol 最新 ticks")
    add_data_root_arg(p_db_symbol)
    p_db_symbol.add_argument("symbol")
    p_db_symbol.add_argument("--db", default=None)
    p_db_symbol.add_argument("--day", default=None)
    p_db_symbol.add_argument("--last", default=20, type=int)
    p_db_symbol.set_defaults(func=cmd_db_symbol)

    p_tg = sub.add_parser("tg", help="Telegram 測試工具")
    tg_sub = p_tg.add_subparsers(dest="tg_command", required=True)
    p_tg_test = tg_sub.add_parser("test", help="送出 Telegram 測試訊息")
    p_tg_test.add_argument("--token", default="")
    p_tg_test.add_argument("--chat-id", default="")
    p_tg_test.add_argument("--thread-id", default="")
    p_tg_test.add_argument("--text", default="")
    p_tg_test.set_defaults(func=cmd_tg_test)

    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(argv) if argv is not None else list(sys.argv[1:])
    if raw[:1] == ["export"] and len(raw) >= 2 and raw[1] in {"db", "gaps", "report"}:
        kind = raw[1]
        rest = raw[2:]
        if "--data-root" in rest:
            idx = rest.index("--data-root")
            if idx + 1 < len(rest):
                value = rest[idx + 1]
                rest = rest[:idx] + rest[idx + 2 :]
                raw = ["export", "--data-root", value, kind, *rest]
    parser = build_parser()
    args = parser.parse_args(raw)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
