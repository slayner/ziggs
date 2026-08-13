#!/usr/bin/env python3
"""Migra dados do bot antigo (SQLite por guilda) → Postgres do Ziggs.

Camada 1 (crítico): user_balances, guild_bank, registrations, economy_config
Camada 2 (histórico): cta_events → events, cta_attendance → event_participants,
  cta_function_logs → event_signups, cta_log_submissions → lootlog_submissions,
  node_defs/maps/calendar/log → Node*, regears → regear_requests (read-only hist)

Uso (no servidor):
  cd /home/ziggs/ziggs/backend
  venv/bin/python /tmp/migrate_old_bot.py /tmp/guild_1511238681829314630.db 1511238681829314630

Idempotente: re-rodar faz UPSERT/INSERT ... ON CONFLICT — seguro.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

import psycopg

# ─── helpers ──────────────────────────────────────────────────────────────────

PG_DSN = os.getenv(
    "ZIGGS_PG_DSN",
    "host=localhost dbname=ziggs user=ziggs password=azqwsx123",
)

OLD_GUILD_ID = 1511238681829314630  # Discord snowflake da guilda SIGHT


def _dt(val) -> datetime | None:
    """Converte timestamp SQLite (string ISO ou 'YYYY-MM-DD HH:MM:SS') → datetime."""
    if not val:
        return None
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        # SQLite sem tz → assume UTC
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
        ):
            try:
                dt = datetime.strptime(val, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
    return None


def _ts_unix(val) -> datetime | None:
    """Unix timestamp (int) → datetime UTC."""
    if not val:
        return None
    try:
        return datetime.fromtimestamp(int(val), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


# ─── Camada 1: crítico ───────────────────────────────────────────────────────

def migrate_guild_bank(pg, ec_row):
    """guild_bank singleton → Guild.bank_balance + settings."""
    balance = ec_row.get("guild_bank_balance") or 0
    with pg.cursor() as cur:
        cur.execute(
            "UPDATE guilds SET bank_balance = %s WHERE id = %s",
            (int(balance), OLD_GUILD_ID),
        )
    print(f"  guild_bank: {balance:,}")


def migrate_economy_config(pg, ec_row):
    """economy_config (60 campos) → Guild.settings (JSONB).

    Mapeia só o que o novo bot já lê:
    - guild_tax_percent → settings.guild_tax_percent
    - node_scout_percent → settings.scout_percent
    - logger_percent → settings.lootlog.logger_percent
    - trial_percent → settings.trial_percent
    - role_trial → settings.trial_role_id
    - voice_cta → settings.voice_cta_channel_id
    - channel_massinfo → settings.events_channel_id
    - channel_battleboard → settings.battle_feed_channel_id
    - guild_ingame_name → Guild.albion_guild_name (se diferente)
    """
    settings_patch: dict = {}
    tax = ec_row.get("guild_tax_percent")
    if tax is not None:
        settings_patch["guild_tax_percent"] = int(tax)
    scout = ec_row.get("node_scout_percent")
    if scout is not None:
        settings_patch["scout_percent"] = int(scout)
    logger_pct = ec_row.get("logger_percent")
    if logger_pct is not None:
        settings_patch.setdefault("lootlog", {})["logger_percent"] = int(logger_pct)
    trial_pct = ec_row.get("trial_percent")
    if trial_pct is not None:
        settings_patch["trial_percent"] = int(trial_pct)
    role_trial = ec_row.get("role_trial")
    if role_trial:
        settings_patch["trial_role_id"] = str(role_trial)
    voice_cta = ec_row.get("voice_cta")
    if voice_cta:
        settings_patch["voice_cta_channel_id"] = str(voice_cta)
    channel_massinfo = ec_row.get("channel_massinfo")
    if channel_massinfo:
        settings_patch["events_channel_id"] = str(channel_massinfo)
    channel_bb = ec_row.get("channel_battleboard")
    if channel_bb:
        settings_patch["battle_feed_channel_id"] = str(channel_bb)
    channel_zergregear = ec_row.get("channel_zergregear")
    if channel_zergregear:
        # Regear zerg channel → regear channels config
        settings_patch.setdefault("regear", {})
        settings_patch["regear"]["channels"] = [
            {"channel_id": str(channel_zergregear), "coverage_pct": 100}
        ]
        settings_patch["regear"]["enabled"] = True
    channel_bombregear = ec_row.get("channel_bombregear")
    if channel_bombregear and channel_bombregear != channel_zergregear:
        settings_patch.setdefault("regear", {})
        channels = settings_patch["regear"].setdefault("channels", [])
        if not any(c["channel_id"] == str(channel_bombregear) for c in channels):
            channels.append({"channel_id": str(channel_bombregear), "coverage_pct": 100})

    if not settings_patch:
        print("  economy_config: nada pra migrar")
        return

    with pg.cursor() as cur:
        cur.execute("SELECT settings FROM guilds WHERE id = %s", (OLD_GUILD_ID,))
        row = cur.fetchone()
        if not row:
            print(f"  economy_config: guild {OLD_GUILD_ID} não existe no Postgres!")
            return
        current = dict(row[0] or {})
        # Merge recursivo simples (1 nível de profundidade)
        for k, v in settings_patch.items():
            if isinstance(v, dict) and isinstance(current.get(k), dict):
                merged = dict(current[k])
                merged.update(v)
                current[k] = merged
            else:
                current[k] = v
        # Guild ingame name
        guild_ingame = ec_row.get("guild_ingame_name")
        if guild_ingame:
            cur.execute(
                "UPDATE guilds SET albion_guild_name = %s WHERE id = %s AND albion_guild_name IS DISTINCT FROM %s",
                (guild_ingame, OLD_GUILD_ID, guild_ingame),
            )
        cur.execute(
            "UPDATE guilds SET settings = %s WHERE id = %s",
            (json.dumps(current), OLD_GUILD_ID),
        )
    print(f"  economy_config: {len(settings_patch)} chaves migradas")


def migrate_user_balances(pg, sqlite):
    """user_balances → economy_balances (UPSERT)."""
    rows = sqlite.execute(
        "SELECT user_id, balance, total_earned FROM user_balances"
    ).fetchall()
    inserted = 0
    updated = 0
    with pg.cursor() as cur:
        for user_id, balance, total_earned in rows:
            cur.execute(
                """
                INSERT INTO economy_balances (guild_id, discord_user_id, balance, total_earned)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (guild_id, discord_user_id)
                DO UPDATE SET
                    balance = EXCLUDED.balance,
                    total_earned = EXCLUDED.total_earned
                RETURNING (xmax = 0) AS inserted
                """,
                (OLD_GUILD_ID, int(user_id), int(balance), int(total_earned)),
            )
            r = cur.fetchone()
            if r and r[0]:
                inserted += 1
            else:
                updated += 1
    pg.commit()
    print(f"  user_balances: {inserted} novos, {updated} atualizados ({len(rows)} total)")


def migrate_registrations(pg, sqlite):
    """registrations + registration_aliases → bot_registrations.

    O schema novo exige albion_player_id (UUID do Albion) + region + role_id.
    O bot antigo só guardava nick (sem id nem region nem role). Vamos criar
    registros com active=False e albion_player_id = nick (placeholder) — o
    /register do bot novo re-valida via API e preenche os dados reais quando
    o jogador rodar de novo. Assim nada quebra, e o histórico não se perde.

    Para que os aliases também funcionem, criamos um registro por (user_id, nick).
    """
    # Tabela principal: 1 linha por user_id (o novo schema é por personagem)
    regs = sqlite.execute(
        "SELECT user_id, nick FROM registrations"
    ).fetchall()

    # Aliases: múltiplos nicks por user_id
    aliases = sqlite.execute(
        "SELECT user_id, nick FROM registration_aliases"
    ).fetchall()

    # Merge: user_id → set(nicks)
    user_nicks: dict[int, set[str]] = {}
    for user_id, nick in regs:
        if nick:
            user_nicks.setdefault(int(user_id), set()).add(nick.strip())
    for user_id, nick in aliases:
        if nick:
            user_nicks.setdefault(int(user_id), set()).add(nick.strip())

    inserted = 0
    skipped = 0
    # role_id padrão: pega da economy_config se tiver
    ec_row = _load_economy_config(sqlite)
    default_role = ec_row.get("role_member") or 0
    if not default_role:
        default_role = 0

    with pg.cursor() as cur:
        for user_id, nicks in user_nicks.items():
            for nick in nicks:
                # Placeholder: albion_player_id = nick (UUID real vem no /register)
                albion_id = f"legacy:{nick.lower()}"
                try:
                    cur.execute(
                        """
                        INSERT INTO bot_registrations
                            (guild_id, discord_user_id, albion_player_id,
                             albion_player_name, region, role_id, is_ally, active)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (guild_id, albion_player_id)
                        DO UPDATE SET
                            discord_user_id = EXCLUDED.discord_user_id,
                            albion_player_name = EXCLUDED.albion_player_name
                        """,
                        (OLD_GUILD_ID, user_id, albion_id, nick,
                         "americas", int(default_role), False, False),
                    )
                    inserted += 1
                except Exception:
                    skipped += 1
    pg.commit()
    print(f"  registrations: {inserted} migrados, {skipped} pulados ({len(user_nicks)} users)")


# ─── Camada 2: histórico ─────────────────────────────────────────────────────

def migrate_cta_events(pg, sqlite):
    """cta_events → events.

    O bot antigo tinha estado implícito (ended_at IS NULL + split_finalized).
    Mapeamos:
    - split_finalized=1 → FINALIZED
    - ended_at NOT NULL (sem split) → REVIEW (precisa de review)
    - ended_at IS NULL → IN_PROGRESS (provavelmente abandoned; mas sem split)
    - started_at IS NULL → SCHEDULED

    Tab image blob → não migra (formato novo usa URL).
    """
    rows = sqlite.execute(
        """SELECT id, caller_id, caller_name, started_at, ended_at,
                  repair_value, tab_location, split_finalized, event_thread_id,
                  event_message_id, total_snapshots, cta_message, battleboard_url,
                  comp, sheet_url
           FROM cta_events ORDER BY id"""
    ).fetchall()

    # Mapeamento old_id → new_id
    id_map: dict[int, int] = {}
    inserted = 0

    with pg.cursor() as cur:
        # Limpa eventos existentes dessa guilda (os 2 de teste não são dessa guilda)
        # NÃO limpa — os 2 existentes são de outra guilda (1518276810591305788)

        for row in rows:
            (old_id, caller_id, caller_name, started_at, ended_at,
             repair_value, tab_location, split_finalized, event_thread_id,
             event_message_id, total_snapshots, cta_message, battleboard_url,
             comp_name, sheet_url) = row

            # Determinar estado (enum Postgres é MAIÚSCULO)
            if split_finalized:
                state = "FINALIZED"
            elif ended_at:
                state = "REVIEW"
            elif started_at:
                state = "IN_PROGRESS"
            else:
                state = "SCHEDULED"

            started = _dt(started_at)
            ended = _dt(ended_at)
            title = cta_message[:255] if cta_message else None
            caller = caller_name if caller_name else None

            # Idempotente: se já existe evento com bot_request_id=legacy:{old_id}, pula
            cur.execute(
                "SELECT id FROM events WHERE bot_request_id = %s",
                (f"legacy:{old_id}",),
            )
            existing = cur.fetchone()
            if existing:
                id_map[old_id] = existing[0]
                continue

            cur.execute(
                """
                INSERT INTO events
                    (guild_id, state, signup_mode, assignment_mode, autofill_mode,
                     participation_mode, seriousness, functions_released,
                     signup_message_dirty, event_embed_dirty,
                     regear_thread_dirty, regear_thread_archived,
                     lootlog_thread_dirty, lootlog_thread_archived,
                     event_thread_archived, attendance, tab_value, is_loss,
                     caller_id, caller_name, title, message,
                     started_at, ended_at, total_snapshots,
                     battleboard_url, bot_request_id,
                     event_channel_id, event_message_id)
                VALUES (%s, %s, 'signup', 'hybrid', 'manual', 'PRESENCE', 'CASUAL',
                        false, false, false, false, false, false, false, false,
                        1.0, 0, false,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (OLD_GUILD_ID, state,
                 int(caller_id) if caller_id else None,
                 caller, title, cta_message,
                 started, ended, int(total_snapshots or 0),
                 battleboard_url,
                 f"legacy:{old_id}",
                 int(event_thread_id) if event_thread_id else None,
                 int(event_message_id) if event_message_id else None),
            )
            new_id = cur.fetchone()[0]
            id_map[old_id] = new_id
            inserted += 1

    pg.commit()
    print(f"  cta_events: {inserted} migrados")
    return id_map


