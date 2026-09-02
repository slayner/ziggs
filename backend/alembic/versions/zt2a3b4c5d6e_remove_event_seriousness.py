"""remove unused event seriousness

Revision ID: zt2a3b4c5d6e
Revises: zs1a2b3c4d5e
Create Date: 2026-08-16 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zt2a3b4c5d6e"
down_revision: Union[str, None] = "zs1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("events", "seriousness")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE event_seriousness")


def downgrade() -> None:
    seriousness = sa.Enum("casual", "serious", name="event_seriousness")
    if op.get_bind().dialect.name == "postgresql":
        seriousness.create(op.get_bind())
    op.add_column("events", sa.Column("seriousness", seriousness, nullable=False, server_default="casual"))
