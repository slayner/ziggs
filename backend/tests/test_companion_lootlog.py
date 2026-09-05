"""Estimativa pública de prata para o lootlog local do Companion."""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import BigInteger, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.loot import ItemPriceCache
from app.api.routes.companion import (
    SilverEstimateIn, SilverEstimateItemIn, companion_lootlog_silver_estimate,
)
from app.services.lootlog import parse_loot_rows


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(_type, _compiler, **_kw):
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(_type, _compiler, **_kw):
    return "INTEGER"


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


class _AsyncDb:
    def __init__(self, db):
        self.db = db

    async def scalars(self, statement):
        return self.db.scalars(statement)


def test_csv_do_companion_preserva_metadados_do_loot_logger():
    rows = parse_loot_rows(
        "timestamp_utc;looted_by__alliance;looted_by__guild;looted_by__name;item_id;item_name;quantity;looted_from__alliance;looted_from__guild;looted_from__name;server__region\n"
        "2026-09-05T00:00:00Z;Alliance;Ziggs;Zezinho;T4_BAG;Adept's Bag;2;Enemies;Rivals;Fulano;europe"
    )
    assert rows == [{
        "ts": "2026-09-05T00:00:00+00:00",
        "item_id": "T4_BAG",
        "item_name": "Adept's Bag",
        "quantity": 2,
        "looted_by": "Zezinho",
        "looted_by_guild": "Ziggs",
        "looted_by_alliance": "Alliance",
        "looted_from": "Fulano",
        "looted_from_guild": "Rivals",
        "looted_from_alliance": "Enemies",
        "server_region": "europe",
    }]


def test_estimativa_de_prata_usa_apenas_cache_local():

    db = _session()
    db.add(ItemPriceCache(
        item_type="T4_BAG", silver_value=123,
        fetched_at=datetime.now(timezone.utc),
    ))
    db.commit()
    out = asyncio.run(companion_lootlog_silver_estimate(SilverEstimateIn(items=[
        SilverEstimateItemIn(item_id="T4_BAG", quantity=2),
        SilverEstimateItemIn(item_id="SEM_CACHE", quantity=9),
    ]), _AsyncDb(db)))
    assert out.silver_total == 246


if __name__ == "__main__":
    test_estimativa_de_prata_usa_apenas_cache_local()
    print("estimativa de prata do companion OK")
