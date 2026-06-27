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
from app.services.player_tracker import HOSTS, make_client, sync_player_kills, upsert_player

log = logging.getLogger(__name__)

BATCH_SIZE = 20      # batalhas por ciclo
BUSY_INTERVAL = 3    # segundos entre ciclos enquanto há fila pra processar
IDLE_INTERVAL = 60    # segundos entre ciclos quando a fila esvaziou
STALE_AFTER = timedelta(days=7)  # só re-busca perfil com mais tempo que isso sem atualizar


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _warm_player(client, db: Session, host: str, region: str, albion_id: str) -> None:
    player = db.scalar(
        select(AlbionPlayer).where(AlbionPlayer.albion_id == albion_id, AlbionPlayer.region == region)
    )
    if player is not None and (datetime.now(timezone.utc) - _aware(player.last_seen_at)) < STALE_AFTER:
        return
    try:
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
        try:
            n = await sync_once()
            if n:
                log.debug("profile_warmer: %d batalhas processadas", n)
        except Exception as e:
            log.error("profile_warmer: erro: %s", e)
            n = 0
        await asyncio.sleep(BUSY_INTERVAL if n > 0 else IDLE_INTERVAL)
