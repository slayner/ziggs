"""Autenticação Discord + gestão de guilda + permissões por cargo."""
from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any

import httpx
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import deps
from app.auth import discord
from app.auth.crypto import decrypt_token, encrypt_token
from app.auth.permissions import ALL_FALSE, PERMISSION_KEYS, compute_permissions
from app.auth.session import make_session
from app.config import get_settings
from app.models.audit import AuditLog
from app.models.battles import BattleGuild
from app.models.economy import EconomyBalance, EconomyTransaction
from app.models.registration import BotRegistration
from app.models.tenancy import Guild, GuildMember, GuildRolePermission, User
from app.services.player_tracker import HOSTS, make_client

router = APIRouter(tags=["auth"])

_STATE_COOKIE = "ziggs_oauth_state"
# Cache de 60s para guilds Discord do usuário — evita rate limit 429
_guilds_cache: dict[int, tuple[list, float]] = {}
_GUILDS_TTL = 60


def _require_bot_secret(authorization: str) -> None:
    """Compara em tempo constante — `!=` normal vaza timing por byte comparado."""
    if not secrets.compare_digest(authorization, f"Bearer {get_settings().bot_api_secret}"):
        raise HTTPException(401)


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
    access_token = token.get("access_token")
    user.discord_access_token = encrypt_token(access_token) if access_token else None

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
    token = decrypt_token(user.discord_access_token)
    if not token:
        raise HTTPException(400, "token Discord não disponível, faça login novamente")

    now = time.monotonic()
    # chave = user.id, não o token: o token muda a cada login e uma chave nova
    # por token nunca seria liberada (memory leak ao longo do tempo).
    cached = _guilds_cache.get(user.id)
    if cached and (now - cached[1]) < _GUILDS_TTL:
        guilds = cached[0]
    else:
        try:
            guilds = discord.fetch_guilds(token)
            _guilds_cache[user.id] = (guilds, now)
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
    token = decrypt_token(user.discord_access_token)
    if not token:
        return
    try:
        data = discord.fetch_guild_member(str(guild_id), token)
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
        "albion_alliance_id": g.albion_alliance_id,
        "albion_alliance_name": g.albion_alliance_name,
        "settings": g.settings,
    }


ALBION_LOOKUP_RETRIES = 3
ALBION_LOOKUP_BACKOFF = 0.6  # segundos, multiplicado pela tentativa (0.6s, 1.2s, ...)


async def _lookup_albion_guild(name: str, region: str | None = None) -> dict | None:
    """Acha a guilda pelo nome exato na busca da Albion (mesmo endpoint usado
    pra jogadores) — devolve o registro de `guilds` com Id/AllianceId/AllianceName.
    Sem isso, albion_guild_id nunca seria preenchido e o /register nunca bateria
    com nenhuma guilda (sempre "no_albion_guild").

    Se uma região foi configurada, busca só nela — nomes de guilda podem se
    repetir entre regiões, então tentar todas correria o risco de resolver pra
    guilda errada. Cada host leva algumas tentativas com backoff: a API da
    Albion cai/timeout com frequência sob carga, e isso é só uma busca por
    nome, não algo crítico de latência."""
    nl = name.lower()
    hosts = [HOSTS[region]] if region in HOSTS else list(HOSTS.values())
    async with make_client() as client:
        for host in hosts:
            for attempt in range(ALBION_LOOKUP_RETRIES):
                try:
                    resp = await client.get(f"https://{host}/api/gameinfo/search", params={"q": name})
                    resp.raise_for_status()
                except httpx.HTTPError:
                    if attempt + 1 < ALBION_LOOKUP_RETRIES:
                        await asyncio.sleep(ALBION_LOOKUP_BACKOFF * (attempt + 1))
                    continue
                candidates = resp.json().get("guilds", [])
                match = next((g for g in candidates if (g.get("Name") or "").lower() == nl), None)
                if match:
                    return match
                break  # resposta válida (só não tem essa guilda nessa região) — não repete
    return None


BOT_LANGUAGES = {"pt", "en", "es"}


