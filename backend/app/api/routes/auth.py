"""Autenticação Discord + gestão de guilda + permissões por cargo."""
from __future__ import annotations

import secrets
import time
from typing import Any

import httpx
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import deps
from app.auth import discord
from app.auth.session import make_session
from app.config import get_settings
from app.models.audit import AuditLog
from app.models.tenancy import Guild, GuildMember, GuildRolePermission, User

router = APIRouter(tags=["auth"])

_STATE_COOKIE = "ziggs_oauth_state"
# Cache de 60s para guilds Discord do usuário — evita rate limit 429
_guilds_cache: dict[str, tuple[list, float]] = {}
_GUILDS_TTL = 60

PERMISSION_KEYS = [
    "events.view", "events.create", "events.manage",
    "comps.view",  "comps.create",  "comps.manage",
    "guild.admin",
]
_ALL_TRUE  = {k: True  for k in PERMISSION_KEYS}
_ALL_FALSE = {k: False for k in PERMISSION_KEYS}


# ── OAuth ─────────────────────────────────────────────────────────────────────

@router.get("/auth/discord/login")
def discord_login():
    state = secrets.token_urlsafe(24)
    resp = RedirectResponse(discord.build_authorize_url(state))
    resp.set_cookie(_STATE_COOKIE, state, max_age=600, httponly=True, samesite="lax")
    return resp


@router.get("/auth/discord/callback")
def discord_callback(
    code: str = Query(...),
    state: str = Query(...),
    oauth_state: str | None = Cookie(default=None, alias=_STATE_COOKIE),
    db: Session = Depends(deps.db_session),
):
    if not oauth_state or not secrets.compare_digest(oauth_state, state):
        raise HTTPException(400, "state inválido")

    try:
        token  = discord.exchange_code(code)
        profile = discord.fetch_user(token["access_token"])
    except httpx.HTTPError as e:
        raise HTTPException(502, f"falha OAuth Discord: {e}")

    uid = int(profile["id"])
    user = db.scalar(select(User).where(User.id == uid))
    is_new = user is None
    if user is None:
        user = User(id=uid)
        db.add(user)
    user.username    = profile.get("username") or user.username or str(uid)
    user.global_name = profile.get("global_name")
    user.avatar      = profile.get("avatar")
    user.discord_access_token = token.get("access_token")

    db.add(AuditLog(
        guild_id=0, actor_id=uid, actor_type="site", source="site",
        action="auth.login", entity="user", entity_id=str(uid),
        after={"new": is_new},
    ))
    db.commit()

    s = get_settings()
    resp = RedirectResponse(s.frontend_url)
    resp.set_cookie(
        s.session_cookie_name, make_session(uid),
        max_age=s.session_max_age, httponly=True, samesite="lax",
        secure=(s.environment != "development"),
    )
    resp.delete_cookie(_STATE_COOKIE)
    return resp


# ── Me ────────────────────────────────────────────────────────────────────────

@router.get("/auth/me")
def me(user: User = Depends(deps.require_user)):
    return {
        "id": str(user.id),
        "username": user.username,
        "global_name": user.global_name,
        "avatar": user.avatar,
        "guild_id": str(user.current_guild_id) if user.current_guild_id else None,
    }


# ── Servidores Discord ────────────────────────────────────────────────────────

@router.get("/auth/guilds")
def my_discord_guilds(
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
):
    if not user.discord_access_token:
        raise HTTPException(400, "token Discord não disponível, faça login novamente")

    token = user.discord_access_token
    now = time.monotonic()
    cached = _guilds_cache.get(token)
    if cached and (now - cached[1]) < _GUILDS_TTL:
        guilds = cached[0]
    else:
        try:
            guilds = discord.fetch_guilds(token)
            _guilds_cache[token] = (guilds, now)
        except httpx.HTTPError as e:
            if cached:
                guilds = cached[0]  # usa cache vencido se disponível
            else:
                raise HTTPException(502, f"erro ao buscar servidores: {e}")

    ids = [int(g["id"]) for g in guilds]
    db_guilds: dict[int, Guild] = {}
    if ids:
        rows = db.scalars(select(Guild).where(Guild.id.in_(ids))).all()
        db_guilds = {row.id: row for row in rows}

    MANAGE_GUILD = 0x20
    return [
        {
            "id": g["id"],
            "name": g["name"],
            "icon": g.get("icon"),
            "is_admin": bool(int(g.get("permissions", 0)) & MANAGE_GUILD),
            "bot_present": db_guilds.get(int(g["id"]), Guild()).bot_present,
        }
        for g in guilds
    ]


