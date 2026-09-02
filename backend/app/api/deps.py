"""
Dependências das rotas.

A identidade vem da SESSÃO do Discord (cookie assinado). A autorização por cargo
(council/logistic/...) entra depois, junto com a sincronização de membros pelo bot.

DB: rotas async usam ``async_db_session()`` (AsyncSession). Rotas ``def``
síncronas e deps que só fazem 1 query pontual continuam com ``db_session()``
(Session sync) — rodam em threadpool do FastAPI, não bloqueiam o event loop.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
import secrets

from fastapi import Depends, Header, HTTPException, Path, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.auth import discord
from app.auth.crypto import decrypt_token
from app.auth.permissions import has_permission
from app.auth.session import verify_session
from app.config import get_settings
from app.db import get_async_session, get_session
from app.models.tenancy import Guild, GuildMember, User


# Rotas async — usar esta. Cada ``await db.execute/scalars(...)`` devolve o
# controle pro event loop durante o I/O do Postgres (não bloqueia outras
# tasks/requests).
async def async_db_session() -> AsyncIterator[AsyncSession]:
    async for db in get_async_session():
        yield db


# Rotas ``def`` síncronas — FastAPI roda em threadpool, não bloqueia o loop.
# NÃO usar em handlers ``async def`` (bloqueia o loop inteiro).
def db_session() -> Iterator[Session]:
    yield from get_session()


def optional_user(
    request: Request,
    db: Session = Depends(db_session),
) -> User | None:
    """Usuário logado, ou None se não houver sessão válida."""
    token = request.cookies.get(get_settings().session_cookie_name)
    uid = verify_session(token)
    if uid is None:
        return None
    return db.scalar(select(User).where(User.id == uid))


def require_user(user: User | None = Depends(optional_user)) -> User:
    """Exige usuário logado (401 caso contrário)."""
    if user is None:
        _dbg("require_user: NO SESSION -> 401")
        raise HTTPException(status_code=401, detail="não autenticado")
    return user


def current_user_id(user: User | None = Depends(optional_user)) -> int | None:
    """Id do usuário logado para auditoria (None se anônimo)."""
    return user.id if user else None


def tenant_guild(
    guild_id: int = Path(...),
    db: Session = Depends(db_session),
) -> Guild:
    """Resolve a guilda do path e garante que ela existe (isolamento multi-tenant)."""
    guild = db.scalar(select(Guild).where(Guild.id == guild_id))
    if guild is None:
        raise HTTPException(status_code=404, detail="guilda não encontrada")
    return guild


def require_guild_member(
    guild_id: int = Path(...),
    user: User = Depends(require_user),
    db: Session = Depends(db_session),
) -> GuildMember:
    """Exige que o usuário logado seja MEMBRO da guilda do path (403 caso contrário).

    Sem isso, `tenant_guild` só confere que a guilda existe — qualquer pessoa
    logada (ou nem isso) conseguiria operar dados de qualquer guilda só sabendo
    o guild_id (snowflake do Discord, não é segredo).
    """
    member = db.scalar(select(GuildMember).where(
        GuildMember.guild_id == guild_id, GuildMember.user_id == user.id,
    ))
    if member is None:
        raise HTTPException(status_code=403, detail="sem acesso a essa guilda")
    return member


def require_active_guild_member(
    member: GuildMember = Depends(require_guild_member),
) -> GuildMember:
    """Membro ATIVO da guilda do path: além de existir (require_guild_member),
    `left_at` deve ser NULL — quem saiu do servidor Discord não acessa o portal
    do membro. Usado SÓ nas rotas /guilds/{guild_id}/member/* (portal do membro);
    as rotas administrativas legadas continuam com require_guild_member."""
    if member.left_at is not None:
        raise HTTPException(status_code=403, detail="membro não está mais na guilda")
    return member


def require_permission(key: str):
    """Factory de dependência: exige a permissão `key` (ou admin de servidor) na
    guilda do path. Usa a mesma lógica de app/auth/permissions.py que alimenta
    /auth/my-permissions — o que a API aplica é o que a UI mostra."""
    def _check(
        member: GuildMember = Depends(require_guild_member),
        db: Session = Depends(db_session),
    ) -> GuildMember:
        if not member.is_guild_admin and not has_permission(db, member, key):
            raise HTTPException(status_code=403, detail=f"permissão '{key}' necessária")
        return member
    return _check


def require_bot_api(authorization: str = Header(...)) -> bool:
    """Auth bot↔site: Bearer BOT_API_SECRET (compare_digest, anti timing-leak).
    Usado pelo endpoint de ingestão de screenshots de regear (sem cookie)."""
    if not secrets.compare_digest(authorization, f"Bearer {get_settings().bot_api_secret}"):
        raise HTTPException(status_code=401, detail="bot api secret inválido")
    return True


# ponytail: debug temporário do 403 da escalação — só grava em DEVELOPMENT
# (em prod vira no-op: logava IDs de usuário num arquivo de crescimento
# ilimitado). Remover de vez quando o 403 for confirmado morto após o reboot.
import os, time
def _dbg(*a):
    if get_settings().environment != "development":
        return
    try:
        with open(os.path.join(os.path.dirname(__file__), "..", "_debug_escalacao.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a) + "\n")
    except Exception:
        pass


def verify_guild_membership(user: User, guild_id: int) -> tuple[str | None, str | None, bool, list[int]]:
    """Confirma junto ao DISCORD (nunca ao body do cliente) que `user` é membro
    de `guild_id`, e deriva (name, icon, is_admin, role_ids) de lá. Sem acesso
    a DB — puramente verificação externa, para ser chamada ANTES de qualquer
    write (select-guild, switch-guild).

    Filiação é confirmada pelo TOKEN DO BOT (não expira; o bot tá no server) —
    fonte primária. Só cai no token OAuth do user (que expira) se o bot não
    estiver no server. 403 se o user não é membro; 502 se nenhuma fonte responde."""
    s = get_settings()
    name: str | None = None
    icon: str | None = None
    is_admin = False
    role_ids: list[int] = []

    # 1) Bot token — fonte primária. fetch_guild dá name/icon/owner/roles de uma
    #    vez; fetch_guild_member_bot dá as roles do user. Se o bot NÃO tá no
    #    server, cai pro passo 2.
    if s.discord_bot_token:
        try:
            ginfo = discord.fetch_guild(str(guild_id), s.discord_bot_token)
        except Exception:
            ginfo = None
        if ginfo is not None:
            try:
                mdata = discord.fetch_guild_member_bot(str(guild_id), str(user.id), s.discord_bot_token)
            except Exception:
                mdata = None
            if mdata is None:
                # bot no server mas o user não é membro — decisivo, nem tenta o
                # token do user.
                raise HTTPException(403, "sem acesso a essa guilda")
            role_perms = {str(r["id"]): int(r.get("permissions", 0)) for r in ginfo.get("roles", [])}
            role_ids = [int(r) for r in mdata.get("roles", [])]
            ADMIN = 0x8  # ADMINISTRATOR implica todas as perms
            is_admin = (str(ginfo.get("owner_id")) == str(user.id)) or any(
                role_perms.get(str(rid), 0) & ADMIN for rid in role_ids
            )
            return ginfo.get("name"), ginfo.get("icon"), is_admin, role_ids

    # 2) Token OAuth do user — só se o bot não estiver no server. O token expira
    #    (o cookie de sessão não), então pode falhar com 401 → 502.
    token = decrypt_token(user.discord_access_token)
    if not token:
        raise HTTPException(403, "sem acesso a essa guilda")
    try:
        gds = discord.fetch_guilds(token)
    except Exception:
        raise HTTPException(502, "erro ao confirmar filiação à guilda")
    g = next((x for x in gds if int(x["id"]) == guild_id), None)
    if g is None:
        raise HTTPException(403, "sem acesso a essa guilda")
    name = g.get("name")
    icon = g.get("icon")
    is_admin = bool(int(g.get("permissions", 0)) & 0x20)  # MANAGE_GUILD
    try:
        role_ids = [int(r) for r in discord.fetch_guild_member(str(guild_id), token).get("roles", [])]
    except Exception:
        pass
    return name, icon, is_admin, role_ids


def _provision_member(db: Session, user: User, guild_id: int) -> GuildMember:
    """Garante a row Guild + GuildMember pra um user que é membro do server
    Discord. A página de escalação é deep link e precisa abrir pra quem é do
    server mesmo sem nunca ter selecionado a guilda no site (caso "bot em server
    público").

    Dep `def` (não async) → roda em threadpool, não bloqueia o event loop."""
    member = db.scalar(select(GuildMember).where(
        GuildMember.guild_id == guild_id, GuildMember.user_id == user.id,
    ))
    if member is not None:
        _dbg("provision HIT existing member user=", user.id, "guild=", guild_id, "is_admin=", member.is_guild_admin)
        return member

    _dbg("provision MISS user=", user.id, "guild=", guild_id, "bot_token_set=", bool(get_settings().discord_bot_token))
    name, icon, is_admin, role_ids = verify_guild_membership(user, guild_id)

    guild = db.scalar(select(Guild).where(Guild.id == guild_id))
    if guild is None:
        guild = Guild(id=guild_id, name=name or "", icon=icon)
        db.add(guild)
    else:
        if name:
            guild.name = name
        if icon:
            guild.icon = icon
    member = GuildMember(guild_id=guild_id, user_id=user.id, is_guild_admin=is_admin)
    if role_ids:
        member.discord_role_ids = role_ids
    db.add(member)
    db.flush()
    db.commit()
    db.refresh(member)
    return member


def require_permission_provisioning(key: str):
    """Igual require_permission, mas auto-provisiona a row Guild + GuildMember
    se o user for membro do server Discord. Usado só pelas rotas de escalação
    (deep link), que precisam abrir pra quem nunca selecionou a guilda no site."""
    def _check(
        guild_id: int = Path(...),
        user: User = Depends(require_user),
        db: Session = Depends(db_session),
    ) -> GuildMember:
        _dbg("perm_check key=", key, "guild=", guild_id, "user=", user.id)
        member = _provision_member(db, user, guild_id)
        if not member.is_guild_admin and not has_permission(db, member, key):
            _dbg("perm_check DENY 403 key=", key, "user=", user.id, "guild=", guild_id, "is_admin=", member.is_guild_admin)
            raise HTTPException(403, detail=f"permissão '{key}' necessária")
        return member
    return _check
