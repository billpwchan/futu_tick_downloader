import asyncio
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from hk_tick_collector.notifiers.telegram import (
    ALERT_CADENCE_SEC,
    AFTER_HOURS_CADENCE_SEC,
    NOTIFY_SCHEMA_VERSION,
    OPEN_CADENCE_SEC,
    PREOPEN_CADENCE_SEC,
    WARN_CADENCE_SEC,
    AlertEvent,
    AlertStateMachine,
    DedupeStore,
    HealthSnapshot,
    MessageRenderer,
    NotifySeverity,
    SymbolSnapshot,
    TelegramNotifier,
    TelegramSendResult,
    _DailyDigestState,
    truncate_rendered_message,
)


def _make_snapshot(
    *,
    created_at: datetime | None = None,
    persisted_per_min: int = 800,
    drift_sec: float | None = 1.0,
    queue_size: int = 5,
    symbol_lag: int = 0,
    push_rows_per_min: int = 1000,
    poll_accepted: int = 180,
    symbols: list[SymbolSnapshot] | None = None,
) -> HealthSnapshot:
    default_symbols = [
        SymbolSnapshot(
            symbol="HK.00700",
            last_tick_age_sec=5.0,
            last_persisted_seq=120,
            max_seq_lag=symbol_lag,
        ),
        SymbolSnapshot(
            symbol="HK.00981",
            last_tick_age_sec=8.5,
            last_persisted_seq=88,
            max_seq_lag=0,
        ),
    ]
    return HealthSnapshot(
        created_at=created_at or datetime.now(tz=timezone.utc),
        pid=1234,
        uptime_sec=3661,
        trading_day="20260212",
        db_path=Path("/data/sqlite/HK/20260212.db"),
        db_rows=123456,
        db_max_ts_utc="2026-02-12T09:30:00+00:00",
        drift_sec=drift_sec,
        queue_size=queue_size,
        queue_maxsize=1000,
        push_rows_per_min=push_rows_per_min,
        poll_fetched=200,
        poll_accepted=poll_accepted,
        persisted_rows_per_min=persisted_per_min,
        dropped_duplicate=20,
        symbols=symbols or default_symbols,
        system_load1=0.12,
        system_rss_mb=88.6,
        system_disk_free_gb=123.4,
    )


def test_sid_eid_format_is_short_and_stable():
    snapshot = _make_snapshot()
    event = AlertEvent(
        created_at=datetime.now(tz=timezone.utc),
        code="DISCONNECT",
        key="DISCONNECT",
        trading_day="20260212",
        summary_lines=["error_type=RuntimeError"],
        suggestions=["journalctl -u hk-tick-collector -n 120 --no-pager"],
    )
    assert re.match(r"^sid-[0-9a-f]{8}$", snapshot.sid)
    assert re.match(r"^eid-[0-9a-f]{8}$", event.eid)


def test_renderer_health_ok_template_contains_sid_and_required_order():
    state_machine = AlertStateMachine(drift_warn_sec=120)
    snapshot = _make_snapshot(created_at=datetime(2026, 2, 12, 2, 0, tzinfo=timezone.utc))
    assessment = state_machine.assess_health(snapshot)
    renderer = MessageRenderer(parse_mode="HTML")
    rendered = renderer.render_health(
        snapshot=snapshot,
        assessment=assessment,
        hostname="collector-a",
        instance_id="node-1",
        include_system_metrics=True,
    )

    lines = rendered.text.splitlines()
    assert lines[0].startswith("<b>🟢 HK Tick Collector 正常</b>")
    assert lines[1].startswith("結論：")
    assert lines[2].startswith("指標：")
    assert lines[3].startswith("進度：")
    assert "persisted=" in lines[2]
    assert "symbols=" in lines[2]
    assert "write_eff=" in lines[3]
    assert "top5_stale=" in lines[3]
    assert "主機：" in rendered.text
    assert f"sid={snapshot.sid}" in rendered.text


