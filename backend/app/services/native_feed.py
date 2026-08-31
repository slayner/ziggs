"""Captura e aplica, em ordem, os feeds nativos de batalha e kill do Albion.

O algoritmo de localização da âncora usa busca exponencial seguida de busca
binária temporal, reduzindo de até ~197 páginas (linear) para ~8-16 probes.
Após localizar, captura sequencialmente com overlap para tolerar feed mutável.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.native_feed import NativeFeedItem, NativeFeedStream

log = logging.getLogger(__name__)

KIND_BATTLE = "battle"
KIND_KILL = "kill"
APPLY_BATCH_SIZE = 10
RETRY_BASE_SECONDS = 5
RETRY_MAX_SECONDS = 300
RECOVERY_MAX_AGE = timedelta(minutes=15)
PROCESSING_STALE_AFTER = timedelta(minutes=5)
DEAD_LETTER_MAX_ATTEMPTS = 20
OVERLAP_PAGES = 1

_capture_locks: dict[tuple[str, str], asyncio.Lock] = {}
_apply_locks: dict[tuple[str, str], asyncio.Lock] = {}


@dataclass(frozen=True)
class ScanResult:
    completed: bool
    blocked: bool
    pages: int
    head_payload: dict | None


@dataclass
class PageProbe:
    offset: int
    returned: int
    newest_time: datetime | None
    oldest_time: datetime | None
    first_id: str | None
    last_id: str | None
    ids: list[str]
    is_descending: bool


FetchPage = Callable[[int, int], Awaitable[list[dict]]]
SourceId = Callable[[dict], str]
OccurredAt = Callable[[dict], datetime]
ApplyItem = Callable[[AsyncSession, NativeFeedItem], Awaitable[None]]


def _capture_lock(kind: str, region: str) -> asyncio.Lock:
    return _capture_locks.setdefault((kind, region), asyncio.Lock())


def _apply_lock(kind: str, region: str) -> asyncio.Lock:
    return _apply_locks.setdefault((kind, region), asyncio.Lock())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def _stream(db: AsyncSession, kind: str, region: str) -> NativeFeedStream:
    stream = await db.get(NativeFeedStream, {"kind": kind, "region": region})
    if stream is None:
        stream = NativeFeedStream(kind=kind, region=region)
        db.add(stream)
        await db.flush()
    return stream


async def _store_items(
    db: AsyncSession,
    *,
    kind: str,
    region: str,
    rows: list[dict],
    source_id: SourceId,
    occurred_at: OccurredAt,
    discovered_by: str | None = None,
) -> list[str]:
    prepared: list[tuple[str, datetime, dict]] = []
    ids: list[str] = []
    seen_ids: set[str] = set()
    for raw in rows:
        sid = source_id(raw)
        if not sid:
            raise ValueError(f"item {kind}/{region} sem source_id")
        when = _aware(occurred_at(raw))
        ids.append(sid)
        if sid not in seen_ids:
            prepared.append((sid, when, raw))
            seen_ids.add(sid)
    existing_ids = set((await db.scalars(
        select(NativeFeedItem.source_id).where(
            NativeFeedItem.kind == kind,
            NativeFeedItem.region == region,
            NativeFeedItem.source_id.in_(seen_ids),
        )
    )).all()) if seen_ids else set()
    for sid, when, raw in prepared:
        if sid not in existing_ids:
            db.add(NativeFeedItem(
                kind=kind,
                region=region,
                source_id=sid,
                occurred_at=when,
                payload=raw,
                discovered_by=discovered_by,
            ))
    await db.flush()
    return ids


def _probe_page(
    offset: int,
    page: list[dict],
    source_id: SourceId,
    occurred_at: OccurredAt,
) -> PageProbe:
    ids = [source_id(raw) for raw in page]
    times = [_aware(occurred_at(raw)) for raw in page]
    is_descending = all(
        times[i] >= times[i + 1] for i in range(len(times) - 1)
    ) if len(times) > 1 else True
    return PageProbe(
        offset=offset,
        returned=len(page),
        newest_time=times[0] if times else None,
        oldest_time=times[-1] if times else None,
        first_id=ids[0] if ids else None,
        last_id=ids[-1] if ids else None,
        ids=ids,
        is_descending=is_descending,
    )


async def capture_discovered_items(
    db: AsyncSession,
    *,
    kind: str,
    region: str,
    rows: list[dict],
    source_id: SourceId,
    occurred_at: OccurredAt,
    discovered_by: str | None = None,
) -> int:
    """Guarda achados de sweepers sem permitir que eles furem a ordenação."""
    await _stream(db, kind, region)
    source_ids = [source_id(raw) for raw in rows]
    existing_ids = set((await db.scalars(
        select(NativeFeedItem.source_id).where(
            NativeFeedItem.kind == kind,
            NativeFeedItem.region == region,
            NativeFeedItem.source_id.in_([sid for sid in source_ids if sid]),
        )
    )).all()) if rows else set()
    await _store_items(
        db, kind=kind, region=region, rows=rows, source_id=source_id,
        occurred_at=occurred_at, discovered_by=discovered_by,
    )
    await db.commit()
    return len(set(source_ids) - existing_ids)


async def _fetch_and_store(
    db: AsyncSession,
    fetch_page: FetchPage,
    offset: int,
    request_size: int,
    *,
    kind: str,
    region: str,
    source_id: SourceId,
    occurred_at: OccurredAt,
) -> tuple[PageProbe, list[dict]]:
    page = await fetch_page(offset, request_size)
    if not isinstance(page, list):
        raise ValueError(f"página {kind}/{region} inválida no offset {offset}")
    ids = await _store_items(
        db, kind=kind, region=region, rows=page, source_id=source_id,
        occurred_at=occurred_at,
    )
    probe = _probe_page(offset, page, source_id, occurred_at)
    probe.ids = ids
    return probe, page


def _classify_outside_window(probe: PageProbe, anchor_time: datetime | None) -> bool:
    """Se o item mais antigo da última página é mais novo que a âncora,
    a âncora saiu da janela."""
    if anchor_time is None or probe.oldest_time is None:
        return False
    return _aware(probe.oldest_time) > _aware(anchor_time)


async def _probe_last_page(
    db: AsyncSession,
    fetch_page: FetchPage,
    *,
    page_size: int,
    offset_limit: int,
    kind: str,
    region: str,
    source_id: SourceId,
    occurred_at: OccurredAt,
) -> PageProbe | None:
    """Sonda a última página válida para classificar outside_window cedo."""
    last_offset = offset_limit - page_size
    if last_offset <= 0:
        return None
    try:
        probe, _ = await _fetch_and_store(
            db, fetch_page, last_offset, page_size,
            kind=kind, region=region, source_id=source_id, occurred_at=occurred_at,
        )
    except Exception:
        return None
    if probe.returned == 0:
        return None
    return probe


async def capture_native_stream(
    db: AsyncSession,
    *,
    kind: str,
    region: str,
    page_size: int,
    offset_limit: int,
    page_budget: int,
    fetch_page: FetchPage,
    source_id: SourceId,
    occurred_at: OccurredAt,
) -> ScanResult:
    """Captura o feed até achar a âncora, usando busca exponencial+binária.

    Fases:
      locating — busca exponencial (dobrar offset) seguida de binária temporal
      capturing — captura sequencial do topo até a fronteira localizada

    A âncora é o ID da última cabeça capturada. Se a API omite o ID mas mantém
    ordem temporal, a fronteira é comprovada quando uma página inteira é
    estritamente anterior ao timestamp da âncora.

    Se a âncora saiu da janela (última página é mais nova), classifica como
    outside_window imediatamente sem percorrer 10000 itens.
    """
    async with _capture_lock(kind, region):
        pages = 0
        head_payload: dict | None = None
        stream = await _stream(db, kind, region)

        # ── Iniciar scan se ocioso ──────────────────────────────────
        if stream.scan_blocked:
            stream.scan_blocked = False
            stream.blocked_at = None
            stream.blocked_reason = None
            stream.scan_active = True
            stream.scan_head_source_id = None
            stream.next_offset = 0
            stream.scan_started_at = _now()
            stream.scan_phase = "locating"
            stream.search_low_offset = 0
            stream.search_high_offset = -1
            stream.next_offset = 0
            await db.commit()

        if not stream.scan_active:
            stream.scan_active = True
            stream.scan_anchor_source_id = (
                stream.captured_head_source_id
                or stream.completed_head_source_id
            )
            stream.scan_anchor_occurred_at = None
            if stream.scan_anchor_source_id:
                stream.scan_anchor_occurred_at = await db.scalar(
                    select(NativeFeedItem.occurred_at).where(
                        NativeFeedItem.kind == kind,
                        NativeFeedItem.region == region,
                        NativeFeedItem.source_id == stream.scan_anchor_source_id,
                    )
                )
            stream.scan_head_source_id = None
            stream.scan_id = str(uuid4())
            stream.scan_resolution = None
            stream.scan_phase = "locating"
            stream.scan_last_progress_at = _now()
            stream.search_low_offset = 0
            stream.search_high_offset = -1
            stream.next_offset = 0
            stream.scan_started_at = _now()
            await db.commit()
            log.info(
                "event=native_feed_scan_started scan_id=%s kind=%s region=%s "
                "anchor_id=%s anchor_time=%s phase=locating",
                stream.scan_id, kind, region, stream.scan_anchor_source_id,
                stream.scan_anchor_occurred_at,
            )

        anchor_id = stream.scan_anchor_source_id
        anchor_time = stream.scan_anchor_occurred_at

        # ── Sem âncora: primeira sincronização — captura linear até exaurir ──
        if anchor_id is None:
            return await _capture_linear(
                db, stream, fetch_page, page_size, offset_limit, page_budget,
                kind=kind, region=region, source_id=source_id, occurred_at=occurred_at,
                pages=pages, head_payload=head_payload,
            )

        # ── Fase locating: busca exponencial + binária ──────────────
        if stream.scan_phase == "locating":
            locate_result = await _locate_anchor(
                db, stream, fetch_page, page_size, offset_limit, page_budget,
                kind=kind, region=region, source_id=source_id,
                occurred_at=occurred_at, anchor_id=anchor_id, anchor_time=anchor_time,
            )
            if locate_result is None:
                # Budget esgotado durante localização — retomar próximo ciclo
                stream.scan_last_progress_at = _now()
                await db.commit()
                return ScanResult(not stream.scan_active, stream.scan_blocked,
                                  pages + 1, head_payload)
            pages, head_payload, capture_target = locate_result
            if capture_target is None:
                # outside_window classificado durante localização
                return ScanResult(True, False, pages, head_payload)

            # Transição para capturing
            stream.scan_phase = "capturing"
            stream.next_offset = 0
            stream.scan_last_progress_at = _now()
            await db.commit()
            log.info(
                "event=native_feed_anchor_located scan_id=%s kind=%s region=%s "
                "capture_target_offset=%d pages=%d",
                stream.scan_id, kind, region, capture_target, pages,
            )
            remaining_budget = page_budget - pages
        else:
            # Retomada de capturing após restart
            capture_target = stream.search_high_offset
            remaining_budget = page_budget

        # ── Fase capturing: captura sequencial do topo até o alvo ───
        return await _capture_to_target(
            db, stream, fetch_page, page_size, offset_limit, remaining_budget,
            kind=kind, region=region, source_id=source_id, occurred_at=occurred_at,
            anchor_id=anchor_id, anchor_time=anchor_time,
            capture_target=capture_target, pages=pages, head_payload=head_payload,
        )


async def _locate_anchor(
    db: AsyncSession,
    stream: NativeFeedStream,
    fetch_page: FetchPage,
    page_size: int,
    offset_limit: int,
    page_budget: int,
    *,
    kind: str,
    region: str,
    source_id: SourceId,
    occurred_at: OccurredAt,
    anchor_id: str,
    anchor_time: datetime | None,
) -> tuple[int, dict | None, int | None] | None:
    """Localiza a âncora por busca exponencial+binária. Retorna (pages,
    head_payload, capture_target_offset) ou None se o budget esgotou, ou
    (pages, head_payload, None) se outside_window."""
    pages = 0
    head_payload: dict | None = None

    # ── Sondagem da última página (outside_window proativo) ──────
    if stream.search_high_offset == -1:
        last_probe = await _probe_last_page(
            db, fetch_page, page_size=page_size, offset_limit=offset_limit,
            kind=kind, region=region, source_id=source_id, occurred_at=occurred_at,
        )
        if last_probe is not None:
            pages += 1
            if _classify_outside_window(last_probe, anchor_time):
                stream.scan_active = False
                stream.scan_anchor_source_id = None
                stream.scan_anchor_occurred_at = None
                stream.next_offset = 0
                stream.scan_resolution = "outside_window"
                stream.scan_phase = None
                stream.scan_last_progress_at = _now()
                stream.search_low_offset = 0
                stream.search_high_offset = 0
                if stream.scan_head_source_id:
                    stream.captured_head_source_id = stream.scan_head_source_id
                stream.scan_head_source_id = None
                await db.commit()
                log.warning(
                    "event=native_feed_anchor_outside_window scan_id=%s kind=%s "
                    "region=%s anchor_id=%s anchor_time=%s oldest_visible_id=%s "
                    "offset_limit=%d pages=%d",
                    stream.scan_id, kind, region, anchor_id, anchor_time,
                    last_probe.last_id, offset_limit, pages,
                )
                return pages, head_payload, None
            # Encontrou o ID exato na última página
            if anchor_id in last_probe.ids:
                # A âncora está na última página — captura linear simples
                return pages, head_payload, last_probe.offset
        stream.search_high_offset = 0
        stream.next_offset = 0
        stream.scan_last_progress_at = _now()
        await db.commit()
        if pages >= page_budget:
            return None

    # ── Busca exponencial: dobrar offset até passar o tempo da âncora ──
    if stream.search_high_offset == 0:
        exp_offset = stream.next_offset
        while pages < page_budget:
            request_size = min(page_size, offset_limit - exp_offset)
            if request_size <= 0:
                stream.search_high_offset = offset_limit
                break
            probe, page = await _fetch_and_store(
                db, fetch_page, exp_offset, request_size,
                kind=kind, region=region, source_id=source_id, occurred_at=occurred_at,
            )
            pages += 1
            if head_payload is None and page:
                head_payload = page[0]
            if stream.scan_head_source_id is None and probe.ids:
                stream.scan_head_source_id = probe.ids[0]

            stream.search_low_offset = exp_offset
            stream.next_offset = min(offset_limit, 2 * exp_offset + page_size)
            stream.scan_last_progress_at = _now()
            await db.commit()
            log.info(
                "event=native_feed_probe_completed scan_id=%s kind=%s region=%s "
                "strategy=exponential offset=%d returned=%d newest_time=%s "
                "oldest_time=%s pages=%d",
                stream.scan_id, kind, region, exp_offset, probe.returned,
                probe.newest_time, probe.oldest_time, pages,
            )

            if anchor_id in probe.ids:
                return pages, head_payload, exp_offset

            if probe.returned == 0:
                # Página vazia — fim da janela real
                stream.search_high_offset = exp_offset
                break

            if probe.oldest_time and anchor_time and _aware(probe.oldest_time) <= _aware(anchor_time):
                stream.search_high_offset = exp_offset
                break

            if probe.returned < request_size or exp_offset + request_size >= offset_limit:
                # Janela esgotou sem encontrar — outside_window se há cabeça
                if stream.scan_head_source_id:
                    stream.scan_active = False
                    stream.scan_anchor_source_id = None
                    stream.scan_anchor_occurred_at = None
                    stream.next_offset = 0
                    stream.scan_resolution = "outside_window"
                    stream.scan_phase = None
                    stream.scan_last_progress_at = _now()
                    stream.captured_head_source_id = stream.scan_head_source_id
                    stream.scan_head_source_id = None
                    await db.commit()
                    log.warning(
                        "event=native_feed_anchor_outside_window scan_id=%s "
                        "kind=%s region=%s anchor_id=%s anchor_time=%s "
                        "pages=%d",
                        stream.scan_id, kind, region, anchor_id, anchor_time, pages,
                    )
                    return pages, head_payload, None
                stream.scan_blocked = True
                stream.blocked_at = _now()
                stream.blocked_reason = f"âncora {anchor_id!r} não encontrada"
                await db.commit()
                return pages, head_payload, 0  # blocked, não None

            exp_offset = stream.next_offset

        if pages >= page_budget:
            return None

    # ── Busca binária entre [low, high] ──────────────────────────
    low = stream.search_low_offset
    high = stream.search_high_offset
    if high == 0:
        high = offset_limit

    while pages < page_budget and high - low > page_size:
        mid = ((low + high) // (2 * page_size)) * page_size
        if mid <= low:
            mid = low + page_size
        if mid >= high:
            mid = high - page_size
        request_size = min(page_size, offset_limit - mid)
        if request_size <= 0:
            break
        probe, page = await _fetch_and_store(
            db, fetch_page, mid, request_size,
            kind=kind, region=region, source_id=source_id, occurred_at=occurred_at,
        )
        pages += 1
        if head_payload is None and page:
            head_payload = page[0]
        if stream.scan_head_source_id is None and probe.ids:
            stream.scan_head_source_id = probe.ids[0]

        stream.scan_last_progress_at = _now()
        await db.commit()
        log.info(
            "event=native_feed_probe_completed scan_id=%s kind=%s region=%s "
            "strategy=binary offset=%d returned=%d newest_time=%s oldest_time=%s "
            "pages=%d",
            stream.scan_id, kind, region, mid, probe.returned,
            probe.newest_time, probe.oldest_time, pages,
        )

        if anchor_id in probe.ids:
            return pages, head_payload, mid

        if probe.returned == 0:
            high = mid
            stream.search_low_offset = low
            stream.search_high_offset = high
            continue

        if probe.newest_time and anchor_time and _aware(probe.newest_time) <= _aware(anchor_time):
            # Página inteira é mais antiga que a âncora — alvo está antes
            high = mid
        elif probe.oldest_time and anchor_time and _aware(probe.oldest_time) > _aware(anchor_time):
            # Página inteira é mais nova que a âncora — alvo está depois
            low = mid
        else:
            # Página atravessa o tempo da âncora — alvo está aqui
            return pages, head_payload, mid

        stream.search_low_offset = low
        stream.search_high_offset = high

    if pages >= page_budget:
        stream.search_low_offset = low
        stream.search_high_offset = high
        await db.commit()
        return None

    # Convergiu — captura linear do topo
    return pages, head_payload, low


async def _capture_linear(
    db: AsyncSession,
    stream: NativeFeedStream,
    fetch_page: FetchPage,
    page_size: int,
    offset_limit: int,
    page_budget: int,
    *,
    kind: str,
    region: str,
    source_id: SourceId,
    occurred_at: OccurredAt,
    pages: int,
    head_payload: dict | None,
) -> ScanResult:
    """Captura linear para primeira sincronização (sem âncora)."""
    for _ in range(page_budget):
        offset = stream.next_offset
        request_size = min(page_size, offset_limit - offset)
        if request_size <= 0:
            await db.commit()
            return ScanResult(True, False, pages, head_payload)
        probe, page = await _fetch_and_store(
            db, fetch_page, offset, request_size,
            kind=kind, region=region, source_id=source_id, occurred_at=occurred_at,
        )
        pages += 1
        if head_payload is None and page:
            head_payload = page[0]
        if stream.scan_head_source_id is None and probe.ids:
            stream.scan_head_source_id = probe.ids[0]
        next_offset = offset + request_size
        exhausted = probe.returned < request_size or next_offset >= offset_limit
        if exhausted:
            stream.scan_active = False
            stream.scan_anchor_source_id = None
            stream.scan_anchor_occurred_at = None
            stream.next_offset = 0
            stream.scan_resolution = "initial"
            stream.scan_phase = None
            stream.scan_last_progress_at = _now()
            if stream.scan_head_source_id:
                stream.captured_head_source_id = stream.scan_head_source_id
            stream.scan_head_source_id = None
            await db.commit()
            log.info(
                "event=native_feed_boundary_promoted scan_id=%s kind=%s region=%s "
                "resolution=initial pages=%d",
                stream.scan_id, kind, region, pages,
            )
            return ScanResult(True, False, pages, head_payload)
        stream.next_offset = next_offset
        stream.scan_last_progress_at = _now()
        await db.commit()
    return ScanResult(not stream.scan_active, stream.scan_blocked, pages, head_payload)


async def _capture_to_target(
    db: AsyncSession,
    stream: NativeFeedStream,
    fetch_page: FetchPage,
    page_size: int,
    offset_limit: int,
    page_budget: int,
    *,
    kind: str,
    region: str,
    source_id: SourceId,
    occurred_at: OccurredAt,
    anchor_id: str,
    anchor_time: datetime | None,
    capture_target: int,
    pages: int,
    head_payload: dict | None,
) -> ScanResult:
    """Captura sequencial do offset 0 até capture_target, com overlap."""
    prev_oldest_time: datetime | None = None
    for _ in range(page_budget):
        offset = stream.next_offset
        request_size = min(page_size, offset_limit - offset)
        if request_size <= 0:
            stream.scan_blocked = True
            stream.blocked_at = _now()
            stream.blocked_reason = (
                f"âncora {anchor_id!r} não apareceu antes do limite {offset_limit}"
            )
            await db.commit()
            log.warning(
                "event=native_feed_scan_blocked scan_id=%s kind=%s region=%s "
                "reason=%s",
                stream.scan_id, kind, region, stream.blocked_reason,
            )
            return ScanResult(False, True, pages, head_payload)

        probe, page = await _fetch_and_store(
            db, fetch_page, offset, request_size,
            kind=kind, region=region, source_id=source_id, occurred_at=occurred_at,
        )
        pages += 1
        if head_payload is None and page:
            head_payload = page[0]
        if stream.scan_head_source_id is None and probe.ids:
            stream.scan_head_source_id = probe.ids[0]

        # Validar monotonicidade entre páginas
        if prev_oldest_time is not None and probe.newest_time:
            if _aware(probe.newest_time) > _aware(prev_oldest_time):
                log.error(
                    "event=native_feed_order_violation scan_id=%s kind=%s "
                    "region=%s offset=%d prev_oldest=%s current_newest=%s",
                    stream.scan_id, kind, region, offset,
                    prev_oldest_time, probe.newest_time,
                )
        prev_oldest_time = probe.oldest_time

        found_anchor = anchor_id in probe.ids
        reaches_anchor_time = bool(
            anchor_time and probe.oldest_time
            and _aware(probe.oldest_time) < _aware(anchor_time)
        )
        next_offset = offset + request_size
        exhausted = probe.returned < request_size or next_offset >= offset_limit
        past_target = next_offset > capture_target + page_size

        if found_anchor or reaches_anchor_time or exhausted or past_target:
            resolution = (
                "exact_id" if found_anchor
                else "temporal" if reaches_anchor_time
                else "outside_window" if exhausted
                else "target_reached"
            )
            stream.scan_active = False
            stream.scan_anchor_source_id = None
            stream.scan_anchor_occurred_at = None
            stream.next_offset = 0
            stream.scan_resolution = resolution
            stream.scan_phase = None
            stream.scan_last_progress_at = _now()
            stream.search_low_offset = 0
            stream.search_high_offset = 0
            if stream.scan_head_source_id:
                stream.captured_head_source_id = stream.scan_head_source_id
            stream.scan_head_source_id = None
            await db.commit()
            log.info(
                "event=native_feed_boundary_promoted scan_id=%s kind=%s region=%s "
                "resolution=%s anchor_id=%s pages=%d",
                stream.scan_id, kind, region, resolution, anchor_id, pages,
            )
            return ScanResult(True, False, pages, head_payload)

        stream.next_offset = next_offset
        stream.scan_last_progress_at = _now()
        await db.commit()
        log.info(
            "event=native_feed_capture_progress scan_id=%s kind=%s region=%s "
            "offset=%d next_offset=%d returned=%d pages=%d",
            stream.scan_id, kind, region, offset, next_offset, probe.returned, pages,
        )

    return ScanResult(not stream.scan_active, stream.scan_blocked, pages, head_payload)


async def apply_native_items(
    db: AsyncSession,
    *,
    kind: str,
    region: str,
    apply_item: ApplyItem,
    batch_size: int = APPLY_BATCH_SIZE,
) -> int:
    """Aplica o inbox estritamente por ocorrência e source_id.

    Uma falha mantém o item em retry e encerra o lote: itens mais novos nunca
    passam por cima dele. Após DEAD_LETTER_MAX_ATTEMPTS, o item vira dead_letter
    e é pulado para desbloquear a fila.
    """
    applied = 0
    for _ in range(batch_size):
        async with _apply_lock(kind, region):
            stream = await _stream(db, kind, region)
            if (
                stream.scan_active
                and stream.scan_started_at
                and _aware(stream.scan_started_at) + RECOVERY_MAX_AGE <= _now()
            ):
                log.warning(
                    "event=native_feed_scan_stalled scan_id=%s kind=%s region=%s "
                    "phase=%s seconds_without_progress=%d current_offset=%d anchor_id=%s",
                    stream.scan_id, kind, region, stream.scan_phase or "capturing",
                    int((_now() - _aware(stream.scan_last_progress_at or stream.scan_started_at)).total_seconds()),
                    stream.next_offset, stream.scan_anchor_source_id,
                )
            boundary = None
            if stream.scan_active or stream.scan_blocked:
                if stream.captured_head_source_id:
                    boundary = await db.scalar(
                        select(NativeFeedItem).where(
                            NativeFeedItem.kind == kind,
                            NativeFeedItem.region == region,
                            NativeFeedItem.source_id == stream.captured_head_source_id,
                        )
                    )
                if boundary is None:
                    await db.commit()
                    break
            query = (
                select(NativeFeedItem)
                .where(
                    NativeFeedItem.kind == kind,
                    NativeFeedItem.region == region,
                    NativeFeedItem.status != "applied",
                    NativeFeedItem.status != "dead",
                )
                .order_by(NativeFeedItem.occurred_at.asc(), NativeFeedItem.source_id.asc())
                .limit(1)
            )
            if boundary is not None:
                boundary_time = _aware(boundary.occurred_at)
                query = query.where(or_(
                    NativeFeedItem.occurred_at < boundary_time,
                    and_(
                        NativeFeedItem.occurred_at == boundary_time,
                        NativeFeedItem.source_id <= boundary.source_id,
                    ),
                ))
            item = await db.scalar(query)
            if item is None:
                await db.commit()
                break
            now = _now()
            if item.status == "processing":
                last_attempt = item.last_attempt_at
                if last_attempt and _aware(last_attempt) + PROCESSING_STALE_AFTER > now:
                    await db.commit()
                    break
                log.warning(
                    "native_feed: retomando reserva abandonada %s/%s/%s",
                    kind, region, item.source_id,
                )
            elif item.next_retry_at and _aware(item.next_retry_at) > now:
                await db.commit()
                break
            item_id = item.id
            item.status = "processing"
            item.last_attempt_at = now
            await db.commit()

        try:
            item = await db.get(NativeFeedItem, item_id)
            if item is None:
                raise RuntimeError("item reservado não encontrado")
            await apply_item(db, item)
        except BaseException as exc:
            await db.rollback()
            async with _apply_lock(kind, region):
                item = await db.get(NativeFeedItem, item_id)
                if item is None:
                    raise
                item.attempts += 1
                item.last_attempt_at = now
                item.last_error = f"{type(exc).__name__}: {exc}"[:1000]
                if item.attempts >= DEAD_LETTER_MAX_ATTEMPTS:
                    item.status = "dead"
                    item.next_retry_at = None
                    log.error(
                        "event=native_feed_item_dead kind=%s region=%s source_id=%s "
                        "attempts=%d error=%s",
                        kind, region, item.source_id, item.attempts, item.last_error,
                    )
                else:
                    item.status = "retry"
                    item.next_retry_at = now + timedelta(
                        seconds=min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2 ** (item.attempts - 1)))
                    )
                    log.warning(
                        "event=native_feed_item_failed kind=%s region=%s source_id=%s "
                        "attempts=%d error=%s",
                        kind, region, item.source_id, item.attempts, exc,
                    )
                await db.commit()
            if isinstance(exc, asyncio.CancelledError):
                raise
            if item.status == "dead":
                continue
            break

        async with _apply_lock(kind, region):
            item = await db.get(NativeFeedItem, item_id)
            if item is None:
                raise RuntimeError("item aplicado não encontrado")
            item.status = "applied"
            item.applied_at = _now()
            item.next_retry_at = None
            item.last_error = None
            await db.commit()
            applied += 1

    async with _apply_lock(kind, region):
        stream = await _stream(db, kind, region)
        remaining_query = select(NativeFeedItem.id).where(
            NativeFeedItem.kind == kind,
            NativeFeedItem.region == region,
            NativeFeedItem.status != "applied",
            NativeFeedItem.status != "dead",
        )
        if stream.captured_head_source_id:
            boundary = await db.scalar(
                select(NativeFeedItem).where(
                    NativeFeedItem.kind == kind,
                    NativeFeedItem.region == region,
                    NativeFeedItem.source_id == stream.captured_head_source_id,
                )
            )
            if boundary is not None:
                boundary_time = _aware(boundary.occurred_at)
                remaining_query = remaining_query.where(or_(
                    NativeFeedItem.occurred_at < boundary_time,
                    and_(
                        NativeFeedItem.occurred_at == boundary_time,
                        NativeFeedItem.source_id <= boundary.source_id,
                    ),
                ))
        remaining = await db.scalar(remaining_query.limit(1))
        if remaining is None and stream.captured_head_source_id:
            stream.completed_head_source_id = stream.captured_head_source_id
            stream.last_completed_at = _now()
        await db.commit()
    return applied