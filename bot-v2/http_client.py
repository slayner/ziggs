"""Cliente HTTP compartilhado bot→backend.

UMA `aiohttp.ClientSession` singleton com `TCPConnector` persistente (keep-alive)
— reusa conexões TCP pra localhost em vez de abrir handshake novo por request.

O padrão anterior (`async with aiohttp.ClientSession() as s:` em ~30 call sites,
um por chamada) não reusava conexão nenhuma: cada request abria e fechava um
socket contra localhost:8000. No Windows/loopback isso acumula sockets em
TIME_WAIT e exaure portas efêmeras — os connects novos gargalam (o bot loga
"sem resposta", o backend não recebe nada) até portas liberarem, quando um
burst de requests engasgados chega de uma vez. Cicla: normal → gargalo → burst.

A sessão singleton mantém um pool com keep-alive: a mesma conexão TCP serve
dezenas de requests, zerando o churn de sockets. `enable_cleanup_closed=True`
limpa conexões meio-fechadas pelo servidor (importante no Windows).
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

import aiohttp

# ponytail: lidas sob demanda (não capturadas em constante de módulo) —
# main.py faz `import http_client` ANTES de `load_dotenv()` rodar (import de
# módulo executa na hora, load_dotenv() só na linha seguinte). Uma constante
# `SITE_URL = os.getenv(...)` aqui capturava string vazia PRA SEMPRE (nunca
# relida depois), deixando _ready() permanentemente False pelo resto do
# processo — get_json/post_json etc. retornavam None sem nem tentar a rede,
# independente do backend estar de pé ou não. Funções pequenas em vez de
# constante: sempre leem o valor atual, imunes a ordem de import.
def _site_url() -> str:
    return os.getenv("BOT_SITE_URL", "").rstrip("/")


def _api_secret() -> str:
    return os.getenv("BOT_API_SECRET", "")


_session: Optional[aiohttp.ClientSession] = None


class BackendUnavailable(RuntimeError):
    """A chamada não alcançou o backend (config, conexão ou timeout)."""


def _ensure_ready(raise_on_unavailable: bool) -> bool:
    if _ready():
        return True
    if raise_on_unavailable:
        raise BackendUnavailable("backend não configurado")
    return False


def _is_transport_error(error: Exception) -> bool:
    return isinstance(error, (aiohttp.ClientConnectionError, asyncio.TimeoutError))


def _headers() -> dict:
    secret = _api_secret()
    return {"Authorization": f"Bearer {secret}"} if secret else {}


def _ready() -> bool:
    return bool(_site_url() and _api_secret())


# ── reachability: log de TRANSIÇÃO, não por-poll ─────────────────────────────
# Antes, cada poll (regear/lootlog/embed-work × N guildas, a cada 10s) descobria
# a queda do backend por conta própria e gritava "sem resposta" a cada tick — um
# único blip (restart do backend, ou request estourando o timeout num burst de
# socket no loopback do Windows, ver docstring do módulo) virava 3×N linhas por
# tick, repetindo a cada 10s. Aqui mora o ÚNICO ponto por onde todo request
# passa: rastreia o estado e loga só a transição (caiu / voltou). _fail_streak
# evita flap na beira do timeout — só declara "caiu" após N falhas seguidas.
_reachable = True
_fail_streak = 0
_DOWN_AFTER = 3  # ponytail: ~3 polls falhos antes de gritar "caiu"; frouxe se flapar


def is_backend_reachable() -> bool:
    return _reachable


def _note_reachable() -> None:
    """Backend respondeu (qualquer status → transporte OK)."""
    global _reachable, _fail_streak
    _fail_streak = 0
    if not _reachable:
        _reachable = True
        print("✓ backend acessível de novo")


def _note_unreachable() -> None:
    """Erro de transporte (peer resetou/timeout) — só loga ao cruzar o limiar."""
    global _reachable, _fail_streak
    _fail_streak += 1
    if _reachable and _fail_streak >= _DOWN_AFTER:
        _reachable = False
        print("✗ backend inacessível — silenciando avisos por-poll até voltar")


def _make_trace() -> aiohttp.TraceConfig:
    """Hook nativo do aiohttp: reachability no nível da sessão em vez de
    instrumentar cada helper. on_request_end = respondeu; on_request_exception =
    erro de transporte."""
    trace = aiohttp.TraceConfig()

    async def _end(_s, _ctx, _p) -> None:
        _note_reachable()

    async def _exc(_s, _ctx, _p) -> None:
        _note_unreachable()

    trace.on_request_end.append(_end)
    trace.on_request_exception.append(_exc)
    return trace


def session() -> aiohttp.ClientSession:
    """Singleton lazy — só é criada no primeiro request (loop já rodando)."""
    global _session
    if _session is None or _session.closed:
        connector = aiohttp.TCPConnector(
            limit=100,           # total de conexões simultâneas
            limit_per_host=32,   # por host (localhost:8000)
            keepalive_timeout=75,
            force_close=False,   # keep-alive ligado (o ponto central do fix)
            enable_cleanup_closed=True,
        )
        _session = aiohttp.ClientSession(connector=connector, trace_configs=[_make_trace()])
    return _session


async def close() -> None:
    global _session
    if _session and not _session.closed:
        await _session.close()
    _session = None


async def _on_exception(e: Exception) -> None:
    """Erro de CONEXÃO (peer fechou/resetou — ClientOSError/WinError 64 no
    Windows é o caso comum quando o backend reinicia) pode deixar uma conexão
    morta pendurada no pool do TCPConnector sem o aiohttp perceber. Sem
    resetar a sessão aqui, toda chamada seguinte tentava reusar a MESMA
    conexão podre e falhava igual — mesmo com o backend saudável de novo,
    "sem resposta" persistia indefinidamente até o processo do bot reiniciar.
    Timeout simples (backend só lento) não entra aqui — não indica conexão
    podre, só descartaria uma sessão saudável à toa.

    O caller decide se uma escrita entra na fila offline. Comandos que precisam
    devolver um resultado ao usuário não podem ser aplicados depois de terem
    mostrado "falhou"."""
    if isinstance(e, aiohttp.ClientConnectionError):
        await close()


# ── helpers (devolvem dict|None: JSON em 200, None caso contrário/exceção) ───

async def get_json(
    path: str, *, timeout: float = 5, tag: str = "",
    raise_on_unavailable: bool = False, retry_for: float = 0,
) -> dict | None:
    if not _ensure_ready(raise_on_unavailable):
        return None
    deadline = asyncio.get_running_loop().time() + retry_for
    while True:
        retry = False
        try:
            async with session().get(f"{_site_url()}{path}", headers=_headers(),
                                     timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                if r.status == 200:
                    return await r.json()
                retry = r.status in (429, 502, 503, 504)
                if r.status == 401 and tag:
                    print(f"[{tag}] 401 em GET {path} — BOT_API_SECRET do bot não bate com o backend")
                await r.read()
        except Exception as e:
            await _on_exception(e)
            retry = _is_transport_error(e)
            if not retry:
                return None
        if not retry or asyncio.get_running_loop().time() >= deadline:
            if raise_on_unavailable:
                raise BackendUnavailable(f"GET {path} indisponível")
            return None
        await asyncio.sleep(min(2, max(0, deadline - asyncio.get_running_loop().time())))


async def get_bytes(path: str, *, timeout: float = 20, tag: str = "") -> bytes | None:
    if not _ready():
        return None
    try:
        async with session().get(
            f"{_site_url()}{path}", headers=_headers(),
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as r:
            if r.status == 200:
                return await r.read()
            if tag:
                await _log_non200(tag, "GET", path, r)
            else:
                await r.read()
    except Exception as e:
        await _on_exception(e)
    return None


async def _write_json(
    method: str, path: str, body: dict | None, *, timeout: float,
    tag: str, attempts: int, queue_on_failure: bool,
    raise_on_unavailable: bool,
) -> dict | None:
    if not _ensure_ready(raise_on_unavailable):
        return None
    attempts = max(1, attempts)
    for attempt in range(attempts):
        try:
            async with session().request(
                method, f"{_site_url()}{path}", json=body, headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as r:
                if r.status == 200:
                    return await r.json()
                if tag:
                    await _log_non200(tag, method, path, r)
                else:
                    await r.read()
                return None
        except Exception as e:
            if tag:
                print(f"[{tag}] exceção em {method} {path}: {type(e).__name__}: {e}")
            await _on_exception(e)
            # O pool pode entregar uma conexão keep-alive morta. Reabrir e
            # repetir resolve esse caso; timeout não é repetido porque o
            # backend pode ter concluído a escrita sem devolver a resposta.
            if isinstance(e, aiohttp.ClientConnectionError) and attempt + 1 < attempts:
                await asyncio.sleep(0.2)
                continue
            if queue_on_failure and isinstance(e, aiohttp.ClientConnectionError):
                import offline_queue
                offline_queue.enqueue(method, path, body)
            if raise_on_unavailable and _is_transport_error(e):
                raise BackendUnavailable(str(e)) from e
            return None
    return None


async def post_json(
    path: str, body: dict, *, timeout: float = 5, tag: str = "",
    attempts: int = 1, queue_on_failure: bool = True,
    raise_on_unavailable: bool = False,
) -> dict | None:
    return await _write_json(
        "POST", path, body, timeout=timeout, tag=tag,
        attempts=attempts, queue_on_failure=queue_on_failure,
        raise_on_unavailable=raise_on_unavailable,
    )


async def patch_json(
    path: str, body: dict, *, timeout: float = 5, tag: str = "",
    attempts: int = 1, queue_on_failure: bool = True,
    raise_on_unavailable: bool = False,
) -> dict | None:
    return await _write_json(
        "PATCH", path, body, timeout=timeout, tag=tag,
        attempts=attempts, queue_on_failure=queue_on_failure,
        raise_on_unavailable=raise_on_unavailable,
    )


async def _log_non200(tag: str, method: str, path: str, r: aiohttp.ClientResponse) -> None:
    """Loga status + corpo truncado de uma resposta não-200 (drena o corpo)."""
    body = ""
    try:
        body = (await r.text())[:200]
    except Exception:
        await r.read()
    print(f"[{tag}] {method} {path} → {r.status}: {body}")


async def delete_json(
    path: str, *, timeout: float = 5, tag: str = "",
    attempts: int = 1, queue_on_failure: bool = True,
    raise_on_unavailable: bool = False,
) -> dict | None:
    return await _write_json(
        "DELETE", path, None, timeout=timeout, tag=tag,
        attempts=attempts, queue_on_failure=queue_on_failure,
        raise_on_unavailable=raise_on_unavailable,
    )


async def post_form(path: str, form: aiohttp.FormData, *, timeout: float = 20,
                    tag: str = "http") -> dict | None:
    """POST multipart. 200 → JSON (ou {} se o corpo não for JSON); senão loga
    status+body e devolve None. Exceções são logadas com path.

    NÃO enfileira: FormData pode conter streams/arquivos que não são
    re-enviáveis depois de consumidos. Fire-and-forget como post_best_effort."""
    if not _ready():
        return None
    try:
        async with session().post(f"{_site_url()}{path}", data=form, headers=_headers(),
                                  timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            if r.status == 200:
                try:
                    return await r.json()
                except Exception:
                    return {}
            body = ""
            try:
                body = (await r.text())[:300]
            except Exception:
                pass
            print(f"[{tag}] POST {path} falhou: HTTP {r.status} body={body!r}")
    except Exception as e:
        print(f"[{tag}] POST {path} exceção: {type(e).__name__}: {e}")
        await _on_exception(e)
    return None


async def request_json(method: str, path: str, *, json: dict | None = None,
                       timeout: float = 10, attempts: int = 1,
                       queue_on_failure: bool = True) -> dict | None:
    """Sem gate de status: devolve await r.json() seja qual for o código (pra
    rotas que precisam ler o corpo mesmo em erro, ex.: /bot/register)."""
    if not _ready():
        return None
    attempts = max(1, attempts)
    for attempt in range(attempts):
        try:
            async with session().request(method, f"{_site_url()}{path}", json=json,
                                         headers=_headers(),
                                         timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                return await r.json()
        except Exception as e:
            await _on_exception(e)
            if isinstance(e, aiohttp.ClientConnectionError) and attempt + 1 < attempts:
                await asyncio.sleep(0.2)
                continue
            if queue_on_failure and method != "GET" and isinstance(e, aiohttp.ClientConnectionError):
                import offline_queue
                offline_queue.enqueue(method, path, json)
            return None
    return None


async def post_best_effort(path: str, body: dict | None = None, *, timeout: float = 5) -> None:
    """POST fogo-e-esquece: só drena a resposta. Pra heartbeats e hooks de
    saída onde o resultado não importa. NÃO enfileira (explicitamente
    fire-and-forget — heartbeat re-envia a cada 5min de qualquer forma)."""
    if not _ready():
        return
    try:
        async with session().post(f"{_site_url()}{path}", json=body or {}, headers=_headers(),
                                   timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            await r.read()
    except Exception as e:
        await _on_exception(e)


if __name__ == "__main__":
    # self-check da máquina de reachability: fica quieta abaixo do limiar,
    # loga a queda UMA vez, segue quieta caída, loga a volta UMA vez.
    import io, contextlib
    _reachable, _fail_streak = True, 0
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        _note_unreachable(); _note_unreachable()          # 2 < DOWN_AFTER: quieto
        assert _reachable, "não devia declarar caído antes do limiar"
        _note_unreachable()                                # 3ª falha: declara caiu
        assert not _reachable
        _note_unreachable()                                # segue caído: sem nova linha
        _note_reachable()                                  # volta
        assert _reachable
    _out = _buf.getvalue()
    assert _out.count("inacessível") == 1, _out
    assert _out.count("acessível de novo") == 1, _out
    print("http_client self-check ok:", repr(_out))
