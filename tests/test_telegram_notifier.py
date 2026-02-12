import asyncio
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from hk_tick_collector.notify.telegram import (
    AlertEvent,
    AlertStateMachine,
    DedupeStore,
    HealthSnapshot,
    MessageRenderer,
    NotifySeverity,
    SymbolSnapshot,
    TelegramNotifier,
    TelegramSendResult,
    truncate_rendered_message,
)


def _make_snapshot(
    *,
    persisted_per_min: int = 800,
    drift_sec: float | None = 1.0,
    queue_size: int = 5,
    symbol_lag: int = 0,
) -> HealthSnapshot:
    return HealthSnapshot(
        created_at=datetime.now(tz=timezone.utc),
        pid=1234,
        uptime_sec=3661,
        trading_day="20260212",
        db_path=Path("/data/sqlite/HK/20260212.db"),
        db_rows=123456,
        db_max_ts_utc="2026-02-12T09:30:00+00:00",
        drift_sec=drift_sec,
        queue_size=queue_size,
        queue_maxsize=1000,
        push_rows_per_min=1000,
        poll_fetched=200,
        poll_accepted=180,
        persisted_rows_per_min=persisted_per_min,
        dropped_duplicate=20,
        symbols=[
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
        ],
        system_load1=0.12,
        system_rss_mb=88.6,
        system_disk_free_gb=123.4,
    )


def test_renderer_outputs_expandable_blockquote_html():
    state_machine = AlertStateMachine(drift_warn_sec=120)
    snapshot = _make_snapshot()
    assessment = state_machine.assess_health(snapshot)

    renderer = MessageRenderer(parse_mode="HTML")
    rendered = renderer.render_health(
        snapshot=snapshot,
        assessment=assessment,
        hostname="collector-a",
        instance_id="node-1",
        include_system_metrics=True,
    )

    assert rendered.parse_mode == "HTML"
    assert "<blockquote expandable>" in rendered.text
    assert "</blockquote>" in rendered.text

    first_layer = rendered.text.split("<blockquote expandable>", 1)[0].strip()
    assert first_layer.count("\n") + 1 <= 10
    assert "結論：" in first_layer
    assert "影響：" in first_layer


def test_truncate_preserves_expandable_blockquote_structure():
    renderer = MessageRenderer(parse_mode="HTML")
    event = AlertEvent(
        created_at=datetime.now(tz=timezone.utc),
        code="PERSIST_STALL",
        key="PERSIST_STALL:20260212",
        fingerprint="PERSIST_STALL:20260212",
        trading_day="20260212",
        severity=NotifySeverity.ALERT.value,
        summary_lines=[f"line_{i}=" + ("x" * 100) for i in range(30)],
        suggestions=["journalctl -u hk-tick-collector -n 200 --no-pager"],
    )
    rendered = renderer.render_alert(
        event=event,
        hostname="collector-a",
        instance_id="node-1",
        market_mode="open",
    )
    clipped = truncate_rendered_message(rendered, max_chars=600)

    assert len(clipped.text) <= 600
    assert "<blockquote expandable>" in clipped.text
    assert "</blockquote>" in clipped.text
    assert "[truncated]" in clipped.text


def test_state_machine_transitions_ok_warn_alert():
    sm = AlertStateMachine(drift_warn_sec=120)

    ok = sm.assess_health(_make_snapshot(persisted_per_min=900, drift_sec=2.0, queue_size=3))
    warn = sm.assess_health(_make_snapshot(persisted_per_min=200, drift_sec=180.0, queue_size=50))
    alert = sm.assess_health(
        _make_snapshot(persisted_per_min=0, drift_sec=240.0, queue_size=120, symbol_lag=50)
    )

    assert ok.severity == NotifySeverity.OK
    assert warn.severity == NotifySeverity.WARN
    assert alert.severity == NotifySeverity.ALERT


def test_dedupe_store_cooldown_and_escalation():
    store = DedupeStore()
    fp = "PERSIST_STALL:20260212:HK.00700"

    should, reason = store.evaluate(
        fingerprint=fp,
        severity=NotifySeverity.ALERT,
        now=0.0,
        cooldown_sec=600,
        escalation_steps=[0, 60, 300],
    )
    assert should is True
    assert reason == "new"

    should, reason = store.evaluate(
        fingerprint=fp,
        severity=NotifySeverity.ALERT,
        now=30.0,
        cooldown_sec=600,
        escalation_steps=[0, 60, 300],
    )
    assert should is False
    assert reason == "cooldown_active"

    should, reason = store.evaluate(
        fingerprint=fp,
        severity=NotifySeverity.ALERT,
        now=61.0,
        cooldown_sec=600,
        escalation_steps=[0, 60, 300],
    )
    assert should is True
    assert reason.startswith("escalation_step_")

    should, reason = store.evaluate(
        fingerprint=fp,
        severity=NotifySeverity.ALERT,
        now=700.0,
        cooldown_sec=600,
        escalation_steps=[0, 60, 300],
    )
    assert should is True
    assert reason in {"escalation_step_300s", "cooldown_elapsed"}


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
                key="SQLITE_BUSY:20260212",
                trading_day="20260212",
                severity=NotifySeverity.WARN.value,
                summary_lines=["busy_backoff_delta=5/min threshold=3"],
                suggestions=["journalctl -u hk-tick-collector -n 200 --no-pager"],
            )
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        await notifier.stop()

        assert len(calls) == 2
        assert any(item == 2 for item in sleeps)

    asyncio.run(runner())
