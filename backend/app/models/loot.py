"""
Loot de eventos: itens looteados pelos jogadores e inventário do baú da guilda.

O bot faz o parsing dos arquivos (.txt do jogo para o baú, formato do programa
terceiro para o log de combate) e envia JSON estruturado para a API. O backend
cuida apenas do armazenamento e da reconciliação (loot vs baú).

A reconciliação é por item_type + quantidade total — não por jogador nem por quem
depositou. Se o baú tem qty >= looteado, o item está coberto.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigInt, Snowflake, TimestampMixin, pk


class EventLootEntry(Base, TimestampMixin):
    """Item looteado por um jogador durante o evento."""
    __tablename__ = "event_loot_entries"

    id: Mapped[int] = pk()
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )

    looted_by_name: Mapped[str] = mapped_column(String(64), nullable=False)
    looted_by_user_id: Mapped[int | None] = mapped_column(Snowflake)

    item_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    item_name: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    silver_value: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)

    in_chest: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str | None] = mapped_column(String(32))  # "bot_upload" | "manual"


class GuildChestEntry(Base, TimestampMixin):
    """Item presente no baú da guilda no momento do snapshot."""
    __tablename__ = "guild_chest_entries"

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True
    )

    deposited_by_name: Mapped[str | None] = mapped_column(String(64))
    deposited_by_user_id: Mapped[int | None] = mapped_column(Snowflake)

    item_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    item_name: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    silver_value: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)

    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LootVerification(Base, TimestampMixin):
    """Marca que um item 'não depositado' de um looter foi CONFERIDO por um admin
    (o vermelho vira amarelo na tela do reconcile). Chave por (evento, looter,
    item) — clicar cria, clicar de novo remove (toggle na rota)."""
    __tablename__ = "loot_verifications"
    __table_args__ = (
        UniqueConstraint("event_id", "looted_by", "item_id", name="uq_loot_verif"),
    )

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    looted_by: Mapped[str] = mapped_column(String(64), nullable=False)
    item_id: Mapped[str] = mapped_column(String(128), nullable=False)
    verified_by_user_id: Mapped[int | None] = mapped_column(Snowflake)


class ItemPriceCache(Base):
    """Cache de preços da API albion-online-data.com (TTL de 1h)."""
    __tablename__ = "item_price_cache"

    item_type: Mapped[str] = mapped_column(String(128), primary_key=True)
    silver_value: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
