from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Iterable, List, Optional

import pandas as pd

from .models import TickRow

logger = logging.getLogger(__name__)


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
    return datetime.fromtimestamp(ts_ms / 1000.0).strftime("%Y%m%d")


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
    return datetime.fromisoformat(text)


def parse_time_to_ts_ms(value: object, trading_day: Optional[str]) -> int:
    if value is None:
        raise ValueError("missing time value")
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 1e12:
            return int(numeric)
        if numeric > 1e9:
            return int(numeric * 1000)
        return int(numeric)

    text = str(value).strip()
    if text.isdigit():
        numeric = int(text)
        if numeric > 1e12:
            return numeric
        if numeric > 1e9:
            return numeric * 1000
        return numeric

    if any(token in text for token in ("-", "/", " ")):
        dt = _parse_datetime(text)
        return int(dt.timestamp() * 1000)

    # time-only string (HH:MM:SS[.ms])
    day = normalize_trading_day(trading_day)
    if day is None:
        day = datetime.now().strftime("%Y%m%d")
    if "." in text:
        dt = datetime.strptime(f"{day} {text}", "%Y%m%d %H:%M:%S.%f")
    else:
        dt = datetime.strptime(f"{day} {text}", "%Y%m%d %H:%M:%S")
    return int(dt.timestamp() * 1000)


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
