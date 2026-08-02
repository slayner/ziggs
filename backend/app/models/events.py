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
    BigInteger, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, Snowflake, json_type, pk
from app.domain.states import EventSeriousness, EventState, EventType, ParticipationMode, VerificationStep


class Event(Base, TimestampMixin):
    __tablename__ = "events"

    id: Mapped[int] = pk()
    bot_request_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )

    state: Mapped[EventState] = mapped_column(
        Enum(EventState, name="event_state"),
        default=EventState.SCHEDULED,
        nullable=False,
        index=True,
    )
    signup_mode: Mapped[str] = mapped_column(String(32), default="signup", nullable=False)
    assignment_mode: Mapped[str] = mapped_column(String(32), default="hybrid", nullable=False)
    autofill_mode: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # NULL até a fase de definição — mas pode vir preenchido já na criação
    # (ver EventCreate) e é só re-confirmado/ajustado depois.
    type: Mapped[EventType | None] = mapped_column(Enum(EventType, name="event_type"))

    comp_id: Mapped[int | None] = mapped_column(
        ForeignKey("comps.id", ondelete="SET NULL"), index=True
    )

    seriousness: Mapped[EventSeriousness] = mapped_column(
        Enum(EventSeriousness, name="event_seriousness"),
        default=EventSeriousness.CASUAL, nullable=False,
    )
    participation_mode: Mapped[ParticipationMode] = mapped_column(
        Enum(ParticipationMode, name="participation_mode"),
        default=ParticipationMode.PRESENCE, nullable=False,
    )
    # Bypassa o quantity gate pra este evento — equivalente ao /liberarfuncoes
    # do bot antigo (ver app/services/event_gates.py).
    functions_released: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Outbox: true quando alguma inscrição mudou e o mass-info do Discord
    # precisa ser reconstruído — o bot limpa via /bot/events/.../massinfo-synced.
    signup_message_dirty: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    signup_last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Outbox do EMBED por evento (thread 📑 EVENTO #N): true quando uma mutação
    # mudou o que o embed mostra e o bot precisa recriá-lo. Limpo via
    # /bot/events/.../embed-synced. Distinto do mass-info (signup_message_dirty).
    event_embed_dirty: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Outbox da THREAD DE REGEAR (🛠️ Regear — Evento #N): true quando o evento
    # entra em IN_PROGRESS e o bot precisa criar a thread no canal de regear da
    # guilda. Limpo via /bot/events/.../regear-thread-synced (seta regear_thread_id).
    # Distinto do embed (que é na fase REVIEW) — a thread de regear vive no
    # andamento, onde os players postam as prints das mortes.
    regear_thread_dirty: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Thread de regear já arquivada (lock) pelo bot — separa de regear_thread_id
    # (que fica setado pra _regear_summary continuar renderizando no review
    # finalizado) e tira o evento da lista de arquivamento do loop (evita
    # re-arquivar a cada 10s / martelar rate-limit do Discord).
    regear_thread_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Outbox da THREAD DE LOOTLOG (🪵 Log — Evento #N): true quando o evento
    # entra em IN_PROGRESS e o bot precisa criar a thread no canal de lootlog da
    # guilda. Limpo via /bot/events/.../lootlog-thread-synced (seta
    # lootlog_thread_id). Espelho do regear_thread_dirty — a thread de lootlog
    # vive no andamento, onde os players postam o .csv do lootlogger.
    lootlog_thread_dirty: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Thread de lootlog já arquivada (lock) pelo bot — separa de lootlog_thread_id
    # (que fica setado) e tira o evento da lista de arquivamento do loop.
    lootlog_thread_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Mesma ideia, pra thread do PRÓPRIO embed (📑 EVENTO #N, quando a sala de
    # revisão está configurada) — fica false pra sempre em guildas sem sala de
    # revisão (embed cai num canal simples, sem thread pra trancar).
    event_thread_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # VOICE_PERCENT: total de snapshots coletados enquanto o evento esteve
    # IN_PROGRESS (denominador do base_percent de cada participante). Só cresce
    # via /bot/events/.../voice-snapshot. Ver app/services/events.py.
    total_snapshots: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_voice_snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Pontos de attendance (economia/leaderboard do bot) que TODO participante
    # deste evento recebe igualmente, não importa o percent do split — editável
    # na bracket expandida. Aceita fração (ex.: 0.5, 1.5). Default 1 = padrão.
    attendance: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

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
    signups: Mapped[list["EventSignup"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    # Escalação: quem o admin colocou em cada slot da comp (ver
    # app/services/event_escalation.py). Diferente de EventSignup (auto-inscrição
    # pré-CTA) e EventParticipant (presença/payout): aqui é o assento na comp.
    assignments: Mapped[list["EventAssignment"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class EventSignup(Base, TimestampMixin):
    """Auto-inscrição pré-CTA: funções desejadas (nomes de GameRole), escolhidas
    pelo próprio membro via botão no Discord (substitui cta_function_logs +
    Google Sheets do bot antigo). Diferente de EventParticipant: não tem
    percent/payout, e pode existir enquanto o evento ainda está `scheduled`.

    `functions` é uma lista ordenada por preferência (functions[0] = primeira
    escolha, usada pelo gate de quantidade). Antes era 3 colunas function_1/2/3;
    virou JSON para guardar todas as roles declaradas pelo jogador, sem teto."""
    __tablename__ = "event_signups"
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
    # Nomes de GameRole escolhidos (não FK — um CompSlot aceita várias GameRole
    # via CompSlotRole). Ordenados por preferência; functions[0] é a preferida.
    functions: Mapped[list] = mapped_column(json_type(), default=list, nullable=False)

    event: Mapped["Event"] = relationship(back_populates="signups")


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
    # VOICE_PERCENT: quantas vezes este jogador estava presente na sala CTA nos
    # snapshots coletados (numerador). Freeze em IN_PROGRESS→DEFINITION vira
    # base_percent = round(snapshots_present*100/event.total_snapshots).
    snapshots_present: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Alistado depois (não estava na zerg) vs detectado ao vivo.
    enlisted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enlisted_by: Mapped[int | None] = mapped_column(Snowflake)

    # Regular vs irregular. NULL = derivado (válido sse o user tem EventSignup
    # neste evento — presença na call sem inscrição = irregular). Admin pode
    # sobrescrever arrastando entre as colunas Irregulares/Válidos no site
    # (True/False explícito). Irregular no finalize: sem split, sem attendance.
    is_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

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
        BigInteger, default=0, nullable=False
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


class EventAssignment(Base, TimestampMixin):
    """Assento de um jogador num slot da comp de um evento (a "escalação").

    Diferente de EventSignup (auto-inscrição por função, sem build) e EventParticipant
    (presença/payout): aqui referencia o CompSlot onde o admin colocou o jogador e qual
    GameRole (flex) ele vai jogar. Um slot = um jogador (unique event+slot); um jogador
    = um slot por evento (unique event+user). comp_slot_id é SET NULL se a comp for
    editada e o slot sumir — vira órfão que a UI esconde (não deleta, dado não se perde).
    """
    __tablename__ = "event_assignments"
    __table_args__ = (
        UniqueConstraint("event_id", "comp_slot_id", name="uq_event_assignments_event_slot"),
        UniqueConstraint("event_id", "user_id", name="uq_event_assignments_event_user"),
    )

    id: Mapped[int] = pk()
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    guild_id: Mapped[int] = mapped_column(
        ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    comp_slot_id: Mapped[int | None] = mapped_column(
        ForeignKey("comp_slots.id", ondelete="SET NULL"), index=True
    )
    user_id: Mapped[int] = mapped_column(Snowflake, nullable=False)
    user_name: Mapped[str | None] = mapped_column(String(255))
    # Admin assignments are immutable from automatic filling. Existing rows are
    # manual assignments, so the migration defaults them to locked=True.
    locked: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    autofill_run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    # Flex role escolhida (uma dos CompSlotRole.game_role_id do slot).
    game_role_id: Mapped[int | None] = mapped_column(
        ForeignKey("game_roles.id", ondelete="SET NULL"), index=True
    )

    event: Mapped["Event"] = relationship(back_populates="assignments")


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
