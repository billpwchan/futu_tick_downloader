from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Iterable, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from .models import TickRow

logger = logging.getLogger(__name__)
HK_TZ = ZoneInfo("Asia/Hong_Kong")
UTC_TZ = timezone.utc
HK_OFFSET_MS = 8 * 3600 * 1000
FUTURE_GUARD_MS = 2 * 3600 * 1000
FUTURE_CORRECTION_TOLERANCE_MS = 30 * 60 * 1000


def normalize_trading_day(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit() and len(text) == 8:
        return text
    if "-" in text:
        return text.replace("-", "")
    if "/" in text:
        return text.replace("/", "")
    return text


def trading_day_from_ts(ts_ms: int) -> str:
    return (
        datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC_TZ)
        .astimezone(HK_TZ)
        .strftime("%Y%m%d")
    )


def _parse_datetime(value: str) -> datetime:
    text = value.strip().replace("T", " ")
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S.%f",
        "%Y/%m/%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _to_utc_epoch_ms(dt: datetime, *, default_tz: ZoneInfo = HK_TZ) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz)
    return _normalize_epoch_ms(int(dt.astimezone(UTC_TZ).timestamp() * 1000))


def _normalize_epoch_ms(value: int) -> int:
    ts_ms = int(value)
    now_ms = int(time.time() * 1000)
    if ts_ms <= now_ms + FUTURE_GUARD_MS:
        return ts_ms

    drift_ms = ts_ms - now_ms
    if abs(drift_ms - HK_OFFSET_MS) <= FUTURE_CORRECTION_TOLERANCE_MS:
        corrected = ts_ms - HK_OFFSET_MS
        logger.warning(
            "ts_ms_future_offset_corrected raw_ts_ms=%s corrected_ts_ms=%s drift_ms=%s",
            ts_ms,
            corrected,
            drift_ms,
        )
        return corrected
    return ts_ms


def _parse_compact_time_text(text: str, trading_day: Optional[str]) -> int | None:
    if len(text) == 6:
        day = normalize_trading_day(trading_day)
        if day is None:
            day = datetime.now(tz=HK_TZ).strftime("%Y%m%d")
        dt = datetime.strptime(f"{day} {text}", "%Y%m%d %H%M%S")
        return _to_utc_epoch_ms(dt)
    if len(text) == 14:
        dt = datetime.strptime(text, "%Y%m%d%H%M%S")
        return _to_utc_epoch_ms(dt)
    return None


def parse_time_to_ts_ms(value: object, trading_day: Optional[str]) -> int:
    if value is None:
        raise ValueError("missing time value")
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 1e12:
            return _normalize_epoch_ms(int(numeric))
        if numeric > 1e9:
            return _normalize_epoch_ms(int(numeric * 1000))
        return int(numeric)

    text = str(value).strip()
    if text.isdigit():
        compact = _parse_compact_time_text(text, trading_day)
        if compact is not None:
            return compact
        numeric = int(text)
        if numeric > 1e12:
            return _normalize_epoch_ms(numeric)
        if numeric > 1e9:
            return _normalize_epoch_ms(numeric * 1000)
        return numeric

    if any(token in text for token in ("-", "/", " ")):
        dt = _parse_datetime(text)
        return _to_utc_epoch_ms(dt)

    # time-only string (HH:MM:SS[.ms])
    day = normalize_trading_day(trading_day)
    if day is None:
        day = datetime.now(tz=HK_TZ).strftime("%Y%m%d")
    if "." in text:
        dt = datetime.strptime(f"{day} {text}", "%Y%m%d %H:%M:%S.%f")
    else:
        dt = datetime.strptime(f"{day} {text}", "%Y%m%d %H:%M:%S")
    return _to_utc_epoch_ms(dt)


def parse_market_symbol(code: str) -> tuple[str, str]:
    if "." in code:
        market, _ = code.split(".", 1)
        return market, code
    return "HK", code


def ticker_df_to_rows(
    df: pd.DataFrame,
    provider: str,
    push_type: str,
    default_symbol: Optional[str] = None,
    trading_day: Optional[str] = None,
) -> List[TickRow]:
    if df is None or df.empty:
        return []

    rows: List[TickRow] = []
    inserted_at_ms = int(time.time() * 1000)

    for _, series in df.iterrows():
        item = series.to_dict()
        code = item.get("code") or item.get("symbol") or default_symbol
        if not code:
            logger.warning("missing code in ticker row: %s", item)
            continue

        market, symbol = parse_market_symbol(str(code))
        day = normalize_trading_day(item.get("trading_day") or item.get("date") or trading_day)
        ts_ms = parse_time_to_ts_ms(
            item.get("time") or item.get("timestamp") or item.get("ts"),
            day,
        )
        if day is None:
            day = trading_day_from_ts(ts_ms)

        rows.append(
            TickRow(
                market=market,
                symbol=symbol,
                ts_ms=ts_ms,
                price=_to_float(item.get("price")),
                volume=_to_int(item.get("volume")),
                turnover=_to_float(item.get("turnover")),
                direction=_to_str(item.get("ticker_direction") or item.get("direction")),
                seq=_to_int(item.get("sequence") or item.get("seq")),
                tick_type=_to_str(item.get("type") or item.get("tick_type")),
                push_type=push_type,
                provider=provider,
                trading_day=day,
                inserted_at_ms=inserted_at_ms,
            )
        )

    return rows


def _to_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_str(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
