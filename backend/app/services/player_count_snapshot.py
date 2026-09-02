"""Coleta periódica (15min) da contagem de jogadores ativos (mesma métrica
de routes/battles.py:active_players) por região + global, persistida em
PlayerCountSnapshot — alimenta o gráfico histórico do dashboard
(Dashboard.tsx ActivePlayersCard). Sem essa tabela não dá pra desenhar um
gráfico: active_players só sabe "agora vs 7 dias atrás", não guarda os
pontos intermediários."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncSessionLocal
from app.models.dashboard_cache import DashboardCache
from app.models.players import PlayerCountSnapshot, PlayerKillEvent

log = logging.getLogger(__name__)

INTERVAL = 900  # 15 minutos — pedido explícito (era "de hora em hora", trocado)

# None = global (sem filtro de região)
_REGIONS: dict[str, str | None] = {"global": None, "americas": "americas", "europe": "europe", "asia": "asia"}

# Payload dos CARDS de active-players (atual vs semana anterior + Δ%) servido por
# routes/battles.py:active_players. Antes era 8 scans do ledger de kills a cada
# abertura do dashboard; agora sai daqui (o "atual" já era calculado pro gráfico,
# só faltava guardar a comparação). Chave na tabela genérica dashboard_cache.
CARDS_KEY = "active_players_cards"


async def _active_player_count(
    db: AsyncSession, region: str | None, start: datetime, end: datetime,
) -> int:
    # ponytail: mirror async local de player_activity.active_player_count (que
    # ainda é sync — também usada por routes/battles.py, não-migrado). Quando
    # battles.py migrar pra async, voltar a importar a versão compartilhada e
    # deletar esta.
    q = select(PlayerKillEvent.killer_player_id, PlayerKillEvent.victim_player_id).where(
        PlayerKillEvent.timestamp >= start, PlayerKillEvent.timestamp < end,
    )
    if region:
        q = q.where(PlayerKillEvent.region == region)
    ids: set[int] = set()
    for killer_id, victim_id in (await db.execute(q)).all():
        if killer_id is not None:
            ids.add(killer_id)
        if victim_id is not None:
            ids.add(victim_id)
    return len(ids)


async def _collect_once(db: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)
    prev_start = now - timedelta(days=14)
    cards: dict[str, dict] = {}
    for key, region in _REGIONS.items():
        current = await _active_player_count(db, region, week_start, now)  # = ponto do gráfico
        db.add(PlayerCountSnapshot(region=key, count=current, recorded_at=now))
        previous = await _active_player_count(db, region, prev_start, week_start)
        cards[key] = {
            "current": current,
            "previous": previous,
            "delta_pct": round((current - previous) / previous * 100) if previous else None,
        }
    row = await db.get(DashboardCache, CARDS_KEY)
    if row is None:
        db.add(DashboardCache(key=CARDS_KEY, payload=cards))
    else:
        row.payload = cards
    await db.commit()


async def run_forever() -> None:
    log.info("player_count_snapshot: iniciando")
    while True:
        async with AsyncSessionLocal() as db:
            try:
                await _collect_once(db)
            except Exception as e:
                log.error("player_count_snapshot: erro: %s", e)
        await asyncio.sleep(INTERVAL)
