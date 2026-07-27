from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.api.routes.auth import (
    EconomyAddIn,
    EconomyUndoIn,
    bot_economy_add,
    bot_economy_undo,
)
from app.config import get_settings
from app.models.base import Base
from app.models.economy import EconomyBalance, EconomyTransaction
from app.models.tenancy import Guild


def test_economy_command_replay_does_not_apply_twice():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        Guild.__table__,
        EconomyBalance.__table__,
        EconomyTransaction.__table__,
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
