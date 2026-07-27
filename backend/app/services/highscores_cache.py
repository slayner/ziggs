"""Precompute dos Highscores (rankings + destaques) a cada 5min — antes disso
cada abertura da página rodava as agregações pesadas ao vivo (GROUP BY de
guildas, scan de participantes pra pontos por arma), aparecendo como "loading".
Mesmo padrão de dashboard_cache: guarda o topo generoso por (kind, janela,
seleção-de-região) na tabela dashboard_cache; as rotas fatiam em Python por
cima. Busca, páginas além do top-N e combinações raras de região continuam indo
ao vivo (ver highscores._compute_rankings)."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.exc import OperationalError

from app.db import SessionLocal
from app.models.dashboard_cache import DashboardCache

logger = logging.getLogger(__name__)

INTERVAL = 300
TOP_N = 500  # páginas 0..(TOP_N/PAGE_SIZE) servidas do cache; mais fundo = ao vivo
# Guildas elegíveis são poucas centenas (~700 all-time, todas as regiões) — cabe
# o ranking INTEIRO no cache. Guardando tudo, a busca por nome no ranking de
# guilda resolve SEMPRE do cache (o `total <= len(rows)` da rota vê o cache
# completo e não recorre ao cálculo ao vivo, que custa a agregação de ~5s),
# mesmo pra guilda de rank baixo ou nome que não casa ninguém.
GUILD_FULL_LIMIT = 100_000

# Todos os 7 subconjuntos não-vazios das 3 regiões — o usuário pode selecionar
# qualquer combinação de servidores no site, então qualquer combinação tem que
# ser cacheada. Antes só as singles e as 3-juntas eram cacheadas, e uma seleção
# de DUAS regiões caía no cálculo ao vivo dos destaques (scan semanal de ~470k
# participantes pro card weapon_scorer, ~20s a cada abertura da página).
REGION_SELECTIONS: list[list[str]] = [
    ["americas"], ["europe"], ["asia"],
    ["americas", "europe"], ["americas", "asia"], ["europe", "asia"],
    ["americas", "europe", "asia"],
]
WINDOWS = ["alltime", "week"]
# Kinds de guilda são baratos (um GROUP BY) — cacheados pra toda seleção.
# weapon:<base> fica de fora de propósito: o usuário escolhe uma arma no
# dropdown (ação deliberada, tolera espera); a página abre em pvp_fame.
GUILD_CACHED_KINDS = ["pvp_fame", "efficiency", "most_battles", "underdog"]
# Kinds de jogador que agregam de AlbionPlayer/player_kill_events diretamente
# (coleta + silver_dropped) — "alltime" só (famas acumulativas / prata
# histórica), nunca janela semanal. Baratos (um GROUP BY / ORDER BY indexado),
# cacheados pra toda seleção de região.
_GATHER_SILVER_KINDS = [
    "gather_total", "gather_wood", "gather_hide", "gather_ore", "gather_rock", "gather_fiber",
    "fishing", "crafting", "silver_dropped",
]
# weapon_scorer (esp. na janela "week") escaneia ~470k participantes; caro
# demais pra rodar por região. Cacheado pro full-combo (3 regiões, default do
# site) e pros pairs (2 regiões — caminho comum quando alguém desmarca um
# servidor). Singles (1 região) ficam ao vivo: raras (o default tem os 3) e
# duplicam o custo do precompute pra um caso que quase ninguém dispara.
_FULL_KEY = "-".join(sorted(["americas", "europe", "asia"]))
_PAIR_KEYS = {
    "-".join(sorted(["americas", "europe"])),
    "-".join(sorted(["americas", "asia"])),
    "-".join(sorted(["europe", "asia"])),
}
_WEAPON_SCORER_CACHEABLE = _PAIR_KEYS | {_FULL_KEY}
CACHED_KINDS = [*GUILD_CACHED_KINDS, "weapon_scorer"]


def _region_key(region_list: list[str] | None) -> str | None:
    """Chave canônica (ordem-independente) da seleção de regiões, ou None se
    não é uma seleção cacheada."""
    if not region_list:
        return None
    key = "-".join(sorted(region_list))
    return key if key in _CACHEABLE else None


_CACHEABLE = {"-".join(sorted(rl)) for rl in REGION_SELECTIONS}


def rankings_cache_key(kind: str, window: str, region_list: list[str] | None) -> str | None:
    rk = _region_key(region_list)
    if not rk:
        return None
    if kind in GUILD_CACHED_KINDS:
        return f"hs:rk:{kind}:{window}:{rk}"
    if kind == "weapon_scorer" and rk in _WEAPON_SCORER_CACHEABLE:
        return f"hs:rk:{kind}:{window}:{rk}"
    # Coleta + silver: alltime só. Janela "week" não faz sentido (famas
    # acumulativas da conta), nunca cacheada — deixa a rota devolver o
    # alltime mesmo se pedirem week (o ranking não tem corte semanal).
    if kind in _GATHER_SILVER_KINDS and window == "alltime":
        return f"hs:rk:{kind}:alltime:{rk}"
    return None


def highlights_cache_key(region_list: list[str] | None) -> str | None:
    rk = _region_key(region_list)
    return f"hs:hl:{rk}" if rk else None


def _upsert(db, key: str, build) -> None:
    """Computa `build()` e grava sob `key`, isolado: um lock/erro numa chave não
    derruba o ciclo inteiro (cada chave é uma transação própria; a que falhar
    recompõe no próximo ciclo). `build` é lazy pra não pagar a query pesada
    quando só íamos descartar por lock."""
    try:
        payload = build()
        row = db.get(DashboardCache, key)
        if row is None:
            db.add(DashboardCache(key=key, payload=payload))
        else:
            row.payload = payload
        db.commit()
    except OperationalError as e:
        db.rollback()
        if "is locked" in str(getattr(e, "orig", e)).lower():
            logger.warning("highscores_cache: db locked em %s — retry próximo ciclo", key)
        else:
            logger.exception("highscores_cache: falha em %s", key)
    except Exception:
        db.rollback()
        logger.exception("highscores_cache: falha em %s", key)


def sync_once() -> None:
    # Import tardio: a rota importa este módulo (nas funções), então importar a
    # rota aqui no topo fecharia o ciclo. Dentro da função é seguro.
    from app.api.routes.highscores import _compute_highlights, _compute_rankings, _window_start

    db = SessionLocal()
    try:
        for region_list in REGION_SELECTIONS:
            rl = region_list  # captura por-iteração pros lambdas
            _upsert(db, highlights_cache_key(rl), lambda: _compute_highlights(db, rl))
            for window in WINDOWS:
                week_start = _window_start(window)
                for kind in CACHED_KINDS:
                    key = rankings_cache_key(kind, window, rl)
                    if not key:
                        continue  # weapon_scorer fora de full/pairs: singles ficam ao vivo
                    # Guilda: ranking inteiro (busca por nome resolve do cache);
                    # jogador: top-N (são ~100k, cachear tudo não faz sentido).
                    lim = GUILD_FULL_LIMIT if kind in GUILD_CACHED_KINDS else TOP_N
                    _upsert(db, key, lambda k=kind, ws=week_start, lim=lim: _compute_rankings(db, k, rl, ws, None, lim, 0))
                # Kinds de coleta + silver: alltime só. Janela "week" não é
                # cacheada (rankings_cache_key devolve None), e a rota devolve
                # o alltime mesmo se pedirem week.
                if window == "alltime":
                    for kind in _GATHER_SILVER_KINDS:
                        key = rankings_cache_key(kind, "alltime", rl)
                        if not key:
                            continue
                        _upsert(db, key, lambda k=kind: _compute_rankings(db, k, rl, None, None, TOP_N, 0))
    finally:
        db.close()


async def run_forever() -> None:
    while True:
        await asyncio.to_thread(sync_once)
        await asyncio.sleep(INTERVAL)
