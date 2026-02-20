import asyncio
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from hk_tick_collector.notifiers.telegram import (
    AlertEvent,
    AlertStateMachine,
    HealthSnapshot,
    NotifySeverity,
    SymbolSnapshot,
    TelegramNotifier,
    TelegramSendResult,
)
from hk_tick_collector.notifiers.telegram_actions import (
    ActionContextStore,
    CallbackRoute,
    SafeOpsCommandRunner,
    TelegramActionRouter,
)
from hk_tick_collector.notifiers.telegram_render import (
    callback_data_len_ok,
    render_alert_compact,
    render_alert_detail,
    render_daily_digest,
    render_health_compact,
    render_health_detail,
)


def _make_snapshot(
    *,
    created_at: datetime | None = None,
    persisted_per_min: int = 12000,
    drift_sec: float | None = 1.2,
    queue_size: int = 8,
    sid: str = "sid-a1b2c3d4",
) -> HealthSnapshot:
    return HealthSnapshot(
        created_at=created_at or datetime.now(tz=timezone.utc),
        pid=111,
        uptime_sec=7200,
        trading_day="20260214",
        db_path=Path("/data/sqlite/HK/20260214.db"),
        db_rows=2300000,
        db_max_ts_utc="2026-02-14T02:22:10+00:00",
        drift_sec=drift_sec,
        queue_size=queue_size,
        queue_maxsize=1000,
        push_rows_per_min=18000,
        poll_fetched=200,
        poll_accepted=180,
        persisted_rows_per_min=persisted_per_min,
        dropped_duplicate=22,
        symbols=[
            SymbolSnapshot(
                symbol="HK.00700",
                last_tick_age_sec=2.3,
                last_persisted_seq=100,
                max_seq_lag=1,
            ),
            SymbolSnapshot(
                symbol="HK.00981",
                last_tick_age_sec=3.1,
                last_persisted_seq=88,
                max_seq_lag=0,
            ),
        ],
        system_load1=0.32,
        system_rss_mb=92.6,
        system_disk_free_gb=120.5,
        sid=sid,
    )


def _make_alert(*, severity: NotifySeverity = NotifySeverity.ALERT) -> AlertEvent:
    return AlertEvent(
        created_at=datetime.now(tz=timezone.utc),
        code="PERSIST_STALL",
        key="PERSIST_STALL",
        fingerprint="PERSIST_STALL",
        trading_day="20260214",
        severity=severity.value,
        headline="異常：持久化停滯",
        impact="資料可能持續落後",
        summary_lines=["lag_sec=88.2", "persisted_per_min=0", "queue=420/1000"],
        suggestions=["看 logs", "看 db stats"],
        sid="sid-a1b2c3d4",
        eid="eid-aabbccdd",
    )


def _build_router(store: ActionContextStore) -> TelegramActionRouter:
    class _FakeOps:
        def collect_recent_logs(self):
            return [
                "ERROR persist stalled token=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                "WARN sqlite_busy delta=5",
                "WATCHDOG persistent_stall",
            ]

        def collect_db_stats(self, *, trading_day):
            return f"rows=10 max_ts=2026 drift=1.2 day={trading_day}"

        def collect_top_symbols(self, *, trading_day, limit, minutes, metric):
            return (
                "=== Top Symbols ===\n"
                f"day={trading_day} limit={limit} minutes={minutes} metric={metric}\n"
                "symbol,rows\nHK.00700,100"
            )

        def collect_symbol_ticks(self, *, symbol, trading_day, last):
            return f"symbol,rows\n{symbol},{last} day={trading_day}"

    snapshot = _make_snapshot()
    sm = AlertStateMachine(drift_warn_sec=120)
    assessment = sm.assess_health(snapshot)

    def _latest_ctx():
        store.put(
            context_id=snapshot.sid,
            kind="HEALTH",
            compact_text="compact",
            detail_text="detail",
            snapshot=snapshot,
            assessment=assessment,
            trading_day=snapshot.trading_day,
        )
        return store.get(snapshot.sid)

    def _render_health_compact(snap, assess):
        return render_health_compact(
            snapshot=snap,
            assessment=assess,
            include_system_metrics=True,
            include_mute=True,
            include_refresh=True,
        )

    def _render_health_detail(snap, assess, expanded):
        return render_health_detail(
            snapshot=snap,
            assessment=assess,
            expanded=expanded,
            include_system_metrics=True,
        )

    def _render_alert_compact(event, mode):
        return render_alert_compact(event=event, market_mode=mode)

    def _render_alert_detail(event, mode, expanded):
        return render_alert_detail(event=event, market_mode=mode, expanded=expanded)

    return TelegramActionRouter(
        context_store=store,
        ops_runner=_FakeOps(),
        allowed_chat_id="-100123",
        admin_user_ids={1001},
        log_max_lines=2,
        refresh_min_interval_sec=5,
        command_rate_limit_per_min=5,
        mute_chat_fn=lambda _chat_id, _seconds: None,
        is_muted_fn=lambda _chat_id: False,
        get_latest_health_ctx_fn=_latest_ctx,
        render_health_compact_fn=_render_health_compact,
        render_health_detail_fn=_render_health_detail,
        render_alert_compact_fn=_render_alert_compact,
        render_alert_detail_fn=_render_alert_detail,
        market_mode_of_event_fn=lambda _event: "open",
        get_daily_top_anomalies_fn=lambda _day: [("PERSIST_STALL", 3), ("SQLITE_BUSY", 2)],
    )


