"""
Eventos (CTAs) — agora com estado EXPLÍCITO.

No bot antigo o estado era implícito (ended_at IS NULL, split_finalized, ...).
Aqui `events.state` é a fonte da verdade e só muda via
`app.domain.state_machine.transition`.

Um evento é criado SEM tipo (lootsplit/regear/...) — o tipo é definido na fase de
`definicao` pela logística. O evento referencia uma comp opcional. Ao entrar em
andamento o bot cria uma role do Discord (event_role_id) sem permissões: serve só
para marcar exatamente quem está no evento mais tarde, sem @everyone.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, Snowflake, json_type, pk
from app.domain.states import EventState, EventType, VerificationStep


class Event(Base, TimestampMixin):
    __tablename__ = "events"

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )

    state: Mapped[EventState] = mapped_column(
        Enum(EventState, name="event_state"),
        default=EventState.SCHEDULED,
        nullable=False,
        index=True,
    )
    # NULL até a fase de definição.
    type: Mapped[EventType | None] = mapped_column(Enum(EventType, name="event_type"))

    comp_id: Mapped[int | None] = mapped_column(
        ForeignKey("comps.id", ondelete="SET NULL"), index=True
    )

    caller_id: Mapped[int | None] = mapped_column(Snowflake)
    caller_name: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(255))
    message: Mapped[str | None] = mapped_column(Text)   # texto livre do caller

    # Linha do tempo.
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    callout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Resultado econômico (preenchido na verificação/finalização).
    tab_value: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tab_image_url: Mapped[str | None] = mapped_column(String(512))
    battleboard_url: Mapped[str | None] = mapped_column(String(512))
    # lootsplit+regear pode dar PERDA: ela é só mostrada/registrada e debitada do
    # banco da guilda (que pode ficar negativo). Nunca é tirada dos jogadores.
    is_loss: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Ganchos do Discord (criados/atualizados pelo bot).
    event_role_id: Mapped[int | None] = mapped_column(Snowflake)   # role do evento
    event_channel_id: Mapped[int | None] = mapped_column(Snowflake)
    event_message_id: Mapped[int | None] = mapped_column(Snowflake)
    lootlog_thread_id: Mapped[int | None] = mapped_column(Snowflake)
    split_thread_id: Mapped[int | None] = mapped_column(Snowflake)
    regear_thread_id: Mapped[int | None] = mapped_column(Snowflake)

    participants: Mapped[list["EventParticipant"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    deaths: Mapped[list["EventDeath"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    verification_steps: Mapped[list["EventVerificationStep"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class EventParticipant(Base, TimestampMixin):
    """Participação de um jogador no evento (a 'attendance' do bot)."""
    __tablename__ = "event_participants"
    __table_args__ = (UniqueConstraint("event_id", "user_id"),)

    id: Mapped[int] = pk()
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(Snowflake, nullable=False)
    user_name: Mapped[str | None] = mapped_column(String(255))

    # Percentuais: base (medido) e efetivo (após desconto de trial). A maneira de
    # registrar % é mantida do bot. Split não definido => 0.
    base_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Alistado depois (não estava na zerg) vs detectado ao vivo.
    enlisted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enlisted_by: Mapped[int | None] = mapped_column(Snowflake)

    silver_received: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # Função que o jogador estava usando neste evento — base para cálculo de regear.
    game_role_id: Mapped[int | None] = mapped_column(
        ForeignKey("game_roles.id", ondelete="SET NULL"), index=True
    )

    event: Mapped["Event"] = relationship(back_populates="participants")


class EventDeath(Base, TimestampMixin):
    """Morte registrada durante um evento — usada para calcular regear."""
    __tablename__ = "event_deaths"

    id: Mapped[int] = pk()
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Snowflake do Discord. Nullable: logística pode registrar manualmente sem ID.
    user_id: Mapped[int | None] = mapped_column(Snowflake)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Valor de prata do regear aprovado.
    silver_value: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), default=0, nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)
    approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    event: Mapped["Event"] = relationship(back_populates="deaths")


class EventVerificationStep(Base, TimestampMixin):
    """
    Progresso de UM dos passos da fase de verificação.

    `data` guarda o payload específico do passo (ex.: lista de loots faltantes,
    nodes capturados) de forma flexível até cada passo ganhar tabela própria.
    A transição verificação→espera exige todos os passos obrigatórios `completed`.
    """
    __tablename__ = "event_verification_steps"
    __table_args__ = (UniqueConstraint("event_id", "step"),)

    id: Mapped[int] = pk()
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step: Mapped[VerificationStep] = mapped_column(
        Enum(VerificationStep, name="verification_step"), nullable=False
    )
    completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completed_by: Mapped[int | None] = mapped_column(Snowflake)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)

    event: Mapped["Event"] = relationship(back_populates="verification_steps")


class EventStateTransition(Base):
    """Histórico imutável de cada mudança de estado de um evento."""
    __tablename__ = "event_state_transitions"

    id: Mapped[int] = pk()
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_state: Mapped[EventState] = mapped_column(
        Enum(EventState, name="event_state"), nullable=False
    )
    to_state: Mapped[EventState] = mapped_column(
        Enum(EventState, name="event_state"), nullable=False
    )
    actor_id: Mapped[int | None] = mapped_column(Snowflake)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # site|bot|system
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
