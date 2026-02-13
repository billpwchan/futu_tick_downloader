from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, time as dt_time
from enum import Enum
from html import escape
from importlib import metadata
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Dict, Optional, Sequence
from zoneinfo import ZoneInfo

from hk_tick_collector import __version__ as PACKAGE_VERSION

logger = logging.getLogger(__name__)

HK_TZ = ZoneInfo("Asia/Hong_Kong")
NOTIFY_SCHEMA_VERSION = "v2.2"
TELEGRAM_MAX_MESSAGE_CHARS = 4096
WARN_CADENCE_SEC = 600
ALERT_CADENCE_SEC = 180
PREOPEN_CADENCE_SEC = 1800
OPEN_CADENCE_SEC = 900
LUNCH_CADENCE_SEC = 1800
AFTER_HOURS_CADENCE_SEC = 3600
HOLIDAY_CLOSED_CYCLES = 3
HOLIDAY_CLOSED_P50_AGE_SEC = 600.0
HOLIDAY_CLOSED_P95_AGE_SEC = 900.0
OPEN_STALE_SYMBOL_AGE_SEC = 10.0
OFFHOURS_STALE_SYMBOL_AGE_SEC = 120.0
OPEN_STALE_BUCKETS = (10.0, 30.0, 60.0)
OFFHOURS_STALE_BUCKETS = (120.0, 300.0, 900.0)
_CALLBACK_MAX_BYTES = 64
_DEFAULT_RENDER_MODE = "product"


class RenderMode(str, Enum):
    PRODUCT = "product"
    OPS = "ops"


def _make_short_id(prefix: str) -> str:
    cleaned = "".join(ch for ch in prefix.lower() if ch.isalnum())[:8] or "id"
    return f"{cleaned}-{secrets.token_hex(4)}"


class NotifySeverity(str, Enum):
    OK = "OK"
    WARN = "WARN"
    ALERT = "ALERT"


_SEVERITY_RANK = {
    NotifySeverity.OK: 0,
    NotifySeverity.WARN: 1,
    NotifySeverity.ALERT: 2,
}


@dataclass(frozen=True)
class SymbolSnapshot:
    symbol: str
    last_tick_age_sec: float | None
    last_persisted_seq: int | None
    max_seq_lag: int


@dataclass(frozen=True)
class HealthSnapshot:
    created_at: datetime
    pid: int
    uptime_sec: int
    trading_day: str
    db_path: Path
    db_rows: int
    db_max_ts_utc: str
    drift_sec: float | None
    queue_size: int
    queue_maxsize: int
    push_rows_per_min: int
    poll_fetched: int
    poll_accepted: int
    persisted_rows_per_min: int
    dropped_duplicate: int
    symbols: Sequence[SymbolSnapshot]
    system_load1: float | None = None
    system_rss_mb: float | None = None
    system_disk_free_gb: float | None = None
    sid: str = field(default_factory=lambda: _make_short_id("sid"))


@dataclass(frozen=True)
class AlertEvent:
    created_at: datetime
    code: str
    key: str
    trading_day: str
    summary_lines: Sequence[str]
    suggestions: Sequence[str]
    severity: str = NotifySeverity.ALERT.value
    headline: str | None = None
    impact: str | None = None
    fingerprint: str | None = None
    sid: str | None = None
    duration_sec: int | None = None
    threshold_sec: int | None = None
    eid: str = field(default_factory=lambda: _make_short_id("eid"))


@dataclass(frozen=True)
class TelegramSendResult:
    ok: bool
    status_code: int
    retry_after: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class RenderedMessage:
    text: str
    parse_mode: str = "HTML"
    reply_markup: dict[str, Any] | None = None


@dataclass(frozen=True)
class HealthAssessment:
    severity: NotifySeverity
    conclusion: str
    impact: str
    needs_action: bool
    market_mode: str


@dataclass(frozen=True)
class _OutboundMessage:
    kind: str
    message: RenderedMessage
    severity: NotifySeverity
    fingerprint: str
    sid: str | None
    eid: str | None
    thread_id: int | None = None
    chat_id: str | None = None


@dataclass
class _DedupeRecord:
    first_seen_at: float
    last_seen_at: float
    last_sent_at: float
    last_sent_severity: NotifySeverity
    next_escalation_index: int
    last_event_id: str | None
    last_snapshot_id: str | None


@dataclass
class _DailyDigestState:
    trading_day: str
    start_db_rows: int | None = None
    total_rows: int = 0
    peak_rows_per_min: int = 0
    max_lag_sec: float = 0.0
    alert_count: int = 0
    recovered_count: int = 0
    db_rows: int = 0
    db_path: str = "n/a"


