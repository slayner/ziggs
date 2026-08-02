"""Engine + sessão SQLAlchemy.

PostgreSQL 16 local em dev (instalado em C:\Program Files\PostgreSQL\16).
SQLite foi removido: com 22+ background tasks fazendo leituras longas e
escritas concorrentes, o write lock do SQLite causava 'database is locked'
por minutos e WAL de 16GB+. PostgreSQL lida com concorrência real de
escritas e leituras sem esses problemas.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_settings = get_settings()

# pool_size=20 + max_overflow=10: cobre ~22 bg tasks + requests simultâneos.
engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    future=True,
    pool_size=20,
    max_overflow=10,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Dependency do FastAPI: abre uma sessão por request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
