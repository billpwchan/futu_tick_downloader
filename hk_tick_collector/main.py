from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime

from .collector import AsyncTickCollector
from .config import Config
from .db import SQLiteTickStore
from .futu_client import FutuQuoteClient
from .logging_config import setup_logging

logger = logging.getLogger(__name__)


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stop_event.set())


async def run() -> None:
    config = Config.from_env()
    setup_logging(config.log_level)

    store = SQLiteTickStore(config.data_root)
    trading_day = datetime.now().strftime("%Y%m%d")
    initial_last_seq = await asyncio.to_thread(
        store.fetch_max_seq_by_symbol,
        trading_day,
        config.symbols,
    )
    if initial_last_seq:
        logger.info("seed_last_seq trading_day=%s values=%s", trading_day, initial_last_seq)
    else:
        logger.info("seed_last_seq trading_day=%s values=none", trading_day)

    collector = AsyncTickCollector(
        store,
        batch_size=config.batch_size,
        max_wait_ms=config.max_wait_ms,
        max_queue_size=config.max_queue_size,
    )
    await collector.start()

    loop = asyncio.get_running_loop()
    client = FutuQuoteClient(config, collector, loop, initial_last_seq=initial_last_seq)
    client_task = asyncio.create_task(client.run_forever())

    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    await stop_event.wait()
    logger.info("shutdown signal received")

    await client.stop()
    try:
        await asyncio.wait_for(client_task, timeout=12)
    except asyncio.TimeoutError:
        logger.warning("client shutdown timeout, cancelling")
        client_task.cancel()
        await asyncio.gather(client_task, return_exceptions=True)

    try:
        await asyncio.wait_for(collector.stop(), timeout=12)
    except asyncio.TimeoutError:
        logger.warning("collector shutdown timeout")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
