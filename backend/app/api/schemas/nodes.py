"""Schemas do subsistema de nodes."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class NodeDefOut(BaseModel):
    id: int
    name: str
    emoji: str | None = None
    weight: float = 1.0
    sort: int = 0


class NodeDefIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    emoji: str | None = None
    weight: float = 1.0
    sort: int = 0


class NodeDefUpdate(BaseModel):
    """Edição de um def já criado (por id). `name` vazio = não mexer; sort saiu
    da UI (ordem é alfabética) mas o campo segue no banco p/ não migrar."""
    name: str | None = None
    emoji: str | None = None
    weight: float | None = None


class NodeEventOut(BaseModel):
    id: int
    guild_id: int
    channel_id: int | None = None
    node_type: str
    map_name: str
    spawn_at: datetime
    added_by_id: int | None = None
    added_by_name: str | None = None


class NodeEventIn(BaseModel):
    node_type: str = Field(min_length=1, max_length=128)
    map_name: str = Field(min_length=1, max_length=128)
    spawn_at: datetime
    channel_id: int | None = None
    allow_duplicate: bool = False


class NodeEventLogOut(BaseModel):
    id: int
    node_type: str
    map_name: str
    spawn_at: datetime
    scout_id: int | None = None
    scout_name: str | None = None
    logged_at: datetime
    # Captura em review: liga o node ao evento + valor vendido (scout payout).
    captured: bool = False
    sold_value: int = 0
    event_id: int | None = None


class MapsOut(BaseModel):
    extras: list[str]
    exclusions: list[str]
    builtin: list[str]


class MapIn(BaseModel):
    map_name: str = Field(min_length=1, max_length=128)


class CalendarOut(BaseModel):
    guild_id: int
    channel_id: int | None = None
    message_id: int | None = None


class CalendarIn(BaseModel):
    channel_id: int | None = None
    message_id: int | None = None


class NearNodesOut(BaseModel):
    ts: datetime
    window_seconds: int
    nodes: list[NodeEventLogOut]