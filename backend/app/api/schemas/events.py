"""Schemas Pydantic da API de eventos."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class EventCreate(BaseModel):
    title: str | None = None
    scheduled_at: datetime | None = None
    comp_id: int | None = None


class EventSummary(BaseModel):
    id: int
    state: str
    type: str | None = None
    title: str | None = None
    caller_name: str | None = None
    scheduled_at: datetime | None = None
    created_at: datetime


class VerificationStepOut(BaseModel):
    step: str
    completed: bool
    data: dict = Field(default_factory=dict)


class ParticipantOut(BaseModel):
    id: int
    user_id: int
    user_name: str | None
    percent: int
    base_percent: int
    is_trial: bool
    silver_received: int
    game_role_id: int | None = None
    game_role_name: str | None = None


class DeathOut(BaseModel):
    id: int
    user_id: int | None
    display_name: str
    silver_value: int
    notes: str | None
    approved: bool


class PayoutRow(BaseModel):
    user_id: int | None
    display_name: str
    percent: int
    lootsplit: int
    regear: int
    total: int


class PayoutPreview(BaseModel):
    tab_value: int
    payouts: list[PayoutRow]
    total_lootsplit: int
    total_regear: int


class EventDetail(BaseModel):
    id: int
    state: str
    type: str | None = None
    title: str | None = None
    message: str | None = None
    comp_id: int | None = None
    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    callout_at: datetime | None = None
    ended_at: datetime | None = None
    is_loss: bool = False
    tab_value: int = 0
    tab_image_url: str | None = None
    allowed_transitions: list[str] = Field(default_factory=list)
    verification: list[VerificationStepOut] = Field(default_factory=list)
    participants: list[ParticipantOut] = Field(default_factory=list)
    deaths: list[DeathOut] = Field(default_factory=list)
    payout: PayoutPreview | None = None


class TransitionRequest(BaseModel):
    to: str
    reason: str | None = None


class SetTypeRequest(BaseModel):
    type: str  # lootsplit | regear | lootsplit_regear


class StepRequest(BaseModel):
    completed: bool = True
    # Dados específicos do passo (ex.: {"value": 500000000} para tab_value)
    data: dict | None = None


class ParticipantIn(BaseModel):
    user_id: int
    user_name: str | None = None
    percent: int = 0
    base_percent: int = 0
    is_trial: bool = False


class DeathIn(BaseModel):
    display_name: str
    user_id: int | None = None
    silver_value: int = 0
    notes: str | None = None


class DeathUpdate(BaseModel):
    approved: bool | None = None
    silver_value: int | None = None
    notes: str | None = None


class ParticipantUpdate(BaseModel):
    game_role_id: int | None = None
    percent: int | None = None
    is_trial: bool | None = None


class RegearItemOut(BaseModel):
    slot: str
    item_id: str
    name: str
    quality: int
    quantity: int
    unit_price: int
    total_price: int


class RegearEstimateOut(BaseModel):
    participant_id: int
    user_name: str | None
    game_role_id: int | None
    game_role_name: str | None
    items: list[RegearItemOut]
    total: int
    price_basis: str
    calculated_at: str
