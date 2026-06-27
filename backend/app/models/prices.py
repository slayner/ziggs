"""
Histórico de preços de itens do Albion Online (fonte: albiondata API).

`item_prices` é append-only — nunca deletamos, só inserimos.
`item_prices_latest` é um upsert: sempre tem só 1 linha por (item_id, city, quality),
facilitando lookups rápidos sem precisar de MAX(recorded_at).

Cidades principais: Caerleon, Bridgewatch, Fort Sterling, Lymhurst, Martlock, Thetford.
Qualidade 1 = Normal (a mais comum nos drops de ZvZ).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigInt, TimestampMixin, pk


class ItemPrice(Base):
    """Registro histórico de preço — append-only, nunca deletado."""
    __tablename__ = "item_prices"

    id: Mapped[int] = pk()
    item_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    city: Mapped[str] = mapped_column(String(48), nullable=False)
    quality: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    sell_price_min: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    price_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ItemPriceLatest(Base):
    """Último preço conhecido — upsert a cada sync, 1 linha por (item_id, city, quality)."""
    __tablename__ = "item_prices_latest"
    __table_args__ = (UniqueConstraint("item_id", "city", "quality"),)

    id: Mapped[int] = pk()
    item_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    city: Mapped[str] = mapped_column(String(48), nullable=False)
    quality: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    sell_price_min: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    price_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
