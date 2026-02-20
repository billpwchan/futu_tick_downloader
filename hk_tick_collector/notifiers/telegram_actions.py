from __future__ import annotations

import asyncio
import re
import shlex
import subprocess
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Any, Callable, Sequence

from .telegram_render import (
    RenderOutput,
    render_db_status_from_snapshot,
    render_logs_summary,
    render_sop,
    render_top_anomalies,
    truncate_text,
)

_CALLBACK_MAX_BYTES = 64
_DAY_RE = re.compile(r"^\d{8}$")
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9._-]{2,24}$")
_DEFAULT_COMMAND_ALLOWLIST = {"help", "db_stats", "top_symbols", "symbol"}
_TOP_METRICS = {"rows", "turnover", "volume"}


@dataclass
class ActionContext:
    context_id: str
    kind: str
    created_at: float
    expires_at: float
    compact_text: str
    detail_text: str
    parse_mode: str = "HTML"
    reply_markup: dict[str, Any] | None = None
    sid: str | None = None
    eid: str | None = None
    trading_day: str | None = None
    snapshot: Any | None = None
    assessment: Any | None = None
    event: Any | None = None
    digest: Any | None = None
    detail_expanded: bool = False
    chat_id: str | None = None
    message_id: int | None = None


@dataclass(frozen=True)
class CallbackRoute:
    action: str
    value: str


@dataclass(frozen=True)
class RouterMessage:
    mode: str
    text: str
    parse_mode: str = "HTML"
    reply_markup: dict[str, Any] | None = None
    kind: str = "CALLBACK"
    severity: str = "OK"
    sid: str | None = None
    eid: str | None = None
    message_id: int | None = None


@dataclass(frozen=True)
class CallbackDispatchResult:
    ack_text: str | None
    messages: list[RouterMessage]


class ActionContextStore:
    def __init__(self, ttl_sec: int = 43200) -> None:
        self._ttl_sec = max(3600, int(ttl_sec))
        self._contexts: dict[str, ActionContext] = {}
        self._message_index: dict[tuple[str, int], str] = {}

    def put(
        self,
        *,
        context_id: str,
        kind: str,
        compact_text: str,
        detail_text: str,
        parse_mode: str = "HTML",
        reply_markup: dict[str, Any] | None = None,
        sid: str | None = None,
        eid: str | None = None,
        trading_day: str | None = None,
        snapshot: Any | None = None,
        assessment: Any | None = None,
        event: Any | None = None,
        digest: Any | None = None,
    ) -> None:
        self._cleanup()
        now = time.time()
        self._contexts[context_id] = ActionContext(
            context_id=context_id,
            kind=kind,
            created_at=now,
            expires_at=now + self._ttl_sec,
            compact_text=compact_text,
            detail_text=detail_text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            sid=sid,
            eid=eid,
            trading_day=trading_day,
            snapshot=snapshot,
            assessment=assessment,
            event=event,
            digest=digest,
        )

    def bind_message(self, *, context_id: str, chat_id: str, message_id: int) -> None:
        self._cleanup()
        ctx = self._contexts.get(context_id)
        if ctx is None:
            return
        ctx.chat_id = chat_id
        ctx.message_id = int(message_id)
        self._message_index[(chat_id, int(message_id))] = context_id

    def get(self, context_id: str) -> ActionContext | None:
        self._cleanup()
        return self._contexts.get(context_id)

    def get_by_message(self, *, chat_id: str, message_id: int) -> ActionContext | None:
        self._cleanup()
        context_id = self._message_index.get((chat_id, int(message_id)))
        if not context_id:
            return None
        return self._contexts.get(context_id)

    def set_detail_expanded(self, *, context_id: str, expanded: bool) -> None:
        self._cleanup()
        ctx = self._contexts.get(context_id)
        if ctx is None:
            return
        ctx.detail_expanded = bool(expanded)

    def count(self) -> int:
        self._cleanup()
        return len(self._contexts)

    def _cleanup(self) -> None:
        now = time.time()
        stale = [key for key, value in self._contexts.items() if value.expires_at <= now]
        for key in stale:
            ctx = self._contexts.pop(key, None)
            if ctx and ctx.chat_id is not None and ctx.message_id is not None:
                self._message_index.pop((ctx.chat_id, ctx.message_id), None)


