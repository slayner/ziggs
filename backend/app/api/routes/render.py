"""Local render cache for Albion item icons.

Avoids hitting the Albion CDN on every icon load and keeps the site working
even if the Albion API/CDN goes down — real art is saved to disk and reused
for the same id+quality+size combination.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, unquote

import httpx
from fastapi import APIRouter, HTTPException, Response
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import AsyncSessionLocal
from app.models.renders import RenderMiss

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
_MISSING_CACHE_HEADERS = {"Cache-Control": "public, max-age=300"}
_VALID_ITEM_SIZES = (0, 64, 128)
# Snap requested sizes to the closest valid value. Some callers (craft
# calculator) request sizes like 32/48/96 — without this, they get HTTP 400
# and no image renders. The CDN only produces 64/128; anything else is a
# client-side display hint, not a different asset. Ties round UP so small
# icons (32) get 64 (a real asset) instead of 0 (full-res default).
def _snap_size(s: int) -> int:
    if s in _VALID_ITEM_SIZES:
        return s
    return min(_VALID_ITEM_SIZES, key=lambda v: (abs(v - s), -v))

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


def _mem_drop(k: str) -> None:
    global _MEM_CACHE_BYTES
    content = _MEM_CACHE.pop(k, None)
    if content is not None:
        _MEM_CACHE_BYTES -= len(content)

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


def _generate_placeholder(key: str) -> bytes | None:
    """Gera um PNG placeholder quando a CDN da Albion não tem o render do item.
    Mostra o número do tier num fundo escuro com borda colorida pela tier.
    Itens sem tier (UNIQUE_*, etc.) mostram '?' num fundo cinza."""
    import re
    from io import BytesIO
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    m = re.match(r"T(\d+)", key)
    tier = int(m.group(1)) if m else 0
    colors = {4: (0x4C, 0xAF, 0x50), 5: (0x2D, 0x9C, 0xDB), 6: (0x9C, 0x27, 0xB0),
              7: (0xFF, 0xA0, 0x00), 8: (0xFF, 0x6B, 0x35)}
    color = colors.get(tier, (0x60, 0x60, 0x68))
    size = 128
    img = Image.new("RGBA", (size, size), (0x1A, 0x1A, 0x22, 255))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=8, outline=color, width=3)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
    except Exception:
        try:
            font = ImageFont.truetype("C:\\Windows\\Fonts\\segoeuib.ttf", 48)
        except Exception:
            font = ImageFont.load_default()
    label = f"T{tier}" if tier else "?"
    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) // 2, (size - th) // 2 - bbox[1]), label, fill=color, font=font)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _missing_path(cache_path: Path) -> Path:
    return cache_path.with_name(f"{cache_path.name}.missing")


@lru_cache(maxsize=10)
def _placeholder_bytes_for_tier(tier_key: str) -> bytes | None:
    # The generated placeholder only varies by tier. Caching the variants lets
    # us recognize legacy placeholders without decoding every cached PNG.
    return _generate_placeholder(tier_key)


def _is_generated_placeholder(content: bytes, key: str) -> bool:
    tier = re.match(r"T(\d+)", key)
    placeholder = _placeholder_bytes_for_tier(f"T{tier.group(1)}" if tier else "UNIQUE")
    return placeholder is not None and content == placeholder


def _cached_real_render(cache_path: Path, key: str) -> bytes | None:
    """Read real cache bytes, excluding corrupt and generated-placeholder files."""
    if not _cache_usable(cache_path):
        return None
    try:
        content = cache_path.read_bytes()
    except OSError:
        return None
    if _is_generated_placeholder(content, key):
        _missing_path(cache_path).touch(exist_ok=True)
        _mem_drop(str(cache_path))
        return None
    return content


def _cache_has_real_render(cache_path: Path, key: str) -> bool:
    return _cached_real_render(cache_path, key) is not None


def _cached_missing_render(cache_path: Path, mkey: str) -> bool:
    """True if this render is known to be missing (marker file exists)."""
    marker = _missing_path(cache_path)
    return marker.exists()


def _cache_path(kind: str, key: str, quality: int = 0, size: int = 0) -> Path:
    if kind == "item":
        return _CACHE_DIR / f"{quote(key, safe='')}_q{quality}_s{size}.png"
    if kind == "spell":
        return _SPELL_DIR / f"{quote(key, safe='')}.png"
    raise ValueError(f"unknown render kind: {kind}")


def _request_params(kind: str, quality: int = 0, size: int = 0) -> dict[str, int]:
    if kind != "item":
        return {}
    params: dict[str, int] = {}
    if quality:
        params["quality"] = quality
    if size:
        params["size"] = size
    return params


_RETRY_DELAYS = (timedelta(hours=6), timedelta(days=1), timedelta(days=7))
_UNAVAILABLE_RETRY_DELAY = timedelta(hours=1)
_RECORDED_MISSES: set[tuple[str, str, int, int]] = set()
_last_miss_error_log = 0.0


def retry_delay(miss_count: int) -> timedelta:
    return _RETRY_DELAYS[min(max(miss_count, 1) - 1, len(_RETRY_DELAYS) - 1)]


async def _record_render_miss(kind: str, key: str, quality: int, size: int) -> None:
    """Insert once; retries themselves update the existing row in the worker."""
    global _last_miss_error_log
    identity = (kind, key, quality, size)
    if identity in _RECORDED_MISSES:
        return
    now = datetime.now(timezone.utc)
    stmt = pg_insert(RenderMiss).values(
        kind=kind,
        key=key,
        quality=quality,
        size=size,
        miss_count=1,
        last_attempt_at=now,
        next_retry_at=now + retry_delay(1),
    ).on_conflict_do_nothing()
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(stmt)
            await db.commit()
    except Exception as exc:
        now_monotonic = time.monotonic()
        if now_monotonic - _last_miss_error_log >= 60:
            _last_miss_error_log = now_monotonic
            logging.getLogger(__name__).warning(
                "não foi possível registrar render ausente: %s; tentará no próximo acesso (%s)", key, exc,
            )
    else:
        _RECORDED_MISSES.add(identity)


async def _fetch_render(
    kind: str, key: str, params: dict[str, int], fallback: str | None = None,
) -> bytes | None:
    """Fetch real render bytes, or None when Albion has no art for the key."""
    async with httpx.AsyncClient(timeout=10) as client:
        async def fetch(candidate: str) -> bytes | None:
            url = f"https://render.albiononline.com/v1/{kind}/{quote(candidate, safe='')}.png"
            resp = await client.get(url, params=params)
            if resp.status_code != 200 or not resp.content or _is_placeholder(resp.content):
                return None
            return resp.content

        content = await fetch(key)
        if content is None and fallback and fallback != key:
            return await fetch(fallback)
        return content


@router.get("/item/{key}")
async def render_item(
    key: str, quality: int = 0, size: int = 0,
) -> Response:
    if not 0 <= quality <= 5:
        raise HTTPException(400, "invalid render parameters")
    size = _snap_size(size)
    return await _cached_render(
        "item", key, _cache_path("item", key, quality, size), _request_params("item", quality, size)
    )


async def render_item_for_card(key: str, quality: int = 0, size: int = 0) -> Response:
    """Resolve a known miss once more before a permanent Discord card is made."""
    try:
        response = await render_item(key, quality, size)
    except HTTPException as e:
        if e.status_code != 404:
            raise
        outcome = await recover_render_miss("item", key, quality, size)
        if outcome is True:
            return await render_item(key, quality, size)
        if outcome is None:
            raise HTTPException(502, "Albion render unavailable")
        raise
    return response


# Spell icon, for the companion damage meter. No quality/size: the spell CDN
# does not accept those params.
@router.get("/spell/{key}")
async def render_spell(key: str) -> Response:
    # `fallback` = English name of the skill. New/reworked skills key art by
    # NAME, not uniquename; a sub-spell then returns the white placeholder by
    # id and resolves through the name.
    return await _cached_render(
        "spell", key, _cache_path("spell", key), {},
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
    if _cached_missing_render(cache_path, mkey):
        await _record_render_miss(kind, key, params.get("quality", 0), params.get("size", 0))
        raise HTTPException(404, "render not found")
    hot = _mem_get(mkey)
    if hot is not None:  # hot icon: served from RAM, no stat or disk
        return Response(content=hot, media_type="image/png", headers=_CACHE_HEADERS)
    cached = _cached_real_render(cache_path, key)
    if cached is not None:
        _mem_put(mkey, cached)
        return Response(content=cached, media_type="image/png", headers=_CACHE_HEADERS)

    try:
        async with key_lock:
            # Fetch and write share the lock so a cold key has one CDN request.
            if _cached_missing_render(cache_path, mkey):
                await _record_render_miss(kind, key, params.get("quality", 0), params.get("size", 0))
                raise HTTPException(404, "render not found")
            hot = _mem_get(mkey)
            if hot is not None:
                return Response(content=hot, media_type="image/png", headers=_CACHE_HEADERS)
            cached = _cached_real_render(cache_path, key)
            if cached is not None:
                _mem_put(mkey, cached)
                return Response(content=cached, media_type="image/png", headers=_CACHE_HEADERS)
            async with _FETCH_SEM:
                content = await _fetch_render(kind, key, params, fallback)
            if content is None:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                _missing_path(cache_path).touch(exist_ok=True)
                _mem_drop(mkey)
                await _record_render_miss(kind, key, params.get("quality", 0), params.get("size", 0))
                raise HTTPException(404, "render not found")

            # Written under the original key even when it came from spell fallback.
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(content)
            _missing_path(cache_path).unlink(missing_ok=True)
            _RECORDED_MISSES.discard((kind, key, params.get("quality", 0), params.get("size", 0)))
            _mem_drop(mkey)
            _mem_put(mkey, content)
            return Response(content=content, media_type="image/png", headers=_CACHE_HEADERS)
    except httpx.HTTPError:
        raise HTTPException(502, "Albion render unavailable")


async def recover_render_miss(kind: str, key: str, quality: int, size: int) -> bool | None:
    """Retry one queued miss: true=recovered, false=still absent, none=CDN error."""
    try:
        cache_path = _cache_path(kind, key, quality, size)
    except ValueError:
        return True  # Discard malformed legacy rows instead of retrying forever.
    mkey = str(cache_path)
    key_lock = _KEY_LOCKS[hash(mkey) % len(_KEY_LOCKS)]
    fallback = _spell_display_names().get(key) if kind == "spell" else None
    async with key_lock:
        missing = _cached_missing_render(cache_path, mkey)
        if not missing and _cache_has_real_render(cache_path, key):
            _missing_path(cache_path).unlink(missing_ok=True)
            _mem_drop(mkey)
            return True
        try:
            async with _FETCH_SEM:
                content = await _fetch_render(kind, key, _request_params(kind, quality, size), fallback)
        except httpx.HTTPError:
            return None
        if content is None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            _missing_path(cache_path).touch(exist_ok=True)
            return False
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(content)
        _missing_path(cache_path).unlink(missing_ok=True)
        _RECORDED_MISSES.discard((kind, key, quality, size))
        _mem_drop(mkey)
        _mem_put(mkey, content)
        return True


_ITEM_CACHE_FILE = re.compile(r"^(.*)_q(\d+)_s(\d+)\.png$")


def discover_cached_render_misses(cache_dir: Path = _CACHE_DIR) -> list[tuple[str, str, int, int]]:
    """Mark old generated placeholders so deployments also heal past misses.
    Legacy placeholder PNGs are deleted so the CDN gets re-tried on next access."""
    misses: set[tuple[str, str, int, int]] = set()
    for cache_path in cache_dir.glob("*.png"):
        match = _ITEM_CACHE_FILE.match(cache_path.name)
        if not match:
            continue
        key = unquote(match.group(1))
        identity = ("item", key, int(match.group(2)), int(match.group(3)))
        marker = _missing_path(cache_path)
        if marker.exists():
            misses.add(identity)
        else:
            try:
                content = cache_path.read_bytes()
            except OSError:
                continue
            if _is_generated_placeholder(content, key):
                marker.touch(exist_ok=True)
                cache_path.unlink(missing_ok=True)
                _mem_drop(str(cache_path))
                misses.add(identity)
    return sorted(misses)


# --- Pre-warm: baixa ícones que faltam no cache de disco ----------------------
# Roda como task no lifespan. Lê item_names.json, checa quais IDs não têm PNG
# no disco, e baixa com rate limit conservador (sem sobrecarregar a CDN).
# Uma vez que o cache está quente, o worker é praticamente no-op.

_PRERENDER_INTERVAL = 3600  # 1h entre ciclos completos
_PRERENDER_BATCH = 50       # ícones por ciclo (pouco a pouco, sem flood)
_PRERENDER_DELAY = 0.3      # segundos entre cada fetch

_ITEM_NAMES_FILE = Path(__file__).resolve().parents[3] / "data" / "item_names.json"


async def run_prerender_forever() -> None:
    """Pré-aquece o cache de ícones baixando renders que faltam da CDN da Albion."""
    log = logging.getLogger("render.prerender")
    await asyncio.sleep(30)  # deixa o startup terminar primeiro
    while True:
        try:
            if not _ITEM_NAMES_FILE.exists():
                log.info("item_names.json ausente — pulando ciclo")
            else:
                data = json.loads(_ITEM_NAMES_FILE.read_text(encoding="utf-8"))
                all_ids = list(data.keys()) if isinstance(data, dict) else []
                missing = []
                for item_id in all_ids:
                    # só checa qualidade 0, tamanho 0 (o default do site)
                    cache_path = _CACHE_DIR / f"{quote(item_id, safe='')}_q0_s0.png"
                    if _cached_missing_render(cache_path, str(cache_path)) is None and not _cache_has_real_render(cache_path, item_id):
                        missing.append(item_id)
                if missing:
                    batch = missing[:_PRERENDER_BATCH]
                    log.info("pré-aquecendo %d/%d ícones restantes", len(batch), len(missing))
                    fetched = 0
                    for item_id in batch:
                        cache_path = _CACHE_DIR / f"{quote(item_id, safe='')}_q0_s0.png"
                        try:
                            await _cached_render("item", item_id, cache_path, {})
                            if _missing_path(cache_path).exists():
                                continue
                            if _cache_has_real_render(cache_path, item_id):
                                fetched += 1
                        except Exception:
                            pass  # erro individual não aborta o batch
                        await asyncio.sleep(_PRERENDER_DELAY)
                    log.info("pré-aquecimento: %d ícones baixados", fetched)
                else:
                    log.info("cache completo — %d ícones", len(all_ids))
        except Exception as e:
            log.warning("erro no ciclo de pré-aquecimento: %s", e)
        await asyncio.sleep(_PRERENDER_INTERVAL)
