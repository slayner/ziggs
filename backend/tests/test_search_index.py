"""
Testes do upsert de SearchEntry — sem rede, banco sqlite em memória. Roda com
pytest OU direto:
    PYTHONPATH=. python tests/test_search_index.py
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.players import SearchEntry
from app.services.search_index import safe_upsert_entry, upsert_entry


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[SearchEntry.__table__])
    return sessionmaker(bind=engine)()


def test_upserts_concorrentes_na_mesma_entidade_nao_duplicam():
    # sync_recent e backfill_cycle rodam em tasks separadas, cada uma com sua
    # própria Session (ver services/battle_tracker.py) — duas sessões podem
    # descobrir a MESMA aliança nova ao mesmo tempo. select-then-insert
    # duplicava (UNIQUE constraint failed); upsert atômico não deve.
    db_a, db_b = _session(), _session()
    safe_upsert_entry(db_a, entity_type="alliance", entity_id="AID1", display_name="DEAs")
    db_a.commit()
    safe_upsert_entry(db_b, entity_type="alliance", entity_id="AID1", display_name="DEAs")
    db_b.commit()

    rows = db_a.query(SearchEntry).all()
    assert len(rows) == 1
    assert rows[0].display_name == "DEAs"


def test_upsert_atualiza_em_vez_de_duplicar():
    db = _session()
    upsert_entry(db, entity_type="alliance", entity_id="AID1", display_name="DEAs", guild_count=5)
    db.commit()
    upsert_entry(db, entity_type="alliance", entity_id="AID1", display_name="DEAs2")
    db.commit()

    rows = db.query(SearchEntry).all()
    assert len(rows) == 1
    assert rows[0].display_name == "DEAs2"
    assert rows[0].guild_count == 5  # guild_count=None não deve apagar o valor anterior


def test_upsert_ignora_entrada_sem_id_ou_nome():
    db = _session()
    upsert_entry(db, entity_type="player", entity_id=None, display_name="X")
    upsert_entry(db, entity_type="player", entity_id="P1", display_name=None)
    db.commit()
    assert db.query(SearchEntry).count() == 0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("todos passaram")
