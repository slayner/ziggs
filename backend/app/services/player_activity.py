"""Contagem de jogadores ativos (distintos com kill OU morte no ledger numa
janela) — compartilhado entre routes/battles.py (active_players, ao vivo) e
services/player_count_snapshot.py (coleta periódica pro gráfico do dashboard)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.players import PlayerKillEvent


def active_player_count(db: Session, region: str | None, start: datetime, end: datetime) -> int:
    """Não dá pra fazer direto em SQL com clareza (união de 2 colunas), então
    junta em Python; volume de uma janela de 7 dias é pequeno o bastante pra
    não pesar."""
    q = select(PlayerKillEvent.killer_player_id, PlayerKillEvent.victim_player_id).where(
        PlayerKillEvent.timestamp >= start, PlayerKillEvent.timestamp < end,
    )
    if region:
        q = q.where(PlayerKillEvent.region == region)
    ids: set[int] = set()
    for killer_id, victim_id in db.execute(q).all():
        if killer_id is not None:
            ids.add(killer_id)
        if victim_id is not None:
            ids.add(victim_id)
    return len(ids)
