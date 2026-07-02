"""battles: fila genérica de reprocessamento (reprocess_reason)

Revision ID: f3c1e9a4b6d2
Revises: d8a4f2c9e1b7
Create Date: 2026-06-30 21:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f3c1e9a4b6d2'
down_revision: Union[str, None] = 'd8a4f2c9e1b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('battles', sa.Column('reprocess_reason', sa.String(length=64), nullable=True))
    op.create_index('ix_battles_reprocess_reason', 'battles', ['reprocess_reason'])


def downgrade() -> None:
    op.drop_index('ix_battles_reprocess_reason', table_name='battles')
    op.drop_column('battles', 'reprocess_reason')
