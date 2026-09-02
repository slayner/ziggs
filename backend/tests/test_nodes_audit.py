"""Nodes service — auditoria de adição/deleção/captura vai pro AuditLog.

Run directly: PYTHONPATH=. python tests/test_nodes_audit.py
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.audit import AuditLog
from app.models.base import Base
from app.models.nodes import NodeEvent, NodeEventLog
from app.models.tenancy import Guild
from app.services import nodes as svc


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):  # pragma: no cover
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # pragma: no cover - test-only shim
    return "INTEGER"


def _setup():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        Guild.__table__, NodeEvent.__table__, NodeEventLog.__table__, AuditLog.__table__,
    ])
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    db.add(Guild(id=1, name="Teste"))
    db.commit()
    return db


def test_add_event_creates_nodeevent_nodeeventlog_and_auditlog():
    db = _setup()
    spawn = datetime.now(timezone.utc) + timedelta(hours=1)
    row = svc.add_event(
        db, 1, "Avalon", "Caerleon", spawn,
        added_by_id=42, added_by_name="scout",
    )
    db.commit()
    # NodeEvent criado
    assert db.get(NodeEvent, row.id) is not None
    # NodeEventLog criado (auditoria permanente de scout)
    logs = db.scalars(select(NodeEventLog).where(NodeEventLog.guild_id == 1)).all()
    assert len(logs) == 1
    assert logs[0].scout_id == 42
    # AuditLog criado com action=node.add
    entries = db.scalars(select(AuditLog)).all()
    actions = [e.action for e in entries]
    assert "node.add" in actions
    audit = next(e for e in entries if e.action == "node.add")
    assert audit.actor_id == 42
    assert audit.entity == "node_event"
    assert audit.after["node_type"] == "Avalon"


def test_delete_event_creates_auditlog_with_before():
    db = _setup()
    spawn = datetime.now(timezone.utc) + timedelta(hours=1)
    row = svc.add_event(db, 1, "Avalon", "Caerleon", spawn, added_by_id=42, added_by_name="scout")
    db.commit()
    ok = svc.delete_event(db, 1, row.id, actor_id=99, actor_source="site")
    db.commit()
    assert ok
    entries = db.scalars(select(AuditLog)).all()
    delete_audit = next((e for e in entries if e.action == "node.delete"), None)
    assert delete_audit is not None
    assert delete_audit.actor_id == 99
    assert delete_audit.before["node_type"] == "Avalon"


def test_delete_event_unknown_returns_false_no_audit():
    db = _setup()
    ok = svc.delete_event(db, 1, 99999)
    db.commit()
    assert ok is False
    entries = db.scalars(select(AuditLog)).all()
    assert all(e.action != "node.delete" for e in entries)


if __name__ == "__main__":
    test_add_event_creates_nodeevent_nodeeventlog_and_auditlog()
    test_delete_event_creates_auditlog_with_before()
    test_delete_event_unknown_returns_false_no_audit()
    print("nodes audit OK")