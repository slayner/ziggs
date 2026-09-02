"""Self-check de concorrência do /bot/economy/pay em PostgreSQL real.

Roda direto: PYTHONPATH=. ZIGGS_TEST_DATABASE_URL=... scripts/python.exe tests/test_economy_pay_pg.py
"""
from concurrent.futures import ThreadPoolExecutor
import os
import threading

from fastapi import HTTPException
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker

from app.api.routes.auth import EconomyPayIn, bot_economy_pay
from app.config import get_settings
from app.models.audit import AuditLog
from app.models.economy import EconomyBalance, EconomyTransaction
from app.models.tenancy import Guild

DB_URL = os.environ.get("ZIGGS_TEST_DATABASE_URL")
GUILD_ID = 9_999_999_999_999_991
SENDER_ID = 9_999_999_999_999_992
RECEIVER_ID = 9_999_999_999_999_993


def _pay(session_factory, request_id: str, barrier: threading.Barrier) -> dict:
    with session_factory() as db:
        barrier.wait()
        return bot_economy_pay(
            GUILD_ID,
            EconomyPayIn(
                from_user_id=SENDER_ID,
                to_user_id=RECEIVER_ID,
                amount=80,
                request_id=request_id,
            ),
            f"Bearer {get_settings().bot_api_secret}",
            db,
        )


def main() -> None:
    if not DB_URL:
        raise RuntimeError("Defina ZIGGS_TEST_DATABASE_URL para rodar este teste")
    engine = create_engine(DB_URL)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with sessions.begin() as db:
        db.execute(delete(AuditLog).where(AuditLog.guild_id == GUILD_ID))
        db.execute(delete(EconomyTransaction).where(EconomyTransaction.guild_id == GUILD_ID))
        db.execute(delete(EconomyBalance).where(EconomyBalance.guild_id == GUILD_ID))
        db.execute(delete(Guild).where(Guild.id == GUILD_ID))
        db.add(Guild(id=GUILD_ID, name="Teste economia concorrente"))
        db.add(EconomyBalance(
            guild_id=GUILD_ID,
            discord_user_id=SENDER_ID,
            balance=100,
            total_earned=100,
        ))

    barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda request_id: _pay(sessions, request_id, barrier),
            ("economy-pay-pg-1", "economy-pay-pg-2"),
        ))
    assert sorted(result["ok"] for result in results) == [False, True], results

    with sessions() as db:
        balances = {
            balance.discord_user_id: balance
            for balance in db.scalars(select(EconomyBalance).where(
                EconomyBalance.guild_id == GUILD_ID,
            )).all()
        }
        assert balances[SENDER_ID].balance == 20
        assert balances[RECEIVER_ID].balance == 80
        assert balances[RECEIVER_ID].total_earned == 80
        assert db.query(EconomyTransaction).filter_by(guild_id=GUILD_ID, kind="pay").count() == 1
        try:
            bot_economy_pay(
                GUILD_ID,
                EconomyPayIn(from_user_id=SENDER_ID, to_user_id=SENDER_ID, amount=1),
                f"Bearer {get_settings().bot_api_secret}",
                db,
            )
        except HTTPException as exc:
            assert exc.status_code == 400
        else:
            raise AssertionError("auto pagamento foi aceito")

    with sessions.begin() as db:
        db.execute(delete(AuditLog).where(AuditLog.guild_id == GUILD_ID))
        db.execute(delete(EconomyTransaction).where(EconomyTransaction.guild_id == GUILD_ID))
        db.execute(delete(EconomyBalance).where(EconomyBalance.guild_id == GUILD_ID))
        db.execute(delete(Guild).where(Guild.id == GUILD_ID))
    engine.dispose()
    print("economy pay PG OK")


if __name__ == "__main__":
    main()
