from __future__ import annotations

from typing import Any

import pandas as pd

from .models import TickRow
from .utils import HK_TZ, ensure_symbol_prefix, parse_time_to_ms, trading_day_from_ts


def _coerce_int(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_futu_df(df: pd.DataFrame, market: str, provider: str = "futu") -> list[TickRow]:
    ticks: list[TickRow] = []
    if df is None or df.empty:
        return ticks
    for _, row in df.iterrows():
        code = str(row.get("code", "")).strip()
        symbol = ensure_symbol_prefix(code, market)
        time_str = str(row.get("time", "")).strip()
        ts_ms = parse_time_to_ms(time_str, HK_TZ)
        trading_day = trading_day_from_ts(ts_ms, HK_TZ)
        tick = TickRow(
            market=market,
            symbol=symbol,
            ts_ms=ts_ms,
            price=_coerce_float(row.get("price")),
            volume=_coerce_int(row.get("volume")),
            turnover=_coerce_float(row.get("turnover")),
            direction=str(row.get("ticker_direction", "")).strip() or None,
            seq=_coerce_int(row.get("sequence")),
            tick_type=str(row.get("type", "")).strip() or None,
            push_type=str(row.get("push_data_type", "")).strip() or None,
            provider=provider,
            trading_day=trading_day,
        )
        ticks.append(tick)
    return ticks
