"""companion_scan_tasks.claimed_by — 1 range por instalação, não por processo

Revision ID: s5d0e3f8c9b2
Revises: r4c9d2e7b8a1
Create Date: 2026-07-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 's5d0e3f8c9b2'
down_revision: Union[str, None] = 'r4c9d2e7b8a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('companion_scan_tasks',
                  sa.Column('claimed_by', sa.String(length=64), nullable=True))
    op.create_index('ix_companion_scan_tasks_claimed_by', 'companion_scan_tasks',
                    ['claimed_by'])


def downgrade() -> None:
    op.drop_index('ix_companion_scan_tasks_claimed_by',
                  table_name='companion_scan_tasks')
    op.drop_column('companion_scan_tasks', 'claimed_by')
