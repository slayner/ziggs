"""Albion season calendar — detected from the ao-data dumps
(times*.json), not from guessed season lengths.

A ranking season is REGIONAL and half-open: ``[start[N], start[N+1])`` — it
includes the full offseason/preseason after N and avoids overlap at the
start of N+1. Americas/Europe start at 11:00 UTC, Asia at 00:00 UTC, so the
starts DIFFER per region and bounds are resolved per region.

Runtime: fetch the 3 ao-data files, in-memory cache (1h TTL), retain the last
valid one on error. Bundled snapshot (``season_calendar_snapshot.json``)
covers offline and guarantees Season 33 — derived from
``referencia/ao-bin-dumps-master/times*.json``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

REGIONS = ("americas", "europe", "asia")
# times.json = americas (11:00 UTC); times_europe.json = europe (11:00 UTC);
# times_asia.json = asia (00:00 UTC). Match player_tracker.HOSTS.
_TIMES_URL = {
    "americas": "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/times.json",
    "europe": "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/times_europe.json",
    "asia": "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/times_asia.json",
}
_FETCH_TIMEOUT = 8.0
_TTL = 3600.0  # 1h

_LOCK: asyncio.Lock | None = None
_cache: dict[str, dict[int, datetime]] | None = None
_cache_at: float = 0.0  # 0 = never had a successful fetch → try on first call


def _get_lock() -> asyncio.Lock:
    global _LOCK
    if _LOCK is None:
        _LOCK = asyncio.Lock()
    return _LOCK


def parse_season_starts(times_json: dict) -> dict[int, datetime]:
    """Extract ``GVG_SEASON_<N>_START`` → ``{N: start (aware UTC)}``.

    Only the STARTs matter: the upper bound of N is the START of N+1 (not
    the END of N), because the bucket includes the entire offseason."""
    out: dict[int, datetime] = {}
    for entry in (times_json.get("Times", {}) or {}).get("DateTime", []) or []:
        name = entry.get("@uniquename", "")
        raw = entry.get("@defaultdatetime")
        if not name.startswith("GVG_SEASON_") or not name.endswith("_START") or not raw:
            continue
        try:
            n = int(name[len("GVG_SEASON_"):-len("_START")])
        except ValueError:
            continue
        out[n] = _parse_dt(raw)
    return out


def _parse_dt(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _load_bundled() -> dict[str, dict[int, datetime]]:
    path = Path(__file__).parent / "season_calendar_snapshot.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        region: {int(n): _parse_dt(iso) for n, iso in seasons.items()}
        for region, seasons in raw.items()
    }


# Snapshot last-known-good — derived from referencia/ao-bin-dumps-master/.
# Guarantees Season 33 offline and serves as the cache's initial fallback.
BUNDLED_CALENDAR: dict[str, dict[int, datetime]] = _load_bundled()


def season_bounds(
    calendar: dict[str, dict[int, datetime]], region: str, season_num: int,
) -> tuple[datetime | None, datetime | None]:
    """``[start[season], start[season+1])`` — upper None if the next season
    hasn't been announced yet (most recent, open season)."""
    starts = calendar.get(region, {})
    return starts.get(season_num), starts.get(season_num + 1)


def current_season(
    calendar: dict[str, dict[int, datetime]], region: str, now: datetime,
) -> int | None:
    """Largest season with ``start <= now`` for that region (the ongoing one).
    Resolved per region, so it covers the short transition where Asia and
    Americas/Europe are in different seasons without attributing wrong bounds."""
    starts = calendar.get(region, {})
    valid = [n for n, start in starts.items() if start <= now]
    return max(valid) if valid else None


async def load_calendar() -> dict[str, dict[int, datetime]]:
    """Return the calendar. Refreshes from the network at most once per hour;
    on error keeps the last valid one — or the bundled one on the first call
    offline."""
    global _cache, _cache_at
    if _cache is not None and time.monotonic() - _cache_at < _TTL:
        return _cache
    async with _get_lock():
        if _cache is not None and time.monotonic() - _cache_at < _TTL:
            return _cache
        fetched = await _fetch_all()
        if fetched:
            _cache = fetched
        elif _cache is None:
            _cache = BUNDLED_CALENDAR  # first call offline → bundled
        # A failure also respects the TTL; without this, every ranking in the
        # same cycle would repeat the 3 downloads while the source is down.
        _cache_at = time.monotonic()
        return _cache


async def _fetch_all() -> dict[str, dict[int, datetime]] | None:
    out: dict[str, dict[int, datetime]] = {}
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
            for region, url in _TIMES_URL.items():
                r = await client.get(url)
                r.raise_for_status()
                parsed = parse_season_starts(r.json())
                if parsed:
                    out[region] = parsed
        if len(out) == len(_TIMES_URL):
            return out
        logger.warning("season_calendar: partial fetch (%d/%d regions)", len(out), len(_TIMES_URL))
        return None
    except Exception:
        logger.exception("season_calendar: calendar fetch failed (using cache)")
        return None
