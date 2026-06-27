"""
Serviço de eventos: cria, lista, lê e move eventos pela máquina de estados.

Toda mudança de estado passa por `state_machine.transition` (grava transição +
audit). Carimba started_at/callout_at/ended_at conforme entra em cada fase.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.schemas.events import (
    DeathIn, DeathOut, DeathUpdate, EventCreate, EventDetail, EventSummary,
    ParticipantIn, ParticipantOut, ParticipantUpdate, PayoutPreview, PayoutRow,
    VerificationStepOut,
)
from app.domain import state_machine
from app.domain.states import (
    REQUIRED_VERIFICATION_STEPS, EventState, EventType, VerificationStep,
    allowed_targets,
)
from app.models.audit import AuditLog
from app.models.events import Event, EventDeath, EventParticipant, EventVerificationStep
from app.models.tenancy import Guild


class ServiceError(Exception):
    """Erro de regra de negócio (vira 400 na rota)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# leitura
# ---------------------------------------------------------------------------

def _get(db: Session, guild_id: int, event_id: int) -> Event | None:
    return db.scalar(
        select(Event)
        .where(Event.id == event_id, Event.guild_id == guild_id)
        .options(
            selectinload(Event.verification_steps),
            selectinload(Event.participants),
            selectinload(Event.deaths),
        )
    )


def _calc_payout(ev: Event) -> PayoutPreview | None:
    """Calcula o preview de pagamento com base no tipo e dados atuais."""
    if ev.type is None:
        return None

    total_pct = sum(p.percent for p in ev.participants)
    rows: dict[int | None, dict] = {}

    for p in ev.participants:
        lootsplit = 0
        if ev.type in (EventType.LOOTSPLIT, EventType.LOOTSPLIT_REGEAR):
            if total_pct > 0:
                lootsplit = int(ev.tab_value * p.percent / total_pct)
        rows[p.user_id] = {
            "user_id": p.user_id,
            "display_name": p.user_name or str(p.user_id),
            "percent": p.percent,
            "lootsplit": lootsplit,
            "regear": 0,
            "total": lootsplit,
        }

    if ev.type in (EventType.REGEAR, EventType.LOOTSPLIT_REGEAR):
        for death in ev.deaths:
            if not death.approved:
                continue
            key = death.user_id
            if key in rows:
                rows[key]["regear"] += death.silver_value
                rows[key]["total"] += death.silver_value
            else:
                # Morte manual de alguém não listado como participante
                rows[key] = {
                    "user_id": death.user_id,
                    "display_name": death.display_name,
                    "percent": 0,
                    "lootsplit": 0,
                    "regear": death.silver_value,
                    "total": death.silver_value,
                }

    payouts = [PayoutRow(**r) for r in rows.values()]
    return PayoutPreview(
        tab_value=ev.tab_value,
        payouts=payouts,
        total_lootsplit=sum(r.lootsplit for r in payouts),
        total_regear=sum(r.regear for r in payouts),
    )


def _detail(ev: Event, db: Session) -> EventDetail:
    from app.models.catalog import GameRole
    step_map = {s.step: s for s in ev.verification_steps}
    steps = [
        VerificationStepOut(
            step=s.value,
            completed=bool(step_map[s].completed if s in step_map else False),
            data=step_map[s].data if s in step_map else {},
        )
        for s in VerificationStep
    ]
    role_ids = {p.game_role_id for p in ev.participants if p.game_role_id}
    role_names: dict[int, str] = {}
    if role_ids:
        role_names = {
            r.id: r.name
            for r in db.scalars(select(GameRole).where(GameRole.id.in_(role_ids)))
        }
    participants = [
        ParticipantOut(
            id=p.id, user_id=p.user_id, user_name=p.user_name,
            percent=p.percent, base_percent=p.base_percent,
            is_trial=p.is_trial, silver_received=p.silver_received,
            game_role_id=p.game_role_id,
            game_role_name=role_names.get(p.game_role_id) if p.game_role_id else None,
        )
        for p in ev.participants
    ]
    deaths = [
        DeathOut(
            id=d.id, user_id=d.user_id, display_name=d.display_name,
            silver_value=d.silver_value, notes=d.notes, approved=d.approved,
        )
        for d in ev.deaths
    ]
    payout = (
        _calc_payout(ev)
        if ev.state in (EventState.WAITING, EventState.FINALIZED)
        else None
    )
    return EventDetail(
        id=ev.id, state=ev.state.value,
        type=ev.type.value if ev.type else None,
        title=ev.title, message=ev.message, comp_id=ev.comp_id,
        scheduled_at=ev.scheduled_at, started_at=ev.started_at,
        callout_at=ev.callout_at, ended_at=ev.ended_at, is_loss=ev.is_loss,
        tab_value=ev.tab_value, tab_image_url=ev.tab_image_url,
        allowed_transitions=sorted(t.value for t in allowed_targets(ev.state)),
        verification=steps,
        participants=participants,
        deaths=deaths,
        payout=payout,
    )


