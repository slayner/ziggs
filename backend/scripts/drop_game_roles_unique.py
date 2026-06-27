"""
Remove a unique constraint (guild_id, name) de game_roles.

SQLite não suporta ALTER TABLE DROP CONSTRAINT — recriamos a tabela.

    python -m scripts.drop_game_roles_unique
"""
import sys
sys.path.insert(0, ".")

from app.db import engine
from sqlalchemy import text

STMTS = [
    "PRAGMA foreign_keys = OFF",
    """
    CREATE TABLE game_roles_new (
        id          INTEGER NOT NULL PRIMARY KEY,
        guild_id    BIGINT NOT NULL REFERENCES guilds(id) ON DELETE CASCADE,
        name        VARCHAR(128) NOT NULL,
        weapon_id   INTEGER REFERENCES weapons(id) ON DELETE SET NULL,
        offhand     VARCHAR(128),
        helmet      VARCHAR(128),
        armor       VARCHAR(128),
        boots       VARCHAR(128),
        cape        VARCHAR(128),
        food        VARCHAR(128),
        abilities   TEXT,
        play_style  TEXT,
        obs         TEXT,
        build_items JSON NOT NULL DEFAULT '[]',
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
        color       VARCHAR(7),
        q_spell     VARCHAR(128),
        w_spell     VARCHAR(128),
        passive_spell VARCHAR(128),
        gear_spells TEXT
    )
    """,
    """
    INSERT INTO game_roles_new
        SELECT id, guild_id, name, weapon_id, offhand, helmet, armor, boots,
               cape, food, abilities, play_style, obs, build_items,
               created_at, updated_at, color, q_spell, w_spell, passive_spell, gear_spells
        FROM game_roles
    """,
    "DROP TABLE game_roles",
    "ALTER TABLE game_roles_new RENAME TO game_roles",
    "CREATE INDEX IF NOT EXISTS ix_game_roles_guild_id ON game_roles (guild_id)",
    "CREATE INDEX IF NOT EXISTS ix_game_roles_weapon_id ON game_roles (weapon_id)",
    "PRAGMA foreign_keys = ON",
]

with engine.connect() as conn:
    for stmt in STMTS:
        conn.execute(text(stmt))
    conn.commit()

print("OK: unique constraint (guild_id, name) removida de game_roles.")
