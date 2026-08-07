"""Scan - endpoints para VPS workers de scanning de batalhas.

Sem auth de sessao. Workers usam header X-Scan-Secret (segredo compartilhado).
O dado reportado e revalidado contra a API publica do Albion no report
(scan_dispatcher.report_work -> upsert_battle_light), nunca confiamos cegamente
no client - mesma doutrina do companion scan.

Rotas:
  POST /scan/register  -> upsert worker
  POST /scan/heartbeat -> update heartbeat
  POST /scan/claim     -> pega proxima tarefa (204 = sem trabalho)
  POST /scan/report    -> reporta resultados (found/missing/errors), revalida
  GET  /scan/stats     -> dashboard (sem auth)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.models.scan_worker import WORK_RANGE_SIZE
from app.services import scan_dispatcher

router = APIRouter(prefix="/scan", tags=["scan"])

_SCAN_SECRET = "ziggs-scan-dev-v1"


def _check_secret(raw: str | None) -> None:
    if raw != _SCAN_SECRET:
        raise HTTPException(401, "unauthorized")


class RegisterIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    region_pref: str | None = Field(default=None, max_length=16)


class RegisterOut(BaseModel):
    worker_id: str
    status: str


@router.post("/register")
async def scan_register(
    body: RegisterIn,
    x_scan_secret: str | None = Header(None),
    db: AsyncSession = Depends(get_async_session),
) -> RegisterOut:
    _check_secret(x_scan_secret)
    w = await scan_dispatcher.register_worker(db, body.worker_id, body.name, body.region_pref)
    return RegisterOut(worker_id=w.worker_id, status=w.status)


class HeartbeatIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)


@router.post("/heartbeat")
async def scan_heartbeat(
    body: HeartbeatIn,
    x_scan_secret: str | None = Header(None),
    db: AsyncSession = Depends(get_async_session),
) -> Response:
    _check_secret(x_scan_secret)
    await scan_dispatcher.heartbeat(db, body.worker_id)
    return Response(status_code=204)


class ClaimIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    region: str | None = Field(default=None, max_length=16)


class ClaimOut(BaseModel):
    task_id: int
    region: str
    battle_id_start: int
    battle_id_end: int


@router.post("/claim")
async def scan_claim(
    body: ClaimIn,
    x_scan_secret: str | None = Header(None),
    db: AsyncSession = Depends(get_async_session),
) -> ClaimOut | None:
    _check_secret(x_scan_secret)
    task = await scan_dispatcher.claim_work(db, body.worker_id, body.region)
    if task is None:
        raise HTTPException(204)  # No Content
    return ClaimOut(
        task_id=task.id,
        region=task.region,
        battle_id_start=task.battle_id_start,
        battle_id_end=task.battle_id_end,
    )


class ReportIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    task_id: int
    found: list[int] = Field(default_factory=list, max_length=WORK_RANGE_SIZE)
    missing: list[int] = Field(default_factory=list, max_length=WORK_RANGE_SIZE)
    errors: list[int] = Field(default_factory=list, max_length=WORK_RANGE_SIZE)


class ReportOut(BaseModel):
    accepted: int
    rejected: int


@router.post("/report")
async def scan_report(
    body: ReportIn,
    x_scan_secret: str | None = Header(None),
    db: AsyncSession = Depends(get_async_session),
) -> ReportOut:
    _check_secret(x_scan_secret)
    try:
        accepted, rejected = await scan_dispatcher.report_work(
            db, body.worker_id, body.task_id,
            body.found, body.missing, body.errors,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return ReportOut(accepted=accepted, rejected=rejected)


@router.get("/stats")
async def scan_stats(db: AsyncSession = Depends(get_async_session)) -> dict:
    return await scan_dispatcher.get_worker_stats(db)