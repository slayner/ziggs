"""craft carts shareable links.

Revision ID: zo4d5e6f7a8b
Revises: zd2e3f4a5b6c
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "zo4d5e6f7a8b"
down_revision: Union[str, None] = "zd2e3f4a5b6c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bigint = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    op.create_table(
        "craft_carts",
        sa.Column("id", bigint, autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_craft_carts_code", "craft_carts", ["code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_craft_carts_code", table_name="craft_carts")
    op.drop_table("craft_carts")