def test_render_health_compact_has_conclusion_kpi_and_buttons():
    sm = AlertStateMachine(drift_warn_sec=120)
    snapshot = _make_snapshot()
    assessment = sm.assess_health(snapshot)
    rendered = render_health_compact(
        snapshot=snapshot,
        assessment=assessment,
        include_system_metrics=True,
        include_mute=True,
        include_refresh=True,
    )
    assert "結論：" in rendered.text
    assert "關鍵指標：" in rendered.text
    assert "下一步：" in rendered.text
    assert rendered.reply_markup is not None
    assert callback_data_len_ok(rendered.reply_markup)


def test_render_alert_and_digest_are_actionable_and_callback_safe():
    alert = _make_alert()
    alert_rendered = render_alert_compact(event=alert, market_mode="open")
    assert "影響：" in alert_rendered.text
    assert "下一步：" in alert_rendered.text
    assert callback_data_len_ok(alert_rendered.reply_markup)

    digest_rendered = render_daily_digest(
        snapshot=_make_snapshot(),
        digest=type(
            "Digest",
            (),
            {
                "trading_day": "20260214",
                "total_rows": 1000000,
                "peak_rows_per_min": 38000,
                "max_lag_sec": 3.2,
                "alert_count": 4,
                "recovered_count": 3,
                "db_rows": 2300000,
                "db_path": "/data/sqlite/HK/20260214.db",
            },
        )(),
        context_id="dg-sid-a1b2c3d4",
    )
    assert "今日 Top 異常" in digest_rendered.text
    assert callback_data_len_ok(digest_rendered.reply_markup)


def test_action_context_store_ttl_cleanup():
    store = ActionContextStore(ttl_sec=3600)
    store.put(context_id="sid-1", kind="HEALTH", compact_text="a", detail_text="b")
    assert store.get("sid-1") is not None


def test_router_parse_compact_callback_format():
    store = ActionContextStore(ttl_sec=3600)
    router = _build_router(store)
    parsed = router.parse_callback_data("d:sid-a1b2c3d4")
    assert parsed == CallbackRoute(action="d", value="sid-a1b2c3d4")
    assert router.parse_callback_data("unknown") is None


def test_router_toggle_detail_returns_edit_message_payload():
    store = ActionContextStore(ttl_sec=3600)
    router = _build_router(store)
    store.put(
        context_id="sid-a1b2c3d4",
        kind="HEALTH",
        compact_text="<b>compact</b>",
        detail_text="<b>detail</b>",
        reply_markup={"inline_keyboard": [[{"text": "x", "callback_data": "d:sid-a1b2c3d4"}]]},
    )

    async def runner():
        dispatch = await router.handle_callback_query(
            chat_id="-100123",
            message_id=88,
            user_id=1001,
            data="d:sid-a1b2c3d4",
        )
        assert dispatch.messages
        msg = dispatch.messages[0]
        assert msg.mode == "edit"
        assert msg.message_id == 88
        assert "detail" in msg.text

    asyncio.run(runner())


def test_router_logs_summary_is_truncated_and_sanitized():
    store = ActionContextStore(ttl_sec=3600)
    router = _build_router(store)

    async def runner():
        dispatch = await router.handle_callback_query(
            chat_id="-100123",
            message_id=1,
            user_id=1001,
            data="log:sid-a1b2c3d4",
        )
        assert dispatch.messages
        text = dispatch.messages[0].text
        assert "近20分鐘日誌摘要" in text
        assert "行數=2" in text

    asyncio.run(runner())


def test_safe_ops_runner_sanitizes_token_text():
    runner = SafeOpsCommandRunner()
    redacted = runner._sanitize("token=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ")  # noqa: SLF001
    assert "REDACTED" in redacted


