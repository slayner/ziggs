"""Rotas de composições (escopadas por guilda)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api import deps
from app.api.schemas.comps import (
    BuildSuggestionOut, CompCreate, CompRead, CompSummary, CompUpdate,
    SuggestRequest,
)
from app.models.tenancy import Guild
from app.services import comps as svc

router = APIRouter(prefix="/guilds/{guild_id}/comps", tags=["comps"])


@router.get("", response_model=list[CompSummary])
def list_comps(
    include_archived: bool = False,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _member=Depends(deps.require_permission("comps.view")),
):
    return svc.list_comps(db, guild.id, include_archived)


@router.post("", response_model=CompRead, status_code=201)
def create_comp(
    payload: CompCreate,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    actor_id: int | None = Depends(deps.current_user_id),
    _member=Depends(deps.require_permission("comps.create")),
):
    try:
        result = svc.create_comp(db, guild.id, payload, actor_id)
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return result


@router.get("/{comp_id}", response_model=CompRead)
def get_comp(
    comp_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _member=Depends(deps.require_permission("comps.view")),
):
    comp = svc.get_comp(db, guild.id, comp_id)
    if comp is None:
        raise HTTPException(status_code=404, detail="comp não encontrada")
    return comp


@router.patch("/{comp_id}", response_model=CompRead)
def update_comp(
    comp_id: int,
    payload: CompUpdate,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    actor_id: int | None = Depends(deps.current_user_id),
    _member=Depends(deps.require_permission("comps.manage")),
):
    try:
        comp = svc.update_comp(db, guild.id, comp_id, payload, actor_id)
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if comp is None:
        raise HTTPException(status_code=404, detail="comp não encontrada")
    db.commit()
    return comp


@router.delete("/{comp_id}", status_code=204)
def delete_comp(
    comp_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    actor_id: int | None = Depends(deps.current_user_id),
    _member=Depends(deps.require_permission("comps.manage")),
):
    if not svc.delete_comp(db, guild.id, comp_id, actor_id):
        raise HTTPException(status_code=404, detail="comp não encontrada")
    db.commit()


@router.post("/{comp_id}/suggest", response_model=BuildSuggestionOut)
def suggest_build(
    comp_id: int,
    payload: SuggestRequest,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _member=Depends(deps.require_permission("comps.view")),
):
    try:
        return svc.suggest(db, guild.id, comp_id, payload.target_function, payload.scope)
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
