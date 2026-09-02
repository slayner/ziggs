"""
Adiciona as colunas killer_guild_id/killer_guild_name/victim_guild_id/
victim_guild_name em player_kill_events — guarda a guilda de cada lado NO
MOMENTO do evento, pra reconstruir o histórico de guildas a partir das
datas reais de kill/morte (ver routes/players.py _guild_history).

    python -m scripts.add_kill_event_guilds
"""
import sys
sys.path.insert(0, ".")

from app.db import engine
from sqlalchemy import text

STMTS = [
    "ALTER TABLE player_kill_events ADD COLUMN killer_guild_id VARCHAR(64)",
    "ALTER TABLE player_kill_events ADD COLUMN killer_guild_name VARCHAR(255)",
    "ALTER TABLE player_kill_events ADD COLUMN victim_guild_id VARCHAR(64)",
    "ALTER TABLE player_kill_events ADD COLUMN victim_guild_name VARCHAR(255)",
]

with engine.connect() as conn:
    for stmt in STMTS:
        conn.execute(text(stmt))
    conn.commit()

print("OK: colunas de guilda por evento adicionadas em player_kill_events.")
