"""battle_kill_events: snapshot do inventário (itens não equipados) de quem matou e quem morreu

Revision ID: b2d5f9a3c7e1
Revises: a1c4e8f2b9d3
Create Date: 2026-06-24 11:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b2d5f9a3c7e1'
down_revision: Union[str, None] = 'a1c4e8f2b9d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('battle_kill_events', sa.Column('killer_inventory', sa.JSON(), nullable=True))
    op.add_column('battle_kill_events', sa.Column('victim_inventory', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('battle_kill_events', 'victim_inventory')
    op.drop_column('battle_kill_events', 'killer_inventory')
