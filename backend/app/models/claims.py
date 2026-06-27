"""Modelos de reivindicação e registro de personagens."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, json_type, pk


class CharacterClaim(Base):
    __tablename__ = "character_claims"

    id: Mapped[int] = pk()
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    albion_player_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    albion_player_name: Mapped[str] = mapped_column(String(255), nullable=False)
    region: Mapped[str] = mapped_column(String(16), nullable=False)
    # [{"slot": "Food", "item_id": "T1_MEAL_STEW", "quantity": 47}, ...]
    challenge: Mapped[list[dict]] = mapped_column(json_type(), nullable=False)
    # "pending" | "verified"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_event_id: Mapped[str | None] = mapped_column(String(64))


class RegisteredCharacter(Base):
    __tablename__ = "registered_characters"

    id: Mapped[int] = pk()
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    albion_player_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    albion_player_name: Mapped[str] = mapped_column(String(255), nullable=False)
    region: Mapped[str] = mapped_column(String(16), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    claim_id: Mapped[int] = mapped_column(
        ForeignKey("character_claims.id"), nullable=False
    )
