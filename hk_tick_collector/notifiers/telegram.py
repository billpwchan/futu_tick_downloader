from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from enum import Enum
from html import escape
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Dict, Optional, Sequence
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

HK_TZ = ZoneInfo("Asia/Hong_Kong")
TELEGRAM_MAX_MESSAGE_CHARS = 4096


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


@dataclass
class _DedupeRecord:
    first_seen_at: float
    last_seen_at: float
    last_sent_at: float
    last_sent_severity: NotifySeverity
    next_escalation_index: int


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
    if dt_time(13, 0) <= t < dt_time(16, 0):
        return "open"
    return "after-hours"


def _is_trading_mode(mode: str) -> bool:
    return mode in {"pre-open", "open"}


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
            )
            return True, "new"

        record.last_seen_at = now

        if _severity_rank(severity) > _severity_rank(record.last_sent_severity):
            record.last_sent_severity = severity
            record.last_sent_at = now
            return True, "severity_upgraded"

        incident_age = max(0.0, now - record.first_seen_at)
        if record.next_escalation_index < len(steps):
            step = steps[record.next_escalation_index]
            if incident_age >= step and (now - record.last_sent_at) >= 1.0:
                record.next_escalation_index += 1
                record.last_sent_at = now
                return True, f"escalation_step_{step}s"

        if (now - record.last_sent_at) >= cooldown:
            record.last_sent_at = now
            return True, "cooldown_elapsed"

        return False, "cooldown_active"

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

    def assess_health(self, snapshot: HealthSnapshot) -> HealthAssessment:
        mode = _infer_market_mode(snapshot.created_at)
        freshness_sec = abs(snapshot.drift_sec) if snapshot.drift_sec is not None else None
        queue_pct = _queue_utilization_pct(snapshot)
        persisted = max(0, int(snapshot.persisted_rows_per_min))
        max_lag = _max_symbol_lag(snapshot)

        low_persist = False
        if self._last_persisted_rows_per_min is not None and self._last_persisted_rows_per_min > 0:
            low_persist = 0 < persisted < max(1, int(self._last_persisted_rows_per_min * 0.3))

        if persisted == 0 and (snapshot.queue_size > 0 or max_lag > 0):
            severity = NotifySeverity.ALERT
            conclusion = "異常：疑似停寫，建議立即處理"
            impact = "資料可能無法持續落庫，延遲與積壓可能持續擴大"
            needs_action = True
        elif (
            (freshness_sec is not None and freshness_sec >= self._drift_warn_sec)
            or queue_pct >= 60.0
            or low_persist
        ):
            severity = NotifySeverity.WARN
            conclusion = "注意：服務仍在運作，但品質指標有退化"
            impact = "目前未完全停寫，但可能出現延遲或吞吐下降"
            needs_action = False
        else:
            severity = NotifySeverity.OK
            conclusion = "正常：資料採集與寫入穩定"
            impact = "目前沒有明顯風險，暫時不需要人工介入"
            needs_action = False

        self._last_persisted_rows_per_min = persisted
        return HealthAssessment(
            severity=severity,
            conclusion=conclusion,
            impact=impact,
            needs_action=needs_action,
            market_mode=mode,
        )

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
    ) -> RenderedMessage:
        if self._parse_mode != "HTML":
            return self._render_health_plain(snapshot, assessment, hostname, instance_id)

        icon = {
            NotifySeverity.OK: "✅",
            NotifySeverity.WARN: "⚠️",
            NotifySeverity.ALERT: "🚨",
        }[assessment.severity]
        host_text = hostname if not instance_id else f"{hostname} ({instance_id})"
        freshness = abs(snapshot.drift_sec) if snapshot.drift_sec is not None else None

        symbols = list(snapshot.symbols)
        symbol_lines = []
        show_count = min(3, len(symbols))
        for item in symbols[:show_count]:
            symbol_lines.append(
                f"- {item.symbol}: age={_format_float(item.last_tick_age_sec)}s, lag={item.max_seq_lag}"
            )
        if len(symbols) > show_count:
            symbol_lines.append(f"- ... +{len(symbols) - show_count} symbols")

        primary_lines = [
            f"<b>{icon} HK Tick Collector · HEALTH · {assessment.severity.value}</b>",
            f"結論：{escape(assessment.conclusion)}",
            f"影響：{escape(assessment.impact)}",
            (
                "關鍵："
                f"freshness={_format_float(freshness)}s, "
                f"persisted/min={snapshot.persisted_rows_per_min}, "
                f"queue={snapshot.queue_size}/{snapshot.queue_maxsize}"
            ),
            (
                f"主機：{escape(host_text)} · day={escape(snapshot.trading_day)} "
                f"· mode={escape(assessment.market_mode)}"
            ),
            "symbols:",
        ]
        primary_lines.extend(escape(line) for line in symbol_lines)

        detail_lines = [
            "tech:",
            f"db_path={snapshot.db_path}",
            f"db_rows={snapshot.db_rows} max_ts_utc={snapshot.db_max_ts_utc}",
            (
                f"push_per_min={snapshot.push_rows_per_min} poll_fetched={snapshot.poll_fetched} "
                f"poll_accepted={snapshot.poll_accepted} dup_drop={snapshot.dropped_duplicate}"
            ),
            "seq:",
        ]
        for item in symbols[:5]:
            detail_lines.append(
                (
                    f"{item.symbol}: last_persisted_seq={_format_int(item.last_persisted_seq)} "
                    f"max_seq_lag={item.max_seq_lag}"
                )
            )
        if include_system_metrics:
            detail_lines.extend(
                [
                    "sys:",
                    (
                        f"load1={_format_float(snapshot.system_load1, 2)} "
                        f"rss_mb={_format_float(snapshot.system_rss_mb, 1)} "
                        f"disk_free_gb={_format_float(snapshot.system_disk_free_gb, 2)}"
                    ),
                ]
            )
        detail_lines.extend(
            [
                "suggest:",
                "journalctl -u hk-tick-collector -n 120 --no-pager",
                f"sqlite3 {snapshot.db_path} 'select count(*), max(ts_ms) from ticks;'",
            ]
        )

        text = "\n".join(primary_lines)
        text += "\n"
        text += "<blockquote expandable>" + escape("\n".join(detail_lines)) + "</blockquote>"
        return RenderedMessage(text=text, parse_mode=self._parse_mode)

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

        icon = {
            NotifySeverity.OK: "✅",
            NotifySeverity.WARN: "⚠️",
            NotifySeverity.ALERT: "🚨",
        }[severity]
        host_text = hostname if not instance_id else f"{hostname} ({instance_id})"
        headline = event.headline or self._default_alert_headline(event.code, severity)
        impact = event.impact or self._default_alert_impact(event.code, severity)

        action_line = "需要處理：是" if severity == NotifySeverity.ALERT else "需要處理：建議關注"
        first_summary = event.summary_lines[0] if event.summary_lines else "n/a"

        primary_lines = [
            f"<b>{icon} HK Tick Collector · {escape(event.code.upper())} · {severity.value}</b>",
            f"結論：{escape(headline)}",
            f"影響：{escape(impact)}",
            f"{escape(action_line)}",
            f"關鍵：{escape(first_summary)}",
            (
                f"主機：{escape(host_text)} · day={escape(event.trading_day)} "
                f"· mode={escape(market_mode)}"
            ),
        ]

        detail_lines = ["tech:"]
        detail_lines.extend(event.summary_lines)
        detail_lines.append(f"fingerprint={event.fingerprint or event.key}")
        if event.suggestions:
            detail_lines.append("suggest:")
            detail_lines.extend(event.suggestions[:3])

        text = "\n".join(primary_lines)
        text += "\n"
        text += "<blockquote expandable>" + escape("\n".join(detail_lines)) + "</blockquote>"
        return RenderedMessage(text=text, parse_mode=self._parse_mode)

    def _render_health_plain(
        self,
        snapshot: HealthSnapshot,
        assessment: HealthAssessment,
        hostname: str,
        instance_id: str | None,
    ) -> RenderedMessage:
        host_text = hostname if not instance_id else f"{hostname} ({instance_id})"
        lines = [
            f"HK Tick Collector HEALTH {assessment.severity.value}",
            f"結論: {assessment.conclusion}",
            f"影響: {assessment.impact}",
            (
                f"freshness={_format_float(snapshot.drift_sec)}s "
                f"persisted/min={snapshot.persisted_rows_per_min} "
                f"queue={snapshot.queue_size}/{snapshot.queue_maxsize}"
            ),
            f"host={host_text} day={snapshot.trading_day} mode={assessment.market_mode}",
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
            f"host={host_text}",
        ]
        lines.extend(event.summary_lines[:3])
        return RenderedMessage(text="\n".join(lines), parse_mode="")

    def _default_alert_headline(self, code: str, severity: NotifySeverity) -> str:
        if code.upper() == "PERSIST_STALL":
            return "異常：持久化疑似停滯"
        if code.upper() == "DISCONNECT":
            return "注意：與 OpenD 連線中斷，正在重連"
        if severity == NotifySeverity.ALERT:
            return "異常：偵測到需要立即處理的事件"
        return "注意：偵測到風險事件"

    def _default_alert_impact(self, code: str, severity: NotifySeverity) -> str:
        if code.upper() == "PERSIST_STALL":
            return "新資料可能無法寫入 SQLite，時序會持續落後"
        if code.upper() == "DISCONNECT":
            return "可能短暫影響即時資料完整性，重連成功後可恢復"
        if severity == NotifySeverity.ALERT:
            return "資料可靠性可能受影響，建議立即排查"
        return "目前為退化狀態，建議持續觀察"


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
        return self._sender(payload)

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
        parse_mode: str = "HTML",
        health_interval_sec: int | None = None,
        health_trading_interval_sec: int | None = None,
        health_offhours_interval_sec: int | None = None,
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
    ) -> None:
        self._enabled = bool(enabled)
        self._chat_id = chat_id.strip()
        self._thread_id = thread_id
        self._include_system_metrics = bool(include_system_metrics)
        self._instance_id = instance_id.strip() if instance_id else None
        self._alert_cooldown_sec = max(30, int(alert_cooldown_sec))
        self._alert_escalation_steps = list(alert_escalation_steps or [0, 600, 1800])
        self._max_retries = max(1, int(max_retries))
        self._now_monotonic = now_monotonic
        self._sleep = sleep or asyncio.sleep

        base_interval = health_interval_sec
        if base_interval is None:
            base_interval = digest_interval_sec if digest_interval_sec is not None else 600
        self._health_interval_sec = max(30, int(base_interval))
        self._health_trading_interval_sec = max(
            30,
            int(
                health_trading_interval_sec
                if health_trading_interval_sec is not None
                else self._health_interval_sec
            ),
        )
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
        self._renderer = MessageRenderer(parse_mode=parse_mode)
        self._state_machine = AlertStateMachine(drift_warn_sec=drift_warn_sec)
        self._dedupe = DedupeStore()
        self._hostname = socket.gethostname()

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
        self._last_health_snapshot: HealthSnapshot | None = None

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
            "telegram_notifier_started chat_id=%s thread_id=%s parse_mode=%s token=%s rate_limit_per_min=%s",
            self._chat_id,
            self._thread_id if self._thread_id is not None else "none",
            self._renderer.parse_mode or "none",
            self._client.masked_token,
            self._rate_limiter.limit_per_window,
        )
        self._worker_task = asyncio.create_task(
            self._worker_loop(), name="telegram-notifier-worker"
        )

    async def stop(self) -> None:
        if self._worker_task is None:
            return
        try:
            await self._queue.put(None)
            await asyncio.wait_for(self._worker_task, timeout=15.0)
        except asyncio.TimeoutError:
            logger.error("telegram_notifier_stop_timeout")
            self._worker_task.cancel()
            await asyncio.gather(self._worker_task, return_exceptions=True)
        finally:
            self._worker_task = None

    def submit_health(self, snapshot: HealthSnapshot) -> None:
        if not self._active:
            return

        now = self._now_monotonic()
        assessment = self._state_machine.assess_health(snapshot)
        interval_sec = self._select_health_interval_sec(assessment.market_mode)
        has_change = self._last_health_snapshot is None or self._has_significant_digest_change(
            self._last_health_snapshot,
            snapshot,
        )

        should_send, reason = self._state_machine.should_emit_health(
            assessment=assessment,
            now=now,
            interval_sec=interval_sec,
            meaningful_change=has_change,
        )
        self._last_health_snapshot = snapshot
        if not should_send:
            logger.info(
                "telegram_health_suppressed reason=%s severity=%s mode=%s",
                reason,
                assessment.severity.value,
                assessment.market_mode,
            )
            return

        rendered = self._renderer.render_health(
            snapshot=snapshot,
            assessment=assessment,
            hostname=self._hostname,
            instance_id=self._instance_id,
            include_system_metrics=self._include_system_metrics,
        )
        self._enqueue_message(
            kind="HEALTH",
            message=rendered,
            severity=assessment.severity,
            fingerprint=f"HEALTH:{assessment.market_mode}",
            reason=reason,
        )

    def submit_alert(self, event: AlertEvent) -> None:
        if not self._active:
            return

        now = self._now_monotonic()
        severity = _severity_from(event.severity)
        fingerprint = event.fingerprint or event.key or event.code
        should_send, reason = self._dedupe.evaluate(
            fingerprint=fingerprint,
            severity=severity,
            now=now,
            cooldown_sec=self._alert_cooldown_sec,
            escalation_steps=self._alert_escalation_steps,
        )
        if not should_send:
            logger.info(
                "telegram_alert_suppressed code=%s fingerprint=%s reason=%s cooldown_sec=%s",
                event.code,
                fingerprint,
                reason,
                self._alert_cooldown_sec,
            )
            return

        mode = _infer_market_mode(event.created_at)
        rendered = self._renderer.render_alert(
            event=event,
            hostname=self._hostname,
            instance_id=self._instance_id,
            market_mode=mode,
        )
        self._enqueue_message(
            kind=event.code,
            message=rendered,
            severity=severity,
            fingerprint=fingerprint,
            reason=reason,
        )

    def _select_health_interval_sec(self, market_mode: str) -> int:
        if _is_trading_mode(market_mode):
            return self._health_trading_interval_sec
        return self._health_offhours_interval_sec

    def _enqueue_message(
        self,
        *,
        kind: str,
        message: RenderedMessage,
        severity: NotifySeverity,
        fingerprint: str,
        reason: str,
    ) -> bool:
        clipped = truncate_rendered_message(message)
        payload = _OutboundMessage(
            kind=kind,
            message=clipped,
            severity=severity,
            fingerprint=fingerprint,
        )
        try:
            self._queue.put_nowait(payload)
            logger.info(
                "telegram_enqueue kind=%s severity=%s fingerprint=%s reason=%s",
                kind,
                severity.value,
                fingerprint,
                reason,
            )
            return True
        except asyncio.QueueFull:
            logger.error(
                "telegram_queue_full kind=%s severity=%s fingerprint=%s dropped=1",
                kind,
                severity.value,
                fingerprint,
            )
            return False

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
                chat_id=self._chat_id,
                text=payload.message.text,
                parse_mode=payload.message.parse_mode,
                thread_id=self._thread_id,
            )
            if result.ok:
                logger.info(
                    "telegram_send_ok kind=%s severity=%s fingerprint=%s attempt=%s",
                    payload.kind,
                    payload.severity.value,
                    payload.fingerprint,
                    attempt,
                )
                return

            if (
                result.status_code == 429
                and result.retry_after is not None
                and attempt < self._max_retries
            ):
                logger.warning(
                    "telegram_rate_limited kind=%s severity=%s fingerprint=%s retry_after=%s attempt=%s",
                    payload.kind,
                    payload.severity.value,
                    payload.fingerprint,
                    result.retry_after,
                    attempt,
                )
                await self._sleep(float(result.retry_after))
                continue

            if attempt >= self._max_retries:
                logger.error(
                    "telegram_send_failed kind=%s severity=%s fingerprint=%s status=%s err=%s attempts=%s",
                    payload.kind,
                    payload.severity.value,
                    payload.fingerprint,
                    result.status_code,
                    result.error or "unknown",
                    attempt,
                )
                return

            await self._sleep(min(8.0, float(2 ** (attempt - 1))))

    async def _wait_for_rate_limit_slot(self) -> None:
        while True:
            delay = self._rate_limiter.reserve_delay()
            if delay <= 0:
                return
            await self._sleep(delay)
