"""attribute item price history rows to companion installations.

Revision ID: z12d5e6f7a8b
Revises: z11c4d5e6f7a
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "z12d5e6f7a8b"
down_revision: Union[str, None] = "z11c4d5e6f7a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "source_install" not in {column["name"] for column in inspector.get_columns("item_prices")}:
        op.add_column("item_prices", sa.Column("source_install", sa.String(32), nullable=True))
    if "ix_item_prices_source_install" not in {
        index["name"] for index in inspector.get_indexes("item_prices")
    }:
        op.create_index("ix_item_prices_source_install", "item_prices", ["source_install"])


def downgrade() -> None:
    op.drop_index("ix_item_prices_source_install", table_name="item_prices")
    op.drop_column("item_prices", "source_install")
