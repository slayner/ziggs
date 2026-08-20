import asyncio
from types import SimpleNamespace

import cogs.audit_log as audit_log


async def main() -> None:
    pending = [{
        "id": 1, "actor_id": "9", "actor_type": "bot", "source": "bot",
        "action": "economy.add", "entity": "balance", "entity_id": "7",
        "before": {"balance": 0}, "after": {"balance": 100},
        "note": "transaction #1", "created_at": "2026-08-17T00:00:00+00:00",
    }]
    sent, acknowledgements = [], []
    fail_ack = True

    class Channel:
        async def send(self, *, embed):
            sent.append(embed.title)

    cog = audit_log.BotAuditLog(SimpleNamespace())
    original = (audit_log._guild_command_config, audit_log._get, audit_log._post, audit_log.guild_lang_for)

    async def config(_guild_id):
        return {"bot_logs_enabled": True}

    async def get(_path):
        return {"entries": pending}

    async def post(_path, body):
        nonlocal fail_ack
        acknowledgements.append(body["last_id"])
        if fail_ack:
            return None
        pending.clear()
        return {"ok": True}

    async def lang(_guild_id):
        return "pt"

    async def channel(_guild):
        return Channel()

    audit_log._guild_command_config, audit_log._get, audit_log._post, audit_log.guild_lang_for = config, get, post, lang
    cog.ensure_logs_channel = channel
    try:
        guild = SimpleNamespace(id=1)
        await cog._sync_guild_unlocked(guild)
        fail_ack = False
        await cog._sync_guild_unlocked(guild)
        await cog._sync_guild_unlocked(guild)
    finally:
        audit_log._guild_command_config, audit_log._get, audit_log._post, audit_log.guild_lang_for = original

    assert sent == ["economy.add", "economy.add"]
    assert acknowledgements == [1, 1]

    class Guild:
        id = 1
        text_channels = []
        default_role = object()

        def get_channel(self, _channel_id):
            return None

        async def fetch_channel(self, _channel_id):
            return object()

        async def create_text_channel(self, **_kwargs):
            raise AssertionError("configured channel must not fall back to logs-bot")

    async def configured(_guild_id):
        return {"logs_channel_id": "2"}

    cog = audit_log.BotAuditLog(SimpleNamespace())
    audit_log._guild_command_config = configured
    try:
        assert await cog.ensure_logs_channel(Guild()) is None
    finally:
        audit_log._guild_command_config = original[0]
    print("audit log delivery: ok")


async def test_hanging_fetch_channel_does_not_block() -> None:
    """fetch_channel que pendura indefinidamente deve retornar None após o
    timeout, em vez de paralisar o loop para sempre."""
    import time as _time

    class HangingGuild:
        id = 1
        text_channels = []
        default_role = object()

        def get_channel(self, _cid):
            return None

        async def fetch_channel(self, _cid):
            await asyncio.sleep(3600)  # "pendura" — nunca retorna
            raise AssertionError("não devia chegar aqui")

    async def configured(_guild_id):
        return {"logs_channel_id": "2"}

    original = audit_log._guild_command_config
    audit_log._guild_command_config = configured
    cog = audit_log.BotAuditLog(SimpleNamespace())
    try:
        t0 = _time.monotonic()
        result = await asyncio.wait_for(cog.ensure_logs_channel(HangingGuild()), timeout=30)
        elapsed = _time.monotonic() - t0
        assert result is None, f"esperado None, got {result}"
        assert elapsed < 20, f"fetch_channel pendurou {elapsed:.1f}s — timeout não funcionou"
    finally:
        audit_log._guild_command_config = original
    print("hanging fetch_channel timeout: ok")


if __name__ == "__main__":
    asyncio.run(main())
    asyncio.run(test_hanging_fetch_channel_does_not_block())
