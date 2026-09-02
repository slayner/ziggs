"""adiciona motivo opcional às transações de economia.

Revision ID: zx5c6d7e8f9a
Revises: zw4b5c6d7e8f
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zx5c6d7e8f9a"
down_revision: Union[str, None] = "zw4b5c6d7e8f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("economy_transactions", sa.Column("reason", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("economy_transactions", "reason")