class SafeOpsCommandRunner:
    def __init__(
        self,
        *,
        service_name: str = "hk-tick-collector",
        log_window_minutes: int = 20,
        timeout_sec: float = 3.0,
        command_timeout_sec: float | None = None,
    ) -> None:
        self._service_name = service_name
        self._log_window_minutes = max(1, int(log_window_minutes))
        self._timeout_sec = max(1.0, float(timeout_sec))
        if command_timeout_sec is None:
            self._command_timeout_sec = self._timeout_sec
        else:
            self._command_timeout_sec = max(self._timeout_sec, float(command_timeout_sec))

    def collect_recent_logs(self) -> list[str]:
        cmd = [
            "journalctl",
            "-u",
            self._service_name,
            "--since",
            f"{self._log_window_minutes} minutes ago",
            "--no-pager",
        ]
        output = self._run_allowed(cmd=cmd)
        lines = output.splitlines()
        pattern = re.compile(r"(ERROR|WARN|WATCHDOG|persist|sqlite_busy|alert_event)", re.IGNORECASE)
        selected = [line.strip() for line in lines if pattern.search(line)]
        return [self._sanitize(line) for line in selected if line.strip()]

    def collect_db_stats(self, *, trading_day: str | None) -> str:
        if trading_day and trading_day.isdigit() and len(trading_day) == 8:
            cmd = ["scripts/hk-tickctl", "db", "stats", "--day", trading_day]
        else:
            cmd = ["scripts/hk-tickctl", "db", "stats"]
        return self._sanitize(self._run_allowed(cmd=cmd, timeout_sec=self._command_timeout_sec))

    def collect_top_symbols(
        self,
        *,
        trading_day: str | None,
        limit: int,
        minutes: int,
        metric: str,
    ) -> str:
        safe_limit = max(1, min(30, int(limit)))
        safe_minutes = max(1, min(240, int(minutes)))
        safe_metric = metric.strip().lower()
        if safe_metric not in _TOP_METRICS:
            safe_metric = "rows"
        cmd = [
            "scripts/hk-tickctl",
            "db",
            "top-symbols",
            "--limit",
            str(safe_limit),
            "--minutes",
            str(safe_minutes),
            "--metric",
            safe_metric,
        ]
        if trading_day and _DAY_RE.match(trading_day):
            cmd.extend(["--day", trading_day])
        return self._sanitize(self._run_allowed(cmd=cmd, timeout_sec=self._command_timeout_sec))

    def collect_symbol_ticks(
        self,
        *,
        symbol: str,
        trading_day: str | None,
        last: int,
    ) -> str:
        safe_symbol = symbol.strip().upper()
        if not _SYMBOL_RE.match(safe_symbol):
            raise ValueError("symbol_not_allowed")
        safe_last = max(1, min(100, int(last)))
        cmd = [
            "scripts/hk-tickctl",
            "db",
            "symbol",
            safe_symbol,
            "--last",
            str(safe_last),
        ]
        if trading_day and _DAY_RE.match(trading_day):
            cmd.extend(["--day", trading_day])
        return self._sanitize(self._run_allowed(cmd=cmd, timeout_sec=self._command_timeout_sec))

    def _run_allowed(self, *, cmd: list[str], timeout_sec: float | None = None) -> str:
        allowed_prefixes = {
            ("journalctl", "-u"),
            ("scripts/hk-tickctl", "db"),
        }
        if tuple(cmd[:2]) not in allowed_prefixes:
            raise ValueError("command_not_allowed")
        effective_timeout = self._timeout_sec if timeout_sec is None else max(1.0, float(timeout_sec))
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            check=False,
        )
        text = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
        return text.strip()

    def _sanitize(self, text: str) -> str:
        if not text:
            return ""
        masked = re.sub(r"\b\d{8,}:[A-Za-z0-9_-]{20,}\b", "[REDACTED_TOKEN]", text)
        masked = re.sub(r"(?i)token\s*[=:]\s*\S+", "token=[REDACTED]", masked)
        return masked


