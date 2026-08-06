"""Precompute dos Highscores (rankings + destaques) a cada 15 minutos.

DESENHO (jul/2026):
1. Leitura em uma sessão dedicada `db_read`: computa TODOS os rankings e
   highlights em memória (nenhum write, nenhum commit intermediário).
2. Uma única transação de escrita `db_write`: grava todos os DashboardCache
   de uma vez só (1 commit por ciclo).
3. Intervalo 15 minutos — o ranking NUNCA recalcula ao abrir a página.
4. Se o ciclo falhar por lock/erro, o próximo tenta de novo; a rota sempre
   lê o cache anterior, mesmo stale.

Antes este serviço fazia ~100 commits por ciclo (um por chave). Com 22 bg
services no mesmo processo SQLite, isso competia pelo write lock, segurava
read transactions durante writes e impedia o WAL checkpoint — causando
'database is locked' e commits de 100+ segundos.
"""
from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import select

from app.db import SyncSessionLocal
from app.models.dashboard_cache import DashboardCache

logger = logging.getLogger(__name__)

# 15 minutos: ranking é background, não on-demand.
INTERVAL = 900

TOP_N = 500
GUILD_FULL_LIMIT = 100_000

REGION_SELECTIONS: list[list[str]] = [
    ["americas"], ["europe"], ["asia"],
    ["americas", "europe"], ["americas", "asia"], ["europe", "asia"],
    ["americas", "europe", "asia"],
]
WINDOWS = ["alltime", "week"]

GUILD_CACHED_KINDS = ["pvp_fame", "efficiency", "most_battles", "underdog"]
_GATHER_SILVER_KINDS = [
    "gather_total", "gather_wood", "gather_hide", "gather_ore", "gather_rock", "gather_fiber",
    "fishing", "crafting", "silver_dropped",
]

_FULL_KEY = "-".join(sorted(["americas", "europe", "asia"]))
_PAIR_KEYS = {
    "-".join(sorted(["americas", "europe"])),
    "-".join(sorted(["americas", "asia"])),
    "-".join(sorted(["europe", "asia"])),
}
_WEAPON_SCORER_CACHEABLE = _PAIR_KEYS | {_FULL_KEY}
CACHED_KINDS = [*GUILD_CACHED_KINDS, "weapon_scorer"]

_CACHEABLE = {"-".join(sorted(rl)) for rl in REGION_SELECTIONS}


def _region_key(region_list: list[str] | None) -> str | None:
    if not region_list:
        return None
    key = "-".join(sorted(region_list))
    return key if key in _CACHEABLE else None


def rankings_cache_key(kind: str, window: str, region_list: list[str] | None) -> str | None:
    rk = _region_key(region_list)
    if not rk:
        return None
    if kind in GUILD_CACHED_KINDS:
        return f"hs:rk:{kind}:{window}:{rk}"
    if kind == "weapon_scorer" and rk in _WEAPON_SCORER_CACHEABLE:
        return f"hs:rk:{kind}:{window}:{rk}"
    if kind in _GATHER_SILVER_KINDS and window == "alltime":
        return f"hs:rk:{kind}:alltime:{rk}"
    return None


def highlights_cache_key(region_list: list[str] | None) -> str | None:
    rk = _region_key(region_list)
    return f"hs:hl:{rk}" if rk else None


def _compute_all() -> dict[str, dict]:
    """Computa todos os caches de highscores em uma única sessão de leitura.
    Devolve {cache_key: payload}."""
    import asyncio
    from app.api.routes.highscores import _compute_highlights, _compute_rankings, _window_start

    payloads: dict[str, dict] = {}
    db_read = SyncSessionLocal()
    try:
        # ponytail: _compute_highlights/_compute_rankings são async (migração async
        # DB), mas highscores_cache roda em thread (to_thread) — asyncio.run cria
        # loop efêmero. Se virar gargalo, migrar highscores_cache pra async direto.
        for region_list in REGION_SELECTIONS:
            hl_key = highlights_cache_key(region_list)
            if hl_key:
                payloads[hl_key] = asyncio.run(_compute_highlights(db_read, region_list))

            for window in WINDOWS:
                week_start = _window_start(window)
                for kind in CACHED_KINDS:
                    key = rankings_cache_key(kind, window, region_list)
                    if not key:
                        continue
                    lim = GUILD_FULL_LIMIT if kind in GUILD_CACHED_KINDS else TOP_N
                    payloads[key] = asyncio.run(_compute_rankings(db_read, kind, region_list, week_start, None, lim, 0))

                if window == "alltime":
                    for kind in _GATHER_SILVER_KINDS:
                        key = rankings_cache_key(kind, "alltime", region_list)
                        if not key:
                            continue
                        payloads[key] = asyncio.run(_compute_rankings(db_read, kind, region_list, None, None, TOP_N, 0))
    finally:
        db_read.close()
    return payloads


def _write_all(payloads: dict[str, dict]) -> int:
    """Grava todos os payloads numa única transação. Retorna quantidade."""
    if not payloads:
        return 0
    db_write = SyncSessionLocal()
    written = 0
    try:
        existing = {
            row.key: row for row in db_write.scalars(
                select(DashboardCache).where(DashboardCache.key.in_(payloads.keys()))
            ).all()
        }
        for key, payload in payloads.items():
            row = existing.get(key)
            if row is None:
                db_write.add(DashboardCache(key=key, payload=payload))
            else:
                row.payload = payload
            written += 1
        db_write.commit()
    except Exception:
        db_write.rollback()
        raise
    finally:
        db_write.close()
    return written


def sync_once() -> int:
    t_start = time.monotonic()
    try:
        payloads = _compute_all()
        written = _write_all(payloads)
        t_total = time.monotonic() - t_start
        logger.info("highscores_cache: ciclo %.1fs (%d chaves)", t_total, written)
        return written
    except Exception:
        logger.exception("highscores_cache: falha no ciclo")
        return 0


async def run_forever() -> None:
    logger.info("highscores_cache: iniciando (intervalo=%ds)", INTERVAL)
    while True:
        await asyncio.to_thread(sync_once)
        await asyncio.sleep(INTERVAL)
