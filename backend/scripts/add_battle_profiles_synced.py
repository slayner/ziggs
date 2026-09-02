"""
Adiciona a coluna profiles_synced em battles — fila pro profile_warmer saber
quais batalhas já tiveram os perfis dos participantes pré-carregados (ver
app/services/profile_warmer.py). Todas as linhas existentes nascem com False,
de propósito: o warmer processa o histórico inteiro já registrado, não só
batalhas novas.

    python -m scripts.add_battle_profiles_synced
"""
import sys
sys.path.insert(0, ".")

from app.db import engine
from sqlalchemy import text

with engine.connect() as conn:
    conn.execute(text("ALTER TABLE battles ADD COLUMN profiles_synced BOOLEAN NOT NULL DEFAULT 0"))
    conn.commit()

print("OK: coluna profiles_synced adicionada em battles (todas as linhas como pendente).")