class GuildSettingsIn(BaseModel):
    albion_guild_name: str | None = None
    albion_guild_region: str | None = None
    register_role_id: str | None = None
    ally_role_id: str | None = None
    ally_allowed_guilds: list[str] | None = None
    bot_language: str | None = None


@router.patch("/auth/guild-settings/{guild_id}")
async def update_guild_settings(
    guild_id: int,
    body: GuildSettingsIn,
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
):
    _require_admin(db, user, guild_id)
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        raise HTTPException(404)

    settings = dict(g.settings or {})
    if "albion_guild_region" in body.model_fields_set:
        if body.albion_guild_region in HOSTS:
            settings["albion_guild_region"] = body.albion_guild_region
        else:
            settings.pop("albion_guild_region", None)

    albion_guild_resolved = True
    if body.albion_guild_name is not None:
        name = body.albion_guild_name or None
        g.albion_guild_name = name
        if name:
            # Resolve nome → id assim que a guilda é definida, em vez de esperar
            # o primeiro /register — já traz a aliança de bônus, no mesmo request.
            found = await _lookup_albion_guild(name, settings.get("albion_guild_region"))
            albion_guild_resolved = found is not None
            if found:
                g.albion_guild_id = str(found["Id"])
                g.albion_alliance_id = str(found.get("AllianceId") or "") or None
                g.albion_alliance_name = found.get("AllianceName") or None
        else:
            g.albion_guild_id = None
            g.albion_alliance_id = None
            g.albion_alliance_name = None

    if "register_role_id" in body.model_fields_set:
        if body.register_role_id:
            settings["register_role_id"] = body.register_role_id
        else:
            settings.pop("register_role_id", None)
    if "ally_role_id" in body.model_fields_set:
        if body.ally_role_id:
            settings["ally_role_id"] = body.ally_role_id
        else:
            settings.pop("ally_role_id", None)
    if "ally_allowed_guilds" in body.model_fields_set:
        if body.ally_allowed_guilds:
            settings["ally_allowed_guilds"] = body.ally_allowed_guilds
        else:
            settings.pop("ally_allowed_guilds", None)
    if "bot_language" in body.model_fields_set:
        if body.bot_language in BOT_LANGUAGES:
            settings["bot_language"] = body.bot_language
        else:
            settings.pop("bot_language", None)
    g.settings = settings

    db.commit()
    return {"ok": True, "albion_guild_resolved": albion_guild_resolved}


async def _is_guild_in_alliance(guild_id: str, alliance_id: str, region: str | None) -> bool:
    """`battle_guilds` é histórico (1 snapshot por batalha) — uma guilda que já
    saiu da aliança continua lá pra sempre com o alliance_id antigo. Confere o
    estado ATUAL direto na Albion antes de sugerir a guilda como aliada.
    Falha de rede = mantém na lista (fail-open): essa rota só monta a lista de
    sugestões pro admin escolher, quem decide de fato quem pode usar /register
    é o ally_allowed_guilds + o check ao vivo em bot_register/registration_checker."""
    hosts = [HOSTS[region]] if region in HOSTS else list(HOSTS.values())
    async with make_client() as client:
        for host in hosts:
            try:
                resp = await client.get(f"https://{host}/api/gameinfo/guilds/{guild_id}")
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
            except httpx.HTTPError:
                continue
            data = resp.json()
            return (str(data.get("AllianceId") or "") or None) == alliance_id
    return True


@router.get("/auth/guild-allies/{guild_id}")
async def guild_allies(
    guild_id: int,
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
):
    """Guildas conhecidas da mesma aliança (vistas pelo tracker de batalhas),
    pra montar a lista de aliados permitidos no /register. A aliança em si é
    descoberta automaticamente (ver bot_register) — sem aliança descoberta
    ainda, devolve lista vazia. Cada candidata é reconfirmada ao vivo (ver
    _is_guild_in_alliance) antes de entrar na resposta, pra não mostrar guilda
    que já saiu da aliança só porque apareceu numa batalha antiga."""
    _require_admin(db, user, guild_id)
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        raise HTTPException(404)
    if not g.albion_alliance_id:
        return []
    rows = db.execute(
        select(BattleGuild.albion_guild_id, BattleGuild.guild_name)
        .where(
            BattleGuild.alliance_id == g.albion_alliance_id,
            BattleGuild.albion_guild_id != str(g.albion_guild_id),
        )
        .distinct()
    ).all()
    if not rows:
        return []

    region = (g.settings or {}).get("albion_guild_region")
    still_in = await asyncio.gather(*(
        _is_guild_in_alliance(gid, g.albion_alliance_id, region) for gid, _ in rows
    ))
    return [{"id": gid, "name": name} for (gid, name), ok in zip(rows, still_in) if ok]