class SlidingWindowRateLimiter:
    def __init__(
        self,
        limit_per_window: int,
        window_sec: float = 60.0,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = max(1, int(limit_per_window))
        self._window_sec = max(1.0, float(window_sec))
        self._now_fn = now_fn
        self._timestamps: Deque[float] = deque()

    @property
    def limit_per_window(self) -> int:
        return self._limit

    def reserve_delay(self) -> float:
        now = self._now_fn()
        while self._timestamps and (now - self._timestamps[0]) >= self._window_sec:
            self._timestamps.popleft()

        if len(self._timestamps) < self._limit:
            self._timestamps.append(now)
            return 0.0
        return max(0.0, self._window_sec - (now - self._timestamps[0]))


def _severity_from(value: str | NotifySeverity) -> NotifySeverity:
    if isinstance(value, NotifySeverity):
        return value
    text = str(value).strip().upper()
    if text == NotifySeverity.WARN.value:
        return NotifySeverity.WARN
    if text == NotifySeverity.ALERT.value:
        return NotifySeverity.ALERT
    return NotifySeverity.OK


def _severity_rank(value: str | NotifySeverity) -> int:
    return _SEVERITY_RANK[_severity_from(value)]


def _normalize_render_mode(value: str | RenderMode | None) -> RenderMode:
    if isinstance(value, RenderMode):
        return value
    text = str(value or _DEFAULT_RENDER_MODE).strip().lower()
    if text == RenderMode.OPS.value:
        return RenderMode.OPS
    return RenderMode.PRODUCT


def _format_uptime(seconds: int) -> str:
    sec = max(0, int(seconds))
    hours = sec // 3600
    minutes = (sec % 3600) // 60
    remainder = sec % 60
    return f"{hours:02d}:{minutes:02d}:{remainder:02d}"


def _format_float(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _format_int(value: int | None) -> str:
    if value is None:
        return "n/a"
    return str(int(value))


def _resolve_collector_version() -> str:
    try:
        version = metadata.version("hk-tick-collector")
        if version:
            return version
    except metadata.PackageNotFoundError:
        pass
    except Exception:
        logger.debug("collector_version_resolve_failed", exc_info=True)
    return PACKAGE_VERSION or "unknown"


def _max_symbol_age_sec(snapshot: HealthSnapshot) -> float | None:
    ages = [s.last_tick_age_sec for s in snapshot.symbols if s.last_tick_age_sec is not None]
    if not ages:
        return None
    return max(ages)


def _max_symbol_lag(snapshot: HealthSnapshot) -> int:
    if not snapshot.symbols:
        return 0
    return max(max(0, item.max_seq_lag) for item in snapshot.symbols)


def _queue_utilization_pct(snapshot: HealthSnapshot) -> float:
    if snapshot.queue_maxsize <= 0:
        return 0.0
    return (snapshot.queue_size / snapshot.queue_maxsize) * 100.0


def _infer_market_mode(created_at: datetime) -> str:
    local = created_at.astimezone(HK_TZ)
    if local.weekday() >= 5:
        return "after-hours"

    t = local.timetz().replace(tzinfo=None)
    if dt_time(9, 0) <= t < dt_time(9, 30):
        return "pre-open"
    if dt_time(9, 30) <= t < dt_time(12, 0):
        return "open"
    if dt_time(12, 0) <= t < dt_time(13, 0):
        return "lunch-break"
    if dt_time(13, 0) <= t < dt_time(16, 0):
        return "open"
    return "after-hours"


def _is_trading_mode(mode: str) -> bool:
    return mode in {"pre-open", "open", "lunch-break"}


def _market_mode_label(mode: str) -> str:
    mapping = {
        "pre-open": "開盤前",
        "open": "盤中",
        "lunch-break": "午休",
        "after-hours": "收盤後",
        "holiday-closed": "休市日",
    }
    return mapping.get(mode, mode)


def _is_after_close_window(created_at: datetime) -> bool:
    local = created_at.astimezone(HK_TZ)
    if local.weekday() >= 5:
        return False
    t = local.timetz().replace(tzinfo=None)
    return t >= dt_time(16, 0)


def _format_duration(seconds: int | float) -> str:
    value = max(0, int(seconds))
    hours = value // 3600
    minutes = (value % 3600) // 60
    if hours > 0:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def _seconds_to_open(created_at: datetime) -> int:
    local = created_at.astimezone(HK_TZ)
    open_at = local.replace(hour=9, minute=30, second=0, microsecond=0)
    return max(0, int((open_at - local).total_seconds()))


def _seconds_since_close(created_at: datetime) -> int:
    local = created_at.astimezone(HK_TZ)
    close_at = local.replace(hour=16, minute=0, second=0, microsecond=0)
    return max(0, int((local - close_at).total_seconds()))


def _symbol_ages(snapshot: HealthSnapshot) -> list[float]:
    return [age for age in (item.last_tick_age_sec for item in snapshot.symbols) if age is not None]


def _symbol_age_pairs(snapshot: HealthSnapshot) -> list[tuple[str, float]]:
    pairs: list[tuple[str, float]] = []
    for item in snapshot.symbols:
        if item.last_tick_age_sec is None:
            continue
        pairs.append((item.symbol, max(0.0, float(item.last_tick_age_sec))))
    return pairs


def _count_stale_symbols(snapshot: HealthSnapshot, *, threshold_sec: float) -> int:
    return sum(1 for age in _symbol_ages(snapshot) if age >= threshold_sec)


def _stale_bucket_counts(snapshot: HealthSnapshot, *, thresholds: Sequence[float]) -> list[int]:
    ages = _symbol_ages(snapshot)
    return [sum(1 for age in ages if age >= threshold) for threshold in thresholds]


def _stale_bucket_label(thresholds: Sequence[float]) -> str:
    parts = [f">={int(value)}s" for value in thresholds]
    return "/".join(parts)


def _top_stale_symbols(snapshot: HealthSnapshot, *, limit: int = 5) -> list[tuple[str, float]]:
    pairs = sorted(_symbol_age_pairs(snapshot), key=lambda item: item[1], reverse=True)
    return pairs[: max(1, int(limit))]


def _format_top_stale(pairs: Sequence[tuple[str, float]]) -> str:
    if not pairs:
        return "n/a"
    return ",".join(f"{symbol}({age:.1f}s)" for symbol, age in pairs)


def _ingest_rows_per_min(snapshot: HealthSnapshot) -> int:
    push_rows = max(0, int(snapshot.push_rows_per_min))
    poll_rows = max(0, int(snapshot.poll_accepted))
    return push_rows + poll_rows


def _write_efficiency_pct(snapshot: HealthSnapshot) -> float:
    ingest_rows = _ingest_rows_per_min(snapshot)
    persisted_rows = max(0, int(snapshot.persisted_rows_per_min))
    baseline = max(1, ingest_rows)
    return min(999.0, (persisted_rows / baseline) * 100.0)


def _percentile_float(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    clipped = max(0.0, min(1.0, float(percentile)))
    ordered = sorted(values)
    index = int((len(ordered) - 1) * clipped)
    return float(ordered[index])


def truncate_rendered_message(
    message: RenderedMessage,
    max_chars: int = TELEGRAM_MAX_MESSAGE_CHARS,
) -> RenderedMessage:
    limit = max(1, int(max_chars))
    text = message.text
    if len(text) <= limit:
        return message

    if message.parse_mode.upper() == "HTML":
        start_tag = "<blockquote expandable>"
        end_tag = "</blockquote>"
        start_idx = text.find(start_tag)
        end_idx = text.rfind(end_tag)
        if start_idx >= 0 and end_idx > start_idx:
            head = text[:start_idx]
            detail = text[start_idx + len(start_tag) : end_idx]
            tail = text[end_idx + len(end_tag) :]
            suffix = "\n... [truncated]"
            keep = limit - len(head) - len(start_tag) - len(end_tag) - len(tail) - len(suffix)
            if keep > 0:
                clipped = detail[:keep] + suffix
                return RenderedMessage(
                    text=f"{head}{start_tag}{clipped}{end_tag}{tail}",
                    parse_mode=message.parse_mode,
                )

    suffix = "\n... [truncated]"
    keep = max(0, limit - len(suffix))
    truncated = text[:keep] + suffix
    return RenderedMessage(text=truncated[:limit], parse_mode=message.parse_mode)


class DedupeStore:
    def __init__(self) -> None:
        self._records: Dict[str, _DedupeRecord] = {}

    def evaluate(
        self,
        *,
        fingerprint: str,
        severity: NotifySeverity,
        now: float,
        cooldown_sec: int,
        escalation_steps: Sequence[int],
        event_id: str | None = None,
        snapshot_id: str | None = None,
    ) -> tuple[bool, str]:
        key = fingerprint.strip() or "unknown"
        steps = self._normalized_steps(escalation_steps)
        cooldown = max(1, int(cooldown_sec))

        record = self._records.get(key)
        if record is None:
            next_idx = self._first_positive_step_index(steps)
            self._records[key] = _DedupeRecord(
                first_seen_at=now,
                last_seen_at=now,
                last_sent_at=now,
                last_sent_severity=severity,
                next_escalation_index=next_idx,
                last_event_id=event_id,
                last_snapshot_id=snapshot_id,
            )
            return True, "new"

        record.last_seen_at = now

        if _severity_rank(severity) > _severity_rank(record.last_sent_severity):
            record.last_sent_severity = severity
            record.last_sent_at = now
            if event_id:
                record.last_event_id = event_id
            if snapshot_id:
                record.last_snapshot_id = snapshot_id
            return True, "severity_upgraded"

        incident_age = max(0.0, now - record.first_seen_at)
        if record.next_escalation_index < len(steps):
            step = steps[record.next_escalation_index]
            if incident_age >= step and (now - record.last_sent_at) >= cooldown:
                record.next_escalation_index += 1
                record.last_sent_at = now
                if event_id:
                    record.last_event_id = event_id
                if snapshot_id:
                    record.last_snapshot_id = snapshot_id
                return True, f"escalation_step_{step}s"

        if (now - record.last_sent_at) >= cooldown:
            record.last_sent_at = now
            if event_id:
                record.last_event_id = event_id
            if snapshot_id:
                record.last_snapshot_id = snapshot_id
            return True, "cooldown_elapsed"

        return False, "cooldown_active"

    def resolve(self, fingerprint: str) -> _DedupeRecord | None:
        key = fingerprint.strip() or "unknown"
        return self._records.pop(key, None)

    @staticmethod
    def _normalized_steps(values: Sequence[int]) -> list[int]:
        cleaned = sorted({max(0, int(item)) for item in values})
        if not cleaned:
            return [0]
        return cleaned

    @staticmethod
    def _first_positive_step_index(steps: Sequence[int]) -> int:
        for idx, step in enumerate(steps):
            if step > 0:
                return idx
        return len(steps)


class AlertStateMachine:
    def __init__(self, *, drift_warn_sec: int) -> None:
        self._drift_warn_sec = max(1, int(drift_warn_sec))
        self._last_health_severity: NotifySeverity | None = None
        self._last_health_sent_at: float | None = None
        self._last_persisted_rows_per_min: int | None = None
        self._holiday_closed_cycles = 0

    def assess_health(self, snapshot: HealthSnapshot) -> HealthAssessment:
        mode = _infer_market_mode(snapshot.created_at)
        if mode == "open":
            if self._is_holiday_closed_candidate(snapshot):
                mode = "holiday-closed"
        else:
            self._holiday_closed_cycles = 0

        freshness_sec = abs(snapshot.drift_sec) if snapshot.drift_sec is not None else None
        queue_pct = _queue_utilization_pct(snapshot)
        persisted = max(0, int(snapshot.persisted_rows_per_min))
        max_lag = _max_symbol_lag(snapshot)
        queue = max(0, int(snapshot.queue_size))

        low_persist = False
        if self._last_persisted_rows_per_min is not None and self._last_persisted_rows_per_min > 0:
            low_persist = 0 < persisted < max(1, int(self._last_persisted_rows_per_min * 0.3))

        if mode == "open":
            if persisted == 0 and (queue > 0 or max_lag > 0):
                severity = NotifySeverity.WARN
                conclusion = "注意：盤中疑似停寫，建議立即檢查"
                impact = "若持續，可能造成即時資料落後與積壓擴大"
                needs_action = True
            elif (
                (freshness_sec is not None and freshness_sec >= self._drift_warn_sec)
                or queue_pct >= 60.0
                or low_persist
            ):
                severity = NotifySeverity.WARN
                conclusion = "注意：盤中品質指標退化"
                impact = "目前仍可運作，但延遲與吞吐可能持續惡化"
                needs_action = True
            else:
                severity = NotifySeverity.OK
                conclusion = "正常：盤中採集與寫入穩定"
                impact = "目前沒有明顯風險，暫時不需要人工介入"
                needs_action = False
        elif mode == "holiday-closed":
            if queue > 0 and persisted <= 0:
                severity = NotifySeverity.WARN
                conclusion = "注意：休市期間仍有佇列積壓"
                impact = "可能影響資料完整性，建議確認寫入是否已排空"
                needs_action = True
            else:
                severity = NotifySeverity.OK
                conclusion = "正常：休市日服務平穩"
                impact = "無交易流量屬預期狀態，暫不需人工介入"
                needs_action = False
        elif mode == "pre-open":
            if queue_pct >= 80.0:
                severity = NotifySeverity.WARN
                conclusion = "注意：開盤前佇列偏高"
                impact = "若未在開盤前回落，盤中可能出現短暫延遲"
                needs_action = True
            else:
                severity = NotifySeverity.OK
                conclusion = "正常：開盤前系統就緒"
                impact = "可待開盤後持續觀察吞吐與延遲"
                needs_action = False
        elif mode == "lunch-break":
            if queue > 0 and persisted <= 0:
                severity = NotifySeverity.WARN
                conclusion = "注意：午休期間存在積壓"
                impact = "若持續到下午開盤，可能出現補寫壓力"
                needs_action = True
            else:
                severity = NotifySeverity.OK
                conclusion = "正常：午休狀態平穩"
                impact = "目前不需人工介入"
                needs_action = False
        else:
            if queue > 0 and persisted <= 0:
                severity = NotifySeverity.WARN
                conclusion = "注意：收盤後仍有佇列積壓"
                impact = "可能影響收盤資料完整性，建議追蹤恢復"
                needs_action = True
            else:
                severity = NotifySeverity.OK
                conclusion = "正常：收盤後服務平穩"
                impact = "目前不需人工介入"
                needs_action = False

        self._last_persisted_rows_per_min = persisted
        return HealthAssessment(
            severity=severity,
            conclusion=conclusion,
            impact=impact,
            needs_action=needs_action,
            market_mode=mode,
        )

    def _is_holiday_closed_candidate(self, snapshot: HealthSnapshot) -> bool:
        if (
            max(0, int(snapshot.persisted_rows_per_min)) > 0
            or max(0, int(snapshot.push_rows_per_min)) > 0
            or max(0, int(snapshot.poll_accepted)) > 0
            or max(0, int(snapshot.queue_size)) > 0
        ):
            self._holiday_closed_cycles = 0
            return False

        ages = _symbol_ages(snapshot)
        if not ages:
            self._holiday_closed_cycles = 0
            return False
        p50_age = _percentile_float(ages, 0.50)
        p95_age = _percentile_float(ages, 0.95)
        if p50_age is None or p95_age is None:
            self._holiday_closed_cycles = 0
            return False
        if p50_age < HOLIDAY_CLOSED_P50_AGE_SEC or p95_age < HOLIDAY_CLOSED_P95_AGE_SEC:
            self._holiday_closed_cycles = 0
            return False

        self._holiday_closed_cycles += 1
        return self._holiday_closed_cycles >= HOLIDAY_CLOSED_CYCLES

    def should_emit_health(
        self,
        *,
        assessment: HealthAssessment,
        now: float,
        interval_sec: int,
        meaningful_change: bool,
    ) -> tuple[bool, str]:
        if self._last_health_severity is None:
            self._last_health_severity = assessment.severity
            self._last_health_sent_at = now
            return True, "bootstrap"

        state_changed = assessment.severity != self._last_health_severity
        cadence_elapsed = self._last_health_sent_at is None or (
            now - self._last_health_sent_at
        ) >= max(1, int(interval_sec))

        if state_changed:
            self._last_health_severity = assessment.severity
            self._last_health_sent_at = now
            return True, "state_changed"
        if cadence_elapsed:
            self._last_health_severity = assessment.severity
            self._last_health_sent_at = now
            if meaningful_change:
                return True, "cadence_with_change"
            return True, "cadence"
        return False, "suppressed"


class MessageRenderer:
    def __init__(self, *, parse_mode: str = "HTML") -> None:
        mode = (parse_mode or "HTML").strip().upper()
        self._parse_mode = "HTML" if mode == "HTML" else ""

    @property
    def parse_mode(self) -> str:
        return self._parse_mode

    def render_health(
        self,
        *,
        snapshot: HealthSnapshot,
        assessment: HealthAssessment,
        hostname: str,
        instance_id: str | None,
        include_system_metrics: bool,
        digest: _DailyDigestState | None = None,
    ) -> RenderedMessage:
        if self._parse_mode != "HTML":
            return self._render_health_plain(snapshot, assessment, hostname, instance_id)

        host_text = hostname if not instance_id else f"{hostname} / {instance_id}"
        lag_sec = abs(snapshot.drift_sec) if snapshot.drift_sec is not None else None
        market_label = _market_mode_label(assessment.market_mode)
        symbol_count = len(snapshot.symbols)
        symbol_ages = _symbol_ages(snapshot)
        p50_age = _percentile_float(symbol_ages, 0.50)
        p95_age = _percentile_float(symbol_ages, 0.95)
        p99_age = _percentile_float(symbol_ages, 0.99)
        stale_threshold_sec = (
            OPEN_STALE_SYMBOL_AGE_SEC
            if assessment.market_mode == "open"
            else OFFHOURS_STALE_SYMBOL_AGE_SEC
        )
        stale_bucket_thresholds = (
            OPEN_STALE_BUCKETS if assessment.market_mode == "open" else OFFHOURS_STALE_BUCKETS
        )
        stale_symbols = _count_stale_symbols(snapshot, threshold_sec=stale_threshold_sec)
        stale_bucket_counts = _stale_bucket_counts(snapshot, thresholds=stale_bucket_thresholds)
        stale_bucket_text = "/".join(str(value) for value in stale_bucket_counts)
        top_stale_text = _format_top_stale(_top_stale_symbols(snapshot, limit=5))
        ingest_rows_per_min = _ingest_rows_per_min(snapshot)
        persisted_rows_per_min = max(0, int(snapshot.persisted_rows_per_min))
        write_efficiency = _write_efficiency_pct(snapshot)
        icon = "🟢" if assessment.severity == NotifySeverity.OK else "🟡"
        system_line = (
            f"資源：load1={_format_float(snapshot.system_load1, 2)} "
            f"rss={_format_float(snapshot.system_rss_mb, 1)}MB "
            f"disk_free={_format_float(snapshot.system_disk_free_gb, 2)}GB"
        )
        progress_line = (
            f"進度：ingest/min={ingest_rows_per_min} | persist/min={persisted_rows_per_min} | "
            f"write_eff={write_efficiency:.1f}% | stale_symbols={stale_symbols} | "
            f"stale_bucket({_stale_bucket_label(stale_bucket_thresholds)})={stale_bucket_text} | "
            f"top5_stale={top_stale_text}"
        )

        if assessment.market_mode == "pre-open":
            metrics_line = (
                f"指標：狀態={market_label} | 距開盤={_format_duration(_seconds_to_open(snapshot.created_at))} | "
                f"symbols={symbol_count} | stale_symbols={stale_symbols} | "
                f"queue={snapshot.queue_size}/{snapshot.queue_maxsize} | "
                f"last_update_at={snapshot.db_max_ts_utc}"
            )
        elif assessment.market_mode == "open":
            metrics_line = (
                f"指標：狀態={market_label} | ingest_lag={_format_float(lag_sec)}s | "
                f"persisted={snapshot.persisted_rows_per_min}/min | "
                f"queue={snapshot.queue_size}/{snapshot.queue_maxsize} | "
                f"symbols={symbol_count} | stale_symbols={stale_symbols} | "
                f"p95_age={_format_float(p95_age)}s | p99_age={_format_float(p99_age)}s"
            )
        elif assessment.market_mode == "lunch-break":
            metrics_line = (
                f"指標：狀態={market_label} | symbols={symbol_count} | stale_symbols={stale_symbols} | "
                f"queue={snapshot.queue_size}/{snapshot.queue_maxsize} | "
                f"last_update_at={snapshot.db_max_ts_utc}"
            )
        elif assessment.market_mode == "holiday-closed":
            db_growth = "n/a"
            if digest is not None and digest.start_db_rows is not None:
                db_growth = f"{digest.db_rows - digest.start_db_rows:+,} rows"
            metrics_line = (
                f"指標：狀態={market_label} | market=holiday-closed | symbols={symbol_count} | "
                f"close_snapshot_ok={'true' if snapshot.queue_size == 0 else 'false'} | "
                f"db_growth_today={db_growth} | last_update_at={snapshot.db_max_ts_utc} | "
                f"p50_age={_format_float(p50_age)}s"
            )
        else:
            db_growth = "n/a"
            if digest is not None and digest.start_db_rows is not None:
                db_growth = f"{digest.db_rows - digest.start_db_rows:+,} rows"
            metrics_line = (
                f"指標：狀態={market_label} | 距收盤={_format_duration(_seconds_since_close(snapshot.created_at))} | "
                f"symbols={symbol_count} | close_snapshot_ok={'true' if snapshot.queue_size == 0 else 'false'} | "
                f"db_growth_today={db_growth} | last_update_at={snapshot.db_max_ts_utc}"
            )

        lines = [
            f"<b>{icon} HK Tick Collector {'正常' if assessment.severity == NotifySeverity.OK else '注意'}</b>",
            f"結論：{escape(assessment.conclusion)}",
            escape(metrics_line),
            escape(progress_line),
        ]
        if assessment.severity == NotifySeverity.WARN:
            lines.append('建議：scripts/hk-tickctl logs --ops --since "20 minutes ago"')
        lines.append(f"主機：{escape(host_text)}")
        if include_system_metrics:
            lines.append(escape(system_line))
        lines.append(f"sid={escape(snapshot.sid)}")
        return RenderedMessage(text="\n".join(lines), parse_mode=self._parse_mode)

    def render_alert(
        self,
        *,
        event: AlertEvent,
        hostname: str,
        instance_id: str | None,
        market_mode: str,
    ) -> RenderedMessage:
        severity = _severity_from(event.severity)
        if self._parse_mode != "HTML":
            return self._render_alert_plain(event, hostname, instance_id, market_mode, severity)

        host_text = hostname if not instance_id else f"{hostname} / {instance_id}"
        headline = event.headline or self._default_alert_headline(event.code, severity)
        impact = event.impact or self._default_alert_impact(event.code, severity)
        summary_text = " | ".join(event.summary_lines[:3]) if event.summary_lines else "n/a"
        suggest_limit = 2 if severity == NotifySeverity.ALERT else 1
        suggestions = [line for line in event.suggestions[:suggest_limit] if line]

        if severity == NotifySeverity.WARN:
            lines = [
                "<b>🟡 注意</b>",
                f"結論：{escape(headline)}",
                f"指標：原因={escape(event.code.upper())} | 可能影響={escape(impact)} | {escape(summary_text)}",
            ]
            if suggestions:
                lines.append(f"建議：{escape(suggestions[0])}")
            lines.extend(
                [
                    f"主機：{escape(host_text)}",
                    f"sid={escape(event.sid or 'n/a')}",
                ]
            )
            return RenderedMessage(text="\n".join(lines), parse_mode=self._parse_mode)

        duration_text = (
            f"{event.duration_sec}s/{event.threshold_sec}s"
            if event.duration_sec is not None and event.threshold_sec is not None
            else "n/a"
        )
        lines = [
            "<b>🔴 異常</b>",
            f"結論：{escape(headline)}",
            (
                "指標："
                f"事件={escape(event.code.upper())} | 持續={escape(duration_text)} | "
                f"影響={escape(impact)} | {escape(summary_text)}"
            ),
        ]
        for idx, command in enumerate(suggestions[:2], start=1):
            lines.append(f"建議{idx}：{escape(command)}")
        lines.extend(
            [
                f"主機：{escape(host_text)}",
                f"eid={escape(event.eid)} sid={escape(event.sid or 'n/a')}",
            ]
        )
        return RenderedMessage(text="\n".join(lines), parse_mode=self._parse_mode)

    def render_recovered(
        self,
        *,
        event: AlertEvent,
        hostname: str,
        instance_id: str | None,
    ) -> RenderedMessage:
        host_text = hostname if not instance_id else f"{hostname} / {instance_id}"
        summary_text = " | ".join(event.summary_lines[:2]) if event.summary_lines else "n/a"
        lines = [
            "<b>✅ 已恢復</b>",
            f"結論：{escape(event.code.upper())} 已恢復正常",
            f"指標：{escape(summary_text)}",
            f"主機：{escape(host_text)}",
            f"eid={escape(event.eid)} sid={escape(event.sid or 'n/a')}",
        ]
        return RenderedMessage(text="\n".join(lines), parse_mode=self._parse_mode)

    def render_daily_digest(
        self,
        *,
        snapshot: HealthSnapshot,
        digest: _DailyDigestState,
        hostname: str,
        instance_id: str | None,
    ) -> RenderedMessage:
        host_text = hostname if not instance_id else f"{hostname} / {instance_id}"
        lines = [
            "<b>📊 日報</b>",
            f"結論：{escape(digest.trading_day)} 收盤摘要",
            (
                "指標："
                f"今日總量={digest.total_rows} | 峰值={digest.peak_rows_per_min}/min | "
                f"最大延遲={digest.max_lag_sec:.1f}s | 告警次數={digest.alert_count} | "
                f"恢復次數={digest.recovered_count}"
            ),
            f"db：{escape(digest.db_path)} rows={digest.db_rows}",
            f"主機：{escape(host_text)}",
            f"sid={escape(snapshot.sid)}",
        ]
        return RenderedMessage(text="\n".join(lines), parse_mode=self._parse_mode)

    def _render_health_plain(
        self,
        snapshot: HealthSnapshot,
        assessment: HealthAssessment,
        hostname: str,
        instance_id: str | None,
    ) -> RenderedMessage:
        host_text = hostname if not instance_id else f"{hostname} ({instance_id})"
        ingest_rows_per_min = _ingest_rows_per_min(snapshot)
        write_efficiency = _write_efficiency_pct(snapshot)
        lines = [
            f"HK Tick Collector HEALTH {assessment.severity.value}",
            f"結論: {assessment.conclusion}",
            (
                f"指標: mode={_market_mode_label(assessment.market_mode)} "
                f"drift={_format_float(snapshot.drift_sec)}s "
                f"persisted/min={snapshot.persisted_rows_per_min} total={snapshot.db_rows}"
            ),
            (
                f"進度: ingest/min={ingest_rows_per_min} persist/min={snapshot.persisted_rows_per_min} "
                f"write_eff={write_efficiency:.1f}%"
            ),
            f"host={host_text} sid={snapshot.sid}",
        ]
        return RenderedMessage(text="\n".join(lines), parse_mode="")

    def _render_alert_plain(
        self,
        event: AlertEvent,
        hostname: str,
        instance_id: str | None,
        market_mode: str,
        severity: NotifySeverity,
    ) -> RenderedMessage:
        host_text = hostname if not instance_id else f"{hostname} ({instance_id})"
        lines = [
            f"HK Tick Collector {event.code} {severity.value}",
            f"day={event.trading_day} mode={market_mode}",
            f"host={host_text} eid={event.eid} sid={event.sid or 'n/a'}",
        ]
        lines.extend(event.summary_lines[:3])
        if severity == NotifySeverity.ALERT and event.suggestions:
            lines.extend(event.suggestions[:2])
        elif severity == NotifySeverity.WARN and event.suggestions:
            lines.extend(event.suggestions[:1])
        return RenderedMessage(text="\n".join(lines), parse_mode="")

    def _default_alert_headline(self, code: str, severity: NotifySeverity) -> str:
        if code.upper() == "PERSIST_STALL":
            return "異常：持久化停滯，資料可能未落庫"
        if code.upper() == "DISCONNECT":
            return "異常：與 OpenD 連線中斷"
        if code.upper() == "SQLITE_BUSY":
            return "異常：SQLite 鎖競爭升高"
        if severity == NotifySeverity.ALERT:
            return "異常：偵測到需要立即處理的事件"
        return "注意：偵測到風險事件"

    def _default_alert_impact(self, code: str, severity: NotifySeverity) -> str:
        if code.upper() == "PERSIST_STALL":
            return "新資料可能無法寫入 SQLite，時序會持續落後"
        if code.upper() == "DISCONNECT":
            return "可能短暫影響即時資料完整性，重連成功後可恢復"
        if code.upper() == "SQLITE_BUSY":
            return "寫入吞吐可能下降，若持續將增加延遲與積壓"
        if severity == NotifySeverity.ALERT:
            return "資料可靠性可能受影響，建議立即排查"
        return "目前為退化狀態，建議持續觀察"


class MessageComposer:
    def __init__(
        self,
        *,
        parse_mode: str = "HTML",
        default_render_mode: str | RenderMode = _DEFAULT_RENDER_MODE,
    ) -> None:
        self._ops_renderer = MessageRenderer(parse_mode=parse_mode)
        self._parse_mode = self._ops_renderer.parse_mode
        self._default_render_mode = _normalize_render_mode(default_render_mode)

    @property
    def parse_mode(self) -> str:
        return self._parse_mode

    @property
    def default_render_mode(self) -> RenderMode:
        return self._default_render_mode

    def render_health(
        self,
        *,
        snapshot: HealthSnapshot,
        assessment: HealthAssessment,
        hostname: str,
        instance_id: str | None,
        include_system_metrics: bool,
        digest: _DailyDigestState | None = None,
        render_mode: str | RenderMode | None = None,
    ) -> RenderedMessage:
        mode = _normalize_render_mode(render_mode or self._default_render_mode)
        if mode == RenderMode.OPS:
            return self._ops_renderer.render_health(
                snapshot=snapshot,
                assessment=assessment,
                hostname=hostname,
                instance_id=instance_id,
                include_system_metrics=include_system_metrics,
                digest=digest,
            )
        return self._render_health_product(
            snapshot=snapshot,
            assessment=assessment,
            hostname=hostname,
            instance_id=instance_id,
        )

    def render_alert(
        self,
        *,
        event: AlertEvent,
        hostname: str,
        instance_id: str | None,
        market_mode: str,
        render_mode: str | RenderMode | None = None,
    ) -> RenderedMessage:
        mode = _normalize_render_mode(render_mode or self._default_render_mode)
        if mode == RenderMode.OPS:
            return self._ops_renderer.render_alert(
                event=event,
                hostname=hostname,
                instance_id=instance_id,
                market_mode=market_mode,
            )
        return self._render_alert_product(
            event=event,
            hostname=hostname,
            instance_id=instance_id,
            market_mode=market_mode,
        )

    def render_recovered(
        self,
        *,
        event: AlertEvent,
        hostname: str,
        instance_id: str | None,
        market_mode: str,
        render_mode: str | RenderMode | None = None,
    ) -> RenderedMessage:
        mode = _normalize_render_mode(render_mode or self._default_render_mode)
        if mode == RenderMode.OPS:
            return self._ops_renderer.render_recovered(
                event=event,
                hostname=hostname,
                instance_id=instance_id,
            )
        return self._render_recovered_product(
            event=event,
            hostname=hostname,
            instance_id=instance_id,
            market_mode=market_mode,
        )

    def render_daily_digest(
        self,
        *,
        snapshot: HealthSnapshot,
        digest: _DailyDigestState,
        hostname: str,
        instance_id: str | None,
        render_mode: str | RenderMode | None = None,
    ) -> RenderedMessage:
        mode = _normalize_render_mode(render_mode or self._default_render_mode)
        if mode == RenderMode.OPS:
            return self._ops_renderer.render_daily_digest(
                snapshot=snapshot,
                digest=digest,
                hostname=hostname,
                instance_id=instance_id,
            )
        return self._render_daily_digest_product(
            snapshot=snapshot,
            digest=digest,
            hostname=hostname,
            instance_id=instance_id,
        )

    def render_db_summary(
        self,
        *,
        snapshot: HealthSnapshot,
        hostname: str,
        instance_id: str | None,
    ) -> RenderedMessage:
        host_text = hostname if not instance_id else f"{hostname} / {instance_id}"
        lines = [
            "<b>🗄️ DB 摘要</b>",
            f"結論：{escape(snapshot.trading_day)} 資料庫狀態",
            (
                "指標："
                f"rows={snapshot.db_rows} | "
                f"queue={snapshot.queue_size}/{snapshot.queue_maxsize} | "
                f"last_update_at={escape(snapshot.db_max_ts_utc)}"
            ),
            f"db：{escape(str(snapshot.db_path))}",
            f"主機：{escape(host_text)}",
            f"sid={escape(snapshot.sid)}",
        ]
        return RenderedMessage(text="\n".join(lines), parse_mode=self._parse_mode)

    def render_runbook(
        self,
        *,
        code: str,
        market_mode: str,
        hostname: str,
        instance_id: str | None,
    ) -> RenderedMessage:
        host_text = hostname if not instance_id else f"{hostname} / {instance_id}"
        normalized = code.strip().upper() or "HEALTH"
        title = f"📘 {normalized} Runbook"
        steps = self._runbook_steps(normalized)
        lines = [
            f"<b>{escape(title)}</b>",
            f"結論：{escape(self._runbook_conclusion(normalized))}",
            f"市況：{escape(_market_mode_label(market_mode))}",
            f"步驟：{escape(steps[0])}；{escape(steps[1])}",
            f"指令：{escape(steps[2])}",
            f"主機：{escape(host_text)}",
        ]
        return RenderedMessage(text="\n".join(lines), parse_mode=self._parse_mode)

    def _render_health_product(
        self,
        *,
        snapshot: HealthSnapshot,
        assessment: HealthAssessment,
        hostname: str,
        instance_id: str | None,
    ) -> RenderedMessage:
        host_text = hostname if not instance_id else f"{hostname} / {instance_id}"
        lag_text = f"{_format_float(abs(snapshot.drift_sec) if snapshot.drift_sec is not None else None)}s"
        throughput_text = f"{max(0, int(snapshot.persisted_rows_per_min))}/min"
        queue_text = f"{snapshot.queue_size}/{snapshot.queue_maxsize}"
        icon = "🟢" if assessment.severity == NotifySeverity.OK else "🟡"
        phase_text = _market_mode_label(assessment.market_mode)
        if assessment.severity == NotifySeverity.OK and assessment.market_mode in {
            "lunch-break",
            "after-hours",
            "holiday-closed",
        }:
            phase_text = f"{phase_text} (market idle, normal)"

        lines = [
            f"<b>{icon} HK Tick 健康摘要</b>",
            f"結論：{escape(assessment.conclusion)}",
            f"KPI：新鮮度延遲={escape(lag_text)} | 寫入吞吐={throughput_text} | 佇列={queue_text}",
            f"市況：{escape(phase_text)}",
            f"主機：{escape(host_text)}",
            f"sid={escape(snapshot.sid)}",
        ]
        return RenderedMessage(text="\n".join(lines), parse_mode=self._parse_mode)

    def _render_alert_product(
        self,
        *,
        event: AlertEvent,
        hostname: str,
        instance_id: str | None,
        market_mode: str,
    ) -> RenderedMessage:
        host_text = hostname if not instance_id else f"{hostname} / {instance_id}"
        severity = _severity_from(event.severity)
        icon = "🔴" if severity == NotifySeverity.ALERT else "🟡"
        title = "警報" if severity == NotifySeverity.ALERT else "注意"
        headline = event.headline or self._ops_renderer._default_alert_headline(event.code, severity)
        kpis = self._extract_event_kpis(event.summary_lines)
        lines = [
            f"<b>{icon} HK Tick {title}</b>",
            f"結論：{escape(headline)}",
            f"KPI：{escape(' | '.join(kpis))}",
            f"市況：{escape(_market_mode_label(market_mode))}",
            f"主機：{escape(host_text)}",
            f"eid={escape(event.eid)} sid={escape(event.sid or 'n/a')}",
        ]
        return RenderedMessage(text="\n".join(lines), parse_mode=self._parse_mode)

    def _render_recovered_product(
        self,
        *,
        event: AlertEvent,
        hostname: str,
        instance_id: str | None,
        market_mode: str,
    ) -> RenderedMessage:
        host_text = hostname if not instance_id else f"{hostname} / {instance_id}"
        kpis = self._extract_event_kpis(event.summary_lines)
        lines = [
            "<b>✅ HK Tick 已恢復</b>",
            f"結論：{escape(event.code.upper())} 已恢復正常",
            f"KPI：{escape(' | '.join(kpis))}",
            f"市況：{escape(_market_mode_label(market_mode))}",
            f"主機：{escape(host_text)}",
            f"eid={escape(event.eid)} sid={escape(event.sid or 'n/a')}",
        ]
        return RenderedMessage(text="\n".join(lines), parse_mode=self._parse_mode)

    def _render_daily_digest_product(
        self,
        *,
        snapshot: HealthSnapshot,
        digest: _DailyDigestState,
        hostname: str,
        instance_id: str | None,
    ) -> RenderedMessage:
        host_text = hostname if not instance_id else f"{hostname} / {instance_id}"
        lines = [
            "<b>📊 HK Tick 日報</b>",
            f"結論：{escape(digest.trading_day)} 收盤摘要",
            (
                "KPI："
                f"總寫入={digest.total_rows} | "
                f"峰值吞吐={digest.peak_rows_per_min}/min | "
                f"告警/恢復={digest.alert_count}/{digest.recovered_count}"
            ),
            "市況：收盤後 (market idle, normal)",
            f"主機：{escape(host_text)}",
            f"sid={escape(snapshot.sid)}",
        ]
        return RenderedMessage(text="\n".join(lines), parse_mode=self._parse_mode)

    def _extract_event_kpis(self, summary_lines: Sequence[str]) -> list[str]:
        queue: str | None = None
        lag: str | None = None
        persist: str | None = None
        for raw in summary_lines:
            text = raw.strip()
            if not queue:
                matched = re.search(r"queue[=:]([0-9]+/[0-9]+)", text)
                if matched:
                    queue = f"佇列={matched.group(1)}"
            if not lag:
                matched = re.search(r"(lag|lag_sec|drift|drift_sec)[=:]([0-9.]+)", text)
                if matched:
                    lag = f"延遲={matched.group(2)}s"
            if not persist:
                matched = re.search(r"(persisted_per_min|persist|min|write)[=:]([0-9.]+)", text)
                if matched:
                    persist = f"寫入={matched.group(2)}/min"
        parts = [item for item in [lag, persist, queue] if item]
        if len(parts) >= 3:
            return parts[:3]
        fallback = [line.strip() for line in summary_lines if line.strip()]
        for item in fallback:
            if len(parts) >= 3:
                break
            if item not in parts:
                parts.append(item[:56])
        while len(parts) < 3:
            parts.append("n/a")
        return parts[:3]

    def _runbook_conclusion(self, code: str) -> str:
        if code == "PERSIST_STALL":
            return "寫入可能停滯，需先確認 queue 與 DB 最新時間戳"
        if code == "SQLITE_BUSY":
            return "SQLite 鎖競爭升高，需確認是否有並行寫入"
        if code == "DISCONNECT":
            return "與 OpenD 連線中斷，先確認 OpenD 服務狀態"
        return "請先確認最新 health 與告警事件是否持續"

    def _runbook_steps(self, code: str) -> list[str]:
        if code == "PERSIST_STALL":
            return [
                "先看最近 20 分鐘告警與 persist 指標",
                "再確認 DB max(ts_ms) 是否持續前進",
                'scripts/hk-tickctl logs --ops --since "20 minutes ago"; scripts/hk-tickctl db stats; sudo systemctl status hk-tick-collector --no-pager',
            ]
        if code == "SQLITE_BUSY":
            return [
                "先檢查 busy backoff 是否持續增加",
                "再確認 queue 與寫入吞吐是否惡化",
                'scripts/hk-tickctl logs --ops --since "20 minutes ago"; scripts/hk-tickctl db stats; lsof /data/sqlite/HK/*.db',
            ]
        if code == "DISCONNECT":
            return [
                "先確認 OpenD 與 collector service 狀態",
                "觀察重連後是否出現已恢復訊息",
                'sudo systemctl status futu-opend --no-pager; scripts/hk-tickctl logs --ops --since "20 minutes ago"; sudo systemctl status hk-tick-collector --no-pager',
            ]
        return [
            "先確認是否為暫時性波動",
            "若持續超過 10 分鐘再執行恢復動作",
            'scripts/hk-tickctl status; scripts/hk-tickctl logs --ops --since "20 minutes ago"; scripts/hk-tickctl db stats',
        ]


class TelegramClient:
    def __init__(
        self,
        *,
        bot_token: str,
        request_timeout_sec: float = 8.0,
        sender: Optional[Callable[[Dict[str, str | int]], TelegramSendResult]] = None,
    ) -> None:
        self._bot_token = bot_token.strip()
        self._request_timeout_sec = max(0.5, float(request_timeout_sec))
        self._sender = sender or self._send_via_http
        self._masked_token = self._mask_secret(self._bot_token)

    @property
    def masked_token(self) -> str:
        return self._masked_token

    def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        parse_mode: str,
        thread_id: int | None,
        reply_markup: dict[str, Any] | None = None,
    ) -> TelegramSendResult:
        payload: Dict[str, str | int] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if thread_id is not None:
            payload["message_thread_id"] = int(thread_id)
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=True)
        return self._sender(payload)

    def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
    ) -> None:
        payload: Dict[str, str | int] = {
            "callback_query_id": callback_query_id,
            "cache_time": 0,
        }
        if text:
            payload["text"] = text
        self._post_json(endpoint="answerCallbackQuery", payload=payload)

    def get_updates(
        self,
        *,
        offset: int,
        timeout_sec: int = 15,
    ) -> list[dict[str, Any]]:
        payload: Dict[str, str | int] = {
            "offset": int(offset),
            "timeout": max(1, int(timeout_sec)),
            "allowed_updates": json.dumps(["callback_query"]),
        }
        data = self._post_json(endpoint="getUpdates", payload=payload)
        result = data.get("result")
        if not isinstance(result, list):
            return []
        updates: list[dict[str, Any]] = []
        for item in result:
            if isinstance(item, dict):
                updates.append(item)
        return updates

    def _send_via_http(self, payload: Dict[str, str | int]) -> TelegramSendResult:
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        encoded = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(url, data=encoded, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            with urllib.request.urlopen(request, timeout=self._request_timeout_sec) as response:
                body = response.read().decode("utf-8", errors="replace")
                status = int(getattr(response, "status", response.getcode()))
                return self._parse_send_response(status, body)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            return self._parse_send_response(int(exc.code), body)
        except Exception as exc:
            return TelegramSendResult(
                ok=False,
                status_code=0,
                error=f"{type(exc).__name__}: {self._sanitize_text(str(exc))}",
            )

    def _post_json(self, *, endpoint: str, payload: Dict[str, str | int]) -> dict[str, Any]:
        url = f"https://api.telegram.org/bot{self._bot_token}/{endpoint}"
        encoded = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(url, data=encoded, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(request, timeout=self._request_timeout_sec) as response:
                body = response.read().decode("utf-8", errors="replace")
                parsed = json.loads(body) if body else {}
                if isinstance(parsed, dict):
                    return parsed
        except Exception:
            logger.debug("telegram_client_post_json_failed endpoint=%s", endpoint, exc_info=True)
        return {}

    def _parse_send_response(self, status_code: int, body: str) -> TelegramSendResult:
        payload: Dict[str, Any] = {}
        if body:
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = {}

        retry_after: int | None = None
        if isinstance(payload.get("parameters"), dict):
            raw = payload["parameters"].get("retry_after")
            if isinstance(raw, int):
                retry_after = raw

        ok_flag = bool(payload.get("ok")) if payload else (200 <= status_code < 300)
        success = ok_flag and (200 <= status_code < 300)
        error = payload.get("description") if isinstance(payload.get("description"), str) else None
        if error is None and not success:
            error = f"http_{status_code}"
        return TelegramSendResult(
            ok=bool(success),
            status_code=status_code,
            retry_after=retry_after,
            error=self._sanitize_text(error) if error else None,
        )

    def _sanitize_text(self, text: str | None) -> str:
        if not text:
            return ""
        if not self._bot_token:
            return text
        return text.replace(self._bot_token, self._masked_token)

    @staticmethod
    def _mask_secret(secret: str) -> str:
        text = secret.strip()
        if not text:
            return "none"
        if len(text) <= 8:
            return "*" * len(text)
        return f"{text[:4]}...{text[-4:]}"


class TelegramNotifier:
    def __init__(
        self,
        *,
        enabled: bool,
        bot_token: str,
        chat_id: str,
        thread_id: int | None = None,
        thread_health_id: int | None = None,
        thread_ops_id: int | None = None,
        parse_mode: str = "HTML",
        default_render_mode: str | RenderMode = _DEFAULT_RENDER_MODE,
        health_interval_sec: int | None = None,
        health_trading_interval_sec: int | None = None,
        health_offhours_interval_sec: int | None = None,
        health_lunch_once: bool = True,
        health_after_close_once: bool = True,
        health_holiday_mode: str = "daily",
        digest_interval_sec: int | None = None,
        alert_cooldown_sec: int = 600,
        alert_escalation_steps: Sequence[int] | None = None,
        rate_limit_per_min: int = 18,
        include_system_metrics: bool = True,
        instance_id: str | None = None,
        drift_warn_sec: int = 120,
        digest_queue_change_pct: float = 20.0,
        digest_last_tick_age_threshold_sec: float = 60.0,
        digest_drift_threshold_sec: float = 60.0,
        max_retries: int = 4,
        request_timeout_sec: float = 8.0,
        queue_maxsize: int = 256,
        sender: Optional[Callable[[Dict[str, str | int]], TelegramSendResult]] = None,
        now_monotonic: Callable[[], float] = time.monotonic,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
        enable_callbacks: bool = True,
    ) -> None:
        self._enabled = bool(enabled)
        self._chat_id = chat_id.strip()
        self._thread_id = thread_id
        self._thread_health_id = thread_health_id
        self._thread_ops_id = thread_ops_id
        self._default_render_mode = _normalize_render_mode(default_render_mode)
        self._include_system_metrics = bool(include_system_metrics)
        self._instance_id = instance_id.strip() if instance_id else None
        self._alert_cooldown_sec = max(30, int(alert_cooldown_sec))
        self._alert_escalation_steps = list(alert_escalation_steps or [0, 600, 1800])
        self._max_retries = max(1, int(max_retries))
        self._now_monotonic = now_monotonic
        self._sleep = sleep or asyncio.sleep
        self._enable_callbacks = bool(enable_callbacks and sender is None)
        self._health_lunch_once = bool(health_lunch_once)
        self._health_after_close_once = bool(health_after_close_once)
        holiday_mode_text = health_holiday_mode.strip().lower()
        self._health_holiday_mode = (
            "disabled" if holiday_mode_text == "disabled" else "daily"
        )

        base_interval = health_interval_sec
        if base_interval is None:
            base_interval = digest_interval_sec if digest_interval_sec is not None else 600
        self._health_interval_sec = max(30, int(base_interval))
        trading_interval = int(
            health_trading_interval_sec
            if health_trading_interval_sec is not None
            else self._health_interval_sec
        )
        self._health_trading_interval_sec = min(1800, max(900, trading_interval))
        self._health_offhours_interval_sec = max(
            30,
            int(
                health_offhours_interval_sec
                if health_offhours_interval_sec is not None
                else self._health_interval_sec
            ),
        )

        self._client = TelegramClient(
            bot_token=bot_token,
            request_timeout_sec=request_timeout_sec,
            sender=sender,
        )
        self._composer = MessageComposer(
            parse_mode=parse_mode,
            default_render_mode=self._default_render_mode,
        )
        self._state_machine = AlertStateMachine(drift_warn_sec=drift_warn_sec)
        self._dedupe = DedupeStore()
        self._hostname = socket.gethostname()
        self._collector_version = _resolve_collector_version()

        self._queue: asyncio.Queue[_OutboundMessage | None] = asyncio.Queue(
            maxsize=max(1, int(queue_maxsize))
        )
        self._rate_limiter = SlidingWindowRateLimiter(
            limit_per_window=max(1, int(rate_limit_per_min)),
            window_sec=60.0,
            now_fn=now_monotonic,
        )

        self._digest_queue_change_pct = max(0.1, float(digest_queue_change_pct))
        self._digest_last_tick_age_threshold_sec = max(
            1.0, float(digest_last_tick_age_threshold_sec)
        )
        self._digest_drift_threshold_sec = max(1.0, float(digest_drift_threshold_sec))

        self._worker_task: asyncio.Task | None = None
        self._callback_task: asyncio.Task | None = None
        self._callback_offset = 0
        self._last_health_snapshot: HealthSnapshot | None = None
        self._last_health_severity: NotifySeverity | None = None
        self._last_health_market_mode: str | None = None
        self._last_health_sent_at: float | None = None
        self._daily_digest_sent: set[str] = set()
        self._digest_state: _DailyDigestState | None = None
        self._phase_once_sent: set[str] = set()
        self._cached_snapshots: Dict[str, tuple[HealthSnapshot, HealthAssessment]] = {}
        self._cached_snapshot_order: Deque[str] = deque()
        self._cached_events: Dict[str, AlertEvent] = {}
        self._cached_event_order: Deque[str] = deque()

        self._active = self._enabled and bool(self._chat_id) and bool(bot_token.strip())
        if self._enabled and not self._active:
            logger.warning(
                "telegram_notifier_disabled_missing_config chat_id=%s token=%s",
                self._chat_id or "none",
                self._client.masked_token,
            )

    @property
    def active(self) -> bool:
        return self._active

    async def start(self) -> None:
        if not self._active:
            return
        if self._worker_task is not None and not self._worker_task.done():
            return
        logger.info(
            "telegram_notifier_started notify_schema=%s version=%s chat_id=%s thread_id=%s "
            "thread_health_id=%s thread_ops_id=%s parse_mode=%s render_mode_default=%s "
            "token=%s rate_limit_per_min=%s cadence_ok_preopen=%s cadence_ok_open=%s "
            "cadence_ok_lunch=%s cadence_ok_after_hours=%s cadence_warn=%s cadence_alert=%s",
            NOTIFY_SCHEMA_VERSION,
            self._collector_version,
            self._chat_id,
            self._thread_id if self._thread_id is not None else "none",
            self._thread_health_id if self._thread_health_id is not None else "none",
            self._thread_ops_id if self._thread_ops_id is not None else "none",
            self._composer.parse_mode or "none",
            self._default_render_mode.value,
            self._client.masked_token,
            self._rate_limiter.limit_per_window,
            PREOPEN_CADENCE_SEC,
            self._health_trading_interval_sec,
            LUNCH_CADENCE_SEC,
            AFTER_HOURS_CADENCE_SEC,
            WARN_CADENCE_SEC,
            ALERT_CADENCE_SEC,
        )
        self._worker_task = asyncio.create_task(
            self._worker_loop(), name="telegram-notifier-worker"
        )
        if self._enable_callbacks:
            self._callback_task = asyncio.create_task(
                self._callback_loop(), name="telegram-notifier-callbacks"
            )

    async def stop(self) -> None:
        if self._worker_task is None and self._callback_task is None:
            return
        try:
            if self._worker_task is not None:
                await self._queue.put(None)
                await asyncio.wait_for(self._worker_task, timeout=15.0)
        except asyncio.TimeoutError:
            logger.error("telegram_notifier_stop_timeout")
            if self._worker_task is not None:
                self._worker_task.cancel()
                await asyncio.gather(self._worker_task, return_exceptions=True)
        finally:
            if self._callback_task is not None:
                self._callback_task.cancel()
                await asyncio.gather(self._callback_task, return_exceptions=True)
                self._callback_task = None
            self._worker_task = None

    def submit_health(self, snapshot: HealthSnapshot) -> None:
        if not self._active:
            return

        now = self._now_monotonic()
        assessment = self._state_machine.assess_health(snapshot)
        self._cache_health_snapshot(snapshot=snapshot, assessment=assessment)
        self._observe_digest(snapshot=snapshot)
        should_send, reason = self._should_emit_health(
            snapshot=snapshot,
            assessment=assessment,
            now=now,
        )
        self._last_health_snapshot = snapshot
        self._last_health_severity = assessment.severity
        self._last_health_market_mode = assessment.market_mode
        if not should_send:
            logger.info(
                "telegram_health_suppressed reason=%s severity=%s mode=%s sid=%s",
                reason,
                assessment.severity.value,
                assessment.market_mode,
                snapshot.sid,
            )
            return

        rendered = self._composer.render_health(
            snapshot=snapshot,
            assessment=assessment,
            hostname=self._hostname,
            instance_id=self._instance_id,
            include_system_metrics=self._include_system_metrics,
            digest=self._digest_state,
            render_mode=self._default_render_mode,
        )
        rendered = self._attach_buttons(
            message=rendered,
            details_callback=self._make_callback_data("d:s", snapshot.sid),
            runbook_callback=self._make_callback_data("r", "HEALTH"),
            db_callback=self._make_callback_data("b", snapshot.sid),
        )
        self._enqueue_message(
            kind="HEALTH",
            message=rendered,
            severity=assessment.severity,
            fingerprint=f"HEALTH:{snapshot.trading_day}:{assessment.market_mode}",
            reason=reason,
            sid=snapshot.sid,
            eid=None,
        )
        self._last_health_sent_at = now
        if assessment.severity == NotifySeverity.OK:
            if (
                assessment.market_mode == "after-hours"
                and snapshot.trading_day not in self._daily_digest_sent
                and self._digest_state is not None
                and _is_after_close_window(snapshot.created_at)
            ):
                digest_message = self._composer.render_daily_digest(
                    snapshot=snapshot,
                    digest=self._digest_state,
                    hostname=self._hostname,
                    instance_id=self._instance_id,
                    render_mode=self._default_render_mode,
                )
                digest_message = self._attach_buttons(
                    message=digest_message,
                    details_callback=self._make_callback_data("d:s", snapshot.sid),
                    runbook_callback=self._make_callback_data("r", "HEALTH"),
                    db_callback=self._make_callback_data("b", snapshot.sid),
                )
                self._enqueue_message(
                    kind="DAILY_DIGEST",
                    message=digest_message,
                    severity=NotifySeverity.OK,
                    fingerprint=f"DAILY_DIGEST:{snapshot.trading_day}",
                    reason="after_close_digest",
                    sid=snapshot.sid,
                    eid=None,
                )
                self._daily_digest_sent.add(snapshot.trading_day)

    def submit_alert(self, event: AlertEvent) -> None:
        if not self._active:
            return

        now = self._now_monotonic()
        severity = _severity_from(event.severity)
        normalized = self._normalize_event_ids(event)
        fingerprint = normalized.fingerprint or normalized.key or normalized.code
        cooldown_sec = self._severity_cooldown_sec(severity)
        escalation_steps = self._severity_escalation_steps(severity, cooldown_sec)
        should_send, reason = self._dedupe.evaluate(
            fingerprint=fingerprint,
            severity=severity,
            now=now,
            cooldown_sec=cooldown_sec,
            escalation_steps=escalation_steps,
            event_id=normalized.eid,
            snapshot_id=normalized.sid,
        )
        if not should_send:
            logger.info(
                "telegram_alert_suppressed code=%s fingerprint=%s reason=%s cooldown_sec=%s eid=%s sid=%s",
                normalized.code,
                fingerprint,
                reason,
                cooldown_sec,
                normalized.eid,
                normalized.sid or "none",
            )
            return

        mode = _infer_market_mode(normalized.created_at)
        rendered = self._composer.render_alert(
            event=normalized,
            hostname=self._hostname,
            instance_id=self._instance_id,
            market_mode=mode,
            render_mode=self._default_render_mode,
        )
        self._cache_event(normalized)
        rendered = self._attach_buttons(
            message=rendered,
            details_callback=self._make_callback_data("d:e", normalized.eid),
            runbook_callback=self._make_callback_data("r", normalized.code.upper()),
            db_callback=self._make_callback_data("b", normalized.sid or "none"),
        )
        if self._digest_state is not None and severity in {
            NotifySeverity.WARN,
            NotifySeverity.ALERT,
        }:
            self._digest_state.alert_count += 1
        self._enqueue_message(
            kind=normalized.code,
            message=rendered,
            severity=severity,
            fingerprint=fingerprint,
            reason=reason,
            sid=normalized.sid,
            eid=normalized.eid,
        )

    def resolve_alert(
        self,
        *,
        code: str,
        fingerprint: str,
        trading_day: str,
        summary_lines: Sequence[str],
        sid: str | None = None,
        eid: str | None = None,
    ) -> None:
        if not self._active:
            return
        record = self._dedupe.resolve(fingerprint)
        resolved_eid = (
            eid or (record.last_event_id if record is not None else None) or _make_short_id("eid")
        )
        resolved_sid = sid or (record.last_snapshot_id if record is not None else None)
        recovered = AlertEvent(
            created_at=datetime.now(tz=HK_TZ),
            code=code,
            key=fingerprint,
            fingerprint=fingerprint,
            trading_day=trading_day,
            severity=NotifySeverity.OK.value,
            summary_lines=list(summary_lines),
            suggestions=[],
            sid=resolved_sid,
            eid=resolved_eid,
        )
        mode = _infer_market_mode(recovered.created_at)
        self._cache_event(recovered)
        rendered = self._composer.render_recovered(
            event=recovered,
            hostname=self._hostname,
            instance_id=self._instance_id,
            market_mode=mode,
            render_mode=self._default_render_mode,
        )
        rendered = self._attach_buttons(
            message=rendered,
            details_callback=self._make_callback_data("d:e", recovered.eid),
            runbook_callback=self._make_callback_data("r", recovered.code.upper()),
            db_callback=self._make_callback_data("b", recovered.sid or "none"),
        )
        self._enqueue_message(
            kind=f"{code}_RECOVERED",
            message=rendered,
            severity=NotifySeverity.OK,
            fingerprint=f"{fingerprint}:RECOVERED:{resolved_eid}",
            reason="state_recovered",
            sid=resolved_sid,
            eid=resolved_eid,
        )
        if self._digest_state is not None:
            self._digest_state.recovered_count += 1

    def _enqueue_message(
        self,
        *,
        kind: str,
        message: RenderedMessage,
        severity: NotifySeverity,
        fingerprint: str,
        reason: str,
        sid: str | None,
        eid: str | None,
        thread_id: int | None = None,
        chat_id: str | None = None,
    ) -> bool:
        clipped = truncate_rendered_message(message)
        resolved_thread_id = (
            int(thread_id)
            if thread_id is not None
            else self._select_thread_id(kind=kind, severity=severity)
        )
        payload = _OutboundMessage(
            kind=kind,
            message=clipped,
            severity=severity,
            fingerprint=fingerprint,
            sid=sid,
            eid=eid,
            thread_id=resolved_thread_id,
            chat_id=chat_id.strip() if chat_id else self._chat_id,
        )
        try:
            self._queue.put_nowait(payload)
            logger.info(
                "telegram_enqueue kind=%s severity=%s fingerprint=%s reason=%s thread_id=%s eid=%s sid=%s",
                kind,
                severity.value,
                fingerprint,
                reason,
                resolved_thread_id if resolved_thread_id is not None else "none",
                eid or "none",
                sid or "none",
            )
            return True
        except asyncio.QueueFull:
            logger.error(
                "telegram_queue_full kind=%s severity=%s fingerprint=%s dropped=1 thread_id=%s eid=%s sid=%s",
                kind,
                severity.value,
                fingerprint,
                resolved_thread_id if resolved_thread_id is not None else "none",
                eid or "none",
                sid or "none",
            )
            return False

    def _should_emit_health(
        self,
        *,
        snapshot: HealthSnapshot,
        assessment: HealthAssessment,
        now: float,
    ) -> tuple[bool, str]:
        phase_key = f"{snapshot.trading_day}:{assessment.market_mode}"
        if assessment.severity == NotifySeverity.OK:
            if assessment.market_mode == "holiday-closed" and self._health_holiday_mode == "disabled":
                return False, "holiday_disabled"
            if assessment.market_mode == "pre-open" and phase_key in self._phase_once_sent:
                return False, "preopen_once"
            if (
                assessment.market_mode == "lunch-break"
                and self._health_lunch_once
                and phase_key in self._phase_once_sent
            ):
                return False, "lunch_once"
            if (
                assessment.market_mode == "after-hours"
                and self._health_after_close_once
                and phase_key in self._phase_once_sent
            ):
                return False, "after_hours_once"
            if (
                assessment.market_mode == "holiday-closed"
                and self._health_holiday_mode == "daily"
                and phase_key in self._phase_once_sent
            ):
                return False, "holiday_daily_once"

        if self._last_health_severity is None:
            self._mark_phase_once_sent(snapshot=snapshot, assessment=assessment)
            return True, "bootstrap"

        if self._last_health_market_mode != assessment.market_mode:
            self._mark_phase_once_sent(snapshot=snapshot, assessment=assessment)
            return True, "market_mode_changed"

        if self._last_health_severity != assessment.severity:
            self._mark_phase_once_sent(snapshot=snapshot, assessment=assessment)
            return True, "state_changed"

        elapsed = None if self._last_health_sent_at is None else (now - self._last_health_sent_at)
        cadence_sec = self._health_cadence_sec(
            market_mode=assessment.market_mode,
            severity=assessment.severity,
        )
        if elapsed is None or elapsed >= cadence_sec:
            self._mark_phase_once_sent(snapshot=snapshot, assessment=assessment)
            if assessment.severity == NotifySeverity.WARN:
                return True, "warn_cadence"
            if assessment.severity == NotifySeverity.ALERT:
                return True, "alert_cadence"
            return True, "ok_cadence"
        if assessment.severity == NotifySeverity.WARN:
            return False, "warn_cooldown"
        if assessment.severity == NotifySeverity.ALERT:
            return False, "alert_cooldown"
        return False, "ok_cooldown"

    def _health_cadence_sec(self, *, market_mode: str, severity: NotifySeverity) -> int:
        if severity == NotifySeverity.ALERT:
            return ALERT_CADENCE_SEC
        if severity == NotifySeverity.WARN:
            return WARN_CADENCE_SEC
        if market_mode == "open":
            return self._health_trading_interval_sec
        if market_mode in {"lunch-break", "after-hours", "holiday-closed"}:
            return self._health_offhours_interval_sec
        return PREOPEN_CADENCE_SEC

    def _mark_phase_once_sent(
        self,
        *,
        snapshot: HealthSnapshot,
        assessment: HealthAssessment,
    ) -> None:
        if assessment.severity != NotifySeverity.OK:
            return
        if assessment.market_mode == "pre-open":
            self._phase_once_sent.add(f"{snapshot.trading_day}:pre-open")
            return
        if assessment.market_mode == "lunch-break" and self._health_lunch_once:
            self._phase_once_sent.add(f"{snapshot.trading_day}:lunch-break")
            return
        if assessment.market_mode == "after-hours" and self._health_after_close_once:
            self._phase_once_sent.add(f"{snapshot.trading_day}:after-hours")
            return
        if assessment.market_mode == "holiday-closed" and self._health_holiday_mode == "daily":
            self._phase_once_sent.add(f"{snapshot.trading_day}:holiday-closed")

    def _normalize_event_ids(self, event: AlertEvent) -> AlertEvent:
        sid = event.sid
        if not sid and self._last_health_snapshot is not None:
            sid = self._last_health_snapshot.sid
        if not sid:
            sid = _make_short_id("sid")
        if event.sid == sid:
            return event
        return replace(event, sid=sid)

    def _severity_cooldown_sec(self, severity: NotifySeverity) -> int:
        if severity == NotifySeverity.ALERT:
            return ALERT_CADENCE_SEC
        if severity == NotifySeverity.WARN:
            return WARN_CADENCE_SEC
        return max(30, int(self._alert_cooldown_sec))

    def _severity_escalation_steps(self, severity: NotifySeverity, cooldown_sec: int) -> list[int]:
        values = [0]
        for step in self._alert_escalation_steps:
            item = max(0, int(step))
            if item == 0 or item >= cooldown_sec:
                values.append(item)
        if severity == NotifySeverity.ALERT:
            values.append(cooldown_sec)
        if severity == NotifySeverity.WARN:
            values.append(cooldown_sec)
        return sorted(set(values))

    def _observe_digest(self, *, snapshot: HealthSnapshot) -> None:
        if self._digest_state is None or self._digest_state.trading_day != snapshot.trading_day:
            self._digest_state = _DailyDigestState(
                trading_day=snapshot.trading_day,
                start_db_rows=int(snapshot.db_rows),
            )
        state = self._digest_state
        state.total_rows += max(0, int(snapshot.persisted_rows_per_min))
        state.peak_rows_per_min = max(
            state.peak_rows_per_min, max(0, int(snapshot.persisted_rows_per_min))
        )
        state.max_lag_sec = max(state.max_lag_sec, abs(snapshot.drift_sec or 0.0))
        state.db_rows = max(state.db_rows, int(snapshot.db_rows))
        state.db_path = str(snapshot.db_path)

    def _has_significant_digest_change(self, old: HealthSnapshot, new: HealthSnapshot) -> bool:
        old_queue_pct = _queue_utilization_pct(old)
        new_queue_pct = _queue_utilization_pct(new)
        if abs(new_queue_pct - old_queue_pct) >= self._digest_queue_change_pct:
            return True

        old_age = _max_symbol_age_sec(old)
        new_age = _max_symbol_age_sec(new)
        if self._crossed_threshold(
            before=old_age,
            after=new_age,
            threshold=self._digest_last_tick_age_threshold_sec,
            use_abs=False,
        ):
            return True

        if (old.persisted_rows_per_min > 0) != (new.persisted_rows_per_min > 0):
            return True

        if self._crossed_threshold(
            before=old.drift_sec,
            after=new.drift_sec,
            threshold=self._digest_drift_threshold_sec,
            use_abs=True,
        ):
            return True
        return False

    @staticmethod
    def _crossed_threshold(
        *,
        before: float | None,
        after: float | None,
        threshold: float,
        use_abs: bool,
    ) -> bool:
        if before is None and after is None:
            return False
        if before is None or after is None:
            value = abs(after) if (after is not None and use_abs) else after
            return bool(value is not None and value >= threshold)

        lhs = abs(before) if use_abs else before
        rhs = abs(after) if use_abs else after
        return (lhs < threshold <= rhs) or (lhs >= threshold > rhs)

    def _select_thread_id(self, *, kind: str, severity: NotifySeverity) -> int | None:
        if kind in {"HEALTH", "DAILY_DIGEST"}:
            if self._thread_health_id is not None:
                return int(self._thread_health_id)
            return self._thread_id
        if kind.endswith("_RECOVERED") or severity in {
            NotifySeverity.WARN,
            NotifySeverity.ALERT,
        }:
            if self._thread_ops_id is not None:
                return int(self._thread_ops_id)
            return self._thread_id
        return self._thread_id

    def _cache_health_snapshot(
        self,
        *,
        snapshot: HealthSnapshot,
        assessment: HealthAssessment,
    ) -> None:
        self._cached_snapshots[snapshot.sid] = (snapshot, assessment)
        self._cached_snapshot_order.append(snapshot.sid)
        while len(self._cached_snapshot_order) > 128:
            oldest = self._cached_snapshot_order.popleft()
            self._cached_snapshots.pop(oldest, None)

    def _cache_event(self, event: AlertEvent) -> None:
        self._cached_events[event.eid] = event
        self._cached_event_order.append(event.eid)
        while len(self._cached_event_order) > 128:
            oldest = self._cached_event_order.popleft()
            self._cached_events.pop(oldest, None)

    def _make_callback_data(self, prefix: str, value: str) -> str:
        normalized = f"{prefix}:{value}".strip()
        if len(normalized.encode("utf-8")) <= _CALLBACK_MAX_BYTES:
            return normalized
        clipped = normalized.encode("utf-8")[:_CALLBACK_MAX_BYTES]
        return clipped.decode("utf-8", errors="ignore")

    def _attach_buttons(
        self,
        *,
        message: RenderedMessage,
        details_callback: str,
        runbook_callback: str,
        db_callback: str,
    ) -> RenderedMessage:
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "Details", "callback_data": details_callback},
                    {"text": "Runbook", "callback_data": runbook_callback},
                    {"text": "DB", "callback_data": db_callback},
                ]
            ]
        }
        return RenderedMessage(
            text=message.text,
            parse_mode=message.parse_mode,
            reply_markup=keyboard,
        )

    async def _callback_loop(self) -> None:
        while True:
            try:
                updates = await asyncio.to_thread(
                    self._client.get_updates,
                    offset=self._callback_offset,
                    timeout_sec=15,
                )
                for update in updates:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        self._callback_offset = max(self._callback_offset, update_id + 1)
                    callback = update.get("callback_query")
                    if isinstance(callback, dict):
                        await self._handle_callback(callback)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("telegram_callback_loop_failed")
                await self._sleep(1.0)

    async def _handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = str(callback.get("id") or "").strip()
        data = str(callback.get("data") or "").strip()
        message = callback.get("message")
        chat_id = self._chat_id
        thread_id: int | None = None
        if isinstance(message, dict):
            chat = message.get("chat")
            if isinstance(chat, dict) and chat.get("id") is not None:
                chat_id = str(chat.get("id"))
            raw_thread = message.get("message_thread_id")
            if isinstance(raw_thread, int):
                thread_id = raw_thread

        if callback_id:
            await asyncio.to_thread(
                self._client.answer_callback_query,
                callback_query_id=callback_id,
            )
        if not data:
            return

        outbound = self._build_callback_outbound(
            data=data,
            chat_id=chat_id,
            thread_id=thread_id,
        )
        if outbound is None:
            return
        self._enqueue_message(
            kind=outbound.kind,
            message=outbound.message,
            severity=outbound.severity,
            fingerprint=outbound.fingerprint,
            reason="button_callback",
            sid=outbound.sid,
            eid=outbound.eid,
            thread_id=outbound.thread_id,
            chat_id=outbound.chat_id,
        )

    def _build_callback_outbound(
        self,
        *,
        data: str,
        chat_id: str,
        thread_id: int | None,
    ) -> _OutboundMessage | None:
        if data.startswith("d:s:"):
            sid = data.split(":", 2)[-1]
            cached = self._cached_snapshots.get(sid)
            if cached is None:
                return None
            snapshot, assessment = cached
            message = self._composer.render_health(
                snapshot=snapshot,
                assessment=assessment,
                hostname=self._hostname,
                instance_id=self._instance_id,
                include_system_metrics=self._include_system_metrics,
                digest=self._digest_state,
                render_mode=RenderMode.OPS,
            )
            return _OutboundMessage(
                kind="OPS_DETAILS",
                message=message,
                severity=assessment.severity,
                fingerprint=f"DETAILS:{sid}",
                sid=sid,
                eid=None,
                thread_id=thread_id,
                chat_id=chat_id,
            )
        if data.startswith("d:e:"):
            eid = data.split(":", 2)[-1]
            event = self._cached_events.get(eid)
            if event is None:
                return None
            mode = _infer_market_mode(event.created_at)
            severity = _severity_from(event.severity)
            if severity == NotifySeverity.OK:
                message = self._composer.render_recovered(
                    event=event,
                    hostname=self._hostname,
                    instance_id=self._instance_id,
                    market_mode=mode,
                    render_mode=RenderMode.OPS,
                )
            else:
                message = self._composer.render_alert(
                    event=event,
                    hostname=self._hostname,
                    instance_id=self._instance_id,
                    market_mode=mode,
                    render_mode=RenderMode.OPS,
                )
            return _OutboundMessage(
                kind="OPS_DETAILS",
                message=message,
                severity=severity,
                fingerprint=f"DETAILS:{eid}",
                sid=event.sid,
                eid=eid,
                thread_id=thread_id,
                chat_id=chat_id,
            )
        if data.startswith("r:"):
            code = data.split(":", 1)[-1] or "HEALTH"
            market_mode = (
                self._last_health_market_mode
                if self._last_health_market_mode is not None
                else "after-hours"
            )
            message = self._composer.render_runbook(
                code=code,
                market_mode=market_mode,
                hostname=self._hostname,
                instance_id=self._instance_id,
            )
            return _OutboundMessage(
                kind="RUNBOOK",
                message=message,
                severity=NotifySeverity.OK,
                fingerprint=f"RUNBOOK:{code}",
                sid=self._last_health_snapshot.sid if self._last_health_snapshot else None,
                eid=None,
                thread_id=thread_id,
                chat_id=chat_id,
            )
        if data.startswith("b:"):
            sid = data.split(":", 1)[-1]
            cached = self._cached_snapshots.get(sid)
            snapshot = cached[0] if cached is not None else self._last_health_snapshot
            if snapshot is None:
                return None
            message = self._composer.render_db_summary(
                snapshot=snapshot,
                hostname=self._hostname,
                instance_id=self._instance_id,
            )
            return _OutboundMessage(
                kind="DB_STATS",
                message=message,
                severity=NotifySeverity.OK,
                fingerprint=f"DB:{snapshot.sid}",
                sid=snapshot.sid,
                eid=None,
                thread_id=thread_id,
                chat_id=chat_id,
            )
        return None

    async def _worker_loop(self) -> None:
        while True:
            payload = await self._queue.get()
            try:
                if payload is None:
                    return
                await self._deliver(payload)
            except Exception:
                logger.exception("telegram_delivery_unhandled_error")
            finally:
                self._queue.task_done()

    async def _deliver(self, payload: _OutboundMessage) -> None:
        for attempt in range(1, self._max_retries + 1):
            await self._wait_for_rate_limit_slot()
            result = await asyncio.to_thread(
                self._client.send_message,
                chat_id=payload.chat_id or self._chat_id,
                text=payload.message.text,
                parse_mode=payload.message.parse_mode,
                thread_id=payload.thread_id,
                reply_markup=payload.message.reply_markup,
            )
            if result.ok:
                logger.info(
                    "telegram_send_ok kind=%s severity=%s fingerprint=%s attempt=%s eid=%s sid=%s",
                    payload.kind,
                    payload.severity.value,
                    payload.fingerprint,
                    attempt,
                    payload.eid or "none",
                    payload.sid or "none",
                )
                return

            if (
                result.status_code == 429
                and result.retry_after is not None
                and attempt < self._max_retries
            ):
                logger.warning(
                    "telegram_rate_limited kind=%s severity=%s fingerprint=%s retry_after=%s attempt=%s eid=%s sid=%s",
                    payload.kind,
                    payload.severity.value,
                    payload.fingerprint,
                    result.retry_after,
                    attempt,
                    payload.eid or "none",
                    payload.sid or "none",
                )
                await self._sleep(float(result.retry_after))
                continue

            if attempt >= self._max_retries:
                logger.error(
                    "telegram_send_failed kind=%s severity=%s fingerprint=%s status=%s err=%s attempts=%s eid=%s sid=%s",
                    payload.kind,
                    payload.severity.value,
                    payload.fingerprint,
                    result.status_code,
                    result.error or "unknown",
                    attempt,
                    payload.eid or "none",
                    payload.sid or "none",
                )
                return

            await self._sleep(min(8.0, float(2 ** (attempt - 1))))

    async def _wait_for_rate_limit_slot(self) -> None:
        while True:
            delay = self._rate_limiter.reserve_delay()
            if delay <= 0:
                return
            await self._sleep(delay)