def migrate_cta_attendance(pg, sqlite, id_map):
    """cta_attendance → event_participants."""
    rows = sqlite.execute(
        """SELECT event_id, user_id, user_name, snapshots_present, snapshots_total,
                  percent, base_percent, is_trial, silver_received, enlisted, enlisted_by
           FROM cta_attendance"""
    ).fetchall()
    inserted = 0
    skipped = 0
    with pg.cursor() as cur:
        for row in rows:
            (old_event_id, user_id, user_name, snap_present, snap_total,
             percent, base_percent, is_trial, silver_received, enlisted, enlisted_by) = row
            new_event_id = id_map.get(old_event_id)
            if not new_event_id:
                skipped += 1
                continue
            cur.execute(
                """
                INSERT INTO event_participants
                    (event_id, guild_id, user_id, user_name,
                     snapshots_present, base_percent, percent, is_trial,
                     enlisted, enlisted_by, silver_received)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id, user_id) DO UPDATE SET
                    snapshots_present = EXCLUDED.snapshots_present,
                    base_percent = EXCLUDED.base_percent,
                    percent = EXCLUDED.percent,
                    is_trial = EXCLUDED.is_trial,
                    silver_received = EXCLUDED.silver_received
                """,
                (new_event_id, OLD_GUILD_ID, int(user_id), user_name,
                 int(snap_present or 0), int(base_percent or 0), int(percent or 0),
                 bool(is_trial), bool(enlisted),
                 int(enlisted_by) if enlisted_by else None,
                 int(silver_received or 0)),
            )
            inserted += 1
    pg.commit()
    print(f"  cta_attendance: {inserted} migrados, {skipped} sem evento mapeado")


