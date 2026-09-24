"""Blotter event fan-out.

Without REDIS_URL this is an in-process set of sockets: correct and zero-latency
for a single API worker. With REDIS_URL every API worker and every Celery task
publishes to one channel, and each worker relays to the sockets it owns — so the
desk stays in sync across a horizontally scaled deployment.

The rest of the codebase only ever calls `bus.publish()`; which path runs is a
deployment decision, not a code change.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

REDIS_URL = os.getenv("REDIS_URL", "")
CHANNEL = "blotter:events"


class EventBus:
    def __init__(self) -> None:
        self.clients: set[Any] = set()
        self._redis: Any = None
        self._relay_task: asyncio.Task | None = None

    async def start(self) -> None:
        if not REDIS_URL:
            return
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        self._relay_task = asyncio.create_task(self._relay())

    async def stop(self) -> None:
        if self._relay_task:
            self._relay_task.cancel()
        if self._redis:
            await self._redis.aclose()

    async def _relay(self) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(CHANNEL)
        async for message in pubsub.listen():
            if message["type"] == "message":
                await self._fanout(json.loads(message["data"]))

    async def publish(self, event: dict) -> None:
        if not REDIS_URL:
            await self._fanout(event)
            return
        # A Celery worker has no long-lived bus, so it opens a client for the
        # publish and closes it again. The API worker reuses its own.
        if self._redis is not None:
            await self._redis.publish(CHANNEL, json.dumps(event))
            return
        import redis.asyncio as aioredis

        client = aioredis.from_url(REDIS_URL)
        try:
            await client.publish(CHANNEL, json.dumps(event))
        finally:
            await client.aclose()

    async def _fanout(self, event: dict) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json(event)
            except Exception:
                self.clients.discard(ws)


bus = EventBus()