# ---------------------------------------------------------------------------
# eventos
# ---------------------------------------------------------------------------

def create_event(
    db: Session, guild_id: int, payload: EventCreate,
    actor_id: int | None, caller_name: str | None,
) -> int:
    ev = Event(
        guild_id=guild_id,
        state=EventState.SCHEDULED,
        title=payload.title,
        scheduled_at=payload.scheduled_at,
        comp_id=payload.comp_id,
        caller_id=actor_id,
        caller_name=caller_name,
    )
    db.add(ev)
    db.flush()
    db.add(AuditLog(
        guild_id=guild_id, actor_id=actor_id, actor_type="site", source="site",
        action="event.create", entity="event", entity_id=str(ev.id),
        after={"state": ev.state.value, "title": ev.title},
    ))
    return ev.id


def list_events(db: Session, guild_id: int) -> list[EventSummary]:
    rows = db.scalars(
        select(Event).where(Event.guild_id == guild_id).order_by(Event.id.desc())
    )
    return [
        EventSummary(
            id=e.id, state=e.state.value,
            type=e.type.value if e.type else None,
            title=e.title, caller_name=e.caller_name,
            scheduled_at=e.scheduled_at, created_at=e.created_at,
        )
        for e in rows
    ]


def get_event(db: Session, guild_id: int, event_id: int) -> EventDetail | None:
    ev = _get(db, guild_id, event_id)
    return _detail(ev, db) if ev else None


def transition(
    db: Session, guild_id: int, event_id: int, to: str,
    actor_id: int | None, reason: str | None,
) -> EventDetail:
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    try:
        target = EventState(to)
    except ValueError:
        raise ServiceError(f"estado inválido: {to}")

    if target is EventState.VERIFICATION:
        _ensure_steps(db, ev)

    actor = state_machine.Actor(id=actor_id, source="site")
    try:
        state_machine.transition(db, ev, target, actor, reason)
    except (state_machine.TransitionDenied,) as e:
        raise ServiceError(str(e))
    except Exception as e:
        raise ServiceError(str(e))

    if target is EventState.IN_PROGRESS and ev.started_at is None:
        ev.started_at = _now()
    elif target is EventState.DEFINITION and ev.callout_at is None:
        ev.callout_at = _now()
    elif target is EventState.FINALIZED:
        ev.ended_at = _now()
        _finalize_payouts(db, ev)

    db.flush()
    return _detail(ev, db)


def set_type(
    db: Session, guild_id: int, event_id: int, type_str: str, actor_id: int | None,
) -> EventDetail:
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    try:
        ev.type = EventType(type_str)
    except ValueError:
        raise ServiceError(f"tipo inválido: {type_str}")
    db.add(AuditLog(
        guild_id=guild_id, actor_id=actor_id, actor_type="site", source="site",
        action="event.set_type", entity="event", entity_id=str(ev.id),
        after={"type": ev.type.value},
    ))
    db.flush()
    return _detail(ev, db)


def _ensure_steps(db: Session, ev: Event) -> None:
    existing = {s.step for s in ev.verification_steps}
    for step in REQUIRED_VERIFICATION_STEPS:
        if step not in existing:
            db.add(EventVerificationStep(event_id=ev.id, step=step, completed=False))
    db.flush()
    db.refresh(ev)


def set_step(
    db: Session, guild_id: int, event_id: int, step_str: str,
    completed: bool, data: dict | None, actor_id: int | None,
) -> EventDetail:
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    try:
        step = VerificationStep(step_str)
    except ValueError:
        raise ServiceError(f"passo inválido: {step_str}")

    _ensure_steps(db, ev)
    row = next((s for s in ev.verification_steps if s.step == step), None)
    if row is None:
        row = EventVerificationStep(event_id=ev.id, step=step)
        db.add(row)
    row.completed = completed
    row.completed_by = actor_id
    row.completed_at = _now() if completed else None
    if data is not None:
        row.data = data

    # Espelha campos importantes de volta no evento para facilitar queries/relatórios.
    if step is VerificationStep.TAB_VALUE and data and "value" in data:
        ev.tab_value = int(data["value"])
    if step is VerificationStep.TAB_IMAGE and data and "url" in data:
        ev.tab_image_url = str(data["url"])

    db.flush()
    db.refresh(ev)
    return _detail(ev, db)


