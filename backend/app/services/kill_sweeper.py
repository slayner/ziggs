"""Background task — busca kill events por ID que caíram fora da janela de
paginação do feed global.

O endpoint `/api/gameinfo/events?limit=51&offset=N` tem teto duro de offset
999 — além disso HTTP 500. Cada região acumula >1k events em poucos dias,
então events antigos saem da janela e o polling nunca os alcança.

O endpoint de DETALHE `/api/gameinfo/events/{eventId}` não tem esse teto.
EventIds são um CONTADOR SEQUENCIAL POR REGIÃO (cada host regional tem a
própria sequência). Logo, o espaço de busca de uma região é exatamente os
BURACOS entre IDs conhecidos — mesmo padrão do battle_sweeper.

Por ciclo:
  1. Por região: enumera buracos entre EventIds conhecidos (do mais novo pro
     mais antigo), mais uma janela abaixo do menor ID.
  2. Sonda cada candidato no host da própria região.
  3. Ingeri os válidos via _record_kill_event (mesmo pipeline do player_tracker).
  4. Memoriza cada ID em KillIdProbe (found/missing) pra nunca re-sondar.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncSessionLocal
from app.models.players import PlayerKillEvent, KillIdProbe
from app.services.albion_gate import OTHER, albion_scope, slot
from app.services.player_tracker import HOSTS, make_client, _record_kill_event, _upsert_event_players

log = logging.getLogger(__name__)

BELOW_MIN_WINDOW = 200
MAX_CANDIDATES_PER_CYCLE = 600
CYCLE_INTERVAL = 180
MAX_429_RETRIES = 3

# Companion-aware: quando há companions ativos sondando kills, o sweeper reduz
# seus candidatos — eles cobrem os mesmos buracos de graça (IP deles).
COMPANION_AWARE_DIVISOR = 4


def _region_kill_candidates(
    ids_desc: list[int],
    probed: set[int],
    limit: int,
) -> list[int]:
    """Buracos da sequência de UMA região, do mais novo pro mais antigo."""
    out: list[int] = []
    if not ids_desc:
        return out
    # Buracos entre IDs conhecidos
    for i in range(len(ids_desc) - 1):
        hi, lo = ids_desc[i], ids_desc[i + 1]
        for c in range(hi - 1, lo, -1):
            if c not in probed:
                out.append(c)
                if len(out) >= limit:
                    return out
    # Janela abaixo do menor ID
    bottom = ids_desc[-1]
    for c in range(bottom - 1, bottom - BELOW_MIN_WINDOW - 1, -1):
        if c <= 0:
            break
        if c not in probed:
            out.append(c)
            if len(out) >= limit:
                return out
    return out


async def generate_kill_candidates(db: AsyncSession, active_companions: int = 0) -> list[tuple[str, int]]:
    """[(region, albion_event_id_int), ...] — buracos por região.

    `active_companions`: se >0, reduz o teto — companions ativos sondam os
    mesmos buracos de graça (IP deles), o sweeper gasta menos do nosso rate limit.
    """
    limit = MAX_CANDIDATES_PER_CYCLE
    if active_companions > 0:
        limit = max(50, MAX_CANDIDATES_PER_CYCLE // COMPANION_AWARE_DIVISOR)
        log.info("kill_sweeper: %d companion(s) ativo(s) — teto reduzido pra %d candidatos",
                 active_companions, limit)

    probed: set[int] = set()
    for x in (await db.scalars(select(KillIdProbe.albion_event_id))):
        try:
            probed.add(int(x))
        except (TypeError, ValueError):
            continue

    per_region_limit = max(1, limit // len(HOSTS))
    out: list[tuple[str, int]] = []
    for region in HOSTS:
        raw = (await db.scalars(
            select(PlayerKillEvent.albion_event_id)
            .where(PlayerKillEvent.region == region)
        )).all()
        ids: set[int] = set()
        for a in raw:
            try:
                ids.add(int(a))
            except (TypeError, ValueError):
                continue
        if not ids:
            continue
        ids_desc = sorted(ids, reverse=True)
        for c in _region_kill_candidates(ids_desc, probed | ids, per_region_limit):
            out.append((region, c))
    return out[:limit]


async def _probe_kill_event(
    client: httpx.AsyncClient, host: str, event_id: str,
) -> tuple[str, dict | None]:
    """Sonda UM evento. Retorna ('found', data) | ('missing', None) | ('error', None)."""
    url = f"https://{host}/api/gameinfo/events/{event_id}"
    for attempt in range(MAX_429_RETRIES):
        try:
            async with slot(host):
                resp = await client.get(url)
        except httpx.RequestError:
            return "error", None
        if resp.status_code == 200:
            try:
                data = resp.json()
            except ValueError:
                return "error", None
            if isinstance(data, dict) and data.get("EventId"):
                return "found", data
            return "error", None
        if resp.status_code == 404:
            return "missing", None
        if resp.status_code == 429:
            if attempt == MAX_429_RETRIES - 1:
                return "error", None
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.replace(".", "", 1).isdigit():
                wait = float(retry_after)
            else:
                wait = 5.0 * (attempt + 1) * random.uniform(0.7, 1.3)
            await asyncio.sleep(wait)
            continue
        return "error", None
    return "error", None


async def _probe_and_ingest(
    client: httpx.AsyncClient,
    db: AsyncSession,
    db_lock: asyncio.Lock,
    region: str,
    event_id: int,
) -> bool:
    """Sonda o candidato, ingere se achado, grava probe. Retorna True sse ingeriu."""
    eid = str(event_id)
    status, raw = await _probe_kill_event(client, HOSTS[region], eid)
    if status == "error":
        return False

    async with db_lock:
        if status == "found" and raw is not None:
            try:
                await _upsert_event_players(db, raw, region)
                await _record_kill_event(db, raw, region, commit=False)
            except Exception as e:
                log.debug("kill_sweeper: erro ao ingerir event %s (%s): %s", eid, region, e)
                await db.rollback()
                status = "missing"

        existing = await db.get(KillIdProbe, eid)
        if existing is None:
            db.add(KillIdProbe(
                albion_event_id=eid, status=status,
                region=region,
                probed_at=datetime.now(timezone.utc),
            ))
        else:
            existing.status = status
            existing.region = region
            existing.probed_at = datetime.now(timezone.utc)
        await db.commit()
    return status == "found"


async def sweep_cycle(client: httpx.AsyncClient, db: AsyncSession) -> dict:
    # Companion-aware: companions ativos sondam os mesmos buracos de graça.
    active = 0
    try:
        from app.services.companion_kill_scan import _kill_claims
        active = len(_kill_claims)
    except Exception:
        pass
    candidates = await generate_kill_candidates(db, active)
    if not candidates:
        log.info("kill_sweeper: sem candidatos novos")
        return {"candidates": 0, "found": 0}

    # Fecha a read tx aberta pelas queries de candidatos ANTES da fase de HTTP:
    # o ciclo inteiro atrás do rate limiter pode passar dos 10min do
    # idle_in_transaction_session_timeout, e o Postgres derrubaria a conexão
    # no meio do ciclo (mesma higiene do battle_sweeper.sweep_cycle).
    await db.commit()

    db_lock = asyncio.Lock()

    async def _one(region: str, eid: int) -> bool:
        try:
            return await _probe_and_ingest(client, db, db_lock, region, eid)
        except Exception as e:
            log.warning("kill_sweeper: falha ao sondar %s (%s): %r", eid, region, e)
            return False

    results = await asyncio.gather(*(_one(r, c) for r, c in candidates))
    found = sum(1 for r in results if r)
    log.info("kill_sweeper: ciclo — %d candidatos, %d achados", len(candidates), found)
    return {"candidates": len(candidates), "found": found}


async def run_forever() -> None:
    log.info("kill_sweeper: iniciando (interval=%ds)", CYCLE_INTERVAL)
    while True:
        async with AsyncSessionLocal() as db:
            try:
                async with make_client() as client:
                    async with albion_scope(OTHER):
                        await sweep_cycle(client, db)
            except Exception as e:
                log.error("kill_sweeper: erro no ciclo: %s", e)
        await asyncio.sleep(CYCLE_INTERVAL)
