"""Pré-aquece o perfil de quem já apareceu numa batalha registrada — evita o
fetch lento (busca por nome + perfil + kills, 3 chamadas à API do Albion) na
primeira visita à página de perfil de um jogador que nunca vimos antes.

Funciona como fila, não como polling: cada Battle tem profiles_synced=False
até ser processada UMA VEZ (nunca reprocessa a mesma batalha de novo). Isso
cobre tanto o histórico inteiro já registrado (rodado uma vez no startup,
ver migração scripts/add_battle_profiles_synced.py) quanto batalhas novas
(toda Battle nasce com profiles_synced=False)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.battles import Battle, BattleParticipant
from app.models.players import AlbionPlayer
from app.services.albion_gate import OTHER, PROFILE, albion_scope, slot
from app.services.player_tracker import HOSTS, make_client, sync_player_kills, upsert_player

log = logging.getLogger(__name__)

# ponytail: era 20 — com o rate limiter global da Albion agora bem mais
# apertado (ver albion_gate.py), 20 batalhas (cada uma com vários
# participantes, até 3 chamadas/jogador) podia virar um ciclo de MINUTOS
# segurando uma única sessão de DB aberta com commits síncronos intercalados
# — cada commit bloqueia o event loop inteiro (SQLAlchemy síncrono chamado
# direto de código async), e com a fila histórica na casa de dezenas de
# milhares (não-urgente, ver REPROCESS_REASON_* pra distinguir de urgente),
# isso deixava o backend inteiro (até rotas simples tipo /health) engasgando
# por vários segundos, intermitente, logo após um restart. Lotes bem
# menores = ciclos bem mais curtos = sessão de DB fecha rápido = mais
# brechas pro event loop respirar entre um ciclo e outro.
BATCH_SIZE = 3       # batalhas por ciclo
BUSY_INTERVAL = 3    # segundos entre ciclos enquanto há fila pra processar
IDLE_INTERVAL = 60    # segundos entre ciclos quando a fila esvaziou
STALE_AFTER = timedelta(days=7)  # só re-busca perfil com mais tempo que isso sem atualizar


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _warm_player(client, db: Session, host: str, region: str, albion_id: str, *, force: bool = False) -> None:
    player = db.scalar(
        select(AlbionPlayer).where(AlbionPlayer.albion_id == albion_id, AlbionPlayer.region == region)
    )
    if not force and player is not None and (datetime.now(timezone.utc) - _aware(player.last_seen_at)) < STALE_AFTER:
        return
    try:
        async with slot():
            resp = await client.get(f"https://{host}/api/gameinfo/players/{albion_id}")
        if resp.status_code != 200:
            return
        raw = resp.json()
        if not (isinstance(raw, dict) and raw.get("Id")):
            return
        await sync_player_kills(client, db, host, region, albion_id)
        upsert_player(db, raw, region)
    except Exception as e:
        log.debug("profile_warmer: falha ao aquecer %s (%s): %s", albion_id, region, e)


REFRESH_BATCH_SIZE = 10  # pedidos explícitos (POST /players/{id}/refresh) por ciclo


async def sync_refresh_requests() -> int:
    """Fura a fila normal pra processar refresh pedido explicitamente pelo
    usuário (botão ⟳ do perfil) — ignora STALE_AFTER, é pedido direto.

    Roda em albion_scope(PROFILE): pedido explícito é user-facing (alguém
    esperando), vai pro reserved pool em vez de competir no bg com todo o
    backfill/sweeper de batalhas (era OTHER → até 3min esperando slot)."""
    db = SessionLocal()
    processed = 0
    try:
        players = db.scalars(
            select(AlbionPlayer)
            .where(AlbionPlayer.refresh_requested_at.isnot(None))
            .order_by(AlbionPlayer.refresh_requested_at)
            .limit(REFRESH_BATCH_SIZE)
        ).all()
        if not players:
            return 0
        async with make_client() as client:
            async with albion_scope(PROFILE):
                for player in players:
                    host = HOSTS.get(player.region)
                    if host is not None:
                        await _warm_player(client, db, host, player.region, player.albion_id, force=True)
                    # Limpa mesmo se o host for inválido ou o fetch falhar — não
                    # trava a fila num pedido que nunca vai completar.
                    player.refresh_requested_at = None
                    db.commit()
                    processed += 1
    finally:
        db.close()
    return processed


# POST /players/{id}/refresh dá set() aqui pra acordar o warmer na hora em
# vez de esperar o sleep idle (até IDLE_INTERVAL=60s). O DB é a fonte de
# verdade (refresh_requested_at); o event é só otimização de wake-up.
refresh_event = asyncio.Event()


def request_refresh() -> None:
    """Sinaliza que um refresh explícito foi enfileirado — acorda o warmer."""
    refresh_event.set()


async def sync_once() -> int:
    """Processa um lote de batalhas pendentes. Retorna quantas processou."""
    db = SessionLocal()
    processed = 0
    try:
        battles = db.scalars(
            select(Battle).where(Battle.profiles_synced.is_(False)).order_by(Battle.id).limit(BATCH_SIZE)
        ).all()
        if not battles:
            return 0
        async with make_client() as client:
            for battle in battles:
                host = HOSTS.get(battle.region)
                if host is not None:
                    player_ids = db.scalars(
                        select(BattleParticipant.albion_player_id).where(BattleParticipant.battle_id == battle.id)
                    ).all()
                    for albion_id in player_ids:
                        await _warm_player(client, db, host, battle.region, albion_id)
                battle.profiles_synced = True
                db.commit()
                processed += 1
    finally:
        db.close()
    return processed


async def run_forever() -> None:
    log.info("profile_warmer: iniciando")
    while True:
        refresh_event.clear()  # POSTs durante o trabalho acendem pra próxima volta
        try:
            async with albion_scope(OTHER):
                n = await sync_refresh_requests()
                if n == 0:
                    n = await sync_once()
                else:
                    log.debug("profile_warmer: %d refresh(es) explícito(s) processado(s)", n)
        except Exception as e:
            log.error("profile_warmer: erro: %s", e)
            n = 0
        # Dorme, mas um POST /refresh acorda imediatamente via refresh_event.
        timeout = BUSY_INTERVAL if n > 0 else IDLE_INTERVAL
        try:
            await asyncio.wait_for(refresh_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
