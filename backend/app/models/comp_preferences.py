"""Preferências de roles de um jogador por composição."""
from __future__ import annotations

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Snowflake, TimestampMixin, pk


class CompRolePreference(Base, TimestampMixin):
    """Role que o jogador sabe/faz em uma composição.

    A tabela guarda apenas escolhas ativas. O snapshot usado em um evento
    continua em ``EventSignup.functions`` para preservar o histórico.
    """

    __tablename__ = "comp_role_preferences"
    __table_args__ = (
        UniqueConstraint("guild_id", "comp_id", "user_id", "game_role_id"),
    )

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    comp_id: Mapped[int] = mapped_column(
        ForeignKey("comps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(Snowflake, nullable=False, index=True)
    game_role_id: Mapped[int] = mapped_column(
        ForeignKey("game_roles.id", ondelete="CASCADE"), nullable=False, index=True
    )
