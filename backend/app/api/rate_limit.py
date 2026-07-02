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

_buckets: dict[tuple[str, str], tuple[float, int]] = {}


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, limit: int, window: float, prefix: str = "", exclude_prefix: str = ""):
        super().__init__(app)
        self.limit = limit
        self.window = window
        self.prefix = prefix
        self.exclude_prefix = exclude_prefix

    async def dispatch(self, request: Request, call_next):
        if self.prefix and not request.url.path.startswith(self.prefix):
            return await call_next(request)
        if self.exclude_prefix and request.url.path.startswith(self.exclude_prefix):
            return await call_next(request)

        ip = request.client.host if request.client else "unknown"
        key = (self.prefix, ip)
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

    app = Starlette(routes=[Route("/x", ok), Route("/render/item/y", ok)])
    app.add_middleware(RateLimitMiddleware, limit=3, window=60, exclude_prefix="/render/item")
    client = TestClient(app)

    statuses = [client.get("/x").status_code for _ in range(5)]
    assert statuses == [200, 200, 200, 429, 429], statuses
    render_statuses = [client.get("/render/item/y").status_code for _ in range(5)]
    assert render_statuses == [200] * 5, render_statuses
    print("rate_limit OK", statuses, render_statuses)
