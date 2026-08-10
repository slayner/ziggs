"""Local render cache for Albion item icons.

Avoids hitting the Albion CDN on every icon load and keeps the site working
even if the Albion API/CDN goes down — once downloaded, the PNG is saved to
disk forever and never fetched again for the same id+quality+size combination.
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

# ponytail: simple queue — limits how many concurrent fetches hit the Albion
# CDN at once (a cold-cache page can request 100+ icons together). Others wait
# their turn instead of all firing in parallel. Move to Redis/dedicated worker
# only if this ever runs across multiple processes.
_FETCH_SEM = asyncio.Semaphore(8)
# Requests for the same icon share the first fetch; different keys still use
# the eight slots above. Striped locks avoid a per-URL dict that grows forever.
_KEY_LOCKS = tuple(asyncio.Lock() for _ in range(64))

# Albion IDs (T5_HEAD_PLATE_SET1@2) plus English names used for crystal weapons
# (Elder's Astral Staff@3) — the only formats this endpoint needs to accept.
_SAFE_KEY = re.compile(r"^[\w@.\-' ]+$")

_CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}

# In-memory cache for HOT icons. The browser caches each icon once (immutable),
# but different users repeat the same common icons (meta gear, weapons) — and
# each serve used to do stat() + disk read. Here the bytes live in RAM: a hot
# hit serves without touching disk. LRU by BYTE BUDGET (icons range from ~2KB
# to ~120KB). asyncio single-thread: get/put have no await in the middle, so
# they're atomic in the event loop — no lock (same logic as albion_gate pools).
_MEM_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
_MEM_CACHE_BYTES = 0
_MEM_CACHE_MAX_BYTES = 64 * 1024 * 1024  # 64 MB — fits the hot set comfortably


def _mem_get(k: str) -> bytes | None:
    v = _MEM_CACHE.get(k)
    if v is not None:
        _MEM_CACHE.move_to_end(k)  # recently used goes to the end (LRU)
    return v


def _mem_put(k: str, content: bytes) -> None:
    global _MEM_CACHE_BYTES
    if len(content) > _MEM_CACHE_MAX_BYTES:
        return  # larger than the whole cache — don't store
    if k in _MEM_CACHE:
        _MEM_CACHE_BYTES -= len(_MEM_CACHE[k])
    _MEM_CACHE[k] = content
    _MEM_CACHE.move_to_end(k)
    _MEM_CACHE_BYTES += len(content)
    while _MEM_CACHE_BYTES > _MEM_CACHE_MAX_BYTES:
        _, evicted = _MEM_CACHE.popitem(last=False)  # remove least recently used
        _MEM_CACHE_BYTES -= len(evicted)

# The spell CDN never returns 404. For an id with no art it returns 200 with
# one of:
#   - ~281 bytes: empty PNG;
#   - 26178 bytes, always this sha1: a shared white placeholder —
#     the "totally white render" that showed up in the damage meter.
# Caching either would write junk forever, so we treat them as not-found and
# try the by-name fallback.
_PLACEHOLDER_SHA1 = "7b910616c1bf680bc6de514a37e21724976b75ad"
_PLACEHOLDER_BYTES = 26178  # size of that file — cheap cache check
_MIN_RENDER_BYTES = 1024


def _cache_usable(path: Path) -> bool:
    """Is the cache valid? Placeholders written by an OLDER proxy version are
    deleted.

    Previously only the empty PNG (~281 B) was rejected, so the 26178 B white
    frame ended up on disk and would be served forever. We only read and hash
    files of that suspicious size; size alone does not identify the placeholder.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < _MIN_RENDER_BYTES:
        path.unlink(missing_ok=True)
        return False
    if size == _PLACEHOLDER_BYTES:
        try:
            placeholder = hashlib.sha1(path.read_bytes()).hexdigest() == _PLACEHOLDER_SHA1
        except OSError:
            return False
        if placeholder:
            path.unlink(missing_ok=True)
            return False
    return True

_SPELLS_FILE = Path(__file__).resolve().parents[3] / "data" / "spell_names.json"


@lru_cache(maxsize=1)
def _spell_display_names() -> dict[str, str]:
    """uniquename → English name.

    At some point Albion started keying new/reworked skill art by NAME
    (`/spell/Powerful%20Swing.png`) instead of uniquename. A sub-spell like
    HAMMER_SHOVE_SWING_EFFECT falls back to the white placeholder by id, but
    the name resolves to the correct art — hence the fallback.
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
    # File name kept as it always was — changing it would invalidate the cache
    # already downloaded to disk.
    return await _cached_render(
        "item", key, _CACHE_DIR / f"{quote(key, safe='')}_q{quality}_s{size}.png", params
    )


# Spell icon, for the companion damage meter. No quality/size: the spell CDN
# does not accept those params.
@router.get("/spell/{key}")
async def render_spell(key: str) -> Response:
    # `fallback` = English name of the skill. New/reworked skills key art by
    # NAME, not uniquename; a sub-spell then returns the white placeholder by
    # id and resolves through the name.
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
        raise HTTPException(400, "invalid key")

    mkey = str(cache_path)
    key_lock = _KEY_LOCKS[hash(mkey) % len(_KEY_LOCKS)]
    hot = _mem_get(mkey)
    if hot is not None:  # hot icon: served from RAM, no stat or disk
        return Response(content=hot, media_type="image/png", headers=_CACHE_HEADERS)

    if _cache_usable(cache_path):
        content = cache_path.read_bytes()
        _mem_put(mkey, content)  # warm it for next time
        return Response(content=content, media_type="image/png", headers=_CACHE_HEADERS)

    async def fetch(k: str) -> bytes | None:
        """Render bytes, or None if Albion has no art for this key."""
        url = f"https://render.albiononline.com/v1/{kind}/{quote(k, safe='')}.png"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
        if resp.status_code != 200 or not resp.content or _is_placeholder(resp.content):
            return None
        return resp.content

    try:
        async with key_lock:
            # another request may have filled the cache (RAM or disk) while we
            # were waiting our turn
            hot = _mem_get(mkey)
            if hot is not None:
                return Response(content=hot, media_type="image/png", headers=_CACHE_HEADERS)
            if _cache_usable(cache_path):
                content = cache_path.read_bytes()
                _mem_put(mkey, content)
                return Response(content=content, media_type="image/png", headers=_CACHE_HEADERS)
            async with _FETCH_SEM:
                content = await fetch(key)
                if content is None and fallback and fallback != key:
                    content = await fetch(fallback)
    except httpx.HTTPError:
        raise HTTPException(502, "Albion render unavailable")

    if content is None:
        # Don't cache: no art today may have art in the next patch, and the
        # client `onError` already hides the image.
        raise HTTPException(404, "render not found")

    # Written under the ORIGINAL key even when it came from the fallback —
    # requesters always ask by uniquename.
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(content)
    _mem_put(mkey, content)
    return Response(content=content, media_type="image/png", headers=_CACHE_HEADERS)
