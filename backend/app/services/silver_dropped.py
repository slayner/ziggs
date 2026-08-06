"""Precifica player_kill_events.silver_dropped em background — mesmo cálculo
de routes/players._silver_dropped (preço dos itens equipados + carregados da
vítima via services.prices.get_battle_prices, cache permanente), mas aplicado
no processamento em vez de só on-demand ao abrir o perfil.

Sem isso, o ranking de highscore "mais prata dropada" precisaria precificar
TODAS as mortes de TODOS os jogadores a cada request — inviável. O worker
varre eventos com silver_dropped=0 e fame>0 (mesmo critério de "vale atividade"
do perfil, ver _counts_for_activity), precifica em lotes e grava. Uma vez
precificado, o evento nunca é reprocessado (silver_dropped>0 marca como feito;
eventos sem gear ficam 0 de verdade e NÃO reprocessam — ver _BARE_SEED).

Mesma doutrina de battle_price_reprocessor: roda dentro do processo da API
(não disputa lock do SQLite com um segundo processo), aos poucos, sem competir
por rate limit com o resto dos serviços de fundo. Quando não sobra nada pra
fazer, fica ocioso (IDLE_INTERVAL)."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncSessionLocal
from app.models.players import PlayerKillEvent
from app.services.lethality import is_likely_lethal
from app.services.prices import get_battle_prices

log = logging.getLogger(__name__)

BATCH_SIZE = 50      # eventos por ciclo — get_battle_prices agrupa todos os itens do lote em poucas chamadas de API
BUSY_INTERVAL = 5    # ainda devagar de propósito (soma à taxa agregada dos outros serviços)
IDLE_INTERVAL = 300  # entre ciclos quando o backlog acabou
# Só precifica eventos recentes — kills antigas sem preço ficam NULL e são
# precificadas on-demand ao abrir o perfil. Sem isso o worker cava 600k de
# backlog histórico antes de chegar na kill que acabou de acontecer.
RECENT_WINDOW = timedelta(hours=6)

# silver_dropped NULL = pendente (ainda não precificado). 0 = já precificado e
# deu zero (vítima sem gear, ou gear sem cotação), >0 = prata real. O worker só
# processa NULL — assim evento precificado-zero (gear sem preço) não é
# reprocessado todo ciclo. O filtro por "tem gear" é em Python porque
# victim_equipment é JSON; "sem gear alguma" (vítima pelada) também fica NULL
# pra sempre, mas nunca é candidato (não tem gear p/_has_gear) — não é bug,
# apenas nunca ganha silver (correto, não tinha nada pra dropar).
def _has_gear(ev: PlayerKillEvent) -> bool:
    eq = ev.victim_equipment or {}
    if any(slot and slot.get("Type") for slot in eq.values()):
        return True
    return any(inv and inv.get("Type") for inv in (ev.victim_inventory or []))


async def _price_events(db: AsyncSession, events: list[PlayerKillEvent]) -> int:
    """Precifica e grava silver_dropped nos eventos do lote. Devolve quantos
    foram atualizados (todos com gear; podem ficar 0 se preço ausente, mas
    nunca reprocessam — já foram escritos)."""
    pairs: list[tuple[str, int]] = []
    for ev in events:
        for item in (ev.victim_equipment or {}).values():
            if item and item.get("Type"):
                pairs.append((item["Type"], 1))
        for inv in (ev.victim_inventory or []):
            if inv and inv.get("Type"):
                pairs.append((inv["Type"], inv.get("Count") or 1))
    if not pairs:
        # Sem gear — não devia chegar aqui (filtro _has_gear), mas defesa.
        return 0
    item_ids = list({iid for iid, _ in pairs})
    t0 = time.monotonic()
    price_by_id = await get_battle_prices(db, item_ids)
    t_fetch = time.monotonic() - t0

    updated = 0
    for ev in events:
        total = 0
        for item in (ev.victim_equipment or {}).values():
            if item and item.get("Type"):
                total += price_by_id.get(item["Type"], 0)
        for inv in (ev.victim_inventory or []):
            if inv and inv.get("Type"):
                total += price_by_id.get(inv["Type"], 0) * (inv.get("Count") or 1)
        ev.silver_dropped = total
        updated += 1
    t1 = time.monotonic()
    await db.commit()
    t_commit = time.monotonic() - t1
    if t_fetch > 2.0 or t_commit > 1.0:
        log.warning("silver_dropped: LENTO — %d eventos, fetch=%.1fs commit=%.1fs (%d itens)",
                    len(events), t_fetch, t_commit, len(item_ids))
    else:
        log.info("silver_dropped: %d eventos, fetch=%.1fs commit=%.1fs (%d itens)",
                 len(events), t_fetch, t_commit, len(item_ids))
    return updated


async def _process_batch(db: AsyncSession) -> int:
    # Lê o lote em ordem e marca rejeitados com zero: deixá-los NULL faria os
    # mesmos primeiros eventos bloquearem a fila para sempre. Histórico sem
    # group_member_count também passa pela estimativa conservadora.
    rows = list((await db.scalars(
        select(PlayerKillEvent)
        .where(
            PlayerKillEvent.silver_dropped.is_(None),
            PlayerKillEvent.timestamp > datetime.now(timezone.utc) - RECENT_WINDOW,
        )
        .order_by(PlayerKillEvent.id.desc())
        .limit(BATCH_SIZE)
    )).all())
    candidates = []
    for ev in rows:
        if _has_gear(ev) and is_likely_lethal(
            ev.fame, ev.victim_equipment, ev.group_member_count,
        ):
            candidates.append(ev)
        else:
            ev.silver_dropped = 0
    if not candidates:
        await db.commit()
        return len(rows)
    # Libera read tx antes do HTTP (get_battle_prices faz chamadas à AODP).
    # Read tx aberta durante await impede wal_checkpoint → WAL cresce →
    # commit futuro fsync-o inteiro.
    await db.commit()
    await _price_events(db, candidates)
    return len(rows)


async def run_forever() -> None:
    log.info("silver_dropped: iniciando (precificação de player_kill_events)")
    # ponytail: folga de 30s no boot — não brigar com os outros serviços
    # acordando (battle_tracker/backfill/sweeper fazem rajada de requests ao
    # subir). O backlog de silver é tolerante (já esperou desde sempre).
    await asyncio.sleep(30)
    while True:
        n = 0
        t_cycle = time.monotonic()
        async with AsyncSessionLocal() as db:
            try:
                n = await _process_batch(db)
                t_total = time.monotonic() - t_cycle
                if n:
                    if t_total > 10.0:
                        log.warning("silver_dropped: CICLO LENTO — %d eventos em %.1fs", n, t_total)
                    else:
                        log.info("silver_dropped: %d eventos em %.1fs", n, t_total)
            except Exception as e:
                await db.rollback()
                log.error("silver_dropped: erro: %s", e)
        await asyncio.sleep(BUSY_INTERVAL if n > 0 else IDLE_INTERVAL)
