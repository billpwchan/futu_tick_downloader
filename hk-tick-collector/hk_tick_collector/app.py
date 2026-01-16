from __future__ import annotations

import asyncio
import logging
import os
import signal

from .config import config_summary, load_config
from .futu_client import FutuTickerClient
from .health import HealthServer
from .queue import TickPersistQueue
from .sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


async def main_async() -> None:
    config_path = os.getenv("HKTC_CONFIG_PATH", "config/collector.yaml")
    config = load_config(config_path)

    logging.basicConfig(
        level=getattr(logging, config.collector.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logger.info("Loaded config: %s", config_summary(config))

    store = SQLiteStore(
        data_dir=config.collector.data_dir,
        journal_mode=config.sqlite.journal_mode,
        synchronous=config.sqlite.synchronous,
        temp_store=config.sqlite.temp_store,
    )
    queue = TickPersistQueue(
        store=store,
        batch_size=config.collector.batch_size,
        max_wait_ms=config.collector.max_wait_ms,
    )

    loop = asyncio.get_running_loop()
    client = FutuTickerClient(
        opend=config.opend,
        reconnect=config.reconnect,
        backfill=config.backfill,
        queue=queue,
        loop=loop,
        market=config.collector.market,
    )

    health = None
    if config.health.enabled:
        health = HealthServer(config.health.host, config.health.port, queue, client)

    stop_event = asyncio.Event()

    def _handle_stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _handle_stop())

    await queue.start()
    await client.start()
    if health:
        await health.start()

    await stop_event.wait()

    logger.info("Shutdown initiated")
    if health:
        await health.stop()
    await client.stop()
    await queue.stop()
    await store.close()
    logger.info("Shutdown complete")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