def test_router_db_output_is_truncated():
    store = ActionContextStore(ttl_sec=3600)

    class _VerboseOps:
        def collect_recent_logs(self):
            return []

        def collect_db_stats(self, *, trading_day):
            return "x" * 5000 + f" day={trading_day}"

    router = TelegramActionRouter(
        context_store=store,
        ops_runner=_VerboseOps(),
        allowed_chat_id="-100123",
        admin_user_ids={1001},
        log_max_lines=20,
        refresh_min_interval_sec=5,
        command_rate_limit_per_min=5,
        mute_chat_fn=lambda _chat_id, _seconds: None,
        is_muted_fn=lambda _chat_id: False,
        get_latest_health_ctx_fn=lambda: None,
        render_health_compact_fn=lambda *_: render_health_compact(
            snapshot=_make_snapshot(),
            assessment=AlertStateMachine(drift_warn_sec=120).assess_health(_make_snapshot()),
            include_system_metrics=True,
            include_mute=True,
            include_refresh=True,
        ),
        render_health_detail_fn=lambda *_: render_health_detail(
            snapshot=_make_snapshot(),
            assessment=AlertStateMachine(drift_warn_sec=120).assess_health(_make_snapshot()),
            expanded=True,
            include_system_metrics=True,
        ),
        render_alert_compact_fn=lambda *_: render_alert_compact(event=_make_alert(), market_mode="open"),
        render_alert_detail_fn=lambda *_: render_alert_detail(
            event=_make_alert(),
            market_mode="open",
            expanded=True,
        ),
        market_mode_of_event_fn=lambda _event: "open",
        get_daily_top_anomalies_fn=lambda _day: [],
    )
    store.put(
        context_id="sid-a1b2c3d4",
        kind="HEALTH",
        compact_text="a",
        detail_text="b",
        trading_day="20260214",
        snapshot=_make_snapshot(),
    )

    async def runner():
        dispatch = await router.handle_callback_query(
            chat_id="-100123",
            message_id=1,
            user_id=1001,
            data="db:sid-a1b2c3d4",
        )
        assert dispatch.messages
        assert "內容已截斷" in dispatch.messages[0].text

    asyncio.run(runner())


def test_router_rejects_non_admin_user():
    store = ActionContextStore(ttl_sec=3600)
    router = _build_router(store)

    async def runner():
        dispatch = await router.handle_callback_query(
            chat_id="-100123",
            message_id=1,
            user_id=9999,
            data="db:sid-a1b2c3d4",
        )
        assert dispatch.messages == []
        assert dispatch.ack_text == "你沒有操作權限"

    asyncio.run(runner())


def test_router_supports_text_commands():
    store = ActionContextStore(ttl_sec=3600)
    router = _build_router(store)

    async def runner():
        dispatch = await router.handle_text_command(
            chat_id="-100123",
            user_id=1001,
            text="/top_symbols 5 10 rows",
            trading_day="20260214",
        )
        assert dispatch is not None
        assert dispatch.messages
        assert "Top Symbols" in dispatch.messages[0].text

    asyncio.run(runner())


def test_router_help_command_escapes_symbol_placeholder():
    store = ActionContextStore(ttl_sec=3600)
    router = _build_router(store)

    async def runner():
        dispatch = await router.handle_text_command(
            chat_id="-100123",
            user_id=1001,
            text="/help",
            trading_day="20260214",
        )
        assert dispatch is not None
        assert dispatch.messages
        assert "&lt;SYMBOL&gt;" in dispatch.messages[0].text

    asyncio.run(runner())


def test_router_text_command_rate_limit():
    store = ActionContextStore(ttl_sec=3600)
    router = _build_router(store)

    async def runner():
        first = await router.handle_text_command(
            chat_id="-100123",
            user_id=1001,
            text="/help",
            trading_day="20260214",
        )
        assert first is not None
        second = await router.handle_text_command(
            chat_id="-100123",
            user_id=1001,
            text="/help",
            trading_day="20260214",
        )
        assert second is not None
        third = await router.handle_text_command(
            chat_id="-100123",
            user_id=1001,
            text="/help",
            trading_day="20260214",
        )
        assert third is not None
        fourth = await router.handle_text_command(
            chat_id="-100123",
            user_id=1001,
            text="/help",
            trading_day="20260214",
        )
        assert fourth is not None
        fifth = await router.handle_text_command(
            chat_id="-100123",
            user_id=1001,
            text="/help",
            trading_day="20260214",
        )
        assert fifth is not None
        blocked = await router.handle_text_command(
            chat_id="-100123",
            user_id=1001,
            text="/help",
            trading_day="20260214",
        )
        assert blocked is not None
        assert "查詢過於頻繁" in blocked.messages[0].text

    asyncio.run(runner())


