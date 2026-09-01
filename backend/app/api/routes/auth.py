"""Autenticação Discord + gestão de guilda + permissões por cargo."""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.api import deps
from app.auth import discord
from app.auth.crypto import decrypt_token, encrypt_token
from app.auth.permissions import ALL_FALSE, PERMISSION_KEYS, compute_permissions
from app.auth.session import make_session
from app.config import get_settings
from app.models.audit import AuditLog
from app.models.battles import BattleGuild
from app.models.catalog import Weapon
from app.models.economy import EconomyBalance, EconomyTransaction
from app.models.events import Event, EventParticipant, EventSignup
from app.models.nodes import NodeEvent
from app.models.registration import BotRegistration
from app.models.tenancy import Guild, GuildAlbionLink, GuildMember, GuildRolePermission, User
from app.services import economy as economy_svc
from app.services import event_gates as event_gates_svc
from app.services import event_signups as event_signups_svc
from app.services import events as events_svc
from app.services import nodes as nodes_svc
from app.services import comps as comps_svc
from app.services import energy as energy_svc
from app.api.schemas.events import EventCreate
from app.services.events import ServiceError
from app.services.player_tracker import HOSTS, make_client

router = APIRouter(tags=["auth"])

_STATE_COOKIE = "ziggs_oauth_state"
# Pra onde voltar depois do OAuth (deep links tipo /eventos/.../escalacao): evita
# o fallback fixo no raiz do front pra o usuário não perder a página que abriu.
_NEXT_COOKIE = "ziggs_oauth_next"
# Cache de 60s para guilds Discord do usuário — evita rate limit 429
_guilds_cache: dict[int, tuple[list, float]] = {}
_GUILDS_TTL = 60


def _require_bot_secret(authorization: str) -> None:
    """Compara em tempo constante — `!=` normal vaza timing por byte comparado."""
    if not secrets.compare_digest(authorization, f"Bearer {get_settings().bot_api_secret}"):
        raise HTTPException(401)


_WEAPON_NAMES: dict[str, dict[str, str]] = {}


def _init_weapon_names() -> None:
    global _WEAPON_NAMES
    if _WEAPON_NAMES:
        return
    data_dir = Path(__file__).resolve().parents[3] / "data"
    frontend_dir = Path(__file__).resolve().parents[4] / "frontend"
    for lang, path in [
        ("en", frontend_dir / "src" / "data" / "en-names.json"),
        ("pt", data_dir / "pt-items.json"),
        ("es", frontend_dir / "src" / "i18n" / "es-items.json"),
    ]:
        try:
            _WEAPON_NAMES[lang] = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            _WEAPON_NAMES[lang] = {}


_init_weapon_names()


def _weapon_base_id(item_id: str) -> str:
    return re.sub(r"^T\d+_", "", item_id or "")


def _translate_weapon_name(weapon_name: str, weapon_item_id: str, lang: str) -> str:
    if lang == "en":
        return weapon_name
    table = _WEAPON_NAMES.get(lang, {})
    translated = table.get(_weapon_base_id(weapon_item_id or ""))
    if translated:
        return translated
    return weapon_name


# ── OAuth ─────────────────────────────────────────────────────────────────────

def _valid_next(next_url: str | None) -> bool:
    """Relativo same-origin (anti open-redirect): começa com "/" mas não com "//"."""
    return bool(next_url) and next_url.startswith("/") and not next_url.startswith("//")


@router.get("/auth/discord/login")
def discord_login(next: str | None = Query(default=None)):
    state = secrets.token_urlsafe(24)
    resp = RedirectResponse(discord.build_authorize_url(state))
    resp.set_cookie(_STATE_COOKIE, state, max_age=600, httponly=True, samesite="lax")
    if _valid_next(next):
        resp.set_cookie(_NEXT_COOKIE, next, max_age=600, httponly=True, samesite="lax")
    return resp


@router.get("/auth/discord/callback")
def discord_callback(
    code: str = Query(...),
    state: str = Query(...),
    oauth_state: str | None = Cookie(default=None, alias=_STATE_COOKIE),
    oauth_next: str | None = Cookie(default=None, alias=_NEXT_COOKIE),
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
    if profile.get("email"):
        user.email = profile["email"]
        user.email_verified = bool(profile.get("verified"))
    access_token = token.get("access_token")
    user.discord_access_token = encrypt_token(access_token) if access_token else None

    db.add(AuditLog(
        guild_id=0, actor_id=uid, actor_type="site", source="site",
        action="auth.login", entity="user", entity_id=str(uid),
        after={"new": is_new},
    ))
    db.commit()

    s = get_settings()
    redirect_to = oauth_next if _valid_next(oauth_next) else s.frontend_url
    resp = RedirectResponse(redirect_to)
    resp.set_cookie(
        s.session_cookie_name, make_session(uid),
        max_age=s.session_max_age, httponly=True, samesite="lax",
        secure=(s.environment != "development"),
    )
    resp.delete_cookie(_STATE_COOKIE)
    resp.delete_cookie(_NEXT_COOKIE)
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


@router.post("/auth/select-guild")
def select_guild(
    body: SelectGuildIn,
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
):
    gid = int(body.guild_id)
    # Verificação no Discord ANTES de qualquer write — é a fonte de
    # is_admin/role_ids, nunca o body (evitava escalação: cliente mandava
    # is_admin=true e virava admin de qualquer guilda). Chamada pura Discord,
    # sem DB — não segura o write lock do SQLite durante as ~15s da rede.
    name, icon, is_admin, role_ids = deps.verify_guild_membership(user, gid)

    guild = db.scalar(select(Guild).where(Guild.id == gid))
    if guild is None:
        guild = Guild(id=gid, name=name or body.guild_name, icon=icon or body.icon)
        db.add(guild)
    else:
        guild.name = name or body.guild_name
        if icon or body.icon:
            guild.icon = icon or body.icon
    member = db.scalar(select(GuildMember).where(
        GuildMember.guild_id == gid,
        GuildMember.user_id == user.id,
    ))
    if member is None:
        member = GuildMember(guild_id=gid, user_id=user.id)
        db.add(member)
    member.is_guild_admin = is_admin
    if role_ids:
        member.discord_role_ids = role_ids

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
    try:
        name, icon, is_admin, role_ids = deps.verify_guild_membership(user, guild_id)
    except HTTPException as ex:
        if ex.status_code == 403:
            # não é mais membro do server Discord — remove o vínculo local
            db.delete(member)
            db.commit()
        raise  # 502 (Discord inalcançável): propaga, flag/row ficam intactas

    member.is_guild_admin = is_admin
    if role_ids:
        member.discord_role_ids = role_ids
    guild = db.scalar(select(Guild).where(Guild.id == guild_id))
    if guild:
        if name:
            guild.name = name
        if icon:
            guild.icon = icon
    user.current_guild_id = guild_id
    db.commit()
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
    _member: GuildMember = Depends(deps.require_guild_member),
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
        "bank_balance": g.bank_balance,
    }


ALBION_LOOKUP_RETRIES = 3
ALBION_LOOKUP_BACKOFF = 0.6  # segundos, multiplicado pela tentativa (0.6s, 1.2s, ...)


def _pick_best_match(matches: list[dict], owned_ids: set[str], alliance_id: str | None) -> dict:
    """Entre vários personagens com o mesmo nome (case-insensitive), prefere o
    que está na guilda configurada (owned_ids) ou na aliança. Caso contrário,
    devolve o primeiro — mesmo comportamento do `next()` antigo.

    O search do Albion pode devolver um personagem DELETADO com o mesmo nome de
    um ativo (ex.: "Xmonkkeyx" deletado + "xMonkkeyx" ativo na SIGHT). Sem
    desambiguar, o .lower() pegava o deletado (GuildId vazio → "not_in_guild"
    injusto). Aqui priorizamos quem tem GuildId/AllianceId reais que batem com
    a guilda/aliança configurada."""
    if not matches:
        return {}  # caller já checa; placeholder pra typing
    # 1. Membro direto de uma das guildas próprias.
    for p in matches:
        gid = str(p.get("GuildId") or "")
        if gid and gid in owned_ids:
            return p
    # 2. Membro da aliança configurada.
    if alliance_id:
        for p in matches:
            aid = str(p.get("AllianceId") or "") or None
            if aid and aid == alliance_id:
                return p
    # 3. Qualquer um que tenha guilda (provavelmente ativo) — evita o deletado
    #    sem guilda quando há um ativo com guilda desconhecida.
    for p in matches:
        gid = str(p.get("GuildId") or "")
        if gid:
            return p
    # 4. Fallback: primeiro da lista (comportamento original).
    return matches[0]


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
    # User-facing (configuração de guilda no dashboard) — não passa pelo
    # albion_gate (rate limiter compartilhado com background).
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
    events_channel_id: str | None = None
    # Sala onde o bot-v2 cria uma thread por evento ao entrar em review e posta
    # o embed 📑 EVENTO #N dentro dela. Null = cai no events_channel_id como
    # mensagem simples (legacy). Ver cogs/event_embeds.py.
    event_review_channel_id: str | None = None
    # Canal dedicado onde o bot-v2 cria uma thread de regear por evento ao
    # entrar em IN_PROGRESS. Prints postadas na thread viram RegearRequests
    # atrelados ao evento (landmark). Null = sem thread automática (regears
    # soltos caem na fila geral sem vínculo). Ver cogs/regear_threads.py.
    regear_thread_channel_id: str | None = None
    # Canal dedicado onde o bot-v2 cria uma thread de lootlog por evento ao
    # entrar em IN_PROGRESS. .csv do lootlogger postado na thread vira
    # LootLogSubmission atrelado ao evento (resolve por lootlog_thread_id).
    # Null = sem thread automática. Espelho do regear_thread_channel_id.
    lootlog_thread_channel_id: str | None = None
    # {weapon_id: [discord_role_id, ...]} — a arma canônica ignora tier e enchant.
    event_weapon_gates: dict[str, list[str]] | None = None
    # Mínimo de roles que um inscrito deve escolher. Toda role conta e não
    # existe limite máximo.
    signup_min_builds: int | None = None
    # Canal onde o bot-v2 posta o calendário de nodes (embed persistente).
    nodes_calendar_channel_id: str | None = None
    # Canal de voz "CTA" — o snapshot loop do bot-v2 mede presença aqui p/ o
    # modo participation_mode=voice_percent (ver app/services/nodes_voice.py).
    voice_cta_channel_id: str | None = None
    # Desconto % aplicado ao base_percent de trials no freeze do evento (0..100).
    trial_percent: int | None = None
    # Role do Discord que marca um membro como trial — o bot-v2 lê isso no voice
    # snapshot (cogs/voice_presence.py) e seta is_trial=true em quem tem a role.
    trial_role_id: str | None = None
    # Todo evento SEMPRE calcula regear; este setting só decide o lootsplit.
    # "none" = sem lootsplit (regear sai do banco da guilda, igual sempre foi).
    # "leftover" = regear sai da PRÓPRIA tab primeiro; o que sobrar é o split
    # (rombo negativo zera, ninguém cobre). "full" = tab inteira (menos corte
    # de logger) vira split; regear é custo à parte do banco (era o antigo
    # tipo lootsplit_regear). "guild_backed" = igual "leftover", mas rombo
    # negativo é descontado igualmente do saldo (EconomyBalance) de TODO
    # membro da guilda. Ver events.LOOTSPLIT_MODES/_calc_payout/_finalize_payouts.
    lootsplit_mode: str | None = None
    # % da tab debitada pro banco da guilda ANTES do pool de participantes
    # (0-100, default 0 = sem taxa). Só vale em modos com split; "none" ignora.
    guild_tax_percent: int | None = None
    # De onde vem o bônus do scout (NodeDef.weight). "node" (default) = peso ×
    # sold_value do node, pool separado financiado pelo que o node vendeu.
    # "tab" = peso × tab_value (% da tab), deduzido da participant pool — o
    # scout come do bolso dos participantes (igual ao logger pool). Ver
    # events.get_scout_bonus_source / _calc_payout.
    scout_bonus_source: str | None = None
    # Momentos em que o mass-info do bot deleta a embed e reenvia com @everyone.
    # Subconjunto de {created, t10min, in_progress, review}; default (chave
    # ausente) = os 3 primeiros. [] = tudo off (status triggers ainda bumpam
    # silenciosamente, só sem @everyone — ver event_signups._enqueue_ping).
    events_ping_triggers: list[str] | None = None
    # Canal de logs do bot (retransmissão do AuditLog). Default (null) = o bot
    # cria e mantém um canal próprio admin-only "logs-bot" (ver cogs/audit_log.py
    # ensure_logs_channel). Setar um canal aqui faz o bot usar esse em vez do
    # auto-criado — útil pra centralizar logs num canal existente da guilda.
    logs_channel_id: str | None = None
    # Feature era sempre-ativa (sem toggle) — default (chave ausente) = True,
    # preserva o comportamento de guildas existentes. False faz o bot parar de
    # criar/manter o canal de logs e de postar (ver cogs/audit_log.py).
    bot_logs_enabled: bool | None = None
    # Canal onde o bot-v2 posta novas batalhas detectadas pelo battle_tracker
    # (link + imagem de resumo). Ver cogs/battle_feed.py.
    battle_feed_channel_id: str | None = None
    # Mínimo de jogadores numa batalha pra ser postada no feed (filtros por
    # guilda — default 10). Batalhas menores que isso são ignoradas.
    battle_feed_min_players: int | None = None
    # ── Registro (/register): quando True (default), o /register exige que o
    # personagem esteja na guilda Albion configurada e o registration_checker
    # vigia os registros ativos, removendo o cargo de quem sair. Quando False,
    # a checagem de guilda só acontece no /register que o próprio usuário faz
    # (self-register); registrar um terceiro assume a identidade sem verificar
    # e ninguém é vigiado — útil pra guildas que usam o cargo só como tag.
    register_remove_role_on_leave: bool | None = None
    # ── Juicy kills: kills com silver_dropped >= min postadas numa sala do
    # Discord. O admin escolhe a sala, os servidores (regiões) pra monitorar,
    # e o threshold de prata (default 50M) e/ou fama. O worker precifica
    # player_kill_events em background (silver_dropped); o bot-v2 faz poll no
    # /bot/guilds/{id}/juicy-kill/queue e posta os que cruzam o threshold.
    juicy_kill_channel_id: str | None = None
    juicy_kill_min_silver: int | None = None       # default 50_000_000
    juicy_kill_min_fame: int | None = None         # 0 = não filtra por fama
    juicy_kill_regions: list[str] | None = None     # [] ou null = todas
    # ── Energy Control: embed constante de saldos negativos (como mass-info).
    # O bot mantém um embed edit in-place listando jogadores com saldo < threshold.
    # Null = feature desligada. Ver cogs/energy_control.py.
    energy_control_channel_id: str | None = None


