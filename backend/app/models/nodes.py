"""Tracking de nodes do Albion (Black Zone) — calendário por guilda.

Fonte da verdade do subsistema de nodes (substitui `bot/database.py` tabelas
`node_*` do bot-v1). O bot-v2 só renderiza o calendário embed e faz proxy HTTP
via `/bot/guilds/{g}/nodes/*`; o site expõe o dashboard/guia em
`/guilds/{g}/nodes/*`.

- `NodeDef`: tipo de node configurável por guilda (nome/emoji/peso-scout/sort).
- `NodeEvent`: node vivo no calendário (podado quando `spawn_at` passa de 1h).
- `NodeEventLog`: auditoria permanente de todo node adicionado — base p/ scout
  payment e "nodes próximos do CTA" (`near_cta`).
- `NodeMap` / `NodeMapExclusion`: mapas extras além da BZ embutida e BZ ocultada.
- `NodeCalendar`: singleton do calendário por guilda (channel_id + message_id).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Snowflake, TimestampMixin, pk


class NodeDef(Base, TimestampMixin):
    """Tipo de node por guilda (ex.: 'Avalonian Road', 'Corrupted Dungeon').
    `name` é a chave armazenada em `node_events.node_type`. `weight` (0..1+)
    é o peso do scout no pagamento."""
    __tablename__ = "node_defs"
    __table_args__ = (UniqueConstraint("guild_id", "name", name="uq_node_defs_guild_name"),)

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    emoji: Mapped[str | None] = mapped_column(String(32))
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False, server_default="1.0")
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")


class NodeEvent(Base, TimestampMixin):
    """Node vivo no calendário (upcoming). Podado quando `spawn_at < now - 1h`."""
    __tablename__ = "node_events"

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_id: Mapped[int | None] = mapped_column(Snowflake)
    node_type: Mapped[str] = mapped_column(String(128), nullable=False)
    map_name: Mapped[str] = mapped_column(String(128), nullable=False)
    spawn_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    added_by_id: Mapped[int | None] = mapped_column(Snowflake)
    added_by_name: Mapped[str | None] = mapped_column(String(255))


class NodeEventLog(Base):
    """Auditoria permanente de cada node adicionado — base p/ scout payment e
    `near_cta`. Um row por adição (não deduplica).

    Em review o gestor marca `captured` e digita `sold_value`; `event_id` liga o
    node ao evento (na primeira captura) pro scout payout: o scout
    (`scout_id`/`scout_name`, quem adicionou o node) recebe `NodeDef.weight ×
    sold_value` — pool separado da tab."""
    __tablename__ = "node_event_log"

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_type: Mapped[str] = mapped_column(String(128), nullable=False)
    map_name: Mapped[str] = mapped_column(String(128), nullable=False)
    spawn_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    scout_id: Mapped[int | None] = mapped_column(Snowflake)
    scout_name: Mapped[str | None] = mapped_column(String(255))
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Captura em review: liga o node ao evento + valor vendido (scout payout).
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    captured: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    sold_value: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False, server_default="0"
    )


class NodeMap(Base, TimestampMixin):
    """Mapa extra além da Black Zone embutida (`BLACKZONE_MAPS`)."""
    __tablename__ = "node_maps"
    __table_args__ = (UniqueConstraint("guild_id", "map_name", name="uq_node_maps_guild_name"),)

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    map_name: Mapped[str] = mapped_column(String(128), nullable=False)


class NodeMapExclusion(Base, TimestampMixin):
    """Mapa da BZ embutida ocultado pelo `/removenodemap`."""
    __tablename__ = "node_map_exclusions"
    __table_args__ = (UniqueConstraint("guild_id", "map_name", name="uq_node_map_excl_guild_name"),)

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    map_name: Mapped[str] = mapped_column(String(128), nullable=False)


class NodeCalendar(Base, TimestampMixin):
    """Singleton do calendário por guilda: canal + mensagem embed persistente."""
    __tablename__ = "node_calendar"

    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), primary_key=True
    )
    channel_id: Mapped[int | None] = mapped_column(Snowflake)
    message_id: Mapped[int | None] = mapped_column(Snowflake)