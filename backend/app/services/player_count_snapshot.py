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
from app.models.players import PlayerCountSnapshot
from app.services.player_activity import active_player_count

log = logging.getLogger(__name__)

INTERVAL = 900  # 15 minutos — pedido explícito (era "de hora em hora", trocado)

# None = global (sem filtro de região)
_REGIONS: dict[str, str | None] = {"global": None, "americas": "americas", "europe": "europe", "asia": "asia"}


def _collect_once(db) -> None:
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)
    for key, region in _REGIONS.items():
        count = active_player_count(db, region, week_start, now)
        db.add(PlayerCountSnapshot(region=key, count=count, recorded_at=now))
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
