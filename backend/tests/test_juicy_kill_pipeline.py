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
    assert loaded is True
    assert cache[("T8_HEAD_PLATE_SET1", 1)] is None


# ── Janela de pricing do worker silver_dropped × delay da API ─────────────────
# O timestamp do evento é o horário do JOGO; com a API atrasada (americas já
# ficou 30h+), a kill chega "velha" e uma janela fixa de 6h deixava ela NULL
# pra sempre — juicy kill nunca postada. A janela é esticada por região pelo
# delay medido (battle_tracker.publish_delay_status).

def test_recent_cutoffs_stretch_by_measured_delay():
    from datetime import datetime, timedelta, timezone

    from app.services import silver_dropped

    was = datetime.now(timezone.utc)
    delays = {
        "americas": {"delay_secs": 111_600.0, "measured_age_secs": 60},  # 31h
        "europe": {"delay_secs": 300.0, "measured_age_secs": 60},        # 5min
    }
    with patch("app.services.battle_tracker.publish_delay_status", return_value=delays):
        cutoffs = silver_dropped._recent_cutoffs()

    now = datetime.now(timezone.utc)
    drift = now - was  # tolerância do tempo de execução do teste

    # americas: 6h de janela + 31h de delay + 1h de margem
    expected_am = now - silver_dropped.RECENT_WINDOW - timedelta(seconds=111_600) - silver_dropped.DELAY_MARGIN
    assert abs((cutoffs["americas"] - expected_am).total_seconds()) < 5
    assert (was - cutoffs["americas"]) > timedelta(hours=36)
    # europe: delay ignorável (5min), fica perto da janela base + margem
    assert (was - cutoffs["europe"]) < silver_dropped.RECENT_WINDOW + silver_dropped.DELAY_MARGIN + drift + timedelta(minutes=6)


def test_recent_cutoffs_default_without_measurement():
    from datetime import timedelta

    from app.services import silver_dropped

    with patch("app.services.battle_tracker.publish_delay_status", return_value={}):
        cutoffs = silver_dropped._recent_cutoffs()

    assert set(cutoffs) == {"americas", "asia", "europe"}
    for cutoff in cutoffs.values():
        # sem medição: janela base + margem, nada esticado
        assert cutoff > datetime.now(timezone.utc) - silver_dropped.RECENT_WINDOW - silver_dropped.DELAY_MARGIN - timedelta(seconds=5)


def test_process_batch_query_filters_by_region_cutoff():
    """O SELECT combina (region, timestamp>cutoff) por região — kill de região
    atrasada com timestamp velho ainda é candidata; região sem delay não
    puxa backlog antigo."""
    import asyncio
    from unittest.mock import MagicMock

    from sqlalchemy.dialects import sqlite

    from app.services import silver_dropped

    captured = {}

    class _Scalars:
        def __init__(self, rows): self._rows = rows
        def all(self): return self._rows

    class _DB:
        async def scalars(self, q):
            captured["sql"] = str(q.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
            return _Scalars([])
        async def commit(self): pass

    delays = {"americas": {"delay_secs": 111_600.0}, "europe": {"delay_secs": 0.0}}
    with patch("app.services.battle_tracker.publish_delay_status", return_value=delays):
        n = asyncio.run(silver_dropped._process_batch(_DB()))

    assert n == 0
    sql = captured["sql"]
    assert "americas" in sql and "europe" in sql and "asia" in sql
    assert "OR" in sql.upper()  # um braço por região, não um cutoff global


if __name__ == "__main__":
    test_pristine_resource_is_not_an_awakened_weapon()
    test_awakened_value_uses_real_attunement_spent()
    test_unawakened_dot_four_weapon_uses_market_price()
    test_battle_price_accepts_quality_one_resources()
    test_battle_price_outlier_tier_cap_removes_troll()
    test_low_tier_price_does_not_break_cap_lookup()
    test_icon_loader_reports_missing_icons()
    test_recent_cutoffs_stretch_by_measured_delay()
    test_recent_cutoffs_default_without_measurement()
    test_process_batch_query_filters_by_region_cutoff()
    print("juicy kill pipeline OK")
