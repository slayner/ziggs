"""Scan workers — registry de VPS workers + fila de ranges de IDs pra sondar.

Distribui o scanning pesado da API do Albion pra 4 VPS tunnel. O backend
vira coordenador: gera ranges a partir dos buracos da sequência por região
(mesma lógica do battle_sweeper/companion_scan), os workers reivindicam,
sondam a API pública e reportam. O backend revalida tudo contra a Albion
no report (upsert_battle_light) — nunca confia no client.

Sem auth. Workers usam header X-Scan-Secret (segredo compartilhado).
"""
from __future__ import annotations

from datetime import timedelta

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigInt, pk


# Worker sem heartbeat dentro disso = marcado 'dead' e seus claims liberados.
WORKER_HEARTBEAT_TIMEOUT = timedelta(seconds=90)
# Claim de tarefa volta a 'pending' depois disso (worker caiu no meio).
WORK_CLAIM_TTL = timedelta(seconds=60)
# Tamanho de cada range de IDs enviado a um worker por claim.
WORK_RANGE_SIZE = 50
# Regiões suportadas (precisa bater com player_tracker.HOSTS).
SCAN_REGIONS = ("americas", "europe", "asia")
# Teto de tarefas pending por região (gera mais conforme consome).
MAX_PENDING_PER_REGION = 100


class ScanWorker(Base):
    """Um VPS worker registrado. Identidade por worker_id (hostname/UUID setado
    pelo VPS), não por auth. last_heartbeat decide vivo/morto."""
    __tablename__ = "scan_workers"

    id: Mapped[int] = pk()
    worker_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    region_pref: Mapped[str | None] = mapped_column(String(16), index=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)
    total_tasks_done: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_battles_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_missing: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_task_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ScanWorkTask(Base):
    """Um range de IDs de batalha pra sondar, por região.

    status:
      'pending'  — aguardando um worker pegar
      'claimed'   — um worker pegou (claim_expires_at no futuro)
      'done'      — reportado e revalidado
      'failed'    — claim expirou sem report (volta a 'pending' no próximo claim)
    """
    __tablename__ = "scan_work_tasks"

    id: Mapped[int] = pk()
    region: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    battle_id_start: Mapped[int] = mapped_column(BigInt(), nullable=False)
    battle_id_end: Mapped[int] = mapped_column(BigInt(), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    claimed_by: Mapped[str | None] = mapped_column(String(64), index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    found_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    missing_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)