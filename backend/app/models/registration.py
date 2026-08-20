"""Registro de membros via bot (/register) — sem relação com claims do site."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Snowflake, pk


class BotRegistration(Base):
    """Um personagem Albion vinculado a um membro Discord via /register.

    Único por (guild_id, albion_player_id, discord_user_id) — a MESMA pessoa
    pode registrar o mesmo personagem em vários Discords (main + alt), cada
    linha com seu cargo. Enquanto `active`, um novo /register pro mesmo par é
    idempotente. `active=False` libera o par pra um novo registro.

    albion_player_id é o ID real da API, EXCETO em registros "de confiança"
    (vigilância desligada + registro de terceiro): nesses é o sintético
    "manual:<nick>" (+ sufixo "#<n>" contando falhas de verificação, ver
    registration_checker), convertido pro ID real quando a vigilância é
    (re)ligada e o nick é encontrado.
    """
    __tablename__ = "bot_registrations"
    __table_args__ = (
        UniqueConstraint("guild_id", "albion_player_id", "discord_user_id", name="uq_bot_reg_character_user"),
    )

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
    # Decisão explícita no Discord (/unregister, remoção de cargo, kick ou ban).
    # Retentativas automáticas iniciadas antes deste instante não podem reativar.
    human_revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Contador de falhas CONSECUTIVAS de revalidação (API do Albion instável
    # retorna GuildId vazio/404 temporário). Só revoga depois de N falhas
    # seguidas; sucesso zera. last_fail_at evita raiva dupla no mesmo ciclo.
    fail_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    last_fail_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