def test_renderer_after_hours_uses_since_close_not_large_drift_seconds():
    state_machine = AlertStateMachine(drift_warn_sec=120)
    after_hours = datetime(2026, 2, 12, 12, 30, tzinfo=timezone.utc)
    snapshot = _make_snapshot(
        created_at=after_hours,
        persisted_per_min=0,
        drift_sec=24000.0,
        queue_size=0,
    )
    assessment = state_machine.assess_health(snapshot)
    assert assessment.severity == NotifySeverity.OK
    renderer = MessageRenderer(parse_mode="HTML")
    digest = _DailyDigestState(
        trading_day="20260212",
        start_db_rows=120000,
        db_rows=123456,
        db_path="/data/sqlite/HK/20260212.db",
    )
    rendered = renderer.render_health(
        snapshot=snapshot,
        assessment=assessment,
        hostname="collector-a",
        instance_id="node-1",
        include_system_metrics=True,
        digest=digest,
    )

    assert "距收盤=" in rendered.text
    assert "db_growth_today=" in rendered.text
    assert "延遲=24000.0s" not in rendered.text


def test_renderer_open_progress_rollup_for_1000_symbols_uses_top5_only():
    state_machine = AlertStateMachine(drift_warn_sec=120)
    created_at = datetime(2026, 2, 12, 2, 5, tzinfo=timezone.utc)
    symbols = [
        SymbolSnapshot(
            symbol=f"HK.{idx:05d}",
            last_tick_age_sec=float(idx + 1),
            last_persisted_seq=idx,
            max_seq_lag=idx % 3,
        )
        for idx in range(1000)
    ]
    snapshot = _make_snapshot(created_at=created_at, symbols=symbols)
    assessment = state_machine.assess_health(snapshot)
    renderer = MessageRenderer(parse_mode="HTML")
    rendered = renderer.render_health(
        snapshot=snapshot,
        assessment=assessment,
        hostname="collector-a",
        instance_id="node-1",
        include_system_metrics=True,
    )

    assert "symbols=1000" in rendered.text
    assert "stale_bucket(&gt;=10s/&gt;=30s/&gt;=60s)=" in rendered.text
    assert "top5_stale=HK.00999(1000.0s)" in rendered.text
    assert "HK.00995(996.0s)" in rendered.text
    assert rendered.text.count("HK.0099") <= 5


def test_renderer_warn_and_alert_template_suggestion_limit_and_ids():
    renderer = MessageRenderer(parse_mode="HTML")
    warn_event = AlertEvent(
        created_at=datetime.now(tz=timezone.utc),
        code="SQLITE_BUSY",
        key="SQLITE_BUSY",
        fingerprint="SQLITE_BUSY",
        trading_day="20260212",
        severity=NotifySeverity.WARN.value,
        summary_lines=["busy_backoff_delta=8/min", "queue=40/1000"],
        suggestions=[
            "journalctl -u hk-tick-collector -n 120 --no-pager",
            "sqlite3 /data/sqlite/HK/20260212.db 'select count(*) from ticks;'",
        ],
        sid="sid-1234abcd",
    )
    warn_rendered = renderer.render_alert(
        event=warn_event,
        hostname="collector-a",
        instance_id="node-1",
        market_mode="open",
    )
    assert "<b>🟡 注意</b>" in warn_rendered.text
    assert warn_rendered.text.count("建議：") == 1

    alert_event = AlertEvent(
        created_at=datetime.now(tz=timezone.utc),
        code="PERSIST_STALL",
        key="PERSIST_STALL",
        fingerprint="PERSIST_STALL",
        trading_day="20260212",
        severity=NotifySeverity.ALERT.value,
        summary_lines=["write=0/min", "queue=420/1000", "lag=128"],
        suggestions=[
            "journalctl -u hk-tick-collector -n 200 --no-pager",
            "sqlite3 /data/sqlite/HK/20260212.db 'select count(*), max(ts_ms) from ticks;'",
            "tail -f /tmp/ignored.log",
        ],
        sid="sid-5678dcba",
    )
    alert_rendered = renderer.render_alert(
        event=alert_event,
        hostname="collector-a",
        instance_id="node-1",
        market_mode="open",
    )
    assert "<b>🔴 異常</b>" in alert_rendered.text
    assert "建議1：" in alert_rendered.text
    assert "建議2：" in alert_rendered.text
    assert "tail -f /tmp/ignored.log" not in alert_rendered.text
    assert f"eid={alert_event.eid}" in alert_rendered.text
    assert "sid=sid-5678dcba" in alert_rendered.text


