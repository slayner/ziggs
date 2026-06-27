"""add gear_spells to game_roles

Revision ID: 1a2b3c4d5e6f
Revises: 0de970cc65bd
Create Date: 2026-06-21 21:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '1a2b3c4d5e6f'
down_revision: Union[str, None] = '0de970cc65bd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('game_roles', sa.Column('gear_spells', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('game_roles', 'gear_spells')