# ── Permissões ────────────────────────────────────────────────────────────────

@router.get("/auth/my-permissions")
def my_permissions(
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
):
    if not user.current_guild_id:
        return ALL_FALSE

    member = db.scalar(select(GuildMember).where(
        GuildMember.guild_id == user.current_guild_id,
        GuildMember.user_id == user.id,
    ))
    if member is None:
        return ALL_FALSE

    return compute_permissions(db, member)


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

    # Discord devolve em ordem arbitrária (por ID) — ordena por `position` (maior
    # = mais alto na hierarquia) pra refletir a ordem real do servidor, já que
    # essa lista também é a fonte de verdade pra hierarquia em outras telas.
    roles = sorted(roles, key=lambda r: r.get("position", 0), reverse=True)

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
    {"name": "avatar", "description": "Mostra o avatar de um usuário ou servidor", "category": "miscellaneous"},
    {"name": "banner", "description": "Mostra o banner de um usuário ou servidor", "category": "miscellaneous"},
    {"name": "register", "description": "Vincula o nick do Albion e libera o cargo de membro configurado", "category": "management"},
    {"name": "unregister", "description": "Remove o registro e o cargo de um membro (controle/admin)", "category": "management"},
    {"name": "balance", "description": "Mostra o saldo de um usuário", "category": "economy"},
    {"name": "pay", "description": "Transfere prata do seu saldo para outro usuário", "category": "economy"},
    {"name": "addmoney", "description": "Adiciona prata ao saldo de um usuário", "category": "economy"},
    {"name": "removemoney", "description": "Remove prata do saldo de um usuário", "category": "economy"},
    {"name": "leaderboard", "description": "Ranking dos usuários pelo saldo atual de prata", "category": "economy"},
    {"name": "economystats", "description": "Mostra um snapshot da economia do servidor", "category": "economy"},
    {"name": "undo", "description": "Reverte uma transação de economia pelo ID", "category": "economy"},
]

# Default de allowed_roles pra comandos sensíveis quando o admin ainda não
# configurou nada — sem isso, "vazio = sem restrição" deixaria /unregister (e
# registrar outras pessoas via /register) liberado pra qualquer membro num
# servidor novo.
DEFAULT_ALLOWED_ROLES = {
    "unregister": ["admin"],
    "register_others": ["admin"],
    "removemoney": ["admin"],
    "addmoney": ["admin"],
    "economystats": ["admin"],
    "undo": ["admin"],
}

# "register_others" não é um comando próprio — é uma sub-permissão do
# /register (quem pode usar o comando pra vincular OUTRA pessoa, além de se
# auto-registrar). Reaproveita o mesmo mecanismo de allowed_roles/command_roles
# dos comandos de verdade, então precisa ser aceita aqui mesmo sem estar no
# COMMANDS_REGISTRY (que é só pra comandos reais, com toggle de ativar/desativar).
EXTRA_PERMISSION_KEYS = {"register_others"}


