"""Regressões do protocolo chunked de reports grandes do scanner VPS.

Roda com pytest ou:
    PYTHONPATH=. python tests/test_scan_report_chunks.py
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import BigInteger, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.models.base import Base
from app.services import scan_dispatcher
from app.models.scan_worker import (
    ScanIngestPayload,
    ScanReportChunk,
    ScanStreamState,
    ScanWorker,
    ScanWorkerRegionMetric,
    ScanWorkTask,
)
from app.services.scan_dispatcher import report_chunk


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(_type, _compiler, **_kw):  # pragma: no cover - shim de teste
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(_type, _compiler, **_kw):  # pragma: no cover - shim de teste
    return "INTEGER"


async def _session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        ScanWorker.__table__, ScanWorkTask.__table__, ScanIngestPayload.__table__,
        ScanReportChunk.__table__, ScanStreamState.__table__, ScanWorkerRegionMetric.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_chunks_so_enfileiram_quando_o_payload_esta_completo():
    engine, Session = await _session()
    original_now = scan_dispatcher._now
    scan_dispatcher._now = lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    payload = [{"_deep_process": True, "battle_id": 99, "raw": {"id": 99}, "events": [{"EventId": "a"}]}]
    raw = json.dumps(payload, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    pieces = [raw[: len(raw) // 2], raw[len(raw) // 2:]]
    lease = "a" * 32
    try:
        async with Session() as db:
            db.add(ScanWorker(worker_id="worker", name="worker", api_token_hash="x"))
            db.add(ScanWorkTask(
                region="europe", feed_type="deep_process", page_offset=99,
                status="claimed", claimed_by="worker", lease_token=lease,
                # SQLite perde tzinfo; o serviço real roda em Postgres UTC.
                claim_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=2)).replace(tzinfo=None),
            ))
            await db.commit()
            task = await db.scalar(select(ScanWorkTask))
            assert task is not None

            accepted, rejected = await report_chunk(
                db, "worker", task.id, lease, 1, 0,
                payload_chunk=base64.b64encode(pieces[0]).decode(), payload_sha256=digest,
                chunk_index=0, chunk_count=2,
            )
            assert (accepted, rejected) == (0, 0)
            assert await db.scalar(select(ScanIngestPayload)) is None

            accepted, rejected = await report_chunk(
                db, "worker", task.id, lease, 1, 0,
                payload_chunk=base64.b64encode(pieces[1]).decode(), payload_sha256=digest,
                chunk_index=1, chunk_count=2,
            )
            assert (accepted, rejected) == (1, 0)
            queued = await db.scalar(select(ScanIngestPayload))
            assert queued is not None and queued.payload == payload
            assert await db.scalar(select(ScanReportChunk)) is None
    finally:
        scan_dispatcher._now = original_now
        await engine.dispose()


async def test_rejeita_upload_que_excede_o_limite_agregado():
    engine, Session = await _session()
    original_now = scan_dispatcher._now
    scan_dispatcher._now = lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    lease = "b" * 32
    try:
        async with Session() as db:
            db.add(ScanWorker(worker_id="worker", name="worker", api_token_hash="x"))
            db.add(ScanWorkTask(
                region="europe", feed_type="deep_process", page_offset=100,
                status="claimed", claimed_by="worker", lease_token=lease,
                claim_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=2)).replace(tzinfo=None),
            ))
            await db.commit()
            task = await db.scalar(select(ScanWorkTask))
            assert task is not None
            try:
                await report_chunk(
                    db, "worker", task.id, lease, 1, 0,
                    payload_chunk=base64.b64encode(b"[]").decode(),
                    payload_sha256=hashlib.sha256(b"[]").hexdigest(),
                    chunk_index=0,
                    chunk_count=scan_dispatcher.MAX_REPORT_CHUNKS + 1,
                )
            except ValueError as exc:
                assert "limite" in str(exc)
            else:
                raise AssertionError("upload acima do limite agregado foi aceito")
    finally:
        scan_dispatcher._now = original_now
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(test_chunks_so_enfileiram_quando_o_payload_esta_completo())
    asyncio.run(test_rejeita_upload_que_excede_o_limite_agregado())
    print("scan report chunks OK")
