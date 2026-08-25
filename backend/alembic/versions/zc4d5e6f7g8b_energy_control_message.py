"""energy control message table

Revision ID: zc4d5e6f7g8b
Revises: zb3c4d5e6f7a
"""
from alembic import op
import sqlalchemy as sa

from app.models.base import Snowflake

revision = "zc4d5e6f7g8b"
down_revision = "zb3c4d5e6f7a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "energy_control_messages",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", Snowflake(), nullable=True),
        sa.Column("message_id", Snowflake(), nullable=True),
        sa.ForeignKeyConstraint(["guild_id"], ["guilds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("guild_id"),
    )


def downgrade() -> None:
    op.drop_table("energy_control_messages")