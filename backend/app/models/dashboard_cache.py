"""Cache pré-computado do dashboard (recent battles + weekly highlights) —
atualizado a cada 1min por app.services.dashboard_cache, lido pelas rotas
sem recomputar a query pesada a cada visita."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, json_type


class DashboardCache(Base):
    __tablename__ = "dashboard_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict | list] = mapped_column(json_type(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
