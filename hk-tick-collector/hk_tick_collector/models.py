from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class TickRow:
    market: str
    symbol: str
    ts_ms: int
    price: Optional[float]
    volume: Optional[int]
    turnover: Optional[float]
    direction: Optional[str]
    seq: Optional[int]
    tick_type: Optional[str]
    push_type: Optional[str]
    provider: str
    trading_day: str
    inserted_at_ms: Optional[int] = None
