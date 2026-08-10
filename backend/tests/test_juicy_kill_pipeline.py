"""Regressions for the price and image used by Juicy Kills."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.services.awakened import awakened_value, is_awakened
from app.services.juicy_kill_image import _load_icons
from app.services.prices import get_battle_prices


class _Rows:
    def all(self):
        return []


class _DB:
    async def scalars(self, _query):
        return _Rows()

    async def commit(self):
        pass

    async def execute(self, _query):
        pass


def test_pristine_resource_is_not_an_awakened_weapon():
    assert awakened_value("T8_ORE_LEVEL4@4") == 0


def test_awakened_value_uses_real_attunement_spent():
    soul = {"era": 2, "attunementSpent": 333_375_064}
    assert is_awakened("T4_2H_BOW_AVALON@4", None) is False
    assert is_awakened("T4_2H_BOW_AVALON@4", soul) is True
    assert awakened_value("T4_2H_BOW_AVALON@4", soul) == 8_100_138


def test_unawakened_dot_four_weapon_uses_market_price():
    async def run():
        rows = [
            {"item_id": "T8_2H_BOW@4", "city": c, "quality": 1, "sell_price_min": 50_000_000}
            for c in ("Bridgewatch", "Lymhurst", "Martlock")
        ]
        with patch("app.services.prices._fetch_spot_prices", AsyncMock(return_value=rows)):
            return await get_battle_prices(_DB(), ["T8_2H_BOW@4"])

    assert asyncio.run(run())["T8_2H_BOW@4"] == 50_000_000


def test_battle_price_accepts_quality_one_resources():
    async def run():
        rows = [
            {"item_id": "T8_ORE_LEVEL4@4", "city": c, "quality": 1, "sell_price_min": p}
            for c, p in zip(
                ("Bridgewatch", "Lymhurst", "Martlock"),
                (1_000_000, 1_100_000, 1_200_000),
            )
        ]
        with patch("app.services.prices._fetch_spot_prices", AsyncMock(return_value=rows)):
            return await get_battle_prices(_DB(), ["T8_ORE_LEVEL4@4"])

    assert asyncio.run(run())["T8_ORE_LEVEL4@4"] == 1_100_000


def test_battle_price_outlier_tier_cap_removes_troll():
    async def run():
        rows = [
            {"item_id": "T8_ORE_LEVEL4@4", "city": c, "quality": 1, "sell_price_min": p}
            for c, p in zip(
                ("Bridgewatch", "Lymhurst", "Martlock"),
                (1_000_000, 1_100_000, 90_000_000),
            )
        ]
        with patch("app.services.prices._fetch_spot_prices", AsyncMock(return_value=rows)):
            return await get_battle_prices(_DB(), ["T8_ORE_LEVEL4@4"])

    assert asyncio.run(run())["T8_ORE_LEVEL4@4"] == 1_100_000


def test_low_tier_price_does_not_break_cap_lookup():
    async def run():
        rows = [
            {"item_id": "T1_WORM", "city": c, "quality": 1, "sell_price_min": 10_000}
            for c in ("Bridgewatch", "Lymhurst", "Martlock")
        ]
        with patch("app.services.prices._fetch_spot_prices", AsyncMock(return_value=rows)):
            return await get_battle_prices(_DB(), ["T1_WORM"])

    assert asyncio.run(run())["T1_WORM"] > 0


def test_icon_loader_reports_missing_icons():
    async def run():
        cache = {}
        items = [{"Type": "T8_HEAD_PLATE_SET1", "Quality": 1}]
        with patch("app.services.juicy_kill_image._fetch_item_icon", AsyncMock(return_value=None)):
            loaded = await _load_icons(items, cache)
        return loaded, cache

    loaded, cache = asyncio.run(run())
    assert loaded is False
    assert cache[("T8_HEAD_PLATE_SET1", 1)] is None


if __name__ == "__main__":
    test_pristine_resource_is_not_an_awakened_weapon()
    test_awakened_value_uses_real_attunement_spent()
    test_unawakened_dot_four_weapon_uses_market_price()
    test_battle_price_accepts_quality_one_resources()
    test_battle_price_outlier_tier_cap_removes_troll()
    test_low_tier_price_does_not_break_cap_lookup()
    test_icon_loader_reports_missing_icons()
    print("juicy kill pipeline OK")
