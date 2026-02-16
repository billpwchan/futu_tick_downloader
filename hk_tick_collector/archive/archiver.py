from __future__ import annotations

import hashlib
import json
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hk_tick_collector import __version__

from ..quality.config import QualityConfig
from ..quality.report import generate_quality_report


@dataclass(frozen=True)
class ArchiveResult:
    trading_day: str
    source_db: Path
    archive_file: Path
    checksum_file: Path
    manifest_file: Path
    verified: bool
    deleted_original: bool
    report_file: Path


def backup_sqlite_db(source_db: Path, backup_db: Path) -> None:
    source = Path(source_db)
    target = Path(backup_db)
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src_conn:
        with sqlite3.connect(target) as dst_conn:
            dst_conn.execute("PRAGMA journal_mode=DELETE;")
            src_conn.backup(dst_conn)
            dst_conn.commit()


def archive_daily_db(
    *,
    trading_day: str,
    data_root: Path,
    archive_dir: Path,
    keep_days: int = 14,
    delete_original: bool = False,
    verify: bool = True,
    quality_config: QualityConfig | None = None,
    compression: str = "zstd",
) -> ArchiveResult:
    quality_cfg = quality_config or QualityConfig.from_env()
    db_path = Path(data_root) / f"{trading_day}.db"
    if not db_path.exists():
        raise FileNotFoundError(f"db not found: {db_path}")

    archive_root = Path(archive_dir)
    archive_root.mkdir(parents=True, exist_ok=True)
    archive_file = archive_root / f"{trading_day}.db.zst"
    checksum_file = archive_root / f"{trading_day}.db.zst.sha256"
    manifest_file = archive_root / "manifest" / f"{trading_day}.json"
    manifest_file.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"hk-archive-{trading_day}-") as tmp_dir:
        tmp_backup = Path(tmp_dir) / f"{trading_day}.backup.db"
        backup_sqlite_db(db_path, tmp_backup)
        _compress_backup(source=tmp_backup, out=archive_file, compression=compression)

        checksum = _sha256_file(archive_file)
        checksum_file.write_text(
            f"{checksum}  {archive_file.name}\n",
            encoding="utf-8",
        )

        report = generate_quality_report(
            data_root=Path(data_root),
            trading_day=trading_day,
            quality_config=quality_cfg,
            db_path=db_path,
        )
        report_file = Path(data_root) / quality_cfg.report_rel_dir / f"{trading_day}.json"

        verify_ok = True
        verify_details: dict[str, Any] = {}
        if verify:
            verify_ok, verify_details = _verify_archive(
                archive_file=archive_file, compression=compression
            )
            if not verify_ok:
                raise RuntimeError(f"archive verify failed: {verify_details}")

        manifest_payload = {
            "trading_day": trading_day,
            "created_at_ms": int(time.time() * 1000),
            "host": socket.gethostname(),
            "collector_version": __version__,
            "source_db": str(db_path),
            "archive_file": str(archive_file),
            "archive_size_bytes": _file_size(archive_file),
            "checksum_sha256": checksum,
            "compression": compression,
            "verify_enabled": bool(verify),
            "verify_ok": bool(verify_ok),
            "verify_details": verify_details,
            "quality_summary": {
                "total_rows": report.get("volume", {}).get("total_rows", 0),
                "start_ts_ms": report.get("coverage", {}).get("start_ts_ms"),
                "end_ts_ms": report.get("coverage", {}).get("end_ts_ms"),
                "hard_gaps_total": report.get("gaps", {}).get("hard_gaps_total", 0),
                "hard_gaps_total_sec": report.get("gaps", {}).get("hard_gaps_total_sec", 0.0),
                "quality_grade": report.get("conclusion", {}).get("quality_grade", "n/a"),
            },
        }
        manifest_file.write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    deleted = False
    if delete_original:
        _cleanup_original_db_files_for_retention(
            data_root=Path(data_root),
            archive_dir=archive_root,
            keep_days=max(0, int(keep_days)),
        )
        deleted = not db_path.exists()

    return ArchiveResult(
        trading_day=trading_day,
        source_db=db_path,
        archive_file=archive_file,
        checksum_file=checksum_file,
        manifest_file=manifest_file,
        verified=bool(verify_ok),
        deleted_original=deleted,
        report_file=report_file,
    )


