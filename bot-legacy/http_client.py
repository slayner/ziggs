"""Cliente HTTP do bot LEGADO para o backend Ziggs (mesma máquina).

Redução deliberada do bot-v2/http_client.py: sessão aiohttp singleton com
keep-alive (uma conexão reaproveitada, sem exaurir portas) + Bearer
BOT_API_SECRET. Sem offline queue/reachability — o único caller é o energylog
e a mentoria, operações manuais cujo erro é visível na hora.
"""
from __future__ import annotations

import os
import time
from urllib.parse import quote

import aiohttp

_session: aiohttp.ClientSession | None = None

# Cache nick→uid e uid→nick (resposta do /bot/registration-lookup). Uma log
# colada tem dezenas de nicks repetidos; TTL 5min segura o registro novo que
# acabou de ser feito entre uma log e outra.
_CACHE_TTL = 300.0
_cache: dict[str, tuple[float, int | None]] = {}


def _site_url() -> str:
    return os.getenv("BOT_SITE_URL", "http://127.0.0.1:8000").rstrip("/")


def _api_secret() -> str:
    return os.getenv("BOT_API_SECRET", "").strip()


def _ready() -> bool:
    return bool(_api_secret())


async def session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=10, keepalive_timeout=75),
            timeout=aiohttp.ClientTimeout(total=5),
        )
    return _session


async def close() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


async def get_json(path: str) -> dict | None:
    """GET → dict em 200, None em qualquer outro caso/erro."""
    if not _ready():
        return None
    try:
        s = await session()
        async with s.get(f"{_site_url()}{path}",
                         headers={"Authorization": f"Bearer {_api_secret()}"}) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
            print(f"✗ backend {resp.status} em {path}")
            return None
    except aiohttp.ClientError as e:
        print(f"✗ backend indisponível ({e})")
        return None


async def lookup_user_by_nick(guild_id: int | None, nick: str) -> int | None:
    """discord_user_id do registro ATIVO com esse nick no backend (ou None).
    Case-insensitive; backend devolve todos os registros que casam — usa o 1º."""
    # guild_id None = comando usado em DM — não há registro de guilda p/ consultar.
    if not _ready() or guild_id is None or not nick.strip():
        return None
    key = f"{guild_id}:{nick.lower()}"
    hit = _cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    data = await get_json(
        f"/bot/registration-lookup/{guild_id}?nick={quote(nick.strip())}")
    uid = None
    if data and data.get("registrations"):
        uid = int(data["registrations"][0]["discord_user_id"])
    _cache[key] = (time.monotonic(), uid)
    return uid


async def lookup_nick_by_user(guild_id: int | None, user_id: int) -> str | None:
    """Nick do jogo do registro ATIVO desse usuário no backend (ou None).
    Usado pela mentoria (título do post de trial)."""
    if not _ready() or guild_id is None:
        return None
    data = await get_json(
        f"/bot/registration-lookup/{guild_id}?user_id={user_id}")
    if data and data.get("registrations"):
        return data["registrations"][0]["albion_player_name"]
    return None
