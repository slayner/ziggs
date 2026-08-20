"""Testes do checkpoint por timestamp + precificação on-demand + postable_cutoff.

Estes testes cobrem a mudança de cursor por id interno → timestamp do jogo:
- Bug 1 (fora de ordem): kill descoberta tardiamente tem id MAIOR mas timestamp
  MENOR → no cursor por id seria postada depois de kills mais recentes.
- Bug 2 (kill perdida): kill precificada depois do cursor avançar → nunca
  mais retornada (id <= since_id). On-demand pricing resolve.

Roda sem rede nem Postgres — usa SQLite em memória + shims JSONB/BigInt.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import BigInteger, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
import app.models  # noqa: F401 — registra tudo no Base.metadata
from app.models.players import AlbionPlayer, PlayerKillEvent
from app.models.tenancy import Guild
from app.models.battles import Battle


# Shims pra rodar modelos Postgres-only em SQLite (mesmo padrão dos outros testes).
@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):  # pragma: no cover
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # pragma: no cover
    return "INTEGER"


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _make_guild(gid=1, **settings_overrides):
    """Guild válida pra insert no SQLite de teste (campos NOT NULL preenchidos)."""
    settings = {"juicy_kill_channel_id": "123", "juicy_kill_min_silver": 50_000_000}
    settings.update(settings_overrides)
    return Guild(id=gid, name=f"TestGuild{gid}", settings=settings)


def _make_db():
    """SQLite em memória + sessão async compatível com os selects do endpoint."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    engine = create_async_engine("sqlite+aiosqlite://", future=True)
    import app.models  # noqa
    async def init():
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c))
    asyncio.run(init())
    return engine


def _seed_kills(session, kills):
    """Insere kills a partir de uma lista de dicts {id, timestamp, region, ...}."""
    for k in kills:
        ev = PlayerKillEvent(
            id=k["id"],
            region=k.get("region", "americas"),
            albion_event_id=str(k["id"]) + "_ev",
            timestamp=k["timestamp"],
            fame=k.get("fame", 1_000_000),
            silver_dropped=k.get("silver_dropped"),
            participant_count=k.get("participant_count", 1),
            is_solo=k.get("is_solo", True),
            killer_player_id=k.get("killer_player_id"),
            victim_player_id=k.get("victim_player_id"),
            victim_equipment=k.get("victim_equipment"),
            victim_inventory=k.get("victim_inventory"),
        )
        session.add(ev)
    session.commit()


def test_juicy_queue_ordena_por_timestamp_nao_por_id():
    """Bug 1: kill com id maior mas timestamp menor (descoberta tardia) deve
    aparecer PRIMEIRO no queue (ordem cronológica), não depois."""
    from app.api.routes import auth
    from app.config import get_settings
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

    engine = create_async_engine("sqlite+aiosqlite://", future=True)

    async def run():
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c))
        async with AsyncSession(engine) as session:
            # Kill NOVA (timestamp recente, id alto) — descoberta primeiro
            t_new = datetime.now(timezone.utc) - timedelta(hours=1)
            # Kill VELHA (timestamp antigo, id MAIS ALTO ainda) — descoberta tardiamente
            t_old = datetime.now(timezone.utc) - timedelta(hours=3)

            guild = _make_guild(1, juicy_kill_last_ts=(datetime.now(timezone.utc) - timedelta(hours=5)).isoformat())
            session.add(guild)

            # Kill velha tem id MAIOR (inserida depois no banco) mas timestamp MENOR.
            # No cursor por id, ela viria DEPOIS da kill nova (errado — fora da ordem
            # cronológica). Por timestamp, vem primeiro (certo).
            session.add(PlayerKillEvent(
                id=10, region="americas", albion_event_id="10_ev",
                timestamp=t_new, fame=1_000_000, silver_dropped=60_000_000,
                participant_count=5, is_solo=False, group_member_count=5,
            ))
            session.add(PlayerKillEvent(
                id=20, region="americas", albion_event_id="20_ev",
                timestamp=t_old, fame=1_000_000, silver_dropped=70_000_000,
                participant_count=5, is_solo=False, group_member_count=5,
            ))
            await session.commit()

            # Mock: sem cutoff (kills recentes demais pra cair no cutoff)
            with patch("app.services.postable.postable_cutoffs_by_region",
                        new=AsyncMock(return_value={"americas": datetime(2020, 1, 1, tzinfo=timezone.utc)})), \
                 patch("app.api.routes.auth._price_kills_on_demand", new=AsyncMock()):
                authorization = f"Bearer {get_settings().bot_api_secret}"
                resp = await auth.bot_juicy_kill_queue(1, authorization, session)

        ids = [k["id"] for k in resp["kills"]]
        # Ordem cronológica: velha (id=20, t_old) ANTES da nova (id=10, t_new).
        # No cursor por id seria [10, 20] (errado). Por timestamp é [20, 10] (certo).
        assert ids == [20, 10], f"ordem cronológica quebrada: {ids}"

    asyncio.run(run())
    print("checkpoint por timestamp OK — ordem cronológica respeitada")


