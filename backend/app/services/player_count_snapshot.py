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

from app.db import SessionLocal
from app.models.dashboard_cache import DashboardCache
from app.models.players import PlayerCountSnapshot
from app.services.player_activity import active_player_count

log = logging.getLogger(__name__)

INTERVAL = 900  # 15 minutos — pedido explícito (era "de hora em hora", trocado)

# None = global (sem filtro de região)
_REGIONS: dict[str, str | None] = {"global": None, "americas": "americas", "europe": "europe", "asia": "asia"}

# Payload dos CARDS de active-players (atual vs semana anterior + Δ%) servido por
# routes/battles.py:active_players. Antes era 8 scans do ledger de kills a cada
# abertura do dashboard; agora sai daqui (o "atual" já era calculado pro gráfico,
# só faltava guardar a comparação). Chave na tabela genérica dashboard_cache.
CARDS_KEY = "active_players_cards"


def _collect_once(db) -> None:
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)
    prev_start = now - timedelta(days=14)
    cards: dict[str, dict] = {}
    for key, region in _REGIONS.items():
        current = active_player_count(db, region, week_start, now)  # = ponto do gráfico
        db.add(PlayerCountSnapshot(region=key, count=current, recorded_at=now))
        previous = active_player_count(db, region, prev_start, week_start)
        cards[key] = {
            "current": current,
            "previous": previous,
            "delta_pct": round((current - previous) / previous * 100) if previous else None,
        }
    row = db.get(DashboardCache, CARDS_KEY)
    if row is None:
        db.add(DashboardCache(key=CARDS_KEY, payload=cards))
    else:
        row.payload = cards
    db.commit()


async def run_forever() -> None:
    log.info("player_count_snapshot: iniciando")
    while True:
        db = SessionLocal()
        try:
            _collect_once(db)
        except Exception as e:
            log.error("player_count_snapshot: erro: %s", e)
        finally:
            db.close()
        await asyncio.sleep(INTERVAL)
