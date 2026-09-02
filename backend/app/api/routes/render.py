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
            # A CDN sinaliza arte inexistente com HTTP 200 + placeholder. Um
            # erro HTTP é transitório: marcá-lo como ausência esconderia um
            # item válido até o próximo reteste longo.
            if resp.status_code != 200:
                resp.raise_for_status()
            if not resp.content or _is_placeholder(resp.content):
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
        "item", key, _cache_path("item", key, quality, size),
        _request_params("item", quality, size),
        fallback=_item_render_fallback(key),
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
    fallback = _spell_display_names().get(key) if kind == "spell" else (
        _item_render_fallback(key) if kind == "item" else None
    )
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
# Roda como task no lifespan. Lê item_names.json + items.txt, checa quais IDs
# não têm PNG no disco, e baixa com rate limit conservador (sem sobrecarregar a
# CDN). Uma vez que o cache está quente, o worker é praticamente no-op.
#
# Cobertura:
#   - Equipamentos do catálogo × qualidades 0-5 × tamanhos 0+64+128
#   - Todos os UniqueNames de item_names.json × qualidades 0-5 × tamanhos 0+64+128
#   - Crystal weapons pelo nome EN (a CDN não os serve por UniqueName)
#   - .missing markers são re-testados a cada ciclo (itens novos do jogo)

_PRERENDER_START_DELAY = 60         # deixa ingestão e bot estabilizarem após restart
_PRERENDER_INTERVAL = 120           # varredura de recuperação sem esperar horas entre lotes
_PRERENDER_BATCH = 200              # corrige misses antigos em poucos ciclos, sem rajada na CDN
_PRERENDER_CONCURRENCY = 2          # seis dos oito slots seguem livres para requests reais
_PRERENDER_DELAY = 0.2              # espaça chamadas à CDN externa
_PRERENDER_MISSING_RETEST = 86400  # re-testa .missing a cada 24h

_ITEM_NAMES_FILE = Path(__file__).resolve().parents[3] / "data" / "item_names.json"
_ITEMS_TXT_FILE = Path(__file__).resolve().parents[3] / "data" / "ao-bin-dump" / "items.txt"
_CATALOG_FILE = Path(__file__).resolve().parents[4] / "frontend" / "public" / "data" / "catalog.json"
_PRERENDER_QUALITIES = range(6)
_PRERENDER_SIZES = (128, 64, 0)

# Crystal weapons: UniqueName → (EN name base, tier). O render CDN só os serve
# pelo nome EN ("Elder's Infinity Blade@2"), não pelo UniqueName.
_CRYSTAL_TIER_PREFIX = {4: "Adept's", 5: "Expert's", 6: "Master's",
                         7: "Grandmaster's", 8: "Elder's"}

# Crystal weapon base IDs (do albion-items.ts artAll). A CDN só os serve
# pelo nome EN, não pelo UniqueName. Hard-coded para garantir cobertura
# mesmo sem items.txt ou item_names.json com essas entradas.
_CRYSTAL_WEAPON_BASES: dict[str, str] = {
    "MAIN_SWORD": "Infinity Blade",
    "2H_GLAIVE": "Rift Glaive",
    "2H_ARCANESTAFF": "Astral Staff",
    "MAIN_ARCANESTAFF": "Arcane Staff",
    "MAIN_CURSEDSTAFF": "Rotcaller Staff",
    "2H_FROSTSTAFF": "Arctic Staff",
    "MAIN_FROSTSTAFF": "Frost Staff",
    "2H_FIRESTAFF": "Great Fire Staff",
    "MAIN_FIRESTAFF": "Flamewalker Staff",
    "2H_HOLYSTAFF": "Exalted Staff",
    "MAIN_HOLYSTAFF": "Holy Staff",
    "MAIN_NATURESTAFF": "Forgebark Staff",
    "2H_NATURESTAFF": "Great Nature Staff",
    "2H_DOUBLEBLADEDSTAFF": "Phantom Twinblade",
    "2H_BOW": "Skystrider Bow",
    "2H_DUALCROSSBOW": "Arclight Blasters",
    "2H_DAGGERPAIR": "Twin Slayers",
    "2H_SCYTHE": "Crystal Reaper",
    "2H_HAMMER": "Truebolt Hammer",
    "MAIN_MACE": "Dreadstorm Monarch",
    "2H_KNUCKLES": "Forcepulse Bracers",
    "2H_SHAPESHIFTER": "Stillgaze Staff",
    "OFF_SHIELD": "Unbreakable Ward",
    "OFF_TOME": "Timelocked Grimoire",
    "OFF_TORCH": "Blueflame Torch",
}