class TelegramActionRouter:
    def __init__(
        self,
        *,
        context_store: ActionContextStore,
        ops_runner: SafeOpsCommandRunner,
        allowed_chat_id: str,
        admin_user_ids: set[int],
        log_max_lines: int,
        refresh_min_interval_sec: int,
        command_rate_limit_per_min: int,
        mute_chat_fn: Callable[[str, int], None],
        is_muted_fn: Callable[[str], bool],
        get_latest_health_ctx_fn: Callable[[], ActionContext | None],
        render_health_compact_fn: Callable[[Any, Any], RenderOutput],
        render_health_detail_fn: Callable[[Any, Any, bool], RenderOutput],
        render_alert_compact_fn: Callable[[Any, str], RenderOutput],
        render_alert_detail_fn: Callable[[Any, str, bool], RenderOutput],
        market_mode_of_event_fn: Callable[[Any], str],
        get_daily_top_anomalies_fn: Callable[[str], list[tuple[str, int]]],
        command_allowlist: set[str] | None = None,
        command_max_lookback_days: int = 30,
    ) -> None:
        self._store = context_store
        self._ops_runner = ops_runner
        self._allowed_chat_id = allowed_chat_id.strip()
        self._admin_user_ids = set(admin_user_ids)
        self._log_max_lines = max(1, int(log_max_lines))
        self._refresh_min_interval_sec = max(5, int(refresh_min_interval_sec))
        self._command_rate_limit_per_min = max(1, int(command_rate_limit_per_min))
        raw_allowlist = set(command_allowlist or _DEFAULT_COMMAND_ALLOWLIST)
        self._command_allowlist = {
            self._canonical_command(item.strip().lower())
            for item in raw_allowlist
            if item and item.strip()
        }
        if not self._command_allowlist:
            self._command_allowlist = set(_DEFAULT_COMMAND_ALLOWLIST)
        self._command_max_lookback_days = max(0, int(command_max_lookback_days))
        self._command_hits: dict[int, deque[float]] = {}
        self._mute_chat_fn = mute_chat_fn
        self._is_muted_fn = is_muted_fn
        self._get_latest_health_ctx_fn = get_latest_health_ctx_fn
        self._render_health_compact_fn = render_health_compact_fn
        self._render_health_detail_fn = render_health_detail_fn
        self._render_alert_compact_fn = render_alert_compact_fn
        self._render_alert_detail_fn = render_alert_detail_fn
        self._market_mode_of_event_fn = market_mode_of_event_fn
        self._get_daily_top_anomalies_fn = get_daily_top_anomalies_fn
        self._last_refresh_at: dict[str, float] = {}

    def parse_callback_data(self, data: str) -> CallbackRoute | None:
        text = data.strip()
        if not text:
            return None
        if len(text.encode("utf-8")) > _CALLBACK_MAX_BYTES:
            return None
        if ":" not in text:
            return None
        action, value = text.split(":", 1)
        normalized = action.strip().lower()
        if normalized not in {"d", "log", "db", "sop", "mute", "rf", "top"}:
            return None
        return CallbackRoute(action=normalized, value=value.strip())

    def parse_text_command(self, text: str) -> tuple[str, list[str]] | None:
        raw = text.strip()
        if not raw.startswith("/"):
            return None
        try:
            tokens = shlex.split(raw)
        except ValueError:
            tokens = raw.split()
        if not tokens:
            return None
        command_token = tokens[0]
        command_name = command_token[1:].split("@", 1)[0].strip().lower()
        return command_name, tokens[1:]

    async def handle_text_command(
        self,
        *,
        chat_id: str,
        user_id: int | None,
        text: str,
        trading_day: str | None,
    ) -> CallbackDispatchResult | None:
        parsed = self.parse_text_command(text)
        if parsed is None:
            return None

        authorized, deny_text = self._authorize(chat_id=chat_id, user_id=user_id)
        if not authorized:
            return CallbackDispatchResult(
                ack_text=None,
                messages=[RouterMessage(mode="send", kind="COMMAND", text=deny_text)],
            )
        if user_id is None:
            return CallbackDispatchResult(
                ack_text=None,
                messages=[RouterMessage(mode="send", kind="COMMAND", text="無法辨識操作者")],
            )
        if not self._within_command_rate_limit(user_id):
            return CallbackDispatchResult(
                ack_text=None,
                messages=[
                    RouterMessage(
                        mode="send",
                        kind="COMMAND",
                        text=(
                            "<b>⏱ 查詢過於頻繁</b>\n"
                            "結論：已觸發每分鐘頻率限制\n"
                            "下一步：請稍後約 1 分鐘再試"
                        ),
                    )
                ],
            )

        command, args = parsed
        command = self._canonical_command(command)
        if command not in self._command_allowlist:
            return self._render_command_result(
                "<b>⛔ 指令未啟用</b>\n"
                "結論：該命令目前不在允許清單\n"
                "下一步：請用 /help 查看已啟用命令"
            )
        try:
            if command == "help":
                return self._render_command_result(self._render_help_text())
            if command == "db_stats":
                return await self._on_command_db_stats(args=args, trading_day=trading_day)
            if command == "top_symbols":
                return await self._on_command_top_symbols(args=args, trading_day=trading_day)
            if command == "symbol":
                return await self._on_command_symbol(args=args, trading_day=trading_day)
            return self._render_command_result(
                "<b>❔ 未知指令</b>\n結論：目前不支援該命令\n下一步：請用 /help 查看可用命令"
            )
        except subprocess.TimeoutExpired:
            return self._render_command_result(
                "<b>⚠️ 操作逾時</b>\n"
                "結論：查詢超過逾時門檻\n"
                "下一步：縮小範圍（如 minutes/last）或稍後再試"
            )
        except Exception:
            return self._render_command_result(
                "<b>⚠️ 操作失敗</b>\n結論：指令執行失敗\n下一步：請稍後再試或查看服務日誌"
            )

    async def handle_callback_query(
        self,
        *,
        chat_id: str,
        message_id: int | None,
        user_id: int | None,
        data: str,
    ) -> CallbackDispatchResult:
        route = self.parse_callback_data(data)
        if route is None:
            return CallbackDispatchResult(ack_text="未知操作", messages=[])

        authorized, deny_text = self._authorize(chat_id=chat_id, user_id=user_id)
        if not authorized:
            return CallbackDispatchResult(ack_text=deny_text, messages=[])

        try:
            if route.action == "d":
                return self._on_toggle_detail(chat_id=chat_id, message_id=message_id, context_id=route.value)
            if route.action == "log":
                return await self._on_logs(context_id=route.value)
            if route.action == "db":
                return await self._on_db(context_id=route.value)
            if route.action == "sop":
                return self._on_sop(value=route.value)
            if route.action == "mute":
                return self._on_mute(chat_id=chat_id, value=route.value)
            if route.action == "rf":
                return self._on_refresh(chat_id=chat_id, message_id=message_id, context_id=route.value)
            if route.action == "top":
                return self._on_top(context_id=route.value)
        except subprocess.TimeoutExpired:
            return CallbackDispatchResult(
                ack_text="操作逾時",
                messages=[
                    RouterMessage(
                        mode="send",
                        text="<b>⚠️ 操作逾時</b>\n結論：查詢超時\n下一步：請稍後再試",
                    )
                ],
            )
        except Exception:
            return CallbackDispatchResult(
                ack_text="操作失敗",
                messages=[
                    RouterMessage(
                        mode="send",
                        text="<b>⚠️ 操作失敗</b>\n結論：互動查詢執行失敗\n下一步：請稍後再試或查看服務日誌",
                    )
                ],
            )

        return CallbackDispatchResult(ack_text=None, messages=[])

    def _on_toggle_detail(
        self,
        *,
        chat_id: str,
        message_id: int | None,
        context_id: str,
    ) -> CallbackDispatchResult:
        ctx = self._store.get(context_id)
        if ctx is None and message_id is not None:
            ctx = self._store.get_by_message(chat_id=chat_id, message_id=message_id)
        if ctx is None:
            return CallbackDispatchResult(ack_text="上下文已過期", messages=[])

        next_expanded = not ctx.detail_expanded
        self._store.set_detail_expanded(context_id=ctx.context_id, expanded=next_expanded)
        text = ctx.detail_text if next_expanded else ctx.compact_text
        text, _ = truncate_text(text)
        if message_id is None:
            return CallbackDispatchResult(
                ack_text="無法編輯原訊息",
                messages=[RouterMessage(mode="send", text=text, reply_markup=ctx.reply_markup)],
            )
        return CallbackDispatchResult(
            ack_text="已更新",
            messages=[
                RouterMessage(
                    mode="edit",
                    message_id=message_id,
                    text=text,
                    parse_mode=ctx.parse_mode,
                    reply_markup=ctx.reply_markup,
                    sid=ctx.sid,
                    eid=ctx.eid,
                )
            ],
        )

    async def _on_logs(self, *, context_id: str) -> CallbackDispatchResult:
        lines = await asyncio.to_thread(self._ops_runner.collect_recent_logs)
        clipped = lines[: self._log_max_lines]
        text = render_logs_summary(lines=clipped, truncated=len(lines) > len(clipped))
        text, _ = truncate_text(text)
        return CallbackDispatchResult(
            ack_text="日誌摘要已生成",
            messages=[RouterMessage(mode="send", text=text)],
        )

    async def _on_db(self, *, context_id: str) -> CallbackDispatchResult:
        ctx = self._store.get(context_id)
        trading_day = ctx.trading_day if ctx is not None else None
        try:
            output = await asyncio.to_thread(
                self._ops_runner.collect_db_stats,
                trading_day=trading_day,
            )
        except Exception:
            output = ""
        if not output and ctx is not None and ctx.snapshot is not None:
            output = render_db_status_from_snapshot(snapshot=ctx.snapshot)
        if not output:
            output = "<b>🗃 DB 狀態</b>\n結論：目前無可用資料\n下一步：稍後再試"
        text, _ = truncate_text(output)
        return CallbackDispatchResult(
            ack_text="DB 狀態已生成",
            messages=[RouterMessage(mode="send", text=text)],
        )

    def _on_sop(self, *, value: str) -> CallbackDispatchResult:
        text = render_sop(code=value)
        text, _ = truncate_text(text)
        return CallbackDispatchResult(
            ack_text="已提供建議",
            messages=[RouterMessage(mode="send", text=text)],
        )

    def _on_mute(self, *, chat_id: str, value: str) -> CallbackDispatchResult:
        seconds = 3600
        if value.isdigit():
            seconds = max(60, min(86400, int(value)))
        self._mute_chat_fn(chat_id, seconds)
        text = (
            "<b>🔕 靜音已啟用</b>\n"
            f"結論：此 chat 將靜音 {seconds // 60} 分鐘的 HEALTH/WARN 心跳\n"
            "關鍵指標：ALERT 類通知仍會送出\n"
            "下一步：若要即時查看現況可按「🔄 刷新」"
        )
        return CallbackDispatchResult(
            ack_text="已靜音",
            messages=[RouterMessage(mode="send", text=text)],
        )

    def _on_refresh(
        self,
        *,
        chat_id: str,
        message_id: int | None,
        context_id: str,
    ) -> CallbackDispatchResult:
        now = time.monotonic()
        last = self._last_refresh_at.get(chat_id, 0.0)
        if (now - last) < self._refresh_min_interval_sec:
            return CallbackDispatchResult(ack_text="刷新太頻繁", messages=[])
        self._last_refresh_at[chat_id] = now

        current = self._get_latest_health_ctx_fn()
        if current is None or current.snapshot is None or current.assessment is None:
            return CallbackDispatchResult(ack_text="目前沒有可刷新資料", messages=[])

        compact = self._render_health_compact_fn(current.snapshot, current.assessment)
        detail = self._render_health_detail_fn(current.snapshot, current.assessment, True)
        self._store.put(
            context_id=context_id,
            kind="HEALTH",
            compact_text=compact.text,
            detail_text=detail.text,
            parse_mode=compact.parse_mode,
            reply_markup=compact.reply_markup,
            sid=getattr(current.snapshot, "sid", None),
            trading_day=getattr(current.snapshot, "trading_day", None),
            snapshot=current.snapshot,
            assessment=current.assessment,
            digest=current.digest,
        )

        target_text = detail.text if current.detail_expanded else compact.text
        target_text, _ = truncate_text(target_text)
        if message_id is None:
            return CallbackDispatchResult(
                ack_text="已刷新",
                messages=[RouterMessage(mode="send", text=target_text, reply_markup=compact.reply_markup)],
            )
        return CallbackDispatchResult(
            ack_text="已刷新",
            messages=[
                RouterMessage(
                    mode="edit",
                    message_id=message_id,
                    text=target_text,
                    parse_mode=compact.parse_mode,
                    reply_markup=compact.reply_markup,
                    sid=getattr(current.snapshot, "sid", None),
                )
            ],
        )

    def _on_top(self, *, context_id: str) -> CallbackDispatchResult:
        ctx = self._store.get(context_id)
        day = ctx.trading_day if ctx and ctx.trading_day else ""
        pairs = self._get_daily_top_anomalies_fn(day)
        text = render_top_anomalies(pairs=pairs)
        text, _ = truncate_text(text)
        return CallbackDispatchResult(
            ack_text="已整理今日異常",
            messages=[RouterMessage(mode="send", text=text)],
        )

    async def _on_command_db_stats(
        self,
        *,
        args: list[str],
        trading_day: str | None,
    ) -> CallbackDispatchResult:
        try:
            positional, options = self._split_option_args(args=args, allowed={"day"})
        except ValueError:
            return self._render_command_result(
                "<b>❌ 參數錯誤</b>\n"
                "結論：/db_stats 只支援 [YYYYMMDD] 或 --day YYYYMMDD\n"
                "下一步：例 /db_stats --day 20260220"
            )
        if len(positional) > 1:
            return self._render_command_result(
                "<b>❌ 參數錯誤</b>\n"
                "結論：日期參數只能提供 1 個\n"
                "下一步：例 /db_stats 20260220"
            )
        day_override, day_error = self._resolve_command_day(
            day_hint=options.get("day") or (positional[0] if positional else None),
            trading_day=trading_day,
        )
        if day_error:
            return self._render_command_result(day_error)
        output = await asyncio.to_thread(
            self._ops_runner.collect_db_stats,
            trading_day=day_override,
        )
        if not output:
            output = "<b>🗃 DB 統計</b>\n結論：目前無可用資料\n下一步：請稍後再試"
        return self._render_command_result(output, escape_html=True)

    async def _on_command_top_symbols(
        self,
        *,
        args: list[str],
        trading_day: str | None,
    ) -> CallbackDispatchResult:
        try:
            positional, options = self._split_option_args(
                args=args, allowed={"limit", "minutes", "metric", "day"}
            )
        except ValueError:
            return self._render_command_result(
                "<b>❌ 參數錯誤</b>\n"
                "結論：可用參數為 --limit/--minutes/--metric/--day\n"
                "下一步：例 /top_symbols --limit 10 --minutes 15 --metric rows"
            )
        if len(positional) > 4:
            return self._render_command_result(
                "<b>❌ 參數錯誤</b>\n"
                "結論：參數過多\n"
                "下一步：例 /top_symbols 10 15 rows 20260220"
            )

        limit_text = options.get("limit") or (positional[0] if len(positional) >= 1 else "")
        minutes_text = options.get("minutes") or (positional[1] if len(positional) >= 2 else "")
        metric = (options.get("metric") or (positional[2] if len(positional) >= 3 else "rows")).strip().lower()
        day_hint = options.get("day") or (positional[3] if len(positional) >= 4 else None)

        if limit_text:
            try:
                limit = int(limit_text)
            except ValueError:
                return self._render_command_result(
                    "<b>❌ 參數錯誤</b>\n"
                    "結論：limit 需為整數\n"
                    "下一步：例 /top_symbols 10 15 rows"
                )
        else:
            limit = 10

        if minutes_text:
            try:
                minutes = int(minutes_text)
            except ValueError:
                return self._render_command_result(
                    "<b>❌ 參數錯誤</b>\n"
                    "結論：minutes 需為整數\n"
                    "下一步：例 /top_symbols 10 15 rows"
                )
        else:
            minutes = 15

        if metric not in _TOP_METRICS:
            return self._render_command_result(
                "<b>❌ 參數錯誤</b>\n結論：metric 只支援 rows/turnover/volume\n下一步：例 /top_symbols 10 15 rows"
            )
        day_override, day_error = self._resolve_command_day(
            day_hint=day_hint,
            trading_day=trading_day,
        )
        if day_error:
            return self._render_command_result(day_error)

        output = await asyncio.to_thread(
            self._ops_runner.collect_top_symbols,
            trading_day=day_override,
            limit=max(1, min(30, limit)),
            minutes=max(1, min(240, minutes)),
            metric=metric,
        )
        if not output:
            output = (
                "<b>📈 Top Symbols</b>\n"
                "結論：查無資料\n"
                "下一步：稍後再試，或確認 ticks 是否有落盤"
            )
        return self._render_command_result(output, escape_html=True)

    async def _on_command_symbol(
        self,
        *,
        args: list[str],
        trading_day: str | None,
    ) -> CallbackDispatchResult:
        if not args:
            return self._render_command_result(
                "<b>❌ 參數錯誤</b>\n結論：缺少 symbol\n下一步：例 /symbol HK.00700 20"
            )
        try:
            positional, options = self._split_option_args(args=args, allowed={"last", "day"})
        except ValueError:
            return self._render_command_result(
                "<b>❌ 參數錯誤</b>\n"
                "結論：可用參數為 --last/--day\n"
                "下一步：例 /symbol HK.00700 --last 20 --day 20260220"
            )
        if not positional:
            return self._render_command_result(
                "<b>❌ 參數錯誤</b>\n結論：缺少 symbol\n下一步：例 /symbol HK.00700 20"
            )
        if len(positional) > 3:
            return self._render_command_result(
                "<b>❌ 參數錯誤</b>\n結論：參數過多\n下一步：例 /symbol HK.00700 20 20260220"
            )

        symbol = positional[0].strip().upper()
        if not _SYMBOL_RE.match(symbol):
            return self._render_command_result(
                "<b>❌ 參數錯誤</b>\n結論：symbol 格式不正確\n下一步：例 /symbol HK.00700 20"
            )
        last_text = options.get("last") or (positional[1] if len(positional) >= 2 else "")
        if last_text:
            try:
                last = int(last_text)
            except ValueError:
                return self._render_command_result(
                    "<b>❌ 參數錯誤</b>\n結論：last 需為整數\n下一步：例 /symbol HK.00700 20"
                )
        else:
            last = 20
        day_override, day_error = self._resolve_command_day(
            day_hint=options.get("day") or (positional[2] if len(positional) >= 3 else None),
            trading_day=trading_day,
        )
        if day_error:
            return self._render_command_result(day_error)

        output = await asyncio.to_thread(
            self._ops_runner.collect_symbol_ticks,
            symbol=symbol,
            trading_day=day_override,
            last=max(1, min(100, last)),
        )
        if not output:
            output = (
                "<b>🔎 Symbol 查詢</b>\n"
                "結論：查無資料\n"
                "下一步：確認 symbol 與交易日是否正確"
            )
        return self._render_command_result(output, escape_html=True)

    def _render_help_text(self) -> str:
        return (
            "<b>🤖 可用指令</b>\n"
            "1) /db_stats [YYYYMMDD] 或 /db_stats --day YYYYMMDD\n"
            "2) /top_symbols [limit] [minutes] [rows|turnover|volume] [YYYYMMDD]\n"
            "   也可用 --limit/--minutes/--metric/--day\n"
            "3) /symbol &lt;SYMBOL&gt; [last] [YYYYMMDD]（支援 --last/--day）\n"
            f"4) 日期查詢最遠可回看 {self._command_max_lookback_days} 天\n"
            "下一步：例 /top_symbols --limit 10 --minutes 15 --metric rows --day 20260220"
        )

    def _render_command_result(self, text: str, *, escape_html: bool = False) -> CallbackDispatchResult:
        rendered_text = escape(text) if escape_html else text
        rendered, _ = truncate_text(rendered_text)
        return CallbackDispatchResult(
            ack_text=None,
            messages=[RouterMessage(mode="send", kind="COMMAND", text=rendered)],
        )

    def _authorize(self, *, chat_id: str, user_id: int | None) -> tuple[bool, str]:
        if self._allowed_chat_id and chat_id != self._allowed_chat_id:
            return False, "此 chat 不允許操作"
        if self._admin_user_ids and (user_id is None or int(user_id) not in self._admin_user_ids):
            return False, "你沒有操作權限"
        return True, ""

    def _canonical_command(self, command: str) -> str:
        normalized = command.strip().lower()
        if normalized == "start":
            return "help"
        return normalized

    def _split_option_args(
        self,
        *,
        args: list[str],
        allowed: set[str],
    ) -> tuple[list[str], dict[str, str]]:
        positional: list[str] = []
        options: dict[str, str] = {}
        index = 0
        while index < len(args):
            token = args[index].strip()
            if token.startswith("--"):
                option_text = token[2:]
                if not option_text:
                    raise ValueError("empty_option")
                if "=" in option_text:
                    raw_key, raw_value = option_text.split("=", 1)
                    value = raw_value.strip()
                else:
                    raw_key = option_text
                    index += 1
                    if index >= len(args):
                        raise ValueError("missing_option_value")
                    value = args[index].strip()
                key = raw_key.strip().replace("-", "_").lower()
                if key not in allowed or key in options or not value:
                    raise ValueError("invalid_option")
                options[key] = value
            else:
                positional.append(token)
            index += 1
        return positional, options

    def _resolve_command_day(
        self,
        *,
        day_hint: str | None,
        trading_day: str | None,
    ) -> tuple[str | None, str | None]:
        fallback_day = self._normalize_day(trading_day)
        if not day_hint:
            return fallback_day, None
        target_day = self._normalize_day(day_hint)
        if target_day is None:
            return None, (
                "<b>❌ 參數錯誤</b>\n"
                "結論：日期需為 YYYYMMDD（亦可用 YYYY-MM-DD）\n"
                "下一步：例 --day 20260220"
            )
        if fallback_day is None:
            return target_day, None
        today = datetime.strptime(fallback_day, "%Y%m%d").date()
        candidate = datetime.strptime(target_day, "%Y%m%d").date()
        delta_days = (today - candidate).days
        if delta_days < 0:
            return None, (
                "<b>❌ 日期超出範圍</b>\n"
                f"結論：不可查詢未來日期（today={fallback_day}）\n"
                "下一步：請改查今天或更早的交易日"
            )
        if delta_days > self._command_max_lookback_days:
            return None, (
                "<b>❌ 日期超出範圍</b>\n"
                f"結論：最多只允許回看 {self._command_max_lookback_days} 天\n"
                f"下一步：請改查 {fallback_day} 往前 {self._command_max_lookback_days} 天內資料"
            )
        return target_day, None

    def _normalize_day(self, value: str | None) -> str | None:
        if not value:
            return None
        normalized = value.strip().replace("-", "").replace("/", "")
        if _DAY_RE.match(normalized):
            return normalized
        return None

    def _within_command_rate_limit(self, user_id: int) -> bool:
        now = time.monotonic()
        bucket = self._command_hits.setdefault(user_id, deque())
        while bucket and (now - bucket[0]) >= 60.0:
            bucket.popleft()
        if len(bucket) >= self._command_rate_limit_per_min:
            return False
        bucket.append(now)
        return True


def summarize_alert_counts(events: Sequence[Any], *, trading_day: str) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for event in events:
        day = str(getattr(event, "trading_day", ""))
        if trading_day and day != trading_day:
            continue
        code = str(getattr(event, "code", "UNKNOWN")).upper()
        severity = str(getattr(event, "severity", ""))
        if "OK" in severity:
            continue
        counter[code] += 1
    return counter.most_common(5)
