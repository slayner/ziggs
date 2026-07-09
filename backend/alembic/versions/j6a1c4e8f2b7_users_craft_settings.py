"""users: craft_settings JSON column (preferências pessoais de craft).

Guarda preferências globais do usuário não ligadas a nenhuma guilda — hoje só
focus_efficiency por familyKey da calculadora de craft (ver
app/api/routes/craft.py). Coluna nova, sem dado anterior pra migrar.

Revision ID: j6a1c4e8f2b7
Revises: i5e1f7a2b8c3
Create Date: 2026-07-05 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.base import json_type

revision: str = 'j6a1c4e8f2b7'
down_revision: Union[str, None] = 'i5e1f7a2b8c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # default '{}' — postgres precisa de cast ::jsonb; sqlite guarda JSON como texto.
    settings_default = sa.text("'{}'::jsonb") if bind.dialect.name == "postgresql" else sa.text("'{}'")
    op.add_column(
        'users',
        sa.Column('craft_settings', json_type(), nullable=False, server_default=settings_default),
    )


def downgrade() -> None:
    op.drop_column('users', 'craft_settings')
