"""lootlog_submissions: adiciona raw_text (texto cru do .csv enviado)

Revision ID: e1f2a3b4c5d6
Revises: d0a1b2c3e4f5
Create Date: 2026-07-08 02:00:00.000000

A área admin do site passa a mostrar o texto cru do arquivo (idêntico ao .csv
do lootlogger, copiável) em vez do parse/valor. Submissões antigas ficam com
raw_text NULL (a coluna é nullable) — só as novas passam a guardar o texto.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd0a1b2c3e4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('lootlog_submissions') as b:
        b.add_column(sa.Column('raw_text', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('lootlog_submissions') as b:
        b.drop_column('raw_text')