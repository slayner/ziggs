"""Rotas de composições (escopadas por guilda)."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api import deps
from app.api.schemas.comps import (
    BuildSuggestionOut, CompCreate, CompRead, CompSummary, CompUpdate,
    FnTypesOut, FnTypesUpdate, SuggestRequest,
)
from app.models.tenancy import Guild
from app.services import comps as svc

# ponytail: todas as rotas chamam services síncronos (svc.list_comps/create_comp/
# get_comp/update_comp/delete_comp/suggest) que recebem Session sync — não dá
# pra passar AsyncSession. Migra quando o service comps migrar.

router = APIRouter(prefix="/guilds/{guild_id}/comps", tags=["comps"])

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_FN_TYPES = 30


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


# ── Tipos de função (fn-types) ──────────────────────────────────────────────
# Antes vivia em localStorage (hideout_fn_types) — por-BROWSER, não por-guilda:
# cada membro via cores/labels diferentes. Agora fica em Guild.settings, igual
# ao resto da config da guilda (auth.py update_guild_settings) — mas com
# endpoint próprio aqui porque pertence a comps.manage, não ao guild.admin de
# PATCH /auth/guild-settings.
#
# ponytail: registrado ANTES de /{comp_id} de propósito — em FastAPI/Starlette
# rotas casam por ORDEM de registro, e {comp_id} (sem conversor de tipo no
# path template) casaria com a string literal "fn-types" antes de chegar
# aqui, gerando 422 (falha ao converter "fn-types" pra int) em vez de rotear
# certo. Comp_id NUNCA pode ser a string "fn-types" mesmo sem essa ordem (é
# sempre um id numérico do banco), mas a ordem de registro é o que garante
# isso na prática.
@router.get("/fn-types", response_model=FnTypesOut)
def get_fn_types(
    guild: Guild = Depends(deps.tenant_guild),
    _member=Depends(deps.require_permission("comps.view")),
):
    return {"fn_types": (guild.settings or {}).get("fn_types", [])}


@router.put("/fn-types", response_model=FnTypesOut)
def put_fn_types(
    payload: FnTypesUpdate,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _member=Depends(deps.require_permission("comps.manage")),
):
    if len(payload.fn_types) > MAX_FN_TYPES:
        raise HTTPException(400, f"máximo de {MAX_FN_TYPES} tipos de função")
    keys = [ft.key for ft in payload.fn_types]
    if len(keys) != len(set(keys)):
        raise HTTPException(400, "chaves de tipo de função devem ser únicas")
    for ft in payload.fn_types:
        if not _HEX_COLOR_RE.match(ft.color):
            raise HTTPException(400, f"cor inválida: {ft.color!r} (use #rrggbb)")

    settings = dict(guild.settings or {})
    settings["fn_types"] = [ft.model_dump() for ft in payload.fn_types]
    guild.settings = settings
    db.commit()
    return {"fn_types": settings["fn_types"]}


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
        # Comp foi deletada por ficar vazia depois do save — 204, não 404.
        db.commit()
        return Response(status_code=204)
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
