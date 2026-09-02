"""Regression check: /event edit keeps reusing its ephemeral panel."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import cogs.event_cmd as event_cmd


class _Response:
    def __init__(self):
        self.done = False

    def is_done(self):
        return self.done

    async def defer(self):
        self.done = True


class _Interaction:
    def __init__(self):
        self.guild_id = 1
        self.user = SimpleNamespace(id=2)
        self.response = _Response()
        self.edits = []

    async def edit_original_response(self, **kwargs):
        self.edits.append(kwargs)


async def main():
    original_patch = event_cmd._patch

    async def fake_patch(path, body):
        return {"event_id": 7, "notified_signups": []}

    event_cmd._patch = fake_patch
    try:
        comps = [{"id": 3, "name": "Brawl"}]
        ev = {
            "id": 7, "title": "Antigo", "scheduled_at": "2026-07-25T01:00:00+00:00",
            "comp_id": None, "comp_name": None, "attendance": 1,
        }
        changes = {}

        async def noop(*_args):
            pass

        assert len(event_cmd.EventSelectView("pt", [ev], noop, show_cancel=False).children) == 1
        assert len(event_cmd.CompSelectView("pt", comps, noop, show_cancel=False).children) == 1
        assert len(event_cmd.EventSelectView("pt", [ev], noop).children) == 2

        interaction = _Interaction()
        modal = event_cmd.TitleModal("pt", ev, comps, changes)
        modal.title_input._value = "Novo objetivo"
        await modal.on_submit(interaction)
        assert ev["title"] == "Novo objetivo"
        assert "Antigo → Novo objetivo" in interaction.edits[-1]["content"]
        assert isinstance(interaction.edits[-1]["view"], event_cmd.EditFieldView)
        assert len(interaction.edits[-1]["view"].children) == 1

        interaction = _Interaction()
        await event_cmd._do_patch_comp(interaction, "pt", ev, 3, comps, changes)
        assert ev["comp_name"] == "Brawl"
        content = interaction.edits[-1]["content"]
        assert "Antigo → Novo objetivo" in content
        assert "Sem comp" in content and "Brawl" in content
        assert isinstance(interaction.edits[-1]["view"], event_cmd.EditFieldView)

        interaction = _Interaction()
        dt = datetime(2026, 7, 25, 2, 30, tzinfo=timezone.utc)
        await event_cmd._do_patch_scheduled(interaction, "pt", ev, dt, comps, changes)
        assert ev["scheduled_at"] == dt.isoformat()
        assert "25/07/2026 01:00 UTC → 25/07/2026 02:30 UTC" in interaction.edits[-1]["content"]

        interaction = _Interaction()
        dt = datetime(2026, 7, 25, 3, 0, tzinfo=timezone.utc)
        await event_cmd._do_patch_scheduled(interaction, "pt", ev, dt, comps, changes)
        content = interaction.edits[-1]["content"]
        assert "25/07/2026 02:30 UTC → 25/07/2026 03:00 UTC" in content
        assert "25/07/2026 01:00 UTC" not in content

        interaction = _Interaction()
        modal = event_cmd.AttendanceModal("pt", ev, comps, changes)
        modal.val._value = "1,5"
        await modal.on_submit(interaction)
        assert ev["attendance"] == 1.5
        content = interaction.edits[-1]["content"]
        assert "1 → 1.5" in content
        assert "Novo objetivo" in content and "Brawl" in content
    finally:
        event_cmd._patch = original_patch


if __name__ == "__main__":
    asyncio.run(main())
    print("event edit flow: ok")
