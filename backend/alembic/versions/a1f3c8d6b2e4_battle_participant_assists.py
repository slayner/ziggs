"""battle_participants: contador de assists (crédito sem ser o matador)

Revision ID: a1f3c8d6b2e4
Revises: c2a8e6f1b7d4
Create Date: 2026-06-29 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a1f3c8d6b2e4'
down_revision: Union[str, None] = 'c2a8e6f1b7d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('battle_participants', sa.Column('assists', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('battle_participants', 'assists')
