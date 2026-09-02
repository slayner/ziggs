"""simplify_event_states: 8→4 estados + captura de node em review (scout payout).

Revision ID: i5e1a8b2c4d6
Revises: h4d0e6b8f9a1
Create Date: 2026-07-04 00:00:00.000000

Fluxo novo: scheduled → in_progress → review → finalized (+ cancelled/deleted).
`review` substitui definition/verification/waiting e faz só verificações básicas
(tab value + captura de node). Tipo do evento passa a ser obrigatório na criação;
eventos em trânsito sem tipo viram `lootsplit`.

NodeEventLog ganha event_id/captured/sold_value p/ ligar o node ao evento na
captura e financiar o scout payout (NodeDef.weight × sold_value, pool separado).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'i5e1a8b2c4d6'
down_revision: Union[str, None] = 'h4d0e6b8f9a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"

    # 1. enum event_state: adiciona 'REVIEW' (Postgres). SQLite é VARCHAR — só data.
    #    SQLAlchemy Enum armazena o NAME do membro (uppercase), então os labels do
    #    tipo PG são os names. Os valores DEFINITION/VERIFICATION/WAITING ficam no
    #    tipo PG (remover exige recriar o tipo — custo alto, sem ganho: nenhuma row
    #    os referencia após o data migration). ponytail: add só o novo, deixar dangling.
    if pg:
        op.execute("ALTER TYPE event_state ADD VALUE IF NOT EXISTS 'REVIEW'")

    # 2. Data migration de eventos em trânsito: DEFINITION/VERIFICATION/WAITING → REVIEW
    op.execute(
        "UPDATE events SET state='REVIEW' "
        "WHERE state IN ('DEFINITION','VERIFICATION','WAITING')"
    )
    # In-flight sem tipo vira LOOTSPLIT. Terminais (FINALIZED/CANCELLED/DELETED)
    # mantêm NULL — não passam por review e não precisam de default.
    op.execute(
        "UPDATE events SET type='LOOTSPLIT' "
        "WHERE type IS NULL AND state NOT IN ('FINALIZED','CANCELLED','DELETED')"
    )
    # event_state_transitions: histórico de audit — deixar como está (linhas com
    # estados removidos continuam válidas como histórico; nenhuma query as filtra).

    # 3. NodeEventLog: event_id (FK SET NULL, index) + captured + sold_value.
    #    batch_alter_table: SQLite não suporta ADD CONSTRAINT via ALTER — batch
    #    recria a tabela (copy-and-move). Postgres roda direto (recopy=False).
    with op.batch_alter_table('node_event_log') as b:
        b.add_column(sa.Column(
            'event_id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.ForeignKey('events.id', ondelete='SET NULL',
                          name='fk_node_event_log_event_id'),
            nullable=True,
        ))
        b.add_column(sa.Column(
            'captured', sa.Boolean(), server_default='false', nullable=False,
        ))
        b.add_column(sa.Column(
            'sold_value', sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            server_default='0', nullable=False,
        ))
        b.create_index('ix_node_event_log_event_id', ['event_id'])


def downgrade() -> None:
    with op.batch_alter_table('node_event_log') as b:
        b.drop_index('ix_node_event_log_event_id')
        b.drop_column('sold_value')
        b.drop_column('captured')
        b.drop_column('event_id')
    # enum: não remove 'review' (Postgres não suporta DROP VALUE sem recriar o tipo).