"""Rotas de craft: preferências pessoais do usuário (sem guilda).

Focus efficiency é por conta Discord, não por guilda — a calculadora de craft
é uma view pública/global no frontend (sem guildId).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.schemas.craft import FocusEfficiencyIn
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