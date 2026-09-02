"""Schemas Pydantic para loot de eventos e inventário do baú."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ── Upload (bot → API) ────────────────────────────────────────────────────────

class LootEntryIn(BaseModel):
    looted_by_name: str
    looted_by_user_id: int | None = None
    item_type: str
    item_name: str
    quantity: int = 1
    silver_value: int = 0  # por unidade; 0 = backend vai buscar via preços


class LootUpload(BaseModel):
    entries: list[LootEntryIn]
    replace: bool = True  # substitui entradas anteriores do evento


class ChestEntryIn(BaseModel):
    item_type: str
    item_name: str
    quantity: int = 1
    silver_value: int = 0
    deposited_by_name: str | None = None
    deposited_by_user_id: int | None = None


class ChestUpload(BaseModel):
    snapshot_at: datetime
    entries: list[ChestEntryIn]
    replace: bool = True


# ── Output (API → frontend / bot) ────────────────────────────────────────────

class LootEntryOut(BaseModel):
    id: int
    looted_by_name: str
    looted_by_user_id: int | None
    item_type: str
    item_name: str
    quantity: int
    silver_value: int
    in_chest: bool


class ChestEntryOut(BaseModel):
    id: int
    item_type: str
    item_name: str
    quantity: int
    silver_value: int
    deposited_by_name: str | None
    snapshot_at: datetime


class MissingItem(BaseModel):
    item_type: str
    item_name: str
    looted_qty: int
    chest_qty: int
    missing_qty: int
    silver_value: int  # valor unitário
    missing_value: int  # missing_qty * silver_value


class ReconcileResult(BaseModel):
    looted: list[LootEntryOut] = Field(default_factory=list)
    chest: list[ChestEntryOut] = Field(default_factory=list)
    missing: list[MissingItem] = Field(default_factory=list)
    total_looted_value: int = 0
    total_chest_value: int = 0
    missing_value: int = 0
    has_loot_log: bool = False
    has_chest_log: bool = False


# ── Reconciliação própria (lootlog + baú + mortes) ───────────────────────────

class LootReconcileEventOut(BaseModel):
    item_id: str
    item_name: str
    quantity: int
    looted_by: str
    looted_from: str | None


class NotDepositedLooter(BaseModel):
    looted_by: str
    qty: int


class NotDepositedOut(BaseModel):
    item_id: str
    item_name: str
    missing_qty: int
    looted_qty: int
    chest_qty: int
    silver_value: int       # unitário
    missing_value: int      # missing_qty * silver_value
    looters: list[NotDepositedLooter]


class RecoveredItem(BaseModel):
    item_id: str | None
    item_name: str | None
    quantity: int
    looted_by: str | None


class DeathLossOut(BaseModel):
    user_id: int | None
    display_name: str
    silver_value: int          # regear aprovado (gear perdida)
    notes: str | None
    recovered_items: list[RecoveredItem]  # itens saídos do cadáver (lootlog)


# ── Timeline por looter (foco no jogador, não no item) ───────────────────────
# status de cada item na visão do looter:
#   "missing"   — sobreviveu e trouxe pra cidade, mas NÃO depositou (vermelho /
#                 amarelo se conferido). É o "roubado".
#   "deposited" — chegou no baú (verde).
#   "died"      — pegou mas morreu carregando (cinza) — desconsiderado do devido.
class ReconcileLooterItem(BaseModel):
    item_id: str
    item_name: str
    status: str            # "missing" | "deposited" | "died"
    quantity: int
    silver_value: int      # unitário
    value: int             # quantity * silver_value
    verified: bool = False  # só relevante em status="missing"


class ReconcileLooter(BaseModel):
    looted_by: str
    missing_qty: int       # total de itens devidos (status=missing)
    missing_value: int     # soma dos value dos itens missing
    items: list[ReconcileLooterItem]


class ReconcileVerifyIn(BaseModel):
    looted_by: str
    item_id: str


class LootReconcileOut(BaseModel):
    has_loot_log: bool
    has_chest_log: bool
    has_deaths: bool
    deposited: list[ChestEntryOut] = Field(default_factory=list)
    not_deposited: list[NotDepositedOut] = Field(default_factory=list)
    looters: list[ReconcileLooter] = Field(default_factory=list)
    died_with: list[DeathLossOut] = Field(default_factory=list)
    loot_events: list[LootReconcileEventOut] = Field(default_factory=list)
    total_looted_value: int = 0
    total_chest_value: int = 0
    missing_value: int = 0
    total_regear_value: int = 0


# ── Preços ───────────────────────────────────────────────────────────────────

class ItemPriceOut(BaseModel):
    item_type: str
    silver_value: int
    source: str  # "cache" | "api" | "unknown"
    fetched_at: datetime | None = None
