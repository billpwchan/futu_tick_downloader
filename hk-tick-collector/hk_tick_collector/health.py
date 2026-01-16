from __future__ import annotations

import logging
from typing import Optional

from aiohttp import web

from .queue import TickPersistQueue

logger = logging.getLogger(__name__)


class HealthServer:
    def __init__(self, host: str, port: int, queue: TickPersistQueue, client) -> None:
        self._host = host
        self._port = port
        self._queue = queue
        self._client = client
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

    async def start(self) -> None:
        self._app = web.Application()
        self._app.router.add_get("/healthz", self._handle_health)
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()
        logger.info("Health server listening on %s:%s", self._host, self._port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
        self._app = None
        self._runner = None
        self._site = None

    async def _handle_health(self, request: web.Request) -> web.Response:
        payload = {
            "status": "ok",
            "last_tick_ts": self._queue.last_tick_ts_ms,
            "queue_size": self._queue.queue_size(),
            "connected": self._client.is_connected(),
        }
        return web.json_response(payload)
