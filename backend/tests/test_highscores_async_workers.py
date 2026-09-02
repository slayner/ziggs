import asyncio
from unittest.mock import patch

from app.api.routes import battles, highscores
from app.services import highscores_cache


class _AsyncSession:
    def __init__(self):
        self.added = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def scalars(self, _query):
        class Result:
            def all(self):
                return []

        return Result()

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        raise AssertionError("unexpected rollback")


def test_highscores_precompute_cycle_uses_async_session():
    session = _AsyncSession()

    async def fake_highlights(db, _regions):
        assert db is session
        return {"source": "fresh"}

    async def fake_rankings(db, *_args, **_kw):
        assert db is session
        return {"total": 0, "rows": []}

    # Resolve windows without touching the network (season would call load_calendar → fetch).
    async def fake_resolve_window(window, _region_list):
        return highscores.TimeWindow()

    seen_kinds = set()
    real_key = highscores_cache.rankings_cache_key

    def trace_key(kind, window, region_list):
        k = real_key(kind, window, region_list)
        if k:
            seen_kinds.add((kind, window, k))
        return k

    with (
        patch.object(highscores_cache, "AsyncSessionLocal", lambda: session, create=True),
        patch.object(
            highscores_cache,
            "SyncSessionLocal",
            lambda: (_ for _ in ()).throw(AssertionError("precompute used a sync session")),
            create=True,
        ),
        patch.object(highscores, "_compute_highlights", fake_highlights),
        patch.object(highscores, "_compute_rankings", fake_rankings),
        patch.object(highscores, "_resolve_window", fake_resolve_window),
        patch.object(highscores_cache, "rankings_cache_key", trace_key),
    ):
        written = asyncio.run(highscores_cache.sync_once())
    assert written == len(session.added) > 0
    assert session.committed
    assert all(row.payload.get("source") == "fresh" for row in session.added if row.key.startswith("hs:hl:"))

    # Only alltime/week are precomputed; month/season are on demand.
    windows_seen = {w for _kind, w, _k in seen_kinds}
    assert windows_seen == set(highscores_cache.WINDOWS)

    # silver_dropped is cached for the same cheap windows.
    silver_windows = {w for kind, w, _k in seen_kinds if kind == "silver_dropped"}
    assert silver_windows == set(highscores_cache.WINDOWS)

    # gather/fishing/crafting are all-time ONLY (account totals, no window).
    for cumul in ("gather_total", "fishing", "crafting"):
        assert {w for kind, w, _k in seen_kinds if kind == cumul} == {"alltime"}

    # season:N historical has NO cache key (computed on the fly).
    assert highscores_cache.rankings_cache_key("pvp_fame", "season:32", ["americas"]) is None


def test_rankings_cache_key_month_and_season_current():
    rk = ["americas"]
    for w in ("alltime", "week"):
        assert highscores_cache.rankings_cache_key("pvp_fame", w, rk) == f"hs:rk:pvp_fame:{w}:americas"
        assert highscores_cache.rankings_cache_key("silver_dropped", w, rk) == f"hs:rk:silver_dropped:{w}:americas"
    for w in ("month", "season"):
        assert highscores_cache.rankings_cache_key("pvp_fame", w, rk) is None
        assert highscores_cache.rankings_cache_key("silver_dropped", w, rk) is None
    # gather is all-time only — week/month/season don't cache (don't support window).
    assert highscores_cache.rankings_cache_key("gather_total", "alltime", rk) == "hs:rk:gather_total:alltime:americas"
    for w in ("week", "month", "season"):
        assert highscores_cache.rankings_cache_key("gather_total", w, rk) is None
    # season:N historical is not precomputed.
    assert highscores_cache.rankings_cache_key("pvp_fame", "season:32", rk) is None


def test_weapon_function_map_accepts_async_session():
    class Result:
        def all(self):
            return [("T7_MAIN_SWORD", "dps")]

    class AsyncSession:
        async def execute(self, _query):
            return Result()

    with patch.object(battles, "_weapon_fn_cache", None):
        assert asyncio.run(battles._weapon_function_map(AsyncSession())) == {"MAIN_SWORD": "dps"}


if __name__ == "__main__":
    test_highscores_precompute_cycle_uses_async_session()
    test_rankings_cache_key_month_and_season_current()
    test_weapon_function_map_accepts_async_session()
    print("ok")
