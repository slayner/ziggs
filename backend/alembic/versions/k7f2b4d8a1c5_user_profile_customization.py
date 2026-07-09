"""users: profile customization columns (theme/avatar/banner).

Desbloqueado por ter um RegisteredCharacter (verificação via /claims) — ver
app/services/user_profile.py. Colunas novas, sem dado anterior pra migrar.

Revision ID: k7f2b4d8a1c5
Revises: j6a1c4e8f2b7
Create Date: 2026-07-06 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'k7f2b4d8a1c5'
down_revision: Union[str, None] = 'j6a1c4e8f2b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('profile_theme', sa.String(length=16), nullable=True))
    op.add_column('users', sa.Column('profile_avatar_path', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('profile_banner_path', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'profile_banner_path')
    op.drop_column('users', 'profile_avatar_path')
    op.drop_column('users', 'profile_theme')
