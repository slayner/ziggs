"""Cálculo compartilhado do valor perdido em mortes de kills e batalhas."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.awakened import awakened_value
from app.services.prices import get_battle_prices_with_presumption


def _item_id(item: object) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        value = item.get("Type") or item.get("item_id")
        return str(value) if value else None
    return None


def _item_count(item: object) -> int:
    if not isinstance(item, dict):
        return 1
    return int(item.get("Count") or item.get("count") or 1)


def _item_soul(item: object, fallback: object = None) -> dict | None:
    value = (item.get("LegendarySoul") or fallback) if isinstance(item, dict) else fallback
    return value if isinstance(value, dict) else None


def death_items(equipment: dict | None, inventory: list[dict] | None) -> list[tuple[str, int, dict | None]]:
    """Normaliza snapshots cru e simplificado sem mudar a fórmula de preço."""
    out = []
    for slot, item in (equipment or {}).items():
        item_id = _item_id(item)
        if not item_id:
            continue
        soul = _item_soul(item, (equipment or {}).get(f"{slot}_legendary_soul"))
        out.append((item_id, 1, soul))
    for item in inventory or []:
        item_id = _item_id(item)
        if item_id:
            out.append((item_id, _item_count(item), _item_soul(item, item.get("legendary_soul"))))
    return out


async def price_death_loadouts(
    db: AsyncSession,
    loadouts: list[tuple[dict | None, list[dict] | None]],
) -> tuple[list[int], dict[str, str], int]:
    """Precifica cada equipamento de vítima com a cadeia usada pelas juicy kills."""
    items_by_loadout = [death_items(equipment, inventory) for equipment, inventory in loadouts]
    item_ids = list({item_id for items in items_by_loadout for item_id, _count, _soul in items})
    if not item_ids:
        return [0] * len(loadouts), {}, 0
    prices, basis = await get_battle_prices_with_presumption(db, item_ids)
    totals = [
        sum((prices.get(item_id, 0) + awakened_value(item_id, soul)) * count for item_id, count, soul in items)
        for items in items_by_loadout
    ]
    return totals, basis, len(item_ids)
