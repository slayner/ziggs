"""Schemas do sistema de regears por screenshot."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── config da guilda ───────────────────────────────────────────────────────────

class RegearChannelOut(BaseModel):
    channel_id: str
    coverage_pct: int = 100


class RegearSettingsOut(BaseModel):
    enabled: bool = False
    channels: list[RegearChannelOut] = Field(default_factory=list)
    enabled_categories: list[str] = Field(default_factory=list)
    disabled_items: list[str] = Field(default_factory=list)
    require_approval: bool = True
    approver_role_ids: list[int] = Field(default_factory=list)


class RegearSettingsIn(BaseModel):
    enabled: bool | None = None
    channels: list[RegearChannelOut] | None = None
    enabled_categories: list[str] | None = None
    disabled_items: list[str] | None = None
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


class RegearRequestUpdate(BaseModel):
    final_total: int | None = None
    status: str | None = None  # paid | denied | pending
    notes: str | None = None
    # Edição de itens detectados (manual): sobrescreve a lista reconhecida.
    detected_items: list[dict[str, Any]] | None = None


class RegearListOut(BaseModel):
    requests: list[RegearRequestOut]