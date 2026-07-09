"""Rotas do lootlog anônimo: envio (membro logado) + área só-admin (site).

Auth: área de revisão é SÓ-ADMIN (guild.admin). Envio (`POST /ingest`) é
qualquer membro logado da guilda (era `/enviarlog` no bot, removido — a
função virou site-only).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import deps
from app.api.schemas.lootlog import (
    BotLootLogIngestOut, LoggerStandingOut, LootLogIngestOut, LootLogListOut,
    LootLogPreviewOut, LootLogSettingsIn, LootLogSettingsOut,
    LootLogSubmissionOut,
)
from app.models.audit import AuditLog
from app.models.events import Event
from app.models.tenancy import Guild, GuildMember, User
from app.services import lootlog

router = APIRouter(tags=["lootlog"])


# ── config (admin) ───────────────────────────────────────────────────────────

@router.get("/guilds/{guild_id}/lootlog/settings", response_model=LootLogSettingsOut)
def get_settings(
    guild: Guild = Depends(deps.tenant_guild),
    _member=Depends(deps.require_permission("guild.admin")),
):
    return LootLogSettingsOut(**lootlog.get_lootlog_settings(guild).to_dict())


@router.put("/guilds/{guild_id}/lootlog/settings", response_model=LootLogSettingsOut)
def put_settings(
    payload: LootLogSettingsIn,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    member=Depends(deps.require_permission("guild.admin")),
):
    before = lootlog.get_lootlog_settings(guild).to_dict()
    after = lootlog.apply_lootlog_settings(guild, payload.model_dump(exclude_unset=True))
    db.add(AuditLog(
        guild_id=guild.id, actor_id=member.user_id, actor_type="site", source="site",
        action="lootlog.settings", entity="guild", entity_id=str(guild.id),
        before=before, after=after.to_dict(),
    ))
    db.commit()
    return LootLogSettingsOut(**after.to_dict())


# ── envio (membro logado → .csv do lootlogger) ───────────────────────────────

@router.post("/guilds/{guild_id}/lootlog/ingest", response_model=LootLogIngestOut)
async def ingest_log(
    guild_id: int,
    event_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(deps.db_session),
    user: User = Depends(deps.require_user),
    _member: GuildMember = Depends(deps.require_guild_member),
):
    """Membro logado envia o próprio .csv/.txt do lootlogger de um CTA."""
    fname = file.filename or "log.csv"
    if not fname.lower().endswith((".csv", ".txt")):
        raise HTTPException(400, "arquivo precisa ser .csv ou .txt")
    data = await file.read()
    if not data:
        raise HTTPException(400, "arquivo vazio")
    if len(data) > lootlog.MAX_FILE_BYTES:
        raise HTTPException(413, "arquivo grande demais (limite 15 MB)")
    try:
        return lootlog.ingest(
            db, guild_id, event_id, user.id, user.global_name or user.username,
            fname, data,
        )
    except lootlog.LootLogServiceError as e:
        raise HTTPException(400, str(e))


# ── envio (bot-v2 → thread de lootlog do evento) ────────────────────────────
# Auth por Bearer BOT_API_SECRET (sem cookie). O bot posta o .csv que um player
# anexou na thread de lootlog do evento; o event_id é resolvido pelo id da
# thread (Event.lootlog_thread_id). Resposta inclui o standings (cada logger +
# sua % do logger_pool) p/ o bot reagir/postar no thread.

@router.post("/bot/guilds/{guild_id}/lootlog/ingest", response_model=BotLootLogIngestOut)
async def bot_ingest_log(
    guild_id: int,
    file: UploadFile = File(...),
    msg_id: str | None = Form(None),
    submitter_name: str | None = Form(None),
    submitter_user_id: int | None = Form(None),
    thread_id: str | None = Form(None),
    db: Session = Depends(deps.db_session),
    _bot=Depends(deps.require_bot_api),
):
    guild = db.get(Guild, guild_id)
    if guild is None:
        raise HTTPException(404, "guilda não encontrada")
    if not thread_id:
        raise HTTPException(400, "thread_id é obrigatório")
    # Resolve o evento pela thread de lootlog criada pelo bot.
    ev = db.scalar(select(Event).where(
        Event.guild_id == guild_id,
        Event.lootlog_thread_id == int(thread_id),
    ))
    if ev is None:
        raise HTTPException(404, "nenhum evento atrelado a esta thread de lootlog")
    fname = file.filename or "log.csv"
    if not fname.lower().endswith((".csv", ".txt")):
        raise HTTPException(400, "arquivo precisa ser .csv ou .txt")
    data = await file.read()
    if not data:
        raise HTTPException(400, "arquivo vazio")
    if len(data) > lootlog.MAX_FILE_BYTES:
        raise HTTPException(413, "arquivo grande demais (limite 15 MB)")
    try:
        out = lootlog.ingest(
            db, guild_id, ev.id, submitter_user_id, submitter_name, fname, data,
        )
    except lootlog.LootLogServiceError as e:
        raise HTTPException(400, str(e))
    # Standings atual: % de cada logger no logger_pool (pesos bot-v1).
    weights = lootlog.compute_logger_weights(db, guild_id, ev.id)
    total_w = sum(weights.values())
    standings = [
        LoggerStandingOut(user_id=uid,
                          percent=round(100 * w / total_w) if total_w > 0 else 0)
        for uid, w in sorted(weights.items(), key=lambda x: -x[1])
    ]
    return BotLootLogIngestOut(
        id=out.id, row_count=out.row_count, silver_total=out.silver_total,
        is_update=out.is_update, standings=standings,
    )


# ── área só-admin (site) ─────────────────────────────────────────────────────

@router.get("/guilds/{guild_id}/lootlog", response_model=LootLogListOut)
def list_submissions(
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    event_id: int = Query(...),
    _member=Depends(deps.require_permission("guild.admin")),
):
    return LootLogListOut(submissions=lootlog.list_submissions(db, guild.id, event_id))


@router.get("/guilds/{guild_id}/lootlog/preview/{event_id}", response_model=LootLogPreviewOut)
def get_preview(
    event_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _member=Depends(deps.require_permission("guild.admin")),
):
    out = lootlog.preview(db, guild, event_id)
    if out is None:
        raise HTTPException(404, "CTA não encontrado")
    return out


@router.get("/guilds/{guild_id}/lootlog/{submission_id}", response_model=LootLogSubmissionOut)
def get_submission(
    submission_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _member=Depends(deps.require_permission("guild.admin")),
):
    out = lootlog.get_submission(db, guild.id, submission_id)
    if out is None:
        raise HTTPException(404, "submissão não encontrada")
    return out


@router.delete("/guilds/{guild_id}/lootlog/{submission_id}")
def remove_submission(
    submission_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    member=Depends(deps.require_permission("guild.admin")),
):
    try:
        lootlog.remove_submission(db, guild.id, submission_id, member.user_id)
    except lootlog.LootLogServiceError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}