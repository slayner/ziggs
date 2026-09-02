"""Rotas de eventos (CTAs), escopadas por guilda."""
from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.api import deps
from app.db import SyncSessionLocal
from app.api.schemas.events import (
    AssignIn, AssignmentOut, AttendanceIn, DeathIn, DeathUpdate, EscalationOut,
    EscalationPricesOut, EventCreate, EventDetail, EventSummary, EventUpdate,
    NodeClaimIn, ParticipantIn, ParticipantUpdate, RegearEstimateOut, RegearItemOut,
    ReleaseFunctionsIn, SignupOut, StepRequest, TransitionRequest,
)
from app.api.schemas.regear import RegearListOut
from app.services import regear as regear_svc
from app.config import get_settings
from app.models.prices import ItemPriceLatest
from app.models.tenancy import Guild, User
from app.domain.states import EventState
from app.services import events as svc
from app.services import event_escalation as esc_svc
from app.services.prices import _AVG_SENTINEL, sync_5city_prices

router = APIRouter(prefix="/guilds/{guild_id}/events", tags=["events"])
public_router = APIRouter(prefix="/public/escalacao", tags=["events"])


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


@router.patch("/{event_id}", response_model=EventDetail)
def update_event(
    event_id: int,
    payload: EventUpdate,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    user: User | None = Depends(deps.optional_user),
    _member=Depends(deps.require_permission("events.manage")),
):
    """Edição parcial: só campos presentes (exclude_unset). Trocar a comp preserva
    as inscrições, limpa apenas as roles e pede uma nova escolha por DM."""
    data = payload.model_dump(exclude_unset=True)
    try:
        svc.update_event(
            db, guild.id, event_id,
            title=data.get("title", svc._UNSET),
            scheduled_at=data.get("scheduled_at", svc._UNSET),
            comp_id=data.get("comp_id", svc._UNSET),
            attendance=data.get("attendance", svc._UNSET),
            signup_mode=data.get("signup_mode", svc._UNSET),
            assignment_mode=data.get("assignment_mode", svc._UNSET),
            autofill_mode=data.get("autofill_mode", svc._UNSET),
            confirm_comp_reset=data.get("confirm_comp_reset", False),
            actor_id=user.id if user else None,
        )
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    detail = svc.get_event(db, guild.id, event_id)
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


@router.get("/{event_id}/regears", response_model=RegearListOut)
def event_regears(
    event_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _member=Depends(deps.require_permission("events.view")),
):
    """Regears da thread do evento (landmark) — alimenta a linha de review e o
    deep link pro RegearPage filtrado."""
    return RegearListOut(requests=regear_svc.list_requests(db, guild.id, None, event_id))


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


@router.post("/{event_id}/publish", response_model=EventDetail)
def publish_event(
    event_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    user: User | None = Depends(deps.optional_user),
    _member=Depends(deps.require_permission("events.manage")),
):
    try:
        detail = svc.transition(
            db, guild.id, event_id, EventState.SCHEDULED.value,
            actor_id=user.id if user else None, reason="publish", actor_source="site",
        )
        db.commit()
        return detail
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{event_id}/unpublish", response_model=EventDetail)
def unpublish_event(
    event_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    user: User | None = Depends(deps.optional_user),
    _member=Depends(deps.require_permission("events.manage")),
):
    try:
        detail = svc.transition(
            db, guild.id, event_id, EventState.DRAFT.value,
            actor_id=user.id if user else None, reason="unpublish", actor_source="site",
        )
        db.commit()
        return detail
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{event_id}/nodes/{node_log_id}/claim", response_model=EventDetail)
def claim_node(
    event_id: int,
    node_log_id: int,
    payload: NodeClaimIn,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    user: User | None = Depends(deps.optional_user),
    _member=Depends(deps.require_permission("events.manage")),
):
    """Captura de node em review: marca se pegamos o node e o valor vendido.
    O scout (quem adicionou o node) recebe NodeDef.weight × sold_value no
    payout — pool separado da tab."""
    from app.services import nodes as nodes_svc
    try:
        nodes_svc.claim_node(
            db, guild.id, event_id, node_log_id,
            payload.captured, payload.sold_value,
            actor_id=user.id if user else None,
        )
    except nodes_svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    detail = svc.get_event(db, guild.id, event_id)
    assert detail is not None
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


