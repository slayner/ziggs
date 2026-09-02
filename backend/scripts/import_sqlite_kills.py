"""Importa kill events (e dependências) do SQLite pro Postgres.
Fases explícitas — você escolhe. Idempotente (ON CONFLICT DO NOTHING).

Uso:
  python -m scripts.import_sqlite_kills <fase>

Fases:
  clear       — TRUNCATE de player_kill_events, albion_players, player_snapshots, player_weapon_stats
  players     — albion_players (com IDs originais do SQLite)
  snapshots   — player_snapshots
  weaponstats — player_weapon_stats
  kills       — player_kill_events
  sequences   — ajusta sequences do Postgres
  status      — contagens atuais
"""
from __future__ import annotations

import sqlite3
import sys
import time
import psycopg

SQLITE_PATH = r"C:\Users\Gabriel\Documents\Code\ziggs\backend\ziggs.db"
PG_DSN = "postgresql://ziggs:azqwsx123@localhost:5432/ziggs"
BATCH = 5000


def open_db():
    lite = sqlite3.connect(SQLITE_PATH)
    lite.row_factory = sqlite3.Row
    pg = psycopg.connect(PG_DSN)
    pg.autocommit = False
    return lite, pg


def batch_insert(lite, pg, query, insert_sql, params_fn, batch_size=BATCH):
    offset = 0
    total = lite.execute(f"SELECT COUNT(*) FROM ({query})").fetchone()[0]
    t0 = time.time()
    inserted = 0
    while offset < total:
        rows = lite.execute(f"{query} LIMIT {batch_size} OFFSET {offset}").fetchall()
        batch = []
        for r in rows:
            p = params_fn(r)
            if p is not None:
                batch.append(p)
        if batch:
            with pg.cursor() as cur:
                cur.executemany(insert_sql, batch)
                inserted += len(batch)
        pg.commit()
        offset += len(rows)
        if offset % (batch_size * 10) == 0 or offset >= total:
            print(f"  {offset}/{total} ({time.time()-t0:.0f}s)")
    print(f"  Done: {inserted} rows ({time.time()-t0:.0f}s)")


def phase_clear(lite, pg):
    print("[clear] TRUNCATE player tables...")
    pg.execute("TRUNCATE player_kill_events, player_snapshots, player_weapon_stats, albion_players CASCADE")
    for tbl, col in [("albion_players", "id"), ("player_snapshots", "id"),
                     ("player_weapon_stats", "id"), ("player_kill_events", "id")]:
        pg.execute(f"SELECT setval(pg_get_serial_sequence('{tbl}', '{col}'), 1, false)")
    pg.commit()
    print("[clear] Done.")


def phase_players(lite, pg):
    print("[players] Importando albion_players...")
    batch_insert(
        lite, pg,
        "SELECT id, albion_id, name, guild_id, guild_name, alliance_id, alliance_name, "
        "alliance_tag, avatar, kill_fame, death_fame, pve_fame, crafting_fame, gathering_fame, "
        "first_seen_at, last_seen_at, region, is_deleted, refresh_requested_at, "
        "lifetime_statistics, gather_wood, gather_hide, gather_ore, gather_rock, gather_fiber, fishing_fame "
        "FROM albion_players",
        """INSERT INTO albion_players (id, albion_id, name, guild_id, guild_name, alliance_id,
           alliance_name, alliance_tag, avatar, kill_fame, death_fame, pve_fame, crafting_fame,
           gathering_fame, first_seen_at, last_seen_at, region, is_deleted, refresh_requested_at,
           lifetime_statistics, gather_wood, gather_hide, gather_ore, gather_rock, gather_fiber, fishing_fame)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (id) DO NOTHING""",
        lambda r: (r["id"], r["albion_id"], r["name"], r["guild_id"], r["guild_name"],
                   r["alliance_id"], r["alliance_name"], r["alliance_tag"], r["avatar"],
                   r["kill_fame"], r["death_fame"], r["pve_fame"], r["crafting_fame"],
                   r["gathering_fame"], r["first_seen_at"], r["last_seen_at"], r["region"],
                   bool(r["is_deleted"]), r["refresh_requested_at"],
                   r["lifetime_statistics"],
                   r["gather_wood"], r["gather_hide"], r["gather_ore"],
                   r["gather_rock"], r["gather_fiber"], r["fishing_fame"]),
    )


