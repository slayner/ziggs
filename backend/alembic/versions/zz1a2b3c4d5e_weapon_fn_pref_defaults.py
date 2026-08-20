"""weapon_fn_preferences: add server_default to created_at/updated_at

Revision ID: zz1a2b3c4d5e
Revises: zy9a0b1c2d3e

A tabela weapon_fn_preferences foi criada (zw3a4b5c6d7f) sem server_default
nas colunas created_at/updated_at — o TimestampMixin declara server_default=
func.now() no modelo, mas o migration DDL não incluiu. O SQLAlchemy confia
que o banco vai preencher (server_default), mas sem o DEFAULT no Postgres,
o INSERT envia NULL e falha com NotNullViolation — bloqueando todo signup
que cria uma nova preferência weapon+fn.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zz1a2b3c4d5e"
down_revision: Union[str, None] = "zy9a0b1c2d3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind is not None and bind.dialect.name == "sqlite":
        sa_now = sa.text("(datetime('now'))")
    else:
        sa_now = sa.func.now()
    op.alter_column(
        "weapon_fn_preferences", "created_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa_now,
    )
    op.alter_column(
        "weapon_fn_preferences", "updated_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa_now,
    )


def downgrade() -> None:
    op.alter_column(
        "weapon_fn_preferences", "updated_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=None,
    )
    op.alter_column(
        "weapon_fn_preferences", "created_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=None,
    )