"""Heuristic valuation for awakened (.4 / Pristine) weapons.

The Albion events API exposes only Type+Quality per equipment item — no traits,
no strain, no per-item ItemPower — so per-weapon strain growth isn't computable
from battle data. We value every awakened weapon by TIER: public awakening base
cost × a tunable strain multiplier (higher tier = higher strain cost per trait
add/reroll). This fills the gap left by the market-price API, which returns
nothing for untradeable @4 items (they'd otherwise count as 0 silver).

ponytail: tier-flat heuristic; if the API ever exposes traits/per-item IP,
recompute per weapon from trait investment instead of this table.
"""
import re

# Public awakening base costs (silver) per tier — Albion awakening dev talk.
AWAKEN_BASE_COST = {4: 124_534, 5: 304_806, 6: 746_289, 7: 2_005_805, 8: 6_000_000}

# Strain multiplier per tier — models "higher tier = each trait add/reroll costs
# more", scaling an awakened weapon's estimated value above its base awakening
# cost. Heuristic defaults, NOT published numbers — tune here if needed.
# ponytail: global constant; move to a guild setting if per-guild tuning is asked.
AWAKEN_STRAIN_MULT = {4: 1.5, 5: 1.6, 6: 1.7, 7: 1.8, 8: 2.0}

_TIER_RE = re.compile(r"^T(\d+)_")


def is_awakened(item_id: str) -> bool:
    """Awakened = .4 (Pristine Enchantment), the only enchant tier produced by
    the awakening system."""
    return item_id.endswith("@4")


def awakened_value(item_id: str) -> int:
    """Estimated silver value of an awakened weapon by tier. 0 if not awakened
    or tier outside T4–T8."""
    if not is_awakened(item_id):
        return 0
    m = _TIER_RE.match(item_id)
    if not m:
        return 0
    tier = int(m.group(1))
    base = AWAKEN_BASE_COST.get(tier)
    if base is None:
        return 0
    return round(base * AWAKEN_STRAIN_MULT.get(tier, 1.0))


if __name__ == "__main__":
    assert awakened_value("T8_MAIN_SWORD@4") == round(6_000_000 * 2.0)
    assert awakened_value("T4_MAIN_SWORD@4") == round(124_534 * 1.5)
    assert awakened_value("T8_MAIN_SWORD@3") == 0       # não awakened
    assert awakened_value("T2_HEAD_PLATE_SET1@4") == 0  # tier < 4
    assert is_awakened("T8_MAIN_SWORD@4") is True
    print("ok")