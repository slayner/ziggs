"""Valuation of the tuning recorded in an Albion LegendarySoul."""
import re


# Current values from ao-bin-dumps legendaryitems.xml.
AWAKENING_SILVER = 6_000_000
ATTUNEMENT_TO_SILVER = 33
FIXED_POINT_SCALE = 10_000
REATTUNEMENT_SILVER = 1_000_000
_WEAPON_RE = re.compile(r"^T[4-8]_(?:MAIN|2H)_.*@4$")


def is_awakened(item_id: str, legendary_soul: dict | None) -> bool:
    """A .4 weapon is awakened only when this specific item has a soul."""
    return bool(legendary_soul and _WEAPON_RE.match(item_id or ""))


def awakened_value(item_id: str, legendary_soul: dict | None = None) -> int:
    """Silver spent awakening and tuning this weapon; excludes its market price."""
    if not is_awakened(item_id, legendary_soul):
        return 0
    soul = legendary_soul or {}
    spent = max(
        0,
        int(soul.get("attunementSpent") or 0),
        int(soul.get("attunementSpentSinceReset") or 0),
    )
    era = max(1, int(soul.get("era") or 1))
    tuning_silver = (spent * ATTUNEMENT_TO_SILVER + FIXED_POINT_SCALE // 2) // FIXED_POINT_SCALE
    return AWAKENING_SILVER + tuning_silver + (era - 1) * REATTUNEMENT_SILVER


if __name__ == "__main__":
    soul = {"era": 2, "attunementSpent": 333_375_064}
    assert awakened_value("T4_2H_BOW_AVALON@4", soul) == 8_100_138
    assert awakened_value("T8_MAIN_SWORD@4") == 0
    assert awakened_value("T8_ORE_LEVEL4@4", soul) == 0
    print("ok")
