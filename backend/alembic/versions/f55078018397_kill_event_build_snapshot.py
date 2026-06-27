"""battle_kill_events: snapshot da build do matador no momento exato da kill

Revision ID: f55078018397
Revises: 3c8e2f5a7b1d
Create Date: 2026-06-25 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f55078018397'
down_revision: Union[str, None] = '3c8e2f5a7b1d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('battle_kill_events', sa.Column('killer_equipment', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('battle_kill_events', 'killer_equipment')
