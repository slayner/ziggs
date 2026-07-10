"""dashboard_cache table + albion_players.refresh_requested_at

Revision ID: f4b2d8e6a1c9
Revises: e1f2a3b4c5d6
Create Date: 2026-07-09 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = 'f4b2d8e6a1c9'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json():
    return JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        'dashboard_cache',
        sa.Column('key', sa.String(length=64), primary_key=True),
        sa.Column('payload', _json(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    with op.batch_alter_table('albion_players') as b:
        b.add_column(sa.Column('refresh_requested_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('albion_players') as b:
        b.drop_column('refresh_requested_at')
    op.drop_table('dashboard_cache')
