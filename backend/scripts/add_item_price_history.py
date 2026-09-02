"""
Cria a tabela item_price_history (histórico agregado de mercado capturado pelos
companions direto do jogo). Idempotente — create_all só cria o que falta.

    python -m scripts.add_item_price_history
"""
import sys
sys.path.insert(0, ".")

import app.models  # noqa: F401 — registra tudo no Base.metadata
from app.db import engine
from app.models.base import Base
from app.models.prices import ItemPriceHistory

Base.metadata.create_all(engine, tables=[ItemPriceHistory.__table__])
print("OK: tabela item_price_history criada (ou já existia).")
