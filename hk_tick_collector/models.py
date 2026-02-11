from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
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
    provider: Optional[str]
    trading_day: str
    recv_ts_ms: int
    inserted_at_ms: int

    def as_tuple(self) -> tuple:
        return (
            self.market,
            self.symbol,
            self.ts_ms,
            self.price,
            self.volume,
            self.turnover,
            self.direction,
            self.seq,
            self.tick_type,
            self.push_type,
            self.provider,
            self.trading_day,
            self.recv_ts_ms,
            self.inserted_at_ms,
        )
