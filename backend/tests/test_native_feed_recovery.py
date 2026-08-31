"""Recuperação cronológica do inbox cru dos feeds nativos, sem rede."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import BigInteger, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.models.base import Base
import app.models  # noqa: F401 - registra os modelos no metadata
from app.models.native_feed import NativeFeedItem, NativeFeedStream
from app.services.native_feed import (
    KIND_KILL, apply_native_items, capture_native_stream,
)


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):  # pragma: no cover
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_, compiler, **kw):  # pragma: no cover
    return "INTEGER"


def _raw(source_id: str, when: datetime) -> dict:
    return {"EventId": source_id, "TimeStamp": when.isoformat()}


def _source(raw: dict) -> str:
    return raw["EventId"]


def _occurred(raw: dict) -> datetime:
    return datetime.fromisoformat(raw["TimeStamp"])


async def _engine():
    engine = create_async_engine("sqlite+aiosqlite://", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine


def test_burst_multiplas_paginas_retoma_e_so_aplica_apos_ancora():
    async def run():
        engine = await _engine()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        pages = {
            0: [_raw("n4", now + timedelta(minutes=4)), _raw("n3", now + timedelta(minutes=3))],
            2: [_raw("n2", now + timedelta(minutes=2)), _raw("z-tie", now + timedelta(minutes=1))],
            4: [_raw("a-tie", now + timedelta(minutes=1)), _raw("old-head", now)],
        }

        async def fetch(offset, _limit):
            return pages[offset]

        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(NativeFeedStream(
                kind=KIND_KILL, region="americas", completed_head_source_id="old-head",
            ))
            db.add(NativeFeedItem(
                kind=KIND_KILL, region="americas", source_id="old-head", occurred_at=now,
                payload=_raw("old-head", now), status="applied",
            ))
            await db.commit()
            first = await capture_native_stream(
                db, kind=KIND_KILL, region="americas", page_size=2, offset_limit=20,
                page_budget=1, fetch_page=fetch, source_id=_source, occurred_at=_occurred,
            )
            assert not first.completed
            assert not first.blocked
            assert await apply_native_items(
                db, kind=KIND_KILL, region="americas", apply_item=lambda _db, _item: _unexpected_apply(),
            ) == 0

        # Nova sessão simula restart entre páginas: next_offset e a âncora vêm do banco.
        applied: list[str] = []

        async def apply(_db, item):
            applied.append(item.source_id)

        async with AsyncSession(engine, expire_on_commit=False) as db:
            resumed = await capture_native_stream(
                db, kind=KIND_KILL, region="americas", page_size=2, offset_limit=20,
                page_budget=3, fetch_page=fetch, source_id=_source, occurred_at=_occurred,
            )
            assert resumed.completed
            assert not resumed.blocked
            assert await apply_native_items(
                db, kind=KIND_KILL, region="americas", apply_item=apply,
            ) == 5
            stream = await db.get(NativeFeedStream, {"kind": KIND_KILL, "region": "americas"})
            assert stream is not None and stream.completed_head_source_id == "n4"

        assert applied == ["a-tie", "z-tie", "n2", "n3", "n4"]
        await engine.dispose()

    asyncio.run(run())


async def _unexpected_apply():
    raise AssertionError("inbox não pode aplicar antes de achar a âncora")


def test_falha_no_meio_retem_e_reexecuta_sem_pular_o_proximo():
    async def run():
        engine = await _engine()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        attempts: list[str] = []

        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(NativeFeedStream(
                kind=KIND_KILL, region="europe", completed_head_source_id="old", captured_head_source_id="new",
            ))
            for index, source_id in enumerate(("a", "b", "c")):
                db.add(NativeFeedItem(
                    kind=KIND_KILL,
                    region="europe",
                    source_id=source_id,
                    occurred_at=now + timedelta(minutes=index),
                    payload=_raw(source_id, now + timedelta(minutes=index)),
                ))
            await db.commit()

            failed_once = True

            async def apply(_db, item):
                nonlocal failed_once
                attempts.append(item.source_id)
                if item.source_id == "b" and failed_once:
                    failed_once = False
                    raise RuntimeError("falha transitória")

            assert await apply_native_items(
                db, kind=KIND_KILL, region="europe", apply_item=apply,
            ) == 1
            middle = await db.scalar(
                select(NativeFeedItem).where(NativeFeedItem.source_id == "b")
            )
            assert middle is not None
            assert middle.status == "retry" and middle.attempts == 1
            assert attempts == ["a", "b"]
            stream = await db.get(NativeFeedStream, {"kind": KIND_KILL, "region": "europe"})
            assert stream is not None and stream.completed_head_source_id == "old"

            # O backoff bloqueia c; depois da janela o mesmo b é tentado primeiro.
            middle.next_retry_at = now - timedelta(seconds=1)
            await db.commit()
            assert await apply_native_items(
                db, kind=KIND_KILL, region="europe", apply_item=apply,
            ) == 2
            assert stream.completed_head_source_id == "new"

        assert attempts == ["a", "b", "b", "c"]
        await engine.dispose()

    asyncio.run(run())


def test_battle_confirma_resumo_sem_esperar_eventos_profundos():
    async def run():
        from app.services import battle_tracker

        battle = SimpleNamespace(found_by=None, processing_tier="light")
        calls: list[tuple[dict, str]] = []
        original = battle_tracker.upsert_battle_light

        async def fake_upsert(_db, raw, region):
            calls.append((raw, region))
            return battle

        battle_tracker.upsert_battle_light = fake_upsert
        try:
            item = SimpleNamespace(
                payload={"id": "battle-1"}, region="americas", discovered_by="Ziggs",
            )
            await battle_tracker._apply_battle_item(object(), item)
        finally:
            battle_tracker.upsert_battle_light = original

        assert calls == [({"id": "battle-1"}, "americas")]
        assert battle.found_by == "Ziggs"

    asyncio.run(run())


def test_captura_nova_fronteira_enquanto_backlog_ainda_aguarda_aplicacao():
    async def run():
        engine = await _engine()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        pages = {
            0: [_raw("new2", now + timedelta(minutes=3)), _raw("new1", now + timedelta(minutes=2))],
            2: [_raw("backlog", now + timedelta(minutes=1)), _raw("old", now)],
        }

        async def fetch(offset, _limit):
            return pages[offset]

        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(NativeFeedStream(
                kind=KIND_KILL,
                region="americas",
                completed_head_source_id="old",
                captured_head_source_id="old",
            ))
            db.add_all([
                NativeFeedItem(
                    kind=KIND_KILL, region="americas", source_id="old", occurred_at=now,
                    payload=_raw("old", now), status="applied",
                ),
                NativeFeedItem(
                    kind=KIND_KILL, region="americas", source_id="backlog",
                    occurred_at=now + timedelta(minutes=1), payload=_raw("backlog", now + timedelta(minutes=1)),
                ),
            ])
            await db.commit()

            result = await capture_native_stream(
                db, kind=KIND_KILL, region="americas", page_size=2, offset_limit=20,
                page_budget=2, fetch_page=fetch, source_id=_source, occurred_at=_occurred,
            )
            assert result.completed and not result.blocked
            stream = await db.get(NativeFeedStream, {"kind": KIND_KILL, "region": "americas"})
            assert stream is not None
            assert stream.captured_head_source_id == "new2"
            assert stream.completed_head_source_id == "old"

            applied: list[str] = []

            async def apply(_db, item):
                applied.append(item.source_id)

            assert await apply_native_items(
                db, kind=KIND_KILL, region="americas", apply_item=apply,
            ) == 3
            assert applied == ["backlog", "new1", "new2"]
            assert stream.completed_head_source_id == "new2"

        await engine.dispose()

    asyncio.run(run())


def test_dreno_usa_a_ultima_fronteira_completa_durante_scan_incompleto():
    async def run():
        engine = await _engine()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        applied: list[str] = []

        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(NativeFeedStream(
                kind=KIND_KILL,
                region="americas",
                completed_head_source_id="old",
                captured_head_source_id="boundary",
                scan_active=True,
            ))
            db.add_all([
                NativeFeedItem(
                    kind=KIND_KILL, region="americas", source_id="old", occurred_at=now,
                    payload=_raw("old", now), status="applied",
                ),
                NativeFeedItem(
                    kind=KIND_KILL, region="americas", source_id="pending", occurred_at=now + timedelta(minutes=1),
                    payload=_raw("pending", now + timedelta(minutes=1)),
                ),
                NativeFeedItem(
                    kind=KIND_KILL, region="americas", source_id="boundary", occurred_at=now + timedelta(minutes=2),
                    payload=_raw("boundary", now + timedelta(minutes=2)),
                ),
                NativeFeedItem(
                    kind=KIND_KILL, region="americas", source_id="too-new", occurred_at=now + timedelta(minutes=3),
                    payload=_raw("too-new", now + timedelta(minutes=3)),
                ),
            ])
            await db.commit()

            async def apply(_db, item):
                applied.append(item.source_id)

            assert await apply_native_items(
                db, kind=KIND_KILL, region="americas", apply_item=apply,
            ) == 2

        assert applied == ["pending", "boundary"]
        await engine.dispose()

    asyncio.run(run())


def test_ancora_omitida_da_lista_fecha_pela_fronteira_temporal():
    async def run():
        engine = await _engine()
        now = datetime.now(timezone.utc).replace(microsecond=0)

        pages = {
            0: [
                _raw("new", now + timedelta(minutes=2)),
                _raw("same-time", now),
            ],
            2: [
                _raw("older-than-anchor", now - timedelta(seconds=1)),
            ],
        }

        async def fetch(offset, _limit):
            return pages[offset]

        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(NativeFeedStream(
                kind=KIND_KILL,
                region="asia",
                completed_head_source_id="omitted-anchor",
                captured_head_source_id="omitted-anchor",
            ))
            db.add(NativeFeedItem(
                kind=KIND_KILL, region="asia", source_id="omitted-anchor", occurred_at=now,
                payload=_raw("omitted-anchor", now), status="applied",
            ))
            await db.commit()

            result = await capture_native_stream(
                db, kind=KIND_KILL, region="asia", page_size=2, offset_limit=20,
                page_budget=2, fetch_page=fetch, source_id=_source, occurred_at=_occurred,
            )
            assert result.completed and not result.blocked
            stream = await db.get(NativeFeedStream, {"kind": KIND_KILL, "region": "asia"})
            assert stream is not None and stream.captured_head_source_id == "new"

        await engine.dispose()

    asyncio.run(run())


def test_captura_nao_espera_aplicacao_lenta_do_item_mais_antigo():
    async def run():
        engine = await _engine()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        started = asyncio.Event()
        release = asyncio.Event()

        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(NativeFeedStream(
                kind=KIND_KILL,
                region="europe",
                completed_head_source_id="old",
                captured_head_source_id="old",
            ))
            db.add_all([
                NativeFeedItem(
                    kind=KIND_KILL, region="europe", source_id="old", occurred_at=now,
                    payload=_raw("old", now), status="applied",
                ),
                NativeFeedItem(
                    kind=KIND_KILL, region="europe", source_id="backlog",
                    occurred_at=now + timedelta(minutes=1), payload=_raw("backlog", now + timedelta(minutes=1)),
                ),
            ])
            await db.commit()

        async def apply(_db, _item):
            started.set()
            await release.wait()

        async with AsyncSession(engine, expire_on_commit=False) as apply_db:
            task = asyncio.create_task(apply_native_items(
                apply_db, kind=KIND_KILL, region="europe", apply_item=apply, batch_size=1,
            ))
            await started.wait()

            async def fetch(_offset, _limit):
                return [_raw("new", now + timedelta(minutes=2)), _raw("old", now)]

            async with AsyncSession(engine, expire_on_commit=False) as capture_db:
                result = await capture_native_stream(
                    capture_db, kind=KIND_KILL, region="europe", page_size=2, offset_limit=20,
                    page_budget=1, fetch_page=fetch, source_id=_source, occurred_at=_occurred,
                )
                assert result.completed and not result.blocked
                stream = await capture_db.get(NativeFeedStream, {"kind": KIND_KILL, "region": "europe"})
                assert stream is not None and stream.captured_head_source_id == "new"

            release.set()
            assert await task == 1

        await engine.dispose()

    asyncio.run(run())


def test_recuperacao_expirada_nao_abandona_ancora():
    async def run():
        engine = await _engine()
        now = datetime.now(timezone.utc).replace(microsecond=0)

        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add_all([
                NativeFeedStream(
                    kind=KIND_KILL, region="americas", completed_head_source_id="old",
                    captured_head_source_id="old", scan_active=True,
                    scan_anchor_source_id="lost", scan_head_source_id="visible",
                    next_offset=0, scan_started_at=now - timedelta(minutes=16),
                ),
                NativeFeedItem(
                    kind=KIND_KILL, region="americas", source_id="old", occurred_at=now,
                    payload=_raw("old", now), status="applied",
                ),
                NativeFeedItem(
                    kind=KIND_KILL, region="americas", source_id="visible",
                    occurred_at=now + timedelta(minutes=1), payload=_raw("visible", now + timedelta(minutes=1)),
                ),
            ])
            await db.commit()
            async def fetch(_offset, _limit):
                return [_raw("visible", now + timedelta(minutes=1)), _raw("lost", now)]

            result = await capture_native_stream(
                db, kind=KIND_KILL, region="americas", page_size=2, offset_limit=20,
                page_budget=1, fetch_page=fetch, source_id=_source, occurred_at=_occurred,
            )
            stream = await db.get(NativeFeedStream, {"kind": KIND_KILL, "region": "americas"})
            assert result.completed and stream is not None
            assert stream.captured_head_source_id == "visible"
            assert not stream.scan_active

        await engine.dispose()

    asyncio.run(run())


def test_janela_sem_ancora_adota_fronteira_visivel_e_aplica():
    async def run():
        engine = await _engine()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        pages = {
            0: [_raw("new2", now + timedelta(minutes=2)), _raw("new1", now + timedelta(minutes=1))],
            2: [_raw("older2", now - timedelta(minutes=1)), _raw("older1", now - timedelta(minutes=2))],
        }

        async def fetch(offset, _limit):
            return pages[offset]

        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(NativeFeedStream(
                kind=KIND_KILL, region="asia", completed_head_source_id="lost-head",
            ))
            await db.commit()
            result = await capture_native_stream(
                db, kind=KIND_KILL, region="asia", page_size=2, offset_limit=4,
                page_budget=3, fetch_page=fetch, source_id=_source, occurred_at=_occurred,
            )
            assert result.completed and not result.blocked
            applied: list[str] = []

            async def apply(_db, item):
                applied.append(item.source_id)

            assert await apply_native_items(
                db, kind=KIND_KILL, region="asia", apply_item=apply,
            ) == 4
            stream = await db.get(NativeFeedStream, {"kind": KIND_KILL, "region": "asia"})
            assert stream is not None
            assert stream.completed_head_source_id == "new2"
            assert not stream.scan_blocked
            assert applied == ["older1", "older2", "new1", "new2"]

        await engine.dispose()

    asyncio.run(run())