def test_renderer_recovered_and_daily_digest_templates():
    renderer = MessageRenderer(parse_mode="HTML")
    snapshot = _make_snapshot()
    recovered_event = AlertEvent(
        created_at=datetime.now(tz=timezone.utc),
        code="DISCONNECT",
        key="DISCONNECT",
        fingerprint="DISCONNECT",
        trading_day="20260212",
        severity=NotifySeverity.OK.value,
        summary_lines=["status=reconnected", "queue=0/1000"],
        suggestions=[],
        sid=snapshot.sid,
    )
    recovered = renderer.render_recovered(
        event=recovered_event,
        hostname="collector-a",
        instance_id="node-1",
    )
    assert "<b>✅ 已恢復</b>" in recovered.text
    assert f"eid={recovered_event.eid}" in recovered.text
    assert f"sid={snapshot.sid}" in recovered.text

    digest = _DailyDigestState(
        trading_day="20260212",
        start_db_rows=1000000,
        total_rows=1800000,
        peak_rows_per_min=38000,
        max_lag_sec=3.6,
        alert_count=4,
        recovered_count=3,
        db_rows=22000000,
        db_path="/data/sqlite/HK/20260212.db",
    )
    digest_rendered = renderer.render_daily_digest(
        snapshot=snapshot,
        digest=digest,
        hostname="collector-a",
        instance_id="node-1",
    )
    assert "<b>📊 日報</b>" in digest_rendered.text
    assert "今日總量=1800000" in digest_rendered.text
    assert "告警次數=4" in digest_rendered.text
    assert "恢復次數=3" in digest_rendered.text


def test_truncate_message_respects_limit():
    renderer = MessageRenderer(parse_mode="HTML")
    event = AlertEvent(
        created_at=datetime.now(tz=timezone.utc),
        code="PERSIST_STALL",
        key="PERSIST_STALL",
        fingerprint="PERSIST_STALL",
        trading_day="20260212",
        severity=NotifySeverity.ALERT.value,
        summary_lines=[f"line_{i}=" + ("x" * 100) for i in range(30)],
        suggestions=["journalctl -u hk-tick-collector -n 200 --no-pager"],
        sid="sid-1234abcd",
    )
    rendered = renderer.render_alert(
        event=event,
        hostname="collector-a",
        instance_id="node-1",
        market_mode="open",
    )
    clipped = truncate_rendered_message(rendered, max_chars=600)
    assert len(clipped.text) <= 600


def test_dedupe_store_cooldown_and_escalation_follows_cadence():
    store = DedupeStore()
    fp = "PERSIST_STALL:20260212"
    should, reason = store.evaluate(
        fingerprint=fp,
        severity=NotifySeverity.ALERT,
        now=0.0,
        cooldown_sec=ALERT_CADENCE_SEC,
        escalation_steps=[0, 60, ALERT_CADENCE_SEC],
    )
    assert should is True
    assert reason == "new"

    should, reason = store.evaluate(
        fingerprint=fp,
        severity=NotifySeverity.ALERT,
        now=60.0,
        cooldown_sec=ALERT_CADENCE_SEC,
        escalation_steps=[0, 60, ALERT_CADENCE_SEC],
    )
    assert should is False
    assert reason == "cooldown_active"

    should, reason = store.evaluate(
        fingerprint=fp,
        severity=NotifySeverity.ALERT,
        now=181.0,
        cooldown_sec=ALERT_CADENCE_SEC,
        escalation_steps=[0, 60, ALERT_CADENCE_SEC],
    )
    assert should is True
    assert reason in {"escalation_step_60s", "escalation_step_180s", "cooldown_elapsed"}


