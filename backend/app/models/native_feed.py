"""Estado durável e inbox cru dos feeds nativos do Albion."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, json_type, pk


class NativeFeedStream(Base):
    """Uma varredura ordenada por tipo de feed e região.

    O offset só existe enquanto uma varredura está ativa. A fronteira capturada
    permite continuar gravando itens recentes no inbox enquanto os antigos
    aguardam aplicação cronológica.
    """
    __tablename__ = "native_feed_streams"

    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    region: Mapped[str] = mapped_column(String(16), primary_key=True)
    completed_head_source_id: Mapped[str | None] = mapped_column(String(64))
    captured_head_source_id: Mapped[str | None] = mapped_column(String(64))
    scan_anchor_source_id: Mapped[str | None] = mapped_column(String(64))
    scan_anchor_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anchor_offset_estimate: Mapped[int | None] = mapped_column(Integer)
    anchor_offset_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_head_source_id: Mapped[str | None] = mapped_column(String(64))
    scan_id: Mapped[str | None] = mapped_column(String(36))
    scan_resolution: Mapped[str | None] = mapped_column(String(16))
    scan_phase: Mapped[str | None] = mapped_column(String(16))
    scan_last_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    search_low_offset: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    search_high_offset: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_offset: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    scan_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    scan_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    scan_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocked_reason: Mapped[str | None] = mapped_column(Text)


class NativeFeedItem(Base):
    """Item cru capturado antes de qualquer escrita nos modelos de domínio."""
    __tablename__ = "native_feed_items"
    __table_args__ = (
        UniqueConstraint("kind", "region", "source_id", name="uq_native_feed_item_source"),
        Index("ix_native_feed_items_apply", "kind", "region", "status", "occurred_at", "source_id"),
    )

    id: Mapped[int] = pk()
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    region: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(json_type(), nullable=False)
    # Crédito do scanner distribuído/companion, aplicado junto da batalha.
    discovered_by: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
