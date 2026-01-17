from __future__ import annotations

import asyncio
import logging
import signal

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
    collector = AsyncTickCollector(
        store,
        batch_size=config.batch_size,
        max_wait_ms=config.max_wait_ms,
        max_queue_size=config.max_queue_size,
    )
    await collector.start()

    loop = asyncio.get_running_loop()
    client = FutuQuoteClient(config, collector, loop)
    client_task = asyncio.create_task(client.run_forever())

    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    await stop_event.wait()
    logger.info("shutdown signal received")

    client_task.cancel()
    try:
        await client_task
    except asyncio.CancelledError:
        pass

    await collector.stop()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
