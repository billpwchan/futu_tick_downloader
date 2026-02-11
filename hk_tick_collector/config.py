from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def _get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _get_env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


def _get_env_list(name: str, default: List[str]) -> List[str]:
    value = os.getenv(name)
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def _get_env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


@dataclass(frozen=True)
class Config:
    futu_host: str
    futu_port: int
    symbols: List[str]
    data_root: Path
    batch_size: int
    max_wait_ms: int
    max_queue_size: int
    backfill_n: int
    reconnect_min_delay: int
    reconnect_max_delay: int
    check_interval_sec: int
    poll_enabled: bool
    poll_interval_sec: int
    poll_num: int
    poll_stale_sec: int
    watchdog_stall_sec: int
    watchdog_upstream_window_sec: int
    drift_warn_sec: int
    stop_flush_timeout_sec: int
    seed_recent_db_days: int
    persist_retry_max_attempts: int
    persist_retry_backoff_sec: float
    persist_retry_backoff_max_sec: float
    persist_heartbeat_interval_sec: float
    watchdog_queue_threshold_rows: int
    watchdog_recovery_max_failures: int
    watchdog_recovery_join_timeout_sec: float
    sqlite_busy_timeout_ms: int
    sqlite_journal_mode: str
    sqlite_synchronous: str
    sqlite_wal_autocheckpoint: int
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        _load_dotenv()
        return cls(
            futu_host=os.getenv("FUTU_HOST", "127.0.0.1"),
            futu_port=_get_env_int("FUTU_PORT", 11111),
            symbols=_get_env_list("FUTU_SYMBOLS", []),
            data_root=Path(os.getenv("DATA_ROOT", "/data/sqlite/HK")),
            batch_size=_get_env_int("BATCH_SIZE", 500),
            max_wait_ms=_get_env_int("MAX_WAIT_MS", 1000),
            max_queue_size=_get_env_int("MAX_QUEUE_SIZE", 20000),
            backfill_n=_get_env_int("BACKFILL_N", 0),
            reconnect_min_delay=_get_env_int("RECONNECT_MIN_DELAY", 1),
            reconnect_max_delay=_get_env_int("RECONNECT_MAX_DELAY", 60),
            check_interval_sec=_get_env_int("CHECK_INTERVAL_SEC", 5),
            poll_enabled=_get_env_bool("FUTU_POLL_ENABLED", True),
            poll_interval_sec=_get_env_int("FUTU_POLL_INTERVAL_SEC", 3),
            poll_num=_get_env_int("FUTU_POLL_NUM", 100),
            poll_stale_sec=_get_env_int("FUTU_POLL_STALE_SEC", 10),
            watchdog_stall_sec=_get_env_int("WATCHDOG_STALL_SEC", 180),
            watchdog_upstream_window_sec=_get_env_int("WATCHDOG_UPSTREAM_WINDOW_SEC", 60),
            drift_warn_sec=_get_env_int("DRIFT_WARN_SEC", 120),
            stop_flush_timeout_sec=_get_env_int("STOP_FLUSH_TIMEOUT_SEC", 60),
            seed_recent_db_days=_get_env_int("SEED_RECENT_DB_DAYS", 3),
            persist_retry_max_attempts=_get_env_int("PERSIST_RETRY_MAX_ATTEMPTS", 0),
            persist_retry_backoff_sec=_get_env_float("PERSIST_RETRY_BACKOFF_SEC", 1.0),
            persist_retry_backoff_max_sec=_get_env_float("PERSIST_RETRY_BACKOFF_MAX_SEC", 2.0),
            persist_heartbeat_interval_sec=_get_env_float("PERSIST_HEARTBEAT_INTERVAL_SEC", 30.0),
            watchdog_queue_threshold_rows=_get_env_int("WATCHDOG_QUEUE_THRESHOLD_ROWS", 100),
            watchdog_recovery_max_failures=_get_env_int("WATCHDOG_RECOVERY_MAX_FAILURES", 3),
            watchdog_recovery_join_timeout_sec=_get_env_float("WATCHDOG_RECOVERY_JOIN_TIMEOUT_SEC", 3.0),
            sqlite_busy_timeout_ms=_get_env_int("SQLITE_BUSY_TIMEOUT_MS", 5000),
            sqlite_journal_mode=os.getenv("SQLITE_JOURNAL_MODE", "WAL"),
            sqlite_synchronous=os.getenv("SQLITE_SYNCHRONOUS", "NORMAL"),
            sqlite_wal_autocheckpoint=_get_env_int("SQLITE_WAL_AUTOCHECKPOINT", 1000),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )
