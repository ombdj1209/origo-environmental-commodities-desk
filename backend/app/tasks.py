"""Celery task tier.

Activated by setting REDIS_URL. The API then enqueues confirmation rendering
instead of running it in-process, which buys three things a background task
cannot: retry with backoff when a registry or mail gateway is down, survival of
an API restart mid-render, and workers that scale independently of web capacity.

    celery -A app.tasks worker --loglevel=info --pool=solo   # Windows
    celery -A app.tasks worker --loglevel=info -c 4          # Linux
"""
from __future__ import annotations

import asyncio

from celery import Celery

from .confirmations import build_confirmation
from .db import engine
from .events import REDIS_URL

celery_app = Celery("otc", broker=REDIS_URL or "memory://", backend=None)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    task_acks_late=True,            # a worker killed mid-render redelivers the job
    worker_prefetch_multiplier=1,
)


def _run(coro) -> None:
    """Each task gets a fresh event loop, so the async engine's pooled connections
    must not outlive it. Disposing is cheap next to rendering a PDF."""
    async def main():
        try:
            await coro
        finally:
            await engine.dispose()

    asyncio.run(main())


@celery_app.task(name="confirmations.render", bind=True, max_retries=5,
                 autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True)
def render_confirmation(self, trade_id: str, performed_by: str) -> None:
    _run(build_confirmation(trade_id, performed_by))


def dispatch_confirmation(trade_id: str, performed_by: str, background) -> str:
    """Enqueue on Celery when a broker is configured, else run in-process.

    Returns which path was taken, so `/api/v1/health` can report the live topology.
    """
    if REDIS_URL:
        render_confirmation.delay(str(trade_id), performed_by)
        return "celery"
    background.add_task(build_confirmation, trade_id, performed_by)
    return "in-process"
