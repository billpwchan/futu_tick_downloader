from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque, Iterable

from ..models import TickRow
from .config import QualityConfig, TradingSession


@dataclass(frozen=True)
class HardGapRecord:
    trading_day: str
    symbol: str
    gap_start_ts_ms: int
    gap_end_ts_ms: int
    gap_sec: float
    reason: str
    meta_json: str

    def as_tuple(self, detected_at_ms: int) -> tuple[object, ...]:
        return (
            self.trading_day,
            self.symbol,
            self.gap_start_ts_ms,
            self.gap_end_ts_ms,
            self.gap_sec,
            detected_at_ms,
            self.reason,
            self.meta_json,
        )


@dataclass(frozen=True)
class SoftStallObservation:
    trading_day: str
    symbol: str
    stall_start_ts_ms: int
    stall_end_ts_ms: int
    stall_sec: float
    meta_json: str


@dataclass(frozen=True)
class _StateSnapshot:
    last_ts_ms: int | None
    recent_ts_ms: tuple[int, ...]


@dataclass(frozen=True)
class GapDetectionPlan:
    hard_gaps: tuple[HardGapRecord, ...]
    soft_stalls: tuple[SoftStallObservation, ...]
    next_states: dict[str, _StateSnapshot]


@dataclass
class _SymbolState:
    last_ts_ms: int | None
    recent_ts_ms: Deque[int]


class GapDetector:
    def __init__(self, config: QualityConfig) -> None:
        self._config = config
        self._states: dict[str, _SymbolState] = {}
        self._session_cache = tuple(config.sessions)
        self._tzinfo = config.tzinfo
        self._active_window_ms = int(config.gap_active_window_sec * 1000)

    @property
    def enabled(self) -> bool:
        return bool(self._config.gap_enabled)

    def build_plan(self, rows: Iterable[TickRow]) -> GapDetectionPlan:
        grouped: dict[str, list[TickRow]] = defaultdict(list)
        for row in rows:
            if not row.symbol:
                continue
            grouped[row.symbol].append(row)

        hard_gaps: list[HardGapRecord] = []
        soft_stalls: list[SoftStallObservation] = []
        next_states: dict[str, _StateSnapshot] = {}

        for symbol, symbol_rows in grouped.items():
            ordered = sorted(symbol_rows, key=lambda item: (int(item.ts_ms), item.seq or -1))
            state = self._states.get(symbol)
            if state is None:
                last_ts_ms: int | None = None
                recent = deque()
            else:
                last_ts_ms = state.last_ts_ms
                recent = deque(state.recent_ts_ms)

            for row in ordered:
                curr_ts = int(row.ts_ms)
                self._trim_recent(recent=recent, current_ts_ms=curr_ts)
                active_count = len(recent) + 1
                active = active_count >= self._config.gap_active_min_ticks

                if last_ts_ms is not None and curr_ts > last_ts_ms and active:
                    prev_session_idx = self._session_index(last_ts_ms)
                    curr_session_idx = self._session_index(curr_ts)
                    if (
                        prev_session_idx is not None
                        and curr_session_idx is not None
                        and prev_session_idx == curr_session_idx
                    ):
                        delta_sec = (curr_ts - last_ts_ms) / 1000.0
                        if delta_sec > self._config.gap_threshold_sec:
                            hard_gaps.append(
                                HardGapRecord(
                                    trading_day=row.trading_day,
                                    symbol=symbol,
                                    gap_start_ts_ms=last_ts_ms,
                                    gap_end_ts_ms=curr_ts,
                                    gap_sec=round(delta_sec, 3),
                                    reason="hard_gap",
                                    meta_json=json.dumps(
                                        {
                                            "prev_ts_ms": last_ts_ms,
                                            "curr_ts_ms": curr_ts,
                                            "gap_threshold_sec": self._config.gap_threshold_sec,
                                            "active_window_sec": self._config.gap_active_window_sec,
                                            "active_min_ticks": self._config.gap_active_min_ticks,
                                            "active_count": active_count,
                                            "session": self._session_cache[curr_session_idx].label,
                                        },
                                        ensure_ascii=True,
                                        separators=(",", ":"),
                                    ),
                                )
                            )
                        elif delta_sec > self._config.gap_stall_warn_sec:
                            soft_stalls.append(
                                SoftStallObservation(
                                    trading_day=row.trading_day,
                                    symbol=symbol,
                                    stall_start_ts_ms=last_ts_ms,
                                    stall_end_ts_ms=curr_ts,
                                    stall_sec=round(delta_sec, 3),
                                    meta_json=json.dumps(
                                        {
                                            "prev_ts_ms": last_ts_ms,
                                            "curr_ts_ms": curr_ts,
                                            "stall_warn_sec": self._config.gap_stall_warn_sec,
                                            "active_count": active_count,
                                            "session": self._session_cache[curr_session_idx].label,
                                        },
                                        ensure_ascii=True,
                                        separators=(",", ":"),
                                    ),
                                )
                            )

                if last_ts_ms is None or curr_ts > last_ts_ms:
                    last_ts_ms = curr_ts
                    recent.append(curr_ts)
                    self._trim_recent(recent=recent, current_ts_ms=curr_ts)

            next_states[symbol] = _StateSnapshot(
                last_ts_ms=last_ts_ms, recent_ts_ms=tuple(int(value) for value in recent)
            )

        return GapDetectionPlan(
            hard_gaps=tuple(hard_gaps),
            soft_stalls=tuple(soft_stalls),
            next_states=next_states,
        )

    def apply_plan(self, plan: GapDetectionPlan) -> None:
        for symbol, snapshot in plan.next_states.items():
            self._states[symbol] = _SymbolState(
                last_ts_ms=snapshot.last_ts_ms,
                recent_ts_ms=deque(snapshot.recent_ts_ms),
            )

    def _trim_recent(self, *, recent: Deque[int], current_ts_ms: int) -> None:
        min_ts_ms = int(current_ts_ms) - self._active_window_ms
        while recent and recent[0] < min_ts_ms:
            recent.popleft()

    def _session_index(self, ts_ms: int) -> int | None:
        local = datetime.fromtimestamp(ts_ms / 1000.0, tz=self._tzinfo)
        if local.weekday() >= 5:
            return None
        current = local.time().replace(tzinfo=None)
        for idx, session in enumerate(self._session_cache):
            if _time_in_session(current, session):
                return idx
        return None


def _time_in_session(value, session: TradingSession) -> bool:
    return session.start <= value < session.end
