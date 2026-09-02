"""regear_request_channel_id: canal de origem da screenshot

Revision ID: h4d0e6b8f9a1
Revises: g3b9c2d5e6f7
Create Date: 2026-07-05 00:00:00.000000

Adiciona `regear_requests.channel_id` — a guilda agora pode ter vários canais
de regear, cada um com sua própria % de cobertura (regear_config.RegearSettings
.channels). Guardar o canal por request permite a fila de retry reaplicar a %
certa mesmo se a config mudar depois.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'h4d0e6b8f9a1'
down_revision: Union[str, None] = 'g3b9c2d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('regear_requests') as b:
        b.add_column(sa.Column('channel_id', sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('regear_requests') as b:
        b.drop_column('channel_id')
