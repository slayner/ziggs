"""Registro de membros via bot (/register) — sem relação com claims do site."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint, func
from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Snowflake, pk


class BotRegistration(Base):
    """Um personagem Albion vinculado a um membro Discord via /register.

    Único por (guild_id, albion_player_id) — uma única linha por personagem,
    reaproveitada entre ciclos de entrada/saída da guild (em vez de acumular
    histórico). Enquanto `active`, um novo /register pro mesmo personagem é
    ignorado (não importa quem peça) — é o "já existe um membro com esse nome
    registrado" pedido. `active=False` libera o personagem pra um novo
    registro (saiu da guild ou foi removido pelo check periódico).
    """
    __tablename__ = "bot_registrations"
    __table_args__ = (UniqueConstraint("guild_id", "albion_player_id", name="uq_bot_reg_character"),)

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    discord_user_id: Mapped[int] = mapped_column(Snowflake, nullable=False, index=True)
    albion_player_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    albion_player_name: Mapped[str] = mapped_column(String(255), nullable=False)
    region: Mapped[str] = mapped_column(String(16), nullable=False)
    role_id: Mapped[int] = mapped_column(Snowflake, nullable=False)
    # True se registrado pela rota de aliado (guilda diferente, mesma aliança)
    # em vez de membro direto — o check periódico revalida cada caso diferente.
    is_ally: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
