from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass
class OpendConfig:
    host: str = "futu-opend"
    port: int = 11111
    session: str = "ALL"
    symbols: list[str] = field(default_factory=list)


@dataclass
class ReconnectConfig:
    base_delay_ms: int = 500
    max_delay_ms: int = 30_000


@dataclass
class BackfillConfig:
    enabled: bool = True
    num: int = 200


@dataclass
class SQLiteConfig:
    journal_mode: str = "WAL"
    synchronous: str = "NORMAL"
    temp_store: str = "MEMORY"


@dataclass
class HealthConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass
class CollectorConfig:
    market: str = "HK"
    data_dir: str = "/data"
    batch_size: int = 1000
    max_wait_ms: int = 500
    log_level: str = "INFO"


@dataclass
class AppConfig:
    opend: OpendConfig = field(default_factory=OpendConfig)
    reconnect: ReconnectConfig = field(default_factory=ReconnectConfig)
    backfill: BackfillConfig = field(default_factory=BackfillConfig)
    sqlite: SQLiteConfig = field(default_factory=SQLiteConfig)
    health: HealthConfig = field(default_factory=HealthConfig)
    collector: CollectorConfig = field(default_factory=CollectorConfig)


ENV_PREFIX = "HKTC_"


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _apply_overrides(config: AppConfig) -> None:
    env = os.environ
    if f"{ENV_PREFIX}OPEND_HOST" in env:
        config.opend.host = env[f"{ENV_PREFIX}OPEND_HOST"]
    if f"{ENV_PREFIX}OPEND_PORT" in env:
        config.opend.port = int(env[f"{ENV_PREFIX}OPEND_PORT"])
    if f"{ENV_PREFIX}OPEND_SESSION" in env:
        config.opend.session = env[f"{ENV_PREFIX}OPEND_SESSION"]
    if f"{ENV_PREFIX}SYMBOLS" in env:
        config.opend.symbols = _parse_csv(env[f"{ENV_PREFIX}SYMBOLS"])

    if f"{ENV_PREFIX}RECONNECT_BASE_DELAY_MS" in env:
        config.reconnect.base_delay_ms = int(env[f"{ENV_PREFIX}RECONNECT_BASE_DELAY_MS"])
    if f"{ENV_PREFIX}RECONNECT_MAX_DELAY_MS" in env:
        config.reconnect.max_delay_ms = int(env[f"{ENV_PREFIX}RECONNECT_MAX_DELAY_MS"])

    if f"{ENV_PREFIX}BACKFILL_ENABLED" in env:
        config.backfill.enabled = _parse_bool(env[f"{ENV_PREFIX}BACKFILL_ENABLED"])
    if f"{ENV_PREFIX}BACKFILL_NUM" in env:
        config.backfill.num = int(env[f"{ENV_PREFIX}BACKFILL_NUM"])

    if f"{ENV_PREFIX}SQLITE_JOURNAL_MODE" in env:
        config.sqlite.journal_mode = env[f"{ENV_PREFIX}SQLITE_JOURNAL_MODE"]
    if f"{ENV_PREFIX}SQLITE_SYNCHRONOUS" in env:
        config.sqlite.synchronous = env[f"{ENV_PREFIX}SQLITE_SYNCHRONOUS"]
    if f"{ENV_PREFIX}SQLITE_TEMP_STORE" in env:
        config.sqlite.temp_store = env[f"{ENV_PREFIX}SQLITE_TEMP_STORE"]

    if f"{ENV_PREFIX}HEALTH_ENABLED" in env:
        config.health.enabled = _parse_bool(env[f"{ENV_PREFIX}HEALTH_ENABLED"])
    if f"{ENV_PREFIX}HEALTH_HOST" in env:
        config.health.host = env[f"{ENV_PREFIX}HEALTH_HOST"]
    if f"{ENV_PREFIX}HEALTH_PORT" in env:
        config.health.port = int(env[f"{ENV_PREFIX}HEALTH_PORT"])

    if f"{ENV_PREFIX}MARKET" in env:
        config.collector.market = env[f"{ENV_PREFIX}MARKET"]
    if f"{ENV_PREFIX}DATA_DIR" in env:
        config.collector.data_dir = env[f"{ENV_PREFIX}DATA_DIR"]
    if f"{ENV_PREFIX}BATCH_SIZE" in env:
        config.collector.batch_size = int(env[f"{ENV_PREFIX}BATCH_SIZE"])
    if f"{ENV_PREFIX}MAX_WAIT_MS" in env:
        config.collector.max_wait_ms = int(env[f"{ENV_PREFIX}MAX_WAIT_MS"])
    if f"{ENV_PREFIX}LOG_LEVEL" in env:
        config.collector.log_level = env[f"{ENV_PREFIX}LOG_LEVEL"]


def _merge_dataclass(dst: Any, src: dict[str, Any]) -> None:
    for key, value in src.items():
        if not hasattr(dst, key):
            continue
        current = getattr(dst, key)
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            _merge_dataclass(current, value)
        else:
            setattr(dst, key, value)


def load_config(path: str) -> AppConfig:
    config = AppConfig()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if isinstance(data, dict):
            _merge_dataclass(config, data)
    _apply_overrides(config)
    return config


def config_summary(config: AppConfig) -> dict[str, Any]:
    return {
        "opend_host": config.opend.host,
        "opend_port": config.opend.port,
        "session": config.opend.session,
        "symbols": config.opend.symbols,
        "market": config.collector.market,
        "data_dir": config.collector.data_dir,
        "batch_size": config.collector.batch_size,
        "max_wait_ms": config.collector.max_wait_ms,
        "backfill": config.backfill.enabled,
        "backfill_num": config.backfill.num,
        "health": config.health.enabled,
    }
