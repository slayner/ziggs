"""Checks puros das invariantes introduzidas na Fase 0 de eventos/regear."""
from pydantic import ValidationError

from app.api.schemas.events import EventCreate
from app.api.schemas.regear import RegearRequestUpdate
from app.domain.states import EventState, allowed_targets
from app.services.event_signups import (
    ack_function_prompt_deletes,
    queue_function_prompt_deletes,
    record_function_prompt_messages,
    signup_block_reason,
    validate_role_minimum,
)
from app.services.events import ServiceError
from app.services.regear import regear_status_transition_allowed


def test_signup_requires_active_event_but_not_comp():
    assert EventState.SCHEDULED in allowed_targets(EventState.DRAFT)
    assert EventState.IN_PROGRESS not in allowed_targets(EventState.DRAFT)
    assert signup_block_reason(EventState.DRAFT, 10)
    assert signup_block_reason(EventState.SCHEDULED, 10) is None
    assert signup_block_reason(EventState.IN_PROGRESS, 10) is None
    assert signup_block_reason(EventState.SCHEDULED, None) is None
    assert signup_block_reason(EventState.REVIEW, 10)
    assert signup_block_reason(EventState.FINALIZED, 10)
    assert signup_block_reason(EventState.SCHEDULED, 10, "announcement")


def test_event_creation_publish_signal_is_optional():
    payload = EventCreate(scheduled_at="2026-07-23T21:00:00Z")
    assert payload.publish is None
    assert payload.signup_mode == "signup"


def test_signup_has_minimum_but_no_maximum():
    roles = [f"role-{i}" for i in range(30)]
    validate_role_minimum(roles, 2)
    try:
        validate_role_minimum(["role"], 2)
    except ServiceError:
        pass
    else:
        raise AssertionError("minimum role count was not enforced")


def test_function_prompt_is_queued_for_delete_on_review():
    class FakeDb:
        def __init__(self):
            self.guild = type("GuildRow", (), {"settings": {}})()
            self.event = type(
                "EventRow", (), {"guild_id": 1, "state": EventState.SCHEDULED},
            )()

        def get(self, model, row_id):
            return self.guild if model.__name__ == "Guild" else self.event

        def flush(self):
            pass

    db = FakeDb()
    message = {"event_id": 9, "user_id": 7, "message_id": "123"}
    record_function_prompt_messages(db, 1, [message])
    assert db.guild.settings["function_prompt_messages"] == [message]
    queue_function_prompt_deletes(db, 1, 9)
    assert db.guild.settings["function_prompt_messages"] == []
    assert db.guild.settings["pending_function_prompt_deletes"] == [message]
    ack_function_prompt_deletes(db, 1, {"123"})
    assert db.guild.settings["pending_function_prompt_deletes"] == []


def test_regear_status_transitions_are_one_way():
    assert regear_status_transition_allowed("pending", "paid")
    assert regear_status_transition_allowed("pending", "denied")
    assert regear_status_transition_allowed("pending", "removed")
    assert regear_status_transition_allowed("paid", "paid")
    assert not regear_status_transition_allowed("paid", "pending")
    assert not regear_status_transition_allowed("paid", "denied")
    assert not regear_status_transition_allowed("denied", "paid")


def test_regear_update_rejects_negative_final_total():
    try:
        RegearRequestUpdate(final_total=-1)
    except ValidationError:
        return
    raise AssertionError("negative final_total was accepted")


if __name__ == "__main__":
    test_signup_requires_active_event_but_not_comp()
    test_event_creation_publish_signal_is_optional()
    test_signup_has_minimum_but_no_maximum()
    test_function_prompt_is_queued_for_delete_on_review()
    test_regear_status_transitions_are_one_way()
    test_regear_update_rejects_negative_final_total()
    print("ok")