@router.patch("/auth/guild-settings/{guild_id}")
async def update_guild_settings(
    guild_id: int,
    body: GuildSettingsIn,
    user: User = Depends(deps.require_user),
    db: AsyncSession = Depends(deps.async_db_session),
):
    await _require_admin_async(db, user, guild_id)
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        raise HTTPException(404)

    settings = dict(g.settings or {})
    had_juicy_kill_channel = bool(settings.get("juicy_kill_channel_id"))
    # Libera read tx antes do HTTP (_lookup_albion_guild chama a API do Albion).
    # expire_on_commit=False: g permanece válido após commit.
    await db.commit()
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
    if "register_remove_role_on_leave" in body.model_fields_set:
        was_on = bool(settings.get("register_remove_role_on_leave", True))
        if body.register_remove_role_on_leave is None:
            settings.pop("register_remove_role_on_leave", None)  # volta ao default (True)
        else:
            settings["register_remove_role_on_leave"] = body.register_remove_role_on_leave
        # Religar a vigilância (False→True) agenda a verificação retroativa dos
        # registros "de confiança" criados enquanto ela estava desligada — o
        # registration_checker resolve cada nick na API do Albion: encontrado →
        # registro vira real (ID verdadeiro) e a membresia é checada na hora;
        # não encontrado após algumas tentativas → perde registro e cargo.
        if not was_on and bool(settings.get("register_remove_role_on_leave", True)):
            settings["register_verify_pending"] = True
    if "bot_language" in body.model_fields_set:
        if body.bot_language in BOT_LANGUAGES:
            settings["bot_language"] = body.bot_language
        else:
            settings.pop("bot_language", None)
    if "events_channel_id" in body.model_fields_set:
        if body.events_channel_id:
            settings["events_channel_id"] = body.events_channel_id
        else:
            settings.pop("events_channel_id", None)
    if "event_review_channel_id" in body.model_fields_set:
        if body.event_review_channel_id:
            settings["event_review_channel_id"] = body.event_review_channel_id
        else:
            settings.pop("event_review_channel_id", None)
    if "regear_thread_channel_id" in body.model_fields_set:
        if body.regear_thread_channel_id:
            settings["regear_thread_channel_id"] = body.regear_thread_channel_id
        else:
            settings.pop("regear_thread_channel_id", None)
    if "lootlog_thread_channel_id" in body.model_fields_set:
        if body.lootlog_thread_channel_id:
            settings["lootlog_thread_channel_id"] = body.lootlog_thread_channel_id
        else:
            settings.pop("lootlog_thread_channel_id", None)
    if "event_weapon_gates" in body.model_fields_set:
        if body.event_weapon_gates:
            settings["event_weapon_gates"] = body.event_weapon_gates
        else:
            settings.pop("event_weapon_gates", None)
    if "signup_min_builds" in body.model_fields_set:
        if body.signup_min_builds and body.signup_min_builds > 0:
            settings["signup_min_builds"] = body.signup_min_builds
        else:
            settings.pop("signup_min_builds", None)
    settings.pop("signup_max_builds", None)
    if "nodes_calendar_channel_id" in body.model_fields_set:
        if body.nodes_calendar_channel_id:
            settings["nodes_calendar_channel_id"] = body.nodes_calendar_channel_id
        else:
            settings.pop("nodes_calendar_channel_id", None)
    if "voice_cta_channel_id" in body.model_fields_set:
        if body.voice_cta_channel_id:
            settings["voice_cta_channel_id"] = body.voice_cta_channel_id
        else:
            settings.pop("voice_cta_channel_id", None)
    if "trial_percent" in body.model_fields_set:
        if body.trial_percent is not None and 0 <= body.trial_percent <= 100:
            settings["trial_percent"] = body.trial_percent
        else:
            settings.pop("trial_percent", None)
    if "trial_role_id" in body.model_fields_set:
        if body.trial_role_id:
            settings["trial_role_id"] = body.trial_role_id
        else:
            settings.pop("trial_role_id", None)
    if "lootsplit_mode" in body.model_fields_set:
        if body.lootsplit_mode in events_svc.LOOTSPLIT_MODES:
            settings["lootsplit_mode"] = body.lootsplit_mode
        else:
            settings.pop("lootsplit_mode", None)
    if "guild_tax_percent" in body.model_fields_set:
        if body.guild_tax_percent is not None and 0 <= body.guild_tax_percent <= 100:
            settings["guild_tax_percent"] = body.guild_tax_percent
        else:
            settings.pop("guild_tax_percent", None)
    if "scout_bonus_source" in body.model_fields_set:
        if body.scout_bonus_source in events_svc.SCOUT_BONUS_SOURCES:
            settings["scout_bonus_source"] = body.scout_bonus_source
        else:
            settings.pop("scout_bonus_source", None)
    if "events_ping_triggers" in body.model_fields_set:
        valid = [t for t in (body.events_ping_triggers or []) if t in event_signups_svc.ALL_PING_TRIGGERS]
        # [] explícito = admin desligou tudo (fica gravado, distinto de "nunca
        # configurado" = default). Preserva ordem/dedup pra a UI bater com o banco.
        if body.events_ping_triggers is None:
            settings.pop("events_ping_triggers", None)
        else:
            seen: list[str] = []
            for t in valid:
                if t not in seen:
                    seen.append(t)
            settings["events_ping_triggers"] = seen
    if "logs_channel_id" in body.model_fields_set:
        if body.logs_channel_id:
            settings["logs_channel_id"] = body.logs_channel_id
            # Inicializa o cursor de AuditLog só se ainda não existir — senão
            # trocar de canal despejaria todo o histórico acumulado no canal
            # novo (desde o id 0). Mesma lógica do /bot/guilds/.../logs-channel
            # que o bot chama ao auto-criar. Cursor já setado = continua de
            # onde parou, sem gap nem dump.
            if "logs_last_sent_id" not in settings:
                max_id = await db.scalar(select(func.max(AuditLog.id)).where(AuditLog.guild_id == guild_id)) or 0
                settings["logs_last_sent_id"] = max_id
        else:
            # null = volta pro auto-create do bot (canal logs-bot admin-only).
            # Mantém o cursor p/ não re-despejar histórico quando ele recriar.
            settings.pop("logs_channel_id", None)
    if "bot_logs_enabled" in body.model_fields_set:
        settings["bot_logs_enabled"] = bool(body.bot_logs_enabled)
    if "battle_feed_channel_id" in body.model_fields_set:
        if body.battle_feed_channel_id:
            settings["battle_feed_channel_id"] = body.battle_feed_channel_id
            if "battle_feed_last_ts" not in settings:
                # Inicializa watermark no maior start_time existente — senão
                # trocar de canal despejaria histórico no canal novo. Backfill
                # de settings legados: se existia battle_feed_last_id, converte
                # pra timestamp (max start_time naquele id) uma única vez.
                from app.models.battles import Battle
                legacy_id = settings.pop("battle_feed_last_id", None)
                if legacy_id:
                    ts = await db.scalar(
                        select(func.max(Battle.start_time)).where(Battle.id <= legacy_id)
                    ) or datetime.now(timezone.utc)
                else:
                    ts = await db.scalar(select(func.max(Battle.start_time))) or datetime.now(timezone.utc)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                settings["battle_feed_last_ts"] = ts.isoformat()
        else:
            settings.pop("battle_feed_channel_id", None)
    if "battle_feed_min_players" in body.model_fields_set:
        if body.battle_feed_min_players and body.battle_feed_min_players > 0:
            settings["battle_feed_min_players"] = body.battle_feed_min_players
        else:
            settings.pop("battle_feed_min_players", None)
    if "juicy_kill_channel_id" in body.model_fields_set:
        if body.juicy_kill_channel_id:
            settings["juicy_kill_channel_id"] = body.juicy_kill_channel_id
            now = datetime.now(timezone.utc)
            # Uma ativação (inclusive após desligar) começa no presente. Não
            # despeja pendências acumuladas enquanto o canal estava desligado.
            wm_raw = dict(settings.get("juicy_kill_last_ts_by_region") or {})
            delivery_from = dict(settings.get("juicy_kill_delivery_from_by_region") or {})
            regs = settings.get("juicy_kill_regions") or list(HOSTS.keys())
            for r in regs:
                if r not in HOSTS:
                    continue
                if not had_juicy_kill_channel or r not in wm_raw:
                    wm_raw[r] = now.isoformat()
                if not had_juicy_kill_channel or r not in delivery_from:
                    delivery_from[r] = now.isoformat()
            if wm_raw:
                settings["juicy_kill_last_ts_by_region"] = wm_raw
            if delivery_from:
                settings["juicy_kill_delivery_from_by_region"] = delivery_from
            # Watermark global legado = agora também (compat)
            if "juicy_kill_last_ts" not in settings:
                settings["juicy_kill_last_ts"] = now.isoformat()
        else:
            settings.pop("juicy_kill_channel_id", None)
    if "juicy_kill_min_silver" in body.model_fields_set:
        if body.juicy_kill_min_silver and body.juicy_kill_min_silver > 0:
            settings["juicy_kill_min_silver"] = body.juicy_kill_min_silver
        else:
            settings.pop("juicy_kill_min_silver", None)
    if "juicy_kill_min_fame" in body.model_fields_set:
        if body.juicy_kill_min_fame and body.juicy_kill_min_fame > 0:
            settings["juicy_kill_min_fame"] = body.juicy_kill_min_fame
        else:
            settings.pop("juicy_kill_min_fame", None)
    if "juicy_kill_regions" in body.model_fields_set:
        regs = [r for r in (body.juicy_kill_regions or []) if r in HOSTS]
        settings["juicy_kill_regions"] = regs  # [] = todas
        # Inicializa watermark das regiões recém-adicionadas = agora.
        # Regiões que já tinham watermark mantêm o seu (não resetam).
        if regs:
            now = datetime.now(timezone.utc)
            wm_raw = dict(settings.get("juicy_kill_last_ts_by_region") or {})
            delivery_from = dict(settings.get("juicy_kill_delivery_from_by_region") or {})
            for r in regs:
                if r in HOSTS and r not in wm_raw:
                    wm_raw[r] = now.isoformat()
                if r in HOSTS and r not in delivery_from:
                    delivery_from[r] = now.isoformat()
            settings["juicy_kill_last_ts_by_region"] = wm_raw
            settings["juicy_kill_delivery_from_by_region"] = delivery_from
    if "energy_control_channel_id" in body.model_fields_set:
        if body.energy_control_channel_id:
            settings["energy_control_channel_id"] = body.energy_control_channel_id
            settings["energy_control_dirty"] = True
        else:
            settings.pop("energy_control_channel_id", None)
            settings["energy_control_dirty"] = True
    if any(name.startswith("juicy_kill_") for name in body.model_fields_set):
        from app.services.juicy_kill_delivery import suppress_incompatible_pending
        await suppress_incompatible_pending(db, guild_id, settings)
    g.settings = settings

    await db.commit()
    return {"ok": True, "albion_guild_resolved": albion_guild_resolved}


async def _is_guild_in_alliance(guild_id: str, alliance_id: str, region: str | None) -> bool:
    """`battle_guilds` é histórico (1 snapshot por batalha) — uma guilda que já
    saiu da aliança continua lá pra sempre com o alliance_id antigo. Confere o
    estado ATUAL direto na Albion antes de sugerir a guilda como aliada.
    Falha de rede = mantém na lista (fail-open): essa rota só monta a lista de
    sugestões pro admin escolher, quem decide de fato quem pode usar /register
    é o ally_allowed_guilds + o check ao vivo em bot_register/registration_checker."""
    hosts = [HOSTS[region]] if region in HOSTS else list(HOSTS.values())
    # User-facing (dashboard de aliados) — não passa pelo albion_gate.
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
    db: AsyncSession = Depends(deps.async_db_session),
):
    """Guildas da mesma aliança (excluindo as próprias do servidor), pra montar
    a lista de aliados permitidos no /register.

    Fonte principal: `Guild.settings["alliance_members"]`, mantida quente pelo
    `guild_verifier` (worker que roda a cada 15min e consulta a API autoritativa
    `/alliances/{id}`). Antes a lista vinha só de `BattleGuild` (vistas em
    batalhas) + check ao vivo por guilda — ficava vazia em guildas sem
    batalhas rastreadas e era lenta (1 HTTP por candidata).

    Fallback pra `BattleGuild` só se `alliance_members` ainda não foi populada
    (guilda recém-linkada antes do 1º ciclo do guild_verifier). Nesse caso usa o
    caminho antigo com `_is_guild_in_alliance` pra não mostrar guilda que já
    saiu da aliança."""
    await _require_admin_async(db, user, guild_id)
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        raise HTTPException(404)
    if not g.albion_alliance_id:
        return []
    from app.services.guild_links import async_albion_guild_ids
    owned_ids = set(await async_albion_guild_ids(db, guild_id))

    # Fonte principal: alliance_members (guild_verifier já populou).
    members = ((g.settings or {}).get("alliance_members") or [])
    if members:
        return [
            {"id": str(m["id"]), "name": m.get("name") or str(m["id"])}
            for m in members
            if str(m.get("id")) not in owned_ids
        ]

    # Fallback: 1 request à API de alianças em vez de N requests por guilda.
    # Acontece só quando o cache está frio (guilda recém-linkada antes do 1º
    # ciclo do guild_verifier, ou falha transiente que não achou membros).
    region = (g.settings or {}).get("albion_guild_region")
    alliance_id = g.albion_alliance_id
    host = HOSTS.get(region) if region in HOSTS else None
    hosts_to_try = [host] if host else list(HOSTS.values())
    await db.commit()
    fetched: list[dict] = []
    async with make_client() as client:
        for h in hosts_to_try:
            try:
                resp = await client.get(f"https://{h}/api/gameinfo/alliances/{alliance_id}")
                if resp.status_code != 200:
                    continue
                raw = resp.json()
                if not isinstance(raw, dict):
                    continue
                gs = raw.get("Guilds") or raw.get("guilds") or []
                for gm in gs:
                    if isinstance(gm, dict) and gm.get("Id") and str(gm["Id"]) not in owned_ids:
                        fetched.append({"id": str(gm["Id"]), "name": gm.get("Name") or str(gm["Id"])})
                break
            except httpx.HTTPError:
                continue
    return fetched


# ── Guildas de Albion vinculadas (multi-guilda por Discord) ───────────────────

@router.get("/auth/guilds/{guild_id}/albion-links")
async def list_albion_links(
    guild_id: int,
    user: User = Depends(deps.require_user),
    db: AsyncSession = Depends(deps.async_db_session),
):
    await _require_admin_async(db, user, guild_id)
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
    links = (await db.scalars(
        select(GuildAlbionLink).where(GuildAlbionLink.guild_id == guild_id)
        .order_by(GuildAlbionLink.id.asc())
    )).all()
    primary_id = str(g.albion_guild_id) if g and g.albion_guild_id else None
    primary_name = g.albion_guild_name if g else None
    primary_region = (g.settings or {}).get("albion_guild_region", "americas") if g else "americas"
    raw_gv = (g.settings or {}).get("guild_verified") if g else None
    guild_verified: bool | None = None if raw_gv is None else bool(raw_gv)
    all_links = []
    if primary_id:
        all_links.append({
            "albion_guild_id": primary_id,
            "albion_guild_name": primary_name,
            "region": primary_region,
            "alliance_id": g.albion_alliance_id if g else None,
            "alliance_name": g.albion_alliance_name if g else None,
            "is_primary": True,
            "verified": guild_verified,
        })
    for l in links:
        if primary_id and l.albion_guild_id == primary_id:
            continue  # dedup: backfill may have created a link matching the primary
        all_links.append({
            "albion_guild_id": l.albion_guild_id,
            "albion_guild_name": l.albion_guild_name,
            "region": l.region,
            "alliance_id": l.alliance_id,
            "alliance_name": l.alliance_name,
            "is_primary": False,
            "verified": l.verified,
        })
    return {
        "primary": primary_id,
        "primary_name": primary_name,
        "guild_verified": guild_verified,
        "links": all_links,
    }


class AlbionLinkIn(BaseModel):
    name: str
    region: str


@router.post("/auth/guilds/{guild_id}/albion-links")
async def add_albion_link(
    guild_id: int,
    body: AlbionLinkIn,
    user: User = Depends(deps.require_user),
    db: AsyncSession = Depends(deps.async_db_session),
):
    await _require_admin_async(db, user, guild_id)
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        raise HTTPException(404)
    region = body.region if body.region in HOSTS else (g.settings or {}).get("albion_guild_region") or "americas"
    gname = body.name.strip()
    agid = f"manual:{gname.lower()}"
    if not g.albion_guild_id:
        g.albion_guild_id = agid
        g.albion_guild_name = gname
        g.albion_alliance_id = None
        g.albion_alliance_name = None
        if g.settings is None:
            g.settings = {}
        g.settings["albion_guild_region"] = region
        await db.commit()
        return {"ok": True, "albion_guild_id": agid, "primary": True}
    if agid == str(g.albion_guild_id):
        raise HTTPException(409, "essa já é a guilda primária")
    existing = await db.scalar(select(GuildAlbionLink).where(
        GuildAlbionLink.guild_id == guild_id,
        GuildAlbionLink.albion_guild_id == agid,
    ))
    if existing:
        existing.albion_guild_name = gname
        await db.commit()
        return {"ok": True, "albion_guild_id": agid}
    link = GuildAlbionLink(
        guild_id=guild_id,
        albion_guild_id=agid,
        albion_guild_name=gname,
        region=region,
        alliance_id=None,
        alliance_name=None,
    )
    db.add(link)
    await db.commit()
    return {"ok": True, "albion_guild_id": agid}


