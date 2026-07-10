"""albion_players.lifetime_statistics (blob) — persiste coleta por recurso etc.

Revision ID: m9b4d7e2a1c8
Revises: f4b2d8e6a1c9
Create Date: 2026-07-09 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = 'm9b4d7e2a1c8'
down_revision: Union[str, None] = 'f4b2d8e6a1c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json():
    return JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    with op.batch_alter_table('albion_players') as b:
        b.add_column(sa.Column('lifetime_statistics', _json(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('albion_players') as b:
        b.drop_column('lifetime_statistics')