"""deduplicate bot commands after lost HTTP responses.

Revision ID: z6b7c8d9e0f1
Revises: z5a6b7c8d9e0f
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "z6b7c8d9e0f1"
down_revision: Union[str, None] = "z5a6b7c8d9e0f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("events") as batch:
        batch.add_column(sa.Column("bot_request_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("last_voice_snapshot_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_unique_constraint("uq_events_bot_request_id", ["bot_request_id"])
    with op.batch_alter_table("economy_transactions") as batch:
        batch.add_column(sa.Column("request_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("undo_request_id", sa.String(length=64), nullable=True))
        batch.create_unique_constraint("uq_economy_transactions_request_id", ["request_id"])
        batch.create_unique_constraint("uq_economy_transactions_undo_request_id", ["undo_request_id"])
    with op.batch_alter_table("node_events") as batch:
        batch.add_column(sa.Column("bot_request_id", sa.String(length=64), nullable=True))
        batch.create_unique_constraint("uq_node_events_bot_request_id", ["bot_request_id"])


def downgrade() -> None:
    with op.batch_alter_table("node_events") as batch:
        batch.drop_constraint("uq_node_events_bot_request_id", type_="unique")
        batch.drop_column("bot_request_id")
    with op.batch_alter_table("economy_transactions") as batch:
        batch.drop_constraint("uq_economy_transactions_undo_request_id", type_="unique")
        batch.drop_constraint("uq_economy_transactions_request_id", type_="unique")
        batch.drop_column("undo_request_id")
        batch.drop_column("request_id")
    with op.batch_alter_table("events") as batch:
        batch.drop_constraint("uq_events_bot_request_id", type_="unique")
        batch.drop_column("last_voice_snapshot_at")
        batch.drop_column("bot_request_id")