# ── Selecionar / trocar guilda ────────────────────────────────────────────────

class SelectGuildIn(BaseModel):
    guild_id: str  # string para preservar precisão de IDs Discord (64-bit)
    guild_name: str
    icon: str | None = None
    is_admin: bool = False


def _sync_member_roles(user: User, guild_id: int, member: GuildMember) -> None:
    """Busca as roles Discord do membro e atualiza o registro (best-effort)."""
    if not user.discord_access_token:
        return
    try:
        data = discord.fetch_guild_member(str(guild_id), user.discord_access_token)
        member.discord_role_ids = data.get("roles", [])
    except Exception:
        pass  # roles serão sincronizadas pelo bot futuramente


@router.post("/auth/select-guild")
def select_guild(
    body: SelectGuildIn,
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
):
    gid = int(body.guild_id)
    guild = db.scalar(select(Guild).where(Guild.id == gid))
    if guild is None:
        guild = Guild(id=gid, name=body.guild_name, icon=body.icon)
        db.add(guild)
    else:
        guild.name = body.guild_name
        if body.icon:
            guild.icon = body.icon
    db.flush()

    member = db.scalar(select(GuildMember).where(
        GuildMember.guild_id == gid,
        GuildMember.user_id == user.id,
    ))
    if member is None:
        member = GuildMember(guild_id=gid, user_id=user.id)
        db.add(member)
    member.is_guild_admin = body.is_admin

    _sync_member_roles(user, gid, member)

    if not guild.bot_present:
        s = get_settings()
        if s.discord_bot_token:
            try:
                discord.fetch_guild(str(gid), s.discord_bot_token)
                guild.bot_present = True
            except httpx.HTTPError:
                pass

    user.current_guild_id = gid
    db.commit()
    return {"guild_id": str(guild.id), "bot_present": guild.bot_present}


@router.post("/auth/switch-guild/{guild_id}")
def switch_guild(
    guild_id: int,
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
):
    member = db.scalar(select(GuildMember).where(
        GuildMember.guild_id == guild_id, GuildMember.user_id == user.id,
    ))
    if member is None:
        raise HTTPException(403, "sem acesso a essa guilda")
    _sync_member_roles(user, guild_id, member)
    user.current_guild_id = guild_id
    db.commit()
    guild = db.scalar(select(Guild).where(Guild.id == guild_id))
    return {"guild_id": str(guild_id), "bot_present": guild.bot_present if guild else False}


# ── Guildas vinculadas ao usuário no site ─────────────────────────────────────

@router.get("/auth/my-site-guilds")
def my_site_guilds(
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
):
    members = db.scalars(select(GuildMember).where(GuildMember.user_id == user.id)).all()
    s = get_settings()
    result = []
    updated = False
    for m in members:
        g = db.scalar(select(Guild).where(Guild.id == m.guild_id))
        if g:
            if not g.bot_present and s.discord_bot_token:
                try:
                    discord.fetch_guild(str(g.id), s.discord_bot_token)
                    g.bot_present = True
                    updated = True
                except httpx.HTTPError:
                    pass
            result.append({
                "id": str(g.id), "name": g.name, "icon": g.icon,
                "bot_present": g.bot_present, "albion_guild_name": g.albion_guild_name,
            })
    if updated:
        db.commit()
    return result


# ── Info e configurações da guilda ────────────────────────────────────────────

