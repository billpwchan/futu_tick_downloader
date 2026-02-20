import os

import pytest

import hk_tick_collector.config as config_module
from hk_tick_collector.config import Config


def _clear_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith(
            (
                "FUTU_",
                "DATA_ROOT",
                "BATCH_SIZE",
                "MAX_WAIT_MS",
                "MAX_QUEUE_SIZE",
                "BACKFILL_N",
                "RECONNECT_",
                "CHECK_INTERVAL_SEC",
                "WATCHDOG_",
                "DRIFT_WARN_SEC",
                "STOP_FLUSH_TIMEOUT_SEC",
                "SEED_RECENT_DB_DAYS",
                "PERSIST_",
                "SQLITE_",
                "TELEGRAM_",
                "TG_",
                "HEALTH_",
                "ALERT_",
                "INSTANCE_ID",
                "LOG_LEVEL",
            )
        ):
            monkeypatch.delenv(key, raising=False)


def test_config_from_env_defaults(monkeypatch):
    monkeypatch.setattr(config_module, "_load_dotenv", lambda: None)
    _clear_env(monkeypatch)

    cfg = Config.from_env()
    assert cfg.futu_host == "127.0.0.1"
    assert cfg.futu_port == 11111
    assert cfg.symbols == []
    assert str(cfg.data_root) == "/data/sqlite/HK"
    assert cfg.batch_size == 500
    assert cfg.max_wait_ms == 1000
    assert cfg.poll_enabled is True
    assert cfg.poll_trading_only is True
    assert cfg.poll_preopen_enabled is False
    assert cfg.poll_offhours_probe_interval_sec == 0
    assert cfg.poll_offhours_probe_num == 1
    assert cfg.futu_holidays == []
    assert cfg.futu_holiday_file == ""
    assert cfg.watchdog_stall_sec == 180
    assert cfg.sqlite_journal_mode == "WAL"
    assert cfg.sqlite_synchronous == "NORMAL"
    assert cfg.telegram_enabled is False
    assert cfg.telegram_rate_limit_per_min == 18
    assert cfg.telegram_thread_id is None
    assert cfg.telegram_thread_health_id is None
    assert cfg.telegram_thread_ops_id is None
    assert cfg.telegram_mode_default == "product"
    assert cfg.telegram_parse_mode == "HTML"
    assert cfg.telegram_health_interval_sec == 900
    assert cfg.telegram_health_lunch_once is True
    assert cfg.telegram_health_after_close_once is True
    assert cfg.telegram_health_holiday_mode == "daily"
    assert cfg.telegram_alert_escalation_steps == [0, 600, 1800]
    assert cfg.telegram_interactive_enabled is False
    assert cfg.telegram_admin_user_ids == []
    assert cfg.telegram_action_context_ttl_sec == 43200
    assert cfg.telegram_action_log_max_lines == 20
    assert cfg.telegram_action_refresh_min_interval_sec == 15
    assert cfg.telegram_action_command_rate_limit_per_min == 8
    assert cfg.telegram_action_timeout_sec == 3.0
    assert cfg.telegram_action_command_timeout_sec == 10.0
    assert cfg.telegram_action_command_allowlist == ["help", "db_stats", "top_symbols", "symbol"]
    assert cfg.telegram_action_command_max_lookback_days == 30


def test_config_bool_and_list_parsing(monkeypatch):
    monkeypatch.setattr(config_module, "_load_dotenv", lambda: None)
    _clear_env(monkeypatch)
    monkeypatch.setenv("FUTU_POLL_ENABLED", "off")
    monkeypatch.setenv("FUTU_POLL_TRADING_ONLY", "0")
    monkeypatch.setenv("FUTU_POLL_PREOPEN_ENABLED", "1")
    monkeypatch.setenv("FUTU_POLL_OFFHOURS_PROBE_INTERVAL_SEC", "600")
    monkeypatch.setenv("FUTU_POLL_OFFHOURS_PROBE_NUM", "3")
    monkeypatch.setenv("FUTU_HOLIDAYS", "20260101, 20260217")
    monkeypatch.setenv("FUTU_HOLIDAY_FILE", "/tmp/hk_holidays.txt")
    monkeypatch.setenv("FUTU_SYMBOLS", " HK.00700 , HK.00981 ,,")
    monkeypatch.setenv("WATCHDOG_STALL_SEC", "240")

    cfg = Config.from_env()
    assert cfg.poll_enabled is False
    assert cfg.poll_trading_only is False
    assert cfg.poll_preopen_enabled is True
    assert cfg.poll_offhours_probe_interval_sec == 600
    assert cfg.poll_offhours_probe_num == 3
    assert cfg.futu_holidays == ["20260101", "20260217"]
    assert cfg.futu_holiday_file == "/tmp/hk_holidays.txt"
    assert cfg.symbols == ["HK.00700", "HK.00981"]
    assert cfg.watchdog_stall_sec == 240


