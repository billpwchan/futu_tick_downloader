from __future__ import annotations

import asyncio
import logging
from typing import List

from futu import OpenQuoteContext, RET_OK, Session, SubType, TickerHandlerBase

from .collector import AsyncTickCollector
from .config import Config
from .mapping import ticker_df_to_rows
from .utils import ExponentialBackoff

logger = logging.getLogger(__name__)


class FutuTickerHandler(TickerHandlerBase):
    def __init__(self, collector: AsyncTickCollector, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._collector = collector
        self._loop = loop

    def on_recv_rsp(self, rsp_pb):
        ret, data = super().on_recv_rsp(rsp_pb)
        if ret != RET_OK:
            logger.error("ticker push error: %s", data)
            return ret, data

        try:
            rows = ticker_df_to_rows(data, provider="futu", push_type="push")
        except Exception:
            logger.exception("failed to map ticker data")
            return ret, data

        if rows:
            self._loop.call_soon_threadsafe(self._collector.enqueue, rows)
        return ret, data


class FutuQuoteClient:
    def __init__(self, config: Config, collector: AsyncTickCollector, loop: asyncio.AbstractEventLoop) -> None:
        self._config = config
        self._collector = collector
        self._loop = loop
        self._ctx: OpenQuoteContext | None = None
        self._handler = FutuTickerHandler(collector, loop)

    async def run_forever(self) -> None:
        backoff = ExponentialBackoff(self._config.reconnect_min_delay, self._config.reconnect_max_delay)
        try:
            while True:
                try:
                    await self._connect_and_subscribe()
                    backoff.reset()
                    await self._monitor_connection()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("futu connection error: %s", exc)
                finally:
                    self._close_ctx()

                delay = backoff.next_delay()
                logger.info("reconnecting in %ss", delay)
                await asyncio.sleep(delay)
        finally:
            self._close_ctx()

    async def _connect_and_subscribe(self) -> None:
        if not self._config.symbols:
            raise RuntimeError("FUTU_SYMBOLS is empty")

        self._close_ctx()
        self._ctx = OpenQuoteContext(host=self._config.futu_host, port=self._config.futu_port)
        self._ctx.set_handler(self._handler)

        ret, data = self._ctx.subscribe(
            self._config.symbols,
            [SubType.TICKER],
            subscribe_push=True,
            session=Session.ALL,
        )
        if ret != RET_OK:
            raise RuntimeError(f"subscribe failed: {data}")

        logger.info("subscribed to %s", ",".join(self._config.symbols))

        if self._config.backfill_n > 0:
            await self._backfill_recent()

    async def _monitor_connection(self) -> None:
        while True:
            await asyncio.sleep(self._config.check_interval_sec)
            if self._ctx is None:
                raise RuntimeError("context closed")
            if not self._ctx.is_connected():
                raise RuntimeError("disconnected")

    async def _backfill_recent(self) -> None:
        if self._ctx is None:
            return
        for symbol in self._config.symbols:
            ret, data = self._ctx.get_rt_ticker(symbol, num=self._config.backfill_n)
            if ret != RET_OK:
                logger.warning("backfill failed for %s: %s", symbol, data)
                continue
            rows = ticker_df_to_rows(data, provider="futu", push_type="backfill", default_symbol=symbol)
            if rows:
                self._collector.enqueue(rows)
                logger.info("backfill %s rows for %s", len(rows), symbol)

    def _close_ctx(self) -> None:
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                logger.exception("failed to close futu context")
            self._ctx = None
