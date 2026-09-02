"""reprocess_campaigns: total marcado por reprocess_reason (pra % de progresso)

Revision ID: a7d2f4c8b1e6
Revises: f3c1e9a4b6d2
Create Date: 2026-06-30 22:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a7d2f4c8b1e6'
down_revision: Union[str, None] = 'f3c1e9a4b6d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'reprocess_campaigns',
        sa.Column('reason', sa.String(length=64), nullable=False),
        sa.Column('total', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('reason'),
    )


def downgrade() -> None:
    op.drop_table('reprocess_campaigns')
