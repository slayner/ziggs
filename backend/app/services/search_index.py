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
import threading
import time

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.db import AsyncSessionLocal, SyncSessionLocal
from app.models.battles import Battle, BattleGuild, BattleParticipant
from app.models.players import AlbionPlayer, SearchEntry
from app.services.search_norm import normalize

log = logging.getLogger(__name__)

REBUILD_INTERVAL = 6 * 3600
_CHUNK = 5000

# Enquanto o rebuild (delete-all + reinsert em lote) roda, ele segura o write
# lock do SQLite em rajadas e reescreve TODA a search_entries a partir da fonte.
# O write-path (upsert por batalha/jogador) não deve brigar por esse lock: o
# rebuild já vai conter tudo que o upsert escreveria. Sem isso, cada upsert
# concorrente estourava "database is locked" — e como roda dentro da transação
# longa do tracker, é conflito de snapshot que `busy_timeout` nem resolve.
_rebuilding = threading.Event()
_last_lock_log = 0.0


def upsert_entry(
    db: Session | AsyncSession, entity_type: str, entity_id: str | None, display_name: str | None, *,
    region: str | None = None, guild_name: str | None = None,
    alliance_name: str | None = None, guild_count: int | None = None,
):
    """INSERT ... ON CONFLICT DO UPDATE. Retorna o resultado de db.execute(stmt)
    — Result (sync) ou coroutine (async). O caller decide se precisa await.
    `safe_upsert_entry` (sync) e `safe_upsert_entry_async` (async) encapsulam."""
    if not entity_id or not display_name:
        return None
    norm = normalize(display_name)
    stmt = pg_insert(SearchEntry).values(
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
    return db.execute(stmt)


def _handle_upsert_error(e: Exception, entity_id: str | None) -> None:
    if isinstance(e, OperationalError) and "database is locked" in str(e).lower():
        global _last_lock_log
        now = time.monotonic()
        if now - _last_lock_log > 60:
            _last_lock_log = now
            log.warning("search_index: SQLite ocupado, upsert pulado (rebuild reconcilia)")
        return
    log.exception("search_index: falha ao indexar %s", entity_id)


def safe_upsert_entry(db: Session, **kwargs) -> None:
    """Versão SYNC — pra callers com Session síncrona (battle_tracker, profiles
    sync). Nunca propaga — a ingestão não pode quebrar por causa do índice."""
    if _rebuilding.is_set():
        return
    try:
        upsert_entry(db, **kwargs)
    except Exception as e:
        _handle_upsert_error(e, kwargs.get("entity_id"))


async def safe_upsert_entry_async(db: AsyncSession, **kwargs) -> None:
    """Versão ASYNC — usa sessão PRÓPRIA pra isolar deadlock do search_entries
    da transação do caller (batalha/kill). Um deadlock no índice aborta só
    esta mini-transação, não a ingestão inteira. Nunca propaga."""
    if _rebuilding.is_set():
        return
    try:
        async with AsyncSessionLocal() as idx_db:
            result = upsert_entry(idx_db, **kwargs)
            if asyncio.iscoroutine(result):
                await result
            await idx_db.commit()
    except Exception as e:
        _handle_upsert_error(e, kwargs.get("entity_id"))


def _flush_chunk(db: Session, rows: list[dict]) -> None:
    if not rows:
        return
    db.bulk_insert_mappings(SearchEntry, rows)
    db.commit()
    rows.clear()


def rebuild(db: Session) -> dict[str, int]:
    # Delete em batches por PK (evita DELETE ALL segurando o write lock
    # por muito tempo — mesma doutrina do weapon_stats batch fix).
    # Batch de 500: SQLite tem limite de ~999 variáveis por query.
    while True:
        ids = db.scalars(select(SearchEntry.id).limit(500)).all()
        if not ids:
            break
        db.query(SearchEntry).filter(SearchEntry.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    counts = {"player": 0, "guild": 0, "alliance": 0}

    # ── Jogadores ────────────────────────────────────────────────────────
    # Materializa todos os SELECTs com .all() e fecha a read tx ANTES de
    # processar — iterar cursor lazy mantém a tx aberta por minutos,
    # bloqueando WAL checkpoint (mesmo fix do weapon_stats).
    p_rows = db.execute(
        select(BattleParticipant.albion_player_id, BattleParticipant.name, func.count(BattleParticipant.id))
        .group_by(BattleParticipant.albion_player_id, BattleParticipant.name)
    ).all()

    # Raw execute (não ORM) pra não acumular objetos na identity map da
    # sessão — 100k AlbionPlayer na identity map mantém a conexão pesada
    # mesmo depois do commit.
    tracker_rows = db.execute(
        select(AlbionPlayer.albion_id, AlbionPlayer.guild_name, AlbionPlayer.alliance_name, AlbionPlayer.region)
    ).all()

    fb_rows = db.execute(
        select(BattleParticipant.albion_player_id, BattleParticipant.guild_name, BattleParticipant.alliance_name)
        .join(Battle, Battle.id == BattleParticipant.battle_id)
        .order_by(BattleParticipant.albion_player_id, Battle.start_time.desc())
    ).all()

    # Fecha a read tx ANTES do processamento em Python.
    db.commit()

    p_agg: dict[str, dict] = {}
    for pid, name, battles in p_rows:
        cur = p_agg.get(pid)
        if cur is None or battles > cur["battles"]:
            p_agg[pid] = {"name": name, "battles": battles}

    tracker_map = {r[0]: r for r in tracker_rows}

    # Afiliação mais recente pra quem não está no tracker (fallback).
    fallback_affil: dict[str, tuple[str | None, str | None]] = {}
    for pid, gname, aname in fb_rows:
        fallback_affil.setdefault(pid, (gname, aname))

    rows: list[dict] = []
    for pid, agg in p_agg.items():
        tr = tracker_map.get(pid)
        if tr is not None:
            guild_name, alliance_name, region = tr[1], tr[2], tr[3]
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
    g_rows = db.execute(
        select(BattleGuild.albion_guild_id, BattleGuild.guild_name, BattleGuild.alliance_name,
               func.count(func.distinct(BattleGuild.battle_id)))
        .group_by(BattleGuild.albion_guild_id, BattleGuild.guild_name, BattleGuild.alliance_name)
    ).all()

    g_region_rows = db.execute(
        select(BattleGuild.albion_guild_id, Battle.region)
        .join(Battle, Battle.id == BattleGuild.battle_id)
    ).all()

    # Fecha a read tx ANTES de processar.
    db.commit()

    g_best: dict[str, dict] = {}
    for gid, name, aname, battles in g_rows:
        cur = g_best.get(gid)
        if cur is None or battles > cur["battles"]:
            g_best[gid] = {"name": name, "alliance_name": aname, "battles": battles}

    g_region: dict[str, str] = {}
    for gid, region in g_region_rows:
        g_region.setdefault(gid, {})
        g_region[gid][region] = g_region[gid].get(region, 0) + 1

    rows = []
    for gid, agg in g_best.items():
        norm = normalize(agg["name"])
        region = max(g_region.get(gid, {}), key=g_region.get(gid, {}).get, default=None) if g_region.get(gid) else None
        rows.append({
            "entity_type": "guild", "entity_id": gid,
            "display_name": agg["name"], "norm_name": norm, "name_len": len(norm),
            "region": region, "guild_name": None, "alliance_name": agg["alliance_name"],
            "guild_count": None, "weight": agg["battles"],
        })
        counts["guild"] += 1
        if len(rows) >= _CHUNK:
            _flush_chunk(db, rows)
    _flush_chunk(db, rows)

    # ── Alianças ─────────────────────────────────────────────────────────
    ag_rows = db.execute(
        select(BattleGuild.alliance_id, BattleGuild.albion_guild_id).where(BattleGuild.alliance_id.isnot(None))
    ).all()

    a_rows = db.execute(
        select(BattleGuild.alliance_id, BattleGuild.alliance_name,
               func.count(func.distinct(BattleGuild.battle_id)))
        .where(BattleGuild.alliance_id.isnot(None))
        .group_by(BattleGuild.alliance_id, BattleGuild.alliance_name)
    ).all()

    ar_rows = db.execute(
        select(BattleGuild.alliance_id, Battle.region)
        .join(Battle, Battle.id == BattleGuild.battle_id)
        .where(BattleGuild.alliance_id.isnot(None))
    ).all()

    # Fecha a read tx ANTES de processar.
    db.commit()

    a_guild_ids: dict[str, set[str]] = {}
    for aid, gid in ag_rows:
        a_guild_ids.setdefault(aid, set()).add(gid)

    a_best: dict[str, dict] = {}
    for aid, aname, battles in a_rows:
        cur = a_best.get(aid)
        if cur is None or battles > cur["battles"]:
            a_best[aid] = {"name": aname, "battles": battles}

    a_region: dict[str, dict[str, int]] = {}
    for aid, region in ar_rows:
        a_region.setdefault(aid, {})
        a_region[aid][region] = a_region[aid].get(region, 0) + 1

    rows = []
    for aid, agg in a_best.items():
        norm = normalize(agg["name"])
        region = max(a_region.get(aid, {}), key=a_region.get(aid, {}).get, default=None) if a_region.get(aid) else None
        rows.append({
            "entity_type": "alliance", "entity_id": aid,
            "display_name": agg["name"], "norm_name": norm, "name_len": len(norm),
            "region": region, "guild_name": None, "alliance_name": None,
            "guild_count": len(a_guild_ids.get(aid, ())), "weight": agg["battles"],
        })
        counts["alliance"] += 1
        if len(rows) >= _CHUNK:
            _flush_chunk(db, rows)
    _flush_chunk(db, rows)

    return counts


def _rebuild_sync() -> dict[str, int]:
    _rebuilding.set()  # write-path pausa upserts enquanto reescrevemos tudo
    db = SyncSessionLocal()
    try:
        return rebuild(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        _rebuilding.clear()


async def run_forever() -> None:
    log.info("search_index: iniciando (intervalo=%ds)", REBUILD_INTERVAL)
    db = SyncSessionLocal()
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
