"""Rotas de loot: upload de logs de combate, inventário do baú e reconciliação."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api import deps
from app.api.schemas.loot import (
    ChestUpload, ItemPriceOut, LootUpload, ReconcileResult,
)
from app.models.tenancy import Guild
from app.services import loot as svc

router = APIRouter(tags=["loot"])


# ── Loot de evento ────────────────────────────────────────────────────────────

@router.post(
    "/guilds/{guild_id}/events/{event_id}/loots",
    response_model=ReconcileResult,
    status_code=201,
)
def upload_loot(
    guild_id: int,
    event_id: int,
    payload: LootUpload,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
):
    result = svc.upload_loot(db, guild.id, event_id, payload)
    db.commit()
    return result


@router.get(
    "/guilds/{guild_id}/events/{event_id}/loots",
    response_model=ReconcileResult,
)
def get_loot(
    guild_id: int,
    event_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
):
    return svc.reconcile(db, guild.id, event_id)


# ── Baú da guilda ─────────────────────────────────────────────────────────────

@router.post(
    "/guilds/{guild_id}/events/{event_id}/chest",
    response_model=ReconcileResult,
    status_code=201,
)
def upload_chest(
    guild_id: int,
    event_id: int,
    payload: ChestUpload,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
):
    result = svc.upload_chest(db, guild.id, event_id, payload)
    db.commit()
    return result


# ── Preços ────────────────────────────────────────────────────────────────────

@router.get(
    "/guilds/{guild_id}/items/prices/{item_type:path}",
    response_model=ItemPriceOut,
)
def item_price(
    guild_id: int,
    item_type: str,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
):
    try:
        return svc.get_price(db, item_type)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao buscar preço: {e}")
