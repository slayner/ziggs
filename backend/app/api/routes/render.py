"""Cache local dos renders de item da Albion.

Pedido original do projeto: evitar bater na CDN da Albion a cada
carregamento de ícone e manter o site funcionando mesmo se a API/CDN da
Albion cair — uma vez baixado, o PNG fica salvo pra sempre em disco e
nunca mais é buscado de novo pra essa mesma combinação id+quality+size.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Response

router = APIRouter(prefix="/render", tags=["render"])

_RENDER_DIR = Path(__file__).resolve().parents[3] / "data" / "render_cache"
_CACHE_DIR = _RENDER_DIR / "items"
_SPELL_DIR = _RENDER_DIR / "spells"

# ponytail: fila simples — limita quantos fetches concorrentes batem na CDN da
# Albion de uma vez (uma página com cache frio pode pedir 100+ ícones juntos).
# O resto espera a vez em vez de disparar tudo em paralelo. Sobe pra Redis/worker
# dedicado só se isso virar múltiplos processos.
_FETCH_SEM = asyncio.Semaphore(8)

# IDs da Albion (T5_HEAD_PLATE_SET1@2) + nomes em inglês usados pras crystal
# weapons (Elder's Astral Staff@3) — único formato que esse endpoint precisa aceitar.
_SAFE_KEY = re.compile(r"^[\w@.\-' ]+$")

_CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}

# Cache em memória dos ícones QUENTES. O browser cacheia cada ícone 1× (immutable),
# mas usuários diferentes repetem os mesmos ícones comuns (gear de meta, armas) —
# e cada serve fazia stat() + leitura de disco. Aqui os bytes ficam em RAM: hit
# quente serve sem tocar o disco. LRU por ORÇAMENTO DE BYTES (ícones variam de
# ~2KB a ~120KB). asyncio single-thread: get/put não têm await no meio, então são
# atômicos no event loop — sem lock (mesma lógica dos pools do albion_gate).
_MEM_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
_MEM_CACHE_BYTES = 0
_MEM_CACHE_MAX_BYTES = 64 * 1024 * 1024  # 64 MB — cabe folgado o conjunto quente


def _mem_get(k: str) -> bytes | None:
    v = _MEM_CACHE.get(k)
    if v is not None:
        _MEM_CACHE.move_to_end(k)  # recém-usado vai pro fim (LRU)
    return v


def _mem_put(k: str, content: bytes) -> None:
    global _MEM_CACHE_BYTES
    if len(content) > _MEM_CACHE_MAX_BYTES:
        return  # maior que o cache inteiro — não guarda
    if k in _MEM_CACHE:
        _MEM_CACHE_BYTES -= len(_MEM_CACHE[k])
    _MEM_CACHE[k] = content
    _MEM_CACHE.move_to_end(k)
    _MEM_CACHE_BYTES += len(content)
    while _MEM_CACHE_BYTES > _MEM_CACHE_MAX_BYTES:
        _, evicted = _MEM_CACHE.popitem(last=False)  # remove o menos-usado
        _MEM_CACHE_BYTES -= len(evicted)

# A CDN de spell NUNCA dá 404. Pra id sem arte ela devolve 200 com uma destas:
#   - ~281 bytes: PNG vazio;
#   - 26178 bytes, sempre este sha1: um placeholder branco compartilhado —
#     é o "render totalmente branco" que aparecia no damage meter.
# Cachear qualquer um dos dois gravaria lixo pra sempre, então tratamos como
# não-encontrado e tentamos o fallback por nome.
_PLACEHOLDER_SHA1 = "7b910616c1bf680bc6de514a37e21724976b75ad"
_PLACEHOLDER_BYTES = 26178  # tamanho do mesmo arquivo — checagem barata no cache
_MIN_RENDER_BYTES = 1024


def _cache_usable(path: Path) -> bool:
    """Cache válido? Placeholder gravado por versão ANTIGA do proxy é apagado.

    Antes daqui só o PNG vazio (~281 B) era rejeitado, então a moldura branca de
    26178 B foi parar em disco e continuaria sendo servida pra sempre. Comparar
    o TAMANHO evita ler e hashear o arquivo a cada request.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < _MIN_RENDER_BYTES or size == _PLACEHOLDER_BYTES:
        path.unlink(missing_ok=True)
        return False
    return True

