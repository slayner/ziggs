"""
Testes do crédito found_by no report do companion — sem rede, sqlite em
memória. Roda com pytest OU direto:
    PYTHONPATH=. python tests/test_companion_found_by.py
"""
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.battles import Battle, BattleGuild, BattleIdProbe
from app.models.companion import CompanionScanTask
from app.services.companion_scan import report_task


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        Battle.__table__, BattleGuild.__table__, BattleIdProbe.__table__,
        CompanionScanTask.__table__,
    ])
    return sessionmaker(bind=engine)()


def _claimed_task(db) -> CompanionScanTask:
    task = CompanionScanTask(
        region="americas", battle_id_start=100, battle_id_end=149,
        status="claimed",
    )
    db.add(task)
    db.commit()
    return task


def _raw(albion_id: int) -> dict:
    return {"id": albion_id, "startTime": "2026-07-15T00:00:00Z", "totalFame": 1}


def test_batalha_nova_ganha_found_by():
    db = _session()
    task = _claimed_task(db)
    report_task(db, task.id, [_raw(100)], [], [], character_name="Slayner")
    b = db.query(Battle).one()
    assert b.found_by == "Slayner"


def test_batalha_ja_conhecida_nao_recredita():
    # batalha que o sweeper do servidor já tinha → companion re-reportar não rouba o crédito
    db = _session()
    db.add(Battle(
        region="americas", albion_id="100",
        start_time=datetime(2026, 7, 15, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
    ))
    db.commit()
    task = _claimed_task(db)
    report_task(db, task.id, [_raw(100)], [], [], character_name="Slayner")
    assert db.query(Battle).one().found_by is None


def test_nick_invalido_e_ignorado():
    # endpoint sem auth: só nick no formato do Albion (alfanumérico 3-16) vira crédito
    db = _session()
    task = _claimed_task(db)
    report_task(db, task.id, [_raw(100)], [], [], character_name="<script>alert(1)</script>")
    assert db.query(Battle).one().found_by is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("OK")
