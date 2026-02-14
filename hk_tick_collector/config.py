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


def _get_env_optional_int(name: str) -> int | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return int(value)


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


def _get_env_text(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


def _get_env_int_list(name: str, default: list[int]) -> list[int]:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return list(default)
    items = [part.strip() for part in value.split(",") if part.strip()]
    return [int(item) for item in items] if items else list(default)


def _get_env_day_list(name: str) -> List[str]:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return []
    items = [part.strip().replace("-", "").replace("/", "") for part in value.split(",")]
    return [item for item in items if len(item) == 8 and item.isdigit()]


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
    poll_trading_only: bool
    poll_preopen_enabled: bool
    poll_offhours_probe_interval_sec: int
    poll_offhours_probe_num: int
    futu_holidays: List[str]
    futu_holiday_file: str
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
    telegram_enabled: bool
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_thread_id: int | None
    telegram_thread_health_id: int | None
    telegram_thread_ops_id: int | None
    telegram_mode_default: str
    telegram_parse_mode: str
    telegram_health_interval_sec: int
    telegram_health_trading_interval_sec: int
    telegram_health_offhours_interval_sec: int
    telegram_health_lunch_once: bool
    telegram_health_after_close_once: bool
    telegram_health_holiday_mode: str
    telegram_alert_cooldown_sec: int
    telegram_alert_escalation_steps: List[int]
    telegram_rate_limit_per_min: int
    telegram_include_system_metrics: bool
    telegram_digest_queue_change_pct: float
    telegram_digest_last_tick_age_threshold_sec: int
    telegram_digest_drift_threshold_sec: int
    telegram_digest_send_alive_when_idle: bool
    telegram_sqlite_busy_alert_threshold: int
    telegram_interactive_enabled: bool
    telegram_admin_user_ids: List[int]
    telegram_action_context_ttl_sec: int
    telegram_action_log_max_lines: int
    telegram_action_refresh_min_interval_sec: int
    telegram_action_timeout_sec: float
    instance_id: str
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        _load_dotenv()
        poll_offhours_probe_num = _get_env_int("FUTU_POLL_OFFHOURS_PROBE_NUM", 1)
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
            poll_trading_only=_get_env_bool("FUTU_POLL_TRADING_ONLY", True),
            poll_preopen_enabled=_get_env_bool("FUTU_POLL_PREOPEN_ENABLED", False),
            poll_offhours_probe_interval_sec=max(
                0, _get_env_int("FUTU_POLL_OFFHOURS_PROBE_INTERVAL_SEC", 0)
            ),
            poll_offhours_probe_num=max(1, poll_offhours_probe_num),
            futu_holidays=_get_env_day_list("FUTU_HOLIDAYS"),
            futu_holiday_file=_get_env_text("FUTU_HOLIDAY_FILE", ""),
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
            watchdog_recovery_join_timeout_sec=_get_env_float(
                "WATCHDOG_RECOVERY_JOIN_TIMEOUT_SEC", 3.0
            ),
            sqlite_busy_timeout_ms=_get_env_int("SQLITE_BUSY_TIMEOUT_MS", 5000),
            sqlite_journal_mode=os.getenv("SQLITE_JOURNAL_MODE", "WAL"),
            sqlite_synchronous=os.getenv("SQLITE_SYNCHRONOUS", "NORMAL"),
            sqlite_wal_autocheckpoint=_get_env_int("SQLITE_WAL_AUTOCHECKPOINT", 1000),
            telegram_enabled=_get_env_bool("TG_ENABLED", False),
            telegram_bot_token=_get_env_text("TG_TOKEN", ""),
            telegram_chat_id=_get_env_text("TG_CHAT_ID", ""),
            telegram_thread_id=_get_env_optional_int("TG_MESSAGE_THREAD_ID"),
            telegram_thread_health_id=_get_env_optional_int("TG_THREAD_HEALTH_ID"),
            telegram_thread_ops_id=_get_env_optional_int("TG_THREAD_OPS_ID"),
            telegram_mode_default=_get_env_text("TG_MODE_DEFAULT", "product").lower(),
            telegram_parse_mode=_get_env_text("TG_PARSE_MODE", "HTML"),
            telegram_health_interval_sec=_get_env_int(
                "HEALTH_INTERVAL_SEC",
                900,
            ),
            telegram_health_trading_interval_sec=_get_env_int(
                "HEALTH_TRADING_INTERVAL_SEC",
                _get_env_int("HEALTH_INTERVAL_SEC", 900),
            ),
            telegram_health_offhours_interval_sec=_get_env_int(
                "HEALTH_OFFHOURS_INTERVAL_SEC",
                _get_env_int("HEALTH_INTERVAL_SEC", 900),
            ),
            telegram_health_lunch_once=_get_env_bool("TG_HEALTH_LUNCH_ONCE", True),
            telegram_health_after_close_once=_get_env_bool(
                "TG_HEALTH_AFTER_CLOSE_ONCE",
                True,
            ),
            telegram_health_holiday_mode=_get_env_text("TG_HEALTH_HOLIDAY_MODE", "daily").lower(),
            telegram_alert_cooldown_sec=_get_env_int(
                "ALERT_COOLDOWN_SEC",
                600,
            ),
            telegram_alert_escalation_steps=_get_env_int_list(
                "ALERT_ESCALATION_STEPS",
                [0, 600, 1800],
            ),
            telegram_rate_limit_per_min=_get_env_int("TG_RATE_LIMIT_PER_MIN", 18),
            telegram_include_system_metrics=_get_env_bool(
                "TG_INCLUDE_SYSTEM_METRICS",
                True,
            ),
            telegram_digest_queue_change_pct=_get_env_float("TG_DIGEST_QUEUE_CHANGE_PCT", 20.0),
            telegram_digest_last_tick_age_threshold_sec=_get_env_int(
                "TG_DIGEST_LAST_TICK_AGE_THRESHOLD_SEC",
                60,
            ),
            telegram_digest_drift_threshold_sec=_get_env_int(
                "TG_DIGEST_DRIFT_THRESHOLD_SEC",
                60,
            ),
            telegram_digest_send_alive_when_idle=_get_env_bool(
                "TG_DIGEST_SEND_ALIVE_WHEN_IDLE",
                False,
            ),
            telegram_sqlite_busy_alert_threshold=_get_env_int(
                "TG_SQLITE_BUSY_ALERT_THRESHOLD",
                3,
            ),
            telegram_interactive_enabled=_get_env_bool("TG_INTERACTIVE_ENABLED", False),
            telegram_admin_user_ids=_get_env_int_list("TG_ADMIN_USER_IDS", []),
            telegram_action_context_ttl_sec=_get_env_int("TG_ACTION_CONTEXT_TTL_SEC", 43200),
            telegram_action_log_max_lines=_get_env_int("TG_ACTION_LOG_MAX_LINES", 20),
            telegram_action_refresh_min_interval_sec=_get_env_int(
                "TG_ACTION_REFRESH_MIN_INTERVAL_SEC", 15
            ),
            telegram_action_timeout_sec=_get_env_float("TG_ACTION_TIMEOUT_SEC", 3.0),
            instance_id=os.getenv("INSTANCE_ID", "").strip(),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )
