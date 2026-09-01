"""Scan — endpoints para VPS workers de feed polling.

Workers usam header X-Scan-Secret (segredo compartilhado). O dado reportado
vem direto da API pública do Albion (workers são nossos), o backend faz
upsert sem revalidar.

Rotas:
  POST /scan/register  -> upsert worker
  POST /scan/heartbeat -> update heartbeat
  POST /scan/claim     -> pega proxima tarefa (204 = sem trabalho)
  POST /scan/report    -> reporta dados crus do feed, backend faz upsert
  GET  /scan/stats     -> dashboard (sem auth)
"""
from __future__ import annotations

import gzip
import io
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.models.scan_worker import FEED_PAGE_SIZE
from app.services import scan_dispatcher

router = APIRouter(prefix="/scan", tags=["scan"])

_SCAN_SECRET = os.getenv("SCAN_SECRET")
_MAX_REPORT_WIRE_BYTES = 5 * 1024 * 1024
_MAX_REPORT_BYTES = 20 * 1024 * 1024


def _check_bootstrap_secret(raw: str | None) -> None:
    if not _SCAN_SECRET or raw != _SCAN_SECRET:
        raise HTTPException(401, "unauthorized")


class RegisterIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    region_pref: str | None = Field(default=None, max_length=16)
    # Tunnel metadata — opcional. Quando preenchido, a VPS aparece no
    # /vps-manifest.json (companion + site) automaticamente.
    vps_label: str | None = Field(default=None, max_length=64)
    vps_country: str | None = Field(default=None, max_length=64)
    vps_endpoint: str | None = Field(default=None, max_length=128)
    vps_server_pubkey: str | None = Field(default=None, max_length=128)
    vps_ping_url: str | None = Field(default=None, max_length=256)


class RegisterOut(BaseModel):
    worker_id: str
    status: str
    credential: str


@router.post("/register")
async def scan_register(
    body: RegisterIn,
    x_scan_secret: str | None = Header(None),
    db: AsyncSession = Depends(get_async_session),
) -> RegisterOut:
    _check_bootstrap_secret(x_scan_secret)
    try:
        w, credential = await scan_dispatcher.register_worker(
            db, body.worker_id, body.name, body.region_pref,
            vps_label=body.vps_label,
            vps_country=body.vps_country,
            vps_endpoint=body.vps_endpoint,
            vps_server_pubkey=body.vps_server_pubkey,
            vps_ping_url=body.vps_ping_url,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return RegisterOut(worker_id=w.worker_id, status=w.status, credential=credential)


class HeartbeatIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)


@router.post("/heartbeat")
async def scan_heartbeat(
    body: HeartbeatIn,
    x_scan_secret: str | None = Header(None),
    db: AsyncSession = Depends(get_async_session),
) -> Response:
    try:
        await scan_dispatcher.authenticate_worker(db, body.worker_id, x_scan_secret)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    await scan_dispatcher.heartbeat(db, body.worker_id)
    return Response(status_code=204)


class ClaimIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    region: str | None = Field(default=None, max_length=16)


class ClaimOut(BaseModel):
    task_id: int
    lease_token: str
    region: str
    feed_type: str
    page_offset: int


@router.post("/claim")
async def scan_claim(
    body: ClaimIn,
    x_scan_secret: str | None = Header(None),
    db: AsyncSession = Depends(get_async_session),
) -> ClaimOut | None:
    try:
        await scan_dispatcher.authenticate_worker(db, body.worker_id, x_scan_secret)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    task = await scan_dispatcher.claim_work(db, body.worker_id, body.region)
    if task is None:
        raise HTTPException(204)
    return ClaimOut(
        task_id=task.id,
        lease_token=task.lease_token or "",
        region=task.region,
        feed_type=task.feed_type,
        page_offset=task.page_offset,
    )


class ReportIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    task_id: int
    lease_token: str = Field(min_length=32, max_length=32)
    found_count: int = Field(default=0)
    error_count: int = Field(default=0)
    data: list[dict[str, Any]] | None = Field(default=None, max_length=FEED_PAGE_SIZE)
    payload_chunk: str | None = Field(default=None, max_length=4_500_000)
    payload_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    chunk_index: int | None = Field(default=None, ge=0)
    chunk_count: int | None = Field(default=None, ge=1, le=128)
    latency_ms: int | None = Field(default=None, ge=0, le=120_000)


class ReportOut(BaseModel):
    accepted: int
    rejected: int


def _decode_report_body(raw: bytes, encoding: str) -> bytes:
    if encoding in ("", "identity"):
        if len(raw) > _MAX_REPORT_BYTES:
            raise HTTPException(413, "report payload too large")
        return raw
    if encoding != "gzip":
        raise HTTPException(415, "unsupported content encoding")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
            decoded = compressed.read(_MAX_REPORT_BYTES + 1)
    except (gzip.BadGzipFile, EOFError, OSError) as exc:
        raise HTTPException(400, "invalid gzip payload") from exc
    if len(decoded) > _MAX_REPORT_BYTES:
        raise HTTPException(413, "report payload too large")
    return decoded


async def _read_report(request: Request) -> ReportIn:
    encoding = request.headers.get("content-encoding", "").lower().strip()
    wire_limit = _MAX_REPORT_WIRE_BYTES if encoding == "gzip" else _MAX_REPORT_BYTES
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > wire_limit:
            raise HTTPException(413, "report payload too large")
    try:
        return ReportIn.model_validate_json(_decode_report_body(bytes(raw), encoding))
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()) from exc


@router.post("/report", status_code=202)
async def scan_report(
    request: Request,
    x_scan_secret: str | None = Header(None),
    x_scan_worker: str | None = Header(None),
    db: AsyncSession = Depends(get_async_session),
) -> ReportOut:
    try:
        await scan_dispatcher.authenticate_worker(db, x_scan_worker or "", x_scan_secret)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    body = await _read_report(request)
    if body.worker_id != x_scan_worker:
        raise HTTPException(401, "worker identity mismatch")
    chunk_fields = (body.payload_sha256, body.chunk_index, body.chunk_count)
    try:
        if body.payload_chunk is not None:
            if body.data is not None or any(value is None for value in chunk_fields):
                raise ValueError("chunk de report inválido")
            accepted, rejected = await scan_dispatcher.report_chunk(
                db, body.worker_id, body.task_id, body.lease_token,
                body.found_count, body.error_count,
                payload_chunk=body.payload_chunk,
                payload_sha256=body.payload_sha256 or "",
                chunk_index=body.chunk_index if body.chunk_index is not None else -1,
                chunk_count=body.chunk_count if body.chunk_count is not None else 0,
                latency_ms=body.latency_ms,
            )
        elif any(value is not None for value in chunk_fields):
            raise ValueError("metadados de chunk incompletos")
        else:
            accepted, rejected = await scan_dispatcher.report_work(
                db, body.worker_id, body.task_id, body.lease_token,
                body.found_count, body.error_count,
                data=body.data,
                latency_ms=body.latency_ms,
            )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return ReportOut(accepted=accepted, rejected=rejected)


class RenewIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    task_id: int
    lease_token: str = Field(min_length=32, max_length=32)


@router.post("/renew")
async def scan_renew(
    body: RenewIn,
    x_scan_secret: str | None = Header(None),
    db: AsyncSession = Depends(get_async_session),
) -> dict:
    try:
        await scan_dispatcher.authenticate_worker(db, body.worker_id, x_scan_secret)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    try:
        expires_at = await scan_dispatcher.renew_lease(
            db, body.worker_id, body.task_id, body.lease_token
        )
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"expires_at": expires_at.isoformat()}


@router.get("/pressure")
async def scan_pressure(x_scan_secret: str | None = Header(None)) -> dict:
    _check_bootstrap_secret(x_scan_secret)
    return scan_dispatcher.pressure_status()


@router.get("/stats")
async def scan_stats(db: AsyncSession = Depends(get_async_session)) -> dict:
    return await scan_dispatcher.get_worker_stats(db)


@router.post("/workers/{worker_id}/{action}", status_code=204)
async def scan_control_worker(
    worker_id: str,
    action: str,
    x_scan_secret: str | None = Header(None),
    db: AsyncSession = Depends(get_async_session),
) -> Response:
    _check_bootstrap_secret(x_scan_secret)
    try:
        await scan_dispatcher.control_worker(db, worker_id, action)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(status_code=204)


@router.post("/streams/{region}/{feed_type}/{action}", status_code=204)
async def scan_control_stream(
    region: str,
    feed_type: str,
    action: str,
    x_scan_secret: str | None = Header(None),
    db: AsyncSession = Depends(get_async_session),
) -> Response:
    _check_bootstrap_secret(x_scan_secret)
    try:
        await scan_dispatcher.control_stream(db, region, feed_type, action)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(status_code=204)


@router.post("/tasks/{task_id}/retry", status_code=204)
async def scan_retry_task(
    task_id: int,
    x_scan_secret: str | None = Header(None),
    db: AsyncSession = Depends(get_async_session),
) -> Response:
    _check_bootstrap_secret(x_scan_secret)
    try:
        await scan_dispatcher.retry_task(db, task_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(status_code=204)