@router.delete("/auth/guilds/{guild_id}/albion-links/{albion_guild_id}")
async def remove_albion_link(
    guild_id: int,
    albion_guild_id: str,
    user: User = Depends(deps.require_user),
    db: AsyncSession = Depends(deps.async_db_session),
):
    await _require_admin_async(db, user, guild_id)
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        raise HTTPException(404)
    is_primary = g.albion_guild_id and albion_guild_id == str(g.albion_guild_id)
    if is_primary:
        remaining = (await db.scalars(
            select(GuildAlbionLink).where(GuildAlbionLink.guild_id == guild_id)
        )).all()
        if remaining:
            raise HTTPException(409, "remova as guildas secundárias antes da primária")
        g.albion_guild_id = None
        g.albion_guild_name = None
        g.albion_alliance_id = None
        g.albion_alliance_name = None
        s = g.settings or {}
        for k in ("events_channel_id", "event_review_channel_id", "regear_thread_channel_id",
                  "lootlog_thread_channel_id", "nodes_calendar_channel_id", "voice_cta_channel_id",
                  "battle_feed_channel_id", "juicy_kill_channel_id", "energy_control_channel_id",
                  "register_role_id", "ally_role_id", "ally_allowed_guilds", "lootsplit_mode",
                  "guild_tax_percent", "scout_bonus_source", "albion_guild_region"):
            s.pop(k, None)
        g.settings = s
        await db.commit()
        return {"ok": True}
    link = await db.scalar(select(GuildAlbionLink).where(
        GuildAlbionLink.guild_id == guild_id,
        GuildAlbionLink.albion_guild_id == albion_guild_id,
    ))
    if link is None:
        raise HTTPException(404)
    await db.delete(link)
    await db.commit()
    return {"ok": True}


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
async def update_role_permissions(
    guild_id: int,
    role_id: int,
    body: RolePermissionsIn,
    user: User = Depends(deps.require_user),
    db: AsyncSession = Depends(deps.async_db_session),
):
    await _require_admin_async(db, user, guild_id)
    rp = await db.scalar(select(GuildRolePermission).where(
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
    await db.commit()
    return {"ok": True}


# Tipos de canal do Discord que aceitam mensagem de texto de bot (text=0,
# announcement=5) — categorias/voz/etc. não servem pra postar o mass-info.
_TEXT_CHANNEL_TYPES = {0, 5}
# Canais de voz (voice=2, stage=13) — pra escolher onde o bot entra dar o CTA
# de evento em voz.
_VOICE_CHANNEL_TYPES = {2, 13}


@router.get("/auth/guild-discord-channels/{guild_id}")
def guild_discord_channels(
    guild_id: int,
    voice: bool = False,
    user: User = Depends(deps.require_user),
    db: Session = Depends(deps.db_session),
):
    """Lista canais do servidor Discord. Por padrão só os de texto (mass-info
    de eventos/nodes). `voice=true` retorna só os de voz (CTA em voz do evento)."""
    _require_admin(db, user, guild_id)
    s = get_settings()
    if not s.discord_bot_token:
        raise HTTPException(503, "bot token não configurado")
    try:
        channels = discord.fetch_guild_channels(str(guild_id), s.discord_bot_token)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            raise HTTPException(502, "Token do bot inválido. Redefina o token no Discord Developer Portal e atualize o .env do backend.")
        raise HTTPException(502, f"Discord retornou {e.response.status_code}")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Erro de conexão com Discord: {e}")

    allowed = _VOICE_CHANNEL_TYPES if voice else _TEXT_CHANNEL_TYPES
    return [
        {"id": c["id"], "name": c["name"], "position": c.get("position", 0)}
        for c in channels
        if c.get("type") in allowed
    ]


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
    {"name": "guildbank", "description": "Mostra o saldo atual do banco da guilda", "category": "economy"},
    {"name": "addguildmoney", "description": "Adiciona prata ao banco da guilda", "category": "economy"},
    {"name": "removeguildmoney", "description": "Remove prata do banco da guilda", "category": "economy"},
    {"name": "event", "description": "Gerencia eventos (CTAs): criar, deletar, editar e adiar", "category": "management"},
    {"name": "profile", "description": "Mostra o perfil de um jogador do Albion (fama PvP/PvE, guilda, saldo e attendance)", "category": "miscellaneous"},
    {"name": "attendance", "description": "Mostra estatísticas de participação em eventos CTA", "category": "management"},
    {"name": "lowattendance", "description": "Lista membros com menor participação nos últimos 7 dias", "category": "management"},
    {"name": "bypass", "description": "Remove um usuário do anúncio recorrente de não-registrados com acesso ao mass-info", "category": "management"},
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
    "guildbank": ["admin"],
    "addguildmoney": ["admin"],
    "removeguildmoney": ["admin"],
    "event": ["admin"],
    "lowattendance": ["admin"],
    "bypass": ["admin"],
}

# "register_others" não é um comando próprio — é uma sub-permissão do
# /register (quem pode usar o comando pra vincular OUTRA pessoa, além de se
# auto-registrar). Reaproveita o mesmo mecanismo de allowed_roles/command_roles
# dos comandos de verdade, então precisa ser aceita aqui mesmo sem estar no
# COMMANDS_REGISTRY (que é só pra comandos reais, com toggle de ativar/desativar).
EXTRA_PERMISSION_KEYS = {"register_others"}


@router.get("/auth/guild-commands/{guild_id}")
async def guild_commands(
    guild_id: int,
    user: User = Depends(deps.require_user),
    db: AsyncSession = Depends(deps.async_db_session),
):
    await _require_admin_async(db, user, guild_id)
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
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
async def toggle_guild_command(
    guild_id: int,
    command_name: str,
    body: CommandToggleIn,
    user: User = Depends(deps.require_user),
    db: AsyncSession = Depends(deps.async_db_session),
):
    await _require_admin_async(db, user, guild_id)
    if command_name not in {c["name"] for c in COMMANDS_REGISTRY}:
        raise HTTPException(404, "comando desconhecido")
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
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
    await db.commit()
    return {"ok": True}


class CommandRolesIn(BaseModel):
    # cada item é um ID de cargo Discord, ou um dos placeholders "everyone"/"admin".
    role_keys: list[str]


@router.patch("/auth/guild-commands/{guild_id}/{command_name}/roles")
async def update_command_roles(
    guild_id: int,
    command_name: str,
    body: CommandRolesIn,
    user: User = Depends(deps.require_user),
    db: AsyncSession = Depends(deps.async_db_session),
):
    await _require_admin_async(db, user, guild_id)
    if command_name not in {c["name"] for c in COMMANDS_REGISTRY} | EXTRA_PERMISSION_KEYS:
        raise HTTPException(404, "comando desconhecido")
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
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
    await db.commit()
    return {"ok": True}


@router.get("/bot/guild-commands/{guild_id}")
async def bot_guild_commands(
    guild_id: int,
    authorization: str = Header(...),
    db: AsyncSession = Depends(deps.async_db_session),
):
    _require_bot_secret(authorization)
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
    settings = (g.settings or {}) if g else {}
    command_roles = dict(settings.get("command_roles", {}))
    for name, default in DEFAULT_ALLOWED_ROLES.items():
        command_roles.setdefault(name, default)
    return {
        "disabled": settings.get("disabled_commands", []),
        "register_role_id": settings.get("register_role_id"),
        "command_roles": command_roles,
        "language": settings.get("bot_language") or "pt",
        "events_channel_id": settings.get("events_channel_id"),
        "event_review_channel_id": settings.get("event_review_channel_id"),
        "regear_thread_channel_id": settings.get("regear_thread_channel_id"),
        "lootlog_thread_channel_id": settings.get("lootlog_thread_channel_id"),
        "event_weapon_gates": settings.get("event_weapon_gates", {}),
        "massinfo_message_id": settings.get("massinfo_message_id"),
        "nodes_calendar_channel_id": settings.get("nodes_calendar_channel_id"),
        "voice_cta_channel_id": settings.get("voice_cta_channel_id"),
        "trial_percent": settings.get("trial_percent"),
        "trial_role_id": settings.get("trial_role_id"),
        "logs_channel_id": settings.get("logs_channel_id"),
        "bot_logs_enabled": settings.get("bot_logs_enabled", True),
        "battle_feed_channel_id": settings.get("battle_feed_channel_id"),
        "battle_feed_min_players": settings.get("battle_feed_min_players", 10),
        "juicy_kill_channel_id": settings.get("juicy_kill_channel_id"),
        "juicy_kill_min_silver": max(settings.get("juicy_kill_min_silver", 50_000_000), _JUICY_KILL_HARD_FLOOR),
        "juicy_kill_hard_floor": _JUICY_KILL_HARD_FLOOR,
        "juicy_kill_min_fame": settings.get("juicy_kill_min_fame", 0),
        "juicy_kill_regions": settings.get("juicy_kill_regions", []),
        "energy_control_channel_id": settings.get("energy_control_channel_id"),
        "energy_alert_threshold": settings.get("energy_alert_threshold", 50),
        # Usuários com acesso ao canal mass-info que o bot NÃO deve anunciar como
        # "sem registro" (ver cogs/massinfo_access.py no bot-v2). Lista de IDs
        # Discord em string — o /bypass do bot adiciona/remove aqui.
        "massinfo_access_bypass_user_ids": settings.get("massinfo_access_bypass_user_ids", []),
    }


# ── Bot: canal de logs (retransmissão do AuditLog) ─────────────────────────
#
# Feature sempre ativa (sem toggle no site) — ver GuildConfig.tsx. Se a guilda
# não tiver logs_channel_id, o bot-v2 cria o canal (admin-only) e reporta o id
# aqui. O cursor logs_last_sent_id vive em Guild.settings (mesmo padrão de
# massinfo_message_id) — o site é a fonte da verdade, o bot não guarda estado.

_AUDIT_LOG_BATCH = 25


def _audit_log_dict(row: AuditLog) -> dict:
    return {
        "id": row.id, "actor_id": str(row.actor_id) if row.actor_id else None,
        "actor_type": row.actor_type, "source": row.source, "action": row.action,
        "entity": row.entity, "entity_id": row.entity_id,
        "before": row.before, "after": row.after, "note": row.note,
        "created_at": row.created_at.isoformat(),
    }


def _attach_actor_names(rows: list[AuditLog], user_rows: list[tuple]) -> list[dict]:
    """Console do site: payload cru + `actor_name` (users.global_name ||
    username). Ator que nunca logou no site (comando do bot) fica sem nome —
    a UI mostra só o id; `actor_id` permanece pra rastreabilidade."""
    names = {uid: (global_name or username) for uid, global_name, username in user_rows}
    entries = [_audit_log_dict(row) for row in rows]
    for row, d in zip(rows, entries):
        d["actor_name"] = names.get(row.actor_id) if row.actor_id else None
    return entries


async def _audit_console_entries(db: AsyncSession, rows: list[AuditLog]) -> list[dict]:
    ids = {r.actor_id for r in rows if r.actor_id}
    user_rows = (await db.execute(
        select(User.id, User.global_name, User.username).where(User.id.in_(ids))
    )).all() if ids else []
    return _attach_actor_names(rows, user_rows)


class LogsChannelIn(BaseModel):
    channel_id: str


class BotChannelUnavailableIn(BaseModel):
    setting: str
    channel_id: str


@router.post("/bot/guilds/{guild_id}/channel-unavailable")
async def bot_clear_unavailable_channel(
    guild_id: int, body: BotChannelUnavailableIn,
    authorization: str = Header(...), db: AsyncSession = Depends(deps.async_db_session),
):
    _require_bot_secret(authorization)
    if body.setting not in {"events_channel_id", "event_review_channel_id", "regear_thread_channel_id", "lootlog_thread_channel_id", "nodes_calendar_channel_id", "voice_cta_channel_id", "juicy_kill_channel_id", "battle_feed_channel_id", "energy_control_channel_id", "logs_channel_id"}:
        raise HTTPException(400, "configuração de canal inválida")
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        raise HTTPException(404)
    settings = dict(g.settings or {})
    if str(settings.get(body.setting) or "") == body.channel_id:
        settings.pop(body.setting, None)
        g.settings = settings
        await db.commit()
    return {"ok": True}


@router.post("/bot/guilds/{guild_id}/logs-channel")
async def bot_set_logs_channel(
    guild_id: int, body: LogsChannelIn,
    authorization: str = Header(...), db: AsyncSession = Depends(deps.async_db_session),
):
    """Chamado uma vez pelo bot logo após criar o canal. Inicializa o cursor no
    id máximo ATUAL do AuditLog da guilda — sem isso, a ativação despejaria todo
    o histórico acumulado (inclusive de antes da feature existir) no canal novo."""
    _require_bot_secret(authorization)
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        raise HTTPException(404)
    settings = dict(g.settings or {})
    # O bot pode chegar aqui com cache anterior à troca manual de canal. A
    # configuração salva pelo admin é autoritativa e não pode ser sobrescrita.
    if settings.get("logs_channel_id") and settings["logs_channel_id"] != body.channel_id:
        return {"ok": True}
    max_id = await db.scalar(select(func.max(AuditLog.id)).where(AuditLog.guild_id == guild_id)) or 0
    settings["logs_channel_id"] = body.channel_id
    settings.setdefault("logs_last_sent_id", max_id)
    g.settings = settings
    await db.commit()
    return {"ok": True}


class MassinfoAccessBypassIn(BaseModel):
    """`action=add` adiciona `user_id` à lista de bypass; `remove` retira.
    `user_id` em string (snowflake Discord, mesmo formato de BotRegistration)."""
    action: str  # "add" | "remove"
    user_id: str


@router.post("/bot/guilds/{guild_id}/massinfo-access/bypass")
async def bot_set_massinfo_access_bypass(
    guild_id: int, body: MassinfoAccessBypassIn,
    authorization: str = Header(...), db: AsyncSession = Depends(deps.async_db_session),
):
    """Persiste a decisão do /bypass do bot: usuários com acesso ao canal
    mass-info que NÃO devem ser anunciados como "sem registro" pelo loop de
    verificação (ver cogs/massinfo_access.py no bot-v2). Idempotente."""
    _require_bot_secret(authorization)
    if body.action not in ("add", "remove"):
        raise HTTPException(400, "action deve ser 'add' ou 'remove'")
    if not body.user_id.isdigit():
        raise HTTPException(400, "user_id deve ser um snowflake numérico")
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        raise HTTPException(404)
    settings = dict(g.settings or {})
    ids = set(settings.get("massinfo_access_bypass_user_ids", []))
    if body.action == "add":
        ids.add(body.user_id)
    else:
        ids.discard(body.user_id)
    settings["massinfo_access_bypass_user_ids"] = sorted(ids)
    g.settings = settings
    db.add(AuditLog(
        guild_id=guild_id, actor_id=int(body.user_id),
        actor_type="bot", source="bot",
        action="massinfo.bypass_" + body.action, entity="guild", entity_id=str(guild_id),
        note=f"user_id={body.user_id}",
    ))
    await db.commit()
    return {"ok": True, "bypass_user_ids": settings["massinfo_access_bypass_user_ids"]}


@router.get("/bot/guilds/{guild_id}/audit-log")
async def bot_audit_log_work(
    guild_id: int, authorization: str = Header(...), db: AsyncSession = Depends(deps.async_db_session),
):
    """Próximo lote de AuditLog ainda não retransmitido (cursor em
    Guild.settings.logs_last_sent_id). Bot posta e confirma via
    /audit-log-synced pra avançar o cursor."""
    _require_bot_secret(authorization)
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
    since_id = (g.settings or {}).get("logs_last_sent_id", 0) if g else 0
    # Cutoff de 48h: se o bot ficou offline/wedged por dias, descarta entradas
    # antigas que não fazem mais sentido retransmitir. Avança o cursor
    # automaticamente para o início da janela de 48h.
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    rows = (await db.scalars(
        select(AuditLog)
        .where(
            AuditLog.guild_id == guild_id,
            AuditLog.id > since_id,
            AuditLog.created_at >= cutoff,
        )
        .order_by(AuditLog.id.asc())
        .limit(_AUDIT_LOG_BATCH)
    )).all()
    return {"entries": [_audit_log_dict(row) for row in rows]}


@router.get("/auth/guilds/{guild_id}/audit-log")
async def guild_audit_log(
    guild_id: int,
    before_id: int | None = Query(None, ge=1),
    after_id: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=200),
    user: User = Depends(deps.require_user),
    db: AsyncSession = Depends(deps.async_db_session),
):
    """Console administrativo do mesmo log que o bot retransmite ao Discord."""
    await _require_admin_async(db, user, guild_id)
    stmt = select(AuditLog).where(AuditLog.guild_id == guild_id)
    if after_id is not None:
        rows = (await db.scalars(
            stmt.where(AuditLog.id > after_id).order_by(AuditLog.id.asc()).limit(limit)
        )).all()
        return {"entries": await _audit_console_entries(db, rows), "has_more": False}

    if before_id is not None:
        stmt = stmt.where(AuditLog.id < before_id)
    rows = (await db.scalars(stmt.order_by(AuditLog.id.desc()).limit(limit))).all()
    return {"entries": await _audit_console_entries(db, rows), "has_more": len(rows) == limit}


class AuditLogSyncedIn(BaseModel):
    last_id: int


@router.post("/bot/guilds/{guild_id}/audit-log-synced")
async def bot_audit_log_synced(
    guild_id: int, body: AuditLogSyncedIn,
    authorization: str = Header(...), db: AsyncSession = Depends(deps.async_db_session),
):
    _require_bot_secret(authorization)
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        raise HTTPException(404)
    settings = dict(g.settings or {})
    settings["logs_last_sent_id"] = max(int(settings.get("logs_last_sent_id", 0)), body.last_id)
    g.settings = settings
    await db.commit()
    return {"ok": True}


# ── Bot: battle feed (mensageiro de batalhas) ──────────────────────────────

from app.models.battles import Battle, BattleGuild, BattleParticipant, BattleKillEvent
from app.services import battle_groups


_BATTLE_FEED_BATCH = 10


@router.get("/bot/guilds/{guild_id}/battle-feed")
async def bot_battle_feed(
    guild_id: int, authorization: str = Header(...), db: AsyncSession = Depends(deps.async_db_session),
):
    """Próximo lote de batalhas ainda não postadas no canal de feed da guilda.

    Checkpoint por timestamp (battle_feed_last_ts), NÃO por id interno — mesma
    razão do juicy-kill queue: uma batalha descoberta tardiamente (sweeper,
    backfill, API atrasada) recebe id MAIOR mas start_time MENOR, e no cursor
    por id seria postada fora de ordem cronológica. Por start_time, posta em
    ordem do jogo.

    Filtra por mínimo de jogadores da PRÓPRIA guilda (albion_guild_id com
    >= N participantes) e por região (se configurada). Só retorna batalhas
    deep-processadas (sides analisados) — batalhas "light" não têm
    factions_summary e o embed ficaria vazio.

    Cutoff postable: batalhas com start_time < agora - 48h - avg_api_delay
    não são postadas."""
    _require_bot_secret(authorization)
    from app.services.postable import postable_cutoffs_by_region
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        raise HTTPException(404)
    settings = g.settings or {}
    channel_id = settings.get("battle_feed_channel_id")
    if not channel_id:
        return {"battles": []}
    watermark = _parse_watermark(settings.get("battle_feed_last_ts"))
    min_players = settings.get("battle_feed_min_players", 10)

    # Região configurada ou todas — cutoff por região.
    guild_region = settings.get("albion_guild_region")
    feed_regions = [guild_region] if guild_region and guild_region in HOSTS else list(HOSTS.keys())
    cutoffs = await postable_cutoffs_by_region(db, feed_regions)

    # Só posta batalhas deep-processadas COM kill events — sem isso, o bot
    # posta o link antes do render estar pronto (batalha "light" ou "deep"
    # sem eventos indexados ainda), o Discord crawler busca a página, não
    # encontra og:image e não gera embed. O Discord não refaz o fetch de
    # links já postados, então o embed fica faltando pra sempre.
    deep_with_kills = (
        select(BattleKillEvent.battle_id)
        .where(BattleKillEvent.battle_id == Battle.id)
        .correlate(Battle)
        .limit(1)
    )
    q = select(Battle).where(
        Battle.is_lethal.is_(True),
        Battle.processing_tier == "deep",
        deep_with_kills.exists(),
    )
    if watermark is not None:
        q = q.where(Battle.start_time > watermark)
    # Cutoff por região (OR com cutoff individual)
    from sqlalchemy import or_, and_
    conds = []
    for r in feed_regions:
        if c := cutoffs.get(r):
            conds.append(and_(Battle.region == r, Battle.start_time >= c))
        else:
            conds.append(Battle.region == r)
    if conds:
        q = q.where(or_(*conds))
    if min_players > 0:
        # Filtro pelas guildas CONFIGURADAS daqui (primária + links) — só posta
        # batalhas onde alguma guilda própria teve >= N jogadores.
        from app.services.guild_links import async_albion_guild_ids
        owned_ids = await async_albion_guild_ids(db, guild_id)
        if owned_ids:
            guild_battle_ids = (
                select(BattleParticipant.battle_id)
                .where(BattleParticipant.guild_id.in_(owned_ids))
                .group_by(BattleParticipant.battle_id)
                .having(func.count(BattleParticipant.id) >= min_players)
            )
            q = q.where(Battle.id.in_(guild_battle_ids))

    battles = (await db.scalars(q.order_by(Battle.start_time.asc()).limit(_BATTLE_FEED_BATCH))).all()
    if not battles:
        return {"battles": []}

    # Garante que toda batalha tem um public_id (cria em lote se faltar).
    groups = await battle_groups.get_or_create_groups_bulk(db, [b.id for b in battles])

    from app.api.routes.battles import _factions_summary, _aware
    out = []
    for b in battles:
        out.append({
            "id": b.id,
            "public_id": groups[b.id].public_id,
            "region": b.region,
            "start_time": _aware(b.start_time).isoformat() if b.start_time else None,
            "total_fame": b.total_fame,
            "kill_count": b.kill_count,
            "cluster": b.cluster,
            "players_total": b.players_total,
            "is_zvz": b.is_zvz,
            "factions": await _factions_summary(db, b.id),
        })
    return {"battles": out}


class BattleFeedSyncedIn(BaseModel):
    last_ts: datetime


@router.post("/bot/guilds/{guild_id}/battle-feed-synced")
async def bot_battle_feed_synced(
    guild_id: int, body: BattleFeedSyncedIn,
    authorization: str = Header(...), db: AsyncSession = Depends(deps.async_db_session),
):
    """Avança o watermark após o bot postar as batalhas com sucesso.

    Watermark = start_time da última batalha postada (não do id)."""
    _require_bot_secret(authorization)
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        raise HTTPException(404)
    settings = dict(g.settings or {})
    new_ts = body.last_ts
    if new_ts.tzinfo is None:
        new_ts = new_ts.replace(tzinfo=timezone.utc)
    current = _parse_watermark(settings.get("battle_feed_last_ts"))
    if current is None or new_ts > current:
        settings["battle_feed_last_ts"] = new_ts.isoformat()
    g.settings = settings
    await db.commit()
    return {"ok": True}


# ── Bot: /register ────────────────────────────────────────────────────────

class BotRegisterIn(BaseModel):
    discord_user_id: str
    albion_player_name: str
    # Momento em que o humano acionou /register. Retentativas automáticas usam
    # este mesmo instante, para nunca desfazer uma revogação posterior.
    requested_at: datetime
    # True quando o comando usou o parâmetro `usuario` pra registrar outra
    # pessoa (não o autor da interação). Com a vigilância desligada
    # (register_remove_role_on_leave=False), esse registro é "de confiança":
    # assume a identidade sem NENHUMA consulta à API do Albion e nunca é
    # revalidado pelo registration_checker.
    registering_other: bool = False


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _registration_request_is_superseded(requested_at: datetime, human_revoked_at: datetime | None) -> bool:
    """A remoção humana vence retentativas que começaram antes dela."""
    return human_revoked_at is not None and _as_utc(requested_at) <= _as_utc(human_revoked_at)


def _revoke_registration_by_human(registrations, revoked_at: datetime | None = None) -> None:
    revoked_at = revoked_at or datetime.now(timezone.utc)
    for registration in registrations:
        registration.active = False
        registration.human_revoked_at = revoked_at


def _registration_roles_to_revoke(registrations, removed_role_ids: set[int], retains_massinfo_access: bool):
    if retains_massinfo_access:
        return []
    return [registration for registration in registrations if registration.role_id in removed_role_ids]


