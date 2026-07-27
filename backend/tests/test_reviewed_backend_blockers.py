from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api.routes import auth, events
from app.config import get_settings
from app.db import engine as app_engine
from app.services import event_escalation, event_signups, regear


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

    with patch.object(event_signups, "_get_event", lambda *_args: ev), patch.object(
        event_signups, "get_eligible_functions",
        lambda *_args: (["Tank"], None, signup, {}, None),
    ), patch.object(event_signups, "_save_role_profile", lambda *_args: None), patch.object(
        event_escalation, "autofill_signup", autofill,
    ):
        event_signups.upsert_signup(db, 1, 2, 3, "User", ["Tank"], set(), {})


def test_autofill_route_returns_service_response():
    expected = {"assigned": 2, "run_id": "run"}
    db = SimpleNamespace(commit=lambda: None)
    member = SimpleNamespace(user_id=7)
    with patch.object(event_escalation, "autofill_event", lambda *_args, **_kwargs: expected):
        assert events.autofill_escalacao(2, 1, db, member) is expected


def test_audit_and_battle_acks_are_monotonic():
    guild = SimpleNamespace(settings={"logs_last_sent_id": 10, "battle_feed_last_id": 20})

    class Db:
        def scalar(self, _query):
            return guild

        def commit(self):
            pass

    authorization = f"Bearer {get_settings().bot_api_secret}"
    auth.bot_audit_log_synced(1, auth.AuditLogSyncedIn(last_id=5), authorization, Db())
    auth.bot_battle_feed_synced(1, auth.BattleFeedSyncedIn(last_id=15), authorization, Db())
    assert guild.settings["logs_last_sent_id"] == 10
    assert guild.settings["battle_feed_last_id"] == 20


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
