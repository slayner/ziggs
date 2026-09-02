"""guild_albion_links: coluna verified (worker de verificação de fundo).

Revision ID: zd2e3f4a5b6c
Revises: zc1d2e3f4a5b
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zd2e3f4a5b6c"
down_revision: Union[str, None] = "zc1d2e3f4a5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "guild_albion_links",
        sa.Column("verified", sa.Boolean(), nullable=True, server_default=sa.text("NULL")),
    )


def downgrade() -> None:
    op.drop_column("guild_albion_links", "verified")