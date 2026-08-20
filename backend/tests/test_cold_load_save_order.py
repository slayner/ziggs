"""Repro comportamental do bug 'primeira carga não salva kills/deaths'.

Simula o cold load de um jogador QUE AINDA NÃO EXISTE no banco (verdadeira
primeira visita pelo /players/by-name/{region}/{name}). O `_cold_load_player`
registrava os eventos de kill/death ANTES de criar a linha do jogador —
`_record_kill_event` resolve killer_player_id/victim_player_id por lookup no
banco, e sem a linha do alvo os FKs ficavam NULL. O dedupe por
(region, albion_event_id) impede o re-link: as kills/deaths ficavam orfanadas
pra sempre, e nem o refresh (warmer) recuperava (eventos já no ledger).

Cobre as 3 rotas que fazem upsert+sync: `_cold_load_player` (by-name),
`get_player` (by-id) e `warm_by_name` (companion bootstrap). A ordem
upsert_player ANTES de sync_player_kills (mesma do `_warm_player`) garante o
link. Ver test_profile_refresh.py test_*_grava_nucleo_antes_da_sync_de_kills.

Self-check (sem framework): `python tests/test_cold_load_save_order.py`.
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import BigInteger, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.players import AlbionPlayer, PlayerKillEvent
from app.services import player_tracker as pt
from app.services import search_index


@compiles(JSONB, "sqlite")
def _jsonb_for_sqlite(_type, _compiler, **_kw):
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_for_sqlite(_type, _compiler, **_kw):
    return "INTEGER"


ALBION_ID = "TargetPlayer12345"
REGION = "americas"
HOST = "gameinfo.albiononline.com"

RAW_PROFILE: dict[str, Any] = {
    "Id": ALBION_ID, "Name": "TargetPlayer",
    "GuildId": None, "GuildName": None,
    "AllianceId": None, "AllianceName": None, "AllianceTag": None,
    "Avatar": "a", "KillFame": 999_111, "DeathFame": 222_333,
    "LifetimeStatistics": {
        "PvE": {"Total": 1_000_000},
        "Crafting": {"Total": 0},
        "Gathering": {"All": {"Total": 500_000}, "Wood": {"Total": 100_000},
                       "Hide": {"Total": 0}, "Ore": {"Total": 200_000},
                       "Rock": {"Total": 0}, "Fiber": {"Total": 0}},
        "FishingFame": 0,
    },
}

KILL_EVENTS: list[dict] = [{
    "EventId": "ev_k1", "TimeStamp": "2026-08-18T01:00:00.000Z",
    "TotalVictimKillFame": 5000, "numberOfParticipants": 1, "BattleId": "111",
    "KillArea": "OPEN_WORLD",
    "Killer": {"Id": ALBION_ID, "Name": "TargetPlayer", "KillFame": 999_111,
               "DeathFame": 222_333, "LifetimeStatistics": RAW_PROFILE["LifetimeStatistics"]},
    "Victim": {"Id": "Victim1", "Name": "SomeVictim", "KillFame": 10,
               "DeathFame": 20, "LifetimeStatistics": {"PvE": {"Total": 5}}},
    "Participants": [],
}]
DEATH_EVENTS: list[dict] = [{
    "EventId": "ev_d1", "TimeStamp": "2026-08-18T01:30:00.000Z",
    "TotalVictimKillFame": 7000, "numberOfParticipants": 1, "BattleId": "222",
    "KillArea": "OPEN_WORLD",
    "Killer": {"Id": "Killer2", "Name": "SomeKiller", "KillFame": 30,
               "DeathFame": 40, "LifetimeStatistics": {"PvE": {"Total": 7}}},
    "Victim": {"Id": ALBION_ID, "Name": "TargetPlayer", "KillFame": 999_111,
               "DeathFame": 222_333, "LifetimeStatistics": RAW_PROFILE["LifetimeStatistics"]},
    "Participants": [],
}]


class _FakeResp:
    def __init__(self, data, status=200):
        self._data = data; self.status_code = status; self.text = str(data)
    def json(self): return self._data
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    async def get(self, url, params=None):
        if "/kills" in url: return _FakeResp(KILL_EVENTS)
        if "/deaths" in url: return _FakeResp(DEATH_EVENTS)
        return _FakeResp([], 404)
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass


async def _build_engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return eng


async def _linked_count(db, player):
    return list((await db.scalars(select(PlayerKillEvent).where(
        (PlayerKillEvent.killer_player_id == player.id)
        | (PlayerKillEvent.victim_player_id == player.id)
    ))).all())


async def _run(kills_first: bool) -> int:
    """Roda upsert+sync na ordem dada (em DB limpo) e devolve qtos eventos
    ficaram linkados ao jogador alvo."""
    # Stub do search_index: ele tenta abrir sessão Postgres de verdade (não
    # existe no teste). safe_upsert_entry_async é só indexação pra busca —
    # não afeta o ledger/FK, que é o que este teste verifica.
    search_index.safe_upsert_entry_async = lambda *a, **k: asyncio.sleep(0)  # type: ignore[assignment]
    eng = await _build_engine()
    Session = async_sessionmaker(bind=eng, expire_on_commit=False)
    try:
        async with Session() as db:
            client = _FakeClient()
            if kills_first:
                await pt.sync_player_kills(client, db, HOST, REGION, ALBION_ID)
                await pt.upsert_player(db, RAW_PROFILE, REGION)
            else:
                await pt.upsert_player(db, RAW_PROFILE, REGION)
                await pt.sync_player_kills(client, db, HOST, REGION, ALBION_ID)
            player = await db.scalar(select(AlbionPlayer).where(AlbionPlayer.albion_id == ALBION_ID))
            assert player is not None
            return len(await _linked_count(db, player))
    finally:
        await eng.dispose()


async def main():
    # ORDEM ANTIGA (bug): sync antes do upsert — orfana o ledger.
    n_orphan = await _run(kills_first=True)
    assert n_orphan == 0, f"ordem antiga deveria orfanar (0 linkados), linkou {n_orphan}"

    # ORDEM NOVA (fix): upsert antes da sync — links corretos.
    n_linked = await _run(kills_first=False)
    assert n_linked == 2, f"ordem nova deveria linkar as 2 kills/deaths, linkou {n_linked}"

    # Cold load BUGADO + refresh (warmer) NÃO recupera (dedupe por event_id).
    search_index.safe_upsert_entry_async = lambda *a, **k: asyncio.sleep(0)  # type: ignore[assignment]
    eng = await _build_engine()
    Session = async_sessionmaker(bind=eng, expire_on_commit=False)
    try:
        async with Session() as db:
            client = _FakeClient()
            await pt.sync_player_kills(client, db, HOST, REGION, ALBION_ID)
            await pt.upsert_player(db, RAW_PROFILE, REGION)
            await pt.upsert_player(db, RAW_PROFILE, REGION)  # refresh (warmer)
            await pt.sync_player_kills(client, db, HOST, REGION, ALBION_ID)
            player = await db.scalar(select(AlbionPlayer).where(AlbionPlayer.albion_id == ALBION_ID))
            n_after_refresh = len(await _linked_count(db, player))
        assert n_after_refresh == 0, (
            f"refresh não recupera órfãs (dedupe event_id), ainda {n_after_refresh} linkados — "
            "a ordem do cold load é a única chance de linkar")
    finally:
        await eng.dispose()

    print("cold_load_save_order OK: ordem correta = upsert_player ANTES de sync_player_kills")


if __name__ == "__main__":
    asyncio.run(main())