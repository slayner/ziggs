"""player_count_snapshots: histórico de jogadores ativos por região (gráfico do dashboard)

Revision ID: b6f4a1e8c3d7
Revises: a7d2f4c8b1e6
Create Date: 2026-07-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b6f4a1e8c3d7'
down_revision: Union[str, None] = 'a7d2f4c8b1e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _bigint():
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        'player_count_snapshots',
        sa.Column('id', _bigint(), primary_key=True),
        sa.Column('region', sa.String(length=16), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_player_count_snapshots_region', 'player_count_snapshots', ['region'])
    op.create_index('ix_player_count_snapshots_recorded_at', 'player_count_snapshots', ['recorded_at'])


def downgrade() -> None:
    op.drop_index('ix_player_count_snapshots_recorded_at', table_name='player_count_snapshots')
    op.drop_index('ix_player_count_snapshots_region', table_name='player_count_snapshots')
    op.drop_table('player_count_snapshots')
