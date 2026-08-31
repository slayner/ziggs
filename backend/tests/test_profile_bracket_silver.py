"""Prata agregada das brackets na atividade do perfil."""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import BigInteger
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.api.routes.players import _battle_silver_by_battle, _is_juicy_bracket
from app.models.base import Base
from app.models.battles import Battle, BattleKillEvent


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(_type, _compiler, **_kw):  # pragma: no cover
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(_type, _compiler, **_kw):  # pragma: no cover
    return "INTEGER"


def test_bracket_silver_soma_todas_as_mortes_e_classifica_acima_de_25m():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", future=True)
        async with engine.begin() as connection:
            await connection.run_sync(
                Base.metadata.create_all,
                tables=[Battle.__table__, BattleKillEvent.__table__],
            )
        now = datetime.now(timezone.utc)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add_all([
                Battle(id=1, region="americas", albion_id="battle-1", start_time=now, fetched_at=now),
                Battle(id=2, region="americas", albion_id="battle-2", start_time=now, fetched_at=now),
                BattleKillEvent(
                    id=1, battle_id=1, albion_event_id="death-1", timestamp=now,
                    fame=1, silver_dropped=24_000_000,
                ),
                BattleKillEvent(
                    id=2, battle_id=1, albion_event_id="death-2", timestamp=now,
                    fame=1, silver_dropped=2_000_000,
                ),
                BattleKillEvent(
                    id=3, battle_id=1, albion_event_id="pending", timestamp=now,
                    fame=1, silver_dropped=None,
                ),
                BattleKillEvent(
                    id=4, battle_id=2, albion_event_id="death-3", timestamp=now,
                    fame=1, silver_dropped=25_000_000,
                ),
            ])
            await db.commit()

            assert await _battle_silver_by_battle(db, [1, 2]) == {
                1: 26_000_000,
                2: 25_000_000,
            }

        await engine.dispose()

    asyncio.run(run())
    assert _is_juicy_bracket(25_000_000) is False
    assert _is_juicy_bracket(25_000_001) is True
