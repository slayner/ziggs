"""Regressões de attempts hedge e backoff regional."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import BigInteger, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.models.base import Base
from app.models.scan_worker import ScanHostRateState, ScanIngestPayload, ScanStreamState, ScanWorker, ScanWorkAttempt, ScanWorkTask
from app.services import scan_dispatcher


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(_type, _compiler, **_kw):
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(_type, _compiler, **_kw):
    return "INTEGER"


async def _session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        ScanWorkTask.__table__, ScanWorkAttempt.__table__, ScanHostRateState.__table__,
        ScanIngestPayload.__table__, ScanStreamState.__table__, ScanWorker.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_primeiro_sucesso_cancela_hedge_e_429_cria_backoff():
    asyncio.run(_test_primeiro_sucesso_cancela_hedge_e_429_cria_backoff())


async def _test_primeiro_sucesso_cancela_hedge_e_429_cria_backoff():
    engine, Session = await _session()
    original_now = scan_dispatcher._now
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    scan_dispatcher._now = lambda: now
    try:
        async with Session() as db:
            db.add(ScanWorkTask(
                region="europe", feed_type="profile", page_offset=0, status="claimed",
                claimed_by="primeiro", lease_token="a" * 32, claimed_at=now,
                claim_expires_at=now + timedelta(minutes=2),
            ))
            await db.flush()
            db.add_all([
                ScanWorkAttempt(task_id=1, worker_id="primeiro", lease_token="a" * 32,
                                claimed_at=now, expires_at=now + timedelta(minutes=2)),
                ScanWorkAttempt(task_id=1, worker_id="hedge", lease_token="b" * 32,
                                is_hedge=True, claimed_at=now, expires_at=now + timedelta(minutes=2)),
            ])
            await db.commit()
            accepted, rejected = await scan_dispatcher.report_work(
                db, "primeiro", 1, "a" * 32, 1, 0, data=[{}], upstream_status_code=200,
            )
            assert (accepted, rejected) == (1, 0)
            hedge = await db.scalar(select(ScanWorkAttempt).where(ScanWorkAttempt.worker_id == "hedge"))
            assert hedge.status == "cancelled"

            db.add(ScanWorkTask(
                region="asia", feed_type="profile", page_offset=0, status="claimed",
                claimed_by="rate", lease_token="c" * 32, claimed_at=now,
                claim_expires_at=now + timedelta(minutes=2),
            ))
            await db.flush()
            db.add(ScanWorkAttempt(task_id=2, worker_id="rate", lease_token="c" * 32,
                                   claimed_at=now, expires_at=now + timedelta(minutes=2)))
            await db.commit()
            await scan_dispatcher.report_work(
                db, "rate", 2, "c" * 32, 0, 1, upstream_status_code=429, backoff_seconds=17,
            )
            state = await db.scalar(select(ScanHostRateState).where(ScanHostRateState.region == "asia"))
            assert state.last_status_code == 429
            assert state.backoff_until == now + timedelta(seconds=17)
    finally:
        scan_dispatcher._now = original_now
        await engine.dispose()


if __name__ == "__main__":
    test_primeiro_sucesso_cancela_hedge_e_429_cria_backoff()
