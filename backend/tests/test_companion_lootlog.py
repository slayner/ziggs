"""Estimativa pública de prata para o lootlog local do Companion."""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.loot import ItemPriceCache
from app.api.routes.companion import (
    SilverEstimateIn, SilverEstimateItemIn, companion_lootlog_silver_estimate,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


class _AsyncDb:
    def __init__(self, db):
        self.db = db

    async def scalars(self, statement):
        return self.db.scalars(statement)


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