def test_juicy_queue_precifica_null_on_demand():
    """Bug 2: kill com silver_dropped=NULL dentro do horizonte deve ser
    precificada on-demand (não ignorada pra sempre)."""
    from app.api.routes import auth
    from app.config import get_settings
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite://", future=True)

    async def run():
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c))
        async with AsyncSession(engine) as session:
            guild = _make_guild(1, juicy_kill_last_ts=(datetime.now(timezone.utc) - timedelta(hours=5)).isoformat())
            session.add(guild)
            # Kill com silver_dropped=NULL — seria perdida no cursor por id.
            session.add(PlayerKillEvent(
                id=10, region="americas", albion_event_id="10_ev",
                timestamp=datetime.now(timezone.utc) - timedelta(hours=1),
                fame=1_000_000, silver_dropped=None,
                participant_count=5, is_solo=False, group_member_count=5,
                victim_equipment={"MainHand": {"Type": "T8_2H_BOW@4"}},
            ))
            await session.commit()

            async def fake_price(db, events):
                for ev in events:
                    ev.silver_dropped = 60_000_000

            with patch("app.services.postable.postable_cutoffs_by_region",
                        new=AsyncMock(return_value={"americas": datetime(2020, 1, 1, tzinfo=timezone.utc)})), \
                 patch("app.api.routes.auth._price_kills_on_demand", new=fake_price):
                authorization = f"Bearer {get_settings().bot_api_secret}"
                resp = await auth.bot_juicy_kill_queue(1, authorization, session)

        # Kill foi precificada on-demand e retornada (não perdida).
        assert len(resp["kills"]) == 1, f"kill NULL deveria ser precificada e retornada: {resp}"
        assert resp["kills"][0]["silver_dropped"] == 60_000_000

    asyncio.run(run())
    print("precificação on-demand OK — kill NULL não é perdida")


def test_juicy_queue_filtra_por_postable_cutoff():
    """Kill mais antiga que cutoff (48h + delay) NÃO deve ser postada."""
    from app.api.routes import auth
    from app.config import get_settings
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite://", future=True)

    async def run():
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c))
        async with AsyncSession(engine) as session:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=50)  # 50h atrás
            guild = _make_guild(1, juicy_kill_last_ts=(datetime.now(timezone.utc) - timedelta(hours=100)).isoformat())
            session.add(guild)
            # Kill VELHA (antes do cutoff) — não deve ser postada.
            session.add(PlayerKillEvent(
                id=10, region="americas", albion_event_id="10_ev",
                timestamp=datetime.now(timezone.utc) - timedelta(hours=72),
                fame=1_000_000, silver_dropped=60_000_000,
                participant_count=5, is_solo=False, group_member_count=5,
            ))
            # Kill NOVA (depois do cutoff) — deve ser postada.
            session.add(PlayerKillEvent(
                id=20, region="americas", albion_event_id="20_ev",
                timestamp=datetime.now(timezone.utc) - timedelta(hours=1),
                fame=1_000_000, silver_dropped=60_000_000,
                participant_count=5, is_solo=False, group_member_count=5,
            ))
            await session.commit()

            with patch("app.services.postable.postable_cutoffs_by_region",
                        new=AsyncMock(return_value={"americas": cutoff})), \
                 patch("app.api.routes.auth._price_kills_on_demand", new=AsyncMock()):
                authorization = f"Bearer {get_settings().bot_api_secret}"
                resp = await auth.bot_juicy_kill_queue(1, authorization, session)

        ids = [k["id"] for k in resp["kills"]]
        assert ids == [20], f"kill velha (id=10) não deveria ser postada: {ids}"

    asyncio.run(run())
    print("postable_cutoff OK — kill velha filtrada")


def test_juicy_synced_watermark_monotonic():
    """Watermark só avança pra frente (timestamp maior), nunca pra trás."""
    from app.api.routes import auth
    from app.config import get_settings

    guild = SimpleNamespace(settings={"juicy_kill_last_ts": "2026-06-01T00:00:00+00:00"})

    class Db:
        async def scalar(self, _q):
            return guild
        async def commit(self):
            pass

    authz = f"Bearer {get_settings().bot_api_secret}"

    # Timestamp MENOR → não avança (mantém watermark atual).
    asyncio.run(auth.bot_juicy_kill_synced(
        1, auth.JuicyKillSyncedIn(last_ts=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        authz, Db(),
    ))
    assert guild.settings["juicy_kill_last_ts"] == "2026-06-01T00:00:00+00:00"

    # Timestamp MAIOR → avança.
    asyncio.run(auth.bot_juicy_kill_synced(
        1, auth.JuicyKillSyncedIn(last_ts=datetime(2026, 7, 1, tzinfo=timezone.utc)),
        authz, Db(),
    ))
    assert guild.settings["juicy_kill_last_ts"] == "2026-07-01T00:00:00+00:00"

    print("watermark monotônico OK")


def test_parse_watermark_nao_quebra_com_none_ou_garbage():
    from app.api.routes import auth
    assert auth._parse_watermark(None) is None
    assert auth._parse_watermark("") is None
    assert auth._parse_watermark("garbage") is None
    dt = auth._parse_watermark("2026-01-01T00:00:00+00:00")
    assert dt is not None and dt.tzinfo is not None
    # Sem tzinfo → assume UTC
    dt2 = auth._parse_watermark("2026-01-01T00:00:00")
    assert dt2 is not None and dt2.tzinfo == timezone.utc
    print("parse watermark OK")


if __name__ == "__main__":
    test_parse_watermark_nao_quebra_com_none_ou_garbage()
    test_juicy_synced_watermark_monotonic()
    test_juicy_queue_ordena_por_timestamp_nao_por_id()
    test_juicy_queue_precifica_null_on_demand()
    test_juicy_queue_filtra_por_postable_cutoff()
    print("checkpoint por timestamp: OK")