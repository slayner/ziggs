"""guarda groupMemberCount das kills para detectar zona laranja.

Revision ID: z9e0f1a2b3c4
Revises: z8d9e0f1a2b3, a7c4e9f2b6d1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "z9e0f1a2b3c4"
down_revision: Union[str, tuple[str, str], None] = ("z8d9e0f1a2b3", "a7c4e9f2b6d1")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Histórico fica NULL e usa a estimativa conservadora. Só eventos novos
    # trazem o grupo autoritativo da API; não vale rebaixar milhões de eventos.
    op.add_column("player_kill_events", sa.Column("group_member_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("player_kill_events", "group_member_count")
