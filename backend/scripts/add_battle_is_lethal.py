"""
Adiciona a coluna is_lethal em battles e faz o backfill pras batalhas já
deep-processadas, usando os BattleKillEvent que já temos salvos (sem precisar
rebuscar nada na API do Albion).

Regra: cada Battle.albion_id é UM mapa/instância só. Se alguma morte da luta
foi de um jogador COM equipamento (não pelado) e a kill deu fama 0, a luta
inteira aconteceu numa zona não letal (duelo, arena etc) — morte de pelado
com fama 0 é normal em qualquer zona e não desqualifica.

    python -m scripts.add_battle_is_lethal
"""
import sys
sys.path.insert(0, ".")

from app.db import engine, SessionLocal
from app.models.battles import Battle, BattleKillEvent
from sqlalchemy import select, text

CORE_SLOTS = ("weapon", "offhand", "helmet", "armor", "boots", "cape")


def _victim_was_equipped(equipment: dict | None) -> bool:
    return bool(equipment) and any(equipment.get(s) for s in CORE_SLOTS)


with engine.connect() as conn:
    conn.execute(text("ALTER TABLE battles ADD COLUMN is_lethal BOOLEAN NOT NULL DEFAULT 1"))
    conn.commit()

db = SessionLocal()
flagged = 0
checked = 0
try:
    deep_battle_ids = db.scalars(select(Battle.id).where(Battle.processing_tier == "deep")).all()
    for battle_id in deep_battle_ids:
        checked += 1
        events = db.scalars(select(BattleKillEvent).where(BattleKillEvent.battle_id == battle_id)).all()
        is_lethal = not any(ev.fame == 0 and _victim_was_equipped(ev.victim_equipment) for ev in events)
        if not is_lethal:
            db.execute(text("UPDATE battles SET is_lethal = 0 WHERE id = :id"), {"id": battle_id})
            flagged += 1
    db.commit()
finally:
    db.close()

print(f"OK: coluna is_lethal adicionada. {checked} batalhas deep checadas, {flagged} marcadas como não letais.")
