import asyncio
from types import SimpleNamespace

import http_client
from cogs.event_cmd import EventCmd
from cogs.event_embeds import _build_event_embed, _is_event_message
from cogs.events import MassinfoView


def _events(count):
    return [{
        "event_id": i, "scheduled_at": "2026-07-27T20:00:00+00:00",
        "state": "scheduled", "signup_mode": "open", "title": f"Event {i}",
    } for i in range(1, count + 1)]


async def main():
    view = MassinfoView(_events(25))
    assert len(view.children) == 21
    assert sum(item.__class__.__name__.endswith("Button") for item in view.children) == 20
    assert len(view.children[-1].options) == 5

    embed = _build_event_embed("pt", 1, 7, {"event": {"participants": []}})
    marked = SimpleNamespace(embeds=[embed])
    arbitrary = SimpleNamespace(embeds=[SimpleNamespace(footer=SimpleNamespace(text=None))])
    assert _is_event_message(marked, 7)
    assert not _is_event_message(arbitrary, 7)

    import cogs.event_cmd as event_cmd
    original_get = event_cmd._get
    async def unavailable(_path):
        raise http_client.BackendUnavailable()
    event_cmd._get = unavailable
    try:
        interaction = SimpleNamespace(guild_id=1)
        assert await EventCmd._comp_autocomplete(None, interaction, "") == []
    finally:
        event_cmd._get = original_get


if __name__ == "__main__":
    asyncio.run(main())
    print("review blockers: ok")
