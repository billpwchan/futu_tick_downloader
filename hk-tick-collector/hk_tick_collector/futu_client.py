from __future__ import annotations

import asyncio
import logging
from typing import Optional

from futu import OpenQuoteContext, RET_OK, Session, SubType, TickerHandlerBase

from .config import BackfillConfig, OpendConfig, ReconnectConfig
from .normalizer import normalize_futu_df
from .queue import TickPersistQueue

logger = logging.getLogger(__name__)


class FutuTickerHandler(TickerHandlerBase):
    def __init__(self, loop: asyncio.AbstractEventLoop, queue: TickPersistQueue, market: str) -> None:
        super().__init__()
        self._loop = loop
        self._queue = queue
        self._market = market

    def on_recv_rsp(self, rsp):
        ret, data = super().on_recv_rsp(rsp)
        if ret != RET_OK:
            logger.error("Futu ticker handler error: %s", data)
            return ret, data
        try:
            ticks = normalize_futu_df(data, self._market, provider="futu")
        except Exception:
            logger.exception("Failed to normalize futu ticker payload")
            return ret, data
        if ticks:
            future = asyncio.run_coroutine_threadsafe(self._queue.enqueue(ticks), self._loop)
            future.add_done_callback(_log_future_exception)
        return ret, data


def _log_future_exception(task: asyncio.Future) -> None:
    try:
        task.result()
    except Exception:
        logger.exception("Tick enqueue failed")


class FutuTickerClient:
    def __init__(
        self,
        opend: OpendConfig,
        reconnect: ReconnectConfig,
        backfill: BackfillConfig,
        queue: TickPersistQueue,
        loop: asyncio.AbstractEventLoop,
        market: str,
    ) -> None:
        self._opend = opend
        self._reconnect = reconnect
        self._backfill = backfill
        self._queue = queue
        self._loop = loop
        self._market = market
        self._ctx: Optional[OpenQuoteContext] = None
        self._handler = FutuTickerHandler(loop, queue, market)
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            await self._task
            self._task = None
        await self._close_ctx()

    async def _run(self) -> None:
        delay = self._reconnect.base_delay_ms / 1000
        max_delay = self._reconnect.max_delay_ms / 1000
        while not self._stop_event.is_set():
            if self._ctx is None or not self._ctx.is_connected():
                self._connected = False
                await self._close_ctx()
                try:
                    await self._connect_and_subscribe()
                    self._connected = True
                    delay = self._reconnect.base_delay_ms / 1000
                    if self._backfill.enabled:
                        await self._backfill_recent()
                except Exception:
                    logger.exception("Failed to connect or subscribe; retrying")
                    self._connected = False
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, max_delay)
                    continue
            await asyncio.sleep(1)

    async def _connect_and_subscribe(self) -> None:
        if not self._opend.symbols:
            raise RuntimeError("No symbols configured to subscribe")
        self._ctx = OpenQuoteContext(host=self._opend.host, port=self._opend.port)
        self._ctx.set_handler(self._handler)
        session = _parse_session(self._opend.session)
        ret, data = self._ctx.subscribe(self._opend.symbols, [SubType.TICKER], session=session)
        if ret != RET_OK:
            raise RuntimeError(f"Subscribe failed: {data}")
        logger.info("Subscribed to %s symbols", len(self._opend.symbols))

    async def _backfill_recent(self) -> None:
        if self._ctx is None:
            return
        for symbol in self._opend.symbols:
            ret, df = self._ctx.get_rt_ticker(symbol, num=self._backfill.num)
            if ret != RET_OK:
                logger.warning("Backfill failed for %s: %s", symbol, df)
                continue
            ticks = normalize_futu_df(df, self._market, provider="futu")
            if ticks:
                await self._queue.enqueue(ticks)
        logger.info("Backfill complete")

    async def _close_ctx(self) -> None:
        if self._ctx is None:
            return
        try:
            try:
                self._ctx.unsubscribe(self._opend.symbols, [SubType.TICKER])
            except Exception:
                logger.exception("Failed to unsubscribe")
            self._ctx.close()
        finally:
            self._ctx = None


def _parse_session(value: str) -> Session:
    session_key = value.strip().upper()
    try:
        return Session[session_key]
    except KeyError as exc:
        raise ValueError(f"Unsupported session value: {value}") from exc
