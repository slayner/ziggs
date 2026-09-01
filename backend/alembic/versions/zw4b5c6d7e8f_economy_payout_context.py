"""contexto detalhado dos payouts de evento no histórico de economia.

Revision ID: zw4b5c6d7e8f
Revises: zz2a3b4c5d6e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "zw4b5c6d7e8f"
down_revision: Union[str, None] = "zz2a3b4c5d6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "economy_transactions",
        sa.Column("payout_context", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.alter_column("economy_transactions", "payout_context", server_default=None)


def downgrade() -> None:
    op.drop_column("economy_transactions", "payout_context")
