import asyncio
from types import SimpleNamespace

import cogs.events as events
from cogs.events import FunctionPickView, _build_function_prompt_embed, _signup_matches


async def main() -> None:
    # 30 opções de par (weapon, fn): 15 armas × dps/support — mesmo weapon em
    # fns diferentes é opção DISTINTA.
    options = []
    for index in range(30):
        fn = "dps" if index % 2 else "support"
        weapon_id = index // 2 + 1
        options.append({
            "key": f"w{weapon_id}:{fn}",
            "weapon_id": weapon_id,
            "weapon_name": f"Weapon {weapon_id}",
            "fn": fn,
        })
    keys = [o["key"] for o in options]
    view = FunctionPickView(
        event_id=9,
        guild_id=1,
        lang="pt",
        options=options,
        initial_options=keys,
        min_builds=2,
        discord_role_ids=[11, 22],
    )
    embed = view._review_embed()
    rendered = "\n".join(field.value for field in embed.fields)
    assert all(f"Weapon {o['weapon_id']}" in rendered for o in options)
    assert len(embed.fields) == 3
    assert all(field.name == "\u200b" and field.inline for field in embed.fields)
    lines = rendered.splitlines()
    assert all(line.startswith(("✨ ", "⚔️ ")) for line in lines)
    support_first = next(i for i, line in enumerate(lines) if line.startswith("✨"))
    dps_first = next(i for i, line in enumerate(lines) if line.startswith("⚔️"))
    assert support_first < dps_first
    assert "/30" not in embed.title
    assert view._minimum_error() is None
    assert view.discord_role_ids == [11, 22]
    assert _signup_matches({"ok": True, "options": keys}, keys)
    assert _signup_matches({"exists": True, "options": keys}, keys)
    assert not _signup_matches({"ok": True, "options": []}, keys)
    assert not _signup_matches({"ok": True, "functions": keys}, keys)  # legado não é identidade

    dm = _build_function_prompt_embed("pt", {
        "event_id": 9,
        "title": "CTA Teste",
        "comp_name": "Brawl",
        "scheduled_at": "2026-07-25T00:30:00+00:00",
        "reason": "defined",
    })
    assert "já estava inscrito" not in (dm.description or "")
    assert "presença" in (dm.description or "")
    assert dm.footer.text and "revisão" in dm.footer.text

    class FakeMessage:
        def __init__(self, message_id):
            self.id = message_id
            self.deleted = False

        async def delete(self):
            self.deleted = True

    old_message = FakeMessage(123)
    new_message = FakeMessage(456)

    class FakeDm:
        async def fetch_message(self, message_id):
            assert message_id == old_message.id
            return old_message

    class FakeUser:
        dm_channel = FakeDm()

        async def send(self, **_kwargs):
            return new_message

    user = FakeUser()
    guild = SimpleNamespace(id=1, get_member=lambda _user_id: user)
    cog = events.Events(SimpleNamespace(fetch_user=None))
    key = (1, 9, 7)
    old_signature = ("CTA Teste", "Brawl", "2026-07-25T00:30:00+00:00", "changed")
    cog._function_prompt_sent[key] = ("123", old_signature)
    original_lang = events.guild_lang_for

    async def fake_lang(_guild_id):
        return "pt"

    events.guild_lang_for = fake_lang
    try:
        sent = await cog.send_function_prompts(guild, [{
            "event_id": 9,
            "user_id": 7,
            "title": "CTA Teste",
            "comp_name": "Clap",
            "scheduled_at": "2026-07-25T00:30:00+00:00",
            "reason": "changed",
        }])
    finally:
        events.guild_lang_for = original_lang
    assert old_message.deleted
    assert sent == [{"event_id": 9, "user_id": 7, "message_id": "456"}]
    assert cog._function_prompt_sent[key][0] == "456"
    print("signup roles and DM embeds: ok")


if __name__ == "__main__":
    asyncio.run(main())
