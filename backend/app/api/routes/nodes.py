"""Rotas de nodes (calendário de nodes do Albion), escopadas por guilda.

Dashboard/guia do subsistema de nodes — espelha o que o bot-v2 renderiza no
Discord. O bot usa as rotas `/bot/guilds/{g}/nodes/*` (em `auth.py`).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import deps
from app.api.schemas.nodes import (
    CalendarIn, CalendarOut, MapIn, MapsOut, NearNodesOut,
    NodeDefIn, NodeDefOut, NodeDefUpdate, NodeEventIn, NodeEventLogOut, NodeEventOut,
)
from app.models.tenancy import Guild, User
from app.services import nodes as svc

router = APIRouter(prefix="/guilds/{guild_id}/nodes", tags=["nodes"])


def _def_out(d) -> NodeDefOut:
    return NodeDefOut(id=d.id, name=d.name, emoji=d.emoji, weight=d.weight, sort=d.sort)


def _ev_out(e) -> NodeEventOut:
    return NodeEventOut(
        id=e.id, guild_id=e.guild_id, channel_id=e.channel_id, node_type=e.node_type,
        map_name=e.map_name, spawn_at=e.spawn_at, added_by_id=e.added_by_id,
        added_by_name=e.added_by_name,
    )


def _log_out(l) -> NodeEventLogOut:
    return NodeEventLogOut(
        id=l.id, node_type=l.node_type, map_name=l.map_name, spawn_at=l.spawn_at,
        scout_id=l.scout_id, scout_name=l.scout_name, logged_at=l.logged_at,
        captured=bool(l.captured), sold_value=int(l.sold_value), event_id=l.event_id,
    )


# ── events (vivos) ───────────────────────────────────────────────────────────

@router.get("", response_model=list[NodeEventOut])
def list_events(
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _m=Depends(deps.require_permission("nodes.view")),
):
    return [_ev_out(e) for e in svc.list_events(db, guild.id)]


@router.post("", response_model=NodeEventOut, status_code=201)
def create_event(
    payload: NodeEventIn,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    user: User | None = Depends(deps.optional_user),
    _m=Depends(deps.require_permission("nodes.manage")),
):
    try:
        e = svc.add_event(
            db, guild.id, payload.node_type, payload.map_name, payload.spawn_at,
            channel_id=payload.channel_id,
            added_by_id=user.id if user else None,
            added_by_name=(user.global_name or user.username) if user else None,
            allow_duplicate=payload.allow_duplicate,
        )
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return _ev_out(e)


@router.delete("/{event_id}", status_code=204)
def delete_event(
    event_id: int,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    user: User | None = Depends(deps.optional_user),
    _m=Depends(deps.require_permission("nodes.manage")),
):
    if not svc.delete_event(
        db, guild.id, event_id,
        actor_id=user.id if user else None, actor_source="site",
    ):
        raise HTTPException(status_code=404, detail="node não encontrado")
    db.commit()


# ── defs ─────────────────────────────────────────────────────────────────────

@router.get("/defs", response_model=list[NodeDefOut])
def list_defs(
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _m=Depends(deps.require_permission("nodes.view")),
):
    return [_def_out(d) for d in svc.list_defs(db, guild.id)]


@router.post("/defs", response_model=NodeDefOut, status_code=201)
def upsert_def(
    payload: NodeDefIn,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _m=Depends(deps.require_permission("nodes.manage")),
):
    d = svc.upsert_def(db, guild.id, payload.name, payload.emoji, payload.weight, payload.sort)
    db.commit()
    return _def_out(d)


@router.delete("/defs/{name}", status_code=204)
def remove_def(
    name: str,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _m=Depends(deps.require_permission("nodes.manage")),
):
    svc.remove_def(db, guild.id, name)
    db.commit()


@router.patch("/defs/{def_id}", response_model=NodeDefOut)
def update_def(
    def_id: int,
    payload: NodeDefUpdate,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _m=Depends(deps.require_permission("nodes.manage")),
):
    try:
        d = svc.update_def(db, guild.id, def_id,
                          name=payload.name, emoji=payload.emoji, weight=payload.weight)
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if d is None:
        raise HTTPException(status_code=404, detail="node não encontrado")
    db.commit()
    return _def_out(d)


# ── maps ─────────────────────────────────────────────────────────────────────

@router.get("/maps", response_model=MapsOut)
def get_maps(
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _m=Depends(deps.require_permission("nodes.view")),
):
    return MapsOut(**svc.list_maps(db, guild.id))


@router.post("/maps", response_model=MapsOut)
def add_map(
    payload: MapIn,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _m=Depends(deps.require_permission("nodes.manage")),
):
    try:
        svc.add_map(db, guild.id, payload.map_name)
    except svc.ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    return MapsOut(**svc.list_maps(db, guild.id))


@router.delete("/maps/{map_name}", response_model=MapsOut)
def remove_map(
    map_name: str,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _m=Depends(deps.require_permission("nodes.manage")),
):
    svc.remove_map(db, guild.id, map_name)
    db.commit()
    return MapsOut(**svc.list_maps(db, guild.id))


# ── log + near-cta + calendar ────────────────────────────────────────────────

@router.get("/log", response_model=list[NodeEventLogOut])
def get_log(
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    limit: int = Query(200, ge=1, le=1000),
    _m=Depends(deps.require_permission("nodes.view")),
):
    return [_log_out(l) for l in svc.list_log(db, guild.id, limit)]


@router.get("/near", response_model=NearNodesOut)
def near_nodes(
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    ts: datetime | None = Query(None),
    window: int = Query(svc.NEAR_CTA_WINDOW_SECONDS, ge=0, le=86400),
    _m=Depends(deps.require_permission("nodes.view")),
):
    ts = ts or datetime.now(timezone.utc)
    nodes = svc.near_cta(db, guild.id, ts, window)
    return NearNodesOut(ts=ts, window_seconds=window, nodes=[_log_out(l) for l in nodes])


@router.get("/calendar", response_model=CalendarOut)
def get_calendar(
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _m=Depends(deps.require_permission("nodes.view")),
):
    row = svc.get_calendar(db, guild.id)
    return CalendarOut(guild_id=guild.id, channel_id=row.channel_id if row else None,
                       message_id=row.message_id if row else None)


@router.post("/calendar", response_model=CalendarOut)
def set_calendar(
    payload: CalendarIn,
    guild: Guild = Depends(deps.tenant_guild),
    db: Session = Depends(deps.db_session),
    _m=Depends(deps.require_permission("nodes.manage")),
):
    row = svc.set_calendar(db, guild.id, channel_id=payload.channel_id, message_id=payload.message_id)
    db.commit()
    return CalendarOut(guild_id=guild.id, channel_id=row.channel_id, message_id=row.message_id)