"""Acha uma kill do americas com inventario grande e vítima com nome."""
import sys
sys.path.insert(0, ".")

from app.db import SessionLocal
from app.models.players import PlayerKillEvent, AlbionPlayer
from sqlalchemy import select

db = SessionLocal()
rows = db.scalars(
    select(PlayerKillEvent)
    .where(PlayerKillEvent.silver_dropped > 0)
    .where(PlayerKillEvent.victim_inventory.isnot(None))
    .where(PlayerKillEvent.region == "americas")
    .order_by(PlayerKillEvent.silver_dropped.desc())
    .limit(80)
).all()

scored = []
for ev in rows:
    inv = [i for i in (ev.victim_inventory or []) if i and i.get("Type")]
    victim = db.get(AlbionPlayer, ev.victim_player_id) if ev.victim_player_id else None
    if not victim or not victim.name:
        continue
    scored.append((len(inv), ev, victim))

scored.sort(key=lambda x: x[0], reverse=True)

for inv_count, ev, victim in scored[:10]:
    killer = db.get(AlbionPlayer, ev.killer_player_id) if ev.killer_player_id else None
    kn = killer.name if killer else "?"
    print(f"event={ev.albion_event_id}  silver={ev.silver_dropped:,}  fame={ev.fame:,}  solo={ev.is_solo}  parts={ev.participant_count}  inv={inv_count}  killer={kn}  victim={victim.name}")

db.close()