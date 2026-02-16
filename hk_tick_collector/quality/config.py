from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time as dt_time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _get_env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    text = value.strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _get_env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


def _get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


@dataclass(frozen=True)
class TradingSession:
    start: dt_time
    end: dt_time
    label: str


def parse_trading_sessions(value: str) -> tuple[TradingSession, ...]:
    sessions: list[TradingSession] = []
    for raw in str(value).split(","):
        text = raw.strip()
        if not text:
            continue
        if "-" not in text:
            raise ValueError(f"invalid TRADING_SESSIONS item: {text}")
        start_text, end_text = text.split("-", 1)
        start = _parse_hhmm(start_text.strip())
        end = _parse_hhmm(end_text.strip())
        if (start.hour, start.minute) >= (end.hour, end.minute):
            raise ValueError(f"session start must be before end: {text}")
        sessions.append(TradingSession(start=start, end=end, label=text))
    if not sessions:
        raise ValueError("TRADING_SESSIONS is empty")
    return tuple(sessions)


def _parse_hhmm(value: str) -> dt_time:
    if ":" not in value:
        raise ValueError(f"invalid time format: {value}")
    hh, mm = value.split(":", 1)
    hour = int(hh)
    minute = int(mm)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"invalid time range: {value}")
    return dt_time(hour, minute)


@dataclass(frozen=True)
class QualityConfig:
    gap_enabled: bool
    gap_threshold_sec: float
    gap_active_window_sec: int
    gap_active_min_ticks: int
    gap_stall_warn_sec: float
    trading_tz: str
    trading_sessions_text: str
    report_rel_dir: str

    @classmethod
    def from_env(cls) -> "QualityConfig":
        return cls(
            gap_enabled=_get_env_bool("GAP_ENABLED", True),
            gap_threshold_sec=max(0.1, _get_env_float("GAP_THRESHOLD_SEC", 10.0)),
            gap_active_window_sec=max(1, _get_env_int("GAP_ACTIVE_WINDOW_SEC", 300)),
            gap_active_min_ticks=max(1, _get_env_int("GAP_ACTIVE_MIN_TICKS", 50)),
            gap_stall_warn_sec=max(0.1, _get_env_float("GAP_STALL_WARN_SEC", 30.0)),
            trading_tz=(os.getenv("TRADING_TZ", "Asia/Hong_Kong") or "Asia/Hong_Kong").strip(),
            trading_sessions_text=(
                os.getenv("TRADING_SESSIONS", "09:30-12:00,13:00-16:00")
                or "09:30-12:00,13:00-16:00"
            ).strip(),
            report_rel_dir="_reports/quality",
        )

    @property
    def sessions(self) -> tuple[TradingSession, ...]:
        return parse_trading_sessions(self.trading_sessions_text)

    @property
    def tzinfo(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.trading_tz)
        except ZoneInfoNotFoundError:
            return ZoneInfo("Asia/Hong_Kong")
