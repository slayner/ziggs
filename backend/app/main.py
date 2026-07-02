"""App FastAPI — Ziggs platform."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.rate_limit import RateLimitMiddleware
from app.api.routes import auth, battles, catalog, claims, comps, events, highscores, loot, meta, players, profiles, render
from app.domain.states import EventState, allowed_targets
from app.services import (
    battle_price_reprocessor, battle_reprocessor, battle_tracker, claim_checker, player_count_snapshot,
    player_tracker, profile_warmer, registration_checker, small_battle_discovery, weapon_stats,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
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
        asyncio.create_task(small_battle_discovery.run_forever()),
        asyncio.create_task(player_count_snapshot.run_forever()),
        asyncio.create_task(battle_price_reprocessor.run_forever()),
    ]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Ziggs API", lifespan=lifespan)

# Login/callback do Discord: bem mais restrito, é a rota mais sensível (a "passagem").
app.add_middleware(RateLimitMiddleware, limit=10, window=60, prefix="/auth/discord")
# Limite geral por IP — spam genérico, sem incomodar uso normal da UI.
# /render/item é excluído: uma única página pode legitimamente disparar dezenas
# de ícones de uma vez (cache hit ou miss), isso não é abuso. Proteção própria
# dele é o semáforo de concorrência em render.py.
app.add_middleware(RateLimitMiddleware, limit=120, window=60, exclude_prefix="/render/item")

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
app.include_router(comps.router)
app.include_router(events.router)
app.include_router(highscores.router)
app.include_router(loot.router)
app.include_router(meta.router)
app.include_router(players.router)
app.include_router(profiles.router)
app.include_router(render.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


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
