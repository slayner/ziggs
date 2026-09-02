"""Massinfo access bypass — persistência e listagem de registrados.

Run directly: PYTHONPATH=. python tests/test_massinfo_access.py
"""
import asyncio
from types import SimpleNamespace

from app.api.routes import auth as auth_routes
from app.config import get_settings


def _auth() -> str:
    return f"Bearer {get_settings().bot_api_secret}"


def _make_db(guild):
    """Mock mínimo de AsyncSession que devolve o guild mockado."""
    class Db:
        def __init__(self):
            self.guild = guild
            self.committed = False
            self.added: list = []

        async def scalar(self, _query):
            return self.guild

        async def scalars(self, stmt):
            # stmt é um select(...); retornamos o `.all()` do resultado
            # armazenado em guild._regs (lista de SimpleNamespace com
            # discord_user_id).
            class _Result:
                def __init__(self, items): self._items = items
                def all(self): return self._items
            return _Result(getattr(self.guild, "_regs", []))

        def add(self, obj):
            self.added.append(obj)

        async def commit(self):
            self.committed = True
    return Db()


def test_bypass_add_then_remove_is_idempotent():
    guild = SimpleNamespace(id=1, settings={})
    db = _make_db(guild)
    a = _auth()
    # Add uma vez
    r1 = asyncio.run(auth_routes.bot_set_massinfo_access_bypass(
        1, auth_routes.MassinfoAccessBypassIn(action="add", user_id="42"), a, db))
    assert r1["ok"]
    assert r1["bypass_user_ids"] == ["42"]
    # Add de novo (idempotente)
    r2 = asyncio.run(auth_routes.bot_set_massinfo_access_bypass(
        1, auth_routes.MassinfoAccessBypassIn(action="add", user_id="42"), a, db))
    assert r2["bypass_user_ids"] == ["42"]
    # Remove
    r3 = asyncio.run(auth_routes.bot_set_massinfo_access_bypass(
        1, auth_routes.MassinfoAccessBypassIn(action="remove", user_id="42"), a, db))
    assert r3["bypass_user_ids"] == []
    # Remove de novo (idempotente)
    r4 = asyncio.run(auth_routes.bot_set_massinfo_access_bypass(
        1, auth_routes.MassinfoAccessBypassIn(action="remove", user_id="42"), a, db))
    assert r4["bypass_user_ids"] == []


def test_bypass_persists_in_guild_settings():
    guild = SimpleNamespace(id=1, settings={})
    db = _make_db(guild)
    a = _auth()
    asyncio.run(auth_routes.bot_set_massinfo_access_bypass(
        1, auth_routes.MassinfoAccessBypassIn(action="add", user_id="100"), a, db))
    asyncio.run(auth_routes.bot_set_massinfo_access_bypass(
        1, auth_routes.MassinfoAccessBypassIn(action="add", user_id="200"), a, db))
    assert guild.settings["massinfo_access_bypass_user_ids"] == ["100", "200"]


def test_bypass_rejects_invalid_action():
    guild = SimpleNamespace(id=1, settings={})
    db = _make_db(guild)
    a = _auth()
    try:
        asyncio.run(auth_routes.bot_set_massinfo_access_bypass(
            1, auth_routes.MassinfoAccessBypassIn(action="nuke", user_id="42"), a, db))
        assert False, "devia rejeitar action inválido"
    except Exception:
        pass


def test_bypass_rejects_non_numeric_user_id():
    guild = SimpleNamespace(id=1, settings={})
    db = _make_db(guild)
    a = _auth()
    try:
        asyncio.run(auth_routes.bot_set_massinfo_access_bypass(
            1, auth_routes.MassinfoAccessBypassIn(action="add", user_id="not-a-snowflake"), a, db))
        assert False, "devia rejeitar user_id não-numérico"
    except Exception:
        pass


def test_registrations_all_returns_active_discord_ids():
    # A rota faz select(BotRegistration.discord_user_id) — scalars retorna os
    # valores diretos, não objetos. Mock devolve ints.
    guild = SimpleNamespace(id=1, settings={}, _regs=[11, 22, 33])
    db = _make_db(guild)
    a = _auth()
    out = asyncio.run(auth_routes.bot_registrations_all(1, a, db))
    assert set(out["discord_user_ids"]) == {"11", "22", "33"}, out


if __name__ == "__main__":
    test_bypass_add_then_remove_is_idempotent()
    test_bypass_persists_in_guild_settings()
    test_bypass_rejects_invalid_action()
    test_bypass_rejects_non_numeric_user_id()
    test_registrations_all_returns_active_discord_ids()
    print("massinfo access bypass OK")