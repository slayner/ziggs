"""
Audit log APPEND-ONLY — "registra TODA mudança que o bot sofreu/causou".

Uma linha por mudança sensível, venha do site, do bot ou de uma rotina
automática. Nunca se faz UPDATE/DELETE aqui: o passado é imutável. O Discord
(canal de logs) passa a ser apenas UMA das visões deste log, não a fonte.

`before`/`after` guardam o diff em JSONB — assim qualquer entidade pode ser
auditada sem uma tabela de log por tipo. `event_id` é opcional (nem toda mudança
é de evento: também registramos saldo, nodes, registro, etc.).

Equivalente central ao `economy_logs`/`role_change_log` do bot, unificado.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Snowflake, json_type, pk


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_guild_created", "guild_id", "created_at"),
        Index("ix_audit_entity", "entity", "entity_id"),
    )

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    # Quem causou e de onde.
    actor_id: Mapped[int | None] = mapped_column(Snowflake)   # NULL = sistema
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)  # site|bot|system
    source: Mapped[str] = mapped_column(String(16), nullable=False)

    # O que mudou.
    action: Mapped[str] = mapped_column(String(64), nullable=False)  # ex.: event.transition
    entity: Mapped[str] = mapped_column(String(48), nullable=False)  # ex.: event, balance
    entity_id: Mapped[str | None] = mapped_column(String(64))

    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), index=True
    )

    before: Mapped[dict | None] = mapped_column(json_type())
    after: Mapped[dict | None] = mapped_column(json_type())
    note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
