"""Reset de dados de bot/eventos/registers do ziggs.db (dev).

Apaga: events + filhos (participants, signups, deaths, verification,
assignments, state_transitions, loot_entries), regear_requests, node_event_log,
bot_registrations, registered_characters, character_claims.
Limpa do Guild.settings as configs de bot (canais, roles, ping, massinfo).
Mantém: guilds, users, comps, battles, preços, economy, e as configs gerais
do Guild (lootsplit_mode, bot_language, lootlog, signup_*, ally_*, regear
feature, albion_guild_region).

Rodar com o backend parado (evita lock/cache stale). Backup em ziggs.db.bak.
"""
import json
import sqlite3
import sys

DB = "ziggs.db"

EVENT_CHILDREN = [
    "event_participants", "event_signups", "event_deaths",
    "event_verification_steps", "event_assignments", "event_state_transitions",
    "event_loot_entries",
]
REGISTERS = ["bot_registrations", "registered_characters", "character_claims"]
REGEAR = ["regear_requests"]
NODE_EVT = ["node_event_log"]

# Chaves de bot a limpar do Guild.settings (canais, roles, ping, massinfo).
BOT_SETTING_KEYS = [
    "events_channel_id", "event_review_channel_id", "regear_thread_channel_id",
    "voice_cta_channel_id", "nodes_calendar_channel_id", "logs_channel_id",
    "command_roles", "disabled_commands", "event_role_gates",
    "massinfo_message_id", "register_role_id", "trial_percent", "trial_role_id",
    "events_ping_triggers", "pending_ping_triggers", "logs_last_sent_id",
]


def count(cur, t):
    try:
        return cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    except sqlite3.OperationalError:
        return None


def main():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    cur = c.cursor()

    before = {t: count(cur, t) for t in
              EVENT_CHILDREN + ["events"] + REGISTERS + REGEAR + NODE_EVT}

    try:
        c.execute("BEGIN")
        for t in EVENT_CHILDREN + REGISTERS + REGEAR + NODE_EVT:
            cur.execute(f'DELETE FROM "{t}"')
        cur.execute("DELETE FROM events")

        # Limpa configs de bot do Guild.settings (JSON column).
        for gid, s in cur.execute("SELECT id, settings FROM guilds").fetchall():
            d = json.loads(s) if s else {}
            changed = False
            for k in BOT_SETTING_KEYS:
                if k in d:
                    d.pop(k)
                    changed = True
            if changed:
                cur.execute(
                    "UPDATE guilds SET settings=? WHERE id=?",
                    (json.dumps(d, ensure_ascii=False), gid),
                )
        c.commit()
    except Exception as e:
        c.execute("ROLLBACK")
        print(f"ERRO, rollback: {e}", file=sys.stderr)
        sys.exit(1)

    after = {t: count(cur, t) for t in
             EVENT_CHILDREN + ["events"] + REGISTERS + REGEAR + NODE_EVT}

    print("Reset OK.\n")
    print(f"{'tabela':30} {'antes':>6}  {'depois':>6}")
    print("-" * 46)
    for t in EVENT_CHILDREN + ["events"] + REGISTERS + REGEAR + NODE_EVT:
        print(f"{t:30} {str(before[t]):>6}  {str(after[t]):>6}")

    # Mostra chaves de settings remanescentes por guilda.
    print("\nGuild.settings restantes:")
    for gid, s in cur.execute("SELECT id, settings FROM guilds").fetchall():
        d = json.loads(s) if s else {}
        print(f"  guild {gid}: {sorted(d.keys())}")

    c.close()


if __name__ == "__main__":
    main()