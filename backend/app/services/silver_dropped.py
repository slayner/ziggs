"""Precifica player_kill_events.silver_dropped em background — mesmo cálculo
de routes/players._silver_dropped (preço dos itens equipados + carregados da
vítima via services.prices.get_battle_prices, cache de 8h), mas aplicado
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
from app.models.battles import Battle, BattleKillEvent
from app.models.players import PlayerKillEvent
from app.services.death_pricing import price_death_loadouts
from app.services.lethality import is_likely_lethal

log = logging.getLogger(__name__)

BATCH_SIZE = 50      # eventos recentes por ciclo — get_battle_prices agrupa itens em poucas chamadas de API
BATTLE_BATCH_PER_REGION = 20
# Backfill baixo e contínuo: mortes novas continuam prioritárias, mas NULL
# histórico também converge para um valor persistido sem script manual caro.
HISTORY_BATCH_PER_REGION = 5
BATTLE_HISTORY_BATCH_SIZE = 10
BUSY_INTERVAL = 5    # ainda devagar de propósito (soma à taxa agregada dos outros serviços)
IDLE_INTERVAL = 300  # entre ciclos quando o backlog acabou
# Mortes recentes têm prioridade para não atrasar juicy kills e a timeline. O
# histórico é drenado em um segundo lote menor a cada ciclo.
RECENT_WINDOW = timedelta(hours=6)
# O timestamp do evento é o horário do JOGO, não o de ingestão. Quando a API
# do Albion atrasa (americas já ficou 30h+), a kill chega aqui já "velha"
# demais pra uma janela fixa — ficava NULL pra sempre e a juicy kill nunca
# era postada. A janela é esticada POR REGIÃO pelo delay medido em
# battle_tracker.publish_delay_status (mesma API, mesmo atraso), com folga
# pra absorver deriva entre medições.
DELAY_MARGIN = timedelta(hours=1)
REGIONS = ("americas", "asia", "europe")
_last_delay_log: dict[str, float] = {}


def _recent_cutoffs() -> dict[str, datetime]:
    """Cutoff de timestamp por região: agora - RECENT_WINDOW - delay(região)
    - margem. Sem medição de delay (restart, 1º ciclo) fica só RECENT_WINDOW."""
    # Import tardio: battle_tracker é módulo pesado e não precisa subir junto.
    from app.services.battle_tracker import publish_delay_status
    now = datetime.now(timezone.utc)
    delays = publish_delay_status()
    cutoffs: dict[str, datetime] = {}
    for region in REGIONS:
        delay_secs = (delays.get(region) or {}).get("delay_secs") or 0
        cutoffs[region] = now - RECENT_WINDOW - timedelta(seconds=delay_secs) - DELAY_MARGIN
        if delay_secs > 3600 and _last_delay_log.get(region) != delay_secs:
            _last_delay_log[region] = delay_secs
            log.info(
                "silver_dropped: API %s com %.1fh de delay — janela de pricing estendida "
                "pra %s (kills atrasadas continuam sendo precificadas)",
                region, delay_secs / 3600, RECENT_WINDOW + timedelta(seconds=delay_secs),
            )
    return cutoffs

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
    t0 = time.monotonic()
    totals_by_event: dict[int, int] = {}
    basis_by_id: dict[str, str] = {}
    item_count = 0
    for region in {event.region for event in events}:
        regional_events = [event for event in events if event.region == region]
        totals, basis, count = await price_death_loadouts(
            db,
            [(event.victim_equipment, event.victim_inventory) for event in regional_events],
            region=region,
        )
        totals_by_event.update({event.id: total for event, total in zip(regional_events, totals)})
        basis_by_id.update(basis)
        item_count += count
    t_fetch = time.monotonic() - t0


    # Conta bases pra log de auditoria — quantos itens saíram de cada estágio
    # da cadeia de presunção (exact|quality|equivalent|presumed|missing).
    basis_counts: dict[str, int] = {}
    for iid in basis_by_id:
        b = basis_by_id.get(iid, "missing")
        basis_counts[b] = basis_counts.get(b, 0) + 1

    updated = 0
    event_ids = [event.id for event in events]
    projections = {
        event.player_kill_event_id: event
        for event in (await db.scalars(select(BattleKillEvent).where(
            BattleKillEvent.player_kill_event_id.in_(event_ids),
        ))).all()
    }
    for ev in events:
        total = totals_by_event[ev.id]
        ev.silver_dropped = total
        projection = projections.get(ev.id)
        if projection is not None:
            projection.silver_dropped = total
        updated += 1

    # A outbox é escrita na mesma transação da precificação: uma kill nunca
    # fica com preço calculado sem poder chegar ao bot.
    from app.services.juicy_kill_delivery import enqueue_priced_events
    await enqueue_priced_events(db, events)
    t1 = time.monotonic()
    await db.commit()
    t_commit = time.monotonic() - t1
    if t_fetch > 2.0 or t_commit > 1.0:
        log.warning("silver_dropped: LENTO — %d eventos, fetch=%.1fs commit=%.1fs (%d itens, bases=%s)",
                    len(events), t_fetch, t_commit, item_count, basis_counts)
    else:
        log.info("silver_dropped: %d eventos, fetch=%.1fs commit=%.1fs (%d itens, bases=%s)",
                 len(events), t_fetch, t_commit, item_count, basis_counts)
    return updated


async def _reconcile_battle_values(db: AsyncSession) -> int:
    """Vincula projeções de batalha ao ledger canônico e replica seu snapshot."""
    rows = (await db.execute(
        select(BattleKillEvent, PlayerKillEvent)
        .join(Battle, Battle.id == BattleKillEvent.battle_id)
        .join(
            PlayerKillEvent,
            (PlayerKillEvent.region == Battle.region)
            & (PlayerKillEvent.albion_event_id == BattleKillEvent.albion_event_id),
        )
        .where(
            (BattleKillEvent.player_kill_event_id.is_(None))
            | (BattleKillEvent.silver_dropped.is_(None)),
        )
        .limit(BATTLE_HISTORY_BATCH_SIZE)
    )).all()
    for battle_event, player_event in rows:
        battle_event.player_kill_event_id = player_event.id
        if player_event.silver_dropped is not None:
            battle_event.silver_dropped = player_event.silver_dropped
    if rows:
        await db.commit()
    return len(rows)


async def _process_battle_batch(db: AsyncSession) -> int:
    """Atualiza a projeção de batalha a partir do ledger canônico."""
    return await _reconcile_battle_values(db)



async def _process_batch(db: AsyncSession) -> int:
    # Lê o lote em ordem e marca rejeitados com zero: deixá-los NULL faria os
    # mesmos primeiros eventos bloquearem a fila para sempre. Histórico sem
    # group_member_count também passa pela estimativa conservadora.
    cutoffs = _recent_cutoffs()
    # Uma consulta por região preserva o uso do índice parcial (region, timestamp).
    # O OR entre regiões fazia o Postgres varrer o índice temporal global da tabela.
    rows = []
    for region, cutoff in cutoffs.items():
        rows.extend((await db.scalars(
            select(PlayerKillEvent)
            .where(
                PlayerKillEvent.silver_dropped.is_(None),
                PlayerKillEvent.region == region,
                PlayerKillEvent.timestamp > cutoff,
            )
            .order_by(PlayerKillEvent.timestamp.asc(), PlayerKillEvent.albion_event_id.asc())
            .limit(BATCH_SIZE)
        )).all())
    rows.sort(key=lambda ev: (ev.timestamp, ev.albion_event_id))
    rows = rows[:BATCH_SIZE]
    # Mesmo quando o feed está em rajada, sempre reserva trabalho para o
    # histórico. A query por região preserva ix_pke_juicy_unpriced_queue.
    for region, cutoff in cutoffs.items():
        rows.extend((await db.scalars(
            select(PlayerKillEvent)
            .where(
                PlayerKillEvent.silver_dropped.is_(None),
                PlayerKillEvent.region == region,
                PlayerKillEvent.timestamp <= cutoff,
            )
            .order_by(PlayerKillEvent.timestamp.asc(), PlayerKillEvent.albion_event_id.asc())
            .limit(HISTORY_BATCH_PER_REGION)
        )).all())
    candidates = []
    for ev in rows:
        if _has_gear(ev) and is_likely_lethal(
            ev.fame, ev.victim_equipment, ev.group_member_count, ev.kill_area,
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
    log.info("silver_dropped: iniciando (precificação de mortes)")
    # ponytail: folga de 30s no boot — não brigar com os outros serviços
    # acordando (battle_tracker/backfill/sweeper fazem rajada de requests ao
    # subir). O backlog de silver é tolerante (já esperou desde sempre).
    await asyncio.sleep(30)
    while True:
        n = 0
        t_cycle = time.monotonic()
        async with AsyncSessionLocal() as db:
            try:
                kill_count = await _process_batch(db)
                battle_count = await _process_battle_batch(db)
                n = kill_count + battle_count
                t_total = time.monotonic() - t_cycle
                if n:
                    if t_total > 10.0:
                        log.warning("silver_dropped: CICLO LENTO — %d kills + %d mortes de batalha em %.1fs", kill_count, battle_count, t_total)
                    else:
                        log.info("silver_dropped: %d kills + %d mortes de batalha em %.1fs", kill_count, battle_count, t_total)
            except Exception as e:
                await db.rollback()
                log.error("silver_dropped: erro: %s", e)
        await asyncio.sleep(BUSY_INTERVAL if n > 0 else IDLE_INTERVAL)
