"""
Catálogo de itens/armas e a biblioteca de FUNÇÕES (roles) da guilda.

`weapons` é GLOBAL (compartilhado por todas as guildas) e carrega a "função
invisível" que cada arma tem — o atributo que dirige as SUGESTÕES de build. Ex.:
o Locus é `support`; suportes costumam usar o mesmo equipamento, então ao montar
um slot de suporte o site sugere a build de outra arma `support` já usada na comp.

`game_roles` é a biblioteca de funções DA GUILDA: uma função = uma arma + a build
completa (peças, comida, habilidades, estilo de jogo, observações). É o que a
planilha antiga guardava por slot, agora reutilizável e referenciável por vários
slots de várias comps.
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, json_type, pk


class Weapon(Base, TimestampMixin):
    """Arma do Albion (catálogo global) + função invisível para sugestões."""
    __tablename__ = "weapons"

    id: Mapped[int] = pk()
    # ID canônico do item no Albion (ex.: "T8_MAIN_HOLYSTAFF_AVALON"). Único.
    item_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    # Função INVISÍVEL: dirige as sugestões de build e a classificação de
    # papéis em battles. Taxonomia oficial (curada por arma, não por categoria):
    # dps / tank / healer / support / pierce. Ver scripts/seed_weapons_spells.py
    # _FUNC_OVERRIDES pra fonte da verdade por arma. Indexado porque a sugestão
    # filtra por ele.
    invisible_function: Mapped[str | None] = mapped_column(String(48), index=True)
    # Categoria mecânica da arma (one-hand/two-hand) — define se aceita offhand.
    category: Mapped[str | None] = mapped_column(String(48))


class GameRole(Base, TimestampMixin):
    """
    Uma FUNÇÃO da guilda: arma + build completa + observações.

    Reutilizável: vários slots de várias comps podem apontar para a mesma função.
    """
    __tablename__ = "game_roles"

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    weapon_id: Mapped[int | None] = mapped_column(
        ForeignKey("weapons.id", ondelete="SET NULL"), index=True
    )

    # Peças do set (texto "tier nome", ex.: "8.4 Capuz do Asceta") — mantém o
    # formato que a planilha já usava no getAssignments.
    offhand: Mapped[str | None] = mapped_column(String(128))
    helmet: Mapped[str | None] = mapped_column(String(128))
    armor: Mapped[str | None] = mapped_column(String(128))
    boots: Mapped[str | None] = mapped_column(String(128))
    cape: Mapped[str | None] = mapped_column(String(128))
    food: Mapped[str | None] = mapped_column(String(128))

    # Texto livre.
    abilities: Mapped[str | None] = mapped_column(Text)    # habilidades
    play_style: Mapped[str | None] = mapped_column(Text)   # estilo de jogo
    obs: Mapped[str | None] = mapped_column(Text)          # observações

    # Itens estruturados para cálculo de regear (com item_id do Albion Online):
    # [{"slot": "helmet", "item_id": "T8_HEAD_CLOTH_MORGANA@4", "name": "...", "quality": 1, "quantity": 1}]
    # Slots válidos: offhand, helmet, armor, boots, cape, food.
    build_items: Mapped[list] = mapped_column(json_type(), default=list, nullable=False)

    # Cor hexadecimal da função (ex.: "#e05252"). Substituí FN_COLORS fixas do frontend.
    color: Mapped[str | None] = mapped_column(String(7))

    # Feitiços selecionados pelo usuário (IDs únicos do Albion, ex.: "GENEROUSHEAL")
    q_spell:       Mapped[str | None] = mapped_column(String(128))
    w_spell:       Mapped[str | None] = mapped_column(String(128))
    passive_spell: Mapped[str | None] = mapped_column(String(128))

    # Feitiços de equipamento (capacete/armadura/botas) — JSON:
    # {"helmet_active": "ENERGY_BARRIER", "helmet_passive": "PASSIVE_MR_AR", ...}
    gear_spells: Mapped[str | None] = mapped_column(Text)


class WeaponSpell(Base, TimestampMixin):
    """Feitiço disponível para uma arma (slot Q, W ou passivo)."""
    __tablename__ = "weapon_spells"

    id: Mapped[int] = pk()
    # ID base da arma sem prefixo de tier (ex.: "MAIN_HOLYSTAFF", "2H_INFERNOSTAFF_HELL")
    weapon_base_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    slot:      Mapped[str] = mapped_column(String(8), nullable=False)   # Q | W | passive
    order_idx: Mapped[int] = mapped_column(default=0, nullable=False)
    spell_id:  Mapped[str] = mapped_column(String(128), nullable=False) # ex.: "GENEROUSHEAL"
    name:      Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    uisprite:  Mapped[str | None] = mapped_column(String(128))
