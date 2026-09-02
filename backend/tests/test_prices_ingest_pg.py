"""Self-check do upsert_companion_prices em Postgres real (não sqlite).
Roda direto: PYTHONPATH=. scripts/python.exe tests/test_prices_ingest_pg.py

Valida: (1) bulk insert+upsert nativo não estoura, (2) duplicata no mesmo lote
vira 1 linha no latest, (3) age mais próxima vence (price_date mais novo prevalece),
(4) rejeita row sem item_id ou price==0.
"""
from datetime import datetime, timezone
import os

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.prices import ItemPrice, ItemPriceLatest
from app.services.prices import upsert_companion_prices

DB_URL = os.environ.get("ZIGGS_TEST_DATABASE_URL")


def _row(price: int, hour: int, item_id: str = "T4_BAG_TEST_PG") -> dict:
    return {
        "item_id": item_id, "city": "Caerleon", "quality": 1,
        "sell_price_min": price,
        "price_date": f"2026-07-18T{hour:02d}:00:00+00:00",
    }


def main() -> None:
    if not DB_URL:
        raise RuntimeError("Defina ZIGGS_TEST_DATABASE_URL para rodar este teste")
    engine = create_engine(DB_URL)
    # Limpa resíduo de runs anteriores (item_id de teste).
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM item_prices WHERE item_id = 'T4_BAG_TEST_PG'"))
        conn.execute(text("DELETE FROM item_prices_latest WHERE item_id = 'T4_BAG_TEST_PG'"))
        conn.commit()
    Base.metadata.create_all(engine, tables=[ItemPrice.__table__, ItemPriceLatest.__table__])
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()

    # (1) lote com duplicata — deve virar 1 linha no latest, 2 no histórico.
    accepted, rejected = upsert_companion_prices(db, [_row(1000, 0), _row(1200, 1)])
    assert (accepted, rejected) == (2, 0), (accepted, rejected)
    assert db.query(ItemPriceLatest).filter(ItemPriceLatest.item_id == "T4_BAG_TEST_PG").count() == 1
    assert db.query(ItemPrice).filter(ItemPrice.item_id == "T4_BAG_TEST_PG").count() == 2
    latest = db.scalar(select(ItemPriceLatest).where(ItemPriceLatest.item_id == "T4_BAG_TEST_PG"))
    assert latest.sell_price_min == 1200, latest.sell_price_min  # o mais novo (h=1) venceu
    print("PASS (1) duplicata vira 1 latest, age mais nova vence")

    # (2) segundo lote: preço mais VELHO não pode sobrescrever o fresco existente.
    upsert_companion_prices(db, [_row(1100, 0)])  # h=0 < h=1 existente
    db.expire_all()
    latest = db.scalar(select(ItemPriceLatest).where(ItemPriceLatest.item_id == "T4_BAG_TEST_PG"))
    assert latest.sell_price_min == 1200, f"price velho sobrescreveu: {latest.sell_price_min}"
    print("PASS (2) price_date velho não sobrescreve fresco")

    # (3) preço mais NOVO sobrescreve.
    upsert_companion_prices(db, [_row(999, 5)])  # h=5 > h=1
    db.expire_all()
    latest = db.scalar(select(ItemPriceLatest).where(ItemPriceLatest.item_id == "T4_BAG_TEST_PG"))
    assert latest.sell_price_min == 999, latest.sell_price_min
    print("PASS (3) price_date novo sobrescreve fresco")

    # (4) rejeita row sem item_id ou price==0.
    accepted, rejected = upsert_companion_prices(db, [
        {"item_id": "", "sell_price_min": 10},
        {"item_id": "T4_BAG_TEST_PG", "sell_price_min": 0},
    ])
    assert (accepted, rejected) == (0, 2), (accepted, rejected)
    print("PASS (4) rejeita sem item_id ou price==0")

    # limpeza
    db.query(ItemPrice).filter(ItemPrice.item_id == "T4_BAG_TEST_PG").delete()
    db.query(ItemPriceLatest).filter(ItemPriceLatest.item_id == "T4_BAG_TEST_PG").delete()
    db.commit()
    db.close()
    engine.dispose()
    print("prices ingest PG OK")


from sqlalchemy import text

if __name__ == "__main__":
    main()
