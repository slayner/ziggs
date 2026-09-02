"""
Composições (comps) com SLOTS FLEXÍVEIS.

Diferença-chave para a planilha antiga: lá um slot aceitava UMA role. Aqui um
slot aceita N roles que "fazem a mesma coisa" — `comp_slot_roles` é a tabela de
junção que dá essa flexibilidade (a feature que, segundo a visão, ninguém tem).

Hierarquia:
    Comp ──< CompParty (grupo) ──< CompSlot (vaga) ──< CompSlotRole ──> GameRole
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, Snowflake, pk


class Comp(Base, TimestampMixin):
    __tablename__ = "comps"
    __table_args__ = (UniqueConstraint("guild_id", "name"),)

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(Snowflake)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    parties: Mapped[list["CompParty"]] = relationship(
        back_populates="comp",
        cascade="all, delete-orphan",
        order_by="CompParty.position",
    )


class CompParty(Base, TimestampMixin):
    """Um grupo dentro da comp (ex.: Party 1, Party 2, ...)."""
    __tablename__ = "comp_parties"
    __table_args__ = (UniqueConstraint("comp_id", "position"),)

    id: Mapped[int] = pk()
    comp_id: Mapped[int] = mapped_column(
        ForeignKey("comps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(String(128))

    comp: Mapped["Comp"] = relationship(back_populates="parties")
    slots: Mapped[list["CompSlot"]] = relationship(
        back_populates="party",
        cascade="all, delete-orphan",
        order_by="CompSlot.position",
    )


class CompSlot(Base, TimestampMixin):
    """Uma vaga na party. Aceita VÁRIAS roles (via comp_slot_roles)."""
    __tablename__ = "comp_slots"
    __table_args__ = (UniqueConstraint("party_id", "position"),)

    id: Mapped[int] = pk()
    party_id: Mapped[int] = mapped_column(
        ForeignKey("comp_parties.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str | None] = mapped_column(String(128))   # rótulo livre da vaga
    notes: Mapped[str | None] = mapped_column(Text)
    fn: Mapped[str | None] = mapped_column(String(64))      # tipo de função da bracket

    party: Mapped["CompParty"] = relationship(back_populates="slots")
    roles: Mapped[list["CompSlotRole"]] = relationship(
        back_populates="slot",
        cascade="all, delete-orphan",
        order_by="CompSlotRole.position",
    )


class CompSlotRole(Base):
    """Junção slot↔função: as N funções aceitáveis numa mesma vaga."""
    __tablename__ = "comp_slot_roles"
    __table_args__ = (UniqueConstraint("slot_id", "game_role_id"),)

    id: Mapped[int] = pk()
    slot_id: Mapped[int] = mapped_column(
        ForeignKey("comp_slots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    game_role_id: Mapped[int] = mapped_column(
        ForeignKey("game_roles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    slot: Mapped["CompSlot"] = relationship(back_populates="roles")
