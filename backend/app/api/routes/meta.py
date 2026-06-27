"""Dados gerais (não-tenant) usados na dashboard.

Patch notes via Steam News API: o changelog oficial (albiononline.com) está
atrás de Cloudflare e o robots.txt do site proíbe bots de IA explicitamente —
não dá pra fazer scraping dali. A Steam News é a fonte acessível mais
próxima, mas só anuncia patches já em LIVE (sem fase de teste) e não tem um
número de versão estruturado — usamos o texto livre do título mesmo assim
(pedido explícito, ver conversa).
"""
from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/meta", tags=["meta"])

_ALBION_STEAM_APPID = 761890
_CACHE_TTL = 3600.0
_cache: list[dict] | None = None
_cache_at = 0.0


def _parse_changelog(items: list[dict]) -> list[dict]:
    out = []
    for n in items:
        title = (n.get("title") or "").strip()
        if not title.lower().startswith("changelog:"):
            continue
        out.append({
            "title": title.split(":", 1)[1].strip(),
            "url": n.get("url"),
            "date": n.get("date"),
        })
    return out


@router.get("/patch-notes")
async def patch_notes() -> list[dict]:
    global _cache, _cache_at
    if _cache is not None and time.time() - _cache_at < _CACHE_TTL:
        return _cache

    url = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v0002/"
    params = {"appid": _ALBION_STEAM_APPID, "count": 1000, "format": "json"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
        resp.raise_for_status()
        items = resp.json()["appnews"]["newsitems"]
    except (httpx.HTTPError, KeyError, ValueError):
        if _cache is not None:
            return _cache
        raise HTTPException(502, "changelog indisponível")

    _cache = _parse_changelog(items)
    _cache_at = time.time()
    return _cache
