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
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Dict, Optional, Sequence

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE_CHARS = 4096
DIGEST_MAX_LINES = 15


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


@dataclass(frozen=True)
class TelegramSendResult:
    ok: bool
    status_code: int
    retry_after: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class _OutboundMessage:
    kind: str
    text: str


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


def _format_uptime(seconds: int) -> str:
    sec = max(0, int(seconds))
    hours = sec // 3600
    minutes = (sec % 3600) // 60
    remainder = sec % 60
    return f"{hours:02d}:{minutes:02d}:{remainder:02d}"


def _format_float(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "none"
    return f"{value:.{digits}f}"


def _format_int(value: int | None) -> str:
    if value is None:
        return "none"
    return str(int(value))


def _max_symbol_age_sec(snapshot: HealthSnapshot) -> float | None:
    ages = [
        item.last_tick_age_sec for item in snapshot.symbols if item.last_tick_age_sec is not None
    ]
    if not ages:
        return None
    return max(ages)


def _queue_utilization_pct(snapshot: HealthSnapshot) -> float:
    if snapshot.queue_maxsize <= 0:
        return 0.0
    return (snapshot.queue_size / snapshot.queue_maxsize) * 100.0


def truncate_message(text: str, max_chars: int = TELEGRAM_MAX_MESSAGE_CHARS) -> str:
    limit = max(1, int(max_chars))
    if len(text) <= limit:
        return text
    suffix = "\n...(truncated)"
    keep = limit - len(suffix)
    if keep <= 0:
        return suffix[:limit]
    return f"{text[:keep]}{suffix}"


def format_health_digest(
    snapshot: HealthSnapshot,
    *,
    hostname: str,
    instance_id: str | None,
    include_system_metrics: bool,
) -> str:
    lines = [
        "📈 HK Tick Collector · HEALTH",
        (
            f"host={hostname}{f' instance={instance_id}' if instance_id else ''} "
            f"pid={snapshot.pid} uptime={_format_uptime(snapshot.uptime_sec)} "
            f"day={snapshot.trading_day} tz=UTC+8"
        ),
        (
            f"db={snapshot.db_path} rows={snapshot.db_rows} max_ts={snapshot.db_max_ts_utc} "
            f"drift_sec={_format_float(snapshot.drift_sec)}"
        ),
        (
            f"queue={snapshot.queue_size}/{snapshot.queue_maxsize} "
            f"push_per_min={snapshot.push_rows_per_min} poll_fetched={snapshot.poll_fetched} "
            f"accepted={snapshot.poll_accepted} persisted_per_min={snapshot.persisted_rows_per_min} "
            f"dup_drop={snapshot.dropped_duplicate}"
        ),
        "symbols:",
    ]

    reserve_for_sys = 1 if include_system_metrics else 0
    available = max(1, DIGEST_MAX_LINES - len(lines) - reserve_for_sys)
    symbols = list(snapshot.symbols)
    if len(symbols) > available:
        shown_count = max(0, available - 1)
        symbols_to_show = symbols[:shown_count]
        for item in symbols_to_show:
            lines.append(
                f"- {item.symbol} age={_format_float(item.last_tick_age_sec)} "
                f"last_persisted_seq={_format_int(item.last_persisted_seq)} "
                f"max_seq_lag={item.max_seq_lag}"
            )
        lines.append(f"- ... +{len(symbols) - shown_count} more")
    else:
        for item in symbols:
            lines.append(
                f"- {item.symbol} age={_format_float(item.last_tick_age_sec)} "
                f"last_persisted_seq={_format_int(item.last_persisted_seq)} "
                f"max_seq_lag={item.max_seq_lag}"
            )

    if include_system_metrics:
        lines.append(
            f"sys: load1={_format_float(snapshot.system_load1, 2)} "
            f"rss_mb={_format_float(snapshot.system_rss_mb, 1)} "
            f"disk_free_gb={_format_float(snapshot.system_disk_free_gb, 2)}"
        )

    return "\n".join(lines)


def format_alive_digest(
    snapshot: HealthSnapshot,
    *,
    hostname: str,
    instance_id: str | None,
) -> str:
    return "\n".join(
        [
            "📈 HK Tick Collector · HEALTH",
            (
                f"host={hostname}{f' instance={instance_id}' if instance_id else ''} "
                f"day={snapshot.trading_day} uptime={_format_uptime(snapshot.uptime_sec)} status=alive"
            ),
            (
                f"queue={snapshot.queue_size}/{snapshot.queue_maxsize} "
                f"persisted_per_min={snapshot.persisted_rows_per_min} "
                f"drift_sec={_format_float(snapshot.drift_sec)}"
            ),
        ]
    )


def format_alert_message(
    event: AlertEvent,
    *,
    hostname: str,
    instance_id: str | None,
) -> str:
    title = event.code.replace("_", " ").upper()
    lines = [
        f"🚨 HK Tick Collector · {title}",
        f"host={hostname}{f' instance={instance_id}' if instance_id else ''} day={event.trading_day}",
    ]
    lines.extend(event.summary_lines)
    lines.extend(f"suggest: {item}" for item in event.suggestions)
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(
        self,
        *,
        enabled: bool,
        bot_token: str,
        chat_id: str,
        thread_id: int | None = None,
        digest_interval_sec: int = 600,
        alert_cooldown_sec: int = 600,
        rate_limit_per_min: int = 18,
        include_system_metrics: bool = True,
        instance_id: str | None = None,
        digest_queue_change_pct: float = 20.0,
        digest_last_tick_age_threshold_sec: float = 60.0,
        digest_drift_threshold_sec: float = 60.0,
        digest_send_alive_when_idle: bool = False,
        max_retries: int = 4,
        request_timeout_sec: float = 8.0,
        queue_maxsize: int = 256,
        sender: Optional[Callable[[Dict[str, str | int]], TelegramSendResult]] = None,
        now_monotonic: Callable[[], float] = time.monotonic,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._bot_token = bot_token.strip()
        self._chat_id = chat_id.strip()
        self._thread_id = thread_id
        self._digest_interval_sec = max(30, int(digest_interval_sec))
        self._alert_cooldown_sec = max(30, int(alert_cooldown_sec))
        self._include_system_metrics = bool(include_system_metrics)
        self._instance_id = instance_id.strip() if instance_id else None
        self._digest_queue_change_pct = max(0.1, float(digest_queue_change_pct))
        self._digest_last_tick_age_threshold_sec = max(
            1.0, float(digest_last_tick_age_threshold_sec)
        )
        self._digest_drift_threshold_sec = max(1.0, float(digest_drift_threshold_sec))
        self._digest_send_alive_when_idle = bool(digest_send_alive_when_idle)
        self._max_retries = max(1, int(max_retries))
        self._request_timeout_sec = max(0.5, float(request_timeout_sec))
        self._hostname = socket.gethostname()
        self._now_monotonic = now_monotonic
        self._sleep = sleep or asyncio.sleep
        self._sender = sender or self._send_via_http
        self._queue: asyncio.Queue[_OutboundMessage | None] = asyncio.Queue(
            maxsize=max(1, int(queue_maxsize))
        )
        self._rate_limiter = SlidingWindowRateLimiter(
            limit_per_window=max(1, int(rate_limit_per_min)),
            window_sec=60.0,
            now_fn=now_monotonic,
        )

        self._worker_task: asyncio.Task | None = None
        self._last_digest_check_at: float | None = None
        self._last_digest_snapshot: HealthSnapshot | None = None
        self._last_alert_sent_at: Dict[str, float] = {}
        self._masked_token = self._mask_secret(self._bot_token)

        self._active = self._enabled and bool(self._bot_token) and bool(self._chat_id)
        if self._enabled and not self._active:
            logger.warning(
                "telegram_notifier_disabled_missing_config chat_id=%s token=%s",
                self._chat_id or "none",
                self._masked_token,
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
            "telegram_notifier_started chat_id=%s thread_id=%s token=%s rate_limit_per_min=%s",
            self._chat_id,
            self._thread_id if self._thread_id is not None else "none",
            self._masked_token,
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
        if (
            self._last_digest_check_at is not None
            and (now - self._last_digest_check_at) < self._digest_interval_sec
        ):
            return
        self._last_digest_check_at = now

        has_change = self._last_digest_snapshot is None or self._has_significant_digest_change(
            self._last_digest_snapshot, snapshot
        )
        self._last_digest_snapshot = snapshot
        if has_change:
            text = format_health_digest(
                snapshot,
                hostname=self._hostname,
                instance_id=self._instance_id,
                include_system_metrics=self._include_system_metrics,
            )
        elif self._digest_send_alive_when_idle:
            text = format_alive_digest(
                snapshot,
                hostname=self._hostname,
                instance_id=self._instance_id,
            )
        else:
            return

        self._enqueue_message("digest", text)

    def submit_alert(self, event: AlertEvent) -> None:
        if not self._active:
            return
        now = self._now_monotonic()
        last_sent = self._last_alert_sent_at.get(event.key)
        if last_sent is not None and (now - last_sent) < self._alert_cooldown_sec:
            logger.info(
                "telegram_alert_suppressed code=%s key=%s cooldown_sec=%s",
                event.code,
                event.key,
                self._alert_cooldown_sec,
            )
            return

        text = format_alert_message(
            event,
            hostname=self._hostname,
            instance_id=self._instance_id,
        )
        if self._enqueue_message("alert", text):
            self._last_alert_sent_at[event.key] = now

    def _enqueue_message(self, kind: str, text: str) -> bool:
        message = _OutboundMessage(kind=kind, text=truncate_message(text))
        try:
            self._queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            logger.error("telegram_queue_full kind=%s dropped=1", kind)
            return False

    def _has_significant_digest_change(self, old: HealthSnapshot, new: HealthSnapshot) -> bool:
        old_queue_pct = _queue_utilization_pct(old)
        new_queue_pct = _queue_utilization_pct(new)
        if abs(new_queue_pct - old_queue_pct) >= self._digest_queue_change_pct:
            return True

        old_age = _max_symbol_age_sec(old)
        new_age = _max_symbol_age_sec(new)
        if self._crossed_threshold(
            old_age,
            new_age,
            self._digest_last_tick_age_threshold_sec,
            use_abs=False,
        ):
            return True

        if (old.persisted_rows_per_min > 0) != (new.persisted_rows_per_min > 0):
            return True

        if self._crossed_threshold(
            old.drift_sec,
            new.drift_sec,
            self._digest_drift_threshold_sec,
            use_abs=True,
        ):
            return True
        return False

    def _crossed_threshold(
        self,
        before: float | None,
        after: float | None,
        threshold: float,
        *,
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
        body = self._build_payload(payload.text)
        for attempt in range(1, self._max_retries + 1):
            await self._wait_for_rate_limit_slot()
            result = await asyncio.to_thread(self._sender, body)
            if result.ok:
                logger.info("telegram_send_ok kind=%s attempt=%s", payload.kind, attempt)
                return

            if (
                result.status_code == 429
                and result.retry_after is not None
                and attempt < self._max_retries
            ):
                logger.warning(
                    "telegram_rate_limited kind=%s retry_after=%s attempt=%s",
                    payload.kind,
                    result.retry_after,
                    attempt,
                )
                await self._sleep(float(result.retry_after))
                continue

            if attempt >= self._max_retries:
                logger.error(
                    "telegram_send_failed kind=%s status=%s err=%s attempts=%s",
                    payload.kind,
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

    def _build_payload(self, text: str) -> Dict[str, str | int]:
        payload: Dict[str, str | int] = {
            "chat_id": self._chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
        if self._thread_id is not None:
            payload["message_thread_id"] = int(self._thread_id)
        return payload

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
            message = self._sanitize_text(str(exc))
            return TelegramSendResult(
                ok=False,
                status_code=0,
                error=f"{type(exc).__name__}: {message}",
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

        retry_after_raw = None
        if isinstance(payload.get("parameters"), dict):
            retry_after_raw = payload["parameters"].get("retry_after")
        retry_after = int(retry_after_raw) if isinstance(retry_after_raw, int) else None

        ok_flag = bool(payload.get("ok")) if payload else (200 <= status_code < 300)
        success = bool(ok_flag and (200 <= status_code < 300))
        error = payload.get("description") if isinstance(payload.get("description"), str) else None
        if error is None and not success:
            error = f"http_{status_code}"
        return TelegramSendResult(
            ok=success,
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
