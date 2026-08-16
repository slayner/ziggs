import re
from types import SimpleNamespace
from unittest.mock import patch

from app.models.events import new_escalation_token
from app.services import event_escalation
from app.services.events import ServiceError


def test_escalation_token_is_url_safe_and_opaque():
    token = new_escalation_token()
    assert re.fullmatch(r"[A-Za-z0-9_-]{32}", token)


def test_public_escalation_builds_read_only_payload_from_token():
    event = SimpleNamespace(id=7, guild_id=123)

    class Db:
        def scalar(self, _statement):
            return event

    db = Db()
    with patch.object(event_escalation, "build_escalation", return_value={"can_manage": False}) as build:
        assert event_escalation.build_public_escalation(db, "a" * 32) == {"can_manage": False}
    build.assert_called_once_with(db, 123, 7, None)


def test_public_escalation_rejects_unknown_token():
    class Db:
        def scalar(self, _statement):
            return None

    try:
        event_escalation.build_public_escalation(Db(), "a" * 32)
    except ServiceError as error:
        assert str(error) == "evento não encontrado"
    else:
        raise AssertionError("unknown public token was accepted")


if __name__ == "__main__":
    test_escalation_token_is_url_safe_and_opaque()
    test_public_escalation_builds_read_only_payload_from_token()
    test_public_escalation_rejects_unknown_token()
    print("event escalation tokens OK")
