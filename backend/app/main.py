"""App FastAPI — Ziggs platform."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.rate_limit import RateLimitMiddleware
from app.api.routes import auth, battles, catalog, claims, companion, comps, craft, events, highscores, loot, lootlog, market_history, meta, nodes, players, profiles, regear, render, user_profile
from app.config import get_settings
from app.domain.states import EventState, allowed_targets
from app.services import (
    battle_price_reprocessor, battle_reprocessor, battle_sweeper, battle_tracker, claim_checker, companion_scan, dashboard_cache,
    gold_price, market_snapshot, player_count_snapshot, player_tracker, profile_warmer, registration_checker, regear_retry,
    search_index, small_battle_discovery, weapon_stats,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if get_settings().disable_background_fetchers:
        # Modo dado móvel: nenhum tracker/fetcher de polling sobe. As rotas da
        # UI continuam disponíveis (só fazem request externo sob demanda).
        print("⚠️  DISABLE_BACKGROUND_FETCHERS=true — fetchers de background desligados.")
        tasks: list[asyncio.Task] = []
    else:
        tasks = [
            asyncio.create_task(player_tracker.run_forever()),
            asyncio.create_task(battle_tracker.run_forever()),
            asyncio.create_task(battle_tracker.run_backfill_forever()),
            asyncio.create_task(battle_tracker.run_retry_stuck_forever()),
            asyncio.create_task(profile_warmer.run_forever()),
            asyncio.create_task(claim_checker.run_forever()),
            asyncio.create_task(registration_checker.run_forever()),
            asyncio.create_task(weapon_stats.run_forever()),
            asyncio.create_task(battle_reprocessor.run_forever()),
            asyncio.create_task(battle_sweeper.run_forever()),
            asyncio.create_task(companion_scan.run_forever()),
            asyncio.create_task(small_battle_discovery.run_forever()),
            asyncio.create_task(player_count_snapshot.run_forever()),
            asyncio.create_task(battle_price_reprocessor.run_forever()),
            asyncio.create_task(regear_retry.run_forever()),
            asyncio.create_task(dashboard_cache.run_forever()),
            asyncio.create_task(search_index.run_forever()),
            asyncio.create_task(gold_price.run_forever()),
            asyncio.create_task(market_snapshot.run_forever()),
        ]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Ziggs API", lifespan=lifespan)

# Login/callback do Discord: bem mais restrito, é a rota mais sensível (a "passagem").
app.add_middleware(RateLimitMiddleware, limit=10, window=60, prefix="/auth/discord")
# Limite geral por IP — split leitura/escrita em vez de um balde único pros
# dois. A SPA é read-heavy por natureza: várias páginas ficam montadas em
# keep-alive (ManagementPage/GuildConfig/EventsPage/EscalacaoPage/RegearPage,
# ver App.tsx) fazendo polling PARALELO (8s a 15s cada) mesmo fora de uso ativo
# só pra não perder atualização ao vivo, e trocar de sub-aba ainda dispara uma
# rajada de fetches simultâneos — um balde de 120/min pro site inteiro (leitura
# E escrita juntas) estourava com uso normal, sem ninguém abusando. Escrita
# (POST/PUT/PATCH/DELETE) é onde abuso de verdade concentra (spam de forms,
# script batendo em endpoint de mutação) — fica num teto bem mais apertado.
# /render/item é excluído: uma única página pode legitimamente disparar dezenas
# de ícones de uma vez (cache hit ou miss), isso não é abuso. Proteção própria
# dele é o semáforo de concorrência em render.py.
# /bot/ é excluído: já é protegido por _require_bot_secret (token, não IP), e o
# bot-v2 tem ~7 loops de polling concorrentes (5s a 5min) batendo do MESMO IP —
# sem essa exclusão, o bucket geral estourava e engolia respostas do bot em
# silêncio (_get/_post tratam não-200 como no-op), fazendo threads de regear e
# detecção de presença na call parecerem quebradas (só "funcionavam" logo após
# reiniciar o bot, quando o bucket ainda estava zerado).
app.add_middleware(RateLimitMiddleware, limit=600, window=60, methods=("GET", "HEAD"),
                    exclude_prefix=("/render/item", "/bot/", "/companion/"))
app.add_middleware(RateLimitMiddleware, limit=90, window=60, methods=("POST", "PUT", "PATCH", "DELETE"),
                    exclude_prefix=("/render/item", "/bot/", "/companion/"))

# Permite que o Vite dev server (localhost:5173) chame a API diretamente.
# Em produção o frontend e o backend ficam na mesma origem — este middleware é inofensivo.
# Precisa ser o ÚLTIMO add_middleware (= mais externo) pra decorar até as respostas
# 429 dos rate limiters acima com os headers de CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(battles.router)
app.include_router(claims.router)
app.include_router(catalog.router)
app.include_router(companion.router)
app.include_router(comps.router)
app.include_router(craft.router)
app.include_router(events.router)
app.include_router(highscores.router)
app.include_router(loot.router)
app.include_router(lootlog.router)
app.include_router(market_history.router)
app.include_router(meta.router)
app.include_router(nodes.router)
app.include_router(players.router)
app.include_router(profiles.router)
app.include_router(regear.router)
app.include_router(render.router)
app.include_router(user_profile.router)

# SPA + OG por rota — precisa ser o ÚLTIMO registro (catch-all). Só ativa se
# frontend/dist existir (build do Vite); em dev com Vite server é no-op.
from app import spa as _spa  # noqa: E402
_spa.install(app)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "background_fetchers_disabled": get_settings().disable_background_fetchers,
    }


@app.get("/meta/event-states")
def event_states() -> dict:
    """Estados e arestas válidas — fonte única para a UI desenhar o fluxo."""
    return {
        "states": [s.value for s in EventState],
        "transitions": {
            s.value: sorted(t.value for t in allowed_targets(s))
            for s in EventState
        },
    }
