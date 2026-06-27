"""Modelos de jogadores de Albion — perfis públicos acumulativos."""
from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigInt, json_type, pk


class AlbionPlayer(Base):
    """Jogador de Albion visto alguma vez no Ziggs. Chave = ID do Albion.

    IDs do Albion não colidem entre regiões (servidores Americas/Europe/Asia
    são totalmente separados, cada um com seu próprio espaço de IDs) — por
    isso `albion_id` continua única globalmente, `region` é só pra saber qual
    host da API usar pra buscar mais dados desse jogador."""
    __tablename__ = "albion_players"

    id: Mapped[int] = pk()
    albion_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Coluna nova (ver scripts/add_player_region.py) — linhas existentes,
    # todas vistas só pelo host Americas até essa mudança, ficam "americas".
    region: Mapped[str] = mapped_column(String(16), nullable=False, server_default="americas")

    # Snapshot mais recente — denormalizado para consulta rápida
    guild_id: Mapped[str | None] = mapped_column(String(64))
    guild_name: Mapped[str | None] = mapped_column(String(255))
    alliance_id: Mapped[str | None] = mapped_column(String(64))
    alliance_name: Mapped[str | None] = mapped_column(String(255))
    alliance_tag: Mapped[str | None] = mapped_column(String(16))
    avatar: Mapped[str | None] = mapped_column(String(255))

    kill_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    death_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    pve_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    crafting_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    gathering_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)

    is_deleted: Mapped[bool] = mapped_column(Boolean(), nullable=False, server_default=sa.false(), default=False)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PlayerSnapshot(Base):
    """Snapshot periódico de um jogador — permite ver histórico de guilds e fama."""
    __tablename__ = "player_snapshots"

    id: Mapped[int] = pk()
    player_id: Mapped[int] = mapped_column(
        ForeignKey("albion_players.id", ondelete="CASCADE"), nullable=False, index=True
    )

    guild_id: Mapped[str | None] = mapped_column(String(64))
    guild_name: Mapped[str | None] = mapped_column(String(255))
    alliance_id: Mapped[str | None] = mapped_column(String(64))
    alliance_tag: Mapped[str | None] = mapped_column(String(16))

    kill_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    death_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    pve_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)

    snapshotted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PlayerKillEvent(Base):
    """Ledger de kills/mortes por jogador — alimentado pelo feed global de
    kills de cada região (ver player_tracker.py), cobre QUALQUER kill
    (1v1 incluso), diferente do battle_tracker (que só processa em
    profundidade batalhas com vários jogadores). Não depende de Battle
    existir: o link pro bracket (quando há um) é resolvido em leitura via
    `region + albion_battle_id`, sem FK rígida."""
    __tablename__ = "player_kill_events"
    __table_args__ = (UniqueConstraint("region", "albion_event_id"),)

    id: Mapped[int] = pk()
    region: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    albion_event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)

    killer_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("albion_players.id", ondelete="SET NULL"), index=True
    )
    victim_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("albion_players.id", ondelete="SET NULL"), index=True
    )

    # numberOfParticipants do evento cru — quantos jogadores do lado do
    # matador ganharam crédito de assist. is_solo = matou sem ajuda de ninguém.
    participant_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_solo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Link fraco pra Battle.albion_id da mesma região — pode não existir
    # ainda (corrida) ou nunca virar um Battle "deep" (luta pequena), por
    # isso não é FK; resolvido em leitura quando precisa linkar pro bracket.
    albion_battle_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # KillArea cru da API — hoje só se viu "OPEN_WORLD" em produção, mas o
    # campo existe e pode diferenciar conteúdo no futuro, custa nada guardar.
    kill_area: Mapped[str | None] = mapped_column(String(32))

    killer_equipment: Mapped[dict | None] = mapped_column(json_type())
    victim_equipment: Mapped[dict | None] = mapped_column(json_type())
    # Usado pra calcular "prata dropada" (preço dos itens via prices.get_battle_prices).
    victim_inventory: Mapped[list[dict] | None] = mapped_column(json_type())

    # Guilda do matador/vítima NESSE momento (snapshot do evento, não o estado
    # atual) — usado pra reconstruir o histórico de guildas a partir das
    # datas reais de kill/morte (ver routes/players.py _guild_history), bem
    # mais granular que confiar só nos PlayerSnapshot esparsos.
    killer_guild_id: Mapped[str | None] = mapped_column(String(64))
    killer_guild_name: Mapped[str | None] = mapped_column(String(255))
    victim_guild_id: Mapped[str | None] = mapped_column(String(64))
    victim_guild_name: Mapped[str | None] = mapped_column(String(255))


class DeletedProfile(Base):
    """Guildas e alianças marcadas como excluídas do jogo (detectado via API do Albion).
    Jogadores usam AlbionPlayer.is_deleted; aqui ficam apenas guild/alliance."""
    __tablename__ = "deleted_profiles"
    __table_args__ = (UniqueConstraint("entity_type", "albion_id"),)

    id: Mapped[int] = pk()
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)  # 'guild' | 'alliance'
    albion_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
