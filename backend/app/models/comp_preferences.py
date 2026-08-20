"""Preferências de um jogador: legado por composição + atual por par
(arma, função de slot) — guild-scoped, não comp-scoped."""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Snowflake, TimestampMixin, pk


class CompRolePreference(Base, TimestampMixin):
    """LEGADO: role que o jogador sabe/faz em uma composição.

    Substituída por `WeaponFnPreference` (preferência por par arma+fn, global
    da guilda) — ver migration zw3a4b5c6d7f. Tabela mantida para leitura do
    histórico; novos signups não escrevem aqui.
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


class WeaponFnPreference(Base, TimestampMixin):
    """Par (arma, função de slot) que o jogador sabe/faz — preferência
    PERSISTENTE e guild-scoped (não comp-scoped).

    Identidade de signup desde ago/2026: (Weapon.id, CompSlot.fn). Quem
    escolhe Longbow+DPS numa comp é pré-selecionado em Longbow+DPS em qualquer
    outra comp da guilda; Longbow+Support continua sendo outro par. Só signups
    atualizam esta tabela, e apenas nos pares VISÍVEIS na comp do evento —
    remover Longbow+DPS num evento não apaga Longbow+Support nem armas que
    não estão naquela comp.
    """

    __tablename__ = "weapon_fn_preferences"
    __table_args__ = (
        UniqueConstraint("guild_id", "user_id", "weapon_id", "fn", name="uq_weapon_fn_pref"),
    )

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(Snowflake, nullable=False, index=True)
    weapon_id: Mapped[int] = mapped_column(
        ForeignKey("weapons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # fn do CompSlot, normalizado (casefold/strip; vazio = "other"). Guardado
    # normalizado para bater com pair_key em qualquer leitura.
    fn: Mapped[str] = mapped_column(String(64), nullable=False)
