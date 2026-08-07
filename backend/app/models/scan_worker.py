"""Scan workers — registry de VPS workers + fila de tasks de feed polling.

Distribui o polling da API do Albion pra VPS tunnel. O backend vira
coordenador: gera tasks de "feed" (poll de batalhas recentes ou kill events
por região), os workers reivindicam, buscam a página da API e reportam os
dados crus. O backend faz upsert (nunca confia no client, mas as VPS são
nossas — o dado vem direto da API pública).

Sem auth. Workers usam header X-Scan-Secret (segredo compartilhado).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, pk


WORKER_HEARTBEAT_TIMEOUT = timedelta(seconds=90)
WORK_CLAIM_TTL = timedelta(seconds=120)
FEED_PAGE_SIZE = 51
SCAN_REGIONS = ("americas", "europe", "asia")
MAX_PENDING_PER_REGION = 50


class ScanWorker(Base):
    __tablename__ = "scan_workers"

    id: Mapped[int] = pk()
    worker_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    region_pref: Mapped[str | None] = mapped_column(String(16), index=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)
    total_tasks_done: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_battles_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_kills_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_missing: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_task_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ScanWorkTask(Base):
    """Task de feed polling: buscar uma página do feed de batalhas ou kills.

    feed_type:
      'battles' — GET /api/gameinfo/battles?sort=recent&limit=51&offset={offset}
      'kills'   — GET /api/gameinfo/events?limit=51&offset={offset}

    status:
      'pending'  — aguardando um worker pegar
      'claimed'  — um worker pegou (claim_expires_at no futuro)
      'done'     — reportado
    """
    __tablename__ = "scan_work_tasks"

    id: Mapped[int] = pk()
    region: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    feed_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    page_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    claimed_by: Mapped[str | None] = mapped_column(String(64), index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    found_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    missing_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)