"""Perfis de guildas e alianças aquecidos pelo profile_warmer — payload cru
da Albion + last_seen_at/refresh_requested_at, mesmo padrão do AlbionPlayer.

Antes dessas tabelas, /public/guilds/{id} e /public/alliances/{id} só tinham
dados agregados de BattleGuild (snapshot por batalha) — nunca buscavam a
Albion no caminho principal. O `last_synced_at` era max(Battle.fetched_at),
que só mudava quando uma NOVA batalha era registrada. Agora o warmer busca
membros, alliance_id atual, kill_fame total da Albion e atualiza o perfil
em background; as rotas continuam fazendo a agregação de batalhas mas com
last_synced_at real do warmer."""
from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigInt, json_type, pk


class GuildProfile(Base):
    """Perfil de guilda aquecido pelo profile_warmer. Chave = albion_id da
    guilda (globalmente único, como AlbionPlayer.albion_id). `region` é
    necessária porque IDs de guilda da Albion são por região (não existem
    nas outras 2) — sem isso o warmer não sabe qual host consultar."""
    __tablename__ = "guild_profiles"

    id: Mapped[int] = pk()
    albion_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    region: Mapped[str] = mapped_column(String(16), nullable=False, server_default="americas")

    # Snapshot mais recente denormalizado — mesmo padrão do AlbionPlayer.
    alliance_id: Mapped[str | None] = mapped_column(String(64))
    alliance_name: Mapped[str | None] = mapped_column(String(255))
    kill_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    death_fame: Mapped[int] = mapped_column(BigInt(), default=0, nullable=False)
    # Membros atuais (lista de {Id, Name, ...}) — o que o /gameinfo/guilds/{id}
    # devolve. Agregação de batalhas (kills/deaths por membro em ZvZ) continua
    # em /public/guilds/{id}; isso aqui é o estado ATUAL da guilda na Albion.
    members: Mapped[list | None] = mapped_column(json_type())
    founder_id: Mapped[str | None] = mapped_column(String(64))

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    refresh_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_priority: Mapped[int] = mapped_column(default=0, nullable=False)


class AllianceProfile(Base):
    """Perfil de aliança aquecido pelo profile_warmer. Mesma estrutura do
    GuildProfile — IDs de aliança também são por região."""
    __tablename__ = "alliance_profiles"

    id: Mapped[int] = pk()
    albion_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    region: Mapped[str] = mapped_column(String(16), nullable=False, server_default="americas")

    # Guildas membros atuais (lista de {Id, Name, ...}).
    guilds: Mapped[list | None] = mapped_column(json_type())
    founder_id: Mapped[str | None] = mapped_column(String(64))

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    refresh_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_priority: Mapped[int] = mapped_column(default=0, nullable=False)