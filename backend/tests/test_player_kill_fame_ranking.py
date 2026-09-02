"""Sanity checks for the player pvp_fame ranking.

Verifies that scope=player pvp_fame counts ALL kill fame from the kill feed
(player_kill_events), not just battle fame. Uses in-memory SQLite with the
JSONB shim like the other ranking tests.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.api.routes.highscores import TimeWindow, _player_kill_fame_rankings
from app.models.base import Base
from app.models.players import AlbionPlayer, PlayerKillEvent


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_test(_type, _compiler, **_kw):
    return "JSON"


def _setup():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[AlbionPlayer.__table__, PlayerKillEvent.__table__])
    db = sessionmaker(bind=engine)()
    a = AlbionPlayer(id=1, albion_id="pA", name="KillerA", region="americas", kill_fame=999999)
    b = AlbionPlayer(id=2, albion_id="pB", name="KillerB", region="americas", kill_fame=1000)
    db.add_all([a, b])
    db.flush()
    now = datetime.now(timezone.utc)
    for i, (killer, victim, fame, days_ago) in enumerate([
        (a, b, 4000, 0),
        (a, b, 1000, 0),
        (b, a, 1000, 10),
    ]):
        db.add(PlayerKillEvent(
            id=i + 1, region="americas", albion_event_id=str(i), timestamp=now - timedelta(days=days_ago),
            fame=fame, killer_player_id=killer.id, victim_player_id=victim.id,
        ))
    db.commit()
    return db


class AsyncAdapter:
    """Wraps a sync session so async ranking functions can use it."""
    def __init__(self, db):
        self._db = db

    async def scalar(self, query):
        return self._db.scalar(query)

    async def execute(self, query):
        return self._db.execute(query)

    async def scalars(self, query):
        return self._db.scalars(query)


def test_alltime_counts_all_kills():
    db = _setup()
    out = asyncio.run(_player_kill_fame_rankings(AsyncAdapter(db), None, TimeWindow(), None, 10, 0))
    assert out["total"] == 2, f"expected 2 killers, got {out['total']}"
    assert out["rows"][0]["name"] == "KillerA", f"expected A first, got {out['rows'][0]['name']}"
    assert out["rows"][0]["value"] == 5000, f"expected A=5000, got {out['rows'][0]['value']}"
    assert out["rows"][1]["name"] == "KillerB"
    assert out["rows"][1]["value"] == 1000
    print("all-time player pvp_fame OK:", [(r["name"], r["value"]) for r in out["rows"]])


def test_weekly_excludes_old_kills():
    db = _setup()
    week_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    week_start -= timedelta(days=week_start.weekday())  # Monday
    tw = TimeWindow(lo=week_start, hi=week_start + timedelta(days=7))
    out = asyncio.run(_player_kill_fame_rankings(AsyncAdapter(db), None, tw, None, 10, 0))
    assert out["total"] == 1, f"expected 1 killer in week, got {out['total']}"
    assert out["rows"][0]["name"] == "KillerA"
    assert out["rows"][0]["value"] == 5000
    print("weekly player pvp_fame OK:", [(r["name"], r["value"]) for r in out["rows"]])


def test_search_filters_by_name():
    db = _setup()
    out = asyncio.run(_player_kill_fame_rankings(AsyncAdapter(db), None, TimeWindow(), "KillerA", 10, 0))
    assert out["total"] == 1
    assert out["rows"][0]["name"] == "KillerA"
    print("search filter OK:", out["rows"][0]["name"])


def test_pagination():
    db = _setup()
    out = asyncio.run(_player_kill_fame_rankings(AsyncAdapter(db), None, TimeWindow(), None, 1, 0))
    assert out["total"] == 2
    assert len(out["rows"]) == 1
    assert out["rows"][0]["name"] == "KillerA"
    out2 = asyncio.run(_player_kill_fame_rankings(AsyncAdapter(db), None, TimeWindow(), None, 1, 1))
    assert out2["rows"][0]["name"] == "KillerB"
    print("pagination OK: page1=%s page2=%s" % (out["rows"][0]["name"], out2["rows"][0]["name"]))


if __name__ == "__main__":
    test_alltime_counts_all_kills()
    test_weekly_excludes_old_kills()
    test_search_filters_by_name()
    test_pagination()
    print("ok")