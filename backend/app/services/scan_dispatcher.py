"""Scan dispatcher — coordena workers de VPS que escaneiam batalhas no Albion.

Sem auth. Gera ranges a partir dos buracos da sequência por região (mesma
lógica do battle_sweeper/companion_scan), workers reivindicam, sondam a API
pública e reportam. O report é REVALIDADO contra a Albion (upsert_battle_light
plus BattleIdProbe) — nunca confiamos cegamente no client, igual ao companion.

Reaproveita _region_candidates (battle_sweeper), upsert_battle_light/
REPROCESS_REASON_SWEEPER (battle_tracker), make_client/HOSTS (player_tracker),
_probe_detail (battle_sweeper).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncSessionLocal
from app.models.battles import Battle, BattleIdProbe
from app.models.scan_worker import (
    MAX_PENDING_PER_REGION,
    SCAN_REGIONS,
    WORK_CLAIM_TTL,
    WORK_RANGE_SIZE,
    WORKER_HEARTBEAT_TIMEOUT,
    ScanWorker,
    ScanWorkTask,
)
from app.services.battle_sweeper import _probe_detail, _region_candidates
from app.services.battle_tracker import REPROCESS_REASON_SWEEPER, upsert_battle_light
from app.services.player_tracker import HOSTS, make_client

log = logging.getLogger(__name__)

# Quantos candidatos sondar pra gerar 1 tarefa (cada tarefa = WORK_RANGE_SIZE IDs).
CANDIDATES_PER_REGION = MAX_PENDING_PER_REGION * WORK_RANGE_SIZE

SCAN_DISPATCHER_INTERVAL = 60  # segundos


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def register_worker(
    db: AsyncSession, worker_id: str, name: str, region_pref: str | None
) -> ScanWorker:
    now = _now()
    w = await db.scalar(select(ScanWorker).where(ScanWorker.worker_id == worker_id))
    if w is None:
        w = ScanWorker(
            worker_id=worker_id, name=name, region_pref=region_pref,
            last_heartbeat=now, status="active",
        )
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
    # Libera tarefas que os workers mortos tinham claim — voltam a pending.
    await db.execute(
        update(ScanWorkTask)
        .where(ScanWorkTask.status == "claimed", ScanWorkTask.claimed_by.in_(dead_ids))
        .values(status="pending", claimed_by=None, claimed_at=None, claim_expires_at=None)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return len(dead_ids)


async def _release_expired_claims(db: AsyncSession) -> int:
    # synchronize_session=False: o UPDATE roda no banco e pronto. Com a sincronia
    # padrão o SQLAlchemy reavalia o WHERE em Python e o SQLite devolve datetime
    # sem tzinfo — comparação com _now() (aware) explode TypeError. Postgres ok.
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


async def _generate_tasks_for_region(db: AsyncSession, region: str) -> int:
    # Reaproveita _region_candidates do battle_sweeper: buracos entre IDs
    # conhecidos, do mais novo pro mais antigo, mais janela abaixo do mínimo.
    existing_pending = await db.scalar(
        select(ScanWorkTask.id)
        .where(ScanWorkTask.region == region, ScanWorkTask.status == "pending")
        .limit(1)
    )
    if existing_pending is not None:
        return 0

    raw = (await db.scalars(select(Battle.albion_id).where(Battle.region == region))).all()
    ids: set[int] = set()
    for a in raw:
        try:
            ids.add(int(a))
        except (TypeError, ValueError):
            continue
    if not ids:
        return 0

    ids_desc = sorted(ids, reverse=True)
    probed = {int(x) for x in (await db.scalars(select(BattleIdProbe.albion_id))).all() if str(x).isdigit()}
    candidates = _region_candidates(ids_desc, probed | ids, CANDIDATES_PER_REGION)

    created = 0
    for i in range(0, len(candidates), WORK_RANGE_SIZE):
        chunk = candidates[i : i + WORK_RANGE_SIZE]
        if not chunk:
            break
        prio = 2 if i == 0 else (1 if i < WORK_RANGE_SIZE * 5 else 0)
        db.add(ScanWorkTask(
            region=region,
            battle_id_start=chunk[-1],
            battle_id_end=chunk[0],
            priority=prio,
            status="pending",
        ))
        created += 1
    if created:
        await db.commit()
    return created


async def generate_tasks(db: AsyncSession) -> int:
    await _release_expired_claims(db)
    total = 0
    for region in SCAN_REGIONS:
        try:
            total += await _generate_tasks_for_region(db, region)
        except Exception as e:
            log.warning("scan_dispatcher: erro ao gerar tarefas (%s): %s", region, e)
            await db.rollback()
    if total:
        await db.commit()
    return total


async def claim_work(db: AsyncSession, worker_id: str, region: str | None = None) -> ScanWorkTask | None:
    await _release_expired_claims(db)

    w = await db.scalar(select(ScanWorker).where(ScanWorker.worker_id == worker_id))
    pref = region or (w.region_pref if w is not None else None)

    async def _pick(reg: str | None) -> ScanWorkTask | None:
        stmt = select(ScanWorkTask).where(ScanWorkTask.status == "pending")
        if reg is not None:
            stmt = stmt.where(ScanWorkTask.region == reg)
        return await db.scalar(stmt.order_by(ScanWorkTask.priority.desc(), ScanWorkTask.id.asc()).limit(1))

    task = await _pick(pref) if pref else None
    if task is None:
        task = await _pick(None)
    if task is None:
        await generate_tasks(db)
        task = await _pick(pref) if pref else None
        if task is None:
            task = await _pick(None)
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
    found: list[int], missing: list[int], errors: list[int],
) -> tuple[int, int]:
    task = await db.get(ScanWorkTask, task_id)
    if task is None:
        raise LookupError("tarefa não encontrada")
    if task.status != "claimed" or not task.claimed_by or task.claimed_by != worker_id:
        raise PermissionError("tarefa não pertence a este worker")

    partitions = (found, missing, errors)
    if any(len(part) > WORK_RANGE_SIZE for part in partitions):
        raise ValueError("partição grande demais")
    reported = found + missing + errors
    if len(reported) > WORK_RANGE_SIZE or len(reported) != len(set(reported)):
        raise ValueError("IDs duplicados ou em excesso")
    if any(aid < task.battle_id_start or aid > task.battle_id_end for aid in reported):
        raise ValueError("ID fora do range reivindicado")

    await db.commit()

    accepted = 0
    region = task.region
    async with make_client() as client:
        verified = await asyncio.gather(*(
            _probe_detail(client, HOSTS[region], str(aid)) for aid in reported
        ))

    verified_missing = 0
    verified_errors = 0
    for aid, (status, raw) in zip(reported, verified):
        battle = None
        try:
            if status == "found" and raw is not None and str(raw.get("id")) == str(aid):
                battle = await upsert_battle_light(db, raw, region)
                if battle is not None:
                    battle.reprocess_reason = REPROCESS_REASON_SWEEPER
                    accepted += 1
                else:
                    # API confirmou o ID, mas lutas pequenas não são armazenadas.
                    status = "found"
            elif status == "missing":
                verified_missing += 1
            else:
                status = "error"
                verified_errors += 1
        except Exception as e:
            log.warning("scan_dispatcher: upsert falhou (%s): %s", aid, e)
            status = "error"
            verified_errors += 1

        if status != "error":
            probe = await db.get(BattleIdProbe, str(aid))
            if probe is None:
                db.add(BattleIdProbe(
                    albion_id=str(aid), status=status, region=region,
                    battle_id=battle.id if battle else None, probed_at=_now(),
                ))
            else:
                probe.status = status
                probe.region = region
                probe.battle_id = battle.id if battle else None
                probe.probed_at = _now()

    task.status = "done"
    task.completed_at = _now()
    task.found_count = accepted
    task.missing_count = verified_missing
    task.error_count = verified_errors

    w = await db.scalar(select(ScanWorker).where(ScanWorker.worker_id == worker_id))
    if w is not None:
        w.total_tasks_done += 1
        w.total_battles_found += accepted
        w.total_errors += verified_errors
        w.last_found = accepted
        w.last_missing = verified_missing
        w.last_errors = verified_errors
        w.last_task_at = _now()
        w.last_heartbeat = _now()
        w.status = "active"

    await db.commit()
    return (accepted, len(reported) - accepted)


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

    per_region: dict[str, dict[str, int]] = {}
    rr = (await db.execute(
        select(ScanWorkTask.region, ScanWorkTask.status, func.count())
        .group_by(ScanWorkTask.region, ScanWorkTask.status)
    )).all()
    for region, status, cnt in rr:
        per_region.setdefault(region, {"pending": 0, "claimed": 0, "done": 0, "failed": 0})
        per_region[region][status] = cnt or 0

    return {"workers": worker_rows, "tasks": status_counts, "per_region": per_region}


async def run_forever() -> None:
    log.info("scan_dispatcher: scheduler iniciado (interval=%ds)", SCAN_DISPATCHER_INTERVAL)
    while True:
        async with AsyncSessionLocal() as db:
            try:
                dead = await mark_dead_workers(db)
                if dead:
                    log.info("scan_dispatcher: %d worker(s) marcado(s) dead", dead)
                # mark_dead_workers solta os claims dos mortos; generate_tasks
                # solta os claims expirados por TTL (worker vivo mas lento).
                n = await generate_tasks(db)
                if n:
                    log.info("scan_dispatcher: %d tarefas geradas", n)
            except Exception as e:
                log.error("scan_dispatcher: erro no scheduler: %s", e)
                await db.rollback()
        await asyncio.sleep(SCAN_DISPATCHER_INTERVAL)