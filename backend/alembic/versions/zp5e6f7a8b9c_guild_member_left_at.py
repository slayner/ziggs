"""guild_member_left_at: track when a member left the Discord guild

Revision ID: zp5e6f7a8b9c
Revises: zo4d5e6f7a8b
Create Date: 2026-08-14 06:00:00.000000

Adiciona `guild_members.left_at` (nullable). Setado quando o membro sai do
servidor Discord (on_member_remove). Limpo quando volta (on_member_join).
Usado pelo loop de confisc: após 7 dias com left_at setado, o saldo do
membro é transferido pro banco da guilda.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'zp5e6f7a8b9c'
down_revision: Union[str, None] = 'zo4d5e6f7a8b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('guild_members') as b:
        b.add_column(sa.Column('left_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('guild_members') as b:
        b.drop_column('left_at')