"""Schemas do sistema de regears por screenshot."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── config da guilda ───────────────────────────────────────────────────────────

class RegearChannelOut(BaseModel):
    channel_id: str


class RegearSettingsOut(BaseModel):
    enabled: bool = False
    event_thread_parent_channel_id: str | None = None
    payment_channel_id: str | None = None
    extra_channels: list[RegearChannelOut] = Field(default_factory=list)
    payment_pct: int = Field(default=100, ge=0, le=100)
    enabled_categories: list[str] = Field(default_factory=list)
    disabled_items: list[str] = Field(default_factory=list)
    requester_role_ids: list[str] = Field(default_factory=list)
    attendance_multiplier_enabled: bool = False
    require_approval: bool = True
    approver_role_ids: list[int] = Field(default_factory=list)


class RegearSettingsIn(BaseModel):
    enabled: bool | None = None
    event_thread_parent_channel_id: str | None = None
    payment_channel_id: str | None = None
    extra_channels: list[RegearChannelOut] | None = None
    payment_pct: int | None = Field(default=None, ge=0, le=100)
    enabled_categories: list[str] | None = None
    disabled_items: list[str] | None = None
    requester_role_ids: list[str] | None = None
    attendance_multiplier_enabled: bool | None = None
    require_approval: bool | None = None
    approver_role_ids: list[int] | None = None


# ── request de regear ──────────────────────────────────────────────────────────

class RegearItemOut(BaseModel):
    item_id: str
    name: str
    quality: int
    slot: str
    category: str | None
    eligible: bool
    unit_price: int
    total_price: int


class RegearRequestOut(BaseModel):
    id: int
    guild_id: int
    event_id: int | None = None
    event_title: str | None = None
    requester_user_id: int | None
    requester_name: str | None
    source_message_id: str | None = None
    source_attachment_id: str | None = None
    source_attachment_index: int | None = None
    payment_message_id: str | None = None
    payment_message_channel_id: str | None = None
    economy_transaction_id: int | None = None
    requester_role_ids_snapshot: list[str] = Field(default_factory=list)
    event_participation_snapshot: dict[str, Any] = Field(default_factory=dict)
    screenshot_url: str
    ocr_name: str | None
    albion_event_id: str | None
    death_timestamp: datetime | None
    detected_items: list[RegearItemOut]
    base_total: int
    suggested_total: int
    final_total: int | None
    coverage_pct: int
    price_basis: str
    status: str
    handled_by_user_id: int | None
    handled_at: datetime | None
    notes: str | None
    created_at: datetime
    recognition_status: str  # recognized | manual | error
    recognition_method: str = "manual"
    recognition_confidence: str = "low"
    recognition_candidates: list[Any] = Field(default_factory=list)
    recognition_window_match: bool | None = None
    recognition_fallback_reason: str | None = None


class RegearRequestUpdate(BaseModel):
    final_total: int | None = Field(default=None, ge=0)
    status: str | None = None  # paid | denied | pending
    notes: str | None = None
    # Edição de itens detectados (manual): sobrescreve a lista reconhecida.
    detected_items: list[dict[str, Any]] | None = Field(default=None, max_length=100)
    event_participation_pct: int | None = Field(default=None, ge=0, le=100)
    event_role_name: str | None = Field(default=None, max_length=255)


class RegearPaymentMessageIn(BaseModel):
    payment_message_id: str | None = None
    payment_message_channel_id: str | None = None


class RegearBotRequestUpdate(RegearRequestUpdate):
    actor_user_id: int
    actor_role_ids: list[int] = Field(default_factory=list)
    actor_is_admin: bool = False


class RegearListOut(BaseModel):
    requests: list[RegearRequestOut]
