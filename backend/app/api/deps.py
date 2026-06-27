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

from app.auth.session import verify_session
from app.config import get_settings
from app.db import get_session
from app.models.tenancy import Guild, User


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
