"""
Adiciona a coluna found_by em battles — nick do jogador cujo companion
descobriu a batalha (agradecimento na página pública). Sem backfill:
batalhas antigas ficam NULL.

    python -m scripts.add_battle_found_by
"""
import sys
sys.path.insert(0, ".")

from sqlalchemy import text

from app.db import engine

with engine.connect() as conn:
    conn.execute(text("ALTER TABLE battles ADD COLUMN found_by VARCHAR(64)"))
    conn.commit()

print("OK: coluna found_by adicionada em battles.")