_SPELLS_FILE = Path(__file__).resolve().parents[3] / "data" / "spell_names.json"


@lru_cache(maxsize=1)
def _spell_display_names() -> dict[str, str]:
    """uniquename → nome em inglês.

    Em algum momento a Albion passou a chavear a arte de skill nova/reworkada
    pelo NOME (`/spell/Powerful%20Swing.png`) em vez do uniquename. Sub-feitiço
    como HAMMER_SHOVE_SWING_EFFECT cai no placeholder branco pelo id, mas o
    nome resolve na arte certa — daí o fallback.
    """
    try:
        data = json.loads(_SPELLS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {s["id"]: s["name"] for s in data if s.get("id") and s.get("name")}


def _is_placeholder(content: bytes) -> bool:
    return (
        len(content) < _MIN_RENDER_BYTES
        or hashlib.sha1(content).hexdigest() == _PLACEHOLDER_SHA1
    )


@router.get("/item/{key}")
async def render_item(key: str, quality: int = 0, size: int = 0) -> Response:
    params: dict[str, int] = {}
    if quality:
        params["quality"] = quality
    if size:
        params["size"] = size
    # Nome do arquivo mantido como sempre foi — mudar invalidaria o cache já
    # baixado em disco.
    return await _cached_render(
        "item", key, _CACHE_DIR / f"{quote(key, safe='')}_q{quality}_s{size}.png", params
    )


# Ícone de feitiço, pro damage meter do companion. Sem quality/size: o CDN de
# spell não aceita esses params.
@router.get("/spell/{key}")
async def render_spell(key: str) -> Response:
    # `fallback` = nome em inglês da skill. Skill nova/reworkada tem a arte
    # chaveada pelo NOME, não pelo uniquename; sub-feitiço então volta
    # placeholder branco pelo id e resolve pelo nome.
    return await _cached_render(
        "spell", key, _SPELL_DIR / f"{quote(key, safe='')}.png", {},
        fallback=_spell_display_names().get(key),
    )


async def _cached_render(
    kind: str,
    key: str,
    cache_path: Path,
    params: dict[str, int],
    fallback: str | None = None,
) -> Response:
    if not key or len(key) > 200 or not _SAFE_KEY.match(key):
        raise HTTPException(400, "chave inválida")

    mkey = str(cache_path)
    hot = _mem_get(mkey)
    if hot is not None:  # ícone quente: serve da RAM, sem stat nem disco
        return Response(content=hot, media_type="image/png", headers=_CACHE_HEADERS)

    if _cache_usable(cache_path):
        content = cache_path.read_bytes()
        _mem_put(mkey, content)  # esquenta pra próxima
        return Response(content=content, media_type="image/png", headers=_CACHE_HEADERS)

    async def fetch(k: str) -> bytes | None:
        """Bytes do render, ou None se a Albion não tem arte pra essa chave."""
        url = f"https://render.albiononline.com/v1/{kind}/{quote(k, safe='')}.png"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
        if resp.status_code != 200 or not resp.content or _is_placeholder(resp.content):
            return None
        return resp.content

    try:
        async with _FETCH_SEM:
            # outra request pode ter preenchido o cache (RAM ou disco) enquanto
            # esperava a vez
            hot = _mem_get(mkey)
            if hot is not None:
                return Response(content=hot, media_type="image/png", headers=_CACHE_HEADERS)
            if _cache_usable(cache_path):
                content = cache_path.read_bytes()
                _mem_put(mkey, content)
                return Response(content=content, media_type="image/png", headers=_CACHE_HEADERS)
            content = await fetch(key)
            if content is None and fallback and fallback != key:
                content = await fetch(fallback)
    except httpx.HTTPError:
        raise HTTPException(502, "render da Albion indisponível")

    if content is None:
        # Nada de cachear: sem arte hoje pode ter arte no próximo patch, e o
        # `onError` do cliente já esconde a imagem.
        raise HTTPException(404, "render não encontrado")

    # Gravado sob a chave ORIGINAL mesmo quando veio pelo fallback — quem pede
    # é sempre pelo uniquename.
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(content)
    _mem_put(mkey, content)
    return Response(content=content, media_type="image/png", headers=_CACHE_HEADERS)
