import asyncio
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from hk_tick_collector.notifiers.telegram import (
    AlertEvent,
    HealthSnapshot,
    SlidingWindowRateLimiter,
    SymbolSnapshot,
    TelegramNotifier,
    TelegramSendResult,
    format_health_digest,
    truncate_message,
)


def _make_snapshot(symbol_count: int = 2) -> HealthSnapshot:
    symbols = []
    for index in range(symbol_count):
        symbols.append(
            SymbolSnapshot(
                symbol=f"HK.{700 + index:05d}",
                last_tick_age_sec=5.0 + index,
                last_persisted_seq=100 + index,
                max_seq_lag=index,
            )
        )
    return HealthSnapshot(
        created_at=datetime.now(tz=timezone.utc),
        pid=1234,
        uptime_sec=3661,
        trading_day="20260212",
        db_path=Path("/data/sqlite/HK/20260212.db"),
        db_rows=123456,
        db_max_ts_utc="2026-02-12T09:30:00+00:00",
        drift_sec=1.2,
        queue_size=10,
        queue_maxsize=1000,
        push_rows_per_min=1000,
        poll_fetched=200,
        poll_accepted=180,
        persisted_rows_per_min=950,
        dropped_duplicate=20,
        symbols=symbols,
        system_load1=0.12,
        system_rss_mb=88.6,
        system_disk_free_gb=123.4,
    )


def test_health_formatter_has_required_fields_and_line_budget():
    text = format_health_digest(
        _make_snapshot(symbol_count=12),
        hostname="collector-a",
        instance_id="node-1",
        include_system_metrics=True,
    )
    assert "📈 HK Tick Collector · HEALTH" in text
    assert "host=collector-a instance=node-1" in text
    assert "db=/data/sqlite/HK/20260212.db rows=123456" in text
    assert "queue=10/1000" in text
    assert "symbols:" in text
    assert len(text.splitlines()) <= 15

    truncated = truncate_message("x" * 5000)
    assert len(truncated) <= 4096
    assert truncated.endswith("...(truncated)")


def test_rate_limiter_enforces_window_cap():
    clock = {"now": 0.0}
    limiter = SlidingWindowRateLimiter(3, window_sec=60, now_fn=lambda: clock["now"])

    assert limiter.reserve_delay() == 0.0
    assert limiter.reserve_delay() == 0.0
    assert limiter.reserve_delay() == 0.0
    assert limiter.reserve_delay() == 60.0

    clock["now"] = 30.0
    assert limiter.reserve_delay() == 30.0

    clock["now"] = 60.1
    assert limiter.reserve_delay() == 0.0


def test_alert_cooldown_dedup():
    clock = {"now": 0.0}
    notifier = TelegramNotifier(
        enabled=True,
        bot_token="1234567890:ABCDEF",
        chat_id="-100123",
        alert_cooldown_sec=600,
        now_monotonic=lambda: clock["now"],
        sender=lambda payload: TelegramSendResult(ok=True, status_code=200),
    )

    event = AlertEvent(
        created_at=datetime.now(tz=timezone.utc),
        code="PERSIST_STALL",
        key="PERSIST_STALL:20260212:HK.00700",
        trading_day="20260212",
        summary_lines=["stall_sec=200/180"],
        suggestions=["journalctl -u hk-tick-collector -n 200 --no-pager"],
    )
    notifier.submit_alert(event)
    notifier.submit_alert(event)
    assert notifier._queue.qsize() == 1

    clock["now"] = 601.0
    notifier.submit_alert(event)
    assert notifier._queue.qsize() == 2


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
            max_retries=3,
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
                summary_lines=["busy_backoff_delta=5/min threshold=3"],
                suggestions=["journalctl -u hk-tick-collector -n 200 --no-pager"],
            )
        )
        await asyncio.wait_for(notifier._queue.join(), timeout=1)
        await notifier.stop()

        assert len(calls) == 2
        assert any(item == 2 for item in sleeps)

    asyncio.run(runner())
