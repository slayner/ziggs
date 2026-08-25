"""Energia de guilda — saldo por membro Discord, ledger imutável e whitelist.

Porta tenant-scoped do sistema de energia do bot legado (bot-legacy/cogs/
energia.py + energy_log/energy_whitelist do database.py), fundação do portal
do membro. Núcleo em app/services/energy.py; autorização de membro ativo e
rotas API vivem fora daqui.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Snowflake, pk


class EnergyBalance(Base):
    __tablename__ = "energy_balances"
    __table_args__ = (UniqueConstraint("guild_id", "discord_user_id", name="uq_energy_balance_member"),)

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    discord_user_id: Mapped[int] = mapped_column(Snowflake, nullable=False, index=True)
    balance: Mapped[int] = mapped_column(BigInteger(), default=0, nullable=False)


class EnergyEntry(Base):
    """Ledger IMUTÁVEL (append-only, nunca update/delete): cada lançamento da
    log do jogo (kind='log') e cada ajuste manual compensatório (kind=
    'adjustment'). `balance == sum(amount)` por (guild, user) SEMPRE — quem
    precisa corrigir saldo emite lançamento compensatório, não reescreve.

    Dedup da log (mesma log colada 2x não conta em dobro) é o unique parcial
    (guild_id, ts, player, amount) ONDE kind='log' — mesma chave UNIQUE do
    bot legado, escopada por guilda. Ajustes manuais ficam fora dele.
    """
    __tablename__ = "energy_entries"
    __table_args__ = (
        Index(
            "uq_energy_entry_log_dedup", "guild_id", "ts", "player", "amount",
            unique=True,
            sqlite_where=text("kind = 'log'"),
            postgresql_where=text("kind = 'log'"),
        ),
    )

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    discord_user_id: Mapped[int] = mapped_column(Snowflake, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), default="log", nullable=False)
    ts: Mapped[str] = mapped_column(String(32), nullable=False)
    player: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    actor_discord_id: Mapped[int | None] = mapped_column(Snowflake, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EnergyWhitelist(Base):
    """Quem cuida da energia da guilda (saldo muito afetado pelo próprio
    trabalho) é ignorado por completo no processamento das logs — não
    registra e não aplica, igual ao bot legado."""
    __tablename__ = "energy_whitelist"
    __table_args__ = (UniqueConstraint("guild_id", "discord_user_id", name="uq_energy_whitelist_member"),)

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    discord_user_id: Mapped[int] = mapped_column(Snowflake, nullable=False, index=True)
    added_by: Mapped[int | None] = mapped_column(Snowflake, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EnergyControlMessage(Base):
    """Singleton do embed de energy-control por guilda: canal + message_id.
    O bot edita in-place a cada atualização (padrão mass-info)."""
    __tablename__ = "energy_control_messages"

    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), primary_key=True
    )
    channel_id: Mapped[int | None] = mapped_column(Snowflake, nullable=True)
    message_id: Mapped[int | None] = mapped_column(Snowflake, nullable=True)
