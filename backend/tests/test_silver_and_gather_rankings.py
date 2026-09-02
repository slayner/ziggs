"""Sanity checks for the silver_dropped worker and the gather/silver rankings.

Uses an in-memory SQLite only to verify the ranking SQL aggregation.
"""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.api.routes.highscores import TimeWindow, _GATHER_KINDS, _SILVER_KIND, _silver_ranking
from app.models.base import Base
from app.models.players import AlbionPlayer, PlayerKillEvent
from app.services.silver_dropped import _has_gear


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_test(_type, _compiler, **_kw):
    return "JSON"


def _fake_ev(eq=None, inv=None):
    """PlayerKillEvent mock with only victim_equipment/inventory — what
    _has_gear reads. silver_dropped is NULL here (not in the filter)."""
    class _Ev:
        def __init__(self, eq, inv):
            self.victim_equipment = eq
            self.victim_inventory = inv
    return _Ev(eq, inv)


def test_has_gear_equipped():
    eq = {"MainHand": {"Type": "T6_1H_SWORD"}, "OffHand": None}
    assert _has_gear(_fake_ev(eq=eq, inv=None)) is True


def test_has_gear_inventory_only():
    """Nothing equipped but a loaded item counts as gear (it's dropped on
    death the same way)."""
    eq = {"MainHand": None}
    inv = [{"Type": "T4_BAG", "Count": 1}]
    assert _has_gear(_fake_ev(eq=eq, inv=inv)) is True


def test_no_gear_naked_victim():
    """Naked victim (no equip, no inventory) — not a pricing candidate.
    Stays NULL in the DB forever, never reprocessed."""
    eq = {"MainHand": None, "OffHand": None}
    assert _has_gear(_fake_ev(eq=eq, inv=None)) is False


def test_empty_inventory_list_is_no_gear():
    """[] is different from None — but both have no gear."""
    assert _has_gear(_fake_ev(eq=None, inv=[])) is False


def test_profile_silver_total_uses_persisted_death_values():
    from app.api.routes.players import _silver_dropped

    class _Death:
        victim_equipment = {"MainHand": {"Type": "T8_2H_BOW@4"}}
        victim_inventory = []
        silver_dropped = 12_345_678

    assert asyncio.run(_silver_dropped(None, [_Death()])) == 12_345_678


def test_battle_valuation_uses_pke_snapshot():
    from unittest.mock import AsyncMock, patch
    from app.services.death_pricing import price_death_loadouts

    raw = (
        {"MainHand": {"Type": "T8_2H_BOW@4", "LegendarySoul": {"value": 20}}},
        [{"Type": "T4_BAG", "Count": 2, "LegendarySoul": {"value": 30}}],
    )

    async def run():
        with patch(
            "app.services.death_pricing.get_battle_prices_with_presumption",
            new=AsyncMock(return_value=({"T8_2H_BOW@4": 100, "T4_BAG": 25}, {})),
        ), patch("app.services.death_pricing.awakened_value", side_effect=lambda _item, soul: (soul or {}).get("value", 0)):
            totals, _basis, _count = await price_death_loadouts(None, [raw])
            assert totals == [230]

    asyncio.run(run())


def test_battle_snapshot_uses_the_same_death_pricing_as_raw_kill():

    from unittest.mock import AsyncMock, patch
    from app.services.death_pricing import price_death_loadouts

    raw = (
        {"MainHand": {"Type": "T8_2H_BOW@4", "LegendarySoul": {"value": 20}}},
        [{"Type": "T4_BAG", "Count": 2, "LegendarySoul": {"value": 30}}],
    )
    battle_snapshot = (
        {"weapon": "T8_2H_BOW@4", "weapon_legendary_soul": {"value": 20}},
        [{"item_id": "T4_BAG", "count": 2, "legendary_soul": {"value": 30}}],
    )

    async def run():
        with patch(
            "app.services.death_pricing.get_battle_prices_with_presumption",
            new=AsyncMock(return_value=({"T8_2H_BOW@4": 100, "T4_BAG": 25}, {})),
        ), patch("app.services.death_pricing.awakened_value", side_effect=lambda _item, soul: (soul or {}).get("value", 0)):
            totals, _basis, _count = await price_death_loadouts(None, [raw, battle_snapshot])
        assert totals == [230, 230]

    asyncio.run(run())


def test_battle_equipment_snapshot_ignores_empty_slots():
    from app.services.battle_tracker import _simplify_equipment

    assert _simplify_equipment({
        "MainHand": None,
        "Head": {"Type": "T6_HEAD_CLOTH_SET1", "Quality": 2},
    }) == {"helmet": "T6_HEAD_CLOTH_SET1", "helmet_quality": 2}