def test_notifier_callback_calls_answer_then_emits_edit_message():
    async def runner() -> None:
        events: list[str] = []

        def fake_sender(payload):
            events.append("send")
            return TelegramSendResult(ok=True, status_code=200, message_id=77)

        notifier = TelegramNotifier(
            enabled=True,
            bot_token="1234567890:ABCDEF",
            chat_id="-100123",
            parse_mode="HTML",
            sender=fake_sender,
            enable_callbacks=False,
            interactive_enabled=False,
            admin_user_ids=[1001],
        )
        await notifier.start()
        notifier._client.answer_callback_query = (  # type: ignore[method-assign]
            lambda **_: events.append("answer")
        )
        notifier._client.edit_message_text = (  # type: ignore[method-assign]
            lambda **_: events.append("edit") or TelegramSendResult(ok=True, status_code=200)
        )

        sm = AlertStateMachine(drift_warn_sec=120)
        snapshot = _make_snapshot()
        assessment = sm.assess_health(snapshot)
        compact = render_health_compact(
            snapshot=snapshot,
            assessment=assessment,
            include_system_metrics=True,
            include_mute=True,
            include_refresh=True,
        )
        detail = render_health_detail(
            snapshot=snapshot,
            assessment=assessment,
            expanded=True,
            include_system_metrics=True,
        )
        notifier._store_action_context(  # type: ignore[attr-defined]
            context_id=snapshot.sid,
            kind="HEALTH",
            compact=compact,
            detail=detail,
            sid=snapshot.sid,
            trading_day=snapshot.trading_day,
            snapshot=snapshot,
            assessment=assessment,
        )

        await notifier._handle_callback(  # type: ignore[attr-defined]
            {
                "id": "cb-1",
                "from": {"id": 1001},
                "data": f"d:{snapshot.sid}",
                "message": {
                    "chat": {"id": -100123},
                    "message_id": 99,
                    "message_thread_id": 555,
                },
            }
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        await notifier.stop()

        assert events[0] == "answer"
        assert "edit" in events

    asyncio.run(runner())


def test_notifier_text_command_emits_reply_message():
    async def runner() -> None:
        calls: list[dict] = []

        def fake_sender(payload):
            calls.append(dict(payload))
            return TelegramSendResult(ok=True, status_code=200, message_id=77)

        notifier = TelegramNotifier(
            enabled=True,
            bot_token="1234567890:ABCDEF",
            chat_id="-100123",
            parse_mode="HTML",
            sender=fake_sender,
            enable_callbacks=False,
            interactive_enabled=False,
            admin_user_ids=[1001],
        )
        await notifier.start()
        await notifier._handle_command_message(  # type: ignore[attr-defined]
            {
                "chat": {"id": -100123},
                "text": "/help",
                "from": {"id": 1001},
                "message_thread_id": 321,
            }
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        await notifier.stop()

        assert calls
        assert "可用指令" in calls[0]["text"]
        assert calls[0]["message_thread_id"] == 321

    asyncio.run(runner())


def test_retry_after_respected_and_eventually_succeeds():
    async def runner() -> None:
        calls = []
        responses = deque(
            [
                TelegramSendResult(ok=False, status_code=429, retry_after=2),
                TelegramSendResult(ok=True, status_code=200, message_id=10),
            ]
        )

        def fake_sender(payload):
            calls.append(dict(payload))
            return responses.popleft()

        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        notifier = TelegramNotifier(
            enabled=True,
            bot_token="1234567890:ABCDEF",
            chat_id="-100123",
            parse_mode="HTML",
            sender=fake_sender,
            sleep=fake_sleep,
            interactive_enabled=False,
        )
        await notifier.start()
        notifier.submit_alert(_make_alert())
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        await notifier.stop()

        assert len(calls) == 2
        assert any(item == 2 for item in sleeps)

    asyncio.run(runner())


def test_notifier_renders_productized_health_and_alert():
    async def runner() -> None:
        calls = []

        def fake_sender(payload):
            calls.append(dict(payload))
            return TelegramSendResult(ok=True, status_code=200, message_id=55)

        notifier = TelegramNotifier(
            enabled=True,
            bot_token="1234567890:ABCDEF",
            chat_id="-100123",
            parse_mode="HTML",
            sender=fake_sender,
            interactive_enabled=False,
        )
        await notifier.start()

        notifier.submit_health(_make_snapshot())
        notifier.submit_alert(_make_alert())
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        await notifier.stop()

        assert any("結論：" in item["text"] for item in calls)
        assert any("關鍵指標：" in item["text"] for item in calls)
        assert any("下一步：" in item["text"] for item in calls)

    asyncio.run(runner())
