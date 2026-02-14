from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
HK_TZ = ZoneInfo("Asia/Hong_Kong")


@dataclass(frozen=True)
class MarketState:
    trading_day: str
    mode: str
    is_trading_day: bool
    is_trading_session: bool


def _normalize_day(value: str) -> str | None:
    text = str(value).strip().replace("-", "").replace("/", "")
    if len(text) == 8 and text.isdigit():
        return text
    return None


def _load_holidays(holiday_file: str) -> set[str]:
    path_text = holiday_file.strip()
    if not path_text:
        return set()
    path = Path(path_text)
    if not path.exists():
        logger.warning("market_calendar_holiday_file_not_found path=%s", path)
        return set()

    days: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        day = _normalize_day(text.split(",", 1)[0])
        if day:
            days.add(day)
    return days


class MarketCalendar:
    def __init__(self, *, holidays: Iterable[str] | None = None, holiday_file: str = "") -> None:
        merged: set[str] = set()
        for value in holidays or []:
            day = _normalize_day(value)
            if day:
                merged.add(day)
        merged.update(_load_holidays(holiday_file))
        self._holidays = merged

    def is_holiday(self, trading_day: str) -> bool:
        return trading_day in self._holidays


def resolve_market_state(
    now: datetime | None = None,
    calendar: MarketCalendar | None = None,
) -> MarketState:
    local = (now or datetime.now(tz=HK_TZ)).astimezone(HK_TZ)
    trading_day = local.strftime("%Y%m%d")
    is_weekend = local.weekday() >= 5
    is_holiday = calendar.is_holiday(trading_day) if calendar is not None else False

    if is_weekend:
        return MarketState(
            trading_day=trading_day,
            mode="after-hours",
            is_trading_day=False,
            is_trading_session=False,
        )
    if is_holiday:
        return MarketState(
            trading_day=trading_day,
            mode="holiday-closed",
            is_trading_day=False,
            is_trading_session=False,
        )

    current = local.timetz().replace(tzinfo=None)
    if dt_time(9, 0) <= current < dt_time(9, 30):
        return MarketState(
            trading_day=trading_day,
            mode="pre-open",
            is_trading_day=True,
            is_trading_session=False,
        )
    if dt_time(9, 30) <= current < dt_time(12, 0):
        return MarketState(
            trading_day=trading_day,
            mode="open",
            is_trading_day=True,
            is_trading_session=True,
        )
    if dt_time(12, 0) <= current < dt_time(13, 0):
        return MarketState(
            trading_day=trading_day,
            mode="lunch-break",
            is_trading_day=True,
            is_trading_session=False,
        )
    if dt_time(13, 0) <= current < dt_time(16, 0):
        return MarketState(
            trading_day=trading_day,
            mode="open",
            is_trading_day=True,
            is_trading_session=True,
        )
    return MarketState(
        trading_day=trading_day,
        mode="after-hours",
        is_trading_day=True,
        is_trading_session=False,
    )
