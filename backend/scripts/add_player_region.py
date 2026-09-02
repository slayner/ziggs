"""
Adiciona a coluna `region` em albion_players (jogadores existentes, todos
vistos só pelo host Americas até essa mudança, ficam "americas").

SQLite aceita ALTER TABLE ADD COLUMN direto (diferente de dropar constraint,
que precisa recriar a tabela — ver drop_game_roles_unique.py).

    python -m scripts.add_player_region
"""
import sys
sys.path.insert(0, ".")

from app.db import engine
from sqlalchemy import text

with engine.connect() as conn:
    conn.execute(text(
        "ALTER TABLE albion_players ADD COLUMN region VARCHAR(16) NOT NULL DEFAULT 'americas'"
    ))
    conn.commit()

print("OK: coluna region adicionada em albion_players (default 'americas').")
