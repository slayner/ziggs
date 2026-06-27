"""App FastAPI — Ziggs platform."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, battles, catalog, claims, comps, events, loot, meta, players, profiles, render
from app.domain.states import EventState, allowed_targets
from app.services import battle_tracker, claim_checker, player_tracker, profile_warmer


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [
        asyncio.create_task(player_tracker.run_forever()),
        asyncio.create_task(battle_tracker.run_forever()),
        asyncio.create_task(battle_tracker.run_backfill_forever()),
        asyncio.create_task(profile_warmer.run_forever()),
        asyncio.create_task(claim_checker.run_forever()),
    ]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Ziggs API", lifespan=lifespan)

# Permite que o Vite dev server (localhost:5173) chame a API diretamente.
# Em produção o frontend e o backend ficam na mesma origem — este middleware é inofensivo.
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
