"""Retry CDN renders that were unavailable when a card or page first needed them."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.routes.render import (
    _UNAVAILABLE_RETRY_DELAY,
    discover_cached_render_misses,
    recover_render_miss,
    retry_delay,
)
from app.db import AsyncSessionLocal
from app.models.renders import RenderMiss

log = logging.getLogger(__name__)

BATCH_SIZE = 25
BUSY_INTERVAL = 5
IDLE_INTERVAL = 300
RETRY_DELAY = 0.3


async def _record_cached_misses() -> int:
    misses = await asyncio.to_thread(discover_cached_render_misses)
    if not misses:
        return 0
    now = datetime.now(timezone.utc)
    rows = [
        {
            "kind": kind,
            "key": key,
            "quality": quality,
            "size": size,
            "miss_count": 1,
            "last_attempt_at": now,
            "next_retry_at": now,
        }
        for kind, key, quality, size in misses
    ]
    async with AsyncSessionLocal() as db:
        await db.execute(pg_insert(RenderMiss).values(rows).on_conflict_do_nothing())
        await db.commit()
    return len(rows)


async def _recover_due() -> int:
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        misses = list((await db.scalars(
            select(RenderMiss)
            .where(RenderMiss.next_retry_at <= now)
            .order_by(RenderMiss.next_retry_at)
            .limit(BATCH_SIZE)
        )).all())
        # Do not keep a read transaction open while waiting on the CDN.
        await db.commit()
        for miss in misses:
            try:
                outcome = await recover_render_miss(miss.kind, miss.key, miss.quality, miss.size)
            except Exception:
                log.exception("render_recovery: falha ao recuperar %s", miss.key)
                outcome = None
            now = datetime.now(timezone.utc)
            if outcome:
                await db.delete(miss)
            elif outcome is None:
                miss.last_attempt_at = now
                miss.next_retry_at = now + _UNAVAILABLE_RETRY_DELAY
            else:
                miss.miss_count += 1
                miss.last_attempt_at = now
                miss.next_retry_at = now + retry_delay(miss.miss_count)
            await asyncio.sleep(RETRY_DELAY)
        if misses:
            await db.commit()
    return len(misses)


async def run_forever() -> None:
    log.info("render_recovery: iniciando")
    await asyncio.sleep(30)
    discovery_needed = True
    while True:
        recovered = 0
        try:
            if discovery_needed:
                discovered = await _record_cached_misses()
                discovery_needed = False
                if discovered:
                    log.info("render_recovery: %d misses legados descobertos", discovered)
            recovered = await _recover_due()
            if recovered:
                log.info("render_recovery: %d retentativas", recovered)
        except Exception:
            log.exception("render_recovery: ciclo falhou")
        await asyncio.sleep(BUSY_INTERVAL if recovered else IDLE_INTERVAL)
