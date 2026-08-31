"""Checks puros das invariantes introduzidas na Fase 0 de eventos/regear."""
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from app.api.schemas.events import EventCreate, ParticipantOut, PayoutPreview, PayoutRow
from app.api.schemas.regear import RegearRequestUpdate
from app.domain.states import EventState, allowed_targets
from app.services.event_signups import (
    ack_function_prompt_deletes,
    queue_function_prompt_deletes,
    record_function_prompt_messages,
    signup_block_reason,
    validate_role_minimum,
)
from app.services import events as events_svc
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


def test_event_response_preserves_discord_snowflake_as_text():
    participant = ParticipantOut(
        id=1, user_id=1511224389314809936, user_name="slayner",
        percent=100, base_percent=100, is_trial=False, silver_received=0,
    )
    assert participant.model_dump()["user_id"] == "1511224389314809936"


def test_finalize_credits_logger_and_scout_outside_participants():
    participant_id = 10
    scout_id = 20
    logger_id = 30
    participant = SimpleNamespace(user_id=participant_id, user_name="participante", percent=100, silver_received=0)
    event = SimpleNamespace(id=99, guild_id=1, tab_value=1_000, participants=[participant])
    payout = PayoutPreview(
        tab_value=1_000,
        payouts=[
            PayoutRow(user_id=participant_id, display_name="participante", percent=100, lootsplit=100, regear=0, total=100),
            PayoutRow(user_id=scout_id, display_name="scout", percent=0, lootsplit=0, regear=0, scout=50, total=50),
        ],
        logger_payouts=[
            PayoutRow(user_id=logger_id, display_name="logger", percent=100, lootsplit=25, regear=0, total=25),
        ],
        total_lootsplit=100,
        total_regear=0,
        total_scout=50,
    )
    balances = {}
    db = SimpleNamespace(add=lambda row: transactions.append(row))
    transactions = []

    def balance_for(_db, _guild_id, user_id):
        return balances.setdefault(user_id, SimpleNamespace(balance=0, total_earned=0))

    with patch.object(events_svc, "_calc_payout", return_value=payout), \
         patch.object(events_svc, "_participant_valid", return_value=True), \
         patch.object(events_svc.economy_svc, "get_or_create_balance", side_effect=balance_for):
        events_svc._finalize_payouts(db, event)

    assert participant.silver_received == 100
    assert {uid: balance.balance for uid, balance in balances.items()} == {
        participant_id: 100, scout_id: 50, logger_id: 25,
    }
    assert {(tx.to_user_id, tx.amount, tx.event_id) for tx in transactions} == {
        (participant_id, 100, event.id), (scout_id, 50, event.id), (logger_id, 25, event.id),
    }


if __name__ == "__main__":
    test_signup_requires_active_event_but_not_comp()
    test_event_creation_publish_signal_is_optional()
    test_signup_has_minimum_but_no_maximum()
    test_function_prompt_is_queued_for_delete_on_review()
    test_regear_status_transitions_are_one_way()
    test_regear_update_rejects_negative_final_total()
    test_event_response_preserves_discord_snowflake_as_text()
    test_finalize_credits_logger_and_scout_outside_participants()
    print("ok")
