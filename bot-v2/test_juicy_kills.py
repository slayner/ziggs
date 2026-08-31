import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("BOT_PUBLIC_URL", "https://ziggs.example")

from cogs.juicy_kills import _build_embed, _message_kill_id
import cogs.juicy_kills as juicy_kills


def test_embed_links_every_player_and_footer_is_site_only():
    embed = _build_embed({
        "id": 1, "region": "europe", "albion_event_id": "99",
        "api_delay_secs": 1801,
        "silver_dropped": 50_000_000, "fame": 1_000_000,
        "killer": {"name": "Killer"}, "victim": {"name": "Victim"},
        "participants": [{"name": "Killer"}, {"name": "Assist One"}],
    }, "kill.png")
    assert embed.title is None
    assert "## " in embed.description
    assert "/eu/Killer?activity=99" in embed.description
    assert "/eu/Victim?activity=99" in embed.description
    assert "/eu/Assist%20One" in embed.description
    assert "/eu/Assist%20One?activity=" not in embed.description
    assert "silver" not in embed.description
    assert "fame" not in embed.description
    assert "Europe" not in embed.description
    assert embed.footer.text == "https://ziggs.example · ziggs:juicy-kill:1 · API delay 30min"
    assert embed.timestamp is None
    assert embed.image.url == "attachment://kill.png"


def test_embed_hides_normal_api_delay():
    embed = _build_embed({
        "id": 1, "region": "americas", "albion_event_id": "99",
        "api_delay_secs": 900, "killer": {"name": "K"}, "victim": {"name": "V"},
    }, "kill.png")
    assert embed.footer.text == "https://ziggs.example · ziggs:juicy-kill:1"


def test_embed_caps_description_and_exposes_stable_marker():
    embed = _build_embed({
        "id": 42, "region": "americas", "albion_event_id": "99",
        "killer": {"name": "K"}, "victim": {"name": "V"},
        "participants": [{"name": f"Player {i} " + "x" * 80} for i in range(100)],
    }, "kill.png")
    assert len(embed.description) <= 4096
    message = type("Message", (), {"embeds": [embed]})()
    assert _message_kill_id(message) == 42
    embed.set_footer(text="42")
    assert _message_kill_id(message) is None


def test_reconhece_kill_ja_enviada_apos_falha_no_ack():
    async def run():
        sent: list[int] = []
        acknowledgements: list[dict] = []
        fail_ack = True

        class Channel:
            async def send(self, *, embed, file):
                sent.append(_message_kill_id(SimpleNamespace(embeds=[embed])))

            async def history(self, *_args, **_kwargs):
                return
                yield  # pragma: no cover

        class Guild:
            id = 1
            filesize_limit = 8 * 1024 * 1024

            def get_channel(self, _channel_id):
                return Channel()

        queue = {"kills": [{
            "id": 7, "region": "asia", "albion_event_id": "123",
            "timestamp": "2026-01-01T01:00:00+00:00",
            "killer": {"name": "K"}, "victim": {"name": "V"}, "participants": [],
        }]}

        async def config(_guild_id):
            return {"juicy_kill_channel_id": "55"}

        async def get(_path, **_kwargs):
            return queue

        async def get_bytes(_path, **_kwargs):
            return b"png"

        async def post(_path, body, **_kwargs):
            nonlocal fail_ack
            acknowledgements.append(body)
            if fail_ack:
                return None
            return {"ok": True}

        original = (
            juicy_kills._guild_command_config, juicy_kills.http_client.get_json,
            juicy_kills.http_client.get_bytes, juicy_kills.http_client.post_json,
        )
        juicy_kills._guild_command_config = config
        juicy_kills.http_client.get_json = get
        juicy_kills.http_client.get_bytes = get_bytes
        juicy_kills.http_client.post_json = post
        try:
            cog = juicy_kills.JuicyKills(SimpleNamespace(user=SimpleNamespace(id=99)))
            await cog._sync_guild_unlocked(Guild())
            fail_ack = False
            await cog._sync_guild_unlocked(Guild())
            queue["kills"] = [{
                "id": 8, "region": "americas", "albion_event_id": "124",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "killer": {"name": "K"}, "victim": {"name": "V"}, "participants": [],
            }]
            await cog._sync_guild_unlocked(Guild())
        finally:
            juicy_kills._guild_command_config, juicy_kills.http_client.get_json, \
                juicy_kills.http_client.get_bytes, juicy_kills.http_client.post_json = original

        assert sent == [7, 8]
        assert acknowledgements == [
            {"kill_ids": [7]},
            {"kill_ids": [7]},
            {"kill_ids": [8]},
        ]

    asyncio.run(run())


if __name__ == "__main__":
    test_embed_links_every_player_and_footer_is_site_only()
    test_embed_hides_normal_api_delay()
    test_embed_caps_description_and_exposes_stable_marker()
    test_reconhece_kill_ja_enviada_apos_falha_no_ack()
    print("juicy kills: ok")
