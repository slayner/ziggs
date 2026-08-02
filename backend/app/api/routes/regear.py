"""Rotas de regear: config da guilda + fila de requests por screenshot.

Auth: config é `guild.admin`; fila segue `events.view`/`events.manage`.
Ingestão (`POST /ingest`) é auth por bot (Bearer BOT_API_SECRET), sem cookie.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api import deps
from app.api.schemas.regear import (
    RegearListOut, RegearRequestOut, RegearRequestUpdate, RegearSettingsIn,
    RegearSettingsOut,
)
from app.models.audit import AuditLog
from app.models.tenancy import Guild
from app.services import regear, regear_config

router = APIRouter(tags=["regear"])


# ── config da guilda ───────────────────────────────────────────────────────────

@router.get("/guilds/{guild_id}/regear/settings", response_model=RegearSettingsOut)
def get_settings(
    guild: Guild = Depends(deps.tenant_guild),
    _member=Depends(deps.require_permission("guild.admin")),
):
    return RegearSettingsOut(**regear_config.get_regear_settings(guild).to_dict())


@router.put("/guilds/{guild_id}/regear/settings", response_model=RegearSettingsOut)
def put_settings(
    payload: RegearSettingsIn,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    member=Depends(deps.require_permission("guild.admin")),
):
    before = regear_config.get_regear_settings(guild).to_dict()
    after = regear_config.apply_regear_settings(guild, payload.model_dump(exclude_unset=True))
    db.add(AuditLog(
        guild_id=guild.id, actor_id=member.user_id, actor_type="site", source="site",
        action="regear.settings", entity="guild", entity_id=str(guild.id),
        before=before, after=after.to_dict(),
    ))
    db.commit()
    return RegearSettingsOut(**after.to_dict())


# ── config para o bot (auth Bearer) ────────────────────────────────────────────
# O bot só precisa do channel_id pra saber qual canal monitorar. Auth por Bearer
# (sem cookie) — a rota guild.admin acima é pro site; esta é pro bot-v2.

@router.get("/bot/guilds/{guild_id}/regear/settings", response_model=RegearSettingsOut)
def bot_settings(
    guild_id: int,
    db: Session = Depends(deps.db_session),
    _bot=Depends(deps.require_bot_api),
):
    guild = db.get(Guild, guild_id)
    if guild is None:
        raise HTTPException(404, "guilda não encontrada")
    return RegearSettingsOut(**regear_config.get_regear_settings(guild).to_dict())


# ── ingestão (bot-v2 → reconhecimento) ─────────────────────────────────────────

@router.post("/guilds/{guild_id}/regear/ingest", response_model=RegearRequestOut)
async def ingest_screenshot(
    guild_id: int,
    file: UploadFile = File(...),
    msg_id: str | None = Form(None),
    requester_name: str | None = Form(None),
    requester_user_id: int | None = Form(None),
    channel_id: str | None = Form(None),
    event_id: int | None = Form(None),
    db: Session = Depends(deps.db_session),
    _bot=Depends(deps.require_bot_api),
):
    """Bot-v2 posta uma screenshot do canal de regear. Auth por Bearer secret.
    `channel_id` identifica QUAL dos canais monitorados recebeu (cada um pode
    ter sua própria % de cobertura — ver regear_config.RegearSettings.channels).
    `event_id` vincula ao evento (landmark); se omitido, o backend deriva do
    `channel_id` (thread de regear do evento → Event.regear_thread_id)."""
    guild = db.get(Guild, guild_id)
    if guild is None:
        raise HTTPException(404, "guilda não encontrada")
    # Libera read tx antes do await (file.read + regear.ingest faz HTTP).
    db.commit()
    image = await file.read(10 * 1024 * 1024 + 1)
    if len(image) > 10 * 1024 * 1024:
        raise HTTPException(413, "imagem excede o limite de 10 MB")
    if not image:
        raise HTTPException(400, "imagem vazia")
    try:
        return await regear.ingest(
            db, guild, image, file.filename or "screenshot.png",
            file.content_type, requester_user_id, requester_name, msg_id, channel_id,
            event_id,
        )
    except regear.RegearServiceError as e:
        raise HTTPException(400, str(e))


# ── fila (site) ─────────────────────────────────────────────────────────────────

@router.get("/guilds/{guild_id}/regear", response_model=RegearListOut)
def list_requests(
    guild_id: int,
    status: str | None = None,
    event_id: int | None = None,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _member=Depends(deps.require_permission("events.view")),
):
    return RegearListOut(requests=regear.list_requests(db, guild.id, status, event_id))


@router.get("/guilds/{guild_id}/regear/{request_id}", response_model=RegearRequestOut)
def get_request(
    request_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _member=Depends(deps.require_permission("events.view")),
):
    out = regear.get_request(db, guild.id, request_id)
    if out is None:
        raise HTTPException(404, "pedido não encontrado")
    return out


@router.get("/guilds/{guild_id}/regear/{request_id}/screenshot")
def serve_screenshot(
    request_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _member=Depends(deps.require_permission("events.view")),
):
    """Serve a imagem salva — auth events.view + valida posse da guilda
    (evita vazamento cross-guild via path)."""
    row = regear.get_request_row(db, guild.id, request_id)
    if row is None:
        raise HTTPException(404, "pedido não encontrado")
    path = regear.screenshot_abs_path(row.screenshot_path)
    if not path.startswith(regear._IMAGES_DIR) or not os.path.isfile(path):
        raise HTTPException(404, "screenshot não encontrada")
    return FileResponse(path)


@router.patch("/guilds/{guild_id}/regear/{request_id}", response_model=RegearRequestOut)
def update_request(
    request_id: int,
    payload: RegearRequestUpdate,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    member=Depends(deps.require_permission("events.view")),
):
    try:
        return regear.update_request(
            db, guild.id, request_id, payload.model_dump(exclude_unset=True), member.user_id,
            actor_role_ids={int(x) for x in (member.discord_role_ids or [])},
            actor_is_admin=member.is_guild_admin,
        )
    except regear.RegearServiceError as e:
        raise HTTPException(404, str(e))


@router.delete("/guilds/{guild_id}/regear/{request_id}")
def remove_request(
    request_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    member=Depends(deps.require_permission("events.manage")),
):
    try:
        regear.remove_request(db, guild.id, request_id, member.user_id)
    except regear.RegearServiceError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}