@router.post("/bot/register/{guild_id}")
async def bot_register(
    guild_id: int,
    body: BotRegisterIn,
    authorization: str = Header(...),
    db: AsyncSession = Depends(deps.async_db_session),
):
    """Chamado pelo /register do bot. Verifica se o personagem está na guilda
    Albion configurada e devolve o cargo a atribuir — quem efetivamente
    atribui o cargo é o bot (já tem o Member da interação em mãos). Exceção:
    com register_remove_role_on_leave=False + registro de um terceiro
    (registering_other), registra de confiança SEM consultar a API do Albion
    — ID sintético "manual:<nick>", jamais revalidado."""
    _require_bot_secret(authorization)
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        raise HTTPException(404, "servidor não encontrado")
    # Modo confiável: vigilância desligada + alguém registrando um terceiro —
    # assume que aquele usuário é aquele jogador, sem checar guilda alguma.
    trusted = (
        not (g.settings or {}).get("register_remove_role_on_leave", True)
        and body.registering_other
    )
    if not g.albion_guild_id and not trusted:
        return {"ok": False, "reason": "no_albion_guild"}

    name = body.albion_player_name.strip()
    nl = name.lower()
    # Kick, ban e /unregister revogam a conta inteira; qualquer retry anterior,
    # inclusive de outro nick, precisa perder para essa decisão humana.
    latest_human_revocation = await db.scalar(select(BotRegistration.human_revoked_at).where(
        BotRegistration.guild_id == guild_id,
        BotRegistration.discord_user_id == int(body.discord_user_id),
        BotRegistration.human_revoked_at.is_not(None),
    ).order_by(BotRegistration.human_revoked_at.desc()))
    if _registration_request_is_superseded(body.requested_at, latest_human_revocation):
        return {"ok": False, "reason": "human_revoked"}
    # Resposta perdida depois do commit: reaplica o cargo sem depender de uma
    # segunda consulta à API da Albion (que pode estar instável justamente
    # durante a repetição).
    previous = await db.scalar(select(BotRegistration).where(
        BotRegistration.guild_id == guild_id,
        BotRegistration.discord_user_id == int(body.discord_user_id),
        func.lower(BotRegistration.albion_player_name) == nl,
        BotRegistration.active.is_(True),
    ))
    if previous:
        return {
            "ok": True,
            "role_id": str(previous.role_id),
            "albion_player_name": previous.albion_player_name,
        }

    settings = g.settings or {}

    if trusted:
        # Zero consultas à API do Albion: assume a identidade informada. O ID é
        # sintético ("manual:<nick>", minúsculo, estável e único por nick) —
        # nunca foi verificado, então o registration_checker pula esses: não há
        # o que revalidar (a checagem consultaria um ID que não existe na API).
        manual_id = f"manual:{nl}"
        region = settings.get("albion_guild_region")
        if region not in HOSTS:
            region = "americas"
        role_id = settings.get("register_role_id")
        if not role_id:
            return {"ok": False, "reason": "no_role_configured"}
        # Linha do PRÓPRIO usuário apenas (por ID sintético OU nick, case-
        # insensitive — cobre nick registrado antes pelo fluxo real, com ID
        # verdadeiro): reusa em vez de acumular. Mesmo nick num OUTRO Discord
        # é permitido — gente com main+alt no Discord registra o mesmo char
        # duas vezes, cada linha com seu cargo (constraint por user).
        existing = await db.scalar(select(BotRegistration).where(
            BotRegistration.guild_id == guild_id,
            BotRegistration.discord_user_id == int(body.discord_user_id),
            or_(
                BotRegistration.albion_player_id == manual_id,
                func.lower(BotRegistration.albion_player_name) == nl,
            ),
        ))
        if existing and existing.active:
            return {
                "ok": True,
                "role_id": str(existing.role_id),
                "albion_player_name": existing.albion_player_name,
            }
        if existing:
            result = await db.execute(update(BotRegistration).where(
                BotRegistration.id == existing.id,
                or_(
                    BotRegistration.human_revoked_at.is_(None),
                    BotRegistration.human_revoked_at < _as_utc(body.requested_at),
                ),
            ).values(
                albion_player_name=name,
                region=region,
                role_id=int(role_id),
                is_ally=False,
                active=True,
                human_revoked_at=None,
                created_at=datetime.now(timezone.utc),
            ))
            if not result.rowcount:
                await db.commit()
                return {"ok": False, "reason": "human_revoked"}
        else:
            db.add(BotRegistration(
                guild_id=guild_id,
                discord_user_id=int(body.discord_user_id),
                albion_player_id=manual_id,
                albion_player_name=name,
                region=region,
                role_id=int(role_id),
                is_ally=False,
                active=True,
            ))
        await db.commit()
        return {"ok": True, "role_id": str(role_id), "albion_player_name": name}

    found: dict | None = None
    region: str | None = None
    any_host_ok = False
    guild_region = (g.settings or {}).get("albion_guild_region")
    # Resolve as guildas próprias e a aliança ANTES do HTTP — usadas pra
    # desambiguar matches de mesmo nome (preferir o char que está na guilda
    # configurada em vez de um deletado com o mesmo nick).
    from app.services.guild_links import async_albion_guild_ids
    owned_ids = set(await async_albion_guild_ids(db, guild_id))
    g_alliance_id = g.albion_alliance_id
    # Libera read tx antes do HTTP (busca na API do Albion por região).
    await db.commit()
    # Alianças e guildas não cruzam região (cada região é um servidor de jogo
    # separado) — se a região da guilda já é conhecida, busca só nela, senão um
    # personagem com o mesmo nick em outra região pode "casar" com a guilda errada.
    hosts = {guild_region: HOSTS[guild_region]} if guild_region in HOSTS else HOSTS
    # Register é user-facing (um humano esperando no Discord) — NÃO passa pelo
    # albion_gate (rate limiter + pool de concorrência compartilhado com 1822+
    # requests de background). Sem isso, o register espera horas na fila quando
    # a API do Albion está instável e o rate limiter recua. O make_client ainda
    # alimenta o rate limiter via response hook (observe_response), mas o
    # request não fica preso atrás de background.
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
                # Pode haver mais de um personagem com o mesmo nome (case-
                # insensitive) — um deletado/sem guilda e outro ativo na
                # guilda configurada. Casar só por .lower() pega o primeiro
                # da lista, que pode ser o deletado (player_guild_id vazio →
                # "not_in_guild" injusto). Preferir o que está na guilda ou
                # aliança configurada; se nenhum bater, cai no primeiro.
                matches = [p for p in candidates if (p.get("Name") or "").lower() == nl]
                if matches:
                    match = _pick_best_match(matches, owned_ids, g_alliance_id)
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
    is_ally = False

    if player_guild_id in owned_ids:
        # Membro direto (guilda primária ou qualquer link) — a busca já trouxe o
        # AllianceId dele, e como aliança é atributo da guilda (não do jogador),
        # isso já basta pra descobrir a aliança da própria guilda sem nenhuma
        # chamada extra à API da Albion.
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
    # Upsert por (guild, personagem, discord) — mesmo char em outro Discord
    # (main + alt da mesma pessoa) ganha linha própria em vez de erro.
    existing = await db.scalar(select(BotRegistration).where(
        BotRegistration.guild_id == guild_id,
        BotRegistration.albion_player_id == albion_player_id,
        BotRegistration.discord_user_id == int(body.discord_user_id),
    ))
    if existing and existing.active:
        # Idempotente: se a resposta do primeiro registro se perdeu, a nova
        # tentativa precisa devolver os dados necessários para o bot aplicar o
        # cargo no Discord.
        return {
            "ok": True,
            "role_id": str(existing.role_id),
            "albion_player_name": existing.albion_player_name,
        }

    if existing:
        result = await db.execute(update(BotRegistration).where(
            BotRegistration.id == existing.id,
            or_(
                BotRegistration.human_revoked_at.is_(None),
                BotRegistration.human_revoked_at < _as_utc(body.requested_at),
            ),
        ).values(
            albion_player_name=found["Name"],
            region=region,
            role_id=int(role_id),
            is_ally=is_ally,
            active=True,
            human_revoked_at=None,
            created_at=datetime.now(timezone.utc),
        ))
        if not result.rowcount:
            await db.commit()
            return {"ok": False, "reason": "human_revoked"}
        reg_id = existing.id
        was_reactivation = True
    else:
        reg = BotRegistration(
            guild_id=guild_id,
            discord_user_id=int(body.discord_user_id),
            albion_player_id=albion_player_id,
            albion_player_name=found["Name"],
            region=region,
            role_id=int(role_id),
            is_ally=is_ally,
            active=True,
        )
        db.add(reg)
        await db.flush()
        reg_id = reg.id
        was_reactivation = False
    db.add(AuditLog(
        guild_id=guild_id, actor_id=int(body.discord_user_id),
        actor_type="bot", source="bot",
        action="registration.reactivate" if was_reactivation else "registration.register",
        entity="bot_registration", entity_id=str(reg_id),
        after={
            "albion_player_name": found["Name"], "albion_player_id": albion_player_id,
            "role_id": str(role_id), "is_ally": is_ally, "region": region,
            "registering_other": body.registering_other,
        },
    ))
    await db.commit()
    return {"ok": True, "role_id": str(role_id), "albion_player_name": found["Name"]}


class BotUnregisterIn(BaseModel):
    discord_user_id: str | None = None
    albion_player_name: str | None = None


@router.post("/bot/unregister/{guild_id}")
async def bot_unregister(
    guild_id: int,
    body: BotUnregisterIn,
    authorization: str = Header(...),
    db: AsyncSession = Depends(deps.async_db_session),
):
    """Comando de controle /unregister (admin): desliga todos os registros
    ativos do usuário-alvo nessa guilda — identificado pelo Discord (id) OU
    pelo nick do Albion, já que o bot aceita os dois. Devolve role_ids e
    discord_user_ids pra o bot remover a tag de quem perdeu o registro."""
    _require_bot_secret(authorization)
    conditions = [BotRegistration.guild_id == guild_id]
    if body.discord_user_id:
        conditions.append(BotRegistration.discord_user_id == int(body.discord_user_id))
    elif body.albion_player_name:
        conditions.append(func.lower(BotRegistration.albion_player_name) == body.albion_player_name.strip().lower())
    else:
        raise HTTPException(400, "informe discord_user_id ou albion_player_name")

    regs = (await db.scalars(select(BotRegistration).where(*conditions))).all()
    if not regs:
        return {"ok": False, "reason": "not_registered"}
    role_ids = sorted({str(r.role_id) for r in regs})
    discord_user_ids = sorted({str(r.discord_user_id) for r in regs})
    _revoke_registration_by_human(regs)
    # Audit por registro revogado — trilha imutável de quem perdeu o registro.
    for r in regs:
        db.add(AuditLog(
            guild_id=guild_id, actor_id=int(body.discord_user_id) if body.discord_user_id else None,
            actor_type="bot", source="bot",
            action="registration.unregister", entity="bot_registration", entity_id=str(r.id),
            before={"albion_player_name": r.albion_player_name, "role_id": str(r.role_id), "is_ally": r.is_ally},
            note=f"alvo: {body.albion_player_name or body.discord_user_id}",
        ))
    await db.commit()
    return {"ok": True, "role_ids": role_ids, "discord_user_ids": discord_user_ids}


@router.get("/bot/registration-lookup/{guild_id}")
async def bot_registration_lookup(
    guild_id: int,
    nick: str | None = None,
    user_id: str | None = None,
    authorization: str = Header(...),
    db: AsyncSession = Depends(deps.async_db_session),
):
    """Busca registro ATIVO por nick do jogo (case-insensitive) ou por
    discord_user_id — o INVERSO do /register. Usado pelo bot LEGADO
    (bot-legacy): o energy control casa o nick da log de energia com o
    usuário Discord, e a mentoria resolve o nick do post de trial; o banco
    deles (SQLite próprio) não tem mais a tabela registrations porque o
    /register agora é do bot novo. Devolve TODOS os registros ativos que
    casam (um usuário pode ter vários personagens)."""
    _require_bot_secret(authorization)
    if not nick and not user_id:
        raise HTTPException(400, "informe nick ou user_id")
    conditions = [
        BotRegistration.guild_id == guild_id,
        BotRegistration.active.is_(True),
    ]
    if nick:
        conditions.append(func.lower(BotRegistration.albion_player_name) == nick.strip().lower())
    else:
        conditions.append(BotRegistration.discord_user_id == int(user_id))
    regs = (await db.scalars(select(BotRegistration).where(*conditions))).all()
    return {
        "ok": True,
        "registrations": [
            {
                "discord_user_id": str(r.discord_user_id),
                "albion_player_name": r.albion_player_name,
                "albion_player_id": r.albion_player_id,
                "region": r.region,
            }
            for r in regs
        ],
    }


@router.get("/bot/registrations/{guild_id}")
async def bot_registrations_all(
    guild_id: int,
    authorization: str = Header(...),
    db: AsyncSession = Depends(deps.async_db_session),
):
    """Lista os discord_user_id com registro ATIVO na guilda — usado pelo loop
    de verificação de acesso ao mass-info (cogs/massinfo_access.py no bot-v2)
    pra saber quem está registrado sem chamar /bot/registration-lookup N vezes.
    Devolve só IDs pra manter o payload mínimo."""
    _require_bot_secret(authorization)
    rows = (await db.scalars(
        select(BotRegistration.discord_user_id).where(
            BotRegistration.guild_id == guild_id,
            BotRegistration.active.is_(True),
        ).distinct()
    )).all()
    return {"discord_user_ids": [str(uid) for uid in rows]}


class BotMemberGoneIn(BaseModel):
    discord_user_id: str


@router.post("/bot/registration-left-guild/{guild_id}")
async def bot_registration_left_guild(
    guild_id: int,
    body: BotMemberGoneIn,
    authorization: str = Header(...),
    db: AsyncSession = Depends(deps.async_db_session),
):
    """Chamado pelo bot em on_member_remove/on_member_ban: o usuário saiu do
    Discord, então qualquer registro ativo dele nessa guilda não faz mais
    sentido (não há cargo pra remover — ele já não está mais no servidor)."""
    _require_bot_secret(authorization)
    regs = (await db.scalars(select(BotRegistration).where(
        BotRegistration.guild_id == guild_id,
        BotRegistration.discord_user_id == int(body.discord_user_id),
        BotRegistration.active.is_(True),
    ))).all()
    _revoke_registration_by_human(regs)
    for r in regs:
        db.add(AuditLog(
            guild_id=guild_id, actor_id=int(body.discord_user_id),
            actor_type="bot", source="bot",
            action="registration.left_guild", entity="bot_registration", entity_id=str(r.id),
            before={"albion_player_name": r.albion_player_name, "role_id": str(r.role_id)},
            note="membro saiu/kick/ban do Discord",
        ))
    await db.commit()
    return {"ok": True}


class BotRoleRemovedIn(BaseModel):
    discord_user_id: str
    removed_role_ids: list[str]
    # Avaliado pelo bot com os cargos finais + overwrites do canal mass-info.
    # Sem canal configurado ou sem acesso, vem False e mantém o comportamento
    # seguro de revogar a role de registration removida.
    retains_massinfo_access: bool = False


@router.post("/bot/registration-role-removed/{guild_id}")
async def bot_registration_role_removed(
    guild_id: int,
    body: BotRoleRemovedIn,
    authorization: str = Header(...),
    db: AsyncSession = Depends(deps.async_db_session),
):
    """Chamado pelo bot em on_member_update quando algum cargo é tirado do
    membro manualmente: se o cargo removido era o de um registro ativo, esse
    registro perde a validade (sem isso, o /register original fica
    "esquecido" no banco mesmo sem o membro ter mais o cargo)."""
    _require_bot_secret(authorization)
    removed = {int(x) for x in body.removed_role_ids}
    regs = (await db.scalars(select(BotRegistration).where(
        BotRegistration.guild_id == guild_id,
        BotRegistration.discord_user_id == int(body.discord_user_id),
        BotRegistration.active.is_(True),
    ))).all()
    revoked = _registration_roles_to_revoke(regs, removed, body.retains_massinfo_access)
    _revoke_registration_by_human(revoked)
    for r in revoked:
        db.add(AuditLog(
            guild_id=guild_id, actor_id=int(body.discord_user_id),
            actor_type="bot", source="bot",
            action="registration.role_removed", entity="bot_registration", entity_id=str(r.id),
            before={"albion_player_name": r.albion_player_name, "role_id": str(r.role_id)},
            note=f"cargos removidos manualmente: {sorted(removed)}",
        ))
    await db.commit()
    role_ids = [str(r.role_id) for r in revoked]
    # Normalmente o cargo já saiu antes do evento chegar. Esta remoção é
    # idempotente e fecha a corrida com uma resposta velha de /register, mesmo
    # quando o bot precisou enfileirar o evento enquanto o backend estava fora.
    bot_token = get_settings().discord_bot_token
    if bot_token:
        for role_id in role_ids:
            try:
                await asyncio.to_thread(
                    discord.remove_guild_member_role,
                    str(guild_id), body.discord_user_id, role_id, bot_token,
                )
            except Exception:
                pass
    return {"ok": True, "role_ids": role_ids}


# ── Bot heartbeat ─────────────────────────────────────────────────────────────

class HeartbeatIn(BaseModel):
    guild_name: str | None = None
    guild_icon: str | None = None


@router.post("/bot/heartbeat/{guild_id}")
async def bot_heartbeat(
    guild_id: int,
    body: HeartbeatIn,
    authorization: str = Header(...),
    db: AsyncSession = Depends(deps.async_db_session),
):
    _require_bot_secret(authorization)
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None and body.guild_name:
        g = Guild(id=guild_id, name=body.guild_name, icon=body.guild_icon)
        db.add(g)
    if g:
        g.bot_present = True
        if body.guild_name:
            g.name = body.guild_name
        await db.commit()
    return {"ok": True}


# ── Bot goodbye ───────────────────────────────────────────────────────────────

@router.post("/bot/goodbye/{guild_id}")
async def bot_goodbye(
    guild_id: int,
    authorization: str = Header(...),
    db: AsyncSession = Depends(deps.async_db_session),
):
    _require_bot_secret(authorization)
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
    if g:
        g.bot_present = False
        await db.commit()
    return {"ok": True}


# ── Bot: economia ─────────────────────────────────────────────────────────────
# Porta simplificada do sistema de economia do bot legado (bot/cogs/economy.py):
# só saldo por membro (balance/total_earned), sem banco da guild nem os outros
# rankings (attendance/mvp/kills/deaths — dependem de dados de CTA que não
# existem aqui). Regras de negócio tipo "quem pode usar" ficam do lado do bot
# (check_command_access, já configurável pelo site); aqui só valida o dinheiro
# em si (valor positivo, saldo suficiente).
# ponytail: rotas sync — economy_svc (get_or_create_balance) ainda não migrado
# pra async; rodam em threadpool do FastAPI, não bloqueiam o event loop.

_get_or_create_balance = economy_svc.get_or_create_balance


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
    request_id: str | None = None


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
    if body.request_id:
        previous = db.scalar(select(EconomyTransaction).where(
            EconomyTransaction.request_id == body.request_id,
            EconomyTransaction.guild_id == guild_id,
        ))
        if previous:
            sender = _get_or_create_balance(db, guild_id, body.from_user_id)
            receiver = _get_or_create_balance(db, guild_id, body.to_user_id)
            return {
                "ok": True, "from_balance": sender.balance,
                "to_balance": receiver.balance, "transaction_id": previous.id,
            }
    sender = _get_or_create_balance(db, guild_id, body.from_user_id)
    if sender.balance < body.amount:
        return {"ok": False, "from_balance": sender.balance}
    receiver = _get_or_create_balance(db, guild_id, body.to_user_id)
    before = {"from_balance": sender.balance, "to_balance": receiver.balance}
    sender.balance -= body.amount
    receiver.balance += body.amount
    receiver.total_earned += body.amount
    tx = EconomyTransaction(
        request_id=body.request_id,
        guild_id=guild_id, kind="pay", actor_discord_id=body.from_user_id,
        from_user_id=body.from_user_id, to_user_id=body.to_user_id,
        total_earned_user_id=body.to_user_id, amount=body.amount,
    )
    db.add(tx)
    db.flush()
    db.add(AuditLog(
        guild_id=guild_id, actor_id=body.from_user_id, actor_type="bot", source="bot",
        action="economy.pay", entity="balance", entity_id=str(body.to_user_id),
        before=before,
        after={"from_balance": sender.balance, "to_balance": receiver.balance, "amount": body.amount},
        note=f"transaction #{tx.id}",
    ))
    db.commit()
    db.refresh(tx)
    return {"ok": True, "from_balance": sender.balance, "to_balance": receiver.balance, "transaction_id": tx.id}


