"""Precompute do dashboard (recent battles + weekly highlights) a cada 1min —
antes disso as duas queries (a de highlights faz JOIN+GROUP BY) rodavam ao
vivo toda vez que alguém abria a home, aparecendo como "loading" mesmo sem o
usuário pedir nada. Mesmo padrão de loop dos outros serviços de background
(ver profile_warmer.run_forever).

Guarda listas "generosas" por região (mais que o normalmente pedido) — as
rotas filtram/fatiam em Python por cima disso, então qualquer combinação de
`regions`/`limit` de dashboard é servida sem tocar o banco de novo."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db import SyncSessionLocal, AsyncSessionLocal
from app.models.battles import Battle
from app.models.dashboard_cache import DashboardCache
from app.services import battle_groups

logger = logging.getLogger(__name__)

REGIONS = ("americas", "europe", "asia")
INTERVAL = 60
RECENT_BATTLES_PER_REGION = 15
HIGHLIGHTS_PER_REGION = 20
# Mesmos defaults usados pelo BattlesCard do dashboard (Dashboard.tsx) — só
# esse formato de pedido é "cacheável"; buscas/filtros custom do BattleTracker
# continuam indo direto pro banco.
DEFAULT_MIN_PLAYERS = 5
DEFAULT_MIN_KILLS = 5


def _upsert(db, key: str, payload) -> None:
    row = db.get(DashboardCache, key)
    if row is None:
        db.add(DashboardCache(key=key, payload=payload))
    else:
        row.payload = payload
    db.commit()


async def refresh_recent_battles() -> dict:
    from app.api.routes.battles import _aware, _factions_summary_bulk

    rows: list[dict] = []
    counts: dict[str, int] = {}
    async with AsyncSessionLocal() as db:
        for region in REGIONS:
            battles = (await db.scalars(
                select(Battle)
                .where(
                    Battle.region == region,
                    Battle.processing_tier == "deep",
                    Battle.is_lethal.is_(True),
                    Battle.kill_count >= DEFAULT_MIN_KILLS,
                    Battle.players_total >= DEFAULT_MIN_PLAYERS,
                )
                .order_by(Battle.start_time.desc())
                .limit(RECENT_BATTLES_PER_REGION)
            )).all()
            groups = await battle_groups.get_or_create_groups_bulk(db, [b.id for b in battles])
            factions_by_battle = await _factions_summary_bulk(db, [b.id for b in battles])
            for b in battles:
                group = groups[b.id]
                rows.append({
                    "public_id": group.public_id,
                    "region": b.region,
                    "start_time": _aware(b.start_time).isoformat(),
                    "end_time": _aware(b.end_time).isoformat() if b.end_time else None,
                    "total_fame": b.total_fame,
                    "kill_count": b.kill_count,
                    "cluster": b.cluster,
                    "players_total": b.players_total,
                    "is_zvz": b.is_zvz,
                    "factions": factions_by_battle.get(b.id, []),
                })
            counts[region] = await db.scalar(
                select(func.count(Battle.id)).where(
                    Battle.region == region,
                    Battle.processing_tier == "deep",
                    Battle.is_lethal.is_(True),
                    Battle.kill_count >= DEFAULT_MIN_KILLS,
                    Battle.players_total >= DEFAULT_MIN_PLAYERS,
                )
            ) or 0
    rows.sort(key=lambda r: r["start_time"], reverse=True)
    return {"rows": rows, "counts": counts}


async def refresh_highlights() -> dict[str, dict]:
    from app.api.routes.battles import (
        _week_start_utc, lethal_with_healing_filter, latest_guild_names, eligible_guild_battles_subquery,
    )
    from app.models.battles import BattleGuild

    week_start = _week_start_utc()
    eligible_guild_battles = eligible_guild_battles_subquery()
    result: dict[str, dict] = {}

    async with AsyncSessionLocal() as db:
        for region in REGIONS:
            fame_rows = (await db.execute(
                select(
                    BattleGuild.albion_guild_id,
                    func.sum(BattleGuild.kill_fame).label("fame"),
                    func.avg(eligible_guild_battles.c.player_count).label("avg_players"),
                )
                .join(Battle, Battle.id == BattleGuild.battle_id)
                .join(
                    eligible_guild_battles,
                    (eligible_guild_battles.c.battle_id == BattleGuild.battle_id)
                    & (eligible_guild_battles.c.guild_id == BattleGuild.albion_guild_id),
                )
                .where(
                    Battle.processing_tier == "deep",
                    Battle.start_time >= week_start,
                    Battle.region == region,
                    *lethal_with_healing_filter(),
                )
                .group_by(BattleGuild.albion_guild_id)
                .order_by(func.sum(BattleGuild.kill_fame).desc())
                .limit(HIGHLIGHTS_PER_REGION)
            )).all()

            if not fame_rows:
                result[f"highlights:{region}"] = {"week_start": week_start.isoformat(), "guilds": []}
                continue

            guild_ids = [r.albion_guild_id for r in fame_rows]
            fame_by_id = {r.albion_guild_id: int(r.fame or 0) for r in fame_rows}
            avg_players_by_id = {r.albion_guild_id: round(r.avg_players or 0) for r in fame_rows}
            latest_by_id = await latest_guild_names(db, guild_ids)

            result[f"highlights:{region}"] = {
                "week_start": week_start.isoformat(),
                "guilds": [
                    {
                        "albion_guild_id": gid,
                        "name": latest_by_id.get(gid, (gid, None))[0],
                        "alliance_name": latest_by_id.get(gid, (gid, None))[1],
                        "fame": fame_by_id[gid],
                        "avg_players": avg_players_by_id[gid],
                    }
                    for gid in guild_ids
                ],
            }
    return result


def sync_once() -> None:
    db = SyncSessionLocal()
    try:
        recent = asyncio.run(refresh_recent_battles())
        _upsert(db, "recent_battles", recent)
        highlights = asyncio.run(refresh_highlights())
        for key, payload in highlights.items():
            _upsert(db, key, payload)
        logger.info("dashboard_cache: ciclo ok")
    except OperationalError as e:
        if "is locked" in str(getattr(e, "orig", e)).lower():
            logger.warning("dashboard_cache: db locked — retry próximo ciclo")
        else:
            logger.exception("dashboard_cache.sync_once falhou")
        db.rollback()
    except Exception:
        logger.exception("dashboard_cache.sync_once falhou")
        db.rollback()
    finally:
        db.close()


async def run_forever() -> None:
    while True:
        await asyncio.to_thread(sync_once)
        await asyncio.sleep(INTERVAL)
