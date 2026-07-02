"""Cache local dos renders de item da Albion.

Pedido original do projeto: evitar bater na CDN da Albion a cada
carregamento de ícone e manter o site funcionando mesmo se a API/CDN da
Albion cair — uma vez baixado, o PNG fica salvo pra sempre em disco e
nunca mais é buscado de novo pra essa mesma combinação id+quality+size.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

router = APIRouter(prefix="/render", tags=["render"])

_CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "render_cache" / "items"

# ponytail: fila simples — limita quantos fetches concorrentes batem na CDN da
# Albion de uma vez (uma página com cache frio pode pedir 100+ ícones juntos).
# O resto espera a vez em vez de disparar tudo em paralelo. Sobe pra Redis/worker
# dedicado só se isso virar múltiplos processos.
_FETCH_SEM = asyncio.Semaphore(8)

# IDs da Albion (T5_HEAD_PLATE_SET1@2) + nomes em inglês usados pras crystal
# weapons (Elder's Astral Staff@3) — único formato que esse endpoint precisa aceitar.
_SAFE_KEY = re.compile(r"^[\w@.\-' ]+$")

_CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


@router.get("/item/{key}")
async def render_item(key: str, quality: int = 0, size: int = 0) -> Response:
    if not key or len(key) > 200 or not _SAFE_KEY.match(key):
        raise HTTPException(400, "chave de item inválida")

    cache_path = _CACHE_DIR / f"{quote(key, safe='')}_q{quality}_s{size}.png"
    if cache_path.exists():
        return FileResponse(cache_path, media_type="image/png", headers=_CACHE_HEADERS)

    params: dict[str, int] = {}
    if quality:
        params["quality"] = quality
    if size:
        params["size"] = size

    url = f"https://render.albiononline.com/v1/item/{quote(key, safe='')}.png"
    try:
        async with _FETCH_SEM:
            # outra request pode ter preenchido o cache enquanto esperava a vez
            if cache_path.exists():
                return FileResponse(cache_path, media_type="image/png", headers=_CACHE_HEADERS)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, params=params)
    except httpx.HTTPError:
        raise HTTPException(502, "render da Albion indisponível")

    if resp.status_code != 200 or not resp.content:
        raise HTTPException(resp.status_code if resp.status_code != 200 else 502, "render não encontrado")

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(resp.content)
    return Response(content=resp.content, media_type="image/png", headers=_CACHE_HEADERS)
