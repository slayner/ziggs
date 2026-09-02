"""deleted_entities: is_deleted em AlbionPlayer + tabela deleted_profiles

Revision ID: e5b8d2f1a3c9
Revises: d4a9c6e2f8b3
Create Date: 2026-06-27 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e5b8d2f1a3c9'
down_revision: Union[str, None] = 'd4a9c6e2f8b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('albion_players', sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        'deleted_profiles',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('entity_type', sa.String(16), nullable=False),
        sa.Column('albion_id', sa.String(64), nullable=False, index=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('entity_type', 'albion_id'),
    )


def downgrade() -> None:
    op.drop_table('deleted_profiles')
    op.drop_column('albion_players', 'is_deleted')
