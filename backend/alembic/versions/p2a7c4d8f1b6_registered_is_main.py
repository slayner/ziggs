"""registered_characters.is_main: conta principal do usuário (perfil v2, WS D)

Revision ID: p2a7c4d8f1b6
Revises: o1e6a9b4d8f3
Create Date: 2026-07-13 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'p2a7c4d8f1b6'
down_revision: Union[str, None] = 'o1e6a9b4d8f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'registered_characters',
        sa.Column('is_main', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Backfill: personagem mais antigo de cada usuário vira main — todo usuário
    # existente já sai com exatamente 1 main. TRUE funciona em SQLite (≥3.23)
    # e Postgres.
    op.execute("""
        UPDATE registered_characters SET is_main = TRUE WHERE id IN (
            SELECT MIN(id) FROM registered_characters GROUP BY user_id
        )
    """)


def downgrade() -> None:
    op.drop_column('registered_characters', 'is_main')
