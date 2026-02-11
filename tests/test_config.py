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