def test_gather_kinds_cover_all_resources():
    """The 8 kinds (total + 5 resources + fishing + crafting) must exist —
    the gather dropdown depends on this. If one is missing, that resource's
    ranking 404s. crafting_fame and gathering_fame were already scalars on
    AlbionPlayer (populated by upsert_player long ago), so they needed no
    migration — only the kind in the highscore."""
    assert set(_GATHER_KINDS) == {
        "gather_total", "gather_wood", "gather_hide",
        "gather_ore", "gather_rock", "gather_fiber",
        "fishing", "crafting",
    }


def test_silver_kind_is_player_kind():
    """silver_dropped is a player ranking (not a guild one) — it must be in
    the frontend PLAYER_KINDS, and the route needs its branch."""
    assert _SILVER_KIND == "silver_dropped"
    # The _silver_ranking branch exists in _compute_rankings (see code); here
    # we just guard that the kind is the expected one, so renaming doesn't
    # break the route.


def test_silver_ranking_aggregates_filters_and_paginates_in_sql():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[AlbionPlayer.__table__, PlayerKillEvent.__table__])
    db = sessionmaker(bind=engine)()
    players = [
        AlbionPlayer(id=1, albion_id="a", name="Alpha", region="americas"),
        AlbionPlayer(id=2, albion_id="b", name="Beta", region="europe"),
        AlbionPlayer(id=3, albion_id="c", name="Gamma", region="americas"),
    ]
    db.add_all(players)
    db.flush()
    now = datetime.now(timezone.utc)
    for i, (player, silver, fame) in enumerate([
        (players[0], 100, 1), (players[0], 50, 1),
        (players[1], 1000, 1), (players[2], 20, 1),
        (players[2], 9999, 0),
    ]):
        db.add(PlayerKillEvent(
            id=i + 1, region=player.region, albion_event_id=str(i), timestamp=now,
            fame=fame, victim_player_id=player.id, silver_dropped=silver,
        ))
    db.commit()

    class AsyncAdapter:
        async def scalar(self, query):
            return db.scalar(query)

        async def execute(self, query):
            return db.execute(query)

    result = asyncio.run(_silver_ranking(AsyncAdapter(), ["americas"], TimeWindow(), "a", 1, 1))
    assert result["total"] == 2
    assert result["rows"] == [{
        "albion_id": "c", "name": "Gamma", "region": "americas",
        "guild_name": None, "alliance_name": None,
        "value": 20, "rank": 2,
    }]


def _price_silver(price_by_id, eq, inv):
    """Same logic as _price_events (sum equipment + inventory*count)."""
    total = 0
    for item in (eq or {}).values():
        if item and item.get("Type"):
            total += price_by_id.get(item["Type"], 0)
    for it in (inv or []):
        if it and it.get("Type"):
            total += price_by_id.get(it["Type"], 0) * (it.get("Count") or 1)
    return total


def test_price_silver_equipment_only():
    prices = {"T6_1H_SWORD": 50000, "T4_BAG": 3000}
    eq = {"MainHand": {"Type": "T6_1H_SWORD"}, "OffHand": None}
    assert _price_silver(prices, eq, None) == 50000


def test_price_silver_inventory_multiplies_by_count():
    """Loaded items come with Count — the price is unit * quantity."""
    prices = {"T4_ORE": 120}
    inv = [{"Type": "T4_ORE", "Count": 50}]
    assert _price_silver(prices, None, inv) == 120 * 50


def test_price_silver_missing_price_is_zero():
    """An item with no cached price contributes 0, doesn't break the sum."""
    prices = {}
    eq = {"MainHand": {"Type": "T8_2H_BOW@3"}}
    assert _price_silver(prices, eq, None) == 0


def test_price_silver_combined_equipment_and_inventory():
    prices = {"T6_HEAD_PLATE_SET1": 40000, "T4_HIDE": 80}
    eq = {"Head": {"Type": "T6_HEAD_PLATE_SET1"}}
    inv = [{"Type": "T4_HIDE", "Count": 100}]
    assert _price_silver(prices, eq, inv) == 40000 + 8000


if __name__ == "__main__":
    test_has_gear_equipped()
    test_has_gear_inventory_only()
    test_no_gear_naked_victim()
    test_empty_inventory_list_is_no_gear()
    test_gather_kinds_cover_all_resources()
    test_silver_kind_is_player_kind()
    test_silver_ranking_aggregates_filters_and_paginates_in_sql()
    test_price_silver_equipment_only()
    test_price_silver_inventory_multiplies_by_count()
    test_price_silver_missing_price_is_zero()
    test_price_silver_combined_equipment_and_inventory()
    print("ok")
