import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api.routes import auth, events
from app.config import get_settings
from app.db import engine as app_engine
from app.services import event_escalation, event_signups, events as events_svc, regear


def test_sqlite_connections_enforce_foreign_keys():
    with app_engine.connect() as conn:
        if conn.dialect.name == "sqlite":
            assert conn.scalar(text("PRAGMA foreign_keys")) == 1


def test_events_view_cannot_remove_regear():
    request = SimpleNamespace(guild_id=1, status="pending")
    member = SimpleNamespace()

    class Db:
        def get(self, _model, _id):
            return request

        def scalar(self, _query):
            return member

    with patch.object(regear, "has_permission", lambda *_args: False):
        try:
            regear.update_request(Db(), 1, 2, {"status": "removed"}, 3)
        except regear.RegearServiceError as exc:
            assert "permissão" in str(exc)
        else:
            raise AssertionError("events.view user removed a regear request")


def test_on_signup_expires_relationship_before_autofill():
    signup = None
    ev = SimpleNamespace(
        autofill_mode="on_signup", functions_released=False, comp_id=None,
        signup_message_dirty=False,
    )

    class Db:
        expired = False

        def add(self, _row):
            pass

        def flush(self):
            pass

        def expire(self, row, attrs):
            assert row is ev and attrs == ["signups"]
            self.expired = True

    db = Db()
    def autofill(*_args):
        assert db.expired

    option = {"key": "w1:dps", "weapon_id": 1, "fn": "dps", "weapon_name": "W", "role_names": ["Tank"]}
    with patch.object(event_signups, "_get_event", lambda *_args: ev), patch.object(
        event_signups, "get_eligible_options",
        lambda *_args: ([option], None, signup, None),
    ), patch.object(event_signups, "_save_weapon_fn_profile", lambda *_args: None), patch.object(
        event_escalation, "autofill_signup", autofill,
    ):
        event_signups.upsert_signup(db, 1, 2, 3, "User", ["w1:dps"], set(), {})


def test_autofill_route_returns_service_response():
    expected = {"assigned": 2, "run_id": "run"}
    db = SimpleNamespace(commit=lambda: None)
    member = SimpleNamespace(user_id=7)
    with patch.object(event_escalation, "autofill_event", lambda *_args, **_kwargs: expected):
        assert events.autofill_escalacao(2, 1, db, member) is expected


def test_audit_and_battle_acks_are_monotonic():
    from datetime import datetime, timezone
    guild = SimpleNamespace(settings={"logs_last_sent_id": 10, "battle_feed_last_ts": "2026-01-01T00:00:00+00:00"})

    class Db:
        async def scalar(self, _query):
            return guild

        async def commit(self):
            pass

    authorization = f"Bearer {get_settings().bot_api_secret}"
    asyncio.run(auth.bot_audit_log_synced(1, auth.AuditLogSyncedIn(last_id=5), authorization, Db()))
    # Watermark só avança se o novo timestamp for MAIOR que o atual.
    asyncio.run(auth.bot_battle_feed_synced(
        1, auth.BattleFeedSyncedIn(last_ts=datetime(2025, 1, 1, tzinfo=timezone.utc)),
        authorization, Db(),
    ))
    assert guild.settings["logs_last_sent_id"] == 10
    # last_ts menor que o watermark atual → mantém o watermark (monotônico).
    assert guild.settings["battle_feed_last_ts"] == "2026-01-01T00:00:00+00:00"

    # last_ts maior que o watermark atual → avança.
    asyncio.run(auth.bot_battle_feed_synced(
        1, auth.BattleFeedSyncedIn(last_ts=datetime(2026, 6, 1, tzinfo=timezone.utc)),
        authorization, Db(),
    ))
    assert guild.settings["battle_feed_last_ts"] == "2026-06-01T00:00:00+00:00"


def test_auto_logs_channel_cannot_overwrite_manual_channel():
    guild = SimpleNamespace(settings={"logs_channel_id": "manual", "logs_last_sent_id": 10})

    class Db:
        committed = False

        async def scalar(self, _query):
            return guild

        async def commit(self):
            self.committed = True

    db = Db()
    authorization = f"Bearer {get_settings().bot_api_secret}"
    result = asyncio.run(auth.bot_set_logs_channel(
        1, auth.LogsChannelIn(channel_id="auto"), authorization, db,
    ))
    assert result == {"ok": True}
    assert guild.settings["logs_channel_id"] == "manual"
    assert not db.committed


def test_audit_log_dict_keeps_console_payload_raw():
    row = SimpleNamespace(
        id=7, actor_id=9, actor_type="bot", source="bot", action="economy.add",
        entity="balance", entity_id="3", before={"balance": 0}, after={"balance": 100},
        note="transaction #7", created_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
    )
    assert auth._audit_log_dict(row) == {
        "id": 7, "actor_id": "9", "actor_type": "bot", "source": "bot",
        "action": "economy.add", "entity": "balance", "entity_id": "3",
        "before": {"balance": 0}, "after": {"balance": 100},
        "note": "transaction #7", "created_at": "2026-08-17T00:00:00+00:00",
    }


def test_audit_console_entries_attach_actor_display_names():
    def row(row_id, actor_id):
        return SimpleNamespace(
            id=row_id, actor_id=actor_id, actor_type="site", source="site",
            action="event.transition", entity="event", entity_id="3",
            before=None, after={"state": "review"}, note=None,
            created_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        )

    entries = auth._attach_actor_names(
        [row(7, 9), row(6, 12), row(5, None)],
        [(9, "Gil do Discord", "gil"), (12, None, "botuser")],
    )
    assert entries[0]["actor_name"] == "Gil do Discord"  # global_name vence
    assert entries[0]["actor_id"] == "9"                 # id fica pra rastreio
    assert entries[1]["actor_name"] == "botuser"         # sem global_name → username
    assert entries[2]["actor_name"] is None              # sistema não tem ator
    # payload cru continua intacto pro retransmissor do bot
    assert entries[0]["action"] == "event.transition"


def test_archiving_event_thread_clears_pending_embed_work():
    event = SimpleNamespace(event_thread_archived=False, event_embed_dirty=True)

    class Db:
        flushed = False

        def scalar(self, _query):
            return event

        def flush(self):
            self.flushed = True

    db = Db()
    assert events_svc.mark_event_thread_archived(db, 1, 2)
    assert event.event_thread_archived
    assert not event.event_embed_dirty
    assert db.flushed


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
