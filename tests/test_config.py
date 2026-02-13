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


def test_config_bool_and_list_parsing(monkeypatch):
    monkeypatch.setattr(config_module, "_load_dotenv", lambda: None)
    _clear_env(monkeypatch)
    monkeypatch.setenv("FUTU_POLL_ENABLED", "off")
    monkeypatch.setenv("FUTU_SYMBOLS", " HK.00700 , HK.00981 ,,")
    monkeypatch.setenv("WATCHDOG_STALL_SEC", "240")

    cfg = Config.from_env()
    assert cfg.poll_enabled is False
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