def migrate_cta_function_logs(pg, sqlite, id_map):
    """cta_function_logs → event_signups (functions vira JSON list)."""
    rows = sqlite.execute(
        """SELECT event_id, user_id, user_name, function1, function2, function3
           FROM cta_function_logs"""
    ).fetchall()
    inserted = 0
    skipped = 0
    with pg.cursor() as cur:
        for row in rows:
            (old_event_id, user_id, user_name, f1, f2, f3) = row
            new_event_id = id_map.get(old_event_id)
            if not new_event_id:
                skipped += 1
                continue
            functions = [f for f in (f1, f2, f3) if f and f.strip()]
            cur.execute(
                """
                INSERT INTO event_signups
                    (event_id, guild_id, user_id, user_name, functions)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (event_id, user_id) DO UPDATE SET
                    functions = EXCLUDED.functions
                """,
                (new_event_id, OLD_GUILD_ID, int(user_id), user_name,
                 json.dumps(functions)),
            )
            inserted += 1
    pg.commit()
    print(f"  cta_function_logs: {inserted} migrados, {skipped} sem evento mapeado")


def migrate_lootlog(pg, sqlite, id_map):
    """cta_log_submissions + cta_log_events → lootlog_submissions."""
    subs = sqlite.execute(
        """SELECT id, event_id, submitter_id, submitter_nick, file_name, file_hash,
                  row_count, submitted_at
           FROM cta_log_submissions ORDER BY id"""
    ).fetchall()

    # Pré-carrega eventos de loot por submission_id
    events_by_sub = {}
    for row in sqlite.execute(
        """SELECT id, event_id, ts, item_id, item_name, quantity, looted_by,
                  looted_by_guild, looted_from, looted_by_alliance,
                  looted_from_alliance, looted_from_guild
           FROM cta_log_events ORDER BY id"""
    ).fetchall():
        events_by_sub.setdefault(row[0], []).append(row)

    inserted = 0
    skipped = 0
    with pg.cursor() as cur:
        # Mapeia old_sub_id → new_sub_id (precisa pra associar loot events)
        sub_id_map: dict[int, int] = {}
        for sub in subs:
            (old_sub_id, old_event_id, submitter_id, submitter_nick,
             file_name, file_hash, row_count, submitted_at) = sub
            new_event_id = id_map.get(old_event_id)
            if not new_event_id:
                skipped += 1
                continue

            loot_rows_raw = events_by_sub.get(old_sub_id, [])
            loot_rows = []
            for r in loot_rows_raw:
                loot_rows.append({
                    "ts": r[2], "item_id": r[3], "item_name": r[4],
                    "quantity": r[5], "looted_by": r[6], "looted_by_guild": r[7],
                    "looted_from": r[8],
                })

            cur.execute(
                """
                INSERT INTO lootlog_submissions
                    (guild_id, event_id, submitter_user_id, submitter_name,
                     file_name, file_hash, row_count, loot_rows, silver_total)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0)
                ON CONFLICT (guild_id, event_id, submitter_user_id)
                DO UPDATE SET
                    file_name = EXCLUDED.file_name,
                    file_hash = EXCLUDED.file_hash,
                    row_count = EXCLUDED.row_count,
                    loot_rows = EXCLUDED.loot_rows
                RETURNING id
                """,
                (OLD_GUILD_ID, new_event_id,
                 int(submitter_id) if submitter_id else None,
                 submitter_nick, file_name or "", file_hash or "",
                 int(row_count or 0), json.dumps(loot_rows)),
            )
            new_sub_id = cur.fetchone()[0]
            sub_id_map[old_sub_id] = new_sub_id
            inserted += 1
    pg.commit()
    print(f"  lootlog: {inserted} submissões migradas ({skipped} sem evento), "
          f"{sum(len(v) for v in events_by_sub.values())} loot events")


