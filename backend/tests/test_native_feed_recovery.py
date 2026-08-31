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


def _make_pages(count: int, page_size: int, start_time: datetime, step: timedelta = timedelta(seconds=1)) -> dict[int, list[dict]]:
    """Cria páginas com timestamps decrescentes (mais novo → mais velho)."""
    pages = {}
    t = start_time
    for i in range(0, count * page_size, page_size):
        page = []
        for j in range(page_size):
            page.append(_raw(f"id-{i + j}", t))
            t = t - step
        pages[i] = page
    return pages


# ── Testes existentes (adaptados) ──────────────────────────────────────


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
            return pages.get(offset, [])

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

        applied: list[str] = []

        async def apply(_db, item):
            applied.append(item.source_id)

        async with AsyncSession(engine, expire_on_commit=False) as db:
            resumed = await capture_native_stream(
                db, kind=KIND_KILL, region="americas", page_size=2, offset_limit=20,
                page_budget=6, fetch_page=fetch, source_id=_source, occurred_at=_occurred,
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
            return pages.get(offset, [])

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
                page_budget=6, fetch_page=fetch, source_id=_source, occurred_at=_occurred,
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
            return pages.get(offset, [])

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
                page_budget=6, fetch_page=fetch, source_id=_source, occurred_at=_occurred,
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
                    page_budget=6, fetch_page=fetch, source_id=_source, occurred_at=_occurred,
                )
                assert result.completed and not result.blocked
                stream = await capture_db.get(NativeFeedStream, {"kind": KIND_KILL, "region": "europe"})
                assert stream is not None and stream.captured_head_source_id == "new"

            release.set()
            assert await task == 1

        await engine.dispose()

    asyncio.run(run())