@router.get("/auth/guild-info/{guild_id}")
def guild_info(
    guild_id: int,
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
):
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        raise HTTPException(404)
    s = get_settings()
    if s.discord_bot_token:
        try:
            discord.fetch_guild(str(guild_id), s.discord_bot_token)
            if not g.bot_present:
                g.bot_present = True
                db.commit()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                if g.bot_present:
                    g.bot_present = False
                    db.commit()
            # 401 = token inválido → mantém valor do heartbeat no DB
        except httpx.HTTPError:
            pass  # erro de rede → mantém valor do DB
    return {
        "id": str(g.id), "name": g.name, "icon": g.icon,
        "bot_present": g.bot_present,
        "albion_guild_name": g.albion_guild_name,
        "settings": g.settings,
    }


class GuildSettingsIn(BaseModel):
    albion_guild_name: str | None = None


@router.patch("/auth/guild-settings/{guild_id}")
def update_guild_settings(
    guild_id: int,
    body: GuildSettingsIn,
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
):
    _require_member(db, user, guild_id)
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        raise HTTPException(404)
    if body.albion_guild_name is not None:
        g.albion_guild_name = body.albion_guild_name or None
    db.commit()
    return {"ok": True}


# ── Permissões ────────────────────────────────────────────────────────────────

@router.get("/auth/my-permissions")
def my_permissions(
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
):
    if not user.current_guild_id:
        return _ALL_FALSE

    member = db.scalar(select(GuildMember).where(
        GuildMember.guild_id == user.current_guild_id,
        GuildMember.user_id == user.id,
    ))
    if member is None:
        return _ALL_FALSE

    # admins do servidor têm tudo
    if member.is_guild_admin:
        return _ALL_TRUE

    role_ids = {int(r) for r in (member.discord_role_ids or [])}
    if not role_ids:
        return _ALL_FALSE

    role_perms = db.scalars(select(GuildRolePermission).where(
        GuildRolePermission.guild_id == user.current_guild_id,
        GuildRolePermission.discord_role_id.in_(role_ids),
    )).all()

    effective = dict(_ALL_FALSE)
    for rp in role_perms:
        for k, v in rp.permissions.items():
            if v and k in effective:
                effective[k] = True
    return effective


# ── Cargos Discord e suas permissões ─────────────────────────────────────────

@router.get("/auth/guild-discord-roles/{guild_id}")
def guild_discord_roles(
    guild_id: int,
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
):
    """Lista cargos do servidor Discord com permissões configuradas."""
    _require_admin(db, user, guild_id)
    s = get_settings()
    if not s.discord_bot_token:
        raise HTTPException(503, "bot token não configurado")
    try:
        roles = discord.fetch_guild_roles(str(guild_id), s.discord_bot_token)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            raise HTTPException(502, "Token do bot inválido. Redefina o token no Discord Developer Portal e atualize o .env do backend.")
        raise HTTPException(502, f"Discord retornou {e.response.status_code}")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Erro de conexão com Discord: {e}")

    existing: dict[int, GuildRolePermission] = {
        rp.discord_role_id: rp
        for rp in db.scalars(select(GuildRolePermission).where(
            GuildRolePermission.guild_id == guild_id
        )).all()
    }

    return [
        {
            "id": r["id"],
            "name": r["name"],
            "color": r["color"],
            "permissions": existing[int(r["id"])].permissions if int(r["id"]) in existing else {},
        }
        for r in roles
        if r["name"] != "@everyone"
    ]


class RolePermissionsIn(BaseModel):
    role_name: str
    permissions: dict[str, Any]


@router.patch("/auth/guild-discord-roles/{guild_id}/{role_id}")
def update_role_permissions(
    guild_id: int,
    role_id: int,
    body: RolePermissionsIn,
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
):
    _require_admin(db, user, guild_id)
    rp = db.scalar(select(GuildRolePermission).where(
        GuildRolePermission.guild_id == guild_id,
        GuildRolePermission.discord_role_id == role_id,
    ))
    if rp is None:
        rp = GuildRolePermission(
            guild_id=guild_id,
            discord_role_id=role_id,
            discord_role_name=body.role_name,
            permissions={},
        )
        db.add(rp)
    rp.discord_role_name = body.role_name
    rp.permissions = {k: bool(v) for k, v in body.permissions.items() if k in PERMISSION_KEYS}
    db.commit()
    return {"ok": True}