# Dragon Leather: a CDN serve pelo nome EN, não pelo UniqueName (igual crystal).
_DRAGON_LEATHER_BASES: dict[str, str] = {
    "HEAD_LEATHER_DRAGON": "Dragonslayer Hood",
    "ARMOR_LEATHER_DRAGON": "Dragonslayer Jacket",
    "SHOES_LEATHER_DRAGON": "Dragonslayer Shoes",
}


def _load_items_txt() -> dict[str, str]:
    """Lê items.txt (UniqueName: EN name) — fonte que cobre crystal weapons."""
    if not _ITEMS_TXT_FILE.exists():
        return {}
    out: dict[str, str] = {}
    for line in _ITEMS_TXT_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        uid, _, en_name = line.partition(":")
        uid = uid.strip()
        en_name = en_name.strip()
        if uid and en_name:
            out[uid] = en_name
    return out


def _crystal_en_name(uid: str, items_txt: dict[str, str]) -> str | None:
    """Converte UniqueName de crystal weapon em nome EN para o render CDN.
    T4_ARTEFACT_MAIN_SWORD_CRYSTAL → "Adept's Infinity Blade"
    (O items.txt tem "Adept's Infinite Crystal" — NÃO é o nome do render.
    O nome do render vem do albion-items.ts: crystalRenderName.)
    """
    m = re.match(r"T(\d+)_ARTEFACT_(.+)_CRYSTAL(@\d+)?$", uid)
    if not m:
        return None
    tier = int(m.group(1))
    prefix = _CRYSTAL_TIER_PREFIX.get(tier, "Elder's")
    base = m.group(2)
    ench = m.group(3) or ""
    name_en = _CRYSTAL_WEAPON_BASES.get(base)
    if not name_en:
        return None
    return f"{prefix} {name_en}{ench}"


