"""Outbox de juicy kills para o bot Discord.

A elegibilidade é calculada uma vez quando a precificação termina. Assim um
atraso que despeja muitos eventos na API gera trabalho proporcional ao burst,
mas o poll do bot continua sendo uma leitura indexada e limitada.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncSessionLocal
from app.models.players import JuicyKillDelivery, PlayerKillEvent
from app.models.tenancy import Guild
from app.services.lethality import is_likely_lethal
from app.services.player_tracker import HOSTS
from app.services.postable import postable_cutoffs_by_region

log = logging.getLogger(__name__)

HARD_FLOOR = 20_000_000
RECOVERY_BATCH_SIZE = 500
RECOVERY_INTERVAL = 2
_FROM_KEY = "juicy_kill_delivery_from_by_region"
_RECOVERY_CURSOR_KEY = "juicy_kill_delivery_recovery_cursor_by_region"
_RECOVERY_TARGET_KEY = "juicy_kill_delivery_recovery_target_by_region"
_RECOVERY_DONE_KEY = "juicy_kill_delivery_recovery_done_by_region"


def _parse_time(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def configured_regions(settings: dict) -> list[str]:
    regions = settings.get("juicy_kill_regions") or list(HOSTS)
    return [region for region in regions if region in HOSTS]


def delivery_from(settings: dict, region: str, fallback: datetime) -> datetime:
    # Watermarks antigos são a fronteira inicial no cutover, preservando o que
    # já foi postado antes da existência da outbox.
    values = settings.get(_FROM_KEY) or settings.get("juicy_kill_last_ts_by_region") or {}
    return _parse_time(values.get(region)) or fallback


def _is_eligible(settings: dict, event: PlayerKillEvent, cutoff: datetime) -> bool:
    if not settings.get("juicy_kill_channel_id"):
        return False
    if event.region not in configured_regions(settings):
        return False
    if event.timestamp < cutoff or event.timestamp <= delivery_from(settings, event.region, cutoff):
        return False
    minimum_silver = max(int(settings.get("juicy_kill_min_silver") or 50_000_000), HARD_FLOOR)
    minimum_fame = int(settings.get("juicy_kill_min_fame") or 0)
    return (
        event.fame >= minimum_fame
        and (event.silver_dropped or 0) >= minimum_silver
        and is_likely_lethal(event.fame, event.victim_equipment, event.group_member_count, event.kill_area)
    )


def _delivery_values(guild: Guild, event: PlayerKillEvent) -> dict:
    return {
        "guild_id": guild.id,
        "kill_id": event.id,
        "occurred_at": event.timestamp,
        "region": event.region,
        "fame": event.fame,
        "silver_dropped": event.silver_dropped or 0,
    }


async def _insert_deliveries(db: AsyncSession, rows: list[dict]) -> None:
    if not rows:
        return
    if db.bind and db.bind.dialect.name == "postgresql":
        statement = postgresql_insert(JuicyKillDelivery)
    else:  # SQLite mantém os testes unitários sem precisar de Postgres local.
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        statement = sqlite_insert(JuicyKillDelivery)
    await db.execute(
        statement
        .values(rows)
        .on_conflict_do_nothing(index_elements=["guild_id", "kill_id"])
    )


async def enqueue_priced_events(db: AsyncSession, events: list[PlayerKillEvent]) -> None:
    """Fanout idempotente das kills recém-precificadas para guildas elegíveis."""
    candidates = [event for event in events if (event.silver_dropped or 0) >= HARD_FLOOR]
    if not candidates:
        return
    guilds = (await db.scalars(select(Guild))).all()
    if not guilds:
        return
    cutoffs = await postable_cutoffs_by_region(db, list(HOSTS))
    rows = []
    for event in candidates:
        cutoff = cutoffs[event.region]
        for guild in guilds:
            settings = guild.settings or {}
            if _is_eligible(settings, event, cutoff):
                rows.append(_delivery_values(guild, event))
    await _insert_deliveries(db, rows)


async def suppress_incompatible_pending(db: AsyncSession, guild_id: int, settings: dict) -> None:
    """Aplica uma configuração mais restritiva sem fazer trabalho no poll."""
    if not settings.get("juicy_kill_channel_id"):
        await db.execute(
            update(JuicyKillDelivery)
            .where(
                JuicyKillDelivery.guild_id == guild_id,
                JuicyKillDelivery.state == "pending",
            )
            .values(state="suppressed")
        )
        return
    regions = configured_regions(settings)
    minimum_silver = max(int(settings.get("juicy_kill_min_silver") or 50_000_000), HARD_FLOOR)
    minimum_fame = int(settings.get("juicy_kill_min_fame") or 0)
    await db.execute(
        update(JuicyKillDelivery)
        .where(
            JuicyKillDelivery.guild_id == guild_id,
            JuicyKillDelivery.state == "pending",
            or_(
                JuicyKillDelivery.silver_dropped < minimum_silver,
                JuicyKillDelivery.fame < minimum_fame,
                JuicyKillDelivery.region.not_in(regions),
            ),
        )
        .values(state="suppressed")
    )


def _cursor_after(timestamp: datetime, kill_id: int):
    return or_(
        PlayerKillEvent.timestamp > timestamp,
        and_(PlayerKillEvent.timestamp == timestamp, PlayerKillEvent.id > kill_id),
    )


async def _recover_guild(db: AsyncSession, guild: Guild) -> int:
    """Materializa, em páginas, o intervalo pendente do cutover para uma guilda."""
    settings = dict(guild.settings or {})
    if not settings.get("juicy_kill_channel_id"):
        return 0
    regions = configured_regions(settings)
    if not regions:
        return 0
    now = datetime.now(timezone.utc)
    targets = dict(settings.get(_RECOVERY_TARGET_KEY) or {})
    cursors = dict(settings.get(_RECOVERY_CURSOR_KEY) or {})
    done = dict(settings.get(_RECOVERY_DONE_KEY) or {})
    cutoffs = await postable_cutoffs_by_region(db, regions)
    recovered = 0
    changed = False

    for region in regions:
        if done.get(region):
            continue
        target = _parse_time(targets.get(region))
        if target is None:
            target = now
            targets[region] = target.isoformat()
            changed = True
        raw_cursor = cursors.get(region) or {}
        cursor_at = _parse_time(raw_cursor.get("timestamp")) or delivery_from(settings, region, cutoffs[region])
        cursor_id = int(raw_cursor.get("kill_id") or 0)
        rows = (await db.scalars(
            select(PlayerKillEvent)
            .where(
                PlayerKillEvent.region == region,
                PlayerKillEvent.fame > 0,
                PlayerKillEvent.silver_dropped.is_not(None),
                PlayerKillEvent.timestamp >= cutoffs[region],
                PlayerKillEvent.timestamp <= target,
                _cursor_after(cursor_at, cursor_id),
            )
            .order_by(PlayerKillEvent.timestamp.asc(), PlayerKillEvent.id.asc())
            .limit(RECOVERY_BATCH_SIZE)
        )).all()
        deliveries = []
        for event in rows:
            if _is_eligible(settings, event, cutoffs[region]):
                deliveries.append(_delivery_values(guild, event))
        await _insert_deliveries(db, deliveries)
        if rows:
            last = rows[-1]
            cursors[region] = {"timestamp": last.timestamp.isoformat(), "kill_id": last.id}
            recovered += len(rows)
            changed = True
        else:
            done[region] = True
            changed = True

    if changed:
        settings[_RECOVERY_TARGET_KEY] = targets
        settings[_RECOVERY_CURSOR_KEY] = cursors
        settings[_RECOVERY_DONE_KEY] = done
        guild.settings = settings
    return recovered


async def recover_once() -> int:
    """Avança o backlog pré-outbox e devolve quantos eventos foram verificados."""
    async with AsyncSessionLocal() as db:
        guilds = (await db.scalars(select(Guild))).all()
        scanned = 0
        try:
            for guild in guilds:
                scanned += await _recover_guild(db, guild)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return scanned


async def run_recovery_forever() -> None:
    """Conclui o cutover em páginas pequenas, sem competir com a leitura do bot."""
    await asyncio.sleep(20)
    while True:
        try:
            scanned = await recover_once()
            if scanned:
                log.info("juicy_kill_delivery: %d eventos verificados no cutover", scanned)
        except Exception as exc:
            log.error("juicy_kill_delivery: falha ao materializar backlog: %s", exc)
        await asyncio.sleep(RECOVERY_INTERVAL)
