"""App FastAPI — Ziggs platform."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

# Configura o logger root ANTES de qualquer import de serviços — senão os
# `log = logging.getLogger(__name__)` no módulo capturam handlers errados.
# Level INFO mostra: "profile_warmer: iniciando", "warm: nomeando X (Y)",
# "battle_sweeper: ciclo — N candidatos, M achados", etc — o feed de "o que
# está acontecendo" que você vê no terminal do backend. DEBUG seria spam
# demais (1 linha por kill evento, por exemplo); INFO é o ponto doce.
# Override via env LOG_LEVEL pra DEBUG sem mexer no código.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


# Windows asyncio proactor: ConnectionResetError (WinError 10054) é spam
# inofensivo — httpx fechou o socket que o remote já derrubou. Sem filtro
# afoga o log e esconde o sinal real (db locked, LENTO, etc).
class _ConnResetFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info and isinstance(record.exc_info[1], ConnectionResetError):
            return False
        return True

logging.getLogger("asyncio").addFilter(_ConnResetFilter())

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.rate_limit import RateLimitMiddleware
from app.api.routes import auth, battles, catalog, claims, companion, comps, craft, events, highscores, loot, lootlog, market_history, meta, nodes, players, profiles, regear, render, scan, user_profile
from app.config import get_settings
from app.domain.states import EventState, allowed_targets
from app.services import (
    battle_price_reprocessor, battle_reprocessor, battle_sweeper, battle_tracker, claim_checker, companion_scan, companion_kill_scan, dashboard_cache,
    gold_price, highscores_cache, kill_sweeper, market_snapshot, player_count_snapshot, player_tracker, profile_warmer, registration_checker, regear_retry,
    scan_dispatcher, search_index, silver_dropped, small_battle_discovery, weapon_stats,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if get_settings().disable_background_fetchers:
        print("⚠️  DISABLE_BACKGROUND_FETCHERS=true — fetchers de background desligados.")
        tasks: list[asyncio.Task] = []
    else:
        import os as _os
        _feed_off = _os.getenv("DISABLE_FEED_FETCHERS", "").lower() in ("1", "true", "yes")
        if _feed_off:
            print("⚠️  DISABLE_FEED_FETCHERS=true — feed polling delegado às VPS workers.")
        tasks = [
            # Feed polling — delegado às VPS workers quando DISABLE_FEED_FETCHERS=true
            *([] if _feed_off else [
                asyncio.create_task(player_tracker.run_forever()),
                asyncio.create_task(player_tracker.run_backfill_forever()),
                asyncio.create_task(battle_tracker.run_forever()),
                asyncio.create_task(battle_tracker.run_backfill_forever()),
                asyncio.create_task(battle_tracker.run_retry_stuck_forever()),
                asyncio.create_task(battle_sweeper.run_forever()),
                asyncio.create_task(kill_sweeper.run_forever()),
                asyncio.create_task(small_battle_discovery.run_forever()),
            ]),
            asyncio.create_task(profile_warmer.run_forever()),
            asyncio.create_task(profile_warmer.run_refresh_forever()),
            asyncio.create_task(claim_checker.run_forever()),
            asyncio.create_task(registration_checker.run_forever()),
            asyncio.create_task(weapon_stats.run_forever()),
            asyncio.create_task(battle_reprocessor.run_forever()),
            asyncio.create_task(companion_scan.run_forever()),
            asyncio.create_task(companion_kill_scan.run_forever()),
            asyncio.create_task(scan_dispatcher.run_forever()),
            asyncio.create_task(player_count_snapshot.run_forever()),
            asyncio.create_task(battle_price_reprocessor.run_forever()),
            asyncio.create_task(silver_dropped.run_forever()),
            asyncio.create_task(regear_retry.run_forever()),
            asyncio.create_task(dashboard_cache.run_forever()),
            asyncio.create_task(highscores_cache.run_forever()),
            asyncio.create_task(gold_price.run_forever()),
            asyncio.create_task(market_snapshot.run_forever()),
        ]
    yield
    for t in tasks:
        t.cancel()


async def _wal_checkpoint_loop() -> None:
    """NOOP — mantido só pra não quebrar referências antigas. SQLite foi
    removido; PostgreSQL não precisa de checkpoint manual."""
    await asyncio.sleep(3600)


app = FastAPI(title="Ziggs API", lifespan=lifespan)



@app.exception_handler(RequestValidationError)
async def _log_validation_errors(request: Request, exc: RequestValidationError):
    # 422 com detalhes: FastAPI só loga o status code, não os campos. Loga o
    # body que chegou + os errors pra ver exatamente o que o client mandou de
    # errado (companion scan/report, prices/submit, etc).
    body = await request.body() if request.method in ("POST", "PUT", "PATCH") else b""
    logging.getLogger("app.validation").warning(
        "422 %s %s — errors=%s body=%s",
        request.method, request.url.path, exc.errors(),
        body[:500].decode("utf-8", errors="replace"),
    )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})

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
                    exclude_prefix=("/render/item", "/bot/", "/companion/", "/scan/"))
app.add_middleware(RateLimitMiddleware, limit=90, window=60, methods=("POST", "PUT", "PATCH", "DELETE"),
                    exclude_prefix=("/render/item", "/bot/", "/companion/", "/scan/"))

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
app.include_router(scan.router)
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
app.include_router(user_profile.bot_router)


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


# SPA + OG por rota — precisa ser o ÚLTIMO registro (catch-all). Só ativa se
# frontend/dist existir (build do Vite); em dev com Vite server é no-op.
from app import spa as _spa  # noqa: E402
_spa.install(app)
