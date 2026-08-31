import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("BOT_PUBLIC_URL", "https://ziggs.example")

import cogs.battle_feed as battle_feed


def test_reconhece_batalha_ja_enviada_apos_falha_no_ack():
    async def run():
        sent: list[str] = []
        acknowledgements: list[str] = []
        fail_ack = True

        class Channel:
            async def send(self, *, content):
                sent.append(content)

            async def history(self, *_args, **_kwargs):
                for content in reversed(sent):
                    yield SimpleNamespace(
                        author=SimpleNamespace(id=99), content=content,
                    )

        class Guild:
            id = 1

            def get_channel(self, _channel_id):
                return Channel()

        async def config(_guild_id):
            return {"battle_feed_channel_id": "55"}

        async def get(_path):
            return {"battles": [{
                "public_id": "abc123", "start_time": "2026-01-01T00:00:00+00:00",
            }]}

        async def post(_path, body):
            nonlocal fail_ack
            acknowledgements.append(body["last_ts"])
            if fail_ack:
                return None
            return {"ok": True}

        original = (battle_feed._guild_command_config, battle_feed._get, battle_feed._post)
        battle_feed._guild_command_config, battle_feed._get, battle_feed._post = config, get, post
        try:
            cog = battle_feed.BattleFeed(SimpleNamespace(user=SimpleNamespace(id=99)))
            await cog._sync_guild_unlocked(Guild())
            fail_ack = False
            await cog._sync_guild_unlocked(Guild())
        finally:
            battle_feed._guild_command_config, battle_feed._get, battle_feed._post = original

        assert sent == ["https://ziggs.example/abc123"]
        assert acknowledgements == ["2026-01-01T00:00:00+00:00"] * 2

    asyncio.run(run())


if __name__ == "__main__":
    test_reconhece_batalha_ja_enviada_apos_falha_no_ack()
    print("battle feed: ok")