def _catalog_equipment_uids() -> list[str]:
    """Lista variações de equipamento que a UI realmente pode solicitar."""
    try:
        data = json.loads(_CATALOG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for family in data:
        if not isinstance(family, dict) or family.get("kind") != "equipment":
            continue
        for variation in family.get("variations") or []:
            if isinstance(variation, dict) and isinstance(variation.get("uniqueName"), str):
                out.append(variation["uniqueName"])
    return out


def _dragon_en_name(uid: str) -> str | None:
    """Converte UniqueName de Dragon Leather em nome EN para o render CDN.
    T4_HEAD_LEATHER_DRAGON@2 → "Adept's Dragonslayer Hood@2"
    """
    m = re.match(r"T(\d+)_(.+?)(@\d+)?$", uid)
    if not m:
        return None
    base = m.group(2)
    if base not in _DRAGON_LEATHER_BASES:
        return None
    tier = int(m.group(1))
    prefix = _CRYSTAL_TIER_PREFIX.get(tier, "Elder's")
    ench = m.group(3) or ""
    return f"{prefix} {_DRAGON_LEATHER_BASES[base]}{ench}"


@lru_cache(maxsize=1)
def _item_en_names() -> dict[str, str]:
    """UniqueName → EN name (de item_names.json). Itens novos da Albion às
    vezes só têm render pelo nome EN, não pelo UniqueName — este mapeamento
    é o fallback genérico para esses casos (igual a spell_names.json para
    spells)."""
    try:
        data = json.loads(_ITEM_NAMES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data


def _item_en_fallback(uid: str) -> str | None:
    """Converte UniqueName em nome EN para o render CDN, via item_names.json.
    T8_MEAL_SPECIAL_FOOD_DRAKE_EGG → "Drake Egg Biscuits"
    T4_BAG@1 → "Adept's Bag@1"
    Retorna None se o UID não estiver mapeado."""
    names = _item_en_names()
    if not names:
        return None
    base, _, ench = uid.rpartition("@")
    if not base:
        base, ench = uid, ""
    en = names.get(base)
    if not en:
        return None
    en = re.sub(r"@0$", "", en)
    if ench and ench != "0":
        en = f"{en}@{ench}"
    return en or None


def _item_render_fallback(uid: str) -> str | None:
    """Fallback de nome EN para o render CDN de itens que não são crystal
    weapons nem Dragon Leather (estes têm path dedicado e nomes diferentes
    no item_names.json — o nome do artefato, não do render)."""
    if _crystal_en_name(uid, {}) or _dragon_en_name(uid):
        return None
    return _item_en_fallback(uid)


def _item_render_key(uid: str) -> str:
    """Converte crystal weapons e Dragon Leather para o nome EN usado pela CDN."""
    return _crystal_en_name(uid, {}) or _dragon_en_name(uid) or uid


def _build_prerender_queue() -> list[tuple[str, int, int]]:
    """Monta a lista de (key, quality, size) para pré-renderizar.
    Equipamentos do catálogo têm prioridade. Inclui UniqueNames de item_names
    e crystal weapons por nome EN.

    Ordem de prioridade:
    1. Equipamentos que a UI expõe, inclusive encantamentos raros
    2. Crystal weapons por nome EN (a CDN não os serve por UniqueName)
    3. Itens gerais por UniqueName
    Dentro de cada grupo: size=128, 64 e 0.
    """
    seen: set[tuple[str, int, int]] = set()
    equipment_queue: list[tuple[str, int, int]] = []
    crystal_queue: list[tuple[str, int, int]] = []
    normal_queue: list[tuple[str, int, int]] = []

    def add(target: list[tuple[str, int, int]], key: str) -> None:
        for quality in _PRERENDER_QUALITIES:
            for size in _PRERENDER_SIZES:
                identity = (key, quality, size)
                if identity not in seen:
                    seen.add(identity)
                    target.append(identity)

    names_data = {}
    if _ITEM_NAMES_FILE.exists():
        try:
            names_data = json.loads(_ITEM_NAMES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    items_txt = _load_items_txt()

    # --- Equipamentos da UI, antes de qualquer item genérico ---
    for uid in _catalog_equipment_uids():
        add(equipment_queue, _item_render_key(uid))

    # --- Crystal weapons por nome EN, inclusive variações fora do catálogo ---
    # Gera diretamente da lista hard-coded: T4-T8 × 25 base IDs × @0-@4.
    for tier in (4, 5, 6, 7, 8):
        prefix = _CRYSTAL_TIER_PREFIX[tier]
        for base, name_en in _CRYSTAL_WEAPON_BASES.items():
            for ench in range(5):
                suffix = f"@{ench}" if ench else ""
                add(crystal_queue, f"{prefix} {name_en}{suffix}")

    # Also check items.txt and item_names.json for crystal UIDs (fallback)
    for uid in items_txt:
        en_name = _crystal_en_name(uid, items_txt)
        if en_name:
            for ench in range(5):
                suffix = f"@{ench}" if ench else ""
                add(crystal_queue, re.sub(r"@\d+$", "", en_name) + suffix)

    for uid in (names_data.keys() if isinstance(names_data, dict) else []):
        en_name = _crystal_en_name(uid, {})
        if en_name:
            for ench in range(5):
                suffix = f"@{ench}" if ench else ""
                add(crystal_queue, re.sub(r"@\d+$", "", en_name) + suffix)

    # --- Dragon Leather por nome EN (mesma doutrina dos crystal weapons) ---
    for tier in (4, 5, 6, 7, 8):
        prefix = _CRYSTAL_TIER_PREFIX[tier]
        for base, name_en in _DRAGON_LEATHER_BASES.items():
            for ench in range(5):
                suffix = f"@{ench}" if ench else ""
                add(crystal_queue, f"{prefix} {name_en}{suffix}")

    # --- Itens normais por UniqueName ---
    for uid in (names_data.keys() if isinstance(names_data, dict) else []):
        add(normal_queue, _item_render_key(uid))

    return equipment_queue + crystal_queue + normal_queue


def _prerender_cache_has_real_render(cache_path: Path, key: str) -> bool:
    """Versão sem cache em memória, segura para a varredura em thread."""
    if not _cache_usable(cache_path):
        return False
    try:
        content = cache_path.read_bytes()
    except OSError:
        return False
    return not _is_generated_placeholder(content, key)


def _find_prerender_missing(
    queue: list[tuple[str, int, int]], *, retry_missing: bool,
) -> tuple[list[tuple[str, int, int]], int]:
    """Varre o disco fora do event loop antes de iniciar o próximo lote."""
    missing: list[tuple[str, int, int]] = []
    retried_missing: list[tuple[str, int, int]] = []
    for key, q, s in queue:
        cp = _cache_path("item", key, q, s)
        marker = _missing_path(cp)
        if not marker.exists() and not _prerender_cache_has_real_render(cp, key):
            missing.append((key, q, s))

    retried = 0
    if retry_missing:
        for key, q, s in queue:
            cp = _cache_path("item", key, q, s)
            marker = _missing_path(cp)
            if marker.exists():
                marker.unlink(missing_ok=True)
                if not _prerender_cache_has_real_render(cp, key):
                    retried_missing.append((key, q, s))
                retried += 1
    # Falhas da execução anterior vêm primeiro: podem ser renders válidos que
    # receberam 5xx transitório da CDN durante a varredura anterior.
    return retried_missing + missing, retried


async def _prerender_one(key: str, quality: int, size: int, log: logging.Logger) -> bool:
    """Baixa um render se faltar no cache. Retorna True se baixou."""
    cache_path = _cache_path("item", key, quality, size)
    if _cache_has_real_render(cache_path, key):
        return False
    try:
        await _cached_render("item", key, cache_path, _request_params("item", quality, size),
                            fallback=_item_render_fallback(key))
        return _cache_has_real_render(cache_path, key)
    except Exception:
        return False


async def run_prerender_forever() -> None:
    """Pré-aquece o cache de ícones baixando renders que faltam da CDN da Albion.

    Cobertura completa: todos os itens × qualidades 0-5 × tamanhos 0+128,
    incluindo crystal weapons por nome EN. Batch concorrente para catch-up
    rápido. Re-testa .missing markers a cada 24h (itens novos do jogo)."""
    log = logging.getLogger("render.prerender")
    log.setLevel(logging.INFO)
    await asyncio.sleep(_PRERENDER_START_DELAY)
    last_missing_retest = 0.0

    while True:
        try:
            queue = _build_prerender_queue()
            if not queue:
                log.info("nenhum item para pré-aquecer")
            else:
                # Re-testa .missing markers a cada 24h
                now_mono = time.monotonic()
                retry_missing = now_mono - last_missing_retest > _PRERENDER_MISSING_RETEST
                if retry_missing:
                    last_missing_retest = now_mono
                missing, retried = await asyncio.to_thread(
                    _find_prerender_missing, queue, retry_missing=retry_missing,
                )
                if retried:
                    log.info("re-testando %d .missing markers", retried)

                if missing:
                    batch = missing[:_PRERENDER_BATCH]
                    log.info("pré-aquecendo %d/%d ícones restantes", len(batch), len(missing))
                    fetched = 0
                    for i in range(0, len(batch), _PRERENDER_CONCURRENCY):
                        chunk = batch[i:i + _PRERENDER_CONCURRENCY]
                        tasks = [_prerender_one(k, q, s, log) for k, q, s in chunk]
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                        fetched += sum(1 for r in results if r is True)
                        await asyncio.sleep(_PRERENDER_DELAY)
                    log.info("pré-aquecimento: %d ícones baixados", fetched)
                    await asyncio.sleep(_PRERENDER_INTERVAL)
                else:
                    log.info("cache completo — %d combinações", len(queue))
                    await asyncio.sleep(3600)
        except Exception as e:
            log.warning("erro no ciclo de pré-aquecimento: %s", e)
            await asyncio.sleep(_PRERENDER_INTERVAL)