class EconomyAddIn(BaseModel):
    discord_user_id: int
    amount: int
    actor_discord_id: int
    request_id: str | None = None


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
    if body.request_id:
        previous = db.scalar(select(EconomyTransaction).where(
            EconomyTransaction.request_id == body.request_id,
            EconomyTransaction.guild_id == guild_id,
        ))
        if previous:
            bal = _get_or_create_balance(db, guild_id, body.discord_user_id)
            return {
                "balance": bal.balance, "total_earned": bal.total_earned,
                "transaction_id": previous.id,
            }
    bal = _get_or_create_balance(db, guild_id, body.discord_user_id)
    before = bal.balance
    bal.balance += body.amount
    bal.total_earned += body.amount
    tx = EconomyTransaction(
        request_id=body.request_id,
        guild_id=guild_id, kind="add", actor_discord_id=body.actor_discord_id,
        from_user_id=None, to_user_id=body.discord_user_id,
        total_earned_user_id=body.discord_user_id, amount=body.amount,
    )
    db.add(tx)
    db.flush()
    db.add(AuditLog(
        guild_id=guild_id, actor_id=body.actor_discord_id, actor_type="bot", source="bot",
        action="economy.add", entity="balance", entity_id=str(body.discord_user_id),
        before={"balance": before}, after={"balance": bal.balance, "amount": body.amount},
        note=f"transaction #{tx.id}",
    ))
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
    request_id: str | None = None


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
    if body.request_id:
        previous = db.scalar(select(EconomyTransaction).where(
            EconomyTransaction.request_id == body.request_id,
            EconomyTransaction.guild_id == guild_id,
        ))
        if previous:
            bal = _get_or_create_balance(db, guild_id, body.discord_user_id)
            return {
                "removed": previous.amount, "balance": bal.balance,
                "transaction_id": previous.id,
            }
    bal = _get_or_create_balance(db, guild_id, body.discord_user_id)
    before = bal.balance
    actual = body.amount if body.allow_negative else min(bal.balance, body.amount)
    bal.balance -= actual
    transaction_id = None
    if actual > 0:
        tx = EconomyTransaction(
            request_id=body.request_id,
            guild_id=guild_id, kind="remove", actor_discord_id=body.actor_discord_id,
            from_user_id=body.discord_user_id, to_user_id=None,
            total_earned_user_id=None, amount=actual,
        )
        db.add(tx)
        db.flush()
        db.add(AuditLog(
            guild_id=guild_id, actor_id=body.actor_discord_id, actor_type="bot", source="bot",
            action="economy.remove", entity="balance", entity_id=str(body.discord_user_id),
            before={"balance": before}, after={"balance": bal.balance, "amount": actual},
            note=f"transaction #{tx.id}",
        ))
        db.commit()
        db.refresh(tx)
        transaction_id = tx.id
    else:
        db.commit()
    return {"removed": actual, "balance": bal.balance, "transaction_id": transaction_id}


class EconomyUndoIn(BaseModel):
    request_id: str | None = None


