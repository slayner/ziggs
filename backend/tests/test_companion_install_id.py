"""
Claim por instalação — 1 PC = 1 range, mesmo com N processos abertos.
Roda com pytest OU: PYTHONPATH=. python tests/test_companion_install_id.py

O caso que motivou: 3 cópias do companion abertas no mesmo PC (rebuilds)
pegaram 3 ranges diferentes e contavam como 3 companions.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.companion import CompanionScanTask
from app.services.companion_scan import claim_task, count_active_companions

PC_A = "a" * 32
PC_B = "b" * 32


def _session(n_tasks: int = 5):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[CompanionScanTask.__table__])
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    for i in range(n_tasks):
        db.add(CompanionScanTask(
            region="americas", battle_id_start=100 + i * 50,
            battle_id_end=149 + i * 50, status="pending",
        ))
    db.commit()
    return db


def test_mesma_instalacao_recebe_o_mesmo_range():
    db = _session()
    primeiro = claim_task(db, PC_A)
    # 2 processos a mais no MESMO PC (o cenário do rebuild).
    assert claim_task(db, PC_A).id == primeiro.id
    assert claim_task(db, PC_A).id == primeiro.id
    assert db.query(CompanionScanTask).filter_by(status="claimed").count() == 1


def test_instalacoes_diferentes_recebem_ranges_diferentes():
    db = _session()
    a = claim_task(db, PC_A)
    b = claim_task(db, PC_B)
    assert a.id != b.id, "PCs diferentes devem escanear ranges diferentes"


def test_contagem_conta_instalacao_nao_processo():
    db = _session()
    for _ in range(3):
        claim_task(db, PC_A)
    assert count_active_companions(db) == 1
    claim_task(db, PC_B)
    assert count_active_companions(db) == 2


def test_companion_sem_header_mantem_comportamento_antigo():
    db = _session()
    a = claim_task(db, None)
    b = claim_task(db, None)
    assert a.id != b.id, "sem install_id não dá pra agrupar — pega range novo"
    assert count_active_companions(db) == 0, "anônimo não entra na contagem"


if __name__ == "__main__":
    test_mesma_instalacao_recebe_o_mesmo_range()
    test_instalacoes_diferentes_recebem_ranges_diferentes()
    test_contagem_conta_instalacao_nao_processo()
    test_companion_sem_header_mantem_comportamento_antigo()
    print("companion install_id OK")
