"""Isolamento regional do cache de preços de mortes."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy import BigInteger, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.models.base import Base
from app.models.prices import ItemPrice, ItemPriceLatest
from app.services.prices import (
    _RRR_BONUS_CITY_FACTOR,
    _unique_to_game,
    get_battle_prices,
    get_battle_prices_with_presumption,
    upsert_companion_prices,
)


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(_type, _compiler, **_kw):  # pragma: no cover
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(_type, _compiler, **_kw):  # pragma: no cover
    return "INTEGER"


def test_cache_e_aodp_respeitam_regiao_do_evento():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", future=True)
        async with engine.begin() as connection:
            await connection.run_sync(
                Base.metadata.create_all,
                tables=[ItemPrice.__table__, ItemPriceLatest.__table__],
            )
        now = datetime.now(timezone.utc)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(ItemPriceLatest(
                item_id=_unique_to_game("T4_BAG"), city="_battle_spot_", quality=1,
                region="west", sell_price_min=100, price_date=now, recorded_at=now,
            ))
            await db.commit()
            rows = [
                {"item_id": "T4_BAG", "city": city, "quality": 1, "sell_price_min": 200}
                for city in ("Bridgewatch", "Lymhurst", "Martlock")
            ]
            with patch("app.services.prices._fetch_spot_prices", AsyncMock(return_value=rows)) as fetch:
                assert await get_battle_prices(db, ["T4_BAG"], region="americas") == {"T4_BAG": 100}
                assert await get_battle_prices(db, ["T4_BAG"], region="europe") == {"T4_BAG": 200}
                assert fetch.await_args.args[1] == "europe"
        await engine.dispose()

    asyncio.run(run())


def test_leste_sem_preco_local_usa_cache_do_oeste():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", future=True)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all, tables=[ItemPriceLatest.__table__])
        now = datetime.now(timezone.utc)
        game_id = _unique_to_game("T4_BAG")
        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(ItemPriceLatest(
                item_id=game_id, city="_battle_spot_", quality=1, region="west",
                sell_price_min=100, price_date=now, recorded_at=now,
            ))
            await db.commit()
            with patch("app.services.prices._fetch_spot_prices", AsyncMock(return_value=[])) as fetch:
                assert await get_battle_prices(db, ["T4_BAG"], region="asia") == {"T4_BAG": 100}
                assert [call.args[1] for call in fetch.await_args_list] == ["east"]
        await engine.dispose()

    asyncio.run(run())


def test_preco_local_do_leste_vence_fallback_do_oeste():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", future=True)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all, tables=[ItemPriceLatest.__table__])
        now = datetime.now(timezone.utc)
        game_id = _unique_to_game("T4_BAG")
        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add_all([
                ItemPriceLatest(item_id=game_id, city="_battle_spot_", quality=1, region="east", sell_price_min=200, price_date=now, recorded_at=now),
                ItemPriceLatest(item_id=game_id, city="_battle_spot_", quality=1, region="west", sell_price_min=100, price_date=now, recorded_at=now),
            ])
            await db.commit()
            with patch("app.services.prices._fetch_spot_prices", AsyncMock()) as fetch:
                assert await get_battle_prices(db, ["T4_BAG"], region="asia") == {"T4_BAG": 200}
                fetch.assert_not_awaited()
        await engine.dispose()

    asyncio.run(run())


def test_presuncao_usa_material_de_fallback_regional():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", future=True)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all, tables=[ItemPriceLatest.__table__])
        now = datetime.now(timezone.utc)
        material = "T4_METALBAR"
        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(ItemPriceLatest(
                item_id=_unique_to_game(material), city="_battle_spot_", quality=1, region="west",
                sell_price_min=1000, price_date=now, recorded_at=now,
            ))
            await db.commit()
            with patch("app.services.prices._fetch_spot_prices", AsyncMock(return_value=[])), patch(
                "app.services.prices._load_craft_catalog",
                return_value={"T4_TEST_ITEM": {"resources": [{"uniqueName": material, "count": 8}]}}
            ):
                prices, basis = await get_battle_prices_with_presumption(db, ["T4_TEST_ITEM"], region="asia")
            assert prices["T4_TEST_ITEM"] == round(8000 * _RRR_BONUS_CITY_FACTOR)
            assert basis["T4_TEST_ITEM"] == "presumed"
        await engine.dispose()

    asyncio.run(run())


def test_companion_mais_fresco_substitui_aodp_so_na_mesma_regiao():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", future=True)
        async with engine.begin() as connection:
            await connection.run_sync(
                Base.metadata.create_all,
                tables=[ItemPrice.__table__, ItemPriceLatest.__table__],
            )
        older = datetime.now(timezone.utc) - timedelta(hours=1)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(ItemPriceLatest(
                item_id="Adept's Bag", city="Caerleon", quality=1,
                region="west", sell_price_min=100, price_date=older, recorded_at=older,
            ))
            await db.commit()
            accepted, rejected = await upsert_companion_prices(db, [{
                "item_id": "Adept's Bag", "city": "Caerleon", "quality": 1,
                "sell_price_min": 250, "price_date": datetime.now(timezone.utc).isoformat(),
                "region": "west",
            }, {
                "item_id": "Adept's Bag", "city": "Caerleon", "quality": 1,
                "sell_price_min": 300, "price_date": datetime.now(timezone.utc).isoformat(),
                "region": "europe",
            }])
            assert (accepted, rejected) == (2, 0)
            west = await db.scalar(select(ItemPriceLatest).where(ItemPriceLatest.region == "west"))
            assert west.sell_price_min == 250
            europe = await db.scalar(select(ItemPriceLatest).where(ItemPriceLatest.region == "europe"))
            assert europe.sell_price_min == 300
            assert west.sell_price_min == 250
        await engine.dispose()

    asyncio.run(run())
