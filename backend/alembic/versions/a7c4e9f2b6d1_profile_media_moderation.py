"""profile media moderation queue and upload block.

Revision ID: a7c4e9f2b6d1
Revises: z6b7c8d9e0f1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7c4e9f2b6d1"
down_revision: Union[str, None] = "z6b7c8d9e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("profile_media_blocked_until", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "profile_media_submissions",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("discord_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "kind"),
        sa.UniqueConstraint("discord_message_id"),
    )
    op.create_index("ix_profile_media_submissions_user_id", "profile_media_submissions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_profile_media_submissions_user_id", table_name="profile_media_submissions")
    op.drop_table("profile_media_submissions")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("profile_media_blocked_until")
