"""regear_requests: coluna recognition_attempts (fila de retry de reconhecimento).

Revision ID: a8f3c2d1e7b9
Revises: e7b2d4a1c9f3
Create Date: 2026-07-03 00:00:00.000000

A API de killboard cai com frequência; a fila de retry re-roda o reconhecimento
nos pedidos que ficaram "manual" por falha de API. `recognition_attempts` capa o
número de tentativas pra não martelar a API indefinidamente.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a8f3c2d1e7b9'
down_revision: Union[str, None] = 'e7b2d4a1c9f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'regear_requests',
        sa.Column('recognition_attempts', sa.Integer(), server_default='0', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('regear_requests', 'recognition_attempts')