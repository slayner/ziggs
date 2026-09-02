"""
Ingest de preços do companion — sem rede, sqlite em memória. Roda com pytest OU:
    PYTHONPATH=. python tests/test_prices_ingest.py

O caso que quebrava: um lote com o MESMO (item_id, city, quality) repetido
estourava UNIQUE constraint em item_prices_latest, porque a sessão é
autoflush=False e o SELECT não enxerga o INSERT pendente do mesmo lote.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.prices import ItemPrice, ItemPriceLatest
from app.services.prices import upsert_companion_prices


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        ItemPrice.__table__, ItemPriceLatest.__table__,
    ])
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def _row(price: int, hour: int) -> dict:
    return {
        "item_id": "T4_BAG", "city": "Caerleon", "quality": 1,
        "sell_price_min": price,
        "price_date": f"2026-07-18T{hour:02d}:00:00+00:00",
    }


def test_lote_com_duplicata_nao_estoura():
    db = _session()
    accepted, rejected = upsert_companion_prices(db, [_row(1000, 0), _row(1200, 1)])
    assert (accepted, rejected) == (2, 0)
    assert db.query(ItemPriceLatest).count() == 1, "duplicata virou 2 linhas"


def test_duplicata_mantem_o_price_date_mais_novo():
    db = _session()
    # mais novo primeiro: o mais velho depois NÃO pode sobrescrever.
    upsert_companion_prices(db, [_row(1200, 5), _row(1000, 1)])
    assert db.query(ItemPriceLatest).one().sell_price_min == 1200


def test_row_sem_item_id_ou_preco_e_rejeitada():
    db = _session()
    accepted, rejected = upsert_companion_prices(db, [
        {"item_id": "", "sell_price_min": 10},
        {"item_id": "T4_BAG", "sell_price_min": 0},
    ])
    assert (accepted, rejected) == (0, 2)


if __name__ == "__main__":
    test_lote_com_duplicata_nao_estoura()
    test_duplicata_mantem_o_price_date_mais_novo()
    test_row_sem_item_id_ou_preco_e_rejeitada()
    print("prices ingest OK")
