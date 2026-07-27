"""Regression check: every ephemeral owns an independent inactivity timer."""
import asyncio
from types import SimpleNamespace

import ephemeral_guard
import error_handler


class _Webhook:
    def __init__(self):
        self.deleted = []
        self.sent = []

    async def delete_message(self, message_id):
        self.deleted.append(message_id)

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))


class _Response:
    def __init__(self):
        self.edits = []

    def is_done(self):
        return False

    async def edit_message(self, **kwargs):
        self.edits.append(kwargs)


async def main():
    old_ttl = ephemeral_guard._TTL
    ephemeral_guard._TTL = 0.02
    ephemeral_guard._active.clear()
    ephemeral_guard._origins.clear()
    ephemeral_guard._token_origins.clear()
    try:
        first_webhook = _Webhook()
        second_webhook = _Webhook()
        first = SimpleNamespace(
            followup=first_webhook, message=SimpleNamespace(id=101),
        )
        second = SimpleNamespace(
            followup=second_webhook, message=SimpleNamespace(id=202),
        )

        ephemeral_guard.track(first, 101)
        ephemeral_guard.track(second, 202)
        assert set(ephemeral_guard._active) == {101, 202}

        await asyncio.sleep(0.01)
        ephemeral_guard.touch(first)
        await asyncio.sleep(0.015)
        assert second_webhook.deleted == [202]
        assert first_webhook.deleted == []

        await asyncio.sleep(0.015)
        assert first_webhook.deleted == [101]
        assert not ephemeral_guard._active

        origin = (7, "message", 99)
        old_webhook = _Webhook()
        ephemeral_guard._schedule_delete(250, old_webhook, origin)
        child = SimpleNamespace(
            user=SimpleNamespace(id=7),
            message=SimpleNamespace(id=250),
            data={"custom_id": "next"},
        )
        assert ephemeral_guard._origin_key(child) == origin
        assert await ephemeral_guard._clear_origin(origin)
        assert old_webhook.deleted == [250]
        assert 250 not in ephemeral_guard._active
        assert origin not in ephemeral_guard._origins

        response = _Response()
        webhook = _Webhook()
        interaction = SimpleNamespace(
            response=response,
            followup=webhook,
            message=SimpleNamespace(
                id=303, flags=SimpleNamespace(ephemeral=True),
            ),
        )
        await error_handler._reply_ephemeral(interaction, "backend offline")
        assert response.edits[-1]["content"] == "backend offline"
        assert not webhook.sent
        assert 303 in ephemeral_guard._active
        ephemeral_guard.cleanup(interaction)
    finally:
        for task, _, _ in ephemeral_guard._active.values():
            task.cancel()
        ephemeral_guard._active.clear()
        ephemeral_guard._origins.clear()
        ephemeral_guard._token_origins.clear()
        ephemeral_guard._blocked_tokens.clear()
        ephemeral_guard._TTL = old_ttl


if __name__ == "__main__":
    asyncio.run(main())
    print("ephemeral guard: ok")
