"""Schemas Pydantic da API de comps."""
from __future__ import annotations

from pydantic import BaseModel, Field


# ----- entrada (criar/editar) -----------------------------------------------
class SlotIn(BaseModel):
    id: int | None = None
    label: str | None = None
    notes: str | None = None
    fn: str | None = None   # tipo de função da bracket (ex.: "tank", "healer")
    # Funções aceitáveis no slot (slot FLEXÍVEL: várias roles). IDs de game_roles.
    role_ids: list[int] = Field(default_factory=list)


class PartyIn(BaseModel):
    name: str | None = None
    slots: list[SlotIn] = Field(default_factory=list)


class CompCreate(BaseModel):
    name: str
    description: str | None = None
    parties: list[PartyIn] = Field(default_factory=list)


class CompUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    archived: bool | None = None
    # Se enviado, SUBSTITUI toda a estrutura de parties/slots.
    parties: list[PartyIn] | None = None


# ----- saída (leitura) ------------------------------------------------------
class RoleRead(BaseModel):
    id: int
    name: str
    weapon_id: int | None = None
    invisible_function: str | None = None
    offhand: str | None = None
    helmet: str | None = None
    armor: str | None = None
    boots: str | None = None
    cape: str | None = None
    food: str | None = None
    abilities: str | None = None
    play_style: str | None = None
    obs: str | None = None
    q_spell: str | None = None
    w_spell: str | None = None
    passive_spell: str | None = None
    gear_spells: dict | None = None
    build_items: list = Field(default_factory=list)


class SlotRead(BaseModel):
    id: int
    position: int
    label: str | None = None
    notes: str | None = None
    fn: str | None = None
    roles: list[RoleRead] = Field(default_factory=list)


class PartyRead(BaseModel):
    id: int
    position: int
    name: str | None = None
    slots: list[SlotRead] = Field(default_factory=list)


class CompRead(BaseModel):
    id: int
    name: str
    description: str | None = None
    archived: bool
    parties: list[PartyRead] = Field(default_factory=list)


class CompSummary(BaseModel):
    id: int
    name: str
    description: str | None = None
    archived: bool
    party_count: int


# ----- sugestão -------------------------------------------------------------
class SuggestRequest(BaseModel):
    target_function: str
    # 'comp' = só funções já nesta comp; 'guild' = toda a biblioteca da guilda.
    scope: str = "comp"


class FieldSuggestionOut(BaseModel):
    value: str
    votes: int
    total: int


class BuildSuggestionOut(BaseModel):
    target_function: str
    sample_size: int
    fields: dict[str, FieldSuggestionOut] = Field(default_factory=dict)


# ----- tipos de função (fn-types) por guilda ---------------------------------
class FnTypeIn(BaseModel):
    key: str
    label: str
    color: str
    emoji: str | None = None


class FnTypesUpdate(BaseModel):
    fn_types: list[FnTypeIn]


class FnTypesOut(BaseModel):
    fn_types: list[FnTypeIn]
