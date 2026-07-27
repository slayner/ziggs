"""store player role preferences per composition.

Revision ID: z5a6b7c8d9e0f
Revises: z4a5b6c7d8e9f
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "z5a6b7c8d9e0f"
down_revision: Union[str, None] = "z4a5b6c7d8e9f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bigint = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    op.create_table(
        "comp_role_preferences",
        sa.Column("id", bigint, autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("comp_id", bigint, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("game_role_id", bigint, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["guild_id"], ["guilds.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["comp_id"], ["comps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["game_role_id"], ["game_roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("guild_id", "comp_id", "user_id", "game_role_id"),
    )
    op.create_index("ix_comp_role_preferences_guild_id", "comp_role_preferences", ["guild_id"])
    op.create_index("ix_comp_role_preferences_comp_id", "comp_role_preferences", ["comp_id"])
    op.create_index("ix_comp_role_preferences_user_id", "comp_role_preferences", ["user_id"])
    op.create_index("ix_comp_role_preferences_game_role_id", "comp_role_preferences", ["game_role_id"])


def downgrade() -> None:
    op.drop_index("ix_comp_role_preferences_game_role_id", table_name="comp_role_preferences")
    op.drop_index("ix_comp_role_preferences_user_id", table_name="comp_role_preferences")
    op.drop_index("ix_comp_role_preferences_comp_id", table_name="comp_role_preferences")
    op.drop_index("ix_comp_role_preferences_guild_id", table_name="comp_role_preferences")
    op.drop_table("comp_role_preferences")
