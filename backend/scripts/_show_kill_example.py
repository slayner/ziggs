"""Mostra uma kill de exemplo do banco pro mockup da imagem juicy kill."""
import sys
sys.path.insert(0, ".")

from app.db import SessionLocal
from app.models.players import PlayerKillEvent, AlbionPlayer
from sqlalchemy import select

db = SessionLocal()
ev = db.scalars(
    select(PlayerKillEvent)
    .where(PlayerKillEvent.silver_dropped > 0)
    .order_by(PlayerKillEvent.silver_dropped.desc())
    .limit(1)
).first()

if not ev:
    print("Nenhuma kill com silver_dropped > 0 encontrada.")
    db.close()
    raise SystemExit(0)

killer = db.get(AlbionPlayer, ev.killer_player_id) if ev.killer_player_id else None
victim = db.get(AlbionPlayer, ev.victim_player_id) if ev.victim_player_id else None

print("=== KILL DE EXEMPLO ===")
print(f"event_id: {ev.albion_event_id}")
print(f"region: {ev.region}")
print(f"fame: {ev.fame:,}")
print(f"silver_dropped: {ev.silver_dropped:,}")
print(f"is_solo: {ev.is_solo}")
print(f"participant_count: {ev.participant_count}")
print(f"timestamp: {ev.timestamp}")
print(f"kill_area: {ev.kill_area}")
print(f"albion_battle_id: {ev.albion_battle_id}")
print()
kn = killer.name if killer else "?"
kg = killer.guild_name if killer else None
ka = killer.alliance_name if killer else None
vn = victim.name if victim else "?"
vg = victim.guild_name if victim else None
va = victim.alliance_name if victim else None
print(f"KILLER: {kn} | guild={kg} | alliance={ka}")
print(f"VICTIM: {vn} | guild={vg} | alliance={va}")
print()
print("KILLER EQUIPMENT:")
for slot, item in (ev.killer_equipment or {}).items():
    if item and item.get("Type"):
        print(f"  {slot}: {item['Type']} (q{item.get('Quality', '?')})")
print()
print("VICTIM EQUIPMENT:")
for slot, item in (ev.victim_equipment or {}).items():
    if item and item.get("Type"):
        print(f"  {slot}: {item['Type']} (q{item.get('Quality', '?')})")
print()
print("VICTIM INVENTORY:")
for inv in (ev.victim_inventory or []):
    if inv and inv.get("Type"):
        print(f"  {inv['Type']} x{inv.get('Count', 1)}")

db.close()