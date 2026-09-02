"""
Testes do crédito found_by no report do companion — sem rede, sqlite em
memória. Roda com pytest OU direto:
    PYTHONPATH=. python tests/test_companion_found_by.py
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.battles import Battle, BattleGuild, BattleIdProbe
from app.models.companion import CompanionScanTask
from app.services.companion_scan import report_task

INSTALL = "a" * 32


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
        status="claimed", claimed_by=INSTALL,
    )
    db.add(task)
    db.commit()
    return task


def _raw(albion_id: int) -> dict:
    # 12 jogadores — acima de DEEP_PROCESS_MIN_PLAYERS (10) pra ser armazenada.
    players = {str(i): {"Id": str(i), "Name": f"Player{i}"} for i in range(12)}
    return {"id": albion_id, "startTime": "2026-07-15T00:00:00Z", "totalFame": 1, "players": players}


class _Client:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass


def _report(db, task, ids, character_name=None, results=None):
    async def probe(_client, _host, aid):
        return (results or {}).get(int(aid), ("found", _raw(int(aid))))

    with patch("app.services.companion_scan.make_client", return_value=_Client()), patch(
        "app.services.companion_scan._probe_detail", probe,
    ):
        return asyncio.run(report_task(
            db, task.id, ids, [], [], INSTALL, "americas",
            character_name=character_name,
        ))


def test_batalha_nova_ganha_found_by():
    db = _session()
    task = _claimed_task(db)
    _report(db, task, [100], character_name="Slayner")
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
    _report(db, task, [100], character_name="Slayner")
    assert db.query(Battle).one().found_by is None


def test_nick_invalido_e_ignorado():
    # endpoint sem auth: só nick no formato do Albion (alfanumérico 3-16) vira crédito
    db = _session()
    task = _claimed_task(db)
    _report(db, task, [100], character_name="<script>alert(1)</script>")
    assert db.query(Battle).one().found_by is None


def test_report_exige_dono_e_range_reivindicado():
    db = _session()
    task = _claimed_task(db)
    for install, aid in (("b" * 32, 100), (INSTALL, 150)):
        try:
            asyncio.run(report_task(db, task.id, [aid], [], [], install, "americas"))
        except (PermissionError, ValueError):
            pass
        else:
            raise AssertionError("report inválido foi aceito")


def test_status_e_payload_vem_da_api_oficial():
    db = _session()
    task = _claimed_task(db)
    results = {
        100: ("missing", None),
        101: ("found", _raw(101)),
        102: ("error", None),
    }

    async def probe(_client, _host, aid):
        return results[int(aid)]

    with patch("app.services.companion_scan.make_client", return_value=_Client()), patch(
        "app.services.companion_scan._probe_detail", probe,
    ):
        asyncio.run(report_task(
            db, task.id, [100], [101], [102], INSTALL, "americas",
        ))

    assert db.scalar(select(Battle.id).where(Battle.albion_id == "101")) is not None
    assert db.get(BattleIdProbe, {"region": "americas", "albion_id": "100"}).status == "missing"
    assert db.get(BattleIdProbe, {"region": "americas", "albion_id": "101"}).status == "found"
    assert db.get(BattleIdProbe, {"region": "americas", "albion_id": "102"}) is None


def test_mesmo_id_em_regioes_diferentes_tem_checkpoints_independentes():
    db = _session()
    db.add_all([
        BattleIdProbe(region="europe", albion_id="100", status="missing"),
        BattleIdProbe(region="americas", albion_id="100", status="found"),
    ])
    db.commit()
    assert db.get(BattleIdProbe, {"region": "europe", "albion_id": "100"}).status == "missing"
    assert db.get(BattleIdProbe, {"region": "americas", "albion_id": "100"}).status == "found"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("OK")
