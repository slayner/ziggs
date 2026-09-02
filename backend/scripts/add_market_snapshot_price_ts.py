"""
Adiciona a coluna price_ts em market_snapshot (quando o preço foi visto pela
última vez — base do filtro de frescor de 3 dias da aba de mercado).

    python -m scripts.add_market_snapshot_price_ts
"""
import sys
sys.path.insert(0, ".")

from sqlalchemy import text

from app.db import engine

with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE market_snapshot ADD COLUMN price_ts DATETIME"))
        conn.commit()
        print("OK: coluna price_ts adicionada.")
    except Exception as e:
        if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
            print("OK: coluna price_ts já existia.")
        else:
            raise
