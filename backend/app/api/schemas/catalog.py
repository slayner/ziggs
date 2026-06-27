"""Schemas Pydantic do catálogo (weapons + game roles + sugestões)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class WeaponOut(BaseModel):
    id: int
    item_id: str
    name: str
    invisible_function: str | None = None
    category: str | None = None


class WeaponSpellOut(BaseModel):
    id: int
    spell_id: str
    slot: str       # Q | W | passive
    order_idx: int
    name: str
    description: str | None = None
    uisprite: str | None = None


class GameRoleOut(BaseModel):
    id: int
    name: str
    invisible_function: str | None = None
    color: str | None = None


class RegearItem(BaseModel):
    """Um item do set de regear de uma função."""
    slot: str           # offhand | helmet | armor | boots | cape | food
    item_id: str        # ID canônico do Albion (ex.: "T8_HEAD_CLOTH_MORGANA@4")
    name: str           # Nome de exibição (ex.: "8.4 Capuz do Asceta")
    quality: int = 1    # 1=Normal, 2=Bom, 3=Excepcional, 4=Excelente, 5=Obra-prima
    quantity: int = 1   # Normalmente 1; comida pode ser > 1


class GameRoleDetail(BaseModel):
    id: int
    name: str
    weapon_id: int | None = None
    weapon_name: str | None = None
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
    build_items: list[RegearItem] = Field(default_factory=list)
    color: str | None = None
    q_spell: str | None = None
    w_spell: str | None = None
    passive_spell: str | None = None
    gear_spells: dict | None = None


class GameRoleCreate(BaseModel):
    name: str
    weapon_id: int | None = None
    offhand: str | None = None
    helmet: str | None = None
    armor: str | None = None
    boots: str | None = None
    cape: str | None = None
    food: str | None = None
    abilities: str | None = None
    play_style: str | None = None
    obs: str | None = None
    build_items: list[RegearItem] = Field(default_factory=list)
    color: str | None = None
    q_spell: str | None = None
    w_spell: str | None = None
    passive_spell: str | None = None
    gear_spells: dict | None = None


class GameRoleUpdate(BaseModel):
    name: str | None = None
    weapon_id: int | None = None
    offhand: str | None = None
    helmet: str | None = None
    armor: str | None = None
    boots: str | None = None
    cape: str | None = None
    food: str | None = None
    abilities: str | None = None
    play_style: str | None = None
    obs: str | None = None
    build_items: list[RegearItem] | None = None
    color: str | None = None
    q_spell: str | None = None
    w_spell: str | None = None
    passive_spell: str | None = None
    gear_spells: dict | None = None


class FieldSuggestion(BaseModel):
    value: str
    votes: int
    total: int


class BuildSuggestionOut(BaseModel):
    target_function: str
    sample_size: int
    fields: dict[str, FieldSuggestion] = Field(default_factory=dict)


class SuggestRequest(BaseModel):
    target_function: str
