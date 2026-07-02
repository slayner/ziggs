"""Rotas de eventos (CTAs), escopadas por guilda."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api import deps
from app.api.schemas.events import (
    DeathIn, DeathUpdate, EventCreate, EventDetail, EventSummary,
    ParticipantIn, ParticipantUpdate, RegearEstimateOut, RegearItemOut,
    SetTypeRequest, StepRequest, TransitionRequest,
)
from app.models.tenancy import Guild, User
from app.services import events as svc

router = APIRouter(prefix="/guilds/{guild_id}/events", tags=["events"])


def _guild_db_user(
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    user: User | None = Depends(deps.optional_user),
):
    return guild, db, user


@router.get("", response_model=list[EventSummary])
def list_events(
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _member=Depends(deps.require_permission("events.view")),
):
    return svc.list_events(db, guild.id)


@router.post("", response_model=EventDetail, status_code=201)
def create_event(
    payload: EventCreate,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    user: User | None = Depends(deps.optional_user),
    _member=Depends(deps.require_permission("events.create")),
):
    eid = svc.create_event(
        db, guild.id, payload,
        actor_id=user.id if user else None,
        caller_name=(user.global_name or user.username) if user else "demo",
    )
    db.commit()
    detail = svc.get_event(db, guild.id, eid)
    assert detail is not None
    return detail


@router.get("/{event_id}", response_model=EventDetail)
def get_event(
    event_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _member=Depends(deps.require_permission("events.view")),
):
    detail = svc.get_event(db, guild.id, event_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="evento não encontrado")
    return detail


@router.post("/{event_id}/transition", response_model=EventDetail)
def transition(
    event_id: int,
    payload: TransitionRequest,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    user: User | None = Depends(deps.optional_user),
    _member=Depends(deps.require_permission("events.manage")),
):
    try:
        detail = svc.transition(
            db, guild.id, event_id, payload.to,
            actor_id=user.id if user else None, reason=payload.reason,
        )
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return detail


@router.post("/{event_id}/type", response_model=EventDetail)
def set_type(
    event_id: int,
    payload: SetTypeRequest,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    user: User | None = Depends(deps.optional_user),
    _member=Depends(deps.require_permission("events.manage")),
):
    try:
        detail = svc.set_type(
            db, guild.id, event_id, payload.type,
            actor_id=user.id if user else None,
        )
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return detail


@router.post("/{event_id}/verification/{step}", response_model=EventDetail)
def set_step(
    event_id: int,
    step: str,
    payload: StepRequest,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    user: User | None = Depends(deps.optional_user),
    _member=Depends(deps.require_permission("events.manage")),
):
    try:
        detail = svc.set_step(
            db, guild.id, event_id, step, payload.completed, payload.data,
            actor_id=user.id if user else None,
        )
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return detail


# ── Participantes ──────────────────────────────────────────────────────────

@router.post("/{event_id}/participants", response_model=EventDetail, status_code=201)
def add_participant(
    event_id: int,
    payload: ParticipantIn,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    user: User | None = Depends(deps.optional_user),
    _member=Depends(deps.require_permission("events.manage")),
):
    try:
        detail = svc.add_participant(
            db, guild.id, event_id, payload, actor_id=user.id if user else None
        )
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return detail


@router.patch("/{event_id}/participants/{participant_id}", response_model=EventDetail)
def update_participant(
    event_id: int,
    participant_id: int,
    payload: ParticipantUpdate,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    user: User | None = Depends(deps.optional_user),
    _member=Depends(deps.require_permission("events.manage")),
):
    try:
        detail = svc.update_participant(
            db, guild.id, event_id, participant_id, payload,
            actor_id=user.id if user else None,
        )
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return detail


@router.get("/{event_id}/participants/{participant_id}/regear-estimate", response_model=RegearEstimateOut)
async def regear_estimate(
    event_id: int,
    participant_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _member=Depends(deps.require_permission("events.view")),
):
    from app.services.prices import estimate_regear
    try:
        est = await estimate_regear(db, participant_id, guild.id, event_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    db.commit()  # persiste preços fetchados
    return RegearEstimateOut(
        participant_id=est.participant_id,
        user_name=est.user_name,
        game_role_id=est.game_role_id,
        game_role_name=est.game_role_name,
        items=[
            RegearItemOut(
                slot=i.slot, item_id=i.item_id, name=i.name,
                quality=i.quality, quantity=i.quantity,
                unit_price=i.unit_price, total_price=i.total_price,
            )
            for i in est.items
        ],
        total=est.total,
        price_basis=est.price_basis,
        calculated_at=est.calculated_at,
    )


@router.delete("/{event_id}/participants/{participant_id}", response_model=EventDetail)
def remove_participant(
    event_id: int,
    participant_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    user: User | None = Depends(deps.optional_user),
    _member=Depends(deps.require_permission("events.manage")),
):
    try:
        detail = svc.remove_participant(
            db, guild.id, event_id, participant_id, actor_id=user.id if user else None
        )
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return detail


# ── Mortes / Regear ───────────────────────────────────────────────────────

@router.post("/{event_id}/deaths", response_model=EventDetail, status_code=201)
def add_death(
    event_id: int,
    payload: DeathIn,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    user: User | None = Depends(deps.optional_user),
    _member=Depends(deps.require_permission("events.manage")),
):
    try:
        detail = svc.add_death(
            db, guild.id, event_id, payload, actor_id=user.id if user else None
        )
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return detail


@router.patch("/{event_id}/deaths/{death_id}", response_model=EventDetail)
def update_death(
    event_id: int,
    death_id: int,
    payload: DeathUpdate,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    user: User | None = Depends(deps.optional_user),
    _member=Depends(deps.require_permission("events.manage")),
):
    try:
        detail = svc.update_death(
            db, guild.id, event_id, death_id, payload,
            actor_id=user.id if user else None,
        )
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return detail


@router.delete("/{event_id}/deaths/{death_id}", response_model=EventDetail)
def remove_death(
    event_id: int,
    death_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    user: User | None = Depends(deps.optional_user),
    _member=Depends(deps.require_permission("events.manage")),
):
    try:
        detail = svc.remove_death(
            db, guild.id, event_id, death_id, actor_id=user.id if user else None
        )
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return detail