# ── Comandos por servidor ─────────────────────────────────────────────────────

COMMANDS_REGISTRY = [
    {"name": "avatar", "description": "Mostra o avatar de um usuário ou servidor"},
    {"name": "banner", "description": "Mostra o banner de um usuário"},
]


@router.get("/auth/guild-commands/{guild_id}")
def guild_commands(
    guild_id: int,
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
):
    _require_admin(db, user, guild_id)
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    disabled: set[str] = set((g.settings or {}).get("disabled_commands", [])) if g else set()
    return [
        {"name": c["name"], "description": c["description"], "enabled": c["name"] not in disabled}
        for c in COMMANDS_REGISTRY
    ]


class CommandToggleIn(BaseModel):
    enabled: bool


@router.patch("/auth/guild-commands/{guild_id}/{command_name}")
def toggle_guild_command(
    guild_id: int,
    command_name: str,
    body: CommandToggleIn,
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
):
    _require_admin(db, user, guild_id)
    if command_name not in {c["name"] for c in COMMANDS_REGISTRY}:
        raise HTTPException(404, "comando desconhecido")
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    if not g:
        raise HTTPException(404)
    settings = dict(g.settings or {})
    disabled: set[str] = set(settings.get("disabled_commands", []))
    if body.enabled:
        disabled.discard(command_name)
    else:
        disabled.add(command_name)
    settings["disabled_commands"] = sorted(disabled)
    g.settings = settings
    db.commit()
    return {"ok": True}


@router.get("/bot/guild-commands/{guild_id}")
def bot_guild_commands(
    guild_id: int,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    s = get_settings()
    if authorization != f"Bearer {s.bot_api_secret}":
        raise HTTPException(401)
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    return {"disabled": (g.settings or {}).get("disabled_commands", []) if g else []}


# ── Bot heartbeat ─────────────────────────────────────────────────────────────

class HeartbeatIn(BaseModel):
    guild_name: str | None = None
    guild_icon: str | None = None


@router.post("/bot/heartbeat/{guild_id}")
def bot_heartbeat(
    guild_id: int,
    body: HeartbeatIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    s = get_settings()
    if authorization != f"Bearer {s.bot_api_secret}":
        raise HTTPException(401)
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None and body.guild_name:
        g = Guild(id=guild_id, name=body.guild_name, icon=body.guild_icon)
        db.add(g)
    if g:
        g.bot_present = True
        if body.guild_name:
            g.name = body.guild_name
        db.commit()
    return {"ok": True}


# ── Bot goodbye ───────────────────────────────────────────────────────────────

@router.post("/bot/goodbye/{guild_id}")
def bot_goodbye(
    guild_id: int,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    s = get_settings()
    if authorization != f"Bearer {s.bot_api_secret}":
        raise HTTPException(401)
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    if g:
        g.bot_present = False
        db.commit()
    return {"ok": True}


# ── Logout ─────────────────────────────────────────────────────────────────────

@router.post("/auth/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(get_settings().session_cookie_name)
    return resp


# ── Helpers internos ──────────────────────────────────────────────────────────

def _require_member(db: Session, user: User, guild_id: int) -> GuildMember:
    m = db.scalar(select(GuildMember).where(
        GuildMember.guild_id == guild_id, GuildMember.user_id == user.id,
    ))
    if m is None:
        raise HTTPException(403, "sem acesso")
    return m


def _require_admin(db: Session, user: User, guild_id: int) -> GuildMember:
    m = _require_member(db, user, guild_id)
    if not m.is_guild_admin:
        raise HTTPException(403, "requer admin do servidor")
    return m
