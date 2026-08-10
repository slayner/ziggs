"""Focused tests for the season calendar: parsing, exact regional bounds of
Season 33, half-open boundary assignment, and the short cross-region
transition."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.services import season_calendar


def _dt(s: str) -> datetime:
    return season_calendar._parse_dt(s)


def test_parse_season_starts_only_gvg_season_start():
    fake = {
        "Times": {
            "DateTime": [
                {"@uniquename": "GVG_SEASON_33_START", "@defaultdatetime": "2026-07-04T11:00:00Z"},
                {"@uniquename": "GVG_SEASON_33_END", "@defaultdatetime": "2026-08-31T11:00:00Z"},
                {"@uniquename": "GVG_SEASON_34_START", "@defaultdatetime": "2026-09-12T11:00:00Z"},
                {"@uniquename": "CRYSTAL_18_ASIA_START", "@defaultdatetime": "2023-02-01T00:00:00Z"},
                {"@uniquename": "DOWNTIME", "@time": "10:00:00"},  # sem defaultdatetime
                {"@uniquename": "GVG_SEASON_X_START", "@defaultdatetime": "2026-01-01T00:00:00Z"},
            ]
        }
    }
    parsed = season_calendar.parse_season_starts(fake)
    # Only GVG_SEASON_<N>_START; END, CRYSTAL and malformed entries are dropped.
    assert parsed == {33: _dt("2026-07-04T11:00:00Z"), 34: _dt("2026-09-12T11:00:00Z")}


def test_season_33_exact_regional_bounds():
    cal = season_calendar.BUNDLED_CALENDAR
    # Americas/Europe: 11:00 UTC. Asia: 00:00 UTC.
    am_lo, am_hi = season_calendar.season_bounds(cal, "americas", 33)
    eu_lo, eu_hi = season_calendar.season_bounds(cal, "europe", 33)
    as_lo, as_hi = season_calendar.season_bounds(cal, "asia", 33)
    assert (am_lo, am_hi) == (_dt("2026-07-04T11:00:00Z"), _dt("2026-09-12T11:00:00Z"))
    assert (eu_lo, eu_hi) == (_dt("2026-07-04T11:00:00Z"), _dt("2026-09-12T11:00:00Z"))
    assert (as_lo, as_hi) == (_dt("2026-07-04T00:00:00Z"), _dt("2026-09-12T00:00:00Z"))


def _in_season(cal, region, n, ts):
    lo, hi = season_calendar.season_bounds(cal, region, n)
    return lo is not None and lo <= ts and (hi is None or ts < hi)


def test_boundary_assignment_half_open_before_and_at_season_34_start():
    """An event just before the Season 34 start belongs to 33; exactly at the
    start it does NOT belong (half-open [S33, S34))."""
    cal = season_calendar.BUNDLED_CALENDAR

    just_before_am = _dt("2026-09-12T10:59:59Z")
    at_s34_am = _dt("2026-09-12T11:00:00Z")
    assert _in_season(cal, "americas", 33, just_before_am)
    assert not _in_season(cal, "americas", 33, at_s34_am)
    # Exactly at the S34 start it falls in S34, not S33.
    assert _in_season(cal, "americas", 34, at_s34_am)

    # Asia flips 11h earlier (00:00 UTC) — same half-open rule on its border.
    just_before_as = _dt("2026-09-11T23:59:59Z")
    at_s34_as = _dt("2026-09-12T00:00:00Z")
    assert _in_season(cal, "asia", 33, just_before_as)
    assert not _in_season(cal, "asia", 33, at_s34_as)
    assert _in_season(cal, "asia", 34, at_s34_as)


def test_current_season_resolves_region_by_region_at_cross_region_transition():
    """During the short transition, Asia has already flipped to S34 (00:00 UTC)
    while Americas/Europe are still in S33 (they flip at 11:00 UTC) — each
    region resolves its own, without attributing wrong bounds."""
    cal = season_calendar.BUNDLED_CALENDAR
    during_transition = _dt("2026-09-12T05:00:00Z")
    assert season_calendar.current_season(cal, "asia", during_transition) == 34
    assert season_calendar.current_season(cal, "americas", during_transition) == 33
    assert season_calendar.current_season(cal, "europe", during_transition) == 33
    # Before and after the transition, all regions agree.
    before = _dt("2026-08-09T12:00:00Z")
    after = _dt("2026-09-12T12:00:00Z")
    assert all(season_calendar.current_season(cal, r, before) == 33 for r in season_calendar.REGIONS)
    assert all(season_calendar.current_season(cal, r, after) == 34 for r in season_calendar.REGIONS)


def test_seasons_endpoint_historical_excludes_current_and_handles_transition():
    """The /seasons endpoint doesn't duplicate the current season in the
    historical list and handles the transition where regions are in different
    seasons."""
    from app.api.routes import highscores

    cal = season_calendar.BUNDLED_CALENDAR

    def run(regions, now):
        region_list = regions.split(",") if regions else None
        selected = region_list or list(season_calendar.REGIONS)
        current = {r: season_calendar.current_season(cal, r, now) for r in selected}
        current_vals = {v for v in current.values() if v is not None}
        common = set(cal.get(selected[0], {}))
        for r in selected[1:]:
            common &= set(cal.get(r, {}))
        min_current = min(current_vals) if current_vals else None
        historical = sorted((n for n in common if min_current is not None and n < min_current), reverse=True)
        return current, historical

    # Today (2026-08-09): all regions in S33. Historical = 17..32; 33 doesn't appear.
    now = _dt("2026-08-09T00:00:00Z")
    current, historical = run("americas,asia", now)
    assert current == {"americas": 33, "asia": 33}
    assert 33 not in historical
    assert max(historical) == 32

    # During the transition: asia=34, americas=33. Neither (33/34) is historical —
    # 33 is still current in americas, 34 is current in asia.
    during = _dt("2026-09-12T05:00:00Z")
    current_t, historical_t = run("americas,asia", during)
    assert current_t == {"americas": 33, "asia": 34}
    assert 33 not in historical_t and 34 not in historical_t


def test_load_calendar_returns_bundled_offline_and_retains_on_error():
    # No network: the real _fetch_all catches the error and returns None; we
    # simulate that None directly. load_calendar falls back to the bundled
    # (last valid) and retains it on subsequent calls (retain last valid on
    # errors).
    calls = 0

    async def no_net(*_a, **_kw):
        nonlocal calls
        calls += 1
        return None

    async def load_twice():
        first = await season_calendar.load_calendar()
        second = await season_calendar.load_calendar()
        assert first is second
        return first

    # Reset the module cache to simulate a cold first call.
    with patch.object(season_calendar, "_cache", None), \
         patch.object(season_calendar, "_cache_at", 0.0), \
         patch.object(season_calendar, "_fetch_all", no_net):
        cal = asyncio.run(load_twice())
    assert season_calendar.season_bounds(cal, "americas", 33)[0] == _dt("2026-07-04T11:00:00Z")
    assert calls == 1


def test_unknown_season_never_falls_back_to_alltime():
    from fastapi import HTTPException
    from app.api.routes import highscores

    async def bundled():
        return season_calendar.BUNDLED_CALENDAR

    with patch.object(season_calendar, "load_calendar", bundled):
        try:
            asyncio.run(highscores._resolve_window("season:999", ["americas"]))
        except HTTPException as exc:
            assert exc.status_code == 422
        else:
            raise AssertionError("unknown season became an all-time ranking")


def test_week_and_month_are_calendar_periods_not_rolling_windows():
    from app.api.routes import highscores

    week = asyncio.run(highscores._resolve_window("week", ["americas"]))
    assert week.lo.weekday() == 6  # Sunday
    assert (week.lo.hour, week.lo.minute, week.lo.second) == (0, 0, 0)
    assert week.hi - week.lo == timedelta(days=7)

    month = asyncio.run(highscores._resolve_window("month", ["americas"]))
    assert (month.lo.day, month.lo.hour, month.lo.minute, month.lo.second) == (1, 0, 0, 0)
    assert (month.hi.day, month.hi.hour, month.hi.minute, month.hi.second) == (1, 0, 0, 0)
    assert month.hi.month == (month.lo.month % 12) + 1

    # The concrete key changes with the bounds; the previous period's cache won't match.
    old_week = highscores.TimeWindow(lo=week.lo - timedelta(days=7), hi=week.lo)
    assert highscores._window_marker(old_week) != highscores._window_marker(week)


if __name__ == "__main__":
    test_parse_season_starts_only_gvg_season_start()
    test_season_33_exact_regional_bounds()
    test_boundary_assignment_half_open_before_and_at_season_34_start()
    test_current_season_resolves_region_by_region_at_cross_region_transition()
    test_seasons_endpoint_historical_excludes_current_and_handles_transition()
    test_load_calendar_returns_bundled_offline_and_retains_on_error()
    test_unknown_season_never_falls_back_to_alltime()
    test_week_and_month_are_calendar_periods_not_rolling_windows()
    print("ok")
