"""
Rate limit por IP, em memória, janela fixa. Sem dependência nova — o app roda
num único processo (SQLite dev / Postgres simples), não precisa de Redis pra isso.

ponytail: dict global sem expurgo — cresce com o nº de IPs distintos vistos
no processo. Ok pra escala atual (plataforma de guilda, não API pública de
massa); trocar por algo com TTL/Redis se isso virar tráfego anônimo alto.
"""
from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_buckets: dict[tuple[str, "tuple[str, ...] | None", str], tuple[float, int]] = {}

# ponytail: prefixos nunca limitados, EM NENHUMA instância deste middleware —
# nem depende de exclude_prefix. /bot/ é chamado pelo bot-v2 de dezenas de
# servidores Discord várias vezes por minuto (GET + POST); qualquer teto por
# IP estouraria e engoliria respostas do bot em silêncio. Garantia dura: mesmo
# se amanhã alguém add um RateLimitMiddleware novo e esquecer o exclude_prefix,
# /bot/ segue livre. /render/item idem (uma página dispara dezenas de ícones).
ALWAYS_UNLIMITED_PREFIXES: tuple[str, ...] = ("/bot/", "/render/item")


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, limit: int, window: float, prefix: str = "",
                 exclude_prefix: str | tuple[str, ...] = "", methods: tuple[str, ...] | None = None):
        super().__init__(app)
        self.limit = limit
        self.window = window
        self.prefix = prefix
        self.exclude_prefix = exclude_prefix
        # None = todo método. Usado pra separar orçamento de leitura (barato,
        # é a maioria do tráfego de navegação normal numa SPA) do de escrita
        # (onde abuso de verdade — spam de forms, script batendo em POST —
        # concentra) em vez de um balde único pros dois.
        self.methods = methods

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Garantia dura: /bot/ e /render/item nunca contam, mesmo se a instância
        # não tiver passado exclude_prefix.
        if path.startswith(ALWAYS_UNLIMITED_PREFIXES):
            return await call_next(request)
        if self.prefix and not path.startswith(self.prefix):
            return await call_next(request)
        if self.exclude_prefix and path.startswith(self.exclude_prefix):
            return await call_next(request)
        if self.methods and request.method not in self.methods:
            return await call_next(request)

        ip = request.client.host if request.client else "unknown"
        key = (self.prefix, self.methods, ip)
        now = time.monotonic()
        window_start, count = _buckets.get(key, (now, 0))
        if now - window_start > self.window:
            window_start, count = now, 0
        count += 1
        _buckets[key] = (window_start, count)

        if count > self.limit:
            return JSONResponse({"detail": "muitas requisições, tente de novo em breve"}, status_code=429)
        return await call_next(request)


if __name__ == "__main__":
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.testclient import TestClient

    async def ok(request):
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/x", ok, methods=["GET", "POST"]),
                            Route("/render/item/y", ok), Route("/bot/z", ok)])
    app.add_middleware(RateLimitMiddleware, limit=3, window=60, exclude_prefix=("/render/item", "/bot/"))
    client = TestClient(app)

    statuses = [client.get("/x").status_code for _ in range(5)]
    assert statuses == [200, 200, 200, 429, 429], statuses
    render_statuses = [client.get("/render/item/y").status_code for _ in range(5)]
    assert render_statuses == [200] * 5, render_statuses
    bot_statuses = [client.get("/bot/z").status_code for _ in range(5)]
    assert bot_statuses == [200] * 5, bot_statuses
    print("rate_limit OK", statuses, render_statuses, bot_statuses)

    # methods=(...): orçamento de leitura/escrita separado — estourar um não
    # deve afetar o outro.
    app2 = Starlette(routes=[Route("/x", ok, methods=["GET", "POST"])])
    app2.add_middleware(RateLimitMiddleware, limit=2, window=60, methods=("POST",))
    app2.add_middleware(RateLimitMiddleware, limit=4, window=60, methods=("GET", "HEAD"))
    client2 = TestClient(app2)

    get_statuses = [client2.get("/x").status_code for _ in range(6)]
    assert get_statuses == [200, 200, 200, 200, 429, 429], get_statuses
    # GET já estourou o próprio teto (4), mas POST tem balde separado — ainda livre.
    post_statuses = [client2.post("/x").status_code for _ in range(3)]
    assert post_statuses == [200, 200, 429], post_statuses
    print("rate_limit methods-split OK", get_statuses, post_statuses)

    # Garantia dura: instância SEM exclude_prefix ainda não pode limitar /bot/
    # (nem /render/item) — protege contra esquecer o exclude no futuro.
    _buckets.clear()  # zera state de testes anteriores no mesmo processo
    app3 = Starlette(routes=[Route("/bot/x", ok, methods=["GET", "POST"]),
                             Route("/y", ok, methods=["GET"])])
    app3.add_middleware(RateLimitMiddleware, limit=1, window=60)  # sem exclude_prefix
    c3 = TestClient(app3)
    bot_unlim = [c3.get("/bot/x").status_code for _ in range(10)]
    assert bot_unlim == [200] * 10, bot_unlim
    y_lim = [c3.get("/y").status_code for _ in range(3)]
    assert y_lim == [200, 429, 429], y_lim
    print("rate_limit /bot/ always-unlimited OK", bot_unlim, y_lim)
