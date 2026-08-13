"""Rotas de craft: preferências pessoais do usuário (sem guilda) + carrinhos
compartilháveis via link.

Focus efficiency é por conta Discord, não por guilda — a calculadora de craft
é uma view pública/global no frontend (sem guildId).

Carrinhos (POST/GET /craft/carts) são públicos: qualquer um pode criar/ler.
O código curto é a chave de lookup, não o id. Sem auth, sem rate limit próprio
(o rate limiter global de escrita já cobre).
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.api import deps
from app.api.schemas.craft import FocusEfficiencyIn
from app.models.craft import CraftCart
from app.models.tenancy import User

router = APIRouter(prefix="/craft", tags=["craft"])


@router.get("/focus-efficiency")
def get_focus_efficiency(user: User | None = Depends(deps.optional_user)) -> dict[str, int]:
    if user is None:
        return {}
    return user.craft_settings.get("focus_efficiency", {})


@router.put("/focus-efficiency")
async def set_focus_efficiency(
    body: FocusEfficiencyIn,
    user: User = Depends(deps.require_user),
    db: AsyncSession = Depends(deps.async_db_session),
) -> dict[str, int]:
    settings = dict(user.craft_settings or {})
    settings["focus_efficiency"] = body.values
    user.craft_settings = settings
    await db.commit()
    return settings["focus_efficiency"]


# ── Carrinhos compartilháveis ────────────────────────────────────────────────
# camelCase bate com o payload do frontend (variation.uniqueName, useFocus, ...).
class CartItemIn(BaseModel):
    uniqueName: str
    qty: int
    useFocus: bool
    placeLabel: str
    journalId: str | None = None
    transmuteTargetId: str | None = None


class CartIn(BaseModel):
    items: list[CartItemIn]


@router.post("/carts")
def save_cart(body: CartIn, db: Session = Depends(deps.db_session)) -> dict:
    # token_urlsafe(6) → ~8 chars, URL-safe (sem -/_ que precisam encoding na
    # maior parte; o que aparecer é seguro em path segment).
    code = secrets.token_urlsafe(6)
    cart = CraftCart(code=code, items=[it.model_dump() for it in body.items])
    db.add(cart)
    db.commit()
    return {"code": code}


@router.get("/carts/{code}")
def get_cart(code: str, db: Session = Depends(deps.db_session)) -> dict:
    cart = db.scalar(select(CraftCart).where(CraftCart.code == code))
    if cart is None:
        raise HTTPException(status_code=404, detail="cart not found")
    return {"code": cart.code, "items": cart.items, "created_at": cart.created_at}
