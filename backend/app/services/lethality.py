"""Heurística conservadora para separar kills full-loot das zonas laranja."""
from __future__ import annotations

import re


ORANGE_GROUP_LIMIT = 3
_FAME_UNIT = 100
_CORE_SLOTS = ("MainHand", "OffHand", "Head", "Armor", "Shoes", "Cape")
_SIMPLE_CORE_SLOTS = ("weapon", "offhand", "helmet", "armor", "boots", "cape")
_TIER = re.compile(r"^T(\d+)_")
_ENCHANT = re.compile(r"@(\d+)$")


def expected_kill_fame(equipment: dict | None) -> int:
    """Baseline pelo tier/encanto. Superestima de propósito: falso negativo é
    preferível a publicar uma kill não-lethal."""
    total = 0
    equipment = equipment or {}
    for slot in (*_CORE_SLOTS, *_SIMPLE_CORE_SLOTS):
        item = equipment.get(slot)
        item_id = item.get("Type", "") if isinstance(item, dict) else item or ""
        tier_match = _TIER.match(item_id)
        if not tier_match:
            continue
        enchant_match = _ENCHANT.search(item_id)
        total += _FAME_UNIT * 2 ** (
            int(tier_match.group(1)) + (int(enchant_match.group(1)) if enchant_match else 0)
        )
    return total


def is_likely_lethal(
    fame: int,
    victim_equipment: dict | None,
    group_member_count: int | None,
    kill_area: str | None = None,
) -> bool:
    """Heurística conservadora: falso negativo é preferível a falso positivo.

    Grupo >3 sugere que não é zona laranja (limite de grupo 3), mas não prova
    que é lethal — arenas, hellgates e CDGs aceitam grupos maiores e geram
    fama muito baixa. Por isso a fama é SEMPRE checada contra o baseline do
    equipamento, independentemente do tamanho do grupo."""
    if fame <= 0:
        return False
    expected = expected_kill_fame(victim_equipment)
    return expected > 0 and fame >= expected
