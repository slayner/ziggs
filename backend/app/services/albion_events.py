"""Cliente thin da API pública (não-oficial tolerada) de eventos/killboard do Albion.

Usada pelo reconhecimento de screenshots de regear: dado um nome de jogador
(OCR), busca o id → últimas mortes → itens exatos (tier/enchant/quality) +
timestamp real. Assim a "esquerda/direita" da screenshot deixa de importar: a
API diz quem é a vítima; o OCR só precisa achar um nome que tenha morte recente.

Hosts por região (settings.albion_guild_region):
  americas → gameinfo.albiononline.com
  europe   → gameinfo-ams.albiononline.com
  asia     → gameinfo-sgp.albiononline.com

Timeouts curtos (6s) — falha vira "manual" no fluxo de regear, não trava o bot.
# ponytail: cache in-memory 60s por nome; a API retém poucos dias, sem
# necessidade de persistir. Mediana de hosts se throughput matters → não agora.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.services.albion_gate import slot

_HOSTS = {
    "americas": "gameinfo.albiononline.com",
    "europe": "gameinfo-ams.albiononline.com",
    "asia": "gameinfo-sgp.albiononline.com",
}
_DEFAULT_HOST = _HOSTS["americas"]
_TIMEOUT = 6.0
# A API de killboard cai com frequência — algumas tentativas com backoff curto
# antes de desistir (a falha vira "manual" no fluxo de regear, e a fila de retry
# tenta de novo depois). ponytail: 3 tentativas; aumentar se a API ficar pior.
_ATTEMPTS = 3
_BACKOFFS = (1.0, 2.5)  # espera antes da 2ª e da 3ª tentativa (segundos)

# cache: (host, nome) -> (expires_ts, player_id). Pequeno, só pra não re-buscar
# o mesmo nome numa rajada de prints.
_search_cache: dict[tuple[str, str], tuple[float, str | None]] = {}
_SEARCH_TTL = 60.0


def host_for(region: str | None) -> str:
    return _HOSTS.get((region or "").lower(), _DEFAULT_HOST)


async def _get_json(url: str) -> Any:
    """GET com retry. 404 = None (jogador/evento não existe, não é erro).
    Timeout / conexão recusada / 5xx → retenta com backoff. Qualquer outra
    falha após as tentativas → relança (caller decide o fallback)."""
    last_exc: Exception | None = None
    for attempt in range(_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                async with slot():
                    resp = await client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            # 5xx e 429 valem retry; 4xx (menos 404 já tratado) não.
            code = e.response.status_code
            if code not in (429,) and not (500 <= code < 600):
                raise
            last_exc = e
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_exc = e
        if attempt < _ATTEMPTS - 1:
            await asyncio.sleep(_BACKOFFS[attempt])
    if last_exc:
        raise last_exc
    return None


async def search_player(name: str, region: str | None = None) -> str | None:
    """Nome → player_id (Albion). Cache curto."""
    host = host_for(region)
    key = (host, name.lower())
    now = time.monotonic()
    cached = _search_cache.get(key)
    if cached and cached[0] > now:
        return cached[1]
    data: Any = None
    try:
        data = await _get_json(f"https://{host}/api/gameinfo/search?q={name}")
    except Exception:
        data = None
    pid: str | None = None
    if isinstance(data, dict):
        players = data.get("players") or []
        # match exato (case-insensitive); senão o primeiro.
        for p in players:
            if str(p.get("Name", "")).lower() == name.lower():
                pid = p.get("Id")
                break
        if pid is None and players:
            pid = players[0].get("Id")
    _search_cache[key] = (now + _SEARCH_TTL, pid)
    return pid


async def recent_deaths(player_id: str, region: str | None = None) -> list[dict]:
    """Últimas ~10 mortes do jogador. Cada event: {EventId, Time, Victim, Killer}."""
    host = host_for(region)
    try:
        data = await _get_json(f"https://{host}/api/gameinfo/players/{player_id}/deaths")
    except Exception:
        return []
    if isinstance(data, list):
        return data
    return []


async def get_event(event_id: str, region: str | None = None) -> dict | None:
    host = host_for(region)
    try:
        return await _get_json(f"https://{host}/api/gameinfo/events/{event_id}")
    except Exception:
        return None


EQUIP_SLOTS = ("MainHand", "OffHand", "Head", "Armor", "Shoes", "Cape", "Mount", "Bag", "Potion", "Food")


def equipment_items(event: dict) -> list[dict]:
    """Extrai [{item_id, quality, slot}] do Victim.Equipment de um death event."""
    victim = event.get("Victim") or {}
    eq = victim.get("Equipment") or {}
    out: list[dict] = []
    for slot in EQUIP_SLOTS:
        entry = eq.get(slot)
        if not entry or not entry.get("Type"):
            continue
        out.append({
            "item_id": entry["Type"],
            "quality": int(entry.get("Quality", 1) or 1),
            "slot": slot,
        })
    return out