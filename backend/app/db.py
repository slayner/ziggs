"""Engine + sessão SQLAlchemy (síncrono por enquanto; simples e suficiente)."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import event, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_settings = get_settings()

# SQLite (dev local) precisa de check_same_thread=False porque o FastAPI roda
# endpoints síncronos em várias threads. timeout=60: espera até 60s pelo lock
# em vez de falhar imediatamente quando há writes concorrentes dos background
# tasks. Subido de 30→60 após o dashboard_cache (transação de leitura longa de
# 45 batalhas × 3 regiões) segurar snapshot por vários segundos e fazer
# commiters concorrentes (sweeper/tracker) estourarem busy_timeout durante
# auto-checkpoint do WAL.
_is_sqlite = _settings.database_url.startswith("sqlite")
_connect_args = {"check_same_thread": False, "timeout": 60} if _is_sqlite else {}
# pool_size=20 + max_overflow=10: ~9 background tasks (bg pool do albion_gate)
# + threads de request compartilham este pool; o default (5) deixava request
# de perfil esperando vaga de conexão atrás de backfill/warmer sob carga.
# SQLite/WAL tolera vários read handles; Postgres então com folga.
engine = create_engine(
    _settings.database_url, pool_pre_ping=True, future=True,
    pool_size=20, max_overflow=10,
    connect_args=_connect_args,
)

if _is_sqlite:
    # WAL mode: leitores não bloqueiam escritores e vice-versa — elimina a
    # maior parte do "database is locked" com múltiplos background tasks.
    @event.listens_for(engine, "connect")
    def _set_sqlite_wal(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Dependency do FastAPI: abre uma sessão por request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
