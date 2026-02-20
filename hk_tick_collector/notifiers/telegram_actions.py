from __future__ import annotations

import asyncio
import re
import shlex
import subprocess
import time
from collections import Counter, deque
from dataclasses import dataclass
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
    ) -> None:
        self._service_name = service_name
        self._log_window_minutes = max(1, int(log_window_minutes))
        self._timeout_sec = max(1.0, float(timeout_sec))

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
        return self._sanitize(self._run_allowed(cmd=cmd))

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
        if safe_metric not in {"rows", "turnover", "volume"}:
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
        return self._sanitize(self._run_allowed(cmd=cmd))

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
        return self._sanitize(self._run_allowed(cmd=cmd))

    def _run_allowed(self, *, cmd: list[str]) -> str:
        allowed_prefixes = {
            ("journalctl", "-u"),
            ("scripts/hk-tickctl", "db"),
        }
        if tuple(cmd[:2]) not in allowed_prefixes:
            raise ValueError("command_not_allowed")
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self._timeout_sec,
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
    ) -> None:
        self._store = context_store
        self._ops_runner = ops_runner
        self._allowed_chat_id = allowed_chat_id.strip()
        self._admin_user_ids = set(admin_user_ids)
        self._log_max_lines = max(1, int(log_max_lines))
        self._refresh_min_interval_sec = max(5, int(refresh_min_interval_sec))
        self._command_rate_limit_per_min = max(1, int(command_rate_limit_per_min))
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
        try:
            if command in {"help", "start"}:
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
                "<b>⚠️ 操作逾時</b>\n結論：查詢超時\n下一步：請稍後再試"
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
        day_override: str | None = None
        if args:
            candidate = args[0].strip()
            if not _DAY_RE.match(candidate):
                return self._render_command_result(
                    "<b>❌ 參數錯誤</b>\n結論：日期需為 YYYYMMDD\n下一步：用 /db_stats 或 /db_stats 20260220"
                )
            day_override = candidate
        output = await asyncio.to_thread(
            self._ops_runner.collect_db_stats,
            trading_day=day_override or trading_day,
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
        limit = 10
        minutes = 15
        metric = "rows"
        if args:
            if not args[0].isdigit():
                return self._render_command_result(
                    "<b>❌ 參數錯誤</b>\n結論：limit 需為整數\n下一步：例 /top_symbols 10 15 rows"
                )
            limit = int(args[0])
        if len(args) >= 2:
            if not args[1].isdigit():
                return self._render_command_result(
                    "<b>❌ 參數錯誤</b>\n結論：minutes 需為整數\n下一步：例 /top_symbols 10 15 rows"
                )
            minutes = int(args[1])
        if len(args) >= 3:
            metric = args[2].strip().lower()
        if metric not in {"rows", "turnover", "volume"}:
            return self._render_command_result(
                "<b>❌ 參數錯誤</b>\n結論：metric 只支援 rows/turnover/volume\n下一步：例 /top_symbols 10 15 rows"
            )

        output = await asyncio.to_thread(
            self._ops_runner.collect_top_symbols,
            trading_day=trading_day,
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
        symbol = args[0].strip().upper()
        if not _SYMBOL_RE.match(symbol):
            return self._render_command_result(
                "<b>❌ 參數錯誤</b>\n結論：symbol 格式不正確\n下一步：例 /symbol HK.00700 20"
            )
        last = 20
        if len(args) >= 2:
            if not args[1].isdigit():
                return self._render_command_result(
                    "<b>❌ 參數錯誤</b>\n結論：last 需為整數\n下一步：例 /symbol HK.00700 20"
                )
            last = int(args[1])

        output = await asyncio.to_thread(
            self._ops_runner.collect_symbol_ticks,
            symbol=symbol,
            trading_day=trading_day,
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
            "1) /db_stats [YYYYMMDD]\n"
            "2) /top_symbols [limit] [minutes] [rows|turnover|volume]\n"
            "3) /symbol &lt;SYMBOL&gt; [last]\n"
            "下一步：例 /top_symbols 10 15 rows"
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
