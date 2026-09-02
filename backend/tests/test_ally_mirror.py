"""Espelho de aliado pra própria guilda — quando o registration_checker valida
um aliado na guilda A e a guilda Albion dele é uma Guild B no Ziggs com
register_role_id configurado, cria BotRegistration espelho em B (is_ally=False)
e atribui o cargo no Discord de B. Idempotente: reativar linha inativa não
duplica.

Run directly: PYTHONPATH=. python tests/test_ally_mirror.py
"""
import asyncio
from types import SimpleNamespace

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.audit import AuditLog
from app.models.base import Base
from app.models.registration import BotRegistration
from app.models.tenancy import Guild
from app.services import registration_checker as rc


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):  # pragma: no cover
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # pragma: no cover
    return "INTEGER"


def _setup():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        Guild.__table__, BotRegistration.__table__, AuditLog.__table__,
    ])
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    # Guilda Ziggs A (onde o aliado está registrado como aliado)
    db.add(Guild(id=100, name="ZiggsA", albion_guild_id="GA",
                 albion_alliance_id="AL1",
                 settings={"register_role_id": "1000",
                           "ally_allowed_guilds": ["GB"],
                           "register_remove_role_on_leave": True}))
    # Guilda Ziggs B (a guilda Albion PRÓPRIA do aliado) — ativa registration
    db.add(Guild(id=200, name="ZiggsB", albion_guild_id="GB",
                 albion_alliance_id="AL1",
                 settings={"register_role_id": "2000",
                           "ally_allowed_guilds": ["none"],
                           "register_remove_role_on_leave": True}))
    # Guilda Ziggs C sem registration ativo (sem register_role_id)
    db.add(Guild(id=300, name="ZiggsC", albion_guild_id="GC",
                 albion_alliance_id="AL1",
                 settings={}))
    db.commit()
    return db


def _ally_reg(guild_id, player_id, user_id, active=True):
    return BotRegistration(
        guild_id=guild_id, discord_user_id=user_id, albion_player_id=player_id,
        albion_player_name="AllyPlayer", region="americas", role_id=1000,
        is_ally=True, active=active,
    )


async def _run_mirrors_no_apply(db, *calls):
    """Roda _mirror_ally_to_own_guild com bot_token=None (sem aplicar cargo)
    pra isolar a lógica de banco. Cada call é a tupla completa de args
    posicionais (incluindo o db do snapshot) — não repete db."""
    for call in calls:
        await rc._mirror_ally_to_own_guild(*call, bot_token=None)


def test_mirror_creates_registration_in_own_guild():
    db = _setup()
    ally = _ally_reg(100, "pid_albion", 42)
    db.add(ally)
    db.commit()
    asyncio.run(_run_mirrors_no_apply(
        db,
        (db, ally, "GB", "AllyPlayer", "AL1", "pid_albion", "americas"),
    ))
    # Linha espelho na guilda 200 (GB) com is_ally=False e role_id=2000
    mirror = db.scalar(select(BotRegistration).where(
        BotRegistration.guild_id == 200,
        BotRegistration.albion_player_id == "pid_albion",
        BotRegistration.discord_user_id == 42,
    ))
    assert mirror is not None, "espelho não foi criado"
    assert mirror.active is True
    assert mirror.is_ally is False
    assert mirror.role_id == 2000
    # AuditLog com action=registration.mirror_ally
    entries = db.scalars(select(AuditLog).where(AuditLog.guild_id == 200)).all()
    assert any(e.action == "registration.mirror_ally" for e in entries)


def test_mirror_is_idempotent_when_line_exists_and_active():
    db = _setup()
    ally = _ally_reg(100, "pid_albion", 42)
    db.add(ally)
    # Linha já ativa na guilda 200
    db.add(BotRegistration(
        guild_id=200, discord_user_id=42, albion_player_id="pid_albion",
        albion_player_name="AllyPlayer", region="americas", role_id=2000,
        is_ally=False, active=True,
    ))
    db.commit()
    asyncio.run(_run_mirrors_no_apply(
        db,
        (db, ally, "GB", "AllyPlayer", "AL1", "pid_albion", "americas"),
    ))
    # Continua com 1 linha na guilda 200 (não duplica)
    rows = db.scalars(select(BotRegistration).where(
        BotRegistration.guild_id == 200,
        BotRegistration.albion_player_id == "pid_albion",
        BotRegistration.discord_user_id == 42,
    )).all()
    assert len(rows) == 1


def test_mirror_reactivates_inactive_line():
    db = _setup()
    ally = _ally_reg(100, "pid_albion", 42)
    db.add(ally)
    # Linha INATIVA na guilda 200 (saiu da guilda própria, voltou)
    db.add(BotRegistration(
        guild_id=200, discord_user_id=42, albion_player_id="pid_albion",
        albion_player_name="OldName", region="europe", role_id=2000,
        is_ally=False, active=False,
    ))
    db.commit()
    asyncio.run(_run_mirrors_no_apply(
        db,
        (db, ally, "GB", "AllyPlayer", "AL1", "pid_albion", "americas"),
    ))
    rows = db.scalars(select(BotRegistration).where(
        BotRegistration.guild_id == 200,
        BotRegistration.albion_player_id == "pid_albion",
        BotRegistration.discord_user_id == 42,
    )).all()
    assert len(rows) == 1
    assert rows[0].active is True
    assert rows[0].albion_player_name == "AllyPlayer"  # atualizou


def test_mirror_skips_when_own_guild_has_no_register_role():
    db = _setup()
    ally = _ally_reg(100, "pid_albion", 42)
    db.add(ally)
    db.commit()
    # GC = guilda 300 sem register_role_id — não deve espelhar
    asyncio.run(_run_mirrors_no_apply(
        db,
        (db, ally, "GC", "AllyPlayer", "AL1", "pid_albion", "americas"),
    ))
    rows = db.scalars(select(BotRegistration).where(
        BotRegistration.guild_id == 300,
        BotRegistration.albion_player_id == "pid_albion",
        BotRegistration.discord_user_id == 42,
    )).all()
    assert len(rows) == 0


def test_mirror_skips_when_player_guild_id_not_in_ziggs():
    db = _setup()
    ally = _ally_reg(100, "pid_albion", 42)
    db.add(ally)
    db.commit()
    # "GZ" não é nenhuma guilda no Ziggs — não espelha
    asyncio.run(_run_mirrors_no_apply(
        db,
        (db, ally, "GZ", "AllyPlayer", "AL1", "pid_albion", "americas"),
    ))
    rows = db.scalars(select(BotRegistration).where(
        BotRegistration.guild_id.in_([100, 200, 300]),
        BotRegistration.albion_player_id == "pid_albion",
    )).all()
    # Só a linha original de aliado em 100
    assert len(rows) == 1
    assert rows[0].guild_id == 100


if __name__ == "__main__":
    test_mirror_creates_registration_in_own_guild()
    test_mirror_is_idempotent_when_line_exists_and_active()
    test_mirror_reactivates_inactive_line()
    test_mirror_skips_when_own_guild_has_no_register_role()
    test_mirror_skips_when_player_guild_id_not_in_ziggs()
    print("ally mirror OK")