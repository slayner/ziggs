"""Renders da CDN que ainda não existem e precisam de nova tentativa."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RenderMiss(Base):
    __tablename__ = "render_misses"

    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    quality: Mapped[int] = mapped_column(Integer, primary_key=True, default=0)
    size: Mapped[int] = mapped_column(Integer, primary_key=True, default=0)
    miss_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_missing_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    last_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
