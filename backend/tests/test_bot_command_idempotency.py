from sqlalchemy import create_engine, func, select
from sqlalchemy import BigInteger
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.api.routes.auth import (
    EconomyAddIn,
    EconomyPayIn,
    EconomyRemoveIn,
    EconomyUndoIn,
    bot_economy_add,
    bot_economy_pay,
    bot_economy_remove,
    bot_economy_undo,
)
from app.config import get_settings
from app.models.audit import AuditLog
from app.models.base import Base
from app.models.economy import EconomyBalance, EconomyTransaction
from app.models.tenancy import Guild


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):  # pragma: no cover - test-only shim
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # pragma: no cover - test-only shim
    return "INTEGER"


def test_economy_command_replay_does_not_apply_twice():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        Guild.__table__,
        EconomyBalance.__table__,
        EconomyTransaction.__table__,
        AuditLog.__table__,
    ])
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    db.add(Guild(id=1, name="Teste", settings={}))
    db.commit()
    auth = f"Bearer {get_settings().bot_api_secret}"
    body = EconomyAddIn(
        discord_user_id=7,
        amount=100,
        actor_discord_id=9,
        request_id="interaction-1",
    )

    first = bot_economy_add(1, body, auth, db)
    replay = bot_economy_add(1, body, auth, db)

    assert replay["transaction_id"] == first["transaction_id"]
    assert db.scalar(select(EconomyBalance.balance)) == 100
    assert db.scalar(select(func.count()).select_from(EconomyTransaction)) == 1

    undo = EconomyUndoIn(request_id="interaction-2")
    first_undo = bot_economy_undo(1, first["transaction_id"], undo, auth, db)
    replay_undo = bot_economy_undo(1, first["transaction_id"], undo, auth, db)
    assert first_undo["ok"] and replay_undo["ok"]
    assert db.scalar(select(EconomyBalance.balance)) == 0


def test_economy_mutations_create_one_audit_log_each():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        Guild.__table__, EconomyBalance.__table__, EconomyTransaction.__table__, AuditLog.__table__,
    ])
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    db.add(Guild(id=1, name="Teste", settings={}))
    db.commit()
    auth = f"Bearer {get_settings().bot_api_secret}"

    add = EconomyAddIn(discord_user_id=7, amount=100, actor_discord_id=9, request_id="add-1")
    bot_economy_add(1, add, auth, db)
    bot_economy_add(1, add, auth, db)
    pay = EconomyPayIn(from_user_id=7, to_user_id=8, amount=30, request_id="pay-1")
    bot_economy_pay(1, pay, auth, db)
    bot_economy_pay(1, pay, auth, db)
    remove = EconomyRemoveIn(discord_user_id=8, amount=10, actor_discord_id=9, request_id="remove-1")
    bot_economy_remove(1, remove, auth, db)
    bot_economy_remove(1, remove, auth, db)

    logs = list(db.scalars(select(AuditLog).order_by(AuditLog.id)))
    assert [(log.action, log.actor_id, log.entity_id) for log in logs] == [
        ("economy.add", 9, "7"),
        ("economy.pay", 7, "8"),
        ("economy.remove", 9, "8"),
    ]
    assert logs[0].before == {"balance": 0}
    assert logs[0].after == {"balance": 100, "amount": 100}
    assert logs[1].before == {"from_balance": 100, "to_balance": 0}
    assert logs[1].after == {"from_balance": 70, "to_balance": 30, "amount": 30}
    assert logs[2].before == {"balance": 30}
    assert logs[2].after == {"balance": 20, "amount": 10}
    assert [log.note for log in logs] == ["transaction #1", "transaction #2", "transaction #3"]