def test_config_parses_telegram_env(monkeypatch):
    monkeypatch.setattr(config_module, "_load_dotenv", lambda: None)
    _clear_env(monkeypatch)
    monkeypatch.setenv("TG_ENABLED", "1")
    monkeypatch.setenv("TG_TOKEN", "token")
    monkeypatch.setenv("TG_CHAT_ID", "-1001")
    monkeypatch.setenv("TG_MESSAGE_THREAD_ID", "9")
    monkeypatch.setenv("TG_THREAD_HEALTH_ID", "11")
    monkeypatch.setenv("TG_THREAD_OPS_ID", "12")
    monkeypatch.setenv("TG_MODE_DEFAULT", "ops")
    monkeypatch.setenv("TG_PARSE_MODE", "HTML")
    monkeypatch.setenv("HEALTH_INTERVAL_SEC", "700")
    monkeypatch.setenv("HEALTH_TRADING_INTERVAL_SEC", "1500")
    monkeypatch.setenv("HEALTH_OFFHOURS_INTERVAL_SEC", "1800")
    monkeypatch.setenv("TG_HEALTH_LUNCH_ONCE", "0")
    monkeypatch.setenv("TG_HEALTH_AFTER_CLOSE_ONCE", "0")
    monkeypatch.setenv("TG_HEALTH_HOLIDAY_MODE", "disabled")
    monkeypatch.setenv("ALERT_COOLDOWN_SEC", "900")
    monkeypatch.setenv("ALERT_ESCALATION_STEPS", "0,300,900")
    monkeypatch.setenv("TG_INTERACTIVE_ENABLED", "1")
    monkeypatch.setenv("TG_ADMIN_USER_IDS", "1001,1002")
    monkeypatch.setenv("TG_ACTION_CONTEXT_TTL_SEC", "21600")
    monkeypatch.setenv("TG_ACTION_LOG_MAX_LINES", "30")
    monkeypatch.setenv("TG_ACTION_REFRESH_MIN_INTERVAL_SEC", "22")
    monkeypatch.setenv("TG_ACTION_COMMAND_RATE_LIMIT_PER_MIN", "6")
    monkeypatch.setenv("TG_ACTION_TIMEOUT_SEC", "4.5")
    monkeypatch.setenv("TG_ACTION_COMMAND_TIMEOUT_SEC", "12")
    monkeypatch.setenv("TG_ACTION_COMMAND_ALLOWLIST", "help,db_stats,symbol")
    monkeypatch.setenv("TG_ACTION_COMMAND_MAX_LOOKBACK_DAYS", "45")

    cfg = Config.from_env()
    assert cfg.telegram_enabled is True
    assert cfg.telegram_bot_token == "token"
    assert cfg.telegram_chat_id == "-1001"
    assert cfg.telegram_thread_id == 9
    assert cfg.telegram_thread_health_id == 11
    assert cfg.telegram_thread_ops_id == 12
    assert cfg.telegram_mode_default == "ops"
    assert cfg.telegram_parse_mode == "HTML"
    assert cfg.telegram_health_interval_sec == 700
    assert cfg.telegram_health_trading_interval_sec == 1500
    assert cfg.telegram_health_offhours_interval_sec == 1800
    assert cfg.telegram_health_lunch_once is False
    assert cfg.telegram_health_after_close_once is False
    assert cfg.telegram_health_holiday_mode == "disabled"
    assert cfg.telegram_alert_cooldown_sec == 900
    assert cfg.telegram_alert_escalation_steps == [0, 300, 900]
    assert cfg.telegram_interactive_enabled is True
    assert cfg.telegram_admin_user_ids == [1001, 1002]
    assert cfg.telegram_action_context_ttl_sec == 21600
    assert cfg.telegram_action_log_max_lines == 30
    assert cfg.telegram_action_refresh_min_interval_sec == 22
    assert cfg.telegram_action_command_rate_limit_per_min == 6
    assert cfg.telegram_action_timeout_sec == 4.5
    assert cfg.telegram_action_command_timeout_sec == 12.0
    assert cfg.telegram_action_command_allowlist == ["help", "db_stats", "symbol"]
    assert cfg.telegram_action_command_max_lookback_days == 45


def test_config_ignores_legacy_telegram_aliases(monkeypatch):
    monkeypatch.setattr(config_module, "_load_dotenv", lambda: None)
    _clear_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "legacy-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-10099")

    cfg = Config.from_env()
    assert cfg.telegram_enabled is False
    assert cfg.telegram_bot_token == ""
    assert cfg.telegram_chat_id == ""


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("FUTU_PORT", "abc"),
        ("MAX_WAIT_MS", "1.5"),
        ("PERSIST_RETRY_MAX_ATTEMPTS", "NaN"),
        ("SQLITE_BUSY_TIMEOUT_MS", "oops"),
        ("PERSIST_RETRY_BACKOFF_SEC", "bad-float"),
    ],
)
def test_config_invalid_numeric_values_raise(monkeypatch, key, value):
    monkeypatch.setattr(config_module, "_load_dotenv", lambda: None)
    _clear_env(monkeypatch)
    monkeypatch.setenv(key, value)

    with pytest.raises(ValueError):
        Config.from_env()