@router.get("/auth/guild-commands/{guild_id}")
def guild_commands(
    guild_id: int,
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
):
    _require_admin(db, user, guild_id)
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    settings = (g.settings or {}) if g else {}
    disabled: set[str] = set(settings.get("disabled_commands", []))
    command_roles: dict = settings.get("command_roles", {})
    return [
        {
            "name": c["name"],
            "description": c["description"],
            "category": c["category"],
            "enabled": c["name"] not in disabled,
            # vazio = sem restrição (qualquer membro pode usar) salvo default
            # mais seguro pra comandos sensíveis (ver DEFAULT_ALLOWED_ROLES).
            "allowed_roles": command_roles.get(c["name"], DEFAULT_ALLOWED_ROLES.get(c["name"], [])),
        }
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


class CommandRolesIn(BaseModel):
    # cada item é um ID de cargo Discord, ou um dos placeholders "everyone"/"admin".
    role_keys: list[str]


@router.patch("/auth/guild-commands/{guild_id}/{command_name}/roles")
def update_command_roles(
    guild_id: int,
    command_name: str,
    body: CommandRolesIn,
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
):
    _require_admin(db, user, guild_id)
    if command_name not in {c["name"] for c in COMMANDS_REGISTRY} | EXTRA_PERMISSION_KEYS:
        raise HTTPException(404, "comando desconhecido")
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    if not g:
        raise HTTPException(404)
    settings = dict(g.settings or {})
    command_roles = dict(settings.get("command_roles", {}))
    if body.role_keys:
        command_roles[command_name] = body.role_keys
    else:
        command_roles.pop(command_name, None)
    settings["command_roles"] = command_roles
    g.settings = settings
    db.commit()
    return {"ok": True}


@router.get("/bot/guild-commands/{guild_id}")
def bot_guild_commands(
    guild_id: int,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    settings = (g.settings or {}) if g else {}
    command_roles = dict(settings.get("command_roles", {}))
    for name, default in DEFAULT_ALLOWED_ROLES.items():
        command_roles.setdefault(name, default)
    return {
        "disabled": settings.get("disabled_commands", []),
        "register_role_id": settings.get("register_role_id"),
        "command_roles": command_roles,
        "language": settings.get("bot_language") or "pt",
    }


# ── Bot: /register ────────────────────────────────────────────────────────

class BotRegisterIn(BaseModel):
    discord_user_id: str
    albion_player_name: str


@router.post("/bot/register/{guild_id}")
async def bot_register(
    guild_id: int,
    body: BotRegisterIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Chamado pelo /register do bot. Verifica se o personagem está na guilda
    Albion configurada e devolve o cargo a atribuir — quem efetivamente
    atribui o cargo é o bot (já tem o Member da interação em mãos)."""
    _require_bot_secret(authorization)
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        raise HTTPException(404, "servidor não encontrado")
    if not g.albion_guild_id:
        return {"ok": False, "reason": "no_albion_guild"}

    name = body.albion_player_name.strip()
    nl = name.lower()
    found: dict | None = None
    region: str | None = None
    any_host_ok = False
    guild_region = (g.settings or {}).get("albion_guild_region")
    # Alianças e guildas não cruzam região (cada região é um servidor de jogo
    # separado) — se a região da guilda já é conhecida, busca só nela, senão um
    # personagem com o mesmo nick em outra região pode "casar" com a guilda errada.
    hosts = {guild_region: HOSTS[guild_region]} if guild_region in HOSTS else HOSTS
    async with make_client() as client:
        for r, host in hosts.items():
            for attempt in range(ALBION_LOOKUP_RETRIES):
                try:
                    resp = await client.get(f"https://{host}/api/gameinfo/search", params={"q": name})
                    resp.raise_for_status()
                except httpx.HTTPError:
                    if attempt + 1 < ALBION_LOOKUP_RETRIES:
                        await asyncio.sleep(ALBION_LOOKUP_BACKOFF * (attempt + 1))
                    continue
                any_host_ok = True
                candidates = resp.json().get("players", [])
                match = next((p for p in candidates if (p.get("Name") or "").lower() == nl), None)
                if match:
                    found, region = match, r
                break  # resposta válida (só não achou esse nick nessa região) — não repete
            if found:
                break

    if not found:
        # Sem NENHUMA resposta válida da API da Albion (fora do ar/instável) é
        # bem diferente de "esse nick não existe" — o bot trata isso como
        # transitório e tenta de novo sozinho em vez de dar um erro definitivo
        # que obrigaria o usuário a rodar /register outra vez.
        if not any_host_ok:
            return {"ok": False, "reason": "albion_unavailable"}
        return {"ok": False, "reason": "not_found"}

    player_guild_id = str(found.get("GuildId") or "")
    player_alliance_id = str(found.get("AllianceId") or "") or None
    settings = g.settings or {}
    is_ally = False

    if player_guild_id == str(g.albion_guild_id):
        # Membro direto — a busca já trouxe o AllianceId dele, e como aliança é
        # atributo da guilda (não do jogador), isso já basta pra descobrir a
        # aliança da própria guilda sem nenhuma chamada extra à API da Albion.
        if player_alliance_id and player_alliance_id != g.albion_alliance_id:
            g.albion_alliance_id = player_alliance_id
            g.albion_alliance_name = found.get("AllianceName") or None
        role_id = settings.get("register_role_id")
    elif g.albion_alliance_id and player_alliance_id == g.albion_alliance_id:
        allowed_allies = settings.get("ally_allowed_guilds") or ["none"]
        if "all" not in allowed_allies and player_guild_id not in allowed_allies:
            return {"ok": False, "reason": "ally_not_allowed"}
        is_ally = True
        role_id = settings.get("ally_role_id") or settings.get("register_role_id")
    else:
        return {"ok": False, "reason": "not_in_guild"}

    if not role_id:
        return {"ok": False, "reason": "no_role_configured"}

    albion_player_id = found["Id"]
    existing = db.scalar(select(BotRegistration).where(
        BotRegistration.guild_id == guild_id,
        BotRegistration.albion_player_id == albion_player_id,
    ))
    if existing and existing.active:
        return {"ok": False, "reason": "already_registered"}

    if existing:
        existing.discord_user_id = int(body.discord_user_id)
        existing.albion_player_name = found["Name"]
        existing.region = region
        existing.role_id = int(role_id)
        existing.is_ally = is_ally
        existing.active = True
    else:
        db.add(BotRegistration(
            guild_id=guild_id,
            discord_user_id=int(body.discord_user_id),
            albion_player_id=albion_player_id,
            albion_player_name=found["Name"],
            region=region,
            role_id=int(role_id),
            is_ally=is_ally,
            active=True,
        ))
    db.commit()
    return {"ok": True, "role_id": str(role_id), "albion_player_name": found["Name"]}


class BotUnregisterIn(BaseModel):
    discord_user_id: str | None = None
    albion_player_name: str | None = None


@router.post("/bot/unregister/{guild_id}")
def bot_unregister(
    guild_id: int,
    body: BotUnregisterIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Comando de controle /unregister (admin): desliga todos os registros
    ativos do usuário-alvo nessa guilda — identificado pelo Discord (id) OU
    pelo nick do Albion, já que o bot aceita os dois. Devolve role_ids e
    discord_user_ids pra o bot remover a tag de quem perdeu o registro."""
    _require_bot_secret(authorization)
    conditions = [BotRegistration.guild_id == guild_id, BotRegistration.active.is_(True)]
    if body.discord_user_id:
        conditions.append(BotRegistration.discord_user_id == int(body.discord_user_id))
    elif body.albion_player_name:
        conditions.append(func.lower(BotRegistration.albion_player_name) == body.albion_player_name.strip().lower())
    else:
        raise HTTPException(400, "informe discord_user_id ou albion_player_name")

    regs = db.scalars(select(BotRegistration).where(*conditions)).all()
    if not regs:
        return {"ok": False, "reason": "not_registered"}
    role_ids = sorted({str(r.role_id) for r in regs})
    discord_user_ids = sorted({str(r.discord_user_id) for r in regs})
    for r in regs:
        r.active = False
    db.commit()
    return {"ok": True, "role_ids": role_ids, "discord_user_ids": discord_user_ids}


class BotMemberGoneIn(BaseModel):
    discord_user_id: str


@router.post("/bot/registration-left-guild/{guild_id}")
def bot_registration_left_guild(
    guild_id: int,
    body: BotMemberGoneIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Chamado pelo bot em on_member_remove/on_member_ban: o usuário saiu do
    Discord, então qualquer registro ativo dele nessa guilda não faz mais
    sentido (não há cargo pra remover — ele já não está mais no servidor)."""
    _require_bot_secret(authorization)
    regs = db.scalars(select(BotRegistration).where(
        BotRegistration.guild_id == guild_id,
        BotRegistration.discord_user_id == int(body.discord_user_id),
        BotRegistration.active.is_(True),
    )).all()
    for r in regs:
        r.active = False
    db.commit()
    return {"ok": True}


class BotRoleRemovedIn(BaseModel):
    discord_user_id: str
    removed_role_ids: list[str]


@router.post("/bot/registration-role-removed/{guild_id}")
def bot_registration_role_removed(
    guild_id: int,
    body: BotRoleRemovedIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Chamado pelo bot em on_member_update quando algum cargo é tirado do
    membro manualmente: se o cargo removido era o de um registro ativo, esse
    registro perde a validade (sem isso, o /register original fica
    "esquecido" no banco mesmo sem o membro ter mais o cargo)."""
    _require_bot_secret(authorization)
    removed = {int(x) for x in body.removed_role_ids}
    regs = db.scalars(select(BotRegistration).where(
        BotRegistration.guild_id == guild_id,
        BotRegistration.discord_user_id == int(body.discord_user_id),
        BotRegistration.active.is_(True),
    )).all()
    for r in regs:
        if r.role_id in removed:
            r.active = False
    db.commit()
    return {"ok": True}


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
    _require_bot_secret(authorization)
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
    _require_bot_secret(authorization)
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    if g:
        g.bot_present = False
        db.commit()
    return {"ok": True}


# ── Bot: economia ─────────────────────────────────────────────────────────────
# Porta simplificada do sistema de economia do bot legado (bot/cogs/economy.py):
# só saldo por membro (balance/total_earned), sem banco da guild nem os outros
# rankings (attendance/mvp/kills/deaths — dependem de dados de CTA que não
# existem aqui). Regras de negócio tipo "quem pode usar" ficam do lado do bot
# (check_command_access, já configurável pelo site); aqui só valida o dinheiro
# em si (valor positivo, saldo suficiente).

def _get_or_create_balance(db: Session, guild_id: int, discord_user_id: int) -> EconomyBalance:
    bal = db.scalar(select(EconomyBalance).where(
        EconomyBalance.guild_id == guild_id, EconomyBalance.discord_user_id == discord_user_id,
    ))
    if bal is None:
        bal = EconomyBalance(guild_id=guild_id, discord_user_id=discord_user_id)
        db.add(bal)
        db.flush()
    return bal


@router.get("/bot/economy/balance/{guild_id}/{discord_user_id}")
def bot_economy_balance(
    guild_id: int,
    discord_user_id: int,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    bal = _get_or_create_balance(db, guild_id, discord_user_id)
    db.commit()
    return {"balance": bal.balance, "total_earned": bal.total_earned}


class EconomyPayIn(BaseModel):
    from_user_id: int
    to_user_id: int
    amount: int


@router.post("/bot/economy/pay/{guild_id}")
def bot_economy_pay(
    guild_id: int,
    body: EconomyPayIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    if body.amount <= 0:
        raise HTTPException(400, "amount deve ser positivo")
    sender = _get_or_create_balance(db, guild_id, body.from_user_id)
    if sender.balance < body.amount:
        return {"ok": False, "from_balance": sender.balance}
    receiver = _get_or_create_balance(db, guild_id, body.to_user_id)
    sender.balance -= body.amount
    receiver.balance += body.amount
    receiver.total_earned += body.amount
    tx = EconomyTransaction(
        guild_id=guild_id, kind="pay", actor_discord_id=body.from_user_id,
        from_user_id=body.from_user_id, to_user_id=body.to_user_id,
        total_earned_user_id=body.to_user_id, amount=body.amount,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return {"ok": True, "from_balance": sender.balance, "to_balance": receiver.balance, "transaction_id": tx.id}


class EconomyAddIn(BaseModel):
    discord_user_id: int
    amount: int
    actor_discord_id: int


@router.post("/bot/economy/add/{guild_id}")
def bot_economy_add(
    guild_id: int,
    body: EconomyAddIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    if body.amount <= 0:
        raise HTTPException(400, "amount deve ser positivo")
    bal = _get_or_create_balance(db, guild_id, body.discord_user_id)
    bal.balance += body.amount
    bal.total_earned += body.amount
    tx = EconomyTransaction(
        guild_id=guild_id, kind="add", actor_discord_id=body.actor_discord_id,
        from_user_id=None, to_user_id=body.discord_user_id,
        total_earned_user_id=body.discord_user_id, amount=body.amount,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return {"balance": bal.balance, "total_earned": bal.total_earned, "transaction_id": tx.id}


class EconomyRemoveIn(BaseModel):
    discord_user_id: int
    amount: int
    # False (padrão): removido no máximo o saldo disponível, nunca fica
    # negativo. True: subtrai o valor cheio mesmo que fique negativo — o bot
    # usa isso pra "remoção com valor explícito" (punição/empréstimo), igual
    # ao bot legado (remove_user_money).
    allow_negative: bool = False
    actor_discord_id: int


@router.post("/bot/economy/remove/{guild_id}")
def bot_economy_remove(
    guild_id: int,
    body: EconomyRemoveIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    if body.amount <= 0:
        raise HTTPException(400, "amount deve ser positivo")
    bal = _get_or_create_balance(db, guild_id, body.discord_user_id)
    actual = body.amount if body.allow_negative else min(bal.balance, body.amount)
    bal.balance -= actual
    transaction_id = None
    if actual > 0:
        tx = EconomyTransaction(
            guild_id=guild_id, kind="remove", actor_discord_id=body.actor_discord_id,
            from_user_id=body.discord_user_id, to_user_id=None,
            total_earned_user_id=None, amount=actual,
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)
        transaction_id = tx.id
    else:
        db.commit()
    return {"removed": actual, "balance": bal.balance, "transaction_id": transaction_id}


@router.post("/bot/economy/undo/{guild_id}/{transaction_id}")
def bot_economy_undo(
    guild_id: int,
    transaction_id: int,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Reverte uma transação pelo id mostrado no rodapé do embed (/undo do bot)
    — aplica o delta inverso do que foi feito, não um snapshot do saldo (se
    outras transações aconteceram depois, elas continuam valendo)."""
    _require_bot_secret(authorization)
    tx = db.scalar(select(EconomyTransaction).where(
        EconomyTransaction.id == transaction_id, EconomyTransaction.guild_id == guild_id,
    ))
    if tx is None:
        return {"ok": False, "reason": "not_found"}
    if tx.undone:
        return {"ok": False, "reason": "already_undone"}

    if tx.from_user_id is not None:
        _get_or_create_balance(db, guild_id, tx.from_user_id).balance += tx.amount
    if tx.to_user_id is not None:
        _get_or_create_balance(db, guild_id, tx.to_user_id).balance -= tx.amount
    if tx.total_earned_user_id is not None:
        _get_or_create_balance(db, guild_id, tx.total_earned_user_id).total_earned -= tx.amount

    tx.undone = True
    db.commit()
    return {
        "ok": True, "kind": tx.kind, "amount": tx.amount,
        "from_user_id": tx.from_user_id, "to_user_id": tx.to_user_id,
    }


@router.get("/bot/economy/leaderboard/{guild_id}")
def bot_economy_leaderboard(
    guild_id: int,
    limit: int = 10,
    offset: int = 0,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    total = db.scalar(
        select(func.count()).select_from(EconomyBalance).where(EconomyBalance.guild_id == guild_id)
    ) or 0
    rows = db.execute(
        select(EconomyBalance.discord_user_id, EconomyBalance.balance)
        .where(EconomyBalance.guild_id == guild_id)
        .order_by(EconomyBalance.balance.desc(), EconomyBalance.discord_user_id.asc())
        .limit(limit).offset(offset)
    ).all()
    return {"total": total, "rows": [{"discord_user_id": uid, "balance": bal} for uid, bal in rows]}


@router.get("/bot/economy/stats/{guild_id}")
def bot_economy_stats(
    guild_id: int,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    row = db.execute(
        select(func.count(), func.coalesce(func.sum(EconomyBalance.balance), 0))
        .where(EconomyBalance.guild_id == guild_id)
    ).one()
    return {"user_count": row[0], "balances_sum": int(row[1])}


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