def phase_snapshots(lite, pg):
    print("[snapshots] Importando player_snapshots...")
    batch_insert(
        lite, pg,
        "SELECT id, player_id, guild_id, guild_name, alliance_id, alliance_tag, "
        "kill_fame, death_fame, pve_fame, snapshotted_at FROM player_snapshots",
        """INSERT INTO player_snapshots (id, player_id, guild_id, guild_name, alliance_id,
           alliance_tag, kill_fame, death_fame, pve_fame, snapshotted_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (id) DO NOTHING""",
        lambda r: (r["id"], r["player_id"], r["guild_id"], r["guild_name"],
                   r["alliance_id"], r["alliance_tag"], r["kill_fame"], r["death_fame"],
                   r["pve_fame"], r["snapshotted_at"]),
    )


def phase_weaponstats(lite, pg):
    print("[weaponstats] Importando player_weapon_stats...")
    batch_insert(
        lite, pg,
        "SELECT id, albion_player_id, weapon_base, kills, appearances, eligible_appearances, "
        "pierce_points, healer_points, zero_death_eligible_fights, tank_ok_fights, synced_at "
        "FROM player_weapon_stats",
        """INSERT INTO player_weapon_stats (id, albion_player_id, weapon_base, kills, appearances,
           eligible_appearances, pierce_points, healer_points, zero_death_eligible_fights,
           tank_ok_fights, synced_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (id) DO NOTHING""",
        lambda r: (r["id"], r["albion_player_id"], r["weapon_base"], r["kills"],
                   r["appearances"], r["eligible_appearances"], r["pierce_points"],
                   r["healer_points"], r["zero_death_eligible_fights"],
                   r["tank_ok_fights"], r["synced_at"]),
    )


def phase_kills(lite, pg):
    print("[kills] Importando player_kill_events...")
    batch_insert(
        lite, pg,
        "SELECT id, region, albion_event_id, timestamp, fame, killer_player_id, victim_player_id, "
        "participant_count, is_solo, albion_battle_id, kill_area, killer_equipment, victim_equipment, "
        "victim_inventory, killer_guild_id, killer_guild_name, victim_guild_id, victim_guild_name, "
        "source_install, silver_dropped, group_member_count, participants "
        "FROM player_kill_events",
        """INSERT INTO player_kill_events (id, region, albion_event_id, timestamp, fame,
           killer_player_id, victim_player_id, participant_count, is_solo, albion_battle_id,
           kill_area, killer_equipment, victim_equipment, victim_inventory, killer_guild_id,
           killer_guild_name, victim_guild_id, victim_guild_name, silver_dropped,
           group_member_count, participants)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                   %s, %s, %s, %s, %s, %s, %s::jsonb)
           ON CONFLICT (id) DO NOTHING""",
        lambda r: (r["id"], r["region"], r["albion_event_id"], r["timestamp"], r["fame"],
                   r["killer_player_id"], r["victim_player_id"], r["participant_count"],
                   bool(r["is_solo"]), r["albion_battle_id"], r["kill_area"],
                   r["killer_equipment"], r["victim_equipment"], r["victim_inventory"],
                   r["killer_guild_id"], r["killer_guild_name"],
                   r["victim_guild_id"], r["victim_guild_name"],
                   r["silver_dropped"], r["group_member_count"], r["participants"]),
    )


def phase_sequences(lite, pg):
    print("[sequences] Ajustando sequences...")
    for tbl, col in [("albion_players", "id"), ("player_snapshots", "id"),
                     ("player_weapon_stats", "id"), ("player_kill_events", "id")]:
        max_id = pg.execute(f"SELECT COALESCE(MAX({col}), 0) FROM {tbl}").fetchone()[0]
        pg.execute(f"SELECT setval(pg_get_serial_sequence('{tbl}', '{col}'), {max_id}, true)")
        print(f"  {tbl}.{col} -> {max_id}")
    pg.commit()
    print("[sequences] Done.")


def phase_status(lite, pg):
    print("=== Postgres ===")
    for tbl in ["albion_players", "player_snapshots", "player_weapon_stats", "player_kill_events"]:
        cnt = pg.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"  {tbl}: {cnt}")
    print("=== SQLite ===")
    for tbl in ["albion_players", "player_snapshots", "player_weapon_stats", "player_kill_events"]:
        cnt = lite.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"  {tbl}: {cnt}")


PHASES = {
    "clear": phase_clear,
    "players": phase_players,
    "snapshots": phase_snapshots,
    "weaponstats": phase_weaponstats,
    "kills": phase_kills,
    "sequences": phase_sequences,
    "status": phase_status,
}

ORDER = ["clear", "players", "snapshots", "weaponstats", "kills", "sequences"]


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in PHASES:
        print("Uso: python -m scripts.import_sqlite_kills <fase>")
        print(f"Fases: {', '.join(ORDER)}")
        print("  status — mostra contagens")
        sys.exit(1)
    phase = sys.argv[1]
    lite, pg = open_db()
    try:
        PHASES[phase](lite, pg)
    finally:
        lite.close()
        pg.close()


if __name__ == "__main__":
    main()