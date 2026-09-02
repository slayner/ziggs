"""Run directly: python test_massinfo_access.py"""
import asyncio
from types import SimpleNamespace

import cogs.massinfo_access as ma


def _member(mid: int, *, view: bool = False, name: str = "u", bot: bool = False) -> SimpleNamespace:
    m = SimpleNamespace(id=mid, name=name, bot=bot, mention=f"<@{mid}>")
    m.roles = []
    return m


def _channel() -> SimpleNamespace:
    class _Perms:
        def __init__(self, view): self.view_channel = view
    ch = SimpleNamespace()
    ch._perms_for = {}

    def permissions_for(member):
        return ch._perms_for.get(member.id, _Perms(False))
    ch.permissions_for = permissions_for
    return ch


def test_has_massinfo_view():
    ch = _channel()
    m1, m2 = _member(1), _member(2)
    ch._perms_for[1] = SimpleNamespace(view_channel=True)
    ch._perms_for[2] = SimpleNamespace(view_channel=False)
    assert ma._has_massinfo_view(m1, ch) is True
    assert ma._has_massinfo_view(m2, ch) is False


def test_unregistered_with_access_filters_bots_registered_and_bypass():
    ch = _channel()
    ch._perms_for[1] = SimpleNamespace(view_channel=True)
    ch._perms_for[2] = SimpleNamespace(view_channel=True)
    ch._perms_for[3] = SimpleNamespace(view_channel=True)  # bot — fica de fora
    ch._perms_for[4] = SimpleNamespace(view_channel=True)  # bypass — fica de fora
    ch._perms_for[5] = SimpleNamespace(view_channel=False)  # sem acesso — fica de fora
    ch._perms_for[6] = SimpleNamespace(view_channel=True)  # registrado — fica de fora

    guild = SimpleNamespace(members=[
        _member(1, name="joao", view=True),
        _member(2, name="maria", view=True),
        _member(3, name="botuser", bot=True),
        _member(4, name="alt", view=True),
        _member(5, name="sem-acesso"),
        _member(6, name="registrado", view=True),
    ])
    registered = {6}
    bypass = {4}

    out = asyncio.run(ma._unregistered_with_access(guild, ch, registered, bypass))
    ids = {m.id for m in out}
    assert ids == {1, 2}, f"esperado {{1,2}}, got {ids}"


def test_build_embed_empty_and_with_members():
    e_empty = ma._build_embed("pt", [])
    assert "Todos" in e_empty.description or "registrados" in e_empty.description.lower()

    members = [_member(i, name=f"u{i}") for i in range(1, 4)]
    e = ma._build_embed("pt", members)
    # 3 menções + bloco de ações
    assert any("1." in f.value or "<@" in f.value for f in e.fields if f.name != "O que fazer")
    # campo de instruções sempre presente
    actions = next((f for f in e.fields if f.name == "O que fazer"), None)
    assert actions is not None
    assert "/register" in actions.value and "/bypass" in actions.value


async def _bypass_main() -> None:
    """Mocka _post e chama _do_bypass; valida o POST correto."""
    import cogs.massinfo_access as cog_mod
    posted: list[tuple[str, dict]] = []
    sent: list[str] = []
    target = SimpleNamespace(id=42, name="alvo", mention="<@42>")
    orig_post = cog_mod._post
    orig_lang = cog_mod.guild_lang

    async def fake_post(path, body):
        posted.append((path, body))
        return {"ok": True, "bypass_user_ids": [str(target.id)]}

    async def fake_lang(_int):
        return "pt"

    async def fake_send(*args, **kw):
        sent.append(args[0] if args else kw.get("content", ""))

    cog_mod._post = fake_post
    cog_mod.guild_lang = fake_lang

    interaction = SimpleNamespace(
        guild_id=1, guild=SimpleNamespace(id=1),
        response=SimpleNamespace(
            is_done=lambda: False,
            send=lambda *a, **kw: None,
            defer=lambda *a, **kw: asyncio.sleep(0),
        ),
        followup=SimpleNamespace(send=fake_send),
        user=SimpleNamespace(id=10),
    )

    try:
        await cog_mod._do_bypass(interaction, target)
    finally:
        cog_mod._post = orig_post
        cog_mod.guild_lang = orig_lang

    assert len(posted) == 1, f"esperado 1 POST, got {len(posted)}"
    path, body = posted[0]
    assert path == "/bot/guilds/1/massinfo-access/bypass"
    assert body == {"action": "add", "user_id": "42"}, body
    assert sent, "followup.send não foi chamado"
    assert "<@42>" in " ".join(sent)


if __name__ == "__main__":
    test_has_massinfo_view()
    test_unregistered_with_access_filters_bots_registered_and_bypass()
    test_build_embed_empty_and_with_members()
    asyncio.run(_bypass_main())
    print("massinfo_access: ok")