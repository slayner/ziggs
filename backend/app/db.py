"""Engine + sessão SQLAlchemy ASSÍNCRONA.

PostgreSQL 16 via psycopg3 async (`postgresql+psycopg_async`). A migração de
sync pra async elimina o gargalo que travava o backend por minutos: 25 bg
tasks + handlers async faziam DB síncrono no mesmo event loop — cada
commit/query bloqueava tudo. Com AsyncSession, cada `await db.execute()`
devolve o controle pro loop durante o I/O do banco.

Compatibilidade: serviços que ainda usam `SessionLocal()` síncrono continuam
funcionando via `_sync_engine` + `SyncSessionLocal` (bridge temporária durante
a migração_progressiva arquivo-por-arquivo). Quando todos os callers forem
async, remover o sync engine.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_settings = get_settings()

# ── Engine ASSÍNCRONA (alvo) ────────────────────────────────────────────────
# Troca o driver: postgresql+psycopg → postgresql+psycopg_async.
# psycopg3 tem suporte nativo a async (AsyncConnection), sem dependência extra.
_async_url = _settings.database_url.replace(
    "postgresql+psycopg://", "postgresql+psycopg_async://"
)

# pool_size=20 + max_overflow=10: cobre ~22 bg tasks + requests simultâneos.
async_engine: AsyncEngine = create_async_engine(
    _async_url,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=10,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    autoflush=False,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_async_session() -> AsyncIterator[AsyncSession]:
    """Dependency do FastAPI: abre uma sessão async por request."""
    async with AsyncSessionLocal() as db:
        yield db


# ── Engine SÍNCRONA (bridge — remover quando migração completar) ────────────
# Serviços ainda não migrados continuam usando SyncSessionLocal().
# Mantido no MESMO arquivo pra que a remoção seja um grep, não uma caçada.
_sync_engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    future=True,
    pool_size=20,
    max_overflow=10,
)

# ponytail: alias mantido para scripts legados que fazem `from app.db import engine`.
engine = _sync_engine

SyncSessionLocal = sessionmaker(bind=_sync_engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Bridge síncrona — NÃO usar em handlers async (bloqueia o event loop).
    Usar get_async_session() em rotas async; SyncSessionLocal() em bg tasks
    ainda não migradas (rodam em threadpool via asyncio.to_thread)."""
    db = SyncSessionLocal()
    try:
        yield db
    finally:
        db.close()