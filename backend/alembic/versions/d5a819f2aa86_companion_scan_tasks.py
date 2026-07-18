"""companion_scan_tasks

Revision ID: d5a819f2aa86
Revises: p2a7c4d8f1b6
Create Date: 2026-07-14 11:40:15.794187
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd5a819f2aa86'
down_revision: Union[str, None] = 'p2a7c4d8f1b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('companion_scan_tasks',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('region', sa.String(length=16), nullable=False),
        sa.Column('battle_id_start', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('battle_id_end', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('claim_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('found_count', sa.Integer(), nullable=False),
        sa.Column('missing_count', sa.Integer(), nullable=False),
        sa.Column('error_count', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_companion_scan_tasks_region', 'companion_scan_tasks', ['region'])
    op.create_index('ix_companion_scan_tasks_priority', 'companion_scan_tasks', ['priority'])
    op.create_index('ix_companion_scan_tasks_status', 'companion_scan_tasks', ['status'])
    op.create_index('ix_companion_scan_tasks_claim_expires_at', 'companion_scan_tasks', ['claim_expires_at'])


def downgrade() -> None:
    op.drop_index('ix_companion_scan_tasks_claim_expires_at', table_name='companion_scan_tasks')
    op.drop_index('ix_companion_scan_tasks_status', table_name='companion_scan_tasks')
    op.drop_index('ix_companion_scan_tasks_priority', table_name='companion_scan_tasks')
    op.drop_index('ix_companion_scan_tasks_region', table_name='companion_scan_tasks')
    op.drop_table('companion_scan_tasks')