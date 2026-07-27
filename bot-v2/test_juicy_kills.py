import os

os.environ.setdefault("BOT_PUBLIC_URL", "https://ziggs.example")

from cogs.juicy_kills import _build_embed, _message_kill_id


def test_embed_links_every_player_and_has_site_only_footer():
    embed = _build_embed({
        "id": 1, "region": "europe", "albion_event_id": "99",
        "api_delay_secs": 901,
        "silver_dropped": 50_000_000, "fame": 1_000_000,
        "killer": {"name": "Killer"}, "victim": {"name": "Victim"},
        "participants": [{"name": "Killer"}, {"name": "Assist One"}],
    }, "kill.png")
    assert embed.title == "Killer killed Victim"
    assert "/eu/Killer?activity=99" in embed.description
    assert "/eu/Victim?activity=99" in embed.description
    assert "/eu/Assist%20One" in embed.description
    assert "/eu/Assist%20One?activity=" not in embed.description
    assert embed.footer.text == "https://ziggs.example · Albion API delay (Europe): ~15min · ziggs:juicy-kill:1"
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


if __name__ == "__main__":
    test_embed_links_every_player_and_has_site_only_footer()
    test_embed_hides_normal_api_delay()
    test_embed_caps_description_and_exposes_stable_marker()
    print("juicy kills: ok")