def _compress_backup(*, source: Path, out: Path, compression: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    mode = compression.strip().lower()
    if mode == "none":
        shutil.copy2(source, out)
        return
    if mode != "zstd":
        raise ValueError(f"unsupported compression mode: {compression}")
    if shutil.which("zstd") is None:
        raise RuntimeError("zstd not found; install with: sudo apt-get install -y zstd")
    cmd = ["zstd", "-T0", "-19", str(source), "-o", str(out), "-f"]
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"zstd compression failed: {message}")


def _verify_archive(*, archive_file: Path, compression: str) -> tuple[bool, dict[str, Any]]:
    if compression == "none":
        return _verify_sqlite_db(archive_file)

    if shutil.which("zstd") is None:
        raise RuntimeError("zstd not found; install with: sudo apt-get install -y zstd")
    test_cmd = ["zstd", "-t", str(archive_file)]
    tested = subprocess.run(test_cmd, capture_output=True, text=True, check=False)
    if tested.returncode != 0:
        return False, {"zstd_test_stderr": (tested.stderr or "").strip()}

    with tempfile.TemporaryDirectory(prefix="hk-archive-verify-") as tmp_dir:
        unzipped = Path(tmp_dir) / "verify.db"
        dec_cmd = ["zstd", "-d", str(archive_file), "-o", str(unzipped), "-f"]
        dec = subprocess.run(dec_cmd, capture_output=True, text=True, check=False)
        if dec.returncode != 0:
            return False, {"zstd_decompress_stderr": (dec.stderr or "").strip()}
        return _verify_sqlite_db(unzipped)


def _verify_sqlite_db(db_file: Path) -> tuple[bool, dict[str, Any]]:
    if not db_file.exists():
        return False, {"error": "db_missing_after_decompress"}
    conn = sqlite3.connect(f"file:{db_file}?mode=ro&immutable=1", uri=True)
    try:
        row = conn.execute("SELECT COUNT(*), MAX(ts_ms) FROM ticks").fetchone()
    except sqlite3.DatabaseError as exc:
        return False, {"sqlite_error": type(exc).__name__, "message": str(exc)}
    finally:
        conn.close()
    return True, {"ticks_count": int(row[0] or 0), "max_ts_ms": int(row[1]) if row[1] else None}


def _cleanup_original_db_files_for_retention(
    *,
    data_root: Path,
    archive_dir: Path,
    keep_days: int,
) -> None:
    db_files = sorted(
        [path for path in Path(data_root).glob("*.db") if len(path.stem) == 8 and path.stem.isdigit()]
    )
    if keep_days > 0:
        db_files = db_files[:-keep_days]
    if keep_days <= 0:
        db_files = db_files[:]

    for db in db_files:
        day = db.stem
        if not _is_archived_and_verified(day=day, archive_dir=Path(archive_dir)):
            continue
        for ext in (".db", ".db-wal", ".db-shm"):
            target = Path(data_root) / f"{day}{ext}"
            if not target.exists():
                continue
            if target.parent.resolve() != Path(data_root).resolve():
                raise RuntimeError(f"unsafe delete path: {target}")
            target.unlink()


def _is_archived_and_verified(*, day: str, archive_dir: Path) -> bool:
    manifest = archive_dir / "manifest" / f"{day}.json"
    archive_file = archive_dir / f"{day}.db.zst"
    checksum_file = archive_dir / f"{day}.db.zst.sha256"
    if not (manifest.exists() and archive_file.exists() and checksum_file.exists()):
        return False
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return bool(payload.get("verify_ok", False))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0
