"""Run directly: python test_registration_revocation.py"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from cogs import registration


def test_discord_revocation_blocks_an_older_response():
    registration._recent_human_revocations.clear()
    requested_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    registration._note_human_revocation(1, 2)
    assert registration._request_is_superseded(1, 2, requested_at)
    assert not registration._request_is_superseded(1, 2, datetime.now(timezone.utc) + timedelta(seconds=1))


def test_unrelated_role_removal_does_not_revoke_registration():
    class Guild:
        id = 1

        def get_member(self, _user_id):
            return None

    registration._recent_human_revocations.clear()
    guild = Guild()
    registered_role = SimpleNamespace(id=10)
    unrelated_role = SimpleNamespace(id=20)
    before = SimpleNamespace(id=2, guild=guild, roles=[registered_role, unrelated_role])
    after = SimpleNamespace(id=2, guild=guild, roles=[registered_role])
    original_config = registration._guild_command_config
    original_post = registration._post_role_removed

    async def no_massinfo(_guild_id):
        return {"events_channel_id": None}

    async def unchanged(*_args):
        return {"ok": True, "role_ids": []}

    registration._guild_command_config = no_massinfo
    registration._post_role_removed = unchanged
    try:
        asyncio.run(registration.Registration(SimpleNamespace()).on_member_update(before, after))
    finally:
        registration._guild_command_config = original_config
        registration._post_role_removed = original_post
    assert not registration._recent_human_revocations


if __name__ == "__main__":
    test_discord_revocation_blocks_an_older_response()
    test_unrelated_role_removal_does_not_revoke_registration()
    print("bot registration revocation OK")