# ---------------------------------------------------------------------------
# participantes
# ---------------------------------------------------------------------------

def add_participant(
    db: Session, guild_id: int, event_id: int,
    payload: ParticipantIn, actor_id: int | None,
) -> EventDetail:
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    existing = {p.user_id for p in ev.participants}
    if payload.user_id in existing:
        raise ServiceError("participante já registrado")
    db.add(EventParticipant(
        event_id=event_id, guild_id=guild_id,
        user_id=payload.user_id, user_name=payload.user_name,
        percent=payload.percent, base_percent=payload.base_percent,
        is_trial=payload.is_trial,
    ))
    db.flush()
    db.refresh(ev)
    return _detail(ev, db)


def remove_participant(
    db: Session, guild_id: int, event_id: int,
    participant_id: int, actor_id: int | None,
) -> EventDetail:
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    row = db.scalar(
        select(EventParticipant).where(
            EventParticipant.id == participant_id,
            EventParticipant.event_id == event_id,
        )
    )
    if row is None:
        raise ServiceError("participante não encontrado")
    db.delete(row)
    db.flush()
    db.refresh(ev)
    return _detail(ev, db)


def update_participant(
    db: Session, guild_id: int, event_id: int,
    participant_id: int, payload: ParticipantUpdate, actor_id: int | None,
) -> EventDetail:
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    row = db.scalar(
        select(EventParticipant).where(
            EventParticipant.id == participant_id,
            EventParticipant.event_id == event_id,
        )
    )
    if row is None:
        raise ServiceError("participante não encontrado")
    if payload.game_role_id is not None:
        row.game_role_id = payload.game_role_id
    elif "game_role_id" in (payload.model_fields_set or set()):
        row.game_role_id = None
    if payload.percent is not None:
        row.percent = payload.percent
    if payload.is_trial is not None:
        row.is_trial = payload.is_trial
    db.flush()
    db.refresh(ev)
    return _detail(ev, db)


# ---------------------------------------------------------------------------
# mortes / regear
# ---------------------------------------------------------------------------

def add_death(
    db: Session, guild_id: int, event_id: int,
    payload: DeathIn, actor_id: int | None,
) -> EventDetail:
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    db.add(EventDeath(
        event_id=event_id, guild_id=guild_id,
        user_id=payload.user_id,
        display_name=payload.display_name,
        silver_value=payload.silver_value,
        notes=payload.notes,
        approved=False,
    ))
    db.flush()
    db.refresh(ev)
    return _detail(ev, db)


def update_death(
    db: Session, guild_id: int, event_id: int,
    death_id: int, payload: DeathUpdate, actor_id: int | None,
) -> EventDetail:
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    death = db.scalar(
        select(EventDeath).where(
            EventDeath.id == death_id, EventDeath.event_id == event_id
        )
    )
    if death is None:
        raise ServiceError("morte não encontrada")
    if payload.approved is not None:
        death.approved = payload.approved
    if payload.silver_value is not None:
        death.silver_value = payload.silver_value
    if payload.notes is not None:
        death.notes = payload.notes
    db.flush()
    db.refresh(ev)
    return _detail(ev, db)


def remove_death(
    db: Session, guild_id: int, event_id: int,
    death_id: int, actor_id: int | None,
) -> EventDetail:
    ev = _get(db, guild_id, event_id)
    if ev is None:
        raise ServiceError("evento não encontrado")
    death = db.scalar(
        select(EventDeath).where(
            EventDeath.id == death_id, EventDeath.event_id == event_id
        )
    )
    if death is None:
        raise ServiceError("morte não encontrada")
    db.delete(death)
    db.flush()
    db.refresh(ev)
    return _detail(ev, db)


# ---------------------------------------------------------------------------
# finalização
# ---------------------------------------------------------------------------

def _finalize_payouts(db: Session, ev: Event) -> None:
    """Grava silver_received em cada participante e debita o regear do banco da guilda.

    Participantes SEMPRE recebem prata (nunca é descontado). O custo do regear
    sai do banco da guilda, que pode ficar negativo (ex.: CTA encerrado em perda).
    """
    if ev.type is None:
        return
    payout = _calc_payout(ev)
    if payout is None:
        return

    payout_map = {r.user_id: r for r in payout.payouts}
    for p in ev.participants:
        row = payout_map.get(p.user_id)
        if row:
            p.silver_received = row.total

    # Regear é custo do banco da guilda (pode ir negativo).
    if payout.total_regear > 0 and ev.type in (EventType.REGEAR, EventType.LOOTSPLIT_REGEAR):
        guild = db.get(Guild, ev.guild_id)
        if guild is not None:
            guild.bank_balance -= payout.total_regear
