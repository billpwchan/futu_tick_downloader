from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List


def _get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _get_env_list(name: str, default: List[str]) -> List[str]:
    value = os.getenv(name)
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


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
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
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
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )
