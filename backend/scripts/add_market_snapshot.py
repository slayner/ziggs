"""
Cria a tabela market_snapshot (resumo de mercado pré-computado pelo varredor
de fundo). Idempotente.

    python -m scripts.add_market_snapshot
"""
import sys
sys.path.insert(0, ".")

import app.models  # noqa: F401 — registra tudo no Base.metadata
from app.db import engine
from app.models.base import Base
from app.models.prices import MarketSnapshot

Base.metadata.create_all(engine, tables=[MarketSnapshot.__table__])
print("OK: tabela market_snapshot criada (ou já existia).")
