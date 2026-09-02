"""Diagnoses which items make up the silver_dropped of a kill."""
import sys
sys.path.insert(0, ".")

from sqlalchemy import select

from app.db import SessionLocal
from app.models.players import PlayerKillEvent
from app.models.prices import ItemPriceLatest
from app.services.awakened import awakened_value
from app.services.prices import _BATTLE_SENTINEL

event_id = sys.argv[1]
db = SessionLocal()
ev = db.scalar(select(PlayerKillEvent).where(PlayerKillEvent.albion_event_id == event_id))
items = []
souls = {}
for item in (ev.victim_equipment or {}).values():
    if item and item.get("Type"):
        items.append((item["Type"], 1, "equip"))
        souls[item["Type"]] = item.get("LegendarySoul")
for item in ev.victim_inventory or []:
    if item and item.get("Type"):
        items.append((item["Type"], item.get("Count") or 1, "inventory"))
        souls[item["Type"]] = item.get("LegendarySoul")

ids = {i for i, _, _ in items}
cached = {
    r.item_id: r.sell_price_min
    for r in db.scalars(select(ItemPriceLatest).where(
        ItemPriceLatest.city == _BATTLE_SENTINEL,
        ItemPriceLatest.item_id.in_(ids),
    ))
}
rows = []
for iid, qty, source in items:
    unit = cached.get(iid, 0) + awakened_value(iid, souls.get(iid))
    rows.append((unit * qty, unit, qty, source, iid))
for total, unit, qty, source, iid in sorted(rows, reverse=True):
    print(f"{total:>15,} = {unit:>12,} x {qty:<5} {source:<9} {iid}")
print(f"computed={sum(r[0] for r in rows):,} stored={ev.silver_dropped:,}")
db.close()
