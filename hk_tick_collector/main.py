from __future__ import annotations

import asyncio
import faulthandler
import logging
import signal
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from .collector import AsyncTickCollector
from .config import Config
from .db import SQLiteTickStore
from .futu_client import FutuQuoteClient
from .logging_config import setup_logging

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

    store = SQLiteTickStore(
        config.data_root,
        busy_timeout_ms=config.sqlite_busy_timeout_ms,
        journal_mode=config.sqlite_journal_mode,
        synchronous=config.sqlite_synchronous,
        wal_autocheckpoint=config.sqlite_wal_autocheckpoint,
    )
    trading_day = datetime.now(tz=HK_TZ).strftime("%Y%m%d")
    await asyncio.to_thread(store.ensure_db, trading_day)
    seed_days = [trading_day]
    recent_days = await asyncio.to_thread(store.list_recent_trading_days, config.seed_recent_db_days)
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
        logger.info("seed_last_seq seed_days=%s values=%s", ",".join(seed_days), initial_last_seq)
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
    client = FutuQuoteClient(config, collector, loop, initial_last_seq=initial_last_seq)
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
        fatal_error = collector.fatal_error() or RuntimeError("persist loop exited unexpectedly")
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
        raise RuntimeError("collector terminated because of fatal background failure") from fatal_error


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