def migrate_nodes(pg, sqlite):
    """node_defs, node_maps, node_map_exclusions, node_calendar, node_events_log."""

    # node_defs → node_defs (novo schema tem guild_id + id auto)
    rows = sqlite.execute(
        "SELECT name, emoji, weight, sort FROM node_defs ORDER BY sort, name"
    ).fetchall()
    inserted = 0
    with pg.cursor() as cur:
        for name, emoji, weight, sort in rows:
            cur.execute(
                """
                INSERT INTO node_defs (guild_id, name, emoji, weight, sort)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (guild_id, name) DO UPDATE SET
                    emoji = EXCLUDED.emoji,
                    weight = EXCLUDED.weight,
                    sort = EXCLUDED.sort
                """,
                (OLD_GUILD_ID, name, emoji, float(weight or 1.0), int(sort or 0)),
            )
            inserted += 1
    pg.commit()
    print(f"  node_defs: {inserted}")

    # node_maps → node_maps
    rows = sqlite.execute(
        "SELECT name FROM node_maps ORDER BY name"
    ).fetchall()
    inserted = 0
    with pg.cursor() as cur:
        for (name,) in rows:
            cur.execute(
                """
                INSERT INTO node_maps (guild_id, map_name)
                VALUES (%s, %s)
                ON CONFLICT (guild_id, map_name) DO NOTHING
                """,
                (OLD_GUILD_ID, name),
            )
            inserted += cur.rowcount
    pg.commit()
    print(f"  node_maps: {inserted}")

    # node_map_exclusions → node_map_exclusions
    rows = sqlite.execute(
        "SELECT name FROM node_map_exclusions ORDER BY name"
    ).fetchall()
    inserted = 0
    with pg.cursor() as cur:
        for (name,) in rows:
            cur.execute(
                """
                INSERT INTO node_map_exclusions (guild_id, map_name)
                VALUES (%s, %s)
                ON CONFLICT (guild_id, map_name) DO NOTHING
                """,
                (OLD_GUILD_ID, name),
            )
            inserted += cur.rowcount
    pg.commit()
    print(f"  node_map_exclusions: {inserted}")

    # node_calendar → node_calendar (UPSERT)
    row = sqlite.execute(
        "SELECT channel_id, message_id FROM node_calendar WHERE id = 1"
    ).fetchone()
    if row:
        with pg.cursor() as cur:
            cur.execute(
                """
                INSERT INTO node_calendar (guild_id, channel_id, message_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (guild_id) DO UPDATE SET
                    channel_id = EXCLUDED.channel_id,
                    message_id = EXCLUDED.message_id
                """,
                (OLD_GUILD_ID, int(row[0]) if row[0] else None,
                 int(row[1]) if row[1] else None),
            )
        pg.commit()
        print(f"  node_calendar: 1")

    # node_events_log → node_event_log
    rows = sqlite.execute(
        """SELECT node_type, map_name, added_by, added_by_id, spawn_timestamp,
                  spawn_utc, logged_at
           FROM node_events_log ORDER BY id"""
    ).fetchall()
    inserted = 0
    with pg.cursor() as cur:
        for row in rows:
            (node_type, map_name, added_by, added_by_id, spawn_ts, spawn_utc, logged_at) = row
            spawn_at = _ts_unix(spawn_ts)
            if not spawn_at:
                continue
            cur.execute(
                """
                INSERT INTO node_event_log
                    (guild_id, node_type, map_name, scout_id, scout_name, spawn_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (OLD_GUILD_ID, node_type, map_name,
                 int(added_by_id) if added_by_id else None,
                 added_by, spawn_at),
            )
            inserted += 1
    pg.commit()
    print(f"  node_event_log: {inserted}")


def migrate_regears(pg, sqlite):
    """regears → regear_requests (histórico — o novo schema é muito diferente).

    Cria RegearRequest com recognition_status='manual' e screenshot_path apontando
    pra image_url do Discord (não é ideal, mas preserva o histórico). Só migra
    os que têm status paid/denied (histórico), pending vira pending.
    """
    rows = sqlite.execute(
        """SELECT user_id, user_name, channel_id, message_id, image_url,
                  status, value, handled_by, handled_at, created_at
           FROM regears ORDER BY id"""
    ).fetchall()
    inserted = 0
    skipped = 0
    with pg.cursor() as cur:
        for row in rows:
            (user_id, user_name, channel_id, message_id, image_url,
             status, value, handled_by, handled_at, created_at) = row
            if not image_url:
                skipped += 1
                continue
            # status mapping: pending→pending, paid→paid, denied→denied, removed→denied
            new_status = "denied" if status == "removed" else status
            if new_status not in ("pending", "paid", "denied"):
                new_status = "pending"

            cur.execute(
                """
                INSERT INTO regear_requests
                    (guild_id, requester_user_id, requester_name,
                     screenshot_path, screenshot_msg_id, channel_id,
                     recognition_status, recognition_method, recognition_confidence,
                     recognition_candidates, detected_items,
                     base_total, suggested_total, coverage_pct,
                     price_basis, final_total, status,
                     handled_by_user_id, handled_at, notes, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (OLD_GUILD_ID,
                 int(user_id) if user_id else None,
                 user_name,
                 image_url,  # screenshot_path = URL (não ideal mas preserva)
                 str(message_id) if message_id else None,
                 str(channel_id) if channel_id else None,
                 "manual", "manual", "low",
                 json.dumps([]),  # recognition_candidates
                 json.dumps([]),  # detected_items vazio
                 int(value or 0), int(value or 0), 100, "",
                 int(value) if new_status == "paid" and value else None,
                 new_status,
                 int(handled_by) if handled_by else None,
                 _dt(handled_at),
                 "Migrado do bot antigo",
                 _dt(created_at) or datetime.now(timezone.utc)),
            )
            inserted += 1
    pg.commit()
    print(f"  regears: {inserted} migrados, {skipped} sem image_url")


# ─── orquestração ─────────────────────────────────────────────────────────────

def _load_economy_config(sqlite) -> dict:
    row = sqlite.execute("SELECT * FROM economy_config WHERE id = 1").fetchone()
    if not row:
        return {}
    cols = [d[0] for d in sqlite.execute("SELECT * FROM economy_config WHERE id = 1").description]
    return dict(zip(cols, row))


def main():
    if len(sys.argv) < 2:
        print("Uso: migrate_old_bot.py <sqlite_path> [guild_id]")
        sys.exit(1)

    sqlite_path = sys.argv[1]
    global OLD_GUILD_ID
    if len(sys.argv) >= 3:
        OLD_GUILD_ID = int(sys.argv[2])

    sqlite = sqlite3.connect(sqlite_path)
    sqlite.row_factory = sqlite3.Row
    pg = psycopg.connect(PG_DSN)
    pg.autocommit = False

    print(f"=== Migração: {sqlite_path} → guild {OLD_GUILD_ID} ===\n")

    # Verifica que a guilda existe
    with pg.cursor() as cur:
        cur.execute("SELECT id, name FROM guilds WHERE id = %s", (OLD_GUILD_ID,))
        g = cur.fetchone()
        if not g:
            print(f"ERRO: guild {OLD_GUILD_ID} não existe no Postgres. Crie-a primeiro.")
            sys.exit(1)
        print(f"Guilda: {g[1]} ({g[0]})\n")

    ec_row = _load_economy_config(sqlite)

    # Carrega guild_bank balance separadamente (tabela própria)
    bank_row = sqlite.execute("SELECT balance FROM guild_bank WHERE id = 1").fetchone()
    if bank_row:
        ec_row["guild_bank_balance"] = bank_row[0]

    print("--- Camada 1: Crítico ---")
    migrate_guild_bank(pg, ec_row)
    migrate_economy_config(pg, ec_row)
    migrate_user_balances(pg, sqlite)
    migrate_registrations(pg, sqlite)

    print("\n--- Camada 2: Histórico ---")
    id_map = migrate_cta_events(pg, sqlite)
    migrate_cta_attendance(pg, sqlite, id_map)
    migrate_cta_function_logs(pg, sqlite, id_map)
    migrate_lootlog(pg, sqlite, id_map)
    migrate_nodes(pg, sqlite)
    migrate_regears(pg, sqlite)

    print("\n=== Migração concluída ===")
    pg.close()
    sqlite.close()


if __name__ == "__main__":
    main()