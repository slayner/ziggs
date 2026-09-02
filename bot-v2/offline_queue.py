"""Fila in-memory de escritas bot→backend que falharam por erro de CONEXÃO
(backend fora do ar, rede caiu, peer resetou). Quando o backend volta, o
watchdog em main.py chama drain() pra re-enviar tudo na ordem.

 ponytail: in-memory, não persiste entre restarts do bot — se o processo
 cai com itens na fila, eles se perdem. Persistir seria YAGNI: a janela
 típica de downtime do backend é segundos-minutos; se o bot também caiu,
 o catch-up do on_ready (refresh_massinfo, sync_guild, etc) reconstroi o
 estado anyway. A fila cobre o caso comum: backend caiu sozinho, bot
 continua vivo, escritas do período ficam retidas até ele voltar.

 Só enfileira POST/PATCH/DELETE — reads (GET) não fazem sentido replayar
 (dados estariam stale), e post_best_effort é explicitamente fire-and-forget.

 Idempotência: a fila só enfileira em ClientConnectionError (request NÃO
 chegou ao backend), nunca em timeout (request pode ter chegado). Isso
 reduz o risco de double-apply, mas não elimina — se a conexão cair depois
 do servidor receber mas antes da resposta, o replay duplica. Rotas não-
 idempotentes (ex.: adicionar dinheiro) precisam de dedup no backend.
"""
from __future__ import annotations

import time
from collections import deque

_MAX = 500       # ponytail: teto — backlog maior descarta o mais velho
_MAX_AGE = 3600  # 1h — itens mais velhos são descartados no enqueue

_q: deque[tuple[str, str, dict | None, float]] = deque()


def enqueue(method: str, path: str, body: dict | None = None) -> None:
    now = time.monotonic()
    while _q and now - _q[0][3] > _MAX_AGE:
        _q.popleft()
    _q.append((method, path, body, now))
    while len(_q) > _MAX:
        _q.popleft()


def pending() -> int:
    return len(_q)


async def drain() -> int:
    """Re-envia pendentes na ordem. Para no primeiro erro de CONEXÃO (backend
    caiu de novo) — os restantes ficam na fila. Erros HTTP (4xx/5xx) NÃO
    param: a requisição foi processada, replayar não ajudaria."""
    import aiohttp
    import http_client
    if not http_client._ready():
        _q.clear()
        return 0
    sent = 0
    while _q:
        method, path, body, _ts = _q[0]
        try:
            async with http_client.session().request(
                method,
                f"{http_client._site_url()}{path}",
                json=body,
                headers=http_client._headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                await r.read()
            _q.popleft()
            sent += 1
        except aiohttp.ClientConnectionError:
            break  # backend ainda fora — item fica na fila
        except Exception:
            _q.popleft()  # timeout/parse/etc — descarta, replay não ajuda
    return sent