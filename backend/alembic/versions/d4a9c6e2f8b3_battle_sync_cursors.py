"""battle_sync_cursors: cursor de paginação do backfill histórico de batalhas

Revision ID: d4a9c6e2f8b3
Revises: c7e1a9d4f2b6
Create Date: 2026-06-24 13:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd4a9c6e2f8b3'
down_revision: Union[str, None] = 'c7e1a9d4f2b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'battle_sync_cursors',
        sa.Column('region', sa.String(16), primary_key=True),
        sa.Column('next_offset', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('done', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_table('battle_sync_cursors')
