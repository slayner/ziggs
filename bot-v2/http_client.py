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


def _headers() -> dict:
    secret = _api_secret()
    return {"Authorization": f"Bearer {secret}"} if secret else {}


def _ready() -> bool:
    return bool(_site_url() and _api_secret())


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
        _session = aiohttp.ClientSession(connector=connector)
    return _session


async def close() -> None:
    global _session
    if _session and not _session.closed:
        await _session.close()
    _session = None


async def _on_exception(e: Exception, *, method: str = "", path: str = "",
                        body: dict | None = None) -> None:
    """Erro de CONEXÃO (peer fechou/resetou — ClientOSError/WinError 64 no
    Windows é o caso comum quando o backend reinicia) pode deixar uma conexão
    morta pendurada no pool do TCPConnector sem o aiohttp perceber. Sem
    resetar a sessão aqui, toda chamada seguinte tentava reusar a MESMA
    conexão podre e falhava igual — mesmo com o backend saudável de novo,
    "sem resposta" persistia indefinidamente até o processo do bot reiniciar.
    Timeout simples (backend só lento) não entra aqui — não indica conexão
    podre, só descartaria uma sessão saudável à toa.

    Se method/path foram dados (escrita: POST/PATCH/DELETE), enfileira pra
    replay quando o backend voltar (ver offline_queue.drain em main.py)."""
    if isinstance(e, aiohttp.ClientConnectionError):
        await close()
        if method and path:
            import offline_queue
            offline_queue.enqueue(method, path, body)


# ── helpers (devolvem dict|None: JSON em 200, None caso contrário/exceção) ───

async def get_json(path: str, *, timeout: float = 5, tag: str = "") -> dict | None:
    if not _ready():
        return None
    try:
        async with session().get(f"{_site_url()}{path}", headers=_headers(),
                                 timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            if r.status == 200:
                return await r.json()
            if r.status == 401 and tag:
                print(f"[{tag}] 401 em GET {path} — BOT_API_SECRET do bot não bate com o backend")
            await r.read()  # drena pra liberar a conexão de volta pro pool
    except Exception as e:
        await _on_exception(e)
    return None


async def post_json(path: str, body: dict, *, timeout: float = 5, tag: str = "") -> dict | None:
    if not _ready():
        return None
    try:
        async with session().post(f"{_site_url()}{path}", json=body, headers=_headers(),
                                  timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            if r.status == 200:
                return await r.json()
            if tag:
                await _log_non200(tag, "POST", path, r)
            else:
                await r.read()
    except Exception as e:
        if tag:
            print(f"[{tag}] exceção em POST {path}: {type(e).__name__}: {e}")
        await _on_exception(e, method="POST", path=path, body=body)
    return None


async def patch_json(path: str, body: dict, *, timeout: float = 5, tag: str = "") -> dict | None:
    if not _ready():
        return None
    try:
        async with session().patch(f"{_site_url()}{path}", json=body, headers=_headers(),
                                   timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            if r.status == 200:
                return await r.json()
            if tag:
                await _log_non200(tag, "PATCH", path, r)
            else:
                await r.read()
    except Exception as e:
        if tag:
            print(f"[{tag}] exceção em PATCH {path}: {type(e).__name__}: {e}")
        await _on_exception(e, method="PATCH", path=path, body=body)
    return None


async def _log_non200(tag: str, method: str, path: str, r: aiohttp.ClientResponse) -> None:
    """Loga status + corpo truncado de uma resposta não-200 (drena o corpo)."""
    body = ""
    try:
        body = (await r.text())[:200]
    except Exception:
        await r.read()
    print(f"[{tag}] {method} {path} → {r.status}: {body}")


async def delete_json(path: str, *, timeout: float = 5) -> dict | None:
    if not _ready():
        return None
    try:
        async with session().delete(f"{_site_url()}{path}", headers=_headers(),
                                    timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            if r.status == 200:
                return await r.json()
            await r.read()
    except Exception as e:
        await _on_exception(e, method="DELETE", path=path)
    return None


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
                       timeout: float = 10) -> dict | None:
    """Sem gate de status: devolve await r.json() seja qual for o código (pra
    rotas que precisam ler o corpo mesmo em erro, ex.: /bot/register)."""
    if not _ready():
        return None
    try:
        async with session().request(method, f"{_site_url()}{path}", json=json,
                                     headers=_headers(),
                                     timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            return await r.json()
    except Exception as e:
        await _on_exception(e, method=method if method != "GET" else "",
                            path=path if method != "GET" else "",
                            body=json if method != "GET" else None)
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