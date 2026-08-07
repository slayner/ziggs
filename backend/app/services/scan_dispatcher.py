"""Scan dispatcher — coordena workers de VPS que pollam o feed do Albion.

O backend gera tasks de "feed" (poll de batalhas recentes ou kill events por
região), workers reivindicam, buscam a página da API pública e reportam os
dados crus. O backend faz upsert (batalhas via upsert_battle_light, kills
via _record_kill_event + upsert_player) — nunca confia cegamente no client,
mas as VPS são nossas e o dado vem direto da API pública.

Reaproveita upsert_battle_light/REPROCESS_REASON_SWEEPER (battle_tracker),
upsert_player/_record_kill_event/_upsert_event_players (player_tracker).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncSessionLocal
from app.models.battles import Battle, BattleIdProbe
from app.models.players import PlayerKillEvent
from app.models.scan_worker import (
    FEED_PAGE_SIZE,
    MAX_PENDING_PER_REGION,
    SCAN_REGIONS,
    WORK_CLAIM_TTL,
    WORKER_HEARTBEAT_TIMEOUT,
    ScanWorker,
    ScanWorkTask,
)
from app.services.battle_tracker import REPROCESS_REASON_SWEEPER, upsert_battle_light
from app.services.player_tracker import _record_kill_event, _upsert_event_players, upsert_player

log = logging.getLogger(__name__)

SCAN_DISPATCHER_INTERVAL = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def register_worker(
    db: AsyncSession, worker_id: str, name: str, region_pref: str | None
) -> ScanWorker:
    now = _now()
    w = await db.scalar(select(ScanWorker).where(ScanWorker.worker_id == worker_id))
    if w is None:
        w = ScanWorker(worker_id=worker_id, name=name, region_pref=region_pref)
        db.add(w)
    else:
        w.name = name
        w.region_pref = region_pref
    w.status = "active"
    w.last_heartbeat = now
    await db.commit()
    await db.refresh(w)
    return w


async def heartbeat(db: AsyncSession, worker_id: str) -> None:
    now = _now()
    await db.execute(
        update(ScanWorker)
        .where(ScanWorker.worker_id == worker_id)
        .values(last_heartbeat=now, status="active")
    )
    await db.commit()


async def mark_dead_workers(db: AsyncSession) -> int:
    cutoff = _now() - WORKER_HEARTBEAT_TIMEOUT
    dead_ids = (await db.scalars(
        select(ScanWorker.worker_id).where(
            ScanWorker.status == "active", ScanWorker.last_heartbeat < cutoff
        )
    )).all()
    if not dead_ids:
        return 0
    await db.execute(
        update(ScanWorker)
        .where(ScanWorker.worker_id.in_(dead_ids))
        .values(status="dead")
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        update(ScanWorkTask)
        .where(ScanWorkTask.status == "claimed", ScanWorkTask.claimed_by.in_(dead_ids))
        .values(status="pending", claimed_by=None, claimed_at=None, claim_expires_at=None)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return len(dead_ids)


async def _release_expired_claims(db: AsyncSession) -> int:
    result = await db.execute(
        update(ScanWorkTask)
        .where(
            ScanWorkTask.status == "claimed",
            ScanWorkTask.claim_expires_at < _now(),
        )
        .values(status="pending", claimed_by=None, claimed_at=None, claim_expires_at=None)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


async def _generate_feed_tasks(db: AsyncSession) -> int:
    """Gera tasks de feed polling: battles e kills para cada região.

    Para cada (region, feed_type), mantém até MAX_PENDING_PER_REGION tasks
    pending. As tasks são páginas do feed (page_offset=0,1,2,...). A página 0
    é alta prioridade (mais recente), páginas maiores são baixa prioridade.
    """
    total = 0
    for region in SCAN_REGIONS:
        for feed_type in ("battles", "kills"):
            pending = await db.scalar(
                select(func.count()).select_from(ScanWorkTask).where(
                    ScanWorkTask.region == region,
                    ScanWorkTask.feed_type == feed_type,
                    ScanWorkTask.status == "pending",
                )
            ) or 0
            if pending >= MAX_PENDING_PER_REGION:
                continue
            needed = MAX_PENDING_PER_REGION - pending
            for page in range(needed):
                # Verifica se já existe task pending/done para esta página
                exists = await db.scalar(
                    select(ScanWorkTask.id).where(
                        ScanWorkTask.region == region,
                        ScanWorkTask.feed_type == feed_type,
                        ScanWorkTask.page_offset == page,
                        ScanWorkTask.status.in_(["pending", "claimed"]),
                    )
                )
                if exists is not None:
                    continue
                prio = 2 if page == 0 else (1 if page < 3 else 0)
                db.add(ScanWorkTask(
                    region=region,
                    feed_type=feed_type,
                    page_offset=page * FEED_PAGE_SIZE,
                    priority=prio,
                    status="pending",
                ))
                total += 1
    if total:
        await db.commit()
    return total


async def generate_tasks(db: AsyncSession) -> int:
    await _release_expired_claims(db)
    total = await _generate_feed_tasks(db)
    return total


async def claim_work(db: AsyncSession, worker_id: str, region: str | None = None) -> ScanWorkTask | None:
    await _release_expired_claims(db)

    # Se worker já tem claim vivo, devolve o mesmo
    held = await db.scalar(
        select(ScanWorkTask)
        .where(
            ScanWorkTask.claimed_by == worker_id,
            ScanWorkTask.status == "claimed",
        )
        .order_by(ScanWorkTask.id.asc())
        .limit(1)
    )
    if held is not None:
        now = _now()
        held.claimed_at = now
        held.claim_expires_at = now + WORK_CLAIM_TTL
        await db.commit()
        return held

    # Tenta região preferida primeiro
    regions = [region] if region and region in SCAN_REGIONS else list(SCAN_REGIONS)
    for r in regions:
        task = await db.scalar(
            select(ScanWorkTask)
            .where(ScanWorkTask.status == "pending", ScanWorkTask.region == r)
            .order_by(ScanWorkTask.priority.desc(), ScanWorkTask.id.asc())
            .limit(1)
        )
        if task is not None:
            now = _now()
            task.status = "claimed"
            task.claimed_by = worker_id
            task.claimed_at = now
            task.claim_expires_at = now + WORK_CLAIM_TTL
            await db.commit()
            await db.refresh(task)
            return task

    # Sem região pref: pega qualquer pending
    task = await db.scalar(
        select(ScanWorkTask)
        .where(ScanWorkTask.status == "pending")
        .order_by(ScanWorkTask.priority.desc(), ScanWorkTask.id.asc())
        .limit(1)
    )
    if task is None:
        await generate_tasks(db)
        task = await db.scalar(
            select(ScanWorkTask)
            .where(ScanWorkTask.status == "pending")
            .order_by(ScanWorkTask.priority.desc(), ScanWorkTask.id.asc())
            .limit(1)
        )
        if task is None:
            return None

    now = _now()
    task.status = "claimed"
    task.claimed_by = worker_id
    task.claimed_at = now
    task.claim_expires_at = now + WORK_CLAIM_TTL
    await db.commit()
    await db.refresh(task)
    return task


async def report_work(
    db: AsyncSession, worker_id: str, task_id: int,
    found_count: int, error_count: int,
    data: list[dict] | None = None,
) -> tuple[int, int]:
    """Processa o report de uma task de feed. `data` é a lista de batalhas ou
    kill events crus vindos da API do Albion. O backend faz upsert."""
    task = await db.get(ScanWorkTask, task_id)
    if task is None:
        raise LookupError("tarefa não encontrada")
    if task.status != "claimed" or not task.claimed_by or task.claimed_by != worker_id:
        raise PermissionError("tarefa não pertence a este worker")

    region = task.region
    accepted = 0
    errors = error_count

    if task.feed_type == "battles" and data:
        for raw in data:
            try:
                battle = await upsert_battle_light(db, raw, region)
                if battle is not None:
                    battle.reprocess_reason = REPROCESS_REASON_SWEEPER
                    accepted += 1
            except Exception as e:
                log.warning("scan_dispatcher: upsert battle falhou (%s): %s", raw.get("id"), e)
                errors += 1

    elif task.feed_type == "kills" and data:
        for ev in data:
            event_id = str(ev.get("EventId") or "")
            if not event_id:
                continue
            if await db.scalar(
                select(PlayerKillEvent.id).where(
                    PlayerKillEvent.region == region,
                    PlayerKillEvent.albion_event_id == event_id,
                )
            ) is not None:
                continue
            try:
                await _upsert_event_players(db, ev, region)
                await _record_kill_event(db, ev, region, commit=False)
                await db.commit()
                accepted += 1
            except Exception as e:
                await db.rollback()
                log.debug("scan_dispatcher: skip kill event %s (%s): %s", event_id, region, e)
                errors += 1

    task.status = "done"
    task.completed_at = _now()
    task.found_count = accepted
    task.missing_count = 0
    task.error_count = errors

    w = await db.scalar(select(ScanWorker).where(ScanWorker.worker_id == worker_id))
    if w is not None:
        w.total_tasks_done += 1
        if task.feed_type == "battles":
            w.total_battles_found += accepted
        else:
            w.total_kills_found += accepted
        w.total_errors += errors
        w.last_found = accepted
        w.last_missing = 0
        w.last_errors = errors
        w.last_task_at = _now()
        w.last_heartbeat = _now()
        w.status = "active"

    await db.commit()
    return (accepted, errors)


async def get_worker_stats(db: AsyncSession) -> dict:
    now = _now()
    workers = (await db.scalars(
        select(ScanWorker).order_by(ScanWorker.status.asc(), ScanWorker.id.asc())
    )).all()
    worker_rows = []
    for w in workers:
        hb_age = (now - w.last_heartbeat).total_seconds() if w.last_heartbeat is not None else None
        worker_rows.append({
            "worker_id": w.worker_id,
            "name": w.name,
            "region_pref": w.region_pref,
            "status": w.status,
            "last_heartbeat_age_s": round(hb_age, 1) if hb_age is not None else None,
            "total_tasks_done": w.total_tasks_done,
            "total_battles_found": w.total_battles_found,
            "total_kills_found": w.total_kills_found,
            "total_errors": w.total_errors,
            "last_found": w.last_found,
            "last_missing": w.last_missing,
            "last_errors": w.last_errors,
            "last_task_at": w.last_task_at.isoformat() if w.last_task_at else None,
        })

    status_counts: dict[str, int] = {"pending": 0, "claimed": 0, "done": 0, "failed": 0}
    rows = (await db.execute(
        select(ScanWorkTask.status, func.count()).group_by(ScanWorkTask.status)
    )).all()
    for status, cnt in rows:
        status_counts[status] = cnt or 0

    per_region: dict[str, dict] = {}
    rr = (await db.execute(
        select(ScanWorkTask.region, ScanWorkTask.feed_type, ScanWorkTask.status, func.count())
        .group_by(ScanWorkTask.region, ScanWorkTask.feed_type, ScanWorkTask.status)
    )).all()
    for region, feed_type, status, cnt in rr:
        key = f"{region}/{feed_type}"
        per_region.setdefault(key, {"pending": 0, "claimed": 0, "done": 0, "failed": 0})
        per_region[key][status] = cnt or 0

    return {"workers": worker_rows, "tasks": status_counts, "per_region": per_region}


async def run_forever() -> None:
    log.info("scan_dispatcher: iniciado (interval=%ds)", SCAN_DISPATCHER_INTERVAL)
    while True:
        async with AsyncSessionLocal() as db:
            try:
                await mark_dead_workers(db)
                n = await generate_tasks(db)
                if n:
                    log.info("scan_dispatcher: %d tasks geradas", n)
            except Exception as e:
                log.error("scan_dispatcher: erro: %s", e)
                await db.rollback()
        await asyncio_sleep(SCAN_DISPATCHER_INTERVAL)


async def asyncio_sleep(s: float):
    import asyncio
    await asyncio.sleep(s)