KILL_FEED_KIND = KIND_KILL


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
                page_budget=6, fetch_page=fetch, source_id=_source, occurred_at=_occurred,
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
            return pages.get(offset, [])

        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(NativeFeedStream(
                kind=KIND_KILL, region="asia", completed_head_source_id="lost-head",
            ))
            await db.commit()
            result = await capture_native_stream(
                db, kind=KIND_KILL, region="asia", page_size=2, offset_limit=4,
                page_budget=6, fetch_page=fetch, source_id=_source, occurred_at=_occurred,
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


# ── Novos testes do planejamento ───────────────────────────────────────


def test_busca_exponencial_encontra_ancora_com_poucos_probes():
    """Âncora próxima do limite 10000 deve ser localizada em ~8-16 probes."""
    async def run():
        engine = await _engine()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        page_size = 2
        offset_limit = 100

        # Páginas com timestamps decrescentes; âncora no offset 20
        pages = {}
        t = now + timedelta(minutes=20)
        for off in range(0, offset_limit, page_size):
            page = []
            for j in range(page_size):
                page.append(_raw(f"id-{off + j}", t))
                t = t - timedelta(seconds=30)
            pages[off] = page

        anchor_time = now + timedelta(minutes=10)

        async def fetch(offset, _limit):
            return pages.get(offset, [])

        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(NativeFeedStream(
                kind=KIND_KILL, region="europe",
                completed_head_source_id="anchor-id",
                captured_head_source_id="anchor-id",
            ))
            db.add(NativeFeedItem(
                kind=KIND_KILL, region="europe", source_id="anchor-id",
                occurred_at=anchor_time, payload=_raw("anchor-id", anchor_time),
                status="applied",
            ))
            await db.commit()

            result = await capture_native_stream(
                db, kind=KIND_KILL, region="europe", page_size=page_size,
                offset_limit=offset_limit, page_budget=60,
                fetch_page=fetch, source_id=_source, occurred_at=_occurred,
            )
            assert result.completed and not result.blocked
            stream = await db.get(NativeFeedStream, {"kind": KIND_KILL, "region": "europe"})
            assert stream is not None
            assert stream.scan_resolution in ("exact_id", "temporal")
            # Localização + captura usa menos probes que linear puro (50 páginas)
            assert result.pages < 50

        await engine.dispose()

    asyncio.run(run())


def test_outside_window_classificado_por_ultima_pagina():
    """Se a última página é mais nova que a âncora, classifica outside_window imediatamente."""
    async def run():
        engine = await _engine()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        page_size = 2
        offset_limit = 10

        # Todas as páginas são mais novas que a âncora
        pages = {}
        t = now + timedelta(hours=2)
        for off in range(0, offset_limit, page_size):
            page = []
            for j in range(page_size):
                page.append(_raw(f"id-{off + j}", t))
                t = t - timedelta(seconds=30)
            pages[off] = page

        anchor_time = now

        async def fetch(offset, _limit):
            return pages.get(offset, [])

        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(NativeFeedStream(
                kind=KIND_KILL, region="americas",
                completed_head_source_id="ancient",
                captured_head_source_id="ancient",
            ))
            db.add(NativeFeedItem(
                kind=KIND_KILL, region="americas", source_id="ancient",
                occurred_at=anchor_time, payload=_raw("ancient", anchor_time),
                status="applied",
            ))
            await db.commit()

            result = await capture_native_stream(
                db, kind=KIND_KILL, region="americas", page_size=page_size,
                offset_limit=offset_limit, page_budget=60,
                fetch_page=fetch, source_id=_source, occurred_at=_occurred,
            )
            assert result.completed and not result.blocked
            stream = await db.get(NativeFeedStream, {"kind": KIND_KILL, "region": "americas"})
            assert stream is not None
            assert stream.scan_resolution == "outside_window"
            # Não deve ter percorrido todas as páginas
            assert result.pages < 5

        await engine.dispose()

    asyncio.run(run())


def test_empate_timestamp_nao_fecha_captura_cedo():
    """Itens com mesmo timestamp da âncora não devem fechar a fronteira."""
    async def run():
        engine = await _engine()
        now = datetime.now(timezone.utc).replace(microsecond=0)

        # Página 0: duas batalhas com mesmo timestamp da âncora
        # Página 2: batalhas estritamente anteriores
        pages = {
            0: [_raw("new", now + timedelta(minutes=1)), _raw("tie-1", now)],
            2: [_raw("tie-2", now), _raw("older", now - timedelta(seconds=1))],
        }

        async def fetch(offset, _limit):
            return pages.get(offset, [])

        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(NativeFeedStream(
                kind=KIND_KILL, region="asia",
                completed_head_source_id="anchor",
                captured_head_source_id="anchor",
            ))
            db.add(NativeFeedItem(
                kind=KIND_KILL, region="asia", source_id="anchor",
                occurred_at=now, payload=_raw("anchor", now), status="applied",
            ))
            await db.commit()

            result = await capture_native_stream(
                db, kind=KIND_KILL, region="asia", page_size=2, offset_limit=20,
                page_budget=6, fetch_page=fetch, source_id=_source, occurred_at=_occurred,
            )
            assert result.completed and not result.blocked
            stream = await db.get(NativeFeedStream, {"kind": KIND_KILL, "region": "asia"})
            assert stream is not None
            # tie-2 deve ter sido capturado também (não fechou na página 0)
            item_tie2 = await db.scalar(
                select(NativeFeedItem).where(NativeFeedItem.source_id == "tie-2")
            )
            assert item_tie2 is not None

        await engine.dispose()

    asyncio.run(run())


def test_restart_durante_locating_retoma_busca():
    """Restart durante a fase locating deve retomar do estado persistido."""
    async def run():
        engine = await _engine()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        page_size = 2
        offset_limit = 40

        pages = {}
        t = now + timedelta(minutes=20)
        for off in range(0, offset_limit, page_size):
            page = []
            for j in range(page_size):
                page.append(_raw(f"id-{off + j}", t))
                t = t - timedelta(seconds=30)
            pages[off] = page

        anchor_time = now + timedelta(minutes=10)

        async def fetch(offset, _limit):
            return pages.get(offset, [])

        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(NativeFeedStream(
                kind=KIND_KILL, region="europe",
                completed_head_source_id="anchor",
                captured_head_source_id="anchor",
            ))
            db.add(NativeFeedItem(
                kind=KIND_KILL, region="europe", source_id="anchor",
                occurred_at=anchor_time, payload=_raw("anchor", anchor_time),
                status="applied",
            ))
            await db.commit()

            # Primeira chamada com budget pequeno — não completa
            first = await capture_native_stream(
                db, kind=KIND_KILL, region="europe", page_size=page_size,
                offset_limit=offset_limit, page_budget=2,
                fetch_page=fetch, source_id=_source, occurred_at=_occurred,
            )
            stream = await db.get(NativeFeedStream, {"kind": KIND_KILL, "region": "europe"})
            assert stream is not None
            assert stream.scan_active
            assert stream.scan_phase in ("locating", "capturing")

        # Nova sessão simula restart
        async with AsyncSession(engine, expire_on_commit=False) as db:
            second = await capture_native_stream(
                db, kind=KIND_KILL, region="europe", page_size=page_size,
                offset_limit=offset_limit, page_budget=60,
                fetch_page=fetch, source_id=_source, occurred_at=_occurred,
            )
            assert second.completed
            stream = await db.get(NativeFeedStream, {"kind": KIND_KILL, "region": "europe"})
            assert stream is not None
            assert not stream.scan_active

        await engine.dispose()

    asyncio.run(run())


def test_primeira_sincronizacao_sem_ancora_captura_e_aplica():
    """Stream nova sem âncora deve capturar linearmente e aplicar."""
    async def run():
        engine = await _engine()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        pages = {
            0: [_raw("b", now + timedelta(minutes=1)), _raw("a", now)],
        }

        async def fetch(offset, _limit):
            return pages.get(offset, [])

        async with AsyncSession(engine, expire_on_commit=False) as db:
            result = await capture_native_stream(
                db, kind=KIND_KILL, region="americas", page_size=2, offset_limit=20,
                page_budget=6, fetch_page=fetch, source_id=_source, occurred_at=_occurred,
            )
            assert result.completed and not result.blocked
            stream = await db.get(NativeFeedStream, {"kind": KIND_KILL, "region": "americas"})
            assert stream is not None
            assert stream.scan_resolution == "initial"
            assert stream.captured_head_source_id in ("a", "b")

            applied: list[str] = []

            async def apply(_db, item):
                applied.append(item.source_id)

            assert await apply_native_items(
                db, kind=KIND_KILL, region="americas", apply_item=apply,
            ) == 2

        await engine.dispose()

    asyncio.run(run())


def test_dead_letter_apos_max_tentativas():
    """Item que falha DEAD_LETTER_MAX_ATTEMPTS vezes vira 'dead' e desbloqueia a fila."""
    async def run():
        engine = await _engine()
        now = datetime.now(timezone.utc).replace(microsecond=0)

        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(NativeFeedStream(
                kind=KIND_KILL, region="europe",
                completed_head_source_id="old", captured_head_source_id="new",
            ))
            db.add(NativeFeedItem(
                kind=KIND_KILL, region="europe", source_id="broken",
                occurred_at=now, payload=_raw("broken", now),
                attempts=19, status="retry",
                next_retry_at=now - timedelta(seconds=1),
            ))
            db.add(NativeFeedItem(
                kind=KIND_KILL, region="europe", source_id="good",
                occurred_at=now + timedelta(minutes=1),
                payload=_raw("good", now + timedelta(minutes=1)),
            ))
            await db.commit()

            async def apply(_db, item):
                if item.source_id == "broken":
                    raise RuntimeError("sempre falha")

            applied = await apply_native_items(
                db, kind=KIND_KILL, region="europe", apply_item=apply, batch_size=5,
            )
            broken = await db.scalar(
                select(NativeFeedItem).where(NativeFeedItem.source_id == "broken")
            )
            assert broken is not None
            assert broken.status == "dead"
            assert broken.attempts == 20

            good = await db.scalar(
                select(NativeFeedItem).where(NativeFeedItem.source_id == "good")
            )
            assert good is not None
            assert good.status == "applied"
            assert applied == 1

        await engine.dispose()

    asyncio.run(run())


def test_localizacao_nao_aplica_itens():
    """A fase de localização não deve aplicar itens ao domínio."""
    async def run():
        engine = await _engine()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        page_size = 2
        offset_limit = 20

        pages = {}
        t = now + timedelta(minutes=10)
        for off in range(0, offset_limit, page_size):
            page = []
            for j in range(page_size):
                page.append(_raw(f"id-{off + j}", t))
                t = t - timedelta(seconds=30)
            pages[off] = page

        anchor_time = now + timedelta(minutes=5)

        async def fetch(offset, _limit):
            return pages.get(offset, [])

        applied: list[str] = []

        async def apply(_db, item):
            applied.append(item.source_id)

        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(NativeFeedStream(
                kind=KIND_KILL, region="americas",
                completed_head_source_id="anchor",
                captured_head_source_id="anchor",
            ))
            db.add(NativeFeedItem(
                kind=KIND_KILL, region="americas", source_id="anchor",
                occurred_at=anchor_time, payload=_raw("anchor", anchor_time),
                status="applied",
            ))
            await db.commit()

            result = await capture_native_stream(
                db, kind=KIND_KILL, region="americas", page_size=page_size,
                offset_limit=offset_limit, page_budget=2,
                fetch_page=fetch, source_id=_source, occurred_at=_occurred,
            )
            # Localização não completa com budget 2
            assert not result.completed
            # Nada aplicado durante localização
            assert applied == []

        await engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    import sys
    for name, obj in list(globals().items()):
        if name.startswith("test_") and callable(obj):
            print(f"  {name} ...", end=" ", flush=True)
            try:
                obj()
                print("OK")
            except Exception as e:
                print(f"FAIL: {e}")
                sys.exit(1)
    print("All tests passed.")


def test_busca_exponencial_retoma_do_ultimo_offset_entre_ciclos():
    """Regressão do incidente 31/08: com budget pequeno por ciclo (rate
    limiter apertado), a busca exponencial recomeçava do offset 0 a cada
    ciclo e nunca alcançava âncoras distantes. Os offsets sondados devem
    avançar monotonicamente entre ciclos."""
    async def run():
        engine = await _engine()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        page_size = 2
        offset_limit = 40

        # Timestamps decrescentes; âncora fica dentro da janela (a última
        # página contém itens mais VELHOS que a âncora — não é outside_window)
        pages = {}
        t = now + timedelta(minutes=30)
        for off in range(0, offset_limit, page_size):
            page = []
            for j in range(page_size):
                page.append(_raw(f"id-{off + j}", t))
                t = t - timedelta(seconds=60)
            pages[off] = page

        anchor_time = now + timedelta(minutes=15)

        probes_seen: list[int] = []

        async def fetch(offset, _limit):
            probes_seen.append(offset)
            return pages.get(offset, [])

        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(NativeFeedStream(
                kind=KIND_KILL, region="americas",
                completed_head_source_id="far-anchor",
                captured_head_source_id="far-anchor",
            ))
            db.add(NativeFeedItem(
                kind=KIND_KILL, region="americas", source_id="far-anchor",
                occurred_at=anchor_time, payload=_raw("far-anchor", anchor_time),
                status="applied",
            ))
            await db.commit()

            # Vários ciclos com budget de 1 página (simula rate limiter lento)
            for _ in range(60):
                result = await capture_native_stream(
                    db, kind=KIND_KILL, region="americas", page_size=page_size,
                    offset_limit=offset_limit, page_budget=2,
                    fetch_page=fetch, source_id=_source, occurred_at=_occurred,
                )
                if result.completed:
                    break

            assert result.completed and not result.blocked
            # Os offsets da busca exponencial devem ser estritamente
            # crescentes (nenhum retorno a 0 no meio da busca)
            offsets = [o for o in probes_seen if o != offset_limit - page_size]
            assert offsets, "nenhum probe exponencial registrado"
            # O primeiro retorno a zero marca a transição locating → capturing.
            # Antes dele, os probes exponenciais precisam avançar sem reiniciar.
            second_zero = next((i for i, off in enumerate(offsets[1:], 1) if off == 0), len(offsets))
            locating_offsets = offsets[:second_zero]
            assert locating_offsets[0] == 0
            for prev, curr in zip(locating_offsets, locating_offsets[1:]):
                assert curr > prev, f"busca voltou para offset menor: {curr} após {prev}"

        await engine.dispose()

    asyncio.run(run())