@router.post("/bot/economy/undo/{guild_id}/{transaction_id}")
def bot_economy_undo(
    guild_id: int,
    transaction_id: int,
    body: EconomyUndoIn,
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
        if body.request_id and tx.undo_request_id == body.request_id:
            return {
                "ok": True, "kind": tx.kind, "amount": tx.amount,
                "from_user_id": tx.from_user_id, "to_user_id": tx.to_user_id,
            }
        return {"ok": False, "reason": "already_undone"}

    if tx.from_user_id is not None:
        _get_or_create_balance(db, guild_id, tx.from_user_id).balance += tx.amount
    if tx.to_user_id is not None:
        _get_or_create_balance(db, guild_id, tx.to_user_id).balance -= tx.amount
    if tx.total_earned_user_id is not None:
        _get_or_create_balance(db, guild_id, tx.total_earned_user_id).total_earned -= tx.amount

    tx.undone = True
    tx.undo_request_id = body.request_id
    db.commit()
    return {
        "ok": True, "kind": tx.kind, "amount": tx.amount,
        "from_user_id": tx.from_user_id, "to_user_id": tx.to_user_id,
    }


@router.get("/bot/economy/leaderboard/{guild_id}")
async def bot_economy_leaderboard(
    guild_id: int,
    limit: int = 10,
    offset: int = 0,
    authorization: str = Header(...),
    db: AsyncSession = Depends(deps.async_db_session),
):
    _require_bot_secret(authorization)
    total = await db.scalar(
        select(func.count()).select_from(EconomyBalance).where(EconomyBalance.guild_id == guild_id)
    ) or 0
    rows = (await db.execute(
        select(EconomyBalance.discord_user_id, EconomyBalance.balance)
        .where(EconomyBalance.guild_id == guild_id)
        .order_by(EconomyBalance.balance.desc(), EconomyBalance.discord_user_id.asc())
        .limit(limit).offset(offset)
    )).all()
    return {"total": total, "rows": [{"discord_user_id": uid, "balance": bal} for uid, bal in rows]}


@router.get("/bot/economy/stats/{guild_id}")
async def bot_economy_stats(
    guild_id: int,
    authorization: str = Header(...),
    db: AsyncSession = Depends(deps.async_db_session),
):
    _require_bot_secret(authorization)
    row = (await db.execute(
        select(func.count(), func.coalesce(func.sum(EconomyBalance.balance), 0))
        .where(EconomyBalance.guild_id == guild_id)
    )).one()
    return {"user_count": row[0], "balances_sum": int(row[1])}


@router.get("/bot/economy/transactions/{guild_id}/{discord_user_id}")
def bot_economy_transactions(
    guild_id: int,
    discord_user_id: int,
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Ledger de transações de um membro (todas as linhas onde ele aparece como
    from, to ou total_earned). Mesma lógica de direção da rota member/wallet,
    mas com BOT_API_SECRET em vez de sessão de site. Inclui event_channel_id e
    event_message_id pra o bot poder mencionar a thread de revisão do evento."""
    _require_bot_secret(authorization)
    uid = discord_user_id
    bal = db.scalar(select(EconomyBalance).where(
        EconomyBalance.guild_id == guild_id,
        EconomyBalance.discord_user_id == uid,
    ))
    balance = int(bal.balance) if bal else 0
    total_earned = int(bal.total_earned) if bal else 0

    count_q = (
        select(EconomyTransaction)
        .where(
            EconomyTransaction.guild_id == guild_id,
            (EconomyTransaction.from_user_id == uid)
            | (EconomyTransaction.to_user_id == uid)
            | (EconomyTransaction.total_earned_user_id == uid),
        )
    )
    total = db.scalar(
        select(func.count()).select_from(count_q.subquery())
    ) or 0

    rows = db.scalars(
        select(EconomyTransaction)
        .where(
            EconomyTransaction.guild_id == guild_id,
            (EconomyTransaction.from_user_id == uid)
            | (EconomyTransaction.to_user_id == uid)
            | (EconomyTransaction.total_earned_user_id == uid),
        )
        .order_by(EconomyTransaction.id.desc())
        .limit(limit).offset(offset)
    ).all()

    counter_ids = {
        r.from_user_id for r in rows if r.from_user_id and r.from_user_id != uid
    } | {
        r.to_user_id for r in rows if r.to_user_id and r.to_user_id != uid
    }
    names: dict[int, str] = {}
    if counter_ids:
        for u in db.scalars(select(User).where(User.id.in_(counter_ids))):
            names[u.id] = u.global_name or u.username

    albion_names: dict[int, str] = {}
    if counter_ids:
        reg_rows = db.execute(
            select(BotRegistration.discord_user_id, BotRegistration.albion_player_name)
            .where(
                BotRegistration.guild_id == guild_id,
                BotRegistration.discord_user_id.in_(counter_ids),
                BotRegistration.active.is_(True),
            )
        ).all()
        for discord_id, albion_name in reg_rows:
            if discord_id not in albion_names:
                albion_names[discord_id] = albion_name

    actor_ids = {
        r.actor_discord_id for r in rows
        if r.actor_discord_id
        and r.actor_discord_id != uid
        and r.actor_discord_id != r.from_user_id
        and r.actor_discord_id != r.to_user_id
        and r.kind in ("add", "remove", "forfeit")
    }
    actor_names: dict[int, str] = {}
    if actor_ids:
        for u in db.scalars(select(User).where(User.id.in_(actor_ids))):
            actor_names[u.id] = u.global_name or u.username

    event_ids = {r.event_id for r in rows if r.event_id is not None}
    event_info: dict[int, dict] = {}
    if event_ids:
        for ev in db.scalars(select(Event).where(Event.id.in_(event_ids))):
            event_info[ev.id] = {
                "title": ev.title or f"Evento #{ev.id}",
                "event_channel_id": str(ev.event_channel_id) if ev.event_channel_id else None,
                "event_message_id": str(ev.event_message_id) if ev.event_message_id else None,
            }

    txs = []
    for r in rows:
        if r.to_user_id == uid:
            direction = "in"
            cp_id = r.from_user_id
        elif r.from_user_id == uid:
            direction = "out"
            cp_id = r.to_user_id
        else:
            direction = "neutral"
            cp_id = None
        actor_name = None
        if r.kind in ("add", "remove", "forfeit") and r.actor_discord_id and r.actor_discord_id != uid:
            actor_name = actor_names.get(r.actor_discord_id)
        ei = event_info.get(r.event_id) if r.event_id else None
        txs.append({
            "id": r.id, "kind": r.kind, "direction": direction, "amount": r.amount,
            "counterparty_id": cp_id,
            "counterparty_name": names.get(cp_id) if cp_id else None,
            "counterparty_albion_name": albion_names.get(cp_id) if cp_id else None,
            "actor_discord_id": r.actor_discord_id if r.actor_discord_id else None,
            "actor_name": actor_name,
            "event_id": r.event_id,
            "event_title": ei["title"] if ei else None,
            "event_channel_id": ei["event_channel_id"] if ei else None,
            "event_message_id": ei["event_message_id"] if ei else None,
            "payout_context": dict(r.payout_context or {}),
            "undone": r.undone, "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return {"balance": balance, "total_earned": total_earned,
            "transactions": txs, "total": int(total)}


# ── Bot: banco da guilda (admin adjust) ──────────────────────────────────────
# !bank add/remove do bot-v2 — admin-only no bot (guild admin permission check
# local, mesmo padrão dos outros comandos admin). O backend confia no
# BOT_API_SECRET e no caller validar; nunca revalida admin aqui (igual ao
# /bot/economy/add e /bot/economy/remove).

class BankAdjustIn(BaseModel):
    # Positivo = credito (add silver ao banco), negativo = débito. Reason é
    # livre e curto — vai no EconomyTransaction.kind e no audit_log, pra o
    # histórico do banco ser rastreável.
    amount: int
    reason: str | None = None
    actor_discord_id: int
    request_id: str | None = None


@router.post("/bot/guilds/{guild_id}/bank/adjust")
def bot_guild_bank_adjust(
    guild_id: int,
    body: BankAdjustIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Ajusta o bank_balance da guilda em ±amount. amount=0 é no-op (não cria
    linha). Cria um EconomyTransaction kind="bank_adjust" pra /undo poder
    reverter e audit_log pra trilha imutável. Pode deixar bank_balance
    negativo (débito sem cobertura é permitido — a guilda pode dever prata)."""
    _require_bot_secret(authorization)
    if body.amount == 0:
        g = db.scalar(select(Guild).where(Guild.id == guild_id))
        return {"ok": True, "balance": g.bank_balance if g else 0, "transaction_id": None}
    if body.request_id:
        previous = db.scalar(select(EconomyTransaction).where(
            EconomyTransaction.request_id == body.request_id,
            EconomyTransaction.guild_id == guild_id,
        ))
        if previous:
            g = db.scalar(select(Guild).where(Guild.id == guild_id))
            return {"ok": True, "balance": g.bank_balance if g else 0, "transaction_id": previous.id}
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        raise HTTPException(404, "guilda não encontrada")
    before = g.bank_balance
    g.bank_balance += body.amount
    tx = EconomyTransaction(
        request_id=body.request_id,
        guild_id=guild_id, kind="bank_adjust", actor_discord_id=body.actor_discord_id,
        # Sem from/to_user_id — é movimento de banco, não de membro. /undo lê
        # o delta e o sinal; o sinal está em amount (negativo = débito).
        from_user_id=None, to_user_id=None, total_earned_user_id=None,
        amount=body.amount,
    )
    db.add(tx)
    db.add(AuditLog(
        guild_id=guild_id, actor_id=body.actor_discord_id, actor_type="bot", source="bot",
        action="guild.bank_adjust", entity="guild", entity_id=str(guild_id),
        before={"bank_balance": before},
        after={"bank_balance": g.bank_balance, "amount": body.amount, "reason": body.reason},
    ))
    db.commit()
    db.refresh(tx)
    return {"ok": True, "balance": g.bank_balance, "transaction_id": tx.id}


@router.get("/bot/guilds/{guild_id}/bank")
def bot_guild_bank_balance(
    guild_id: int,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """!bank (sem args) lê aqui o saldo atual do banco da guilda."""
    _require_bot_secret(authorization)
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    region = (g.settings or {}).get("albion_guild_region") if g else None
    return {"balance": g.bank_balance if g else 0, "region": region}


# ── Bot: confisc de saldo de membros que saíram (grace 7 dias) ───────────────

@router.post("/bot/economy/member-left/{guild_id}/{discord_user_id}")
def bot_economy_member_left(
    guild_id: int, discord_user_id: int,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Chamado pelo bot em on_member_remove: marca left_at no GuildMember."""
    _require_bot_secret(authorization)
    economy_svc.set_member_left(db, guild_id, discord_user_id)
    db.commit()
    return {"ok": True}


@router.post("/bot/economy/member-returned/{guild_id}/{discord_user_id}")
def bot_economy_member_returned(
    guild_id: int, discord_user_id: int,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Chamado pelo bot em on_member_join: limpa left_at (membro voltou)."""
    _require_bot_secret(authorization)
    economy_svc.clear_member_left(db, guild_id, discord_user_id)
    db.commit()
    return {"ok": True}


@router.post("/bot/economy/forfeit-due/{guild_id}")
def bot_economy_forfeit_due(
    guild_id: int,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Loop do bot chama isto periodicamente: confisca saldos de membros que
    saíram há mais de 7 dias. Devolve a lista de confiscos pro bot logar."""
    _require_bot_secret(authorization)
    result = economy_svc.forfeit_due(db, guild_id)
    db.commit()
    return {"forfeited": result}


# ── Bot: eventos (mass-info + inscrições) ───────────────────────────────────────
# Fonte da verdade do gate/inscrições é o site — o bot só chama estas rotas e
# renderiza; nunca reimplementa a cascata de parties/cargos (ver
# app/services/event_gates.py e event_signups.py).
# ponytail: rotas sync — event_signups_svc/events_svc ainda não migrados pra
# async; rodam em threadpool do FastAPI, não bloqueiam o event loop.

def _parse_role_ids(raw: str | None) -> set[int]:
    if not raw:
        return set()
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


@router.get("/bot/events/{guild_id}/pending-work")
def bot_events_pending_work(
    guild_id: int,
    force: bool = False,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    events = event_signups_svc.list_active_events(db, guild_id)
    # Catch-up: avança SCHEDULED cujo horário chegou (robusto a períodos offline
    # do bot — roda no primeiro poll após reconectar). Transition marca dirty,
    # então o has_pending_work abaixo devolve o embed atualizado no mesmo ciclo.
    event_signups_svc.catch_up_states(db, guild_id, events)
    # t10min: enfileira gatilho de @everyone pra eventos SCHEDULED que acabaram
    # de entrar na janela lupa (T-10min). Idempotente via settings.lupa_announced.
    # Depois do catch_up (eventos podem ter virado in_progress e saído do set
    # "scheduled" — esses não pegam t10min, só in_progress, que é o correto).
    # Commit explícito: get_session (deps) não commita, e esse é um GET que agora
    # escreve no outbox — sem isso o t10min enfileirado se perde no rollback.
    event_signups_svc.announce_lupa_starts(db, guild_id, events)
    db.commit()
    # Outbox de pings: o que o bot vai bumpar/@pingar nesse ciclo. force=True
    # (on_ready) pula o outbox — só reedita in-place pra religar botões mortos
    # pelo restart, sem re-pingar eventos já pingados antes da queda.
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    settings = (g.settings or {}) if g else {}
    ping_triggers = [] if force else event_signups_svc.get_pending_ping_triggers(settings)
    function_prompts = [] if force else event_signups_svc.get_pending_function_prompts(settings)
    function_prompt_deletes = (
        [] if force else event_signups_svc.get_pending_function_prompt_deletes(settings)
    )
    # force=True ignora o gate de dirty/staleness — usado só no catch-up de
    # on_ready do bot-v2: um restart invalida os bindings de View em memória
    # (botões do mass-info) independente de o conteúdo ter mudado, então o
    # embed precisa ser reeditado mesmo "fresco" pra religar os botões.
    if (
        not force
        and not event_signups_svc.has_pending_work(db, guild_id, events)
        and not ping_triggers
        and not function_prompts
        and not function_prompt_deletes
    ):
        return {
            "events": [], "ping_triggers": [], "function_prompts": [],
            "function_prompt_deletes": [],
        }
    # needs_rebuild sinaliza ao bot pra reeditar o embed MESMO quando events é
    # vazio — caso do último evento ativo ter sido cancelado/finalizado/excluído:
    # o dirty mora num evento terminal (fora de ACTIVE_STATES), has_pending_work
    # retorna True, mas build_pending_work não lista nada. Sem este flag o bot
    # deixaria a linha cancelada estampada no embed antigo.
    payload = event_signups_svc.build_pending_work(db, guild_id, events)
    payload["needs_rebuild"] = True
    payload["ping_triggers"] = ping_triggers
    payload["function_prompts"] = function_prompts
    payload["function_prompt_deletes"] = function_prompt_deletes
    return payload


class MassinfoSyncedIn(BaseModel):
    message_id: str
    ack_ping_triggers: bool = False


@router.post("/bot/events/{guild_id}/massinfo-synced")
def bot_events_massinfo_synced(
    guild_id: int,
    body: MassinfoSyncedIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        raise HTTPException(404)
    settings = dict(g.settings or {})
    settings["massinfo_message_id"] = body.message_id
    g.settings = settings
    event_signups_svc.mark_massinfo_synced(db, guild_id)
    if body.ack_ping_triggers:
        event_signups_svc.ack_ping_triggers(db, guild_id)
    db.commit()
    return {"ok": True}


@router.post("/bot/events/{guild_id}/ping-triggers-acked")
def bot_events_ping_triggers_acked(
    guild_id: int,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Bot chama DEPOIS de consumir o outbox de pings (bump + @everyone) pra
    esvaziar settings.pending_ping_triggers — sem isso o próximo poll de 5s
    re-pingaria os mesmos gatilhos. Separado do /massinfo-synced porque o
    on_ready (force=True) lê o outbox vazio e NÃO deve limpá-lo (os pings
    pendentes ficam pro próximo poll normal disparar de fato)."""
    _require_bot_secret(authorization)
    event_signups_svc.ack_ping_triggers(db, guild_id)
    db.commit()
    return {"ok": True}


@router.post("/bot/events/{guild_id}/function-prompts-acked")
def bot_events_function_prompts_acked(
    guild_id: int,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    event_signups_svc.ack_function_prompts(db, guild_id)
    db.commit()
    return {"ok": True}


class FunctionPromptMessagesIn(BaseModel):
    messages: list[dict]


@router.post("/bot/events/{guild_id}/function-prompt-messages")
def bot_events_function_prompt_messages(
    guild_id: int,
    body: FunctionPromptMessagesIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    event_signups_svc.record_function_prompt_messages(db, guild_id, body.messages)
    event_signups_svc.ack_function_prompts(db, guild_id)
    db.commit()
    return {"ok": True}


class FunctionPromptDeletesAckIn(BaseModel):
    message_ids: list[str]


@router.post("/bot/events/{guild_id}/function-prompt-deletes-acked")
def bot_events_function_prompt_deletes_acked(
    guild_id: int,
    body: FunctionPromptDeletesAckIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    event_signups_svc.ack_function_prompt_deletes(db, guild_id, set(body.message_ids))
    db.commit()
    return {"ok": True}


@router.get("/bot/events/{guild_id}/{event_id}/eligible-functions")
def bot_events_eligible_functions(
    guild_id: int,
    event_id: int,
    discord_user_id: int,
    discord_role_ids: str = "",
    lang: str = "en",
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    settings = (g.settings or {}) if g else {}
    event_weapon_gates = settings.get("event_weapon_gates", {})
    event = db.scalar(select(Event).where(Event.id == event_id, Event.guild_id == guild_id))
    try:
        options, reason, current, min_builds = event_signups_svc.get_eligible_options(
            db, guild_id, event_id, discord_user_id, _parse_role_ids(discord_role_ids), event_weapon_gates,
        )
    except ServiceError as e:
        raise HTTPException(404, str(e))
    for o in options:
        o["weapon_name"] = _translate_weapon_name(
            o.get("weapon_name", ""), o.get("weapon_item_id", ""), lang,
        )
    profile_options = event_signups_svc.get_profile_options(
        db, guild_id, event.comp_id if event else None, discord_user_id,
        {o["key"]: o for o in options},
    )
    fn_types = {
        item.get("key"): {
            "label": item.get("label") or item.get("key"),
            "emoji": item.get("emoji") or "❔",
            "position": index,
        }
        for index, item in enumerate(settings.get("fn_types") or [])
        if item.get("key")
    }

    def _fn_label(fn: str | None) -> str:
        meta = fn_types.get(fn or "") or fn_types.get(event_gates_svc.fn_key(fn))
        return (meta or {}).get("label") or fn or "other"

    def _pair_label(weapon_id: int, fn: str | None) -> str:
        weapon_name = next(
            (o["weapon_name"] for o in options if o["weapon_id"] == weapon_id), None,
        ) or f"w{weapon_id}"
        return weapon_name

    current_options = [
        event_gates_svc.pair_key(int(e["weapon_id"]), e.get("fn"))
        for e in ((current.weapon_fns or []) if current else [])
        if isinstance(e, dict) and e.get("weapon_id") is not None
    ]
    return {
        # Opções distintas por par (weapon, fn) — a identidade do signup.
        "options": [
            {
                "key": o["key"], "weapon_id": o["weapon_id"],
                "weapon_name": o["weapon_name"], "fn": o["fn"],
                "label": o["weapon_name"],
            }
            for o in options
        ],
        "denial_reason": reason,
        "signup_min_builds": min_builds,
        "assignment_mode": event.assignment_mode if event else "hybrid",
        "functions_released": bool(event and event.functions_released),
        "current_signup": (
            {
                "options": current_options,
                "labels": [
                    _pair_label(int(e["weapon_id"]), e.get("fn"))
                    for e in (current.weapon_fns or [])
                    if isinstance(e, dict) and e.get("weapon_id") is not None
                ],
                # Legado (exibição): nomes de GameRole do snapshot.
                "functions": list(current.functions or []),
            }
            if current else None
        ),
        # Pré-seleção: preferências globais (weapon, fn) visíveis nesta comp.
        "profile_options": profile_options,
        "category_types": fn_types,
    }


class BotSignupIn(BaseModel):
    user_id: int
    user_name: str | None = None
    # Identidade nova: pair keys ("w<weapon_id>:<fn>") na ordem de preferência.
    options: list[str] = []
    # Legado (bot antigo durante o deploy): nomes de GameRole.
    functions: list[str] = []
    discord_role_ids: list[int] = []


@router.post("/bot/events/{guild_id}/{event_id}/signups")
def bot_events_upsert_signup(
    guild_id: int,
    event_id: int,
    body: BotSignupIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    event_weapon_gates = ((g.settings or {}) if g else {}).get("event_weapon_gates", {})
    try:
        row = event_signups_svc.upsert_signup(
            db, guild_id, event_id, body.user_id, body.user_name, body.options,
            set(body.discord_role_ids), event_weapon_gates,
            legacy_names=body.functions,
        )
    except ServiceError as e:
        raise HTTPException(404, str(e))
    event = db.get(Event, event_id)
    db.commit()
    # Labels ("Arma · Fn") pro bot confirmar o que foi gravado.
    wids = {
        int(e["weapon_id"]) for e in (row.weapon_fns or [])
        if isinstance(e, dict) and e.get("weapon_id") is not None
    }
    weapon_names = {
        w.id: w.name for w in db.scalars(select(Weapon).where(Weapon.id.in_(wids)))
    } if wids else {}
    saved_pairs = [
        e for e in (row.weapon_fns or [])
        if isinstance(e, dict) and e.get("weapon_id") is not None
    ]

    def _saved_label(e: dict) -> str:
        wid = int(e["weapon_id"])
        return weapon_names.get(wid) or ('w' + str(wid))

    return {
        "ok": True,
        "options": [
            event_gates_svc.pair_key(int(e["weapon_id"]), e.get("fn"))
            for e in saved_pairs
        ],
        "labels": [_saved_label(e) for e in saved_pairs],
        "functions": list(row.functions or []),
        "assignment_mode": event.assignment_mode if event else "hybrid",
    }


@router.get("/bot/events/{guild_id}/{event_id}/signups/{user_id}")
async def bot_events_get_signup(
    guild_id: int,
    event_id: int,
    user_id: int,
    authorization: str = Header(...),
    db: AsyncSession = Depends(deps.async_db_session),
):
    """Confirmação read-after-write usada pelo bot quando a resposta do POST
    pode ter se perdido depois do commit."""
    _require_bot_secret(authorization)
    event = await db.scalar(select(Event).where(
        Event.id == event_id, Event.guild_id == guild_id,
    ))
    if event is None:
        raise HTTPException(404, "evento não encontrado")
    row = await db.scalar(select(EventSignup).where(
        EventSignup.event_id == event_id,
        EventSignup.guild_id == guild_id,
        EventSignup.user_id == user_id,
    ))
    return {
        "exists": row is not None,
        "options": [
            event_gates_svc.pair_key(int(e["weapon_id"]), e.get("fn"))
            for e in ((row.weapon_fns or []) if row else [])
            if isinstance(e, dict) and e.get("weapon_id") is not None
        ],
        "functions": list(row.functions or []) if row else [],
        "assignment_mode": event.assignment_mode,
    }


@router.delete("/bot/events/{guild_id}/{event_id}/signups/{user_id}")
def bot_events_remove_signup(
    guild_id: int,
    event_id: int,
    user_id: int,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    try:
        event_signups_svc.remove_signup(db, guild_id, event_id, user_id)
    except ServiceError as e:
        raise HTTPException(404, str(e))
    db.commit()
    return {"ok": True}


class BotVoiceSnapshotIn(BaseModel):
    present: list[dict] = []          # [{user_id, user_name, is_trial?}]
    at: str | None = None             # ISO; opcional (default = agora)


@router.get("/bot/events/{guild_id}/voice-active")
def bot_events_voice_active(
    guild_id: int, authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Lista eventos IN_PROGRESS + VOICE_PERCENT — alvo do snapshot loop do
    bot-v2 (cogs/voice_presence.py, a cada 30s)."""
    _require_bot_secret(authorization)
    return {"events": events_svc.list_voice_active(db, guild_id)}


@router.post("/bot/events/{guild_id}/{event_id}/voice-snapshot")
def bot_events_voice_snapshot(
    guild_id: int, event_id: int, body: BotVoiceSnapshotIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Snapshot da sala CTA: acumula total_snapshots + snapshots_present por
    jogador. No-op se o evento não for VOICE_PERCENT ou não estiver IN_PROGRESS
    — o bot pode chamar a cada 30s sem raciocinar. Freeze roda no callout."""
    _require_bot_secret(authorization)
    from datetime import datetime as _dt
    at = None
    if body.at:
        try:
            at = _dt.fromisoformat(body.at)
        except ValueError:
            at = None
    try:
        res = events_svc.voice_snapshot(db, guild_id, event_id, body.present, at)
    except events_svc.ServiceError as e:
        raise HTTPException(404, str(e))
    db.commit()
    return res


# ── Bot: mutações de evento + embed por evento (thread 📑 EVENTO #N) ───────────
# O bot-v2 faz gate de cargo local (como em /bot/economy/*) e chama estas rotas
# com BOT_API_SECRET. actor_id vem do body (quem clicou o botão no Discord); o
# audit de transição grava source="bot". Escalação (assign) fica só no site.

class BotTransitionIn(BaseModel):
    to: str
    actor_id: int | None = None
    actor_name: str | None = None
    reason: str | None = None


@router.post("/bot/events/{guild_id}/{event_id}/transition")
def bot_events_transition(
    guild_id: int, event_id: int, body: BotTransitionIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    current = db.scalar(select(Event).where(
        Event.id == event_id, Event.guild_id == guild_id,
    ))
    if current is not None and current.state.value == body.to:
        return events_svc.get_event(db, guild_id, event_id)
    try:
        detail = events_svc.transition(
            db, guild_id, event_id, body.to,
            actor_id=body.actor_id, reason=body.reason, actor_source="bot",
        )
    except events_svc.ServiceError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return detail


# ── /event no bot-v2: criar/deletar/editar/adiar ───────────────────────────────

@router.get("/bot/events/{guild_id}/comps")
def bot_events_list_comps(
    guild_id: int,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Comps não-arquivadas da guilda, pro picker de comp do /event criar/editar."""
    _require_bot_secret(authorization)
    return [{"id": c.id, "name": c.name} for c in comps_svc.list_comps(db, guild_id, False)]


@router.get("/bot/events/{guild_id}/manageable")
def bot_events_manageable(
    guild_id: int,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Eventos não-finalizados (agendado/andamento/revisão) — alimenta os pickers
    de deletar/editar/adiar do /event no bot."""
    _require_bot_secret(authorization)
    return events_svc.list_manageable_events(db, guild_id)


class BotEventCreateIn(BaseModel):
    title: str | None = None
    scheduled_at: str | None = None      # ISO UTC (o bot manda datetime.isoformat())
    comp_id: int | None = None
    message: str | None = None
    publish: bool | None = None
    signup_mode: str = "signup"
    assignment_mode: str = "hybrid"
    autofill_mode: str = "manual"
    actor_id: int | None = None
    actor_name: str | None = None
    request_id: str | None = None


@router.post("/bot/events/{guild_id}")
def bot_events_create(
    guild_id: int, body: BotEventCreateIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    if body.request_id:
        previous = db.scalar(select(Event).where(
            Event.bot_request_id == body.request_id,
            Event.guild_id == guild_id,
        ))
        if previous:
            return {"id": previous.id}
    from datetime import datetime
    scheduled_at = None
    if body.scheduled_at:
        try:
            scheduled_at = datetime.fromisoformat(body.scheduled_at)
        except ValueError:
            raise HTTPException(400, "scheduled_at inválido")
    try:
        eid = events_svc.create_event(
            db, guild_id,
            EventCreate(title=body.title, scheduled_at=scheduled_at,
                        comp_id=body.comp_id, message=body.message,
                        publish=body.publish, signup_mode=body.signup_mode,
                        assignment_mode=body.assignment_mode,
                        autofill_mode=body.autofill_mode),
            actor_id=body.actor_id, caller_name=body.actor_name,
        )
    except events_svc.ServiceError as e:
        raise HTTPException(400, str(e))
    if body.request_id:
        db.get(Event, eid).bot_request_id = body.request_id
    db.commit()
    return {"id": eid}


class BotEventUpdateIn(BaseModel):
    title: str | None = None
    scheduled_at: str | None = None
    comp_id: int | None = None
    attendance: float | None = None
    actor_id: int | None = None
    # Flags: só aplica o campo cujo flag veio True (comp_id=None é válido = limpar).
    set_title: bool = False
    set_scheduled_at: bool = False
    set_comp: bool = False
    set_attendance: bool = False
    confirm_comp_reset: bool = False


@router.patch("/bot/events/{guild_id}/{event_id}")
def bot_events_update(
    guild_id: int, event_id: int, body: BotEventUpdateIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Edição por campo do /event editar. Devolve notified_signups quando a comp
    trocou e havia inscritos — o bot pede as novas roles por DM."""
    _require_bot_secret(authorization)
    from datetime import datetime
    scheduled_at = events_svc._UNSET
    if body.set_scheduled_at:
        if body.scheduled_at:
            try:
                scheduled_at = datetime.fromisoformat(body.scheduled_at)
            except ValueError:
                raise HTTPException(400, "scheduled_at inválido")
        else:
            scheduled_at = None
    try:
        result = events_svc.update_event(
            db, guild_id, event_id,
            title=body.title if body.set_title else events_svc._UNSET,
            scheduled_at=scheduled_at,
            comp_id=body.comp_id if body.set_comp else events_svc._UNSET,
            attendance=body.attendance if body.set_attendance else events_svc._UNSET,
            confirm_comp_reset=body.confirm_comp_reset,
            actor_id=body.actor_id,
        )
    except events_svc.ServiceError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return result


@router.delete("/bot/events/{guild_id}/{event_id}")
def bot_events_delete(
    guild_id: int, event_id: int,
    actor_id: int | None = None,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    current = db.scalar(select(Event).where(
        Event.id == event_id, Event.guild_id == guild_id,
    ))
    if current is not None and current.state.value == "deleted":
        return {"ok": True}
    try:
        events_svc.delete_event(db, guild_id, event_id, actor_id, actor_source="bot")
    except events_svc.ServiceError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {"ok": True}


class BotNodeClaimIn(BaseModel):
    captured: bool = True
    sold_value: int = 0
    actor_id: int | None = None


@router.post("/bot/events/{guild_id}/{event_id}/nodes/{node_log_id}/claim")
def bot_events_claim_node(
    guild_id: int, event_id: int, node_log_id: int, body: BotNodeClaimIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    from app.services import nodes as nodes_svc
    try:
        nodes_svc.claim_node(
            db, guild_id, event_id, node_log_id,
            body.captured, body.sold_value, body.actor_id,
        )
    except nodes_svc.ServiceError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return events_svc.get_event(db, guild_id, event_id)


class BotStepIn(BaseModel):
    completed: bool = True
    data: dict | None = None
    actor_id: int | None = None


@router.post("/bot/events/{guild_id}/{event_id}/verification/{step}")
def bot_events_set_step(
    guild_id: int, event_id: int, step: str, body: BotStepIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    try:
        detail = events_svc.set_step(
            db, guild_id, event_id, step, body.completed, body.data, body.actor_id,
        )
    except events_svc.ServiceError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return detail


class BotReleaseIn(BaseModel):
    released: bool = True
    actor_id: int | None = None


@router.post("/bot/events/{guild_id}/{event_id}/release-functions")
def bot_events_release_functions(
    guild_id: int, event_id: int, body: BotReleaseIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    try:
        detail = events_svc.set_functions_released(
            db, guild_id, event_id, body.released, body.actor_id,
        )
    except events_svc.ServiceError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return detail


class BotParticipantIn(BaseModel):
    user_id: int
    user_name: str | None = None
    percent: int = 0
    base_percent: int = 0
    is_trial: bool = False
    actor_id: int | None = None


@router.post("/bot/events/{guild_id}/{event_id}/participants")
def bot_events_add_participant(
    guild_id: int, event_id: int, body: BotParticipantIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    from app.api.schemas.events import ParticipantIn
    try:
        detail = events_svc.add_participant(
            db, guild_id, event_id,
            ParticipantIn(user_id=body.user_id, user_name=body.user_name,
                          percent=body.percent, base_percent=body.base_percent,
                          is_trial=body.is_trial),
            actor_id=body.actor_id,
        )
    except events_svc.ServiceError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return detail


class BotParticipantUpdateIn(BaseModel):
    game_role_id: int | None = None
    percent: int | None = None
    is_trial: bool | None = None
    actor_id: int | None = None


@router.patch("/bot/events/{guild_id}/{event_id}/participants/{participant_id}")
def bot_events_update_participant(
    guild_id: int, event_id: int, participant_id: int, body: BotParticipantUpdateIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    from app.api.schemas.events import ParticipantUpdate
    try:
        detail = events_svc.update_participant(
            db, guild_id, event_id, participant_id,
            ParticipantUpdate(game_role_id=body.game_role_id, percent=body.percent,
                              is_trial=body.is_trial),
            actor_id=body.actor_id,
        )
    except events_svc.ServiceError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return detail


@router.delete("/bot/events/{guild_id}/{event_id}/participants/{participant_id}")
def bot_events_remove_participant(
    guild_id: int, event_id: int, participant_id: int,
    actor_id: int | None = None,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    try:
        detail = events_svc.remove_participant(
            db, guild_id, event_id, participant_id, actor_id=actor_id,
        )
    except events_svc.ServiceError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return detail


class BotDeathIn(BaseModel):
    display_name: str
    user_id: int | None = None
    silver_value: int = 0
    notes: str | None = None
    actor_id: int | None = None


@router.post("/bot/events/{guild_id}/{event_id}/deaths")
def bot_events_add_death(
    guild_id: int, event_id: int, body: BotDeathIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    from app.api.schemas.events import DeathIn
    try:
        detail = events_svc.add_death(
            db, guild_id, event_id,
            DeathIn(display_name=body.display_name, user_id=body.user_id,
                    silver_value=body.silver_value, notes=body.notes),
            actor_id=body.actor_id,
        )
    except events_svc.ServiceError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return detail


class BotDeathUpdateIn(BaseModel):
    approved: bool | None = None
    silver_value: int | None = None
    notes: str | None = None
    actor_id: int | None = None


@router.patch("/bot/events/{guild_id}/{event_id}/deaths/{death_id}")
def bot_events_update_death(
    guild_id: int, event_id: int, death_id: int, body: BotDeathUpdateIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    from app.api.schemas.events import DeathUpdate
    try:
        detail = events_svc.update_death(
            db, guild_id, event_id, death_id,
            DeathUpdate(approved=body.approved, silver_value=body.silver_value, notes=body.notes),
            actor_id=body.actor_id,
        )
    except events_svc.ServiceError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return detail


@router.delete("/bot/events/{guild_id}/{event_id}/deaths/{death_id}")
def bot_events_remove_death(
    guild_id: int, event_id: int, death_id: int,
    actor_id: int | None = None,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    _require_bot_secret(authorization)
    try:
        detail = events_svc.remove_death(
            db, guild_id, event_id, death_id, actor_id=actor_id,
        )
    except events_svc.ServiceError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return detail


@router.get("/bot/events/{guild_id}/{event_id}/embed")
def bot_events_embed(
    guild_id: int, event_id: int,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """DTO único pro embed 📑 EVENTO #N: detail + nodes próximos do callout +
    escalação (read-only). Única chamada de leitura do embed."""
    _require_bot_secret(authorization)
    dto = events_svc.embed_dto(db, guild_id, event_id)
    if dto is None:
        raise HTTPException(404, "evento não encontrado")
    return dto


@router.get("/bot/events/{guild_id}/embed-work")
def bot_events_embed_work(
    guild_id: int, force: bool = False, authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Eventos com embed sujo (mutação ocorreu) e thread ativa — o loop do bot-v2
    puxa isto, busca /embed de cada um, reedita, e chama /embed-synced.
    force=True devolve todos os eventos com thread ativa, sujo ou não — ver
    events.list_embed_dirty. Também devolve eventos terminais com thread de
    embed ainda não trancada (archive), espelho do regear-thread-work."""
    _require_bot_secret(authorization)
    return {
        "events": events_svc.list_embed_dirty(db, guild_id, force=force),
        "archive": events_svc.list_event_thread_terminal(db, guild_id),
    }


class BotEmbedSyncedIn(BaseModel):
    event_channel_id: str | None = None
    event_message_id: str | None = None
    lootlog_thread_id: str | None = None
    split_thread_id: str | None = None
    regear_thread_id: str | None = None
    clear_dirty: bool = True


@router.post("/bot/events/{guild_id}/{event_id}/embed-synced")
def bot_events_embed_synced(
    guild_id: int, event_id: int, body: BotEmbedSyncedIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Grava os ids de canal/mensagem/thread que o bot criou/achou e limpa o
    flag event_embed_dirty. Chamado após criar a thread (callout) E após cada
    refresh do embed."""
    _require_bot_secret(authorization)
    def _opt(s: str | None) -> int | None:
        return int(s) if s else None
    ok = events_svc.set_embed_ids(
        db, guild_id, event_id,
        event_channel_id=_opt(body.event_channel_id),
        event_message_id=_opt(body.event_message_id),
        lootlog_thread_id=_opt(body.lootlog_thread_id),
        split_thread_id=_opt(body.split_thread_id),
        regear_thread_id=_opt(body.regear_thread_id),
        clear_dirty=body.clear_dirty,
    )
    if not ok:
        raise HTTPException(404, "evento não encontrado")
    db.commit()
    return {"ok": True}


@router.post("/bot/events/{guild_id}/{event_id}/event-thread-archived")
def bot_events_event_thread_archived(
    guild_id: int, event_id: int, authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Sinaliza que o bot trancou (lock) a thread do embed do evento — tira o
    evento da lista de arquivamento do loop. Best-effort (200 mesmo se já
    estava, ou se o evento nunca teve thread pra trancar)."""
    _require_bot_secret(authorization)
    events_svc.mark_event_thread_archived(db, guild_id, event_id)
    db.commit()
    return {"ok": True}


# ── Bot: threads de regear por evento ──────────────────────────────────────────
# O bot-v2 cria uma thread no regear_thread_channel_id quando o evento entra em
# IN_PROGRESS (outbox: events.regear_thread_dirty). Prints postadas na thread
# viram RegearRequests atrelados ao evento (landmark). Em estados terminais o
# bot arquiva a thread.

@router.get("/bot/events/{guild_id}/regear-thread-work")
def bot_events_regear_thread_work(
    guild_id: int, authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Eventos IN_PROGRESS com thread de regear pendente de criação. O loop do
    bot-v2 puxa isto, cria a thread no regear_thread_channel_id, e chama
    /regear-thread-synced. Também devolve eventos terminais com thread ativa
    pra arquivamento (best-effort)."""
    _require_bot_secret(authorization)
    return {
        "create": events_svc.list_regear_thread_dirty(db, guild_id),
        "archive": events_svc.list_regear_thread_terminal(db, guild_id),
    }


class BotRegearThreadSyncedIn(BaseModel):
    regear_thread_id: str
    clear_dirty: bool = True


@router.post("/bot/events/{guild_id}/{event_id}/regear-thread-synced")
def bot_events_regear_thread_synced(
    guild_id: int, event_id: int, body: BotRegearThreadSyncedIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Grava o id da thread de regear criada pelo bot e limpa o flag dirty."""
    _require_bot_secret(authorization)
    thread_id = int(body.regear_thread_id) if body.regear_thread_id else None
    ok = events_svc.set_regear_thread_id(db, guild_id, event_id, thread_id,
                                         clear_dirty=body.clear_dirty)
    if not ok:
        raise HTTPException(404, "evento não encontrado")
    db.commit()
    return {"ok": True}


@router.post("/bot/events/{guild_id}/{event_id}/regear-thread-archived")
def bot_events_regear_thread_archived(
    guild_id: int, event_id: int, authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Sinaliza que o bot arquivou (lock) a thread de regear do evento — tira o
    evento da lista de arquivamento do loop. Best-effort (200 mesmo se já estava)."""
    _require_bot_secret(authorization)
    events_svc.mark_regear_thread_archived(db, guild_id, event_id)
    db.commit()
    return {"ok": True}


# ── Bot: threads de lootlog por evento (espelho do regear) ───────────────────
# O bot-v2 cria uma thread no lootlog_thread_channel_id quando o evento entra em
# IN_PROGRESS (outbox: events.lootlog_thread_dirty). .csv do lootlogger postado
# na thread vira LootLogSubmission atrelado ao evento. Em terminais arquiva.

@router.get("/bot/events/{guild_id}/lootlog-thread-work")
def bot_events_lootlog_thread_work(
    guild_id: int, authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Eventos com thread de lootlog pendente de criação + terminais pra
    arquivamento. Mesmo formato do regear-thread-work."""
    _require_bot_secret(authorization)
    return {
        "create": events_svc.list_lootlog_thread_dirty(db, guild_id),
        "archive": events_svc.list_lootlog_thread_terminal(db, guild_id),
    }


class BotLootlogThreadSyncedIn(BaseModel):
    lootlog_thread_id: str
    clear_dirty: bool = True


@router.post("/bot/events/{guild_id}/{event_id}/lootlog-thread-synced")
def bot_events_lootlog_thread_synced(
    guild_id: int, event_id: int, body: BotLootlogThreadSyncedIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Grava o id da thread de lootlog criada pelo bot e limpa o flag dirty."""
    _require_bot_secret(authorization)
    thread_id = int(body.lootlog_thread_id) if body.lootlog_thread_id else None
    ok = events_svc.set_lootlog_thread_id(db, guild_id, event_id, thread_id,
                                          clear_dirty=body.clear_dirty)
    if not ok:
        raise HTTPException(404, "evento não encontrado")
    db.commit()
    return {"ok": True}


@router.post("/bot/events/{guild_id}/{event_id}/lootlog-thread-archived")
def bot_events_lootlog_thread_archived(
    guild_id: int, event_id: int, authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Sinaliza que o bot arquivou (lock) a thread de lootlog do evento."""
    _require_bot_secret(authorization)
    events_svc.mark_lootlog_thread_archived(db, guild_id, event_id)
    db.commit()
    return {"ok": True}


# ── Bot: nodes (calendário) ────────────────────────────────────────────────────
# O bot-v2 renderiza o calendário embed e faz proxy por aqui. Gate de cargo é
# feito no bot (como em /bot/economy/*); o endpoint confia no BOT_API_SECRET.
# ponytail: rotas sync — nodes_svc ainda não migrado pra async; rodam em
# threadpool do FastAPI, não bloqueiam o event loop (exceto bot_nodes_clear,
# que é own-DB só e foi migrado).

class BotNodeEventIn(BaseModel):
    node_type: str
    map_name: str
    spawn_at: str          # ISO; o bot manda UTC
    channel_id: int | None = None
    added_by_id: int | None = None
    added_by_name: str | None = None
    allow_duplicate: bool = False
    request_id: str | None = None


@router.get("/bot/guilds/{guild_id}/nodes/calendar")
def bot_nodes_get_calendar(guild_id: int, authorization: str = Header(...),
                           db: Session = Depends(deps.db_session)):
    _require_bot_secret(authorization)
    row = nodes_svc.get_calendar(db, guild_id)
    return {"channel_id": str(row.channel_id) if row and row.channel_id else None,
            "message_id": str(row.message_id) if row and row.message_id else None}


class BotCalendarIn(BaseModel):
    channel_id: str | None = None
    message_id: str | None = None


@router.post("/bot/guilds/{guild_id}/nodes/calendar")
def bot_nodes_set_calendar(guild_id: int, body: BotCalendarIn,
                           authorization: str = Header(...),
                           db: Session = Depends(deps.db_session)):
    _require_bot_secret(authorization)
    ch = int(body.channel_id) if body.channel_id else None
    mid = int(body.message_id) if body.message_id else None
    row = nodes_svc.set_calendar(db, guild_id, channel_id=ch, message_id=mid)
    db.commit()
    return {"ok": True, "channel_id": str(row.channel_id) if row.channel_id else None,
            "message_id": str(row.message_id) if row.message_id else None}


@router.get("/bot/guilds/{guild_id}/nodes")
def bot_nodes_list(guild_id: int, authorization: str = Header(...),
                   db: Session = Depends(deps.db_session)):
    _require_bot_secret(authorization)
    rows = nodes_svc.list_events(db, guild_id)
    return {"events": [
        {"id": e.id, "node_type": e.node_type, "map_name": e.map_name,
         "spawn_at": e.spawn_at.isoformat() if e.spawn_at else None,
         "added_by_id": str(e.added_by_id) if e.added_by_id else None,
         "added_by_name": e.added_by_name}
        for e in rows
    ]}


@router.post("/bot/guilds/{guild_id}/nodes")
def bot_nodes_add(guild_id: int, body: BotNodeEventIn,
                  authorization: str = Header(...),
                  db: Session = Depends(deps.db_session)):
    _require_bot_secret(authorization)
    if body.request_id:
        previous = db.scalar(select(NodeEvent).where(
            NodeEvent.bot_request_id == body.request_id,
            NodeEvent.guild_id == guild_id,
        ))
        if previous:
            return {"ok": True, "id": previous.id}
    from datetime import datetime as _dt
    try:
        spawn = _dt.fromisoformat(body.spawn_at)
    except ValueError:
        raise HTTPException(400, "spawn_at inválido (ISO)")
    ch = body.channel_id
    try:
        e = nodes_svc.add_event(
            db, guild_id, body.node_type, body.map_name, spawn,
            channel_id=ch, added_by_id=body.added_by_id,
            added_by_name=body.added_by_name, allow_duplicate=body.allow_duplicate,
        )
    except nodes_svc.ServiceError as e:
        raise HTTPException(400, str(e))
    e.bot_request_id = body.request_id
    db.commit()
    return {"ok": True, "id": e.id}


@router.delete("/bot/guilds/{guild_id}/nodes/{event_id}")
def bot_nodes_delete(guild_id: int, event_id: int, authorization: str = Header(...),
                     db: Session = Depends(deps.db_session)):
    _require_bot_secret(authorization)
    nodes_svc.delete_event(db, guild_id, event_id)
    db.commit()
    return {"ok": True}


@router.get("/bot/guilds/{guild_id}/nodes/removable")
def bot_nodes_removable(guild_id: int, user_id: int, staff: bool = False,
                        authorization: str = Header(...),
                        db: Session = Depends(deps.db_session)):
    _require_bot_secret(authorization)
    rows = nodes_svc.removable_events(db, guild_id, staff=staff, user_id=user_id)
    return {"events": [
        {"id": e.id, "node_type": e.node_type, "map_name": e.map_name,
         "spawn_at": e.spawn_at.isoformat() if e.spawn_at else None,
         "added_by_id": str(e.added_by_id) if e.added_by_id else None}
        for e in rows
    ]}


@router.get("/bot/guilds/{guild_id}/nodes/defs")
def bot_nodes_defs(guild_id: int, authorization: str = Header(...),
                   db: Session = Depends(deps.db_session)):
    _require_bot_secret(authorization)
    rows = nodes_svc.list_defs(db, guild_id)
    return {"defs": [
        {"id": d.id, "name": d.name, "emoji": d.emoji, "weight": d.weight, "sort": d.sort}
        for d in rows
    ]}


@router.get("/bot/guilds/{guild_id}/nodes/maps")
def bot_nodes_maps(guild_id: int, authorization: str = Header(...),
                   db: Session = Depends(deps.db_session)):
    _require_bot_secret(authorization)
    return {"maps": nodes_svc.effective_maps(db, guild_id)}


class BotMapIn(BaseModel):
    map_name: str


@router.post("/bot/guilds/{guild_id}/nodes/maps")
def bot_nodes_add_map(guild_id: int, body: BotMapIn,
                      authorization: str = Header(...),
                      db: Session = Depends(deps.db_session)):
    _require_bot_secret(authorization)
    try:
        nodes_svc.add_map(db, guild_id, body.map_name)
    except nodes_svc.ServiceError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {"ok": True, "maps": nodes_svc.effective_maps(db, guild_id)}


@router.delete("/bot/guilds/{guild_id}/nodes/maps/{map_name}")
def bot_nodes_remove_map(guild_id: int, map_name: str,
                         authorization: str = Header(...),
                         db: Session = Depends(deps.db_session)):
    _require_bot_secret(authorization)
    nodes_svc.remove_map(db, guild_id, map_name)
    db.commit()
    return {"ok": True, "maps": nodes_svc.effective_maps(db, guild_id)}


@router.post("/bot/guilds/{guild_id}/nodes/clear")
async def bot_nodes_clear(guild_id: int, authorization: str = Header(...),
                    db: AsyncSession = Depends(deps.async_db_session)):
    """Equivalente ao /stopnode do bot-v1: poda TODOS os nodes vivos e zera o
    calendário (channel_id/message_id). O log permanente (`node_event_log`) é
    preservado — é auditoria."""
    _require_bot_secret(authorization)
    from sqlalchemy import delete as _delete
    from app.models.nodes import NodeCalendar
    await db.execute(_delete(NodeEvent).where(NodeEvent.guild_id == guild_id))
    cal = await db.get(NodeCalendar, guild_id)
    if cal is not None:
        cal.channel_id = None
        cal.message_id = None
    await db.commit()
    return {"ok": True}


@router.get("/bot/guilds/{guild_id}/nodes/near")
def bot_nodes_near(guild_id: int, ts: str, authorization: str = Header(...),
                   window: int = Query(nodes_svc.NEAR_CTA_WINDOW_SECONDS, ge=0, le=86400),
                   db: Session = Depends(deps.db_session)):
    _require_bot_secret(authorization)
    from datetime import datetime as _dt
    try:
        ts_dt = _dt.fromisoformat(ts)
    except ValueError:
        raise HTTPException(400, "ts inválido (ISO)")
    rows = nodes_svc.near_cta(db, guild_id, ts_dt, window)
    return {"ts": ts, "window_seconds": window, "nodes": [
        {"id": l.id, "node_type": l.node_type, "map_name": l.map_name,
         "spawn_at": l.spawn_at.isoformat() if l.spawn_at else None,
         "scout_id": str(l.scout_id) if l.scout_id else None,
         "scout_name": l.scout_name}
        for l in rows
    ]}


# ── Logout ─────────────────────────────────────────────────────────────────────

@router.post("/auth/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(get_settings().session_cookie_name)
    return resp


# ── Helpers internos ──────────────────────────────────────────────────────────

# ── Bot: juicy kills (kills com silver_dropped >= threshold) ────────────────
#
# O admin configura sala + regiões + threshold no site (GuildConfig). O bot-v2
# faz poll neste endpoint, pega deliveries já materializadas, posta na sala
# (embed + imagem), e confirma cada uma com /juicy-kill/synced.
# Mesmo pattern do battle-feed. silver_dropped é precificado pelo worker
# silver_dropped (services/silver_dropped.py) — kills sem preço ainda ficam
# NULL e não entram no queue (o bot não posta kill sem valor calculado).

_JUICY_KILL_BATCH = 25
_JUICY_KILL_HARD_FLOOR = 20_000_000  # mínimo absoluto — nenhum guilda desce abaixo disso


def _parse_watermark(value) -> datetime | None:
    """Lê watermark de settings (ISO string ou None) -> datetime aware, ou None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


@router.get("/bot/guilds/{guild_id}/juicy-kill/queue")
async def bot_juicy_kill_queue(
    guild_id: int, authorization: str = Header(...), db: AsyncSession = Depends(deps.async_db_session),
):
    """Próximo lote da outbox de juicy kills, já elegível e ordenado."""
    _require_bot_secret(authorization)
    from app.models.players import AlbionPlayer, JuicyKillDelivery, PlayerKillEvent
    from app.services.postable import postable_cutoffs_by_region
    g = await db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is None:
        raise HTTPException(404)
    settings = g.settings or {}
    channel_id = settings.get("juicy_kill_channel_id")
    if not channel_id:
        return {"kills": []}
    regions = settings.get("juicy_kill_regions") or []

    regions_to_query = [region for region in (regions or list(HOSTS)) if region in HOSTS]
    if not regions_to_query:
        return {"kills": []}
    cutoffs = await postable_cutoffs_by_region(db, regions_to_query)
    regions_to_query = [region for region in regions_to_query if region in cutoffs]
    if not regions_to_query:
        return {"kills": []}

    deliveries = []
    for region in regions_to_query:
        # Uma leitura curta por região usa o índice parcial da outbox. Mesmo um
        # burst de milhares de eventos nunca muda o custo deste poll.
        deliveries.extend((await db.scalars(
            select(JuicyKillDelivery)
            .where(
                JuicyKillDelivery.guild_id == guild_id,
                JuicyKillDelivery.state == "pending",
                JuicyKillDelivery.region == region,
                JuicyKillDelivery.occurred_at >= cutoffs[region],
            )
            .order_by(JuicyKillDelivery.occurred_at.asc(), JuicyKillDelivery.kill_id.asc())
            .limit(_JUICY_KILL_BATCH)
        )).all())
    deliveries.sort(key=lambda delivery: (delivery.occurred_at, delivery.kill_id))
    deliveries = deliveries[:_JUICY_KILL_BATCH]
    if not deliveries:
        await db.commit()
        return {"kills": []}

    events = (await db.scalars(
        select(PlayerKillEvent).where(PlayerKillEvent.id.in_([delivery.kill_id for delivery in deliveries]))
    )).all()
    events_by_id = {event.id: event for event in events}
    missing = [delivery.kill_id for delivery in deliveries if delivery.kill_id not in events_by_id]
    if missing:
        await db.execute(
            update(JuicyKillDelivery)
            .where(JuicyKillDelivery.guild_id == guild_id, JuicyKillDelivery.kill_id.in_(missing))
            .values(state="suppressed")
        )
        deliveries = [delivery for delivery in deliveries if delivery.kill_id in events_by_id]

    player_ids = {
        player_id
        for event in events_by_id.values()
        for player_id in (event.killer_player_id, event.victim_player_id)
        if player_id is not None
    }
    players_by_id = {}
    if player_ids:
        players = (await db.scalars(select(AlbionPlayer).where(AlbionPlayer.id.in_(player_ids)))).all()
        players_by_id = {player.id: player for player in players}
    from app.services.battle_tracker import publish_delay_status
    delays = publish_delay_status()
    out = [
        _juicy_kill_build(
            events_by_id[delivery.kill_id],
            players_by_id.get(events_by_id[delivery.kill_id].killer_player_id),
            players_by_id.get(events_by_id[delivery.kill_id].victim_player_id),
            delays.get(events_by_id[delivery.kill_id].region, {}),
        )
        for delivery in deliveries
    ]
    await db.commit()
    return {"kills": out}


def _juicy_kill_payload(db: Session, ev) -> dict:
    """Payload de uma juicy kill pro bot — tudo que ele precisa pra montar o
    embed e a imagem: killer/victim (nome, guilda, aliança), equipamento,
    inventário, fama, silver_dropped, região, timestamp."""
    from app.models.players import AlbionPlayer
    from app.services.battle_tracker import publish_delay_status
    killer = db.scalar(select(AlbionPlayer).where(AlbionPlayer.id == ev.killer_player_id)) if ev.killer_player_id else None
    victim = db.scalar(select(AlbionPlayer).where(AlbionPlayer.id == ev.victim_player_id)) if ev.victim_player_id else None
    delay = publish_delay_status().get(ev.region, {})
    return _juicy_kill_build(ev, killer, victim, delay)


def _juicy_kill_build(ev, killer, victim, delay) -> dict:
    return {
        "id": ev.id,
        "region": ev.region,
        "api_delay_secs": delay.get("delay_secs"),
        "albion_event_id": ev.albion_event_id,
        "timestamp": ev.timestamp.isoformat() if ev.timestamp else None,
        "fame": ev.fame,
        "silver_dropped": ev.silver_dropped or 0,
        "is_solo": ev.is_solo,
        "participant_count": ev.participant_count,
        "participants": ev.participants or [],
        "group_member_count": ev.group_member_count,
        "albion_battle_id": ev.albion_battle_id,
        "kill_area": ev.kill_area,
        "killer": {
            "name": killer.name if killer else None,
            "albion_id": killer.albion_id if killer else None,
            "guild_name": killer.guild_name if killer else None,
            "alliance_name": killer.alliance_name if killer else None,
        } if killer else None,
        "victim": {
            "name": victim.name if victim else None,
            "albion_id": victim.albion_id if victim else None,
            "guild_name": victim.guild_name if victim else None,
            "alliance_name": victim.alliance_name if victim else None,
        } if victim else None,
        "killer_equipment": ev.killer_equipment,
        "victim_equipment": ev.victim_equipment,
        "victim_inventory": ev.victim_inventory,
    }


class JuicyKillSyncedIn(BaseModel):
    kill_ids: list[int]


@router.post("/bot/guilds/{guild_id}/juicy-kill/synced")
async def bot_juicy_kill_synced(
    guild_id: int, body: JuicyKillSyncedIn,
    authorization: str = Header(...), db: AsyncSession = Depends(deps.async_db_session),
):
    """Confirma deliveries individuais após o Discord aceitar as mensagens."""
    _require_bot_secret(authorization)
    from app.models.players import JuicyKillDelivery
    exists_guild = await db.scalar(select(Guild.id).where(Guild.id == guild_id))
    if exists_guild is None:
        raise HTTPException(404)
    if not body.kill_ids:
        return {"ok": True, "synced": 0}
    result = await db.execute(
        update(JuicyKillDelivery)
        .where(
            JuicyKillDelivery.guild_id == guild_id,
            JuicyKillDelivery.kill_id.in_(body.kill_ids),
            JuicyKillDelivery.state == "pending",
        )
        .values(state="sent", sent_at=func.now())
    )
    await db.commit()
    return {"ok": True, "synced": result.rowcount}


# ponytail: rota async com Session sync — render_juicy_kill_image ainda usa
# db.get/Scalar síncronos; trocar por AsyncSession quebra. Roda em threadpool
# via deps.db_session; o await só cobre o HTTP de ícones dentro do render.
@router.get("/bot/guilds/{guild_id}/juicy-kill/{kill_id}/image")
async def bot_juicy_kill_image(
    guild_id: int, kill_id: int, authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    from fastapi.responses import FileResponse
    from app.services.juicy_kill_image import render_juicy_kill_image

    _require_bot_secret(authorization)
    if db.scalar(select(Guild.id).where(Guild.id == guild_id)) is None:
        raise HTTPException(404)
    # Libera read tx antes do HTTP (render_juicy_kill_image baixa ícones da CDN).
    db.commit()
    path = await render_juicy_kill_image(db, kill_id)
    if path is None:
        raise HTTPException(404, "juicy kill não encontrada")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ── Bot: attendance / lowattendance / warm ─────────────────────────────────────

@router.get("/bot/guilds/{guild_id}/attendance/{discord_user_id}")
def bot_attendance(
    guild_id: int,
    discord_user_id: int,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Stats de attendance de um membro: eventos totais da guild, eventos
    participados, ranking, e os mesmos números nos últimos 7 dias."""
    _require_bot_secret(authorization)

    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    # Eventos finalizados/não-terminais: contamos todos que tiveram
    # participação (IN_PROGRESS ou além). Eventos cancelados/deleted não contam.
    counted_states = (
        "in_progress", "review", "finalized",
    )

    total_all = db.scalar(
        select(func.count(Event.id)).where(
            Event.guild_id == guild_id,
            Event.state.in_(counted_states),
        )
    ) or 0

    total_7d = db.scalar(
        select(func.count(Event.id)).where(
            Event.guild_id == guild_id,
            Event.state.in_(counted_states),
            Event.ended_at >= week_ago,
        )
    ) or 0

    # Participações do usuário (is_valid != False — irregular não conta).
    user_all = db.scalar(
        select(func.count(EventParticipant.id)).join(
            Event, Event.id == EventParticipant.event_id
        ).where(
            EventParticipant.guild_id == guild_id,
            EventParticipant.user_id == discord_user_id,
            Event.state.in_(counted_states),
            EventParticipant.is_valid != False,  # noqa: E712
        )
    ) or 0

    user_7d = db.scalar(
        select(func.count(EventParticipant.id)).join(
            Event, Event.id == EventParticipant.event_id
        ).where(
            EventParticipant.guild_id == guild_id,
            EventParticipant.user_id == discord_user_id,
            Event.state.in_(counted_states),
            Event.ended_at >= week_ago,
            EventParticipant.is_valid != False,  # noqa: E712
        )
    ) or 0

    # Ranking: quantos membros têm mais participações que este.
    # Subquery: participações por user_id nesta guild.
    per_user = (
        select(
            EventParticipant.user_id,
            func.count(EventParticipant.id).label("cnt"),
        ).join(Event, Event.id == EventParticipant.event_id)
        .where(
            EventParticipant.guild_id == guild_id,
            Event.state.in_(counted_states),
            EventParticipant.is_valid != False,  # noqa: E712
        )
        .group_by(EventParticipant.user_id)
    ).subquery()

    rank = db.scalar(
        select(func.count()).select_from(per_user).where(
            per_user.c.cnt > user_all
        )
    ) or 0
    rank += 1  # 1-indexed

    # Último evento participado
    last_event = db.scalar(
        select(Event.ended_at).join(
            EventParticipant, EventParticipant.event_id == Event.id
        ).where(
            EventParticipant.guild_id == guild_id,
            EventParticipant.user_id == discord_user_id,
            Event.ended_at.is_not(None),
            EventParticipant.is_valid != False,  # noqa: E712
        ).order_by(Event.ended_at.desc()).limit(1)
    )

    # Nick registrado (pra /profile sem argumento) + região da guilda
    reg = db.scalar(
        select(BotRegistration).where(
            BotRegistration.guild_id == guild_id,
            BotRegistration.discord_user_id == discord_user_id,
            BotRegistration.active.is_(True),
        )
    )
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    region = (g.settings or {}).get("albion_guild_region") if g else None

    return {
        "total_events": total_all,
        "user_events": user_all,
        "total_events_7d": total_7d,
        "user_events_7d": user_7d,
        "rank": rank if user_all > 0 else None,
        "last_event": last_event.isoformat() if last_event else None,
        "albion_player_name": reg.albion_player_name if reg else None,
        "region": region,
    }


LOWATTENDANCE_MAX_ROWS = 15


@router.get("/bot/guilds/{guild_id}/lowattendance")
def bot_lowattendance(
    guild_id: int,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Lista registrations ativas com menor attendance nos últimos 7 dias.
    Filtra quem foi registrado há < 7 dias (created_at do BotRegistration)."""
    _require_bot_secret(authorization)

    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    counted_states = ("in_progress", "review", "finalized")

    # Registrations ativas
    regs = db.scalars(
        select(BotRegistration).where(
            BotRegistration.guild_id == guild_id,
            BotRegistration.active.is_(True),
        )
    ).all()

    if not regs:
        return {"members": [], "total_7d": 0}

    # Total de eventos nos últimos 7 dias
    total_7d = db.scalar(
        select(func.count(Event.id)).where(
            Event.guild_id == guild_id,
            Event.state.in_(counted_states),
            Event.ended_at >= week_ago,
        )
    ) or 0

    results = []
    filtered_recent = 0
    for reg in regs:
        # Filtra quem foi registrado há < 7 dias
        if reg.created_at and reg.created_at > week_ago:
            filtered_recent += 1
            continue

        count_7d = db.scalar(
            select(func.count(EventParticipant.id)).join(
                Event, Event.id == EventParticipant.event_id
            ).where(
                EventParticipant.guild_id == guild_id,
                EventParticipant.user_id == reg.discord_user_id,
                Event.state.in_(counted_states),
                Event.ended_at >= week_ago,
                EventParticipant.is_valid != False,  # noqa: E712
            )
        ) or 0

        last_event = db.scalar(
            select(Event.ended_at).join(
                EventParticipant, EventParticipant.event_id == Event.id
            ).where(
                EventParticipant.guild_id == guild_id,
                EventParticipant.user_id == reg.discord_user_id,
                Event.ended_at.is_not(None),
                EventParticipant.is_valid != False,  # noqa: E712
            ).order_by(Event.ended_at.desc()).limit(1)
        )

        results.append({
            "discord_user_id": str(reg.discord_user_id),
            "albion_player_name": reg.albion_player_name,
            "count_7d": count_7d,
            "last_event": last_event.isoformat() if last_event else None,
        })

    # Ordena por count_7d ASC, depois last_event ASC (mais antigo primeiro)
    results.sort(key=lambda r: (r["count_7d"], r["last_event"] or ""))
    top_low = results[:LOWATTENDANCE_MAX_ROWS]

    return {"members": top_low, "total_7d": total_7d, "filtered_recent": filtered_recent}


class BotWarmIn(BaseModel):
    name: str
    region: str


@router.post("/bot/guilds/{guild_id}/warm")
async def bot_warm(
    guild_id: int,
    body: BotWarmIn,
    authorization: str = Header(...),
):
    """Bot pede pra aquecer o perfil de um personagem no backend — reusa o
    profile_warmer.warm_by_name (mesma lógica do companion). Sem teto de
    install (o teto real é a cota da Albion no albion_gate)."""
    _require_bot_secret(authorization)
    from app.services import profile_warmer
    name = (body.name or "").strip()
    region = (body.region or "").strip().lower()
    if not name or region not in HOSTS:
        raise HTTPException(400, "name/region inválidos")
    return await profile_warmer.warm_by_name(name, region)


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


# ponytail: versões async dos helpers de filiação — só as rotas async usam. As
# sync continuam com as versões `Session` acima (que rodam em threadpool).
async def _require_member_async(db: AsyncSession, user: User, guild_id: int) -> GuildMember:
    m = await db.scalar(select(GuildMember).where(
        GuildMember.guild_id == guild_id, GuildMember.user_id == user.id,
    ))
    if m is None:
        raise HTTPException(403, "sem acesso")
    return m


async def _require_admin_async(db: AsyncSession, user: User, guild_id: int) -> GuildMember:
    m = await _require_member_async(db, user, guild_id)
    if not m.is_guild_admin:
        raise HTTPException(403, "requer admin do servidor")
    return m


# ── Sync de membros Discord (bot → backend) ──────────────────────────────────

class BotMemberIn(BaseModel):
    user_id: int
    username: str
    global_name: str | None = None
    avatar: str | None = None
    discord_role_ids: list[str] = []
    is_guild_admin: bool = False


class BotMemberSyncIn(BaseModel):
    members: list[BotMemberIn]


@router.post("/bot/guilds/{guild_id}/members-sync")
def bot_members_sync(
    guild_id: int,
    body: BotMemberSyncIn,
    authorization: str = Header(...),
    db: Session = Depends(deps.db_session),
):
    """Bot envia a lista de membros do Discord (5min). O backend faz upsert de
    User + GuildMember — popula a tabela com TODOS os membros, não só os que
    logaram no site. O snapshot também é o fallback autoritativo das remoções
    humanas que algum evento do gateway não conseguiu entregar."""
    _require_bot_secret(authorization)
    if not body.members:
        return {"ok": True, "synced": 0}
    # Pass 1: upsert Users — flush antes de criar GuildMember porque
    # user_id é inteiro flat (não relationship), SQLAlchemy não ordena
    # INSERTs por dependência FK implícita e GuildMember vinha antes de User.
    for m in body.members:
        user = db.get(User, m.user_id)
        if user is None:
            db.add(User(
                id=m.user_id, username=m.username,
                global_name=m.global_name, avatar=m.avatar,
            ))
        else:
            user.username = m.username
            if m.global_name is not None:
                user.global_name = m.global_name
            if m.avatar is not None:
                user.avatar = m.avatar
    db.flush()
    # Pass 2: upsert GuildMembers — Users já estão no banco.
    for m in body.members:
        gm = db.scalar(select(GuildMember).where(
            GuildMember.guild_id == guild_id, GuildMember.user_id == m.user_id,
        ))
        if gm is None:
            db.add(GuildMember(
                guild_id=guild_id, user_id=m.user_id,
                discord_role_ids=m.discord_role_ids,
                is_guild_admin=m.is_guild_admin,
            ))
        else:
            gm.discord_role_ids = m.discord_role_ids
            gm.is_guild_admin = m.is_guild_admin
            gm.left_at = None
    db.flush()
    db.commit()
    return {"ok": True, "synced": len(body.members)}


@router.get("/guilds/{guild_id}/members/search")
def search_guild_members(
    guild_id: int,
    q: str = Query("", max_length=100),
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _member: GuildMember = Depends(deps.require_guild_member),
):
    """Busca membros do server por nome (autocomplete do site). `q` vazio
    retorna os primeiros 20 (listagem ao focar no campo). Com `q`, filtra
    por username/global_name/in_game_name E por nick de personagem registrado
    (BotRegistration.albion_player_name) — assim buscar "TankMaster" acha o
    user do Discord que registrou aquele char, mesmo que o nome do Discord
    seja completamente diferente."""
    stmt = (
        select(GuildMember, User)
        .join(User, GuildMember.user_id == User.id)
        .where(
            GuildMember.guild_id == guild_id,
            GuildMember.left_at.is_(None),
        )
    )
    if q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            func.lower(User.username).like(func.lower(pattern)),
            func.lower(User.global_name).like(func.lower(pattern)),
            func.lower(GuildMember.in_game_name).like(func.lower(pattern)),
            exists().where(
                BotRegistration.guild_id == guild_id,
                BotRegistration.discord_user_id == User.id,
                BotRegistration.active.is_(True),
                func.lower(BotRegistration.albion_player_name).like(func.lower(pattern)),
            ),
        ))
    rows = db.execute(stmt.order_by(User.global_name, User.username).limit(20)).all()
    # Busca nicks in-game ativos (BotRegistration) para os user_ids encontrados.
    # Um user pode ter múltiplos chars registrados — pega o mais recente (id desc).
    user_ids = [u.id for _gm, u in rows]
    ign_map: dict[int, str] = {}
    if user_ids:
        ign_rows = db.execute(
            select(BotRegistration.discord_user_id, BotRegistration.albion_player_name)
            .where(
                BotRegistration.guild_id == guild_id,
                BotRegistration.discord_user_id.in_(user_ids),
                BotRegistration.active.is_(True),
            )
            .order_by(BotRegistration.id.desc())
        ).all()
        for uid, name in ign_rows:
            if uid not in ign_map:
                ign_map[uid] = name
    return [
        {
            # Discord snowflakes não cabem em Number no browser. O autocomplete
            # devolve texto para o POST de participante preservar o ID inteiro.
            "user_id": str(gm.user_id),
            "username": u.username,
            "global_name": u.global_name,
            "avatar": _discord_avatar_url(u.id, u.avatar),
            "in_game_name": ign_map.get(u.id) or gm.in_game_name,
        }
        for gm, u in rows
    ]


def _discord_avatar_url(user_id: int, avatar: str | None) -> str | None:
    """Constrói a URL do avatar do Discord a partir do hash. O banco guarda
    apenas o hash (padrão do OAuth), não a URL completa."""
    if not avatar:
        return None
    ext = ".gif" if avatar.startswith("a_") else ".png"
    return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}{ext}"


# ── Energy Control (embed de saldos negativos) ───────────────────────────────
@router.get("/bot/guilds/{guild_id}/energy-control")
def bot_energy_control_get(guild_id: int, force: bool = False,
                           authorization: str = Header(...),
                           db: Session = Depends(deps.db_session)):
    """Devolve o estado atual do embed de energy-control: channel_id,
    message_id, rows (jogadores com saldo < threshold), dirty flag, e
    new_uids (jogadores que entraram na lista desde a última sync —
    o bot menciona esses 1x no content do reenvio).
    ?force=true ignora o dirty check (usado no catch_up pós-restart)."""
    _require_bot_secret(authorization)
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    settings = (g.settings or {}) if g else {}
    threshold = settings.get("energy_alert_threshold", 100)
    try:
        threshold = int(threshold)
    except (TypeError, ValueError):
        threshold = 100
    channel_id, message_id = energy_svc.get_control_message(db, guild_id)
    rows = energy_svc.control_rows(db, guild_id, threshold)
    current_uids = {r["user_id"] for r in rows}
    known_uids = set(settings.get("energy_control_known_uids") or [])
    # new_uids = quem está na lista agora mas não estava antes.
    # No force (catch_up pós-restart) não pinga ninguém — os known continuam known.
    if force:
        new_uids = set()
    else:
        new_uids = current_uids - known_uids
    return {
        "channel_id": str(channel_id) if channel_id else None,
        "message_id": str(message_id) if message_id else None,
        "threshold": threshold,
        "rows": rows,
        "new_uids": sorted(new_uids),
        "dirty": bool(force or settings.get("energy_control_dirty")),
    }


class BotEnergyControlSyncedIn(BaseModel):
    channel_id: str | None = None
    message_id: str | None = None
    known_uids: list[int] | None = None


@router.post("/bot/guilds/{guild_id}/energy-control/synced")
def bot_energy_control_synced(guild_id: int, body: BotEnergyControlSyncedIn,
                              authorization: str = Header(...),
                              db: Session = Depends(deps.db_session)):
    """Persiste channel_id + message_id, atualiza known_uids e limpa o dirty."""
    _require_bot_secret(authorization)
    ch = int(body.channel_id) if body.channel_id else None
    mid = int(body.message_id) if body.message_id else None
    energy_svc.set_control_message(db, guild_id, ch, mid)
    g = db.scalar(select(Guild).where(Guild.id == guild_id))
    if g is not None:
        settings = dict(g.settings or {})
        settings.pop("energy_control_dirty", None)
        if body.known_uids is not None:
            settings["energy_control_known_uids"] = body.known_uids
        g.settings = settings
    db.commit()
    return {"ok": True}
