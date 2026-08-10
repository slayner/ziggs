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

from app.db import AsyncSessionLocal
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
# Mês e seasons calculam sob demanda. Pré-computá-los dobrou o ciclo para mais
# de 2 minutos de CPU alta em produção; semana/all-time preservam o cache antigo.
WINDOWS = ["alltime", "week"]

GUILD_CACHED_KINDS = ["pvp_fame", "efficiency", "most_battles", "underdog"]
# gather/fishing/crafting são famas ACUMULADAS da conta (sem timestamp) —
# honestamente all-time só, não suportam window. Não rotule como seasonal.
_ALLTIME_ONLY_KINDS = [
    "gather_total", "gather_wood", "gather_hide", "gather_ore", "gather_rock", "gather_fiber",
    "fishing", "crafting",
]
# silver_dropped é timestamp-backed (PlayerKillEvent.timestamp) — suporta os
# windows correntes (alltime/week/month/season) e é pré-calculado pra todos eles.
_SILVER_KIND = "silver_dropped"

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
    if not rk or window not in WINDOWS:
        return None  # window fora dos correntes (ex.: season:N histórico) → ao vivo
    if kind in GUILD_CACHED_KINDS:
        return f"hs:rk:{kind}:{window}:{rk}"
    if kind == "weapon_scorer" and rk in _WEAPON_SCORER_CACHEABLE:
        return f"hs:rk:{kind}:{window}:{rk}"
    if kind in _ALLTIME_ONLY_KINDS and window == "alltime":
        return f"hs:rk:{kind}:alltime:{rk}"
    if kind == _SILVER_KIND:
        return f"hs:rk:{kind}:{window}:{rk}"
    return None


def highlights_cache_key(region_list: list[str] | None) -> str | None:
    rk = _region_key(region_list)
    return f"hs:hl:{rk}" if rk else None


async def _compute_all() -> dict[str, dict]:
    """Computa todos os caches de highscores em uma única sessão de leitura.
    Devolve {cache_key: payload}."""
    from app.api.routes.highscores import _compute_highlights, _compute_rankings, _resolve_window, _window_marker

    payloads: dict[str, dict] = {}
    async with AsyncSessionLocal() as db_read:
        for region_list in REGION_SELECTIONS:
            hl_key = highlights_cache_key(region_list)
            if hl_key:
                payloads[hl_key] = await _compute_highlights(db_read, region_list)

            for window in WINDOWS:
                tw = await _resolve_window(window, region_list)
                for kind in CACHED_KINDS:
                    key = rankings_cache_key(kind, window, region_list)
                    if not key:
                        continue
                    lim = GUILD_FULL_LIMIT if kind in GUILD_CACHED_KINDS else TOP_N
                    payloads[key] = await _compute_rankings(db_read, kind, region_list, tw, None, lim, 0)
                    payloads[key]["_window"] = _window_marker(tw)

                if window == "alltime":
                    # gather/fishing/crafting: acumulados da conta, all-time só.
                    for kind in _ALLTIME_ONLY_KINDS:
                        key = rankings_cache_key(kind, "alltime", region_list)
                        if not key:
                            continue
                        payloads[key] = await _compute_rankings(db_read, kind, region_list, tw, None, TOP_N, 0)
                        payloads[key]["_window"] = _window_marker(tw)

                # silver_dropped: timestamp-backed, suporta todos os windows correntes.
                skey = rankings_cache_key(_SILVER_KIND, window, region_list)
                if skey:
                    payloads[skey] = await _compute_rankings(db_read, _SILVER_KIND, region_list, tw, None, TOP_N, 0)
                    payloads[skey]["_window"] = _window_marker(tw)
    return payloads


async def _write_all(payloads: dict[str, dict]) -> int:
    """Grava todos os payloads numa única transação. Retorna quantidade."""
    if not payloads:
        return 0
    written = 0
    async with AsyncSessionLocal() as db_write:
        existing = {
            row.key: row for row in (await db_write.scalars(
                select(DashboardCache).where(DashboardCache.key.in_(payloads.keys()))
            )).all()
        }
        try:
            for key, payload in payloads.items():
                row = existing.get(key)
                if row is None:
                    db_write.add(DashboardCache(key=key, payload=payload))
                else:
                    row.payload = payload
                written += 1
            await db_write.commit()
        except Exception:
            await db_write.rollback()
            raise
    return written


async def sync_once() -> int:
    t_start = time.monotonic()
    try:
        payloads = await _compute_all()
        written = await _write_all(payloads)
        t_total = time.monotonic() - t_start
        logger.info("highscores_cache: ciclo %.1fs (%d chaves)", t_total, written)
        return written
    except Exception:
        logger.exception("highscores_cache: falha no ciclo")
        return 0


async def run_forever() -> None:
    # ponytail: o ciclo repetia as mesmas agregações por kind/região e ocupou
    # vários núcleos por >4min em produção. Reativar após agrupar essas queries.
    logger.info("highscores_cache: precompute automático desativado")
    while True:
        await asyncio.sleep(INTERVAL)
