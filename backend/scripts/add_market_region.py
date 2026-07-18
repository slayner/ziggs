"""
Adiciona a coluna `region` em market_snapshot e item_price_history — mercados
do Albion são separados por servidor (americas/europe/asia) e os cluster ids
se repetem entre regiões.

Estado atual: coluna criada com default 'americas'. O varredor/endpoints ainda
não são region-aware (todo dado cai em 'americas' por ora) — esta migração só
garante que o schema bate com os models e o backend não quebra em bancos
existentes. Idempotente.

    python -m scripts.add_market_region
"""
import sys
sys.path.insert(0, ".")

from sqlalchemy import text

from app.db import engine


def _add_column(conn, table: str) -> None:
    try:
        conn.execute(text(
            f"ALTER TABLE {table} ADD COLUMN region VARCHAR(16) NOT NULL DEFAULT 'americas'"
        ))
        conn.commit()
        print(f"OK: coluna region adicionada em {table}.")
    except Exception as e:
        if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
            print(f"OK: coluna region já existia em {table}.")
        else:
            raise


with engine.connect() as conn:
    _add_column(conn, "market_snapshot")
    _add_column(conn, "item_price_history")
