"""item_price_history/market_snapshot: adiciona coluna region (mercados por servidor)

Revision ID: r4c9d2e7b8a1
Revises: q3b8e5c1a2d7
Create Date: 2026-07-18 00:00:00.000000

Mercados do Albion sao separados por servidor (americas/europe/asia) e os
cluster ids se REPETEM entre regioes. Sem `region` na chave unica, dados de
regioes diferentes colidiam no mesmo bucket (item_price_history) ou mesma
linha (market_snapshot). O modelo ja tinha a coluna, mas a migration original
(item_price_history) e a criacao runtime (market_snapshot) nao a incluíram.

SQLite nao tem ALTER TABLE DROP/ADD CONSTRAINT — recriamos as tabelas a mão
(criar nova, copiar, dropar, renomear), padrão SQLite pra qualquer mudanca de
constraint. As constraints anonimas (sem nome) sao recriadas explicitamente
com nome pra migrations futuras conseguirem referencia-las.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'r4c9d2e7b8a1'
down_revision: Union[str, None] = 'q3b8e5c1a2d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── item_price_history: add region + unique com region ────────────────
    op.execute("""
        CREATE TABLE item_price_history_new (
            id INTEGER NOT NULL PRIMARY KEY,
            item_id VARCHAR(128) NOT NULL,
            albion_id INTEGER NOT NULL,
            region VARCHAR(16) NOT NULL DEFAULT 'americas',
            quality INTEGER NOT NULL,
            location VARCHAR(48) NOT NULL,
            timescale INTEGER NOT NULL,
            bucket_ts BIGINT NOT NULL,
            item_count INTEGER NOT NULL,
            silver_amount INTEGER NOT NULL,
            recorded_at DATETIME NOT NULL,
            CONSTRAINT uq_item_price_history_region
                UNIQUE (item_id, region, quality, location, timescale, bucket_ts)
        )
    """)
    op.execute("""
        INSERT INTO item_price_history_new
            (id, item_id, albion_id, region, quality, location, timescale,
             bucket_ts, item_count, silver_amount, recorded_at)
        SELECT id, item_id, albion_id, 'americas', quality, location, timescale,
               bucket_ts, item_count, silver_amount, recorded_at
        FROM item_price_history
    """)
    op.execute("DROP TABLE item_price_history")
    op.execute("ALTER TABLE item_price_history_new RENAME TO item_price_history")
    op.create_index('ix_item_price_history_item_id', 'item_price_history', ['item_id'])

    # ── market_snapshot: idem ─────────────────────────────────────────────
    op.execute("""
        CREATE TABLE market_snapshot_new (
            id INTEGER NOT NULL PRIMARY KEY,
            item_id VARCHAR(128) NOT NULL,
            region VARCHAR(16) NOT NULL DEFAULT 'americas',
            price INTEGER NOT NULL,
            change_pct FLOAT NOT NULL,
            demand INTEGER NOT NULL,
            source VARCHAR(8) NOT NULL,
            price_ts DATETIME,
            updated_at DATETIME NOT NULL,
            CONSTRAINT uq_market_snapshot_region UNIQUE (item_id, region)
        )
    """)
    op.execute("""
        INSERT INTO market_snapshot_new
            (id, item_id, region, price, change_pct, demand, source,
             price_ts, updated_at)
        SELECT id, item_id, 'americas', price, change_pct, demand, source,
               price_ts, updated_at
        FROM market_snapshot
    """)
    op.execute("DROP TABLE market_snapshot")
    op.execute("ALTER TABLE market_snapshot_new RENAME TO market_snapshot")
    op.create_index('ix_market_snapshot_item_id', 'market_snapshot', ['item_id'])


def downgrade() -> None:
    # Reverso: volta sem region e sem nome nas constraints (estado original).
    op.execute("""
        CREATE TABLE item_price_history_old (
            id INTEGER NOT NULL PRIMARY KEY,
            item_id VARCHAR(128) NOT NULL,
            albion_id INTEGER NOT NULL,
            quality INTEGER NOT NULL,
            location VARCHAR(48) NOT NULL,
            timescale INTEGER NOT NULL,
            bucket_ts BIGINT NOT NULL,
            item_count INTEGER NOT NULL,
            silver_amount INTEGER NOT NULL,
            recorded_at DATETIME NOT NULL,
            UNIQUE (item_id, quality, location, timescale, bucket_ts)
        )
    """)
    op.execute("""
        INSERT INTO item_price_history_old
            (id, item_id, albion_id, quality, location, timescale,
             bucket_ts, item_count, silver_amount, recorded_at)
        SELECT id, item_id, albion_id, quality, location, timescale,
               bucket_ts, item_count, silver_amount, recorded_at
        FROM item_price_history
    """)
    op.execute("DROP TABLE item_price_history")
    op.execute("ALTER TABLE item_price_history_old RENAME TO item_price_history")
    op.create_index('ix_item_price_history_item_id', 'item_price_history', ['item_id'])

    op.execute("""
        CREATE TABLE market_snapshot_old (
            id INTEGER NOT NULL PRIMARY KEY,
            item_id VARCHAR(128) NOT NULL,
            price INTEGER NOT NULL,
            change_pct FLOAT NOT NULL,
            demand INTEGER NOT NULL,
            source VARCHAR(8) NOT NULL,
            updated_at DATETIME NOT NULL,
            price_ts DATETIME,
            UNIQUE (item_id)
        )
    """)
    op.execute("""
        INSERT INTO market_snapshot_old
            (id, item_id, price, change_pct, demand, source, price_ts, updated_at)
        SELECT id, item_id, price, change_pct, demand, source, price_ts, updated_at
        FROM market_snapshot
    """)
    op.execute("DROP TABLE market_snapshot")
    op.execute("ALTER TABLE market_snapshot_old RENAME TO market_snapshot")
    op.create_index('ix_market_snapshot_item_id', 'market_snapshot', ['item_id'])