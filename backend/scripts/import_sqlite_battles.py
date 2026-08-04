"""Importa batalhas do SQLite pro Postgres. Fases explícitas — você escolhe.

Uso:
  python -m scripts.import_sqlite_battles <fase>

Fases:
  clear        — TRUNCATE de tudo (cascade), reset sequences
  battles      — batalhas letais (com IDs originais)
  sides        — battle_sides (só de batalhas letais)
  guilds       — battle_guilds (só de batalhas letais)
  participants — battle_participants (só de batalhas letais)
  kills        — battle_kill_events (só de batalhas letais)
  probes       — todos os probes: não-letais + missing + found
  sequences    — ajusta sequences do Postgres pro maior ID
  status       — mostra contagens atuais

Idempotente: ON CONFLICT DO NOTHING em tudo. Pode parar e re-rodar a mesma fase.
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


def make_lethal_temp(lite):
    lite.execute("DROP TABLE IF EXISTS _tmp_lethal_ids")
    lite.execute("CREATE TEMP TABLE _tmp_lethal_ids AS SELECT id FROM battles WHERE is_lethal = 1")
    lite.execute("CREATE INDEX IF NOT EXISTS _idx_lethal ON _tmp_lethal_ids(id)")


def batch_insert(lite, pg, query, insert_sql, params_fn, batch_size=BATCH):
    """Lê do SQLite em batches e insere no Postgres com executemany."""
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
        print(f"  {offset}/{total} ({time.time()-t0:.0f}s)")
    print(f"  Done: {inserted} rows ({time.time()-t0:.0f}s)")


# --- FASES ---

def phase_clear(lite, pg):
    print("[clear] TRUNCATE + reset sequences...")
    pg.execute("TRUNCATE battles, battle_sides, battle_guilds, battle_participants, battle_kill_events, battle_id_probes CASCADE")
    for tbl, col in [("battles", "id"), ("battle_sides", "id"),
                     ("battle_guilds", "id"), ("battle_participants", "id"),
                     ("battle_kill_events", "id")]:
        pg.execute(f"SELECT setval(pg_get_serial_sequence('{tbl}', '{col}'), 1, false)")
    pg.commit()
    print("[clear] Done.")


def phase_battles(lite, pg):
    make_lethal_temp(lite)
    print("[battles] Importando batalhas letais...")
    batch_insert(
        lite, pg,
        "SELECT id, region, albion_id, start_time, end_time, total_fame, "
        "kill_count, cluster, players_total, processing_tier, is_zvz, is_lethal, "
        "fetched_at, profiles_synced, reprocess_reason, found_by "
        "FROM battles WHERE is_lethal = 1",
        """INSERT INTO battles (id, region, albion_id, start_time, end_time,
           total_fame, kill_count, cluster, players_total, processing_tier,
           is_zvz, is_lethal, fetched_at, profiles_synced, reprocess_reason, found_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (id) DO NOTHING""",
        lambda r: (r["id"], r["region"], str(r["albion_id"]), r["start_time"],
                   r["end_time"], r["total_fame"], r["kill_count"], r["cluster"],
                   r["players_total"], r["processing_tier"], bool(r["is_zvz"]),
                   bool(r["is_lethal"]), r["fetched_at"], bool(r["profiles_synced"]),
                   r["reprocess_reason"], r["found_by"]),
    )


def phase_sides(lite, pg):
    make_lethal_temp(lite)
    print("[sides] Importando battle_sides...")
    batch_insert(
        lite, pg,
        "SELECT s.id, s.battle_id, s.label, s.is_rats, s.player_count, s.score "
        "FROM battle_sides s WHERE s.battle_id IN (SELECT id FROM _tmp_lethal_ids)",
        """INSERT INTO battle_sides (id, battle_id, label, is_rats, player_count, score)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (id) DO NOTHING""",
        lambda r: (r["id"], r["battle_id"], r["label"], bool(r["is_rats"]),
                   r["player_count"], r["score"]),
    )


def phase_guilds(lite, pg):
    make_lethal_temp(lite)
    print("[guilds] Importando battle_guilds...")
    batch_insert(
        lite, pg,
        "SELECT g.id, g.battle_id, g.albion_guild_id, g.guild_name, g.alliance_id, "
        "g.alliance_name, g.kill_fame, g.kills, g.deaths, g.side_id "
        "FROM battle_guilds g WHERE g.battle_id IN (SELECT id FROM _tmp_lethal_ids)",
        """INSERT INTO battle_guilds (id, battle_id, albion_guild_id, guild_name,
           alliance_id, alliance_name, kill_fame, kills, deaths, side_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (id) DO NOTHING""",
        lambda r: (r["id"], r["battle_id"], r["albion_guild_id"], r["guild_name"],
                   r["alliance_id"], r["alliance_name"], r["kill_fame"],
                   r["kills"], r["deaths"], r["side_id"]),
    )


def phase_participants(lite, pg):
    make_lethal_temp(lite)
    print("[participants] Importando battle_participants...")
    batch_insert(
        lite, pg,
        "SELECT p.id, p.battle_id, p.albion_player_id, p.name, p.guild_id, p.guild_name, "
        "p.alliance_id, p.alliance_name, p.side_id, p.kills, p.deaths, p.kill_fame, p.ip, "
        "p.damage_dealt, p.damage_taken, p.healing_done, p.equipment, p.assists "
        "FROM battle_participants p WHERE p.battle_id IN (SELECT id FROM _tmp_lethal_ids)",
        """INSERT INTO battle_participants (id, battle_id, albion_player_id, name,
           guild_id, guild_name, alliance_id, alliance_name, side_id, kills, deaths,
           kill_fame, ip, damage_dealt, damage_taken, healing_done, equipment, assists)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
           ON CONFLICT (id) DO NOTHING""",
        lambda r: (r["id"], r["battle_id"], r["albion_player_id"], r["name"],
                   r["guild_id"], r["guild_name"], r["alliance_id"], r["alliance_name"],
                   r["side_id"], r["kills"], r["deaths"], r["kill_fame"], r["ip"],
                   r["damage_dealt"], r["damage_taken"], r["healing_done"],
                   r["equipment"], r["assists"]),
    )


def phase_kills(lite, pg):
    make_lethal_temp(lite)
    print("[kills] Importando battle_kill_events...")
    batch_insert(
        lite, pg,
        "SELECT k.id, k.battle_id, k.albion_event_id, k.timestamp, k.fame, "
        "k.killer_participant_id, k.victim_participant_id, k.killer_side_id, k.victim_side_id, "
        "k.killer_equipment, k.victim_equipment, k.killer_inventory, k.victim_inventory "
        "FROM battle_kill_events k WHERE k.battle_id IN (SELECT id FROM _tmp_lethal_ids)",
        """INSERT INTO battle_kill_events (id, battle_id, albion_event_id, timestamp,
           fame, killer_participant_id, victim_participant_id, killer_side_id,
           victim_side_id, killer_equipment, victim_equipment, killer_inventory,
           victim_inventory)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
           ON CONFLICT (id) DO NOTHING""",
        lambda r: (r["id"], r["battle_id"], r["albion_event_id"], r["timestamp"],
                   r["fame"], r["killer_participant_id"], r["victim_participant_id"],
                   r["killer_side_id"], r["victim_side_id"],
                   r["killer_equipment"], r["victim_equipment"],
                   r["killer_inventory"], r["victim_inventory"]),
    )


def phase_probes(lite, pg):
    print("[probes] Importando probes (3 sub-fases)...")

    # 1. Não-letais como 'missing'
    print("  [1/3] Batalhas nao-letais -> missing")
    batch_insert(
        lite, pg,
        "SELECT region, albion_id FROM battles WHERE is_lethal = 0 OR is_lethal IS NULL",
        """INSERT INTO battle_id_probes (albion_id, status, region)
           VALUES (%s, 'missing', %s)
           ON CONFLICT (albion_id) DO NOTHING""",
        lambda r: (str(r["albion_id"]), r["region"]),
    )

    # 2. Probes missing do SQLite
    print("  [2/3] Probes missing existentes")
    batch_insert(
        lite, pg,
        "SELECT albion_id, region FROM battle_id_probes WHERE status = 'missing'",
        """INSERT INTO battle_id_probes (albion_id, status, region)
           VALUES (%s, 'missing', %s)
           ON CONFLICT (albion_id) DO NOTHING""",
        lambda r: (str(r["albion_id"]), r["region"]),
    )

    # 3. Probes found do SQLite (battle_id=NULL — pode apontar pra nao-letal)
    print("  [3/3] Probes found")
    batch_insert(
        lite, pg,
        "SELECT albion_id, region FROM battle_id_probes WHERE status = 'found'",
        """INSERT INTO battle_id_probes (albion_id, status, region, battle_id)
           VALUES (%s, 'found', %s, NULL)
           ON CONFLICT (albion_id) DO NOTHING""",
        lambda r: (str(r["albion_id"]), r["region"]),
    )


def phase_sequences(lite, pg):
    print("[sequences] Ajustando sequences...")
    for tbl, col in [("battles", "id"), ("battle_sides", "id"),
                     ("battle_guilds", "id"), ("battle_participants", "id"),
                     ("battle_kill_events", "id")]:
        max_id = pg.execute(f"SELECT COALESCE(MAX({col}), 0) FROM {tbl}").fetchone()[0]
        pg.execute(f"SELECT setval(pg_get_serial_sequence('{tbl}', '{col}'), {max_id}, true)")
        print(f"  {tbl}.{col} -> {max_id}")
    pg.commit()
    print("[sequences] Done.")


def phase_status(lite, pg):
    print("=== Postgres ===")
    for tbl in ["battles", "battle_sides", "battle_guilds",
                "battle_participants", "battle_kill_events", "battle_id_probes"]:
        cnt = pg.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"  {tbl}: {cnt}")
    print("=== SQLite ===")
    for tbl in ["battles", "battle_sides", "battle_guilds",
                "battle_participants", "battle_kill_events", "battle_id_probes"]:
        cnt = lite.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"  {tbl}: {cnt}")


PHASES = {
    "clear": phase_clear,
    "battles": phase_battles,
    "sides": phase_sides,
    "guilds": phase_guilds,
    "participants": phase_participants,
    "kills": phase_kills,
    "probes": phase_probes,
    "sequences": phase_sequences,
    "status": phase_status,
}

ORDER = ["clear", "battles", "sides", "guilds", "participants", "kills", "probes", "sequences"]


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in PHASES:
        print("Uso: python -m scripts.import_sqlite_battles <fase>")
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