def test_notifier_health_and_alert_cadence_with_recovered():
    async def runner() -> None:
        calls = []

        def fake_sender(payload):
            calls.append(dict(payload))
            return TelegramSendResult(ok=True, status_code=200)

        monotonic_now = {"value": 0.0}

        def fake_now() -> float:
            return monotonic_now["value"]

        async def fake_sleep(_: float) -> None:
            return

        notifier = TelegramNotifier(
            enabled=True,
            bot_token="1234567890:ABCDEF",
            chat_id="-100123",
            parse_mode="HTML",
            alert_cooldown_sec=600,
            alert_escalation_steps=[0, 600, 1800],
            sender=fake_sender,
            now_monotonic=fake_now,
            sleep=fake_sleep,
        )
        await notifier.start()
        open_time = datetime(2026, 2, 12, 2, 0, tzinfo=timezone.utc)

        # bootstrap OK
        notifier.submit_health(
            _make_snapshot(
                created_at=open_time,
                persisted_per_min=800,
                drift_sec=1.0,
                queue_size=1,
            )
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        assert len(calls) == 1

        # same OK should be suppressed
        monotonic_now["value"] += 30
        notifier.submit_health(
            _make_snapshot(
                created_at=open_time,
                persisted_per_min=820,
                drift_sec=1.0,
                queue_size=1,
            )
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        assert len(calls) == 1

        # open heartbeat cadence should emit every 10 minutes
        monotonic_now["value"] += OPEN_CADENCE_SEC
        notifier.submit_health(
            _make_snapshot(
                created_at=open_time,
                persisted_per_min=830,
                drift_sec=1.5,
                queue_size=1,
            )
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        assert len(calls) == 2
        assert "persisted=" in calls[-1]["text"]

        # WARN should send immediately on state change
        monotonic_now["value"] += 5
        notifier.submit_health(
            _make_snapshot(
                created_at=open_time,
                persisted_per_min=0,
                drift_sec=180.0,
                queue_size=200,
            )
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        assert len(calls) == 3
        assert "🟡 HK Tick Collector 注意" in calls[-1]["text"]

        # WARN cadence: <10m suppressed
        monotonic_now["value"] += WARN_CADENCE_SEC - 5
        notifier.submit_health(
            _make_snapshot(
                created_at=open_time,
                persisted_per_min=0,
                drift_sec=180.0,
                queue_size=180,
            )
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        assert len(calls) == 3

        # WARN cadence: >=10m allowed
        monotonic_now["value"] += 10
        notifier.submit_health(
            _make_snapshot(
                created_at=open_time,
                persisted_per_min=0,
                drift_sec=180.0,
                queue_size=180,
            )
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        assert len(calls) == 4

        # ALERT event cadence + recovered
        event = AlertEvent(
            created_at=datetime.now(tz=timezone.utc),
            code="DISCONNECT",
            key="DISCONNECT",
            fingerprint="DISCONNECT",
            trading_day="20260212",
            severity=NotifySeverity.ALERT.value,
            summary_lines=["error_type=ConnectionError"],
            suggestions=["journalctl -u hk-tick-collector -n 120 --no-pager"],
            sid="sid-aaaaaaaa",
        )
        monotonic_now["value"] += 1
        notifier.submit_alert(event)
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        assert len(calls) == 5
        assert "🔴 異常" in calls[-1]["text"]

        # <3m suppressed
        monotonic_now["value"] += ALERT_CADENCE_SEC - 20
        notifier.submit_alert(event)
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        assert len(calls) == 5

        # >=3m allowed
        monotonic_now["value"] += 25
        notifier.submit_alert(event)
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        assert len(calls) == 6

        notifier.resolve_alert(
            code="DISCONNECT",
            fingerprint="DISCONNECT",
            trading_day="20260212",
            summary_lines=["status=reconnected", "queue=0/1000"],
            sid="sid-aaaaaaaa",
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        assert len(calls) == 7
        assert "✅ 已恢復" in calls[-1]["text"]

        await notifier.stop()

    asyncio.run(runner())


def test_notifier_after_hours_ok_cadence_and_mode_transition():
    async def runner() -> None:
        calls = []

        def fake_sender(payload):
            calls.append(dict(payload))
            return TelegramSendResult(ok=True, status_code=200)

        monotonic_now = {"value": 0.0}

        def fake_now() -> float:
            return monotonic_now["value"]

        async def fake_sleep(_: float) -> None:
            return

        notifier = TelegramNotifier(
            enabled=True,
            bot_token="1234567890:ABCDEF",
            chat_id="-100123",
            parse_mode="HTML",
            sender=fake_sender,
            now_monotonic=fake_now,
            sleep=fake_sleep,
        )
        await notifier.start()

        open_time = datetime(2026, 2, 12, 2, 0, tzinfo=timezone.utc)
        after_hours = datetime(2026, 2, 12, 12, 30, tzinfo=timezone.utc)

        notifier.submit_health(
            _make_snapshot(created_at=open_time, persisted_per_min=900, drift_sec=1.0)
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        assert len(calls) == 1

        # mode changes to after-hours: emit immediately
        monotonic_now["value"] += 60
        notifier.submit_health(
            _make_snapshot(
                created_at=after_hours, persisted_per_min=0, drift_sec=24000.0, queue_size=0
            )
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        assert len(calls) == 3  # HEALTH + DAILY_DIGEST
        assert "距收盤=" in calls[-2]["text"]

        # after-hours cadence not reached yet
        monotonic_now["value"] += AFTER_HOURS_CADENCE_SEC - 1
        notifier.submit_health(
            _make_snapshot(
                created_at=after_hours, persisted_per_min=0, drift_sec=24500.0, queue_size=0
            )
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        assert len(calls) == 3

        # after-hours cadence reached
        monotonic_now["value"] += 2
        notifier.submit_health(
            _make_snapshot(
                created_at=after_hours, persisted_per_min=0, drift_sec=25000.0, queue_size=0
            )
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        assert len(calls) == 4
        assert "距收盤=" in calls[-1]["text"]

        await notifier.stop()

    asyncio.run(runner())


def test_state_machine_pre_open_and_after_hours_not_warn_for_large_drift():
    sm = AlertStateMachine(drift_warn_sec=120)
    pre_open = datetime(2026, 2, 12, 1, 20, tzinfo=timezone.utc)
    after_hours = datetime(2026, 2, 12, 12, 30, tzinfo=timezone.utc)

    pre_open_assessment = sm.assess_health(
        _make_snapshot(created_at=pre_open, persisted_per_min=0, drift_sec=9999.0, queue_size=0)
    )
    after_hours_assessment = sm.assess_health(
        _make_snapshot(created_at=after_hours, persisted_per_min=0, drift_sec=24000.0, queue_size=0)
    )

    assert pre_open_assessment.severity == NotifySeverity.OK
    assert after_hours_assessment.severity == NotifySeverity.OK


def test_state_machine_switches_to_holiday_closed_and_back_to_open():
    sm = AlertStateMachine(drift_warn_sec=120)
    open_time = datetime(2026, 2, 12, 2, 30, tzinfo=timezone.utc)
    stale_symbols = [
        SymbolSnapshot(
            symbol=f"HK.{idx:05d}",
            last_tick_age_sec=1200.0 + float(idx),
            last_persisted_seq=idx,
            max_seq_lag=0,
        )
        for idx in range(50)
    ]
    for _ in range(2):
        assessment = sm.assess_health(
            _make_snapshot(
                created_at=open_time,
                persisted_per_min=0,
                push_rows_per_min=0,
                poll_accepted=0,
                queue_size=0,
                drift_sec=10.0,
                symbols=stale_symbols,
            )
        )
        assert assessment.market_mode == "open"

    holiday_assessment = sm.assess_health(
        _make_snapshot(
            created_at=open_time,
            persisted_per_min=0,
            push_rows_per_min=0,
            poll_accepted=0,
            queue_size=0,
            drift_sec=10.0,
            symbols=stale_symbols,
        )
    )
    assert holiday_assessment.market_mode == "holiday-closed"
    assert holiday_assessment.severity == NotifySeverity.OK

    recovered_assessment = sm.assess_health(
        _make_snapshot(
            created_at=open_time,
            persisted_per_min=500,
            push_rows_per_min=700,
            poll_accepted=100,
            queue_size=1,
            drift_sec=1.0,
            symbols=stale_symbols,
        )
    )
    assert recovered_assessment.market_mode == "open"


def test_preopen_open_afterhours_constants():
    assert PREOPEN_CADENCE_SEC == 1800
    assert OPEN_CADENCE_SEC == 600
    assert AFTER_HOURS_CADENCE_SEC == 3600


def test_notifier_start_logs_notify_schema(caplog):
    async def runner() -> None:
        caplog.set_level("INFO")

        def fake_sender(_):
            return TelegramSendResult(ok=True, status_code=200)

        notifier = TelegramNotifier(
            enabled=True,
            bot_token="1234567890:ABCDEF",
            chat_id="-100123",
            parse_mode="HTML",
            sender=fake_sender,
        )
        await notifier.start()
        await notifier.stop()

    asyncio.run(runner())
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert f"notify_schema={NOTIFY_SCHEMA_VERSION}" in joined


def test_notifier_market_mode_change_to_holiday_closed_emits_immediately():
    async def runner() -> None:
        calls = []

        def fake_sender(payload):
            calls.append(dict(payload))
            return TelegramSendResult(ok=True, status_code=200)

        monotonic_now = {"value": 0.0}

        def fake_now() -> float:
            return monotonic_now["value"]

        async def fake_sleep(_: float) -> None:
            return

        notifier = TelegramNotifier(
            enabled=True,
            bot_token="1234567890:ABCDEF",
            chat_id="-100123",
            parse_mode="HTML",
            sender=fake_sender,
            now_monotonic=fake_now,
            sleep=fake_sleep,
        )
        await notifier.start()
        open_time = datetime(2026, 2, 12, 2, 30, tzinfo=timezone.utc)
        stale_symbols = [
            SymbolSnapshot(
                symbol=f"HK.{idx:05d}",
                last_tick_age_sec=1200.0 + float(idx),
                last_persisted_seq=idx,
                max_seq_lag=0,
            )
            for idx in range(30)
        ]

        # bootstrap
        notifier.submit_health(
            _make_snapshot(
                created_at=open_time,
                persisted_per_min=0,
                push_rows_per_min=0,
                poll_accepted=0,
                queue_size=0,
                drift_sec=10.0,
                symbols=stale_symbols,
            )
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        assert len(calls) == 1
        assert "狀態=盤中" in calls[-1]["text"]

        # still open mode candidate; suppressed by cadence
        monotonic_now["value"] += 60
        notifier.submit_health(
            _make_snapshot(
                created_at=open_time,
                persisted_per_min=0,
                push_rows_per_min=0,
                poll_accepted=0,
                queue_size=0,
                drift_sec=10.0,
                symbols=stale_symbols,
            )
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        assert len(calls) == 1

        # mode changes to holiday-closed: should emit immediately
        monotonic_now["value"] += 60
        notifier.submit_health(
            _make_snapshot(
                created_at=open_time,
                persisted_per_min=0,
                push_rows_per_min=0,
                poll_accepted=0,
                queue_size=0,
                drift_sec=10.0,
                symbols=stale_symbols,
            )
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        assert len(calls) == 2
        assert "狀態=休市日" in calls[-1]["text"]

        # traffic returns, mode switches back to open and emits immediately
        monotonic_now["value"] += 30
        notifier.submit_health(
            _make_snapshot(
                created_at=open_time,
                persisted_per_min=500,
                push_rows_per_min=700,
                poll_accepted=100,
                queue_size=1,
                drift_sec=1.0,
                symbols=stale_symbols,
            )
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        assert len(calls) == 3
        assert "狀態=盤中" in calls[-1]["text"]

        await notifier.stop()

    asyncio.run(runner())


def test_retry_after_respected_and_eventually_succeeds():
    async def runner() -> None:
        calls = []
        responses = deque(
            [
                TelegramSendResult(
                    ok=False,
                    status_code=429,
                    retry_after=2,
                    error="Too Many Requests",
                ),
                TelegramSendResult(ok=True, status_code=200),
            ]
        )

        def fake_sender(payload):
            calls.append(dict(payload))
            return responses.popleft()

        sleeps = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        notifier = TelegramNotifier(
            enabled=True,
            bot_token="1234567890:ABCDEF",
            chat_id="-100123",
            parse_mode="HTML",
            alert_cooldown_sec=600,
            alert_escalation_steps=[0],
            sender=fake_sender,
            sleep=fake_sleep,
        )
        await notifier.start()
        notifier.submit_alert(
            AlertEvent(
                created_at=datetime.now(tz=timezone.utc),
                code="SQLITE_BUSY",
                key="SQLITE_BUSY",
                fingerprint="SQLITE_BUSY",
                trading_day="20260212",
                severity=NotifySeverity.ALERT.value,
                summary_lines=["busy_backoff_delta=5/min threshold=3"],
                suggestions=["journalctl -u hk-tick-collector -n 200 --no-pager"],
            )
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        await notifier.stop()

        assert len(calls) == 2
        assert any(item == 2 for item in sleeps)

    asyncio.run(runner())
