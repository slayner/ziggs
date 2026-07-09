"""regear_event_link: RegearRequest.event_id + Event.regear_thread_dirty

Revision ID: l8a3c5e7d9b2
Revises: k7f2b4d8a1c5
Create Date: 2026-07-07 00:00:00.000000

Vincula screenshots de regear ao evento (thread de regear criada no andamento).
- `regear_requests.event_id` FK → events.id ON DELETE SET NULL: o regear fica
  pendente na fila geral mesmo se o evento for apagado (só perde o tag).
- `events.regear_thread_dirty`: outbox flag — o bot cria a thread de regear no
  canal dedicado quando o evento entra em IN_PROGRESS, e limpa via
  /bot/events/.../regear-thread-synced (seta regear_thread_id).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'l8a3c5e7d9b2'
down_revision: Union[str, None] = 'k7f2b4d8a1c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('regear_requests') as b:
        b.add_column(sa.Column('event_id', sa.BigInteger(), nullable=True))
        b.create_foreign_key(
            'fk_regear_requests_event_id_events',
            'events',
            ['event_id'],
            ['id'],
            ondelete='SET NULL',
        )
        b.create_index('ix_regear_requests_event_id', ['event_id'], unique=False)

    with op.batch_alter_table('events') as b:
        b.add_column(sa.Column(
            'regear_thread_dirty', sa.Boolean(), nullable=False, server_default=sa.text('0')
        ))


def downgrade() -> None:
    with op.batch_alter_table('events') as b:
        b.drop_column('regear_thread_dirty')

    with op.batch_alter_table('regear_requests') as b:
        b.drop_index('ix_regear_requests_event_id')
        b.drop_constraint('fk_regear_requests_event_id_events', type_='foreignkey')
        b.drop_column('event_id')