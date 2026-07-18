"""Índice de busca global (SearchEntry) — mantém entity_type=player/guild/
alliance pré-normalizado pra evitar full-scan de battle_participants/
battle_guilds a cada tecla digitada (ver routes/profiles.py _search e
routes/players.py search_players).

`upsert_entry`: chamado no write-path (battle_tracker/player_tracker) — só
mantém IDENTIDADE (nome/afiliação) fresca entre rebuilds; NÃO recalcula
`weight` (custaria um COUNT a cada kill/batalha ingerida). Sem commit — a
transação de quem chama decide quando persistir, e uma falha aqui nunca deve
derrubar a ingestão (ver try/except nos call sites).

`rebuild`: recomputa do zero a partir das tabelas-fonte (fonte da verdade),
inclusive `weight`. Roda no boot (se a tabela estiver vazia) e a cada
REBUILD_INTERVAL (ver run_forever), igual ao padrão de services/weapon_stats.py."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.battles import Battle, BattleGuild, BattleParticipant
from app.models.players import AlbionPlayer, SearchEntry
from app.services.search_norm import normalize

log = logging.getLogger(__name__)

REBUILD_INTERVAL = 6 * 3600
_CHUNK = 5000


def upsert_entry(
    db: Session, entity_type: str, entity_id: str | None, display_name: str | None, *,
    region: str | None = None, guild_name: str | None = None,
    alliance_name: str | None = None, guild_count: int | None = None,
) -> None:
    """INSERT ... ON CONFLICT DO UPDATE, não select-then-insert: battle_tracker
    roda sync_recent e backfill_cycle em tasks concorrentes com sessões
    separadas (ver run_forever/run_backfill_forever), então dois SELECTs
    podem ver a MESMA aliança/guilda como inexistente ao mesmo tempo e ambos
    tentar inserir — UNIQUE constraint failed, e como o INSERT do ORM só
    dispara no flush (não no db.add), o try/except de safe_upsert_entry nem
    pegava o erro. execute() aqui roda a SQL na hora, dentro do try/except."""
    if not entity_id or not display_name:
        return
    norm = normalize(display_name)
    stmt = sqlite_insert(SearchEntry).values(
        entity_type=entity_type, entity_id=entity_id,
        display_name=display_name, norm_name=norm, name_len=len(norm),
        region=region, guild_name=guild_name, alliance_name=alliance_name,
        guild_count=guild_count, weight=1,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[SearchEntry.entity_type, SearchEntry.entity_id],
        set_={
            "display_name": stmt.excluded.display_name,
            "norm_name": stmt.excluded.norm_name,
            "name_len": stmt.excluded.name_len,
            "region": func.coalesce(stmt.excluded.region, SearchEntry.region),
            "guild_name": stmt.excluded.guild_name,
            "alliance_name": stmt.excluded.alliance_name,
            "guild_count": func.coalesce(stmt.excluded.guild_count, SearchEntry.guild_count),
        },
    )
    db.execute(stmt)


def safe_upsert_entry(db: Session, **kwargs) -> None:
    """Como upsert_entry, mas nunca propaga — a ingestão de batalhas/
    jogadores não pode quebrar por causa do índice de busca."""
    try:
        upsert_entry(db, **kwargs)
    except Exception:
        log.exception("search_index: falha ao indexar %s", kwargs.get("entity_id"))


def _flush_chunk(db: Session, rows: list[dict]) -> None:
    if not rows:
        return
    db.bulk_insert_mappings(SearchEntry, rows)
    db.commit()
    rows.clear()


def rebuild(db: Session) -> dict[str, int]:
    db.query(SearchEntry).delete()
    db.commit()
    counts = {"player": 0, "guild": 0, "alliance": 0}

    # ── Jogadores ────────────────────────────────────────────────────────
    # Nome/contagem vêm de battle_participants — cobre todo mundo que já
    # apareceu numa luta rastreada (não só quem foi upsertado pelo
    # player_tracker), igual à fonte do _search antigo.
    p_agg: dict[str, dict] = {}
    for pid, name, battles in db.execute(
        select(BattleParticipant.albion_player_id, BattleParticipant.name, func.count(BattleParticipant.id))
        .group_by(BattleParticipant.albion_player_id, BattleParticipant.name)
    ):
        cur = p_agg.get(pid)
        if cur is None or battles > cur["battles"]:
            p_agg[pid] = {"name": name, "battles": battles}

    tracker_map = {ap.albion_id: ap for ap in db.scalars(select(AlbionPlayer)).yield_per(2000)}

    # Afiliação mais recente pra quem não está no tracker (fallback — mesma
    # ideia do _latest_affil antigo, mas pré-computada de uma vez só).
    fallback_affil: dict[str, tuple[str | None, str | None]] = {}
    for pid, gname, aname in db.execute(
        select(BattleParticipant.albion_player_id, BattleParticipant.guild_name, BattleParticipant.alliance_name)
        .join(Battle, Battle.id == BattleParticipant.battle_id)
        .order_by(BattleParticipant.albion_player_id, Battle.start_time.desc())
    ):
        fallback_affil.setdefault(pid, (gname, aname))

    rows: list[dict] = []
    for pid, agg in p_agg.items():
        ap = tracker_map.get(pid)
        if ap is not None:
            guild_name, alliance_name, region = ap.guild_name, ap.alliance_name, ap.region
        else:
            guild_name, alliance_name = fallback_affil.get(pid, (None, None))
            region = "americas"
        norm = normalize(agg["name"])
        rows.append({
            "entity_type": "player", "entity_id": pid,
            "display_name": agg["name"], "norm_name": norm, "name_len": len(norm),
            "region": region, "guild_name": guild_name, "alliance_name": alliance_name,
            "guild_count": None, "weight": agg["battles"],
        })
        counts["player"] += 1
        if len(rows) >= _CHUNK:
            _flush_chunk(db, rows)
    _flush_chunk(db, rows)

    # ── Guildas ──────────────────────────────────────────────────────────
    # Mesma (guild_id, nome, aliança) que mais aparece vence — igual ao dedup
    # do _search antigo (guilda pode ter trocado de nome/aliança ao longo do
    # tempo, a variante mais comum é a exibida).
    g_best: dict[str, dict] = {}
    for gid, name, aname, battles in db.execute(
        select(BattleGuild.albion_guild_id, BattleGuild.guild_name, BattleGuild.alliance_name,
               func.count(func.distinct(BattleGuild.battle_id)))
        .group_by(BattleGuild.albion_guild_id, BattleGuild.guild_name, BattleGuild.alliance_name)
    ):
        cur = g_best.get(gid)
        if cur is None or battles > cur["battles"]:
            g_best[gid] = {"name": name, "alliance_name": aname, "battles": battles}

    rows = []
    for gid, agg in g_best.items():
        norm = normalize(agg["name"])
        rows.append({
            "entity_type": "guild", "entity_id": gid,
            "display_name": agg["name"], "norm_name": norm, "name_len": len(norm),
            "region": None, "guild_name": None, "alliance_name": agg["alliance_name"],
            "guild_count": None, "weight": agg["battles"],
        })
        counts["guild"] += 1
        if len(rows) >= _CHUNK:
            _flush_chunk(db, rows)
    _flush_chunk(db, rows)

    # ── Alianças ─────────────────────────────────────────────────────────
    a_guild_ids: dict[str, set[str]] = {}
    for aid, gid in db.execute(
        select(BattleGuild.alliance_id, BattleGuild.albion_guild_id).where(BattleGuild.alliance_id.isnot(None))
    ):
        a_guild_ids.setdefault(aid, set()).add(gid)

    a_best: dict[str, dict] = {}
    for aid, aname, battles in db.execute(
        select(BattleGuild.alliance_id, BattleGuild.alliance_name,
               func.count(func.distinct(BattleGuild.battle_id)))
        .where(BattleGuild.alliance_id.isnot(None))
        .group_by(BattleGuild.alliance_id, BattleGuild.alliance_name)
    ):
        cur = a_best.get(aid)
        if cur is None or battles > cur["battles"]:
            a_best[aid] = {"name": aname, "battles": battles}

    rows = []
    for aid, agg in a_best.items():
        norm = normalize(agg["name"])
        rows.append({
            "entity_type": "alliance", "entity_id": aid,
            "display_name": agg["name"], "norm_name": norm, "name_len": len(norm),
            "region": None, "guild_name": None, "alliance_name": None,
            "guild_count": len(a_guild_ids.get(aid, ())), "weight": agg["battles"],
        })
        counts["alliance"] += 1
        if len(rows) >= _CHUNK:
            _flush_chunk(db, rows)
    _flush_chunk(db, rows)

    return counts


def _rebuild_sync() -> dict[str, int]:
    db = SessionLocal()
    try:
        return rebuild(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def run_forever() -> None:
    log.info("search_index: iniciando (intervalo=%ds)", REBUILD_INTERVAL)
    db = SessionLocal()
    try:
        is_empty = db.scalar(select(func.count()).select_from(SearchEntry)) == 0
    finally:
        db.close()

    if is_empty:
        try:
            n = await asyncio.to_thread(_rebuild_sync)
            log.info("search_index: rebuild inicial %s", n)
        except Exception as e:
            log.error("search_index: erro no rebuild inicial: %s", e)

    while True:
        await asyncio.sleep(REBUILD_INTERVAL)
        try:
            n = await asyncio.to_thread(_rebuild_sync)
            log.info("search_index: rebuild periódico %s", n)
        except Exception as e:
            log.error("search_index: erro: %s", e)
