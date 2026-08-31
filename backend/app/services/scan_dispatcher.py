"""Distributed Albion feed coordinator.

The backend owns the strategy and cursors. VPS workers only claim pages,
fetch them from Albion and report the raw payload. Claims are global and
atomic, so every VPS can work on every region without overlapping another.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, delete, distinct, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncSessionLocal, async_engine
from app.models.battles import Battle, BattleSyncCursor
from app.models.players import KillSyncCursor, PlayerKillEvent
from app.models.scan_worker import (
    FEED_PAGE_SIZE,
    SCAN_REGIONS,
    WORK_CLAIM_TTL,
    WORKER_HEARTBEAT_TIMEOUT,
    ScanIngestPayload,
    ScanIncident,
    ScanLap,
    ScanStreamState,
    ScanWorker,
    ScanWorkerRegionMetric,
    ScanWorkTask,
)
from app.services.battle_tracker import (
    BATTLES_API_OFFSET_LIMIT,
    DEEP_PROCESS_MIN_PLAYERS,
    EVENTS_MAX_PAGES,
    _battle_occurred_at,
    _battle_source_id,
    fetch_battles,
    fetch_battle_detail,
    fetch_events,
)
from app.services.albion_gate import OTHER, slot
from app.models.native_feed import NativeFeedItem
from app.services.native_feed import KIND_BATTLE, KIND_KILL, capture_discovered_items

from app.services.player_tracker import (
    HOSTS,
    KILL_BACKFILL_OFFSET_LIMIT,
    _event_occurred_at,
    _event_source_id,
    make_client,
)

log = logging.getLogger(__name__)

SCAN_DISPATCHER_INTERVAL = 15
ORDERED_RECOVERY_INTERVAL = 15
RECENT_PAGES = 8
MAX_RECENT_PAGES = 16
BACKFILL_PAGE_STRIDE = 40
RECENT_INTERVAL = {"battles": timedelta(seconds=60), "kills": timedelta(seconds=120)}
BACKFILL_TASKS_PER_WORKER = 2
MAX_BACKFILL_TASKS_PER_STREAM = 12
DONE_RETENTION = timedelta(hours=1)
DEAD_WORKER_RETENTION = timedelta(hours=24)
FAILED_RETRY_INTERVAL = timedelta(seconds=60)
BACKEND_IDLE_SECONDS = 5.0
INGEST_BACKPRESSURE = 100
INGEST_FORCE_DRAIN = 300
INGEST_MAX_ATTEMPTS = 5
CIRCUIT_ERROR_THRESHOLD = 3
CIRCUIT_OPEN_INTERVAL = timedelta(seconds=60)
FOREGROUND_LATENCY_LIMIT_MS = 500.0
FOREGROUND_LATENCY_TTL = 30.0
DB_CHECKED_OUT_LIMIT = 20
CLAIM_LOCK_ID = 0x5A494748
WINDOW_RATE_ALPHA = 0.25
LATENCY_EWMA_ALPHA = 0.25
_BACKEND_WORKER_ID = "backend-idle"

_foreground_inflight = 0
_last_foreground_activity = time.monotonic()
_last_foreground_latency_ms = 0.0
# ponytail: processo web único; persistir este contador só importa se claims forem
# distribuídas entre múltiplos processos FastAPI.
_claim_sequence = 0
_affinity_sequence = 0
_INTERNAL_PREFIXES = ("/scan/", "/bot/", "/health", "/render/", "/companion/")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _incident(
    db: AsyncSession, event: str, *, actor: str = "system",
    worker_id: str | None = None, region: str | None = None,
    feed_type: str | None = None, task_id: int | None = None,
    details: dict | None = None,
) -> None:
    db.add(ScanIncident(
        event=event, actor=actor, worker_id=worker_id, region=region,
        feed_type=feed_type, task_id=task_id, details=details,
    ))


def foreground_request_started(path: str) -> bool:
    """Track user-facing HTTP pressure for the opportunistic backend worker."""
    global _foreground_inflight, _last_foreground_activity
    if path.startswith(_INTERNAL_PREFIXES):
        return False
    _foreground_inflight += 1
    _last_foreground_activity = time.monotonic()
    return True


def foreground_request_finished(tracked: bool, latency_ms: float = 0.0) -> None:
    global _foreground_inflight, _last_foreground_activity, _last_foreground_latency_ms
    if not tracked:
        return
    _foreground_inflight = max(0, _foreground_inflight - 1)
    _last_foreground_activity = time.monotonic()
    _last_foreground_latency_ms = latency_ms


def backend_is_idle() -> bool:
    return (
        _foreground_inflight == 0
        and time.monotonic() - _last_foreground_activity >= BACKEND_IDLE_SECONDS
    )


def pressure_status() -> dict:
    return {
        "idle": backend_is_idle(),
        "inflight": _foreground_inflight,
        "quiet_for_s": round(max(0.0, time.monotonic() - _last_foreground_activity), 3),
        "last_latency_ms": round(_last_foreground_latency_ms, 1),
    }


def _parallelism_policy(
    active_workers: int,
    ingest_backlog: int,
    web_idle: bool,
    latency_ms: float,
    latency_age_s: float,
    db_checked_out: int,
) -> tuple[int, bool]:
    pressured = (
        not web_idle
        or ingest_backlog >= INGEST_BACKPRESSURE
        or (latency_ms >= FOREGROUND_LATENCY_LIMIT_MS and latency_age_s < FOREGROUND_LATENCY_TTL)
        or db_checked_out >= DB_CHECKED_OUT_LIMIT
    )
    limit = max(1, active_workers // 2) if pressured else max(1, active_workers)
    return limit, pressured


def _backfill_due(recent_only: bool) -> bool:
    global _claim_sequence
    if recent_only:
        return False
    _claim_sequence += 1
    return _claim_sequence % 4 == 0


def _next_window_rate(current: float | None, new_items: int, elapsed_s: float) -> float:
    sample = new_items * 60 / max(1.0, elapsed_s)
    return sample if current is None else current * (1 - WINDOW_RATE_ALPHA) + sample * WINDOW_RATE_ALPHA


def _next_latency(current: float | None, sample_ms: int) -> float:
    return float(sample_ms) if current is None else current * (1 - LATENCY_EWMA_ALPHA) + sample_ms * LATENCY_EWMA_ALPHA


def _affinity_due() -> bool:
    global _affinity_sequence
    _affinity_sequence += 1
    return _affinity_sequence % 3 == 0


async def register_worker(
    db: AsyncSession, worker_id: str, name: str, region_pref: str | None,
    *,
    vps_label: str | None = None,
    vps_country: str | None = None,
    vps_endpoint: str | None = None,
    vps_server_pubkey: str | None = None,
    vps_ping_url: str | None = None,
) -> tuple[ScanWorker, str]:
    worker = await db.scalar(select(ScanWorker).where(ScanWorker.worker_id == worker_id))
    if worker is None:
        worker = ScanWorker(worker_id=worker_id, name=name, region_pref=None)
        db.add(worker)
    else:
        if worker.status == "quarantined" or worker.credential_revoked:
            raise PermissionError("worker credential revoked")
        worker.name = name
        worker.region_pref = None
    worker.status = "active"
    worker.last_heartbeat = _now()
    # Tunnel metadata — atualiza a cada registro (VPS pode mudar endpoint/key).
    worker.vps_label = vps_label
    worker.vps_country = vps_country
    worker.vps_endpoint = vps_endpoint
    worker.vps_server_pubkey = vps_server_pubkey
    worker.vps_ping_url = vps_ping_url
    token = secrets.token_urlsafe(32)
    worker.api_token_hash = hashlib.sha256(token.encode()).hexdigest()
    worker.credential_revoked = False
    await db.commit()
    await db.refresh(worker)
    return worker, token


async def authenticate_worker(db: AsyncSession, worker_id: str, token: str | None) -> None:
    worker = await db.scalar(select(ScanWorker).where(ScanWorker.worker_id == worker_id))
    if (
        worker is None
        or not token
        or not worker.api_token_hash
        or worker.credential_revoked
        or worker.status == "quarantined"
        or not secrets.compare_digest(
            worker.api_token_hash, hashlib.sha256(token.encode()).hexdigest()
        )
    ):
        raise PermissionError("invalid worker credential")


async def heartbeat(db: AsyncSession, worker_id: str) -> None:
    await db.execute(
        update(ScanWorker)
        .where(ScanWorker.worker_id == worker_id)
        .values(
            last_heartbeat=_now(),
            status=case((ScanWorker.status == "dead", "active"), else_=ScanWorker.status),
            region_pref=None,
        )
    )
    await db.commit()


async def count_active_workers(db: AsyncSession) -> int:
    cutoff = _now() - WORKER_HEARTBEAT_TIMEOUT
    return int(await db.scalar(
        select(func.count(distinct(ScanWorker.worker_id))).where(
            ScanWorker.status == "active",
            ScanWorker.last_heartbeat >= cutoff,
        )
    ) or 0)


async def mark_dead_workers(db: AsyncSession) -> int:
    cutoff = _now() - WORKER_HEARTBEAT_TIMEOUT
    dead_ids = (await db.scalars(
        select(ScanWorker.worker_id).where(
            ScanWorker.status == "active",
            ScanWorker.last_heartbeat < cutoff,
        )
    )).all()
    if not dead_ids:
        return 0
    dead_streams = (await db.execute(
        select(ScanWorkTask.region, ScanWorkTask.feed_type).where(
            ScanWorkTask.status == "claimed",
            ScanWorkTask.claimed_by.in_(dead_ids),
        ).distinct()
    )).all()
    for worker_id in dead_ids:
        _incident(db, "worker_dead", worker_id=worker_id)
    await db.execute(
        update(ScanWorker)
        .where(ScanWorker.worker_id.in_(dead_ids))
        .values(status="dead")
        .execution_options(synchronize_session=False)
    )
    await _reopen_half_open(db, dead_streams)
    await db.execute(
        update(ScanWorkTask)
        .where(ScanWorkTask.status == "claimed", ScanWorkTask.claimed_by.in_(dead_ids))
        .values(
            status="pending",
            claimed_by=None,
            lease_token=None,
            claimed_at=None,
            claim_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return len(dead_ids)


async def _release_expired_claims(db: AsyncSession) -> int:
    expired_streams = (await db.execute(
        select(ScanWorkTask.region, ScanWorkTask.feed_type).where(
            ScanWorkTask.status == "claimed",
            ScanWorkTask.claim_expires_at < _now(),
        ).distinct()
    )).all()
    result = await db.execute(
        update(ScanWorkTask)
        .where(
            ScanWorkTask.status == "claimed",
            ScanWorkTask.claim_expires_at < _now(),
        )
        .values(
            status="pending",
            claimed_by=None,
            lease_token=None,
            claimed_at=None,
            claim_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    await _reopen_half_open(db, expired_streams)
    return result.rowcount or 0


async def _reopen_half_open(db: AsyncSession, streams) -> None:
    for region, feed_type in streams:
        await db.execute(
            update(ScanStreamState)
            .where(
                ScanStreamState.region == region,
                ScanStreamState.feed_type == feed_type,
                ScanStreamState.circuit_state == "half_open",
            )
            .values(circuit_state="open", opened_until=_now() + CIRCUIT_OPEN_INTERVAL)
        )


def _apply_circuit_result(state: ScanStreamState, success: bool, now: datetime) -> None:
    if success:
        state.circuit_state = "closed"
        state.consecutive_errors = 0
        state.opened_until = None
        return
    state.consecutive_errors += 1
    if state.circuit_state == "half_open" or state.consecutive_errors >= CIRCUIT_ERROR_THRESHOLD:
        state.circuit_state = "open"
        state.opened_until = now + CIRCUIT_OPEN_INTERVAL


async def _record_stream_result(
    db: AsyncSession, task: ScanWorkTask, success: bool, item_count: int = 0
) -> None:
    state = await db.scalar(
        select(ScanStreamState)
        .where(
            ScanStreamState.region == task.region,
            ScanStreamState.feed_type == task.feed_type,
        )
        .with_for_update()
    )
    if state is not None:
        if task.priority <= 0:
            return
        was_open = state.circuit_state
        _apply_circuit_result(state, success, _now())
        if (
            success
            and task.priority > 0
            and task.page_offset == (state.recent_pages - 1) * FEED_PAGE_SIZE
            and item_count >= FEED_PAGE_SIZE
        ):
            state.recent_pages = min(MAX_RECENT_PAGES, state.recent_pages + 1)
        if state.circuit_state == "open" and was_open != "open":
            log.warning("scan_dispatcher: circuit open %s/%s", task.region, task.feed_type)
            _incident(db, "circuit_open", region=task.region, feed_type=task.feed_type)
        elif success and was_open != "closed":
            _incident(db, "circuit_closed", region=task.region, feed_type=task.feed_type)


async def _ensure_recent_task(
    db: AsyncSession, region: str, feed_type: str, offset: int, priority: int
) -> int:
    active = await db.scalar(
        select(ScanWorkTask.id).where(
            ScanWorkTask.region == region,
            ScanWorkTask.feed_type == feed_type,
            ScanWorkTask.page_offset == offset,
            ScanWorkTask.status.in_(("pending", "claimed", "reported")),
        ).limit(1)
    )
    if active is not None:
        return 0

    recent_failure = await db.scalar(
        select(ScanWorkTask.id).where(
            ScanWorkTask.region == region,
            ScanWorkTask.feed_type == feed_type,
            ScanWorkTask.page_offset == offset,
            ScanWorkTask.status == "failed",
            ScanWorkTask.completed_at >= _now() - FAILED_RETRY_INTERVAL,
        ).limit(1)
    )
    if recent_failure is not None:
        return 0

    reusable = await db.scalar(
        select(ScanWorkTask)
        .where(
            ScanWorkTask.region == region,
            ScanWorkTask.feed_type == feed_type,
            ScanWorkTask.page_offset == offset,
            ScanWorkTask.status.in_(("done", "failed")),
        )
        .order_by(ScanWorkTask.completed_at.desc().nullslast(), ScanWorkTask.id.desc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if (
        reusable is not None
        and reusable.status == "done"
        and reusable.completed_at is not None
        and reusable.completed_at >= _now() - RECENT_INTERVAL[feed_type]
    ):
        return 0
    if reusable is None:
        db.add(ScanWorkTask(
            region=region,
            feed_type=feed_type,
            page_offset=offset,
            priority=priority,
            status="pending",
        ))
    else:
        reusable.priority = priority
        reusable.status = "pending"
        reusable.claimed_by = None
        reusable.lease_token = None
        reusable.claimed_at = None
        reusable.claim_expires_at = None
        reusable.completed_at = None
        reusable.found_count = 0
        reusable.missing_count = 0
        reusable.error_count = 0
    return 1


def _stream_limits(
    feed_type: str,
    stride: int = FEED_PAGE_SIZE,
    recent_pages: int = RECENT_PAGES,
) -> tuple[int, int]:
    start = recent_pages * FEED_PAGE_SIZE
    api_limit = BATTLES_API_OFFSET_LIMIT if feed_type == "battles" else KILL_BACKFILL_OFFSET_LIMIT
    max_offset = api_limit - FEED_PAGE_SIZE
    last = start + ((max_offset - start) // stride) * stride
    return start, max(start, last)


async def _locked_cursor(db: AsyncSession, region: str, feed_type: str):
    model = BattleSyncCursor if feed_type == "battles" else KillSyncCursor
    cursor = await db.scalar(
        select(model).where(model.region == region).with_for_update().limit(1)
    )
    if cursor is None:
        cursor = model(region=region, next_offset=RECENT_PAGES * FEED_PAGE_SIZE, done=False)
        db.add(cursor)
        await db.flush()
    return cursor


async def _reserve_backfill_tasks(
    db: AsyncSession, region: str, feed_type: str, target: int
) -> int:
    lap = await db.scalar(
        select(ScanLap).where(
            ScanLap.region == region,
            ScanLap.feed_type == feed_type,
            ScanLap.status == "active",
        ).with_for_update().limit(1)
    )
    state = await db.scalar(select(ScanStreamState).where(
        ScanStreamState.region == region,
        ScanStreamState.feed_type == feed_type,
    ))
    recent_pages = state.recent_pages if state is not None else RECENT_PAGES
    if lap is None:
        start, last = _stream_limits(feed_type, BACKFILL_PAGE_STRIDE, recent_pages)
        available_pages = ((last - start) // BACKFILL_PAGE_STRIDE) + 1
        lap = ScanLap(
            region=region,
            feed_type=feed_type,
            status="active",
            expected_pages=available_pages,
            completed_pages=0,
            page_stride=BACKFILL_PAGE_STRIDE,
        )
        db.add(lap)
        await db.flush()
        await db.execute(
            update(ScanWorkTask).where(
                ScanWorkTask.region == region,
                ScanWorkTask.feed_type == feed_type,
                ScanWorkTask.priority == 0,
                ScanWorkTask.status.in_(("pending", "claimed", "reported")),
                ScanWorkTask.lap_id.is_(None),
            ).values(lap_id=lap.id)
        )
    start, last = _stream_limits(feed_type, lap.page_stride, recent_pages)
    available_pages = ((last - start) // lap.page_stride) + 1
    target = min(target, available_pages)
    current = int(await db.scalar(
        select(func.count()).select_from(ScanWorkTask).where(
            ScanWorkTask.region == region,
            ScanWorkTask.feed_type == feed_type,
            ScanWorkTask.priority == 0,
            ScanWorkTask.status.in_(("pending", "claimed", "reported")),
        )
    ) or 0)
    needed = max(0, target - current)
    if not needed:
        return 0

    cursor = await _locked_cursor(db, region, feed_type)
    offset = cursor.next_offset
    if offset < start or offset > last or (offset - start) % lap.page_stride:
        offset = start

    assigned_offsets = set((await db.scalars(
        select(ScanWorkTask.page_offset).where(
            ScanWorkTask.region == region,
            ScanWorkTask.feed_type == feed_type,
            ScanWorkTask.status.in_(("pending", "claimed", "reported")),
        )
    )).all())
    created = 0
    attempts = 0
    while created < needed and attempts < available_pages:
        attempts += 1
        if offset not in assigned_offsets:
            db.add(ScanWorkTask(
                region=region,
                feed_type=feed_type,
                page_offset=offset,
                priority=0,
                status="pending",
                lap_id=lap.id,
            ))
            assigned_offsets.add(offset)
            created += 1
        offset += lap.page_stride
        if offset > last:
            offset = start
    cursor.next_offset = offset
    cursor.done = False
    return created


async def _cleanup_history(db: AsyncSession) -> None:
    now = _now()
    await db.execute(delete(ScanWorkTask).where(
        ScanWorkTask.status.in_(("done", "failed")),
        ScanWorkTask.completed_at < now - DONE_RETENTION,
        or_(
            ScanWorkTask.lap_id.is_(None),
            ScanWorkTask.lap_id.not_in(
                select(ScanLap.id).where(ScanLap.status == "active")
            ),
        ),
    ))
    await db.execute(delete(ScanWorker).where(
        ScanWorker.status == "dead",
        ScanWorker.last_heartbeat < now - DEAD_WORKER_RETENTION,
    ))
    await db.execute(delete(ScanIngestPayload).where(
        ScanIngestPayload.status.in_(("done", "failed")),
        ScanIngestPayload.completed_at < now - DONE_RETENTION,
    ))


async def _retry_failed_tasks(db: AsyncSession) -> int:
    await db.execute(text("""
        DELETE FROM scan_work_tasks t
        WHERE t.status = 'failed'
        AND EXISTS (
            SELECT 1 FROM scan_work_tasks t2
            WHERE t2.region = t.region
              AND t2.feed_type = t.feed_type
              AND t2.page_offset = t.page_offset
              AND t2.status IN ('pending', 'claimed', 'reported')
        )
    """))
    # Deleta failed duplicada (mantem a mais recente por offset) — dois
    # faileds no mesmo offset viram dois pendings e violam a unique partial.
    await db.execute(text("""
        DELETE FROM scan_work_tasks
        WHERE status = 'failed'
        AND id NOT IN (
            SELECT DISTINCT ON (region, feed_type, page_offset) id
            FROM scan_work_tasks
            WHERE status = 'failed'
            ORDER BY region, feed_type, page_offset, completed_at DESC
        )
    """))
    result = await db.execute(
        update(ScanWorkTask)
        .where(
            ScanWorkTask.status == "failed",
            ScanWorkTask.completed_at < _now() - FAILED_RETRY_INTERVAL,
            # deep_process: 404 permanente em batalhas velhas — não recriar
            # depois de 3 tentativas. A batalha é marcada como deep abaixo.
            ~((ScanWorkTask.feed_type == "deep_process") & (ScanWorkTask.attempt_count >= 3)),
        )
        .values(
            status="pending",
            claimed_by=None,
            claimed_at=None,
            claim_expires_at=None,
            completed_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    # Batalhas deep_process que excederam 3 tentativas: marcar
    # reprocess_reason pra o battle_reprocessor tentar de novo e deletar as
    # tasks. NÃO setar processing_tier="deep" — deep significa "deep-processado
    # com sucesso" (com kill_events/sides/equipment). Marcar deep sem processar
    # deixava a batalha sem kill_events, só com side "rats", e o frontend não
    # tinha como distinguir de uma deep real.
    exhausted = (await db.scalars(
        select(ScanWorkTask).where(
            ScanWorkTask.feed_type == "deep_process",
            ScanWorkTask.status == "failed",
            ScanWorkTask.attempt_count >= 3,
        )
    )).all()
    if exhausted:
        albion_ids = [str(t.page_offset) for t in exhausted]
        await db.execute(
            update(Battle)
            .where(Battle.albion_id.in_(albion_ids))
            .values(processing_tier="light", reprocess_reason="deep_process_failed")
            .execution_options(synchronize_session=False)
        )
        for t in exhausted:
            await db.delete(t)
    return result.rowcount or 0


# ── Deep-process delegado pros workers ──────────────────────────────────────
# Workers ociosos (sem feed recente pra claimar) podem ajudar o backend a
# deep-processar batalhas antigas. O backend cuida das recentes (via
# _retry_stuck_battles em ordem decrescente); os workers pegam as mais velhas
# (ascendente) pra não competir no mesmo conjunto.
DEEP_PROCESS_TASKS_PER_WORKER = 3
DEEP_PROCESS_BATCH = 20  # batalhas light transformadas em tasks por ciclo


async def _ensure_deep_process_tasks(db: AsyncSession, active_workers: int) -> int:
    """Cria tarefas 'deep_process' pra batalhas light antigas que workers
    ociosos podem claimar. prioridade 0 (só roda quando não há feed recente).

    Pega batalhas light em ordem ASCENDENTE (mais velhas primeiro) — o backend
    via _retry_stuck_battles cuida das recentes (descendente). Assim os dois
    caminhos não competem no mesmo conjunto.

    page_offset guarda o battle.id (não o offset do feed)."""
    # Já existem tasks deep_process pendentes/claimed suficientes?
    target = max(1, active_workers * DEEP_PROCESS_TASKS_PER_WORKER)
    existing = int(await db.scalar(
        select(func.count()).select_from(ScanWorkTask).where(
            ScanWorkTask.feed_type == "deep_process",
            ScanWorkTask.status.in_(("pending", "claimed")),
        )
    ) or 0)
    needed = max(0, DEEP_PROCESS_BATCH - existing, target - existing)
    if needed == 0:
        return 0

    # Batalhas light com players suficientes, DESCENDENTE (recentes primeiro) —
    # a API pública do Albion só serve batalhas numa janela de ~7-14 dias;
    # batalhas mais velhas retornam 404 e o worker fica em loop de erros.
    # Batalhas que já têm task deep_process (qualquer status — done/failed
    # também, pra não recriar tasks pra batalhas já processadas).
    already_tasked = set((await db.scalars(
        select(ScanWorkTask.page_offset).where(
            ScanWorkTask.feed_type == "deep_process",
        )
    )).all())
    # page_offset é int mas albion_id é varchar — compara como strings
    already_tasked_str = {str(v) for v in already_tasked}
    candidates = (await db.scalars(
        select(Battle).where(
            Battle.processing_tier == "light",
            Battle.players_total >= DEEP_PROCESS_MIN_PLAYERS,
            ~Battle.albion_id.in_(already_tasked_str) if already_tasked_str else True,
        )
        .order_by(Battle.start_time.desc())
        .limit(needed)
    )).all()
    created = 0
    for b in candidates:
        db.add(ScanWorkTask(
            region=b.region,
            feed_type="deep_process",
            page_offset=int(b.albion_id),
            priority=1,
            status="pending",
        ))
        created += 1
    return created


async def generate_tasks(db: AsyncSession) -> int:
    await _release_expired_claims(db)
    await _retry_failed_tasks(db)
    active_workers = await count_active_workers(db)
    backfill_target = max(
        1,
        min(MAX_BACKFILL_TASKS_PER_STREAM, active_workers * BACKFILL_TASKS_PER_WORKER),
    )
    created = 0
    for region in SCAN_REGIONS:
        for feed_type in ("battles", "kills"):
            try:
                async with db.begin_nested():
                    state = await db.scalar(select(ScanStreamState).where(
                        ScanStreamState.region == region,
                        ScanStreamState.feed_type == feed_type,
                    ))
                    if state is not None and state.paused:
                        continue
                    for page in range(state.recent_pages if state is not None else RECENT_PAGES):
                        created += await _ensure_recent_task(
                            db,
                            region,
                            feed_type,
                            page * FEED_PAGE_SIZE,
                            2 if page == 0 else 1,
                        )
                    created += await _reserve_backfill_tasks(
                        db, region, feed_type, backfill_target
                    )
            except IntegrityError as exc:
                log.warning("scan_dispatcher: conflito ao agendar %s/%s: %s", region, feed_type, exc.orig)
    # Deep-process delegado: quando há workers ativos, cria tarefas pra
    # batalhas light antigas (o backend cuida das recentes via _retry_stuck).
    # prioridade 0 (baixa) — só roda quando não há feed recente pra claimar.
    if active_workers > 0:
        created += await _ensure_deep_process_tasks(db, active_workers)
    await _cleanup_history(db)
    await db.commit()
    return created


async def _claim_next(
    db: AsyncSession,
    worker_id: str,
    *,
    backfill_only: bool = False,
    recent_only: bool = False,
    prefer_latency: bool = False,
) -> ScanWorkTask | None:
    now = _now()
    query = (
        select(ScanWorkTask)
        .outerjoin(
            ScanStreamState,
            and_(
                ScanStreamState.region == ScanWorkTask.region,
                ScanStreamState.feed_type == ScanWorkTask.feed_type,
            ),
        )
        .outerjoin(
            ScanWorkerRegionMetric,
            and_(
                ScanWorkerRegionMetric.worker_id == worker_id,
                ScanWorkerRegionMetric.region == ScanWorkTask.region,
            ),
        )
        .where(
            ScanWorkTask.status == "pending",
            # deep_process não tem ScanStreamState — só aplica filtro de
            # circuit/paused pra battles/kills (que têm stream state).
            or_(
                ScanStreamState.id.is_(None),
                and_(
                    ScanStreamState.paused.is_(False),
                    or_(
                        ScanWorkTask.priority == 0,
                        ScanStreamState.circuit_state == "closed",
                        and_(
                            ScanStreamState.circuit_state == "open",
                            ScanStreamState.opened_until <= now,
                        ),
                    ),
                ),
            ),
        )
    )
    if backfill_only:
        query = query.where(ScanWorkTask.priority == 0)
    elif recent_only:
        query = query.where(ScanWorkTask.priority > 0)
    order = [ScanWorkTask.priority.desc()]
    if prefer_latency:
        order.append(ScanWorkerRegionMetric.ewma_latency_ms.asc().nullsfirst())
    order.extend((
        ScanStreamState.last_claimed_at.asc().nullsfirst(),
        ScanWorkTask.id.asc(),
    ))
    task = (await db.scalars(
        query
        .order_by(*order)
        .with_for_update(of=(ScanWorkTask,), skip_locked=True)
        .limit(1)
    )).first()
    if task is None:
        return None
    task.status = "claimed"
    task.claimed_by = worker_id
    task.lease_token = uuid.uuid4().hex
    task.attempt_count += 1
    task.claimed_at = now
    task.claim_expires_at = now + WORK_CLAIM_TTL
    # stream pode ser None (deep_process não tem ScanStreamState)
    stream = await db.scalar(select(ScanStreamState).where(
        ScanStreamState.region == task.region,
        ScanStreamState.feed_type == task.feed_type,
    ))
    if stream is not None:
        stream.last_claimed_at = now
        if stream.circuit_state == "open":
            stream.circuit_state = "half_open"
    await db.commit()
    await db.refresh(task)
    return task


async def claim_work(
    db: AsyncSession, worker_id: str, region: str | None = None
) -> ScanWorkTask | None:
    """Claim globally by priority; region is retained only for API compatibility."""
    await db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": CLAIM_LOCK_ID})
    await _release_expired_claims(db)
    held = await db.scalar(
        select(ScanWorkTask)
        .where(ScanWorkTask.claimed_by == worker_id, ScanWorkTask.status == "claimed")
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if held is not None:
        if not held.lease_token:
            held.lease_token = uuid.uuid4().hex
            held.attempt_count += 1
        held.claimed_at = _now()
        held.claim_expires_at = _now() + WORK_CLAIM_TTL
        await db.commit()
        return held

    worker = await db.scalar(select(ScanWorker).where(ScanWorker.worker_id == worker_id))
    if worker is None or worker.status == "draining":
        await db.commit()
        return None

    ingest_backlog = int(await db.scalar(
        select(func.count()).select_from(ScanIngestPayload).where(
            ScanIngestPayload.status.in_(("pending", "processing"))
        )
    ) or 0)
    active_workers = await count_active_workers(db)
    active_claims = int(await db.scalar(
        select(func.count()).select_from(ScanWorkTask).where(
            ScanWorkTask.status == "claimed"
        )
    ) or 0)
    latency_age = max(0.0, time.monotonic() - _last_foreground_activity)
    claim_limit, pressured = _parallelism_policy(
        active_workers,
        ingest_backlog,
        backend_is_idle(),
        _last_foreground_latency_ms,
        latency_age,
        async_engine.pool.checkedout(),
    )
    if active_claims >= claim_limit:
        await db.commit()
        return None
    recent_only = ingest_backlog >= INGEST_BACKPRESSURE or pressured
    backfill_due = _backfill_due(recent_only)
    prefer_latency = _affinity_due()
    task = await _claim_next(
        db, worker_id, backfill_only=backfill_due, recent_only=recent_only,
        prefer_latency=prefer_latency,
    )
    if task is None and backfill_due:
        task = await _claim_next(
            db, worker_id, recent_only=recent_only, prefer_latency=prefer_latency
        )
    if task is None:
        # Sem tarefas recentes — workers ociosos ajudam no backfill (prioridade
        # mínima). Assim que uma tarefa recente aparece (prioridade > 0), o
        # próximo worker a pega primeiro (ordem por prioridade descendente).
        task = await _claim_next(
            db, worker_id, backfill_only=True, prefer_latency=prefer_latency
        )
    if task is not None:
        return task
    return None


async def control_worker(db: AsyncSession, worker_id: str, action: str) -> None:
    worker = await db.scalar(
        select(ScanWorker).where(ScanWorker.worker_id == worker_id).with_for_update()
    )
    if worker is None:
        raise LookupError("worker not found")
    if action == "drain":
        worker.status = "draining"
    elif action == "quarantine":
        streams = (await db.execute(select(
            ScanWorkTask.region, ScanWorkTask.feed_type
        ).where(
            ScanWorkTask.claimed_by == worker_id,
            ScanWorkTask.status == "claimed",
        ).distinct())).all()
        worker.status = "quarantined"
        worker.credential_revoked = True
        await db.execute(update(ScanWorkTask).where(
            ScanWorkTask.claimed_by == worker_id,
            ScanWorkTask.status == "claimed",
        ).values(
            status="pending", claimed_by=None, lease_token=None,
            claimed_at=None, claim_expires_at=None,
        ))
        await _reopen_half_open(db, streams)
    elif action == "resume":
        worker.status = "active"
        if worker.credential_revoked:
            worker.credential_revoked = False
            worker.api_token_hash = None
    else:
        raise ValueError("invalid worker action")
    _incident(db, f"worker_{action}", actor="operator", worker_id=worker_id)
    await db.commit()


async def control_stream(db: AsyncSession, region: str, feed_type: str, action: str) -> None:
    state = await db.scalar(select(ScanStreamState).where(
        ScanStreamState.region == region,
        ScanStreamState.feed_type == feed_type,
    ).with_for_update())
    if state is None:
        raise LookupError("stream not found")
    if action not in ("pause", "resume"):
        raise ValueError("invalid stream action")
    state.paused = action == "pause"
    _incident(db, f"stream_{action}", actor="operator", region=region, feed_type=feed_type)
    await db.commit()


async def retry_task(db: AsyncSession, task_id: int) -> None:
    task = await db.scalar(
        select(ScanWorkTask).where(ScanWorkTask.id == task_id).with_for_update()
    )
    if task is None:
        raise LookupError("task not found")
    if task.status != "failed":
        raise ValueError("task is not failed")
    task.status = "pending"
    task.attempt_count = 0
    task.error_count = 0
    task.completed_at = None
    task.claimed_by = None
    task.lease_token = None
    task.claimed_at = None
    task.claim_expires_at = None
    _incident(
        db, "task_retry", actor="operator", region=task.region,
        feed_type=task.feed_type, task_id=task.id,
        details={"offset": task.page_offset},
    )
    await db.commit()


async def _release_task(db: AsyncSession, task_id: int) -> None:
    task = await db.get(ScanWorkTask, task_id)
    await db.execute(
        update(ScanWorkTask)
        .where(ScanWorkTask.id == task_id, ScanWorkTask.status == "claimed")
        .values(
            status="pending",
            claimed_by=None,
            lease_token=None,
            claimed_at=None,
            claim_expires_at=None,
        )
    )
    if task is not None:
        await _reopen_half_open(db, [(task.region, task.feed_type)])
    await db.commit()


async def report_work(
    db: AsyncSession,
    worker_id: str,
    task_id: int,
    lease_token: str,
    found_count: int,
    error_count: int,
    data: list[dict] | None = None,
    latency_ms: int | None = None,
) -> tuple[int, int]:
    task = await db.scalar(
        select(ScanWorkTask)
        .where(ScanWorkTask.id == task_id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if task is None:
        raise LookupError("tarefa não encontrada")
    if (
        task.status == "reported"
        and task.claimed_by == worker_id
        and task.lease_token
        and secrets.compare_digest(task.lease_token, lease_token)
    ):
        queued = await db.scalar(
            select(ScanIngestPayload).where(ScanIngestPayload.task_id == task.id)
        )
        return (len(queued.payload), 0) if queued is not None else (0, 0)
    if (
        task.status != "claimed"
        or task.claimed_by != worker_id
        or not task.lease_token
        or not secrets.compare_digest(task.lease_token, lease_token)
        or not task.claim_expires_at
        or task.claim_expires_at <= _now()
    ):
        raise PermissionError("stale lease")
    if data is not None and len(data) > FEED_PAGE_SIZE and task.feed_type != "deep_process":
        raise ValueError("página maior que o limite do feed")
    if latency_ms is not None:
        metric = await db.scalar(select(ScanWorkerRegionMetric).where(
            ScanWorkerRegionMetric.worker_id == worker_id,
            ScanWorkerRegionMetric.region == task.region,
        ).with_for_update())
        if metric is None:
            metric = ScanWorkerRegionMetric(
                worker_id=worker_id, region=task.region,
                samples=0, successes=0, errors=0,
            )
            db.add(metric)
        failed = bool(error_count and not data)
        metric.samples += 1
        metric.successes += 0 if failed else 1
        metric.errors += 1 if failed else 0
        metric.ewma_latency_ms = _next_latency(metric.ewma_latency_ms, latency_ms)
        metric.last_seen_at = _now()
    if error_count and not data:
        await _record_stream_result(db, task, False)
        task.status = "failed"
        task.claimed_by = None
        task.lease_token = None
        task.claimed_at = None
        task.claim_expires_at = None
        task.completed_at = _now()
        task.error_count += error_count
        if task.priority > 0 and task.attempt_count == 5:
            _incident(
                db, "page_failed", worker_id=worker_id, region=task.region,
                feed_type=task.feed_type, task_id=task.id,
                details={"offset": task.page_offset, "attempts": task.attempt_count},
            )
        worker = await db.scalar(
            select(ScanWorker).where(ScanWorker.worker_id == worker_id)
        )
        if worker is not None:
            worker.total_errors += error_count
            worker.last_errors = error_count
            worker.last_found = 0
            worker.last_task_at = _now()
            worker.last_heartbeat = _now()
        await db.commit()
        return 0, error_count
    payload = data or []
    await _record_stream_result(db, task, True, len(payload))
    queued = await db.scalar(
        select(ScanIngestPayload)
        .where(ScanIngestPayload.task_id == task.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if queued is None:
        db.add(ScanIngestPayload(
            task_id=task.id,
            worker_id=worker_id,
            region=task.region,
            feed_type=task.feed_type,
            payload=payload,
            status="pending",
            next_attempt_at=_now(),
        ))
    else:
        queued.worker_id = worker_id
        queued.payload = payload
        queued.status = "pending"
        queued.attempt_count = 0
        queued.next_attempt_at = _now()
        queued.created_at = _now()
        queued.started_at = None
        queued.completed_at = None
        queued.error = None
    task.status = "reported"
    task.claim_expires_at = None
    await db.commit()
    return len(payload), 0


async def _apply_ingest_payload(
    db: AsyncSession, task: ScanWorkTask, ingest: ScanIngestPayload
) -> tuple[int, int]:
    accepted = 0
    errors = 0
    payload = ingest.payload or []
    if task.feed_type == "deep_process":
        # Worker buscou detail + events da batalha via albion_id (guardado em
        # page_offset). Resolve albion_id → Battle.id interno pra _write_deep_data.
        from app.services.battle_tracker import _write_deep_data
        for item in payload:
            if not item.get("_deep_process"):
                continue
            albion_id = item.get("battle_id")
            raw = item.get("raw")
            events = item.get("events") or []
            if not albion_id or raw is None:
                errors += 1
                continue
            battle = await db.scalar(
                select(Battle).where(
                    Battle.albion_id == str(albion_id),
                    Battle.region == task.region,
                )
            )
            if battle is None:
                errors += 1
                continue
            try:
                ok = await asyncio.to_thread(_write_deep_data, battle.id, raw, events)
                if ok:
                    accepted += 1
                else:
                    b = await db.get(Battle, battle.id)
                    if b is not None:
                        b.reprocess_reason = b.reprocess_reason or "deep_process_empty"
                    errors += 1
            except Exception as exc:
                log.warning("scan_dispatcher: deep_process %s: %s", albion_id, exc)
                b = await db.get(Battle, battle.id)
                if b is not None:
                    b.reprocess_reason = b.reprocess_reason or "deep_process_failed"
                errors += 1
    elif task.feed_type == "battles":
        # Worker pages podem chegar fora de ordem. Guardá-las no inbox evita que
        # uma página posterior publique antes da fronteira da stream local.
        for raw in payload:
            try:
                accepted += await capture_discovered_items(
                    db,
                    kind=KIND_BATTLE,
                    region=task.region,
                    rows=[raw],
                    source_id=_battle_source_id,
                    occurred_at=_battle_occurred_at,
                )
            except Exception as exc:
                log.warning("scan_dispatcher: captura battle %s: %s", raw.get("id"), exc)
                errors += 1
    else:
        for event in payload:
            try:
                accepted += await capture_discovered_items(
                    db,
                    kind=KIND_KILL,
                    region=task.region,
                    rows=[event],
                    source_id=_event_source_id,
                    occurred_at=_event_occurred_at,
                )
            except Exception as exc:
                log.debug("scan_dispatcher: captura kill %s/%s: %s", task.region, event.get("EventId"), exc)
                errors += 1
    return accepted, errors


async def ingest_one() -> bool:
    async with AsyncSessionLocal() as db:
        ingest = await db.scalar(
            select(ScanIngestPayload)
            .where(
                ScanIngestPayload.status == "pending",
                ScanIngestPayload.next_attempt_at <= _now(),
            )
            .order_by(ScanIngestPayload.id.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if ingest is None:
            return False
        ingest.status = "processing"
        ingest.started_at = _now()
        ingest.attempt_count += 1
        ingest_id = ingest.id
        task_id = ingest.task_id
        await db.commit()

    try:
        async with AsyncSessionLocal() as db:
            ingest = await db.get(ScanIngestPayload, ingest_id)
            task = await db.get(ScanWorkTask, task_id)
            if ingest is None or task is None or task.status != "reported":
                raise RuntimeError("ingest sem task reported")
            accepted, errors = await _apply_ingest_payload(db, task, ingest)
            if task.page_offset == 0:
                state = await db.scalar(select(ScanStreamState).where(
                    ScanStreamState.region == task.region,
                    ScanStreamState.feed_type == task.feed_type,
                ).with_for_update())
                if state is not None:
                    observed_at = _now()
                    if state.last_head_at is not None:
                        state.window_items_per_min = _next_window_rate(
                            state.window_items_per_min,
                            accepted,
                            (observed_at - state.last_head_at).total_seconds(),
                        )
                    state.last_head_at = observed_at
            task.status = "done"
            task.lease_token = None
            task.claimed_by = None
            task.claimed_at = None
            task.completed_at = _now()
            task.found_count = accepted
            task.missing_count = 0
            task.error_count = errors
            if task.lap_id is not None:
                lap = await db.scalar(
                    select(ScanLap).where(ScanLap.id == task.lap_id).with_for_update()
                )
                if lap is not None and lap.status == "active":
                    lap.completed_pages += 1
                    lap.last_progress_at = _now()
                    if lap.completed_pages >= lap.expected_pages:
                        lap.status = "done"
                        lap.completed_at = _now()
            ingest.status = "done"
            ingest.completed_at = _now()
            ingest.error = None
            worker = await db.scalar(
                select(ScanWorker).where(ScanWorker.worker_id == ingest.worker_id)
            )
            if worker is not None:
                worker.total_tasks_done += 1
                if task.feed_type == "battles":
                    worker.total_battles_found += accepted
                else:
                    worker.total_kills_found += accepted
                worker.total_errors += errors
                worker.last_found = accepted
                worker.last_missing = 0
                worker.last_errors = errors
                worker.last_task_at = _now()
            await db.commit()
        return True
    except Exception as exc:
        async with AsyncSessionLocal() as db:
            ingest = await db.get(ScanIngestPayload, ingest_id)
            task = await db.get(ScanWorkTask, task_id)
            if ingest is not None:
                ingest.error = str(exc)[:2000]
                if ingest.attempt_count >= INGEST_MAX_ATTEMPTS:
                    ingest.status = "failed"
                    ingest.completed_at = _now()
                    if task is not None:
                        task.status = "failed"
                        task.lease_token = None
                        task.claimed_by = None
                        task.claimed_at = None
                        task.completed_at = _now()
                else:
                    ingest.status = "pending"
                    ingest.next_attempt_at = _now() + timedelta(
                        seconds=min(300, 5 * (2 ** ingest.attempt_count))
                    )
            await db.commit()
        log.warning("scan_dispatcher: ingest %s failed: %s", ingest_id, exc)
        return True


async def renew_lease(
    db: AsyncSession, worker_id: str, task_id: int, lease_token: str
) -> datetime:
    task = await db.scalar(
        select(ScanWorkTask)
        .where(ScanWorkTask.id == task_id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if (
        task is None
        or task.status != "claimed"
        or task.claimed_by != worker_id
        or not task.lease_token
        or not secrets.compare_digest(task.lease_token, lease_token)
        or not task.claim_expires_at
        or task.claim_expires_at <= _now()
    ):
        raise PermissionError("stale lease")
    task.claimed_at = _now()
    task.claim_expires_at = _now() + WORK_CLAIM_TTL
    await db.commit()
    return task.claim_expires_at


async def get_worker_stats(db: AsyncSession) -> dict:
    now = _now()
    visible_since = now - DEAD_WORKER_RETENTION
    workers = (await db.scalars(
        select(ScanWorker)
        .where(ScanWorker.last_heartbeat >= visible_since)
        .order_by(ScanWorker.status.asc(), ScanWorker.name.asc())
    )).all()
    rows = []
    for worker in workers:
        age = (now - worker.last_heartbeat).total_seconds() if worker.last_heartbeat else None
        rows.append({
            "worker_id": worker.worker_id,
            "name": worker.name,
            "region_pref": "global",
            "status": worker.status,
            "last_heartbeat_age_s": round(age, 1) if age is not None else None,
            "total_tasks_done": worker.total_tasks_done,
            "total_battles_found": worker.total_battles_found,
            "total_kills_found": worker.total_kills_found,
            "total_errors": worker.total_errors,
            "last_found": worker.last_found,
            "last_missing": worker.last_missing,
            "last_errors": worker.last_errors,
            "last_task_at": worker.last_task_at.isoformat() if worker.last_task_at else None,
        })

    status_counts = {
        "pending": 0, "claimed": 0, "reported": 0, "done": 0, "failed": 0
    }
    live_task_statuses = ("pending", "claimed", "reported")
    for status, count in (await db.execute(
        select(ScanWorkTask.status, func.count())
        .where(ScanWorkTask.status.in_(live_task_statuses))
        .group_by(ScanWorkTask.status)
    )).all():
        status_counts[status] = count or 0

    per_region: dict[str, dict[str, int]] = {}
    for region, feed_type, status, count in (await db.execute(
        select(ScanWorkTask.region, ScanWorkTask.feed_type, ScanWorkTask.status, func.count())
        .where(ScanWorkTask.status.in_(live_task_statuses))
        .group_by(ScanWorkTask.region, ScanWorkTask.feed_type, ScanWorkTask.status)
    )).all():
        key = f"{region}/{feed_type}"
        per_region.setdefault(key, {
            "pending": 0, "claimed": 0, "reported": 0, "done": 0, "failed": 0
        })
        per_region[key][status] = count or 0

    active = await count_active_workers(db)
    backfill_target = max(
        1, min(MAX_BACKFILL_TASKS_PER_STREAM, active * BACKFILL_TASKS_PER_WORKER)
    )
    cursors = {}
    for region in SCAN_REGIONS:
        battle_cursor = await db.get(BattleSyncCursor, region)
        kill_cursor = await db.get(KillSyncCursor, region)
        cursors[region] = {
            "battles": battle_cursor.next_offset if battle_cursor else RECENT_PAGES * FEED_PAGE_SIZE,
            "kills": kill_cursor.next_offset if kill_cursor else RECENT_PAGES * FEED_PAGE_SIZE,
        }
    ingest_counts = {"pending": 0, "processing": 0, "done": 0, "failed": 0}
    for status, count in (await db.execute(
        select(ScanIngestPayload.status, func.count()).group_by(ScanIngestPayload.status)
    )).all():
        ingest_counts[status] = count or 0
    oldest_ingest = await db.scalar(
        select(func.min(ScanIngestPayload.created_at)).where(
            ScanIngestPayload.status.in_(("pending", "processing"))
        )
    )
    ingest_counts["oldest_age_s"] = (
        round((now - oldest_ingest).total_seconds(), 1) if oldest_ingest else 0
    )
    latency_age = max(0.0, time.monotonic() - _last_foreground_activity)
    claim_limit, claim_pressured = _parallelism_policy(
        active,
        ingest_counts["pending"] + ingest_counts["processing"],
        backend_is_idle(),
        _last_foreground_latency_ms,
        latency_age,
        async_engine.pool.checkedout(),
    )
    circuits = {}
    stream_states = (await db.scalars(
        select(ScanStreamState).order_by(ScanStreamState.region, ScanStreamState.feed_type)
    )).all()
    for state in stream_states:
        circuits[f"{state.region}/{state.feed_type}"] = {
            "state": state.circuit_state,
            "consecutive_errors": state.consecutive_errors,
            "opened_until": state.opened_until.isoformat() if state.opened_until else None,
            "recent_pages": state.recent_pages,
            "window_items_per_min": round(state.window_items_per_min, 2) if state.window_items_per_min is not None else None,
            "last_head_at": state.last_head_at.isoformat() if state.last_head_at else None,
            "paused": state.paused,
        }
    laps = {}
    alerts = []
    for lap in (await db.scalars(
        select(ScanLap)
        .order_by(ScanLap.started_at.desc())
    )).all():
        key = f"{lap.region}/{lap.feed_type}"
        if key in laps:
            continue
        last_progress = lap.last_progress_at
        progress_age = (now - (last_progress or lap.started_at)).total_seconds()
        if lap.status == "active" and progress_age >= 900:
            alerts.append({"type": "lap_stalled", "stream": key, "age_s": round(progress_age)})
        laps[key] = {
            "lap_id": lap.id,
            "status": lap.status,
            "expected_pages": lap.expected_pages,
            "completed_pages": lap.completed_pages,
            "page_stride": lap.page_stride,
            "overlap_items": FEED_PAGE_SIZE - lap.page_stride,
            "started_at": lap.started_at.isoformat(),
            "completed_at": lap.completed_at.isoformat() if lap.completed_at else None,
            "last_progress_at": last_progress.isoformat() if last_progress else None,
        }
        elapsed = max(1.0, ((lap.completed_at or now) - lap.started_at).total_seconds())
        rate = lap.completed_pages * 60 / elapsed
        laps[key]["pages_per_min"] = round(rate, 2)
        laps[key]["eta_s"] = (
            round((lap.expected_pages - lap.completed_pages) / rate * 60)
            if rate > 0 and lap.status == "active"
            else None
        )
    for task in (await db.scalars(select(ScanWorkTask).where(
        ScanWorkTask.status == "failed",
        ScanWorkTask.priority > 0,
        ScanWorkTask.attempt_count >= 5,
    ))).all():
        alerts.append({
            "type": "page_failed",
            "stream": f"{task.region}/{task.feed_type}",
            "offset": task.page_offset,
            "attempts": task.attempt_count,
        })
    sla = {}
    for state in stream_states:
        key = f"{state.region}/{state.feed_type}"
        lap = laps.get(key)
        scan_rate = (
            lap["pages_per_min"] * lap["page_stride"] if lap is not None else 0.0
        )
        window_rate = state.window_items_per_min
        ratio = scan_rate / window_rate if window_rate and window_rate > 0 else None
        recent_age = (
            (now - state.last_head_at).total_seconds() if state.last_head_at else None
        )
        stale_after = RECENT_INTERVAL[state.feed_type].total_seconds() * 2
        status = "unknown"
        if state.circuit_state != "closed" or (recent_age is not None and recent_age > stale_after):
            status = "critical"
        elif ratio is not None:
            status = "healthy" if ratio >= 1 else "at_risk"
        sla[key] = {
            "status": status,
            "recent_age_s": round(recent_age, 1) if recent_age is not None else None,
            "window_items_per_min": round(window_rate, 2) if window_rate is not None else None,
            "scan_items_per_min": round(scan_rate, 2),
            "coverage_ratio": round(ratio, 2) if ratio is not None else None,
        }
    incidents = [{
        "id": incident.id,
        "event": incident.event,
        "actor": incident.actor,
        "worker_id": incident.worker_id,
        "stream": (
            f"{incident.region}/{incident.feed_type}"
            if incident.region and incident.feed_type else None
        ),
        "task_id": incident.task_id,
        "details": incident.details,
        "created_at": incident.created_at.isoformat(),
    } for incident in (await db.scalars(
        select(ScanIncident).order_by(ScanIncident.id.desc()).limit(20)
    )).all()]
    affinity = {}
    for metric in (await db.scalars(select(ScanWorkerRegionMetric).order_by(
        ScanWorkerRegionMetric.worker_id, ScanWorkerRegionMetric.region
    ))).all():
        affinity.setdefault(metric.worker_id, {})[metric.region] = {
            "samples": metric.samples,
            "successes": metric.successes,
            "errors": metric.errors,
            "ewma_latency_ms": round(metric.ewma_latency_ms, 1) if metric.ewma_latency_ms is not None else None,
        }
    # Contagem de batalhas por processing_tier por região — light = descoberta
    # mas sem eventos/deep-process, deep = processada. A diferença é a fila de
    # processamento pendente que o embed de monitoring mostra.
    processing: dict[str, dict[str, int]] = {}
    for region, tier, count in (await db.execute(
        select(Battle.region, Battle.processing_tier, func.count())
        .where(Battle.processing_tier.in_(("light", "deep")))
        .group_by(Battle.region, Battle.processing_tier)
    )).all():
        processing.setdefault(region, {"light": 0, "deep": 0})
        processing[region][tier] = count or 0

    inbox: dict[str, dict[str, object]] = {}
    for region, status, count, latest in (await db.execute(
        select(
            NativeFeedItem.region,
            NativeFeedItem.status,
            func.count(),
            func.max(NativeFeedItem.occurred_at),
        )
        .where(NativeFeedItem.kind == KIND_BATTLE)
        .group_by(NativeFeedItem.region, NativeFeedItem.status)
    )).all():
        entry = inbox.setdefault(region, {"pending": 0, "latest_pending_at": None})
        if status != "applied":
            entry["pending"] = int(entry["pending"]) + (count or 0)
            entry["latest_pending_at"] = latest.isoformat() if latest else None

    # Última batalha deep/light processada por região (para debugging de progresso)
    processing_latest: dict[str, dict[str, object]] = {}
    for region, tier, albion_id, start_time in (await db.execute(
        select(Battle.region, Battle.processing_tier, Battle.albion_id, Battle.start_time)
        .where(Battle.processing_tier.in_(("light", "deep")))
        .order_by(Battle.region, Battle.processing_tier, Battle.start_time.desc())
    )).all():
        processing_latest.setdefault(region, {"light": None, "deep": None})
        if processing_latest[region][tier] is None:
            processing_latest[region][tier] = {"albion_id": albion_id, "start_time": start_time.isoformat() if start_time else None}

    return {
        "workers": rows,
        "tasks": status_counts,
        "per_region": per_region,
        "cursors": cursors,
        "ingest": ingest_counts,
        "circuits": circuits,
        "laps": laps,
        "alerts": alerts,
        "sla": sla,
        "incidents": incidents,
        "affinity": affinity,
        "processing": processing,
        "inbox": inbox,
        "processing_latest": processing_latest,
        "strategy": {
            "active_vps": active,
            "mode": "fallback" if active == 0 else "assist" if backend_is_idle() else "coordinator",
            "recent_pages": RECENT_PAGES,
            "backfill_tasks_per_stream": backfill_target,
            "foreground_inflight": _foreground_inflight,
            "vps_claim_limit": claim_limit,
            "claim_pressured": claim_pressured,
            "db_checked_out": async_engine.pool.checkedout(),
        },
    }


async def run_forever() -> None:
    log.info("scan_dispatcher: coordinator started")
    while True:
        async with AsyncSessionLocal() as db:
            try:
                await mark_dead_workers(db)
                created = await generate_tasks(db)
                if created:
                    log.info("scan_dispatcher: %d tasks scheduled", created)
            except Exception as exc:
                log.error("scan_dispatcher: scheduler: %s", exc)
                await db.rollback()
        await asyncio.sleep(SCAN_DISPATCHER_INTERVAL)


async def run_ordered_recovery_forever() -> None:
    """Mantém âncoras quando workers são a fonte principal das páginas cruas.

    Workers continuam buscando o grosso do feed; este verificador serial só
    percorre a fronteira persistida, para que o inbox distribuído tenha uma
    conclusão segura antes de aplicar qualquer item.
    """
    from app.services.battle_tracker import _capture_and_apply_battle_stream
    from app.services.player_tracker import _capture_and_apply_kill_stream

    log.info("scan_dispatcher: verificador ordenado de streams iniciado")
    while True:
        async with AsyncSessionLocal() as db:
            try:
                async with make_client() as client:
                    for region, host in HOSTS.items():
                        await _capture_and_apply_battle_stream(
                            client, db, region, host, page_budget=1, priority=OTHER,
                        )
                        await _capture_and_apply_kill_stream(
                            client, db, region, host, page_budget=1, priority=OTHER,
                        )
            except Exception as exc:
                log.error("scan_dispatcher: verificador ordenado: %s", exc)
                await db.rollback()
        await asyncio.sleep(ORDERED_RECOVERY_INTERVAL)


async def run_ingest_forever(web_is_idle: Callable[[], Awaitable[bool]]) -> None:
    """Bounded durable consumer. On Postgres writes don't block reads, so we
    always drain when there's backlog — the web_is_idle gate (kept for the
    callback signature) is no longer the bottleneck it was on SQLite."""
    log.info("scan_dispatcher: ingest consumer started")
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(ScanIngestPayload)
                    .where(
                        ScanIngestPayload.status == "processing",
                        ScanIngestPayload.started_at < _now() - timedelta(minutes=5),
                    )
                    .values(status="pending", next_attempt_at=_now())
                )
                backlog = int(await db.scalar(
                    select(func.count()).select_from(ScanIngestPayload).where(
                        ScanIngestPayload.status.in_(("pending", "processing"))
                    )
                ) or 0)
                await db.commit()
            processed = await ingest_one() if backlog else False
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("scan_dispatcher: consumidor de ingest falhou: %s", exc)
            processed = False
        await asyncio.sleep(0 if processed else 1)


async def _fetch_task(task: ScanWorkTask) -> list[dict]:
    host = HOSTS[task.region]
    async with make_client() as client:
        if task.feed_type == "battles":
            return await fetch_battles(client, host, offset=task.page_offset)
        if task.feed_type == "deep_process":
            # page_offset guarda o albion_id (não o battle.id interno).
            albion_id = str(task.page_offset)
            raw = await fetch_battle_detail(client, host, albion_id)
            if raw is None:
                return []
            events = await fetch_events(client, host, albion_id)
            # Empacota como um único item; battle_id = albion_id aqui,
            # _apply_ingest_payload resolve pra Battle.id interno.
            return [{"_deep_process": True, "battle_id": albion_id, "raw": raw, "events": events}]
        async with slot(host):
            response = await client.get(
                f"https://{host}/api/gameinfo/events",
                params={"limit": FEED_PAGE_SIZE, "offset": task.page_offset},
            )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []


async def run_idle_worker_forever(web_is_idle: Callable[[], Awaitable[bool]]) -> None:
    """Use spare backend capacity without competing with user-facing requests."""
    log.info("scan_dispatcher: idle backend helper started")
    await asyncio.sleep(15)
    while True:
        task = None
        active_vps = 0
        try:
            async with AsyncSessionLocal() as db:
                active_vps = await count_active_workers(db)
                ingest_backlog = int(await db.scalar(
                    select(func.count()).select_from(ScanIngestPayload).where(
                        ScanIngestPayload.status.in_(("pending", "processing"))
                    )
                ) or 0)
                if ingest_backlog:
                    await asyncio.sleep(2)
                    continue
                if active_vps > 0 and not await web_is_idle():
                    await asyncio.sleep(2)
                    continue
                task = await _claim_next(
                    db,
                    _BACKEND_WORKER_ID,
                    backfill_only=active_vps > 0,
                )
            if task is None:
                await asyncio.sleep(5)
                continue
            payload = await _fetch_task(task)
            if active_vps > 0 and not await web_is_idle():
                async with AsyncSessionLocal() as db:
                    await _release_task(db, task.id)
                await asyncio.sleep(2)
                continue
            async with AsyncSessionLocal() as db:
                queued, errors = await report_work(
                    db,
                    _BACKEND_WORKER_ID,
                    task.id,
                    task.lease_token or "",
                    len(payload),
                    0,
                    data=payload,
                )
            log.info(
                "scan_dispatcher: backend helper %s/%s offset=%d queued=%d errors=%d vps=%d",
                task.region,
                task.feed_type,
                task.page_offset,
                queued,
                errors,
                active_vps,
            )
        except Exception as exc:
            log.debug("scan_dispatcher: backend helper: %s", exc)
            if task is not None:
                async with AsyncSessionLocal() as db:
                    await _release_task(db, task.id)
            await asyncio.sleep(5)
        await asyncio.sleep(3 if active_vps == 0 else 15)