@router.post("/{event_id}/release-functions", response_model=EventDetail)
def release_functions(
    event_id: int,
    payload: ReleaseFunctionsIn,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    user: User | None = Depends(deps.optional_user),
    _member=Depends(deps.require_permission("events.manage")),
):
    """Bypassa o gate de quantidade pra este evento — equivalente ao
    /liberarfuncoes do bot antigo."""
    try:
        detail = svc.set_functions_released(
            db, guild.id, event_id, payload.released, actor_id=user.id if user else None,
        )
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return detail


@router.post("/{event_id}/attendance", response_model=EventDetail)
def set_attendance(
    event_id: int,
    payload: AttendanceIn,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    user: User | None = Depends(deps.optional_user),
    _member=Depends(deps.require_permission("events.manage")),
):
    """Attendance é um valor único por evento — mesma quantidade pra todo
    participante, independente do percent do split de cada um."""
    try:
        detail = svc.set_attendance(
            db, guild.id, event_id, payload, actor_id=user.id if user else None,
        )
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return detail


@router.get("/{event_id}/signups", response_model=list[SignupOut])
def list_signups(
    event_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _member=Depends(deps.require_permission("events.view")),
):
    """Visibilidade admin do que foi coletado via botões no Discord (auto-inscrição)."""
    try:
        return svc.list_signups(db, guild.id, event_id)
    except svc.ServiceError as e:
        raise HTTPException(status_code=404, detail=str(e))


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
    db: AsyncSession = Depends(deps.async_db_session),
    _member=Depends(deps.require_permission("events.view")),
):
    from app.services.prices import estimate_regear
    try:
        est = await estimate_regear(db, participant_id, guild.id, event_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await db.commit()  # persiste preços fetchados
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


# ── Escalação (assentamento de inscritos nos slots da comp) ──────────────────

@public_router.get("/{token}", response_model=EscalationOut)
def get_public_escalacao(
    token: str = Path(..., min_length=32, max_length=32, pattern=r"^[A-Za-z0-9_-]+$"),
    db: Session = Depends(deps.db_session),
):
    """Escalação compartilhável, somente leitura, por token aleatório do evento."""
    try:
        return esc_svc.build_public_escalation(db, token)
    except esc_svc.ServiceError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/{event_id}/escalacao", response_model=EscalationOut)
def get_escalacao(
    event_id: int,
    guild_id: int = Path(...),
    db: Session = Depends(deps.db_session),
    member=Depends(deps.require_permission_provisioning("events.view")),
):
    """Planilha de escalação do evento: árvore da comp + assentos atuais +
    inscritos (com funções escolhidas). `can_manage` é resolvido server-side
    (evita desync com a guilda corrente do cliente). Auto-provisiona a row
    Guild + GuildMember se o user for membro do server Discord (deep link)."""
    deps._dbg("GET escalacao guild=", guild_id, "event=", event_id, "member.user=", member.user_id, "is_admin=", member.is_guild_admin)
    try:
        payload = esc_svc.build_escalation(db, guild_id, event_id, member)
    except esc_svc.ServiceError as e:
        deps._dbg("build_escalation ServiceError:", str(e))
        raise HTTPException(status_code=404, detail=str(e))
    deps._dbg("GET escalacao OK guild=", guild_id, "event=", event_id)
    return payload


@router.post("/{event_id}/escalacao/assign", response_model=AssignmentOut)
def assign_escalacao(
    event_id: int,
    payload: AssignIn,
    guild_id: int = Path(...),
    db: Session = Depends(deps.db_session),
    member=Depends(deps.require_permission_provisioning("escalacao.manage")),
):
    try:
        row = esc_svc.assign(
            db, guild_id, event_id,
            payload.slot_id, payload.user_id, payload.user_name, payload.game_role_id,
            actor_id=member.user_id,
        )
    except esc_svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return AssignmentOut(
        slot_id=row.comp_slot_id, user_id=row.user_id,
        user_name=row.user_name, game_role_id=row.game_role_id, locked=row.locked,
    )


@router.post("/{event_id}/escalacao/autofill")
def autofill_escalacao(
    event_id: int,
    guild_id: int = Path(...),
    db: Session = Depends(deps.db_session),
    member=Depends(deps.require_permission_provisioning("escalacao.manage")),
):
    try:
        result = esc_svc.autofill_event(db, guild_id, event_id, actor_id=member.user_id)
    except esc_svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return result


@router.get("/{event_id}/escalacao/autofill/preview")
def preview_autofill_escalacao(
    event_id: int,
    guild_id: int = Path(...),
    db: Session = Depends(deps.db_session),
    _member=Depends(deps.require_permission_provisioning("escalacao.manage")),
):
    try:
        return {"assignments": esc_svc.preview_autofill(db, guild_id, event_id)}
    except esc_svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{event_id}/escalacao/autofill/undo")
def undo_autofill_escalacao(
    event_id: int,
    run_id: str,
    guild_id: int = Path(...),
    db: Session = Depends(deps.db_session),
    member=Depends(deps.require_permission_provisioning("escalacao.manage")),
):
    try:
        removed = esc_svc.undo_autofill(db, guild_id, event_id, run_id, member.user_id)
    except esc_svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return {"removed": removed}


@router.delete("/{event_id}/escalacao/slot/{slot_id}")
def unassign_slot(
    event_id: int,
    slot_id: int,
    guild_id: int = Path(...),
    db: Session = Depends(deps.db_session),
    member=Depends(deps.require_permission_provisioning("escalacao.manage")),
):
    try:
        esc_svc.unassign_slot(db, guild_id, event_id, slot_id, actor_id=member.user_id)
    except esc_svc.ServiceError as e:
        raise HTTPException(status_code=404, detail=str(e))
    db.commit()
    return {"ok": True}


@router.delete("/{event_id}/escalacao/user/{user_id}")
def unassign_user(
    event_id: int,
    user_id: int,
    guild_id: int = Path(...),
    db: Session = Depends(deps.db_session),
    member=Depends(deps.require_permission_provisioning("escalacao.manage")),
):
    try:
        esc_svc.unassign_user(db, guild_id, event_id, user_id, actor_id=member.user_id)
    except esc_svc.ServiceError as e:
        raise HTTPException(status_code=404, detail=str(e))
    db.commit()
    return {"ok": True}


@router.get("/{event_id}/escalacao/prices", response_model=EscalationPricesOut)
async def escalacao_prices(
    event_id: int,
    guild_id: int = Path(...),
    db: AsyncSession = Depends(deps.async_db_session),
    _member=Depends(deps.require_permission_provisioning("events.view")),
):
    """Média 5 cidades × qualidades 1-4 dos build_items de todas as roles da comp.

    Honra o kill-switch: com `disable_background_fetchers=true` lê só o cache
    (ItemPriceLatest) sem rede — em dado móvel limitado fica prices={} (UI mostra
    "—"). Com fetchers ligados, sincroniza on-demand como o restante do site."""
    # ponytail: esc_svc.build_escalation ainda é sync; roda em thread para não
    # bloquear o event loop. Quando o serviço for migrado, remover o SyncSessionLocal
    # e chamar direto com a AsyncSession.
    def _build():
        with SyncSessionLocal() as sdb:
            return esc_svc.build_escalation(sdb, guild_id, event_id, None)

    try:
        payload = await asyncio.to_thread(_build)
    except esc_svc.ServiceError as e:
        raise HTTPException(status_code=404, detail=str(e))
    item_ids = {
        b["item_id"]
        for p in payload["parties"] for s in p["slots"]
        for r in s["roles"] for b in (r.get("build_items") or [])
        if b.get("item_id")
    }
    if not item_ids:
        return EscalationPricesOut(prices={})

    id_list = list(item_ids)
    if not get_settings().disable_background_fetchers:
        await sync_5city_prices(db, id_list)

    rows = (await db.scalars(
        select(ItemPriceLatest).where(
            ItemPriceLatest.item_id.in_(id_list),
            ItemPriceLatest.city == _AVG_SENTINEL,
            ItemPriceLatest.quality.in_([2, 3, 4]),
        )
    )).all()
    by_item: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        by_item[r.item_id].append(r.sell_price_min)
    return EscalationPricesOut(
        prices={iid: int(sum(v) / len(v)) for iid, v in by_item.items()}
    )
