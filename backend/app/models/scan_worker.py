"""Scan workers — registry de VPS workers + fila de tasks de feed polling.

Distribui o polling da API do Albion pra VPS tunnel. O backend vira
coordenador: gera tasks de "feed" (poll de batalhas recentes ou kill events
por região), os workers reivindicam, buscam a página da API e reportam os
dados crus. O backend faz upsert (nunca confia no client, mas as VPS são
nossas — o dado vem direto da API pública).

Workers registram com segredo de bootstrap e usam credencial individual.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigInt, json_type, pk


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
    api_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    credential_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # ─── Tunnel metadata — opcional, enviado no /scan/register.
    # Quando preenchido, a VPS aparece no /vps-manifest.json (companion + site).
    # VPS sem tunnel (worker puro de scan) deixa esses campos vazios.
    vps_label: Mapped[str | None] = mapped_column(String(64))
    vps_country: Mapped[str | None] = mapped_column(String(64))
    vps_endpoint: Mapped[str | None] = mapped_column(String(128))
    vps_server_pubkey: Mapped[str | None] = mapped_column(String(128))
    vps_ping_url: Mapped[str | None] = mapped_column(String(256))


class ScanWorkTask(Base):
    """Task de feed polling ou deep-process delegado.

    feed_type:
      'battles'      — GET /api/gameinfo/battles?sort=recent&limit=51&offset={offset}
      'kills'        — GET /api/gameinfo/events?limit=51&offset={offset}
      'deep_process' — deep-process de batalha light: busca detail + events
                       paginados de /api/gameinfo/battles/{id} + /events/battle/{id}.
                       page_offset guarda o battle.id (não o offset do feed).

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
    lease_token: Mapped[str | None] = mapped_column(String(32), index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    found_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    missing_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lap_id: Mapped[int | None] = mapped_column(BigInt(), index=True)


class ScanStreamState(Base):
    __tablename__ = "scan_stream_states"
    __table_args__ = (UniqueConstraint("region", "feed_type", name="uq_scan_stream_state"),)

    id: Mapped[int] = pk()
    region: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    feed_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    circuit_state: Mapped[str] = mapped_column(String(16), default="closed", nullable=False, index=True)
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    recent_pages: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    window_items_per_min: Mapped[float | None] = mapped_column(Float())
    last_head_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    opened_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ScanLap(Base):
    __tablename__ = "scan_laps"

    id: Mapped[int] = pk()
    region: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    feed_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)
    expected_pages: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_pages: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    page_stride: Mapped[int] = mapped_column(Integer, default=51, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ScanIngestPayload(Base):
    """Durable raw report waiting for bounded ingestion outside the HTTP request."""
    __tablename__ = "scan_ingest_payloads"

    id: Mapped[int] = pk()
    task_id: Mapped[int] = mapped_column(BigInt(), nullable=False, unique=True, index=True)
    worker_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    region: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    feed_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    payload: Mapped[list] = mapped_column(json_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text())


class ScanIncident(Base):
    __tablename__ = "scan_incidents"

    id: Mapped[int] = pk()
    event: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), index=True)
    region: Mapped[str | None] = mapped_column(String(16), index=True)
    feed_type: Mapped[str | None] = mapped_column(String(16), index=True)
    task_id: Mapped[int | None] = mapped_column(BigInt(), index=True)
    details: Mapped[dict | None] = mapped_column(json_type())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class ScanWorkerRegionMetric(Base):
    __tablename__ = "scan_worker_region_metrics"
    __table_args__ = (
        UniqueConstraint("worker_id", "region", name="uq_scan_worker_region_metric"),
    )

    id: Mapped[int] = pk()
    worker_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    region: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    samples: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    successes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ewma_latency_ms: Mapped[float | None] = mapped_column(Float())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
