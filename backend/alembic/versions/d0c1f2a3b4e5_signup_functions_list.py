"""signup_functions_list: function_1/2/3 -> functions (JSON list)

Migra o storage de signups de 3 colunas (function_1/2/3) pra uma única coluna
JSON `functions` (lista ordenada por preferência). Motivo: o máximo de builds
que um jogador pode escolher ao se inscrever passa a ser configurável pela
guilda (settings signup_max_builds), podendo passar de 3.

Revision ID: d0c1f2a3b4e5
Revises: c4a2e8f1b9d3
Create Date: 2026-07-02 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.base import json_type

revision: str = 'd0c1f2a3b4e5'
down_revision: Union[str, None] = 'c4a2e8f1b9d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # default '[]' — postgres precisa de cast ::jsonb; sqlite guarda JSON como texto.
    fns_default = sa.text("'[]'::jsonb") if bind.dialect.name == "postgresql" else sa.text("'[]'")
    op.add_column(
        'event_signups',
        sa.Column('functions', json_type(), nullable=False, server_default=fns_default),
    )

    # Backfill portável: monta a lista ordenada das 3 colunas antigas (function_1
    # = preferida). Usa sa.table pra o bind processor do json_type serializar a
    # lista em ambos os dialectos.
    signups = sa.table(
        'event_signups',
        sa.column('id', sa.BigInteger),
        sa.column('function_1', sa.String),
        sa.column('function_2', sa.String),
        sa.column('function_3', sa.String),
        sa.column('functions', json_type()),
    )
    rows = bind.execute(
        sa.select(signups.c.id, signups.c.function_1, signups.c.function_2, signups.c.function_3)
    ).all()
    for r in rows:
        fns = [f for f in (r.function_1, r.function_2, r.function_3) if f]
        bind.execute(signups.update().where(signups.c.id == r.id).values(functions=fns))

    op.drop_column('event_signups', 'function_1')
    op.drop_column('event_signups', 'function_2')
    op.drop_column('event_signups', 'function_3')


def downgrade() -> None:
    bind = op.get_bind()
    op.add_column('event_signups', sa.Column('function_1', sa.String(128), nullable=True))
    op.add_column('event_signups', sa.Column('function_2', sa.String(128), nullable=True))
    op.add_column('event_signups', sa.Column('function_3', sa.String(128), nullable=True))

    signups = sa.table(
        'event_signups',
        sa.column('id', sa.BigInteger),
        sa.column('function_1', sa.String),
        sa.column('function_2', sa.String),
        sa.column('function_3', sa.String),
        sa.column('functions', json_type()),
    )
    rows = bind.execute(sa.select(signups.c.id, signups.c.functions)).all()
    for r in rows:
        fns = list(r.functions or [])
        bind.execute(signups.update().where(signups.c.id == r.id).values(
            function_1=fns[0] if len(fns) > 0 else None,
            function_2=fns[1] if len(fns) > 1 else None,
            function_3=fns[2] if len(fns) > 2 else None,
        ))
    op.drop_column('event_signups', 'functions')