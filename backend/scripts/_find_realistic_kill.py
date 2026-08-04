"""Mostra kills com mais itens no inventario pra escolher uma realista."""
import sys
sys.path.insert(0, ".")

from app.db import SessionLocal
from app.models.players import PlayerKillEvent, AlbionPlayer
from sqlalchemy import select

db = SessionLocal()
# Pega kills com silver_dropped > 0, ordena por silver desc, e conta inventario em Python
rows = db.scalars(
    select(PlayerKillEvent)
    .where(PlayerKillEvent.silver_dropped > 0)
    .where(PlayerKillEvent.victim_inventory.isnot(None))
    .order_by(PlayerKillEvent.silver_dropped.desc())
    .limit(50)
).all()

scored = []
for ev in rows:
    inv = [i for i in (ev.victim_inventory or []) if i and i.get("Type")]
    scored.append((len(inv), ev))

scored.sort(key=lambda x: x[0], reverse=True)

for inv_count, ev in scored[:10]:
    print(f"event={ev.albion_event_id}  silver={ev.silver_dropped:,}  fame={ev.fame:,}  solo={ev.is_solo}  parts={ev.participant_count}  region={ev.region}  inv_items={inv_count}")

# Mostra detalhes da melhor
if scored:
    ev = scored[0][1]
    killer = db.get(AlbionPlayer, ev.killer_player_id) if ev.killer_player_id else None
    victim = db.get(AlbionPlayer, ev.victim_player_id) if ev.victim_player_id else None
    print(f"\n=== DETALHES: {ev.albion_event_id} ===")
    print(f"KILLER: {killer.name if killer else '?'} | guild={killer.guild_name if killer else None} | alliance={killer.alliance_name if killer else None}")
    print(f"VICTIM: {victim.name if victim else '?'} | guild={victim.guild_name if victim else None} | alliance={victim.alliance_name if victim else None}")
    print(f"silver_dropped: {ev.silver_dropped:,}")
    print(f"fame: {ev.fame:,}")
    print(f"solo: {ev.is_solo}  participants: {ev.participant_count}")
    print(f"region: {ev.region}  timestamp: {ev.timestamp}")
    print(f"\nVICTIM INVENTORY ({scored[0][0]} itens):")
    for inv in (ev.victim_inventory or []):
        if inv and inv.get("Type"):
            print(f"  {inv['Type']} x{inv.get('Count',1)} q{inv.get('Quality',0)}")

db.close()