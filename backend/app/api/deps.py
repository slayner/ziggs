"""
Dependências das rotas.

A identidade vem da SESSÃO do Discord (cookie assinado). A autorização por cargo
(council/logistic/...) entra depois, junto com a sincronização de membros pelo bot.
"""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Path, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.permissions import has_permission
from app.auth.session import verify_session
from app.config import get_settings
from app.db import get_session
from app.models.tenancy import Guild, GuildMember, User


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
