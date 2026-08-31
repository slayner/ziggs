"""Captura e aplica, em ordem, os feeds nativos de batalha e kill do Albion."""
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
# A aplicação do inbox só persiste o resumo leve local. Cinco minutos cobre uma
# transação lenta e também recupera rapidamente uma reserva deixada por restart.
PROCESSING_STALE_AFTER = timedelta(minutes=5)

_capture_locks: dict[tuple[str, str], asyncio.Lock] = {}
_apply_locks: dict[tuple[str, str], asyncio.Lock] = {}


@dataclass(frozen=True)
class ScanResult:
    completed: bool
    blocked: bool
    pages: int
    head_payload: dict | None


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
    force_restart: bool = False,
) -> ScanResult:
    """Captura páginas até achar a cabeça previamente concluída.

    Cada página e o próximo offset são confirmados juntos. Se a janela acaba
    antes da âncora, a stream fica bloqueada e nenhum item do inbox é aplicado.
    Alguns feeds omitem ocasionalmente um ID, mesmo preservando a ordem por
    timestamp: nesse caso, alcançar o horário da âncora também comprova a
    fronteira sem avançar pelo offset.
    A nova cabeça vira a próxima âncora assim que a captura termina. A
    aplicação continua bloqueada atrás do item mais antigo pendente, mas a
    captura nunca para por causa desse backlog.

    Se `force_restart=True`, uma nova captura começa do offset 0. Uma captura
    já ativa preserva seu offset até comprovar a âncora, evitando reinícios que
    impediriam recuperar uma fronteira mais antiga.
    """
    async with _capture_lock(kind, region):
        pages = 0
        head_payload: dict | None = None
        for _ in range(page_budget):
            stream = await _stream(db, kind, region)
            if stream.scan_blocked:
                # A API pode voltar a expor a âncora numa janela posterior.
                # Recomeça a mesma varredura, sem trocar a fronteira pendente.
                stream.scan_blocked = False
                stream.blocked_at = None
                stream.blocked_reason = None
                stream.scan_active = True
                stream.scan_head_source_id = None
                stream.next_offset = 0
                stream.scan_started_at = _now()
                await db.commit()
            if not stream.scan_active:
                stream.scan_active = True
                # A captura avança independentemente da aplicação. Assim, um
                # burst não some da janela remota enquanto o inbox o drena.
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
                stream.scan_last_progress_at = _now()
                stream.next_offset = 0
                stream.scan_started_at = _now()
                await db.commit()
                log.info(
                    "event=native_feed_scan_started scan_id=%s kind=%s region=%s anchor_id=%s anchor_time=%s",
                    stream.scan_id, kind, region, stream.scan_anchor_source_id,
                    stream.scan_anchor_occurred_at,
                )

            offset = stream.next_offset
            request_size = min(page_size, offset_limit - offset)
            if request_size <= 0:
                reason = (
                    f"âncora {stream.scan_anchor_source_id!r} não apareceu antes do limite "
                    f"de offset {offset_limit}"
                )
                stream.scan_blocked = True
                stream.blocked_at = _now()
                stream.blocked_reason = reason
                await db.commit()
                log.warning(
                    "native_feed: %s/%s bloqueado: %s; nenhum item será aplicado. "
                    "Inspecione native_feed_streams antes de qualquer reset manual.",
                    kind, region, reason,
                )
                return ScanResult(False, True, pages, head_payload)

            page = await fetch_page(offset, request_size)
            if not isinstance(page, list):
                raise ValueError(f"página {kind}/{region} inválida no offset {offset}")
            ids = await _store_items(
                db, kind=kind, region=region, rows=page, source_id=source_id,
                occurred_at=occurred_at,
            )
            pages += 1
            if head_payload is None and page:
                head_payload = page[0]
            if stream.scan_head_source_id is None and ids:
                stream.scan_head_source_id = ids[0]

            found_anchor = bool(
                stream.scan_anchor_source_id
                and stream.scan_anchor_source_id in ids
            )
            anchor_occurred_at = stream.scan_anchor_occurred_at
            if stream.scan_anchor_source_id and anchor_occurred_at is None:
                anchor_occurred_at = await db.scalar(
                    select(NativeFeedItem.occurred_at).where(
                        NativeFeedItem.kind == kind,
                        NativeFeedItem.region == region,
                        NativeFeedItem.source_id == stream.scan_anchor_source_id,
                    )
                )
                stream.scan_anchor_occurred_at = anchor_occurred_at
            page_times = [_aware(occurred_at(raw)) for raw in page]
            is_descending = all(
                page_times[index] >= page_times[index + 1]
                for index in range(len(page_times) - 1)
            )
            if page and not is_descending:
                log.error(
                    "event=native_feed_order_violation scan_id=%s kind=%s region=%s offset=%d",
                    stream.scan_id, kind, region, offset,
                )
            # Só a página inteira estritamente anterior comprova uma âncora omitida.
            # Isso preserva todos os itens que compartilham o timestamp da âncora.
            reaches_anchor_time = bool(
                anchor_occurred_at and page_times and max(page_times) < _aware(anchor_occurred_at)
            )
            anchor_source_id = stream.scan_anchor_source_id
            next_offset = offset + request_size
            exhausted = len(page) < request_size or next_offset >= offset_limit
            if found_anchor or reaches_anchor_time or (stream.scan_anchor_source_id is None and exhausted):
                resolution = "exact_id" if found_anchor else "temporal" if reaches_anchor_time else "initial"
                stream.scan_active = False
                stream.scan_anchor_source_id = None
                stream.scan_anchor_occurred_at = None
                stream.next_offset = 0
                stream.scan_resolution = resolution
                stream.scan_last_progress_at = _now()
                if stream.scan_head_source_id:
                    stream.captured_head_source_id = stream.scan_head_source_id
                stream.scan_head_source_id = None
                await db.commit()
                log.info(
                    "event=native_feed_boundary_promoted scan_id=%s kind=%s region=%s resolution=%s anchor_id=%s pages=%d",
                    stream.scan_id, kind, region, resolution, anchor_source_id, pages,
                )
                return ScanResult(True, False, pages, head_payload)
            if exhausted:
                if ids and stream.scan_head_source_id:
                    oldest_visible = ids[-1]
                    stream.scan_active = False
                    stream.scan_anchor_source_id = None
                    stream.scan_anchor_occurred_at = None
                    stream.next_offset = 0
                    stream.scan_resolution = "outside_window"
                    stream.scan_last_progress_at = _now()
                    stream.captured_head_source_id = stream.scan_head_source_id
                    stream.scan_head_source_id = None
                    await db.commit()
                    log.warning(
                        "event=native_feed_anchor_outside_window scan_id=%s kind=%s region=%s "
                        "anchor_id=%s anchor_time=%s oldest_visible_id=%s offset_limit=%d pages=%d",
                        stream.scan_id, kind, region, anchor_source_id, anchor_occurred_at,
                        oldest_visible, offset_limit, pages,
                    )
                    return ScanResult(True, False, pages, head_payload)
                reason = (
                    f"âncora {stream.scan_anchor_source_id!r} não apareceu na janela "
                    f"até offset {offset}; nenhuma fronteira visível foi recebida"
                )
                stream.scan_blocked = True
                stream.blocked_at = _now()
                stream.blocked_reason = reason
                await db.commit()
                log.warning("native_feed: %s/%s bloqueado: %s", kind, region, reason)
                return ScanResult(False, True, pages, head_payload)
            stream.next_offset = next_offset
            stream.scan_last_progress_at = _now()
            await db.commit()
            log.info(
                "event=native_feed_capture_progress scan_id=%s kind=%s region=%s offset=%d next_offset=%d "
                "returned=%d pages=%d",
                stream.scan_id, kind, region, offset, next_offset, len(page), pages,
            )

        stream = await _stream(db, kind, region)
        await db.commit()
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
    passam por cima dele. Durante uma nova captura incompleta, ainda pode
    drenar até a última fronteira já capturada, que é uma faixa completa e
    comprovadamente ordenada. O lock cobre apenas a reserva do item: capturas
    continuam persistindo enquanto o writer lento faz HTTP/deep-processing.
    Os writers de domínio são idempotentes, portanto uma queda entre a escrita
    e o status ``applied`` é segura para reexecução.
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
                    "event=native_feed_scan_stalled scan_id=%s kind=%s region=%s phase=capturing "
                    "seconds_without_progress=%d current_offset=%d anchor_id=%s",
                    stream.scan_id, kind, region,
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
                item.status = "retry"
                item.last_attempt_at = now
                item.last_error = f"{type(exc).__name__}: {exc}"[:1000]
                item.next_retry_at = now + timedelta(
                    seconds=min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2 ** (item.attempts - 1)))
                )
                await db.commit()
                log.warning(
                    "native_feed: falha ao aplicar %s/%s/%s (tentativa %d): %s",
                    kind, region, item.source_id, item.attempts, exc,
                )
            if isinstance(exc, asyncio.CancelledError):
                raise
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
