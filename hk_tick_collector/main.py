from __future__ import annotations

import asyncio
import faulthandler
import logging
import signal
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .collector import AsyncTickCollector
from .config import Config
from .db import SQLiteTickStore
from .futu_client import FutuQuoteClient
from .logging_config import setup_logging
from .notifiers.telegram import AlertEvent, NotifySeverity, TelegramNotifier
from .quality.config import QualityConfig
from .quality.gap_detector import GapDetector

logger = logging.getLogger(__name__)
HK_TZ = ZoneInfo("Asia/Hong_Kong")


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stop_event.set())


def _install_fault_diagnostics() -> None:
    # Keep crash diagnostics in journald/stderr and allow on-demand thread dumps via SIGUSR1.
    faulthandler.enable(file=sys.stderr, all_threads=True)
    try:
        faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True, chain=True)
    except (AttributeError, OSError, RuntimeError, ValueError):
        logger.warning("faulthandler_sigusr1_register_failed")


async def run() -> None:
    config = Config.from_env()
    setup_logging(config.log_level)
    _install_fault_diagnostics()
    notifier: TelegramNotifier | None = None
    if config.telegram_enabled:
        try:
            notifier = TelegramNotifier(
                enabled=config.telegram_enabled,
                bot_token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
                thread_id=config.telegram_thread_id,
                thread_health_id=config.telegram_thread_health_id,
                thread_ops_id=config.telegram_thread_ops_id,
                parse_mode=config.telegram_parse_mode,
                default_render_mode=config.telegram_mode_default,
                health_interval_sec=config.telegram_health_interval_sec,
                health_trading_interval_sec=config.telegram_health_trading_interval_sec,
                health_offhours_interval_sec=config.telegram_health_offhours_interval_sec,
                health_lunch_once=config.telegram_health_lunch_once,
                health_after_close_once=config.telegram_health_after_close_once,
                health_holiday_mode=config.telegram_health_holiday_mode,
                alert_cooldown_sec=config.telegram_alert_cooldown_sec,
                alert_escalation_steps=config.telegram_alert_escalation_steps,
                rate_limit_per_min=config.telegram_rate_limit_per_min,
                include_system_metrics=config.telegram_include_system_metrics,
                instance_id=config.instance_id,
                drift_warn_sec=config.drift_warn_sec,
                digest_queue_change_pct=config.telegram_digest_queue_change_pct,
                digest_last_tick_age_threshold_sec=config.telegram_digest_last_tick_age_threshold_sec,
                digest_drift_threshold_sec=config.telegram_digest_drift_threshold_sec,
                interactive_enabled=config.telegram_interactive_enabled,
                admin_user_ids=config.telegram_admin_user_ids,
                action_context_ttl_sec=config.telegram_action_context_ttl_sec,
                action_log_max_lines=config.telegram_action_log_max_lines,
                action_refresh_min_interval_sec=config.telegram_action_refresh_min_interval_sec,
                action_command_rate_limit_per_min=config.telegram_action_command_rate_limit_per_min,
                action_timeout_sec=config.telegram_action_timeout_sec,
                action_command_timeout_sec=config.telegram_action_command_timeout_sec,
                action_command_allowlist=config.telegram_action_command_allowlist,
                action_command_max_lookback_days=config.telegram_action_command_max_lookback_days,
            )
            await notifier.start()
        except Exception:
            notifier = None
            logger.exception("telegram_notifier_init_failed")

    try:
        quality_config = QualityConfig.from_env()
        gap_detector = GapDetector(quality_config) if quality_config.gap_enabled else None
        store = SQLiteTickStore(
            config.data_root,
            busy_timeout_ms=config.sqlite_busy_timeout_ms,
            journal_mode=config.sqlite_journal_mode,
            synchronous=config.sqlite_synchronous,
            wal_autocheckpoint=config.sqlite_wal_autocheckpoint,
            gap_detector=gap_detector,
        )
        trading_day = datetime.now(tz=HK_TZ).strftime("%Y%m%d")
        seed_days = [trading_day]
        recent_days = await asyncio.to_thread(
            store.list_recent_trading_days, config.seed_recent_db_days
        )
        for day in recent_days:
            if day not in seed_days:
                seed_days.append(day)
        initial_last_seq = await asyncio.to_thread(
            store.fetch_max_seq_by_symbol_recent,
            config.symbols,
            seed_days,
            config.seed_recent_db_days,
        )
        if initial_last_seq:
            logger.info(
                "seed_last_seq seed_days=%s values=%s", ",".join(seed_days), initial_last_seq
            )
        else:
            logger.info("seed_last_seq seed_days=%s values=none", ",".join(seed_days))

        collector = AsyncTickCollector(
            store,
            batch_size=config.batch_size,
            max_wait_ms=config.max_wait_ms,
            max_queue_size=config.max_queue_size,
            persist_retry_max_attempts=config.persist_retry_max_attempts,
            persist_retry_backoff_sec=config.persist_retry_backoff_sec,
            persist_retry_backoff_max_sec=config.persist_retry_backoff_max_sec,
            heartbeat_interval_sec=config.persist_heartbeat_interval_sec,
        )
        await collector.start()

        loop = asyncio.get_running_loop()
        client = FutuQuoteClient(
            config,
            collector,
            loop,
            initial_last_seq=initial_last_seq,
            store=store,
            notifier=notifier,
        )
        collector.set_persist_observer(client.handle_persist_result)
        client_task = asyncio.create_task(client.run_forever())

        stop_event = asyncio.Event()
        _install_signal_handlers(stop_event)
        stop_wait_task = asyncio.create_task(stop_event.wait())
        collector_fatal_task = asyncio.create_task(collector.wait_fatal())
        tasks = {stop_wait_task, collector_fatal_task, client_task}

        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        aux_pending = [task for task in pending if task is not client_task]
        for task in aux_pending:
            task.cancel()
        for task in aux_pending:
            await asyncio.gather(task, return_exceptions=True)

        fatal_error: BaseException | None = None
        if collector_fatal_task in done and collector_fatal_task.exception() is None:
            fatal_error = collector.fatal_error() or RuntimeError(
                "persist loop exited unexpectedly"
            )
            logger.error("shutdown reason=collector_fatal err=%r", fatal_error)
        elif client_task in done and not stop_event.is_set():
            client_error = client_task.exception()
            if client_error is not None:
                fatal_error = client_error
                logger.error("shutdown reason=futu_client_fatal err=%r", client_error)
            else:
                fatal_error = RuntimeError("futu client task exited unexpectedly")
                logger.error("shutdown reason=futu_client_exit_without_error")
        else:
            logger.info("shutdown signal received")

        if fatal_error is not None and notifier is not None:
            notifier.submit_alert(
                AlertEvent(
                    created_at=datetime.now(tz=timezone.utc),
                    code="RESTART",
                    key="RESTART",
                    fingerprint="RESTART",
                    trading_day=datetime.now(tz=HK_TZ).strftime("%Y%m%d"),
                    severity=NotifySeverity.ALERT.value,
                    headline="異常：服務出現致命錯誤，systemd 可能觸發重啟",
                    impact="若反覆重啟，資料連續性可能受影響",
                    summary_lines=[
                        f"reason={type(fatal_error).__name__}",
                        "service_exit=unexpected",
                    ],
                    suggestions=[
                        "journalctl -u hk-tick-collector -n 200 --no-pager",
                        "systemctl status hk-tick-collector --no-pager",
                    ],
                )
            )

        await client.stop()
        try:
            await asyncio.wait_for(client_task, timeout=12)
        except asyncio.TimeoutError:
            logger.warning("client shutdown timeout, cancelling")
            client_task.cancel()
            await asyncio.gather(client_task, return_exceptions=True)

        try:
            await collector.stop(timeout_sec=config.stop_flush_timeout_sec, cancel_on_timeout=False)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("collector flush timed out during shutdown") from exc

        if fatal_error is not None:
            raise RuntimeError(
                "collector terminated because of fatal background failure"
            ) from fatal_error
    finally:
        if notifier is not None:
            await notifier.stop()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
