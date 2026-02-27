from __future__ import annotations

import os
import re
from dataclasses import dataclass
from html import escape
from typing import Any, Sequence

TELEGRAM_MAX_MESSAGE_CHARS = 4096
_CALLBACK_MAX_BYTES = 64


@dataclass(frozen=True)
class RenderOutput:
    text: str
    parse_mode: str = "HTML"
    reply_markup: dict[str, Any] | None = None


def _market_mode_label(mode: str) -> str:
    mapping = {
        "pre-open": "盤前",
        "open": "盤中",
        "lunch-break": "午休",
        "after-hours": "盤後",
        "holiday-closed": "休市",
    }
    return mapping.get(mode, mode)


def _format_float(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _format_uptime(seconds: int | None) -> str:
    if seconds is None:
        return "n/a"
    total = max(0, int(seconds))
    hours, remain = divmod(total, 3600)
    minutes, secs = divmod(remain, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _cpu_load_summary(load1: float | None) -> tuple[str, str]:
    if load1 is None:
        return "n/a", "n/a"
    cores = os.cpu_count()
    if cores is None or cores <= 0:
        return f"{load1:.2f}", "n/a"
    pct = max(0.0, (float(load1) / float(cores)) * 100.0)
    return f"{load1:.2f}/{cores}c", f"{pct:.1f}%"


def _build_cb(prefix: str, value: str) -> str:
    raw = f"{prefix}:{value}".strip()
    if len(raw.encode("utf-8")) <= _CALLBACK_MAX_BYTES:
        return raw
    return raw.encode("utf-8")[:_CALLBACK_MAX_BYTES].decode("utf-8", errors="ignore")


def _keyboard(*rows: list[dict[str, str]]) -> dict[str, Any]:
    return {"inline_keyboard": [row for row in rows if row]}


def _sanitize_line(text: str, limit: int = 96) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip())
    return cleaned[:limit]


def _summary_from_event_lines(lines: Sequence[str], limit: int = 2) -> str:
    parts = [_sanitize_line(line, 44) for line in lines if line.strip()]
    if not parts:
        return "n/a"
    return " | ".join(parts[: max(1, int(limit))])


def _extract_kpi(lines: Sequence[str]) -> tuple[str, str, str]:
    lag = "n/a"
    persist = "n/a"
    queue = "n/a"
    for line in lines:
        text = line.strip()
        if lag == "n/a":
            m = re.search(r"(lag|lag_sec|drift|drift_sec)[=:]([0-9.]+)", text)
            if m:
                lag = f"{m.group(2)}s"
        if persist == "n/a":
            m = re.search(r"(persisted_per_min|persisted|min|write|write_per_min)[=:]([0-9.]+)", text)
            if m:
                persist = f"{m.group(2)}/min"
        if queue == "n/a":
            m = re.search(r"queue[=:]([0-9]+/[0-9]+)", text)
            if m:
                queue = m.group(1)
    return lag, persist, queue


def _basic_health_buttons(context_id: str, *, include_mute: bool, include_refresh: bool) -> dict[str, Any]:
    row1 = [
        {"text": "🔎 詳情", "callback_data": _build_cb("d", context_id)},
        {"text": "🧾 近20分鐘日誌", "callback_data": _build_cb("log", context_id)},
        {"text": "🗃 DB 狀態", "callback_data": _build_cb("db", context_id)},
    ]
    row2 = [
        {"text": "🧯 建議/處置", "callback_data": _build_cb("sop", context_id)},
    ]
    if include_mute:
        row2.append({"text": "🔕 靜音 1h", "callback_data": _build_cb("mute", "3600")})
    if include_refresh:
        row2.append({"text": "🔄 刷新", "callback_data": _build_cb("rf", context_id)})
    return _keyboard(row1, row2)


def render_health_compact(
    *,
    snapshot: Any,
    assessment: Any,
    include_system_metrics: bool,
    include_mute: bool,
    include_refresh: bool,
) -> RenderOutput:
    severity = str(getattr(assessment, "severity", "OK"))
    severity_label = "OK" if "OK" in severity else ("WARN" if "WARN" in severity else "ALERT")
    icon = "🟢" if severity_label == "OK" else ("🟡" if severity_label == "WARN" else "🔴")
    mode = _market_mode_label(getattr(assessment, "market_mode", "after-hours"))
    lag_sec = abs(float(getattr(snapshot, "drift_sec", 0.0) or 0.0))

    rows_today = int(getattr(snapshot, "db_rows", 0))
    persist = int(getattr(snapshot, "persisted_rows_per_min", 0))
    queue_size = int(getattr(snapshot, "queue_size", 0))
    queue_max = int(getattr(snapshot, "queue_maxsize", 0))
    disk_free = getattr(snapshot, "system_disk_free_gb", None)
    load1 = getattr(snapshot, "system_load1", None)
    rss_mb = getattr(snapshot, "system_rss_mb", None)
    uptime_sec = getattr(snapshot, "uptime_sec", None)
    load_text, cpu_pct = _cpu_load_summary(load1)

    summary = str(getattr(assessment, "conclusion", "狀態已更新")).strip()
    context_id = str(getattr(snapshot, "sid", "sid-unknown"))

    lines = [
        f"<b>{icon} HEALTH {severity_label}</b>",
        f"結論：{escape(summary)}",
        (
            "關鍵指標："
            f"市況={escape(mode)} | 落庫={persist}/min | 延遲={lag_sec:.1f}s | "
            f"今日rows={rows_today:,} | 佇列={queue_size}/{queue_max}"
        ),
    ]
    if include_system_metrics:
        lines.append(
            "資源："
            f"cpu(load1)={cpu_pct} | load1={load_text} | "
            f"rss={_format_float(rss_mb, 1)}MB | disk_free={_format_float(disk_free, 1)}GB"
        )
    lines.append(
        "運行："
        f"uptime={_format_uptime(uptime_sec)} | pid={getattr(snapshot, 'pid', 'n/a')}"
    )
    lines.extend(
        [
            "下一步：需要更多上下文請按「🔎 詳情」，要排查請先按「🧾 日誌」或「🗃 DB 狀態」。",
            f"sid={escape(context_id)}",
        ]
    )

    return RenderOutput(
        text="\n".join(lines),
        reply_markup=_basic_health_buttons(
            context_id,
            include_mute=include_mute,
            include_refresh=include_refresh,
        ),
    )


def render_health_detail(
    *,
    snapshot: Any,
    assessment: Any,
    expanded: bool,
    include_system_metrics: bool,
) -> RenderOutput:
    mode = _market_mode_label(getattr(assessment, "market_mode", "after-hours"))
    persist = int(getattr(snapshot, "persisted_rows_per_min", 0))
    push = int(getattr(snapshot, "push_rows_per_min", 0))
    poll = int(getattr(snapshot, "poll_accepted", 0))
    queue_size = int(getattr(snapshot, "queue_size", 0))
    queue_max = int(getattr(snapshot, "queue_maxsize", 0))
    lag_sec = abs(float(getattr(snapshot, "drift_sec", 0.0) or 0.0))
    symbols = getattr(snapshot, "symbols", [])
    symbol_count = len(symbols)
    max_age = 0.0
    max_lag = 0
    for item in symbols:
        age = getattr(item, "last_tick_age_sec", None)
        if age is not None:
            max_age = max(max_age, float(age))
        max_lag = max(max_lag, int(getattr(item, "max_seq_lag", 0)))

    if not expanded:
        text = (
            "<b>🔎 詳情（已收合）</b>\n"
            "結論：目前顯示為精簡模式。\n"
            "下一步：按「🔎 詳情」可展開工程指標（p95/p99、symbol lag、吞吐拆分）。"
        )
        return RenderOutput(text=text)

    lines = [
        "<b>🔎 詳情（已展開）</b>",
        f"結論：{escape(mode)} 目前詳細健康指標如下",
        (
            "關鍵指標："
            f"persist/min={persist} | push/min={push} | poll_accept/min={poll} | "
            f"queue={queue_size}/{queue_max} | drift={lag_sec:.2f}s"
        ),
        f"詳情：symbols={symbol_count} | max_tick_age={max_age:.1f}s | max_seq_lag={max_lag}",
    ]
    if include_system_metrics:
        load_text, cpu_pct = _cpu_load_summary(getattr(snapshot, "system_load1", None))
        lines.append(
            "詳情："
            f"cpu(load1)={cpu_pct} | load1={load_text} | "
            f"uptime={_format_uptime(getattr(snapshot, 'uptime_sec', None))} | "
            f"pid={getattr(snapshot, 'pid', 'n/a')} | "
            f"disk_free={_format_float(getattr(snapshot, 'system_disk_free_gb', None), 2)}GB | "
            f"rss={_format_float(getattr(snapshot, 'system_rss_mb', None), 1)}MB"
        )
    lines.append("下一步：內容已截斷，請用 🧾/🗃 看更多。")
    return RenderOutput(text="\n".join(lines))


def render_alert_compact(*, event: Any, market_mode: str) -> RenderOutput:
    severity = str(getattr(event, "severity", "ALERT"))
    sev_label = "WARN" if "WARN" in severity else ("OK" if "OK" in severity else "ALERT")
    icon = "🟡" if sev_label == "WARN" else ("✅" if sev_label == "OK" else "🔴")
    title = "ALERT" if sev_label == "ALERT" else ("WARN" if sev_label == "WARN" else "RECOVERED")

    code = str(getattr(event, "code", "UNKNOWN")).upper()
    headline = str(getattr(event, "headline", "") or "事件需關注")
    impact = str(getattr(event, "impact", "") or "可能影響即時資料完整性")
    summary = _summary_from_event_lines(getattr(event, "summary_lines", []), limit=2)
    event_id = str(getattr(event, "eid", "eid-unknown"))

    lines = [
        f"<b>{icon} {title}</b>",
        f"結論：{escape(headline)}",
        (
            "關鍵指標："
            f"事件={escape(code)} | 市況={escape(_market_mode_label(market_mode))} | "
            f"重點={escape(summary)}"
        ),
        f"影響：{escape(impact)}",
        "下一步：先按「🧾 近20分鐘日誌」確認是否持續，再按「🧯 建議/處置」。",
        f"eid={escape(event_id)} sid={escape(str(getattr(event, 'sid', 'n/a') or 'n/a'))}",
    ]
    keyboard = _keyboard(
        [
            {"text": "🔎 詳情", "callback_data": _build_cb("d", event_id)},
            {"text": "🧾 近20分鐘日誌", "callback_data": _build_cb("log", event_id)},
            {"text": "🗃 DB 狀態", "callback_data": _build_cb("db", str(getattr(event, "sid", "none") or "none"))},
        ],
        [
            {"text": "🧯 建議/處置", "callback_data": _build_cb("sop", code)},
            {"text": "🔄 刷新", "callback_data": _build_cb("rf", str(getattr(event, "sid", "none") or "none"))},
        ],
    )
    return RenderOutput(text="\n".join(lines), reply_markup=keyboard)


def render_alert_detail(*, event: Any, market_mode: str, expanded: bool) -> RenderOutput:
    code = str(getattr(event, "code", "UNKNOWN")).upper()
    summary_lines = list(getattr(event, "summary_lines", []))
    suggestions = [item.strip() for item in getattr(event, "suggestions", []) if str(item).strip()]

    if not expanded:
        return RenderOutput(
            text=(
                "<b>🔎 詳情（已收合）</b>\n"
                "結論：目前顯示為事件摘要。\n"
                "下一步：按「🔎 詳情」可展開事件細節與建議。"
            )
        )

    lines = [
        "<b>🔎 事件詳情（已展開）</b>",
        f"結論：{escape(code)} 事件詳細資訊",
        f"關鍵指標：市況={escape(_market_mode_label(market_mode))} | 條目={len(summary_lines)}",
        "詳情：" + escape(" | ".join(_sanitize_line(item, 80) for item in summary_lines[:3]) or "n/a"),
    ]
    if suggestions:
        lines.append("建議：" + escape("；".join(_sanitize_line(item, 80) for item in suggestions[:2])))
    lines.append("下一步：內容已截斷，請用 🧾/🗃 看更多。")
    return RenderOutput(text="\n".join(lines))


def render_daily_digest(
    *,
    snapshot: Any,
    digest: Any,
    context_id: str | None = None,
) -> RenderOutput:
    sid = str(getattr(snapshot, "sid", "sid-unknown"))
    ctx_id = context_id or sid
    lines = [
        "<b>📊 DAILY DIGEST</b>",
        f"結論：{escape(str(getattr(digest, 'trading_day', 'n/a')))} 收盤摘要",
        (
            "關鍵指標："
            f"總量={int(getattr(digest, 'total_rows', 0)):,} | 峰值={int(getattr(digest, 'peak_rows_per_min', 0))}/min | "
            f"最大延遲={float(getattr(digest, 'max_lag_sec', 0.0)):.1f}s | "
            f"告警/恢復={int(getattr(digest, 'alert_count', 0))}/{int(getattr(digest, 'recovered_count', 0))}"
        ),
        f"資料檔：{escape(str(getattr(digest, 'db_path', 'n/a')))} | rows={int(getattr(digest, 'db_rows', 0)):,}",
        "下一步：可按「📈 今日 Top 異常」看今天最常見事件；若無異常會顯示「無」。",
        f"sid={escape(sid)}",
    ]
    keyboard = _keyboard(
        [
            {"text": "🔎 詳情", "callback_data": _build_cb("d", ctx_id)},
            {"text": "🗃 DB 狀態", "callback_data": _build_cb("db", sid)},
            {"text": "📈 今日 Top 異常", "callback_data": _build_cb("top", ctx_id)},
        ],
        [{"text": "🧯 建議/處置", "callback_data": _build_cb("sop", "HEALTH")}],
    )
    return RenderOutput(text="\n".join(lines), reply_markup=keyboard)


def truncate_text(text: str, *, max_chars: int = TELEGRAM_MAX_MESSAGE_CHARS) -> tuple[str, bool]:
    limit = max(1, int(max_chars))
    if len(text) <= limit:
        return text, False
    suffix = "\n... 內容已截斷，請用 🧾/🗃 看更多。"
    keep = max(0, limit - len(suffix))
    return text[:keep] + suffix, True


def render_db_status_from_snapshot(*, snapshot: Any) -> str:
    return "\n".join(
        [
            "<b>🗃 DB 狀態</b>",
            f"結論：{escape(str(getattr(snapshot, 'trading_day', 'n/a')))} 資料庫快照",
            (
                "關鍵指標："
                f"rows={int(getattr(snapshot, 'db_rows', 0)):,} | "
                f"max_ts_utc={escape(str(getattr(snapshot, 'db_max_ts_utc', 'n/a')))} | "
                f"drift={_format_float(getattr(snapshot, 'drift_sec', None), 2)}s"
            ),
            f"檔案：{escape(str(getattr(snapshot, 'db_path', 'n/a')))}",
            "下一步：若 drift 持續上升，先看 🧾 再看 🧯。",
        ]
    )


def render_sop(*, code: str) -> str:
    normalized = code.strip().upper() or "HEALTH"
    mapping = {
        "PERSIST_STALL": (
            "先確認是否真的停寫",
            "看最近 20 分鐘日誌，確認 queue 是否持續上升",
            "看 DB max_ts 是否前進；若未前進再看 service 狀態",
        ),
        "SQLITE_BUSY": (
            "先確認是否鎖競爭",
            "看 sqlite_busy 是否連續出現並拖慢 persist/min",
            "若持續 >10 分鐘，再排查並行寫入或 I/O 壓力",
        ),
        "DISCONNECT": (
            "先確認連線中斷範圍",
            "看 OpenD / collector service 是否 active",
            "觀察是否出現 RECOVERED，若無則依 runbook 重啟服務",
        ),
        "HEALTH": (
            "先看健康趨勢",
            "先按 🧾 再按 🗃，判斷是延遲問題還是停寫",
            "確認是否需要人工介入（通常盤前/盤後可先觀察）",
        ),
    }
    title, step1, step2 = mapping.get(normalized, mapping["HEALTH"])
    return "\n".join(
        [
            "<b>🧯 建議/處置</b>",
            f"結論：{escape(normalized)} {escape(title)}",
            f"關鍵指標：步驟1={escape(step1)} | 步驟2={escape(step2)}",
            "下一步：若異常持續超過 10 分鐘，請交給值班 SRE。",
        ]
    )


def render_logs_summary(*, lines: Sequence[str], truncated: bool) -> str:
    body = "\n".join(lines) if lines else "（無）"
    tail = "\n... 已截斷" if truncated else ""
    return "\n".join(
        [
            "<b>🧾 近20分鐘日誌摘要</b>",
            "結論：已整理高優先訊息（ERROR/WARN/WATCHDOG/persist/sqlite_busy）",
            f"關鍵指標：行數={len(lines)}",
            "下一步：若同類錯誤重複，請按 🧯 依 SOP 處置",
            escape(body) + tail,
        ]
    )


def render_top_anomalies(*, pairs: Sequence[tuple[str, int]]) -> str:
    if not pairs:
        return "\n".join(
            [
                "<b>📈 今日 Top 異常</b>",
                "結論：今日無異常事件",
                "關鍵指標：Top=無",
                "下一步：維持觀察即可",
            ]
        )
    top_lines = [f"{code}: {count}" for code, count in pairs[:5]]
    return "\n".join(
        [
            "<b>📈 今日 Top 異常</b>",
            "結論：今日異常分佈如下",
            f"關鍵指標：總類型={len(pairs)} | Top1={pairs[0][0]}({pairs[0][1]})",
            "下一步：先處理出現次數最高的事件",
            escape("\n".join(top_lines)),
        ]
    )


def callback_data_len_ok(reply_markup: dict[str, Any] | None) -> bool:
    if not reply_markup:
        return True
    keyboard = reply_markup.get("inline_keyboard")
    if not isinstance(keyboard, list):
        return True
    for row in keyboard:
        if not isinstance(row, list):
            continue
        for button in row:
            if not isinstance(button, dict):
                continue
            callback_data = str(button.get("callback_data", ""))
            if len(callback_data.encode("utf-8")) > _CALLBACK_MAX_BYTES:
                return False
    return True


def summarize_event_kpi(lines: Sequence[str]) -> str:
    lag, persist, queue = _extract_kpi(lines)
    return f"延遲={lag} | 寫入={persist} | 佇列={queue}"
