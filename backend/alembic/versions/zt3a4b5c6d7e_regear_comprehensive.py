"""regear abrangente: origem, pagamento e snapshots.

Revision ID: zt3a4b5c6d7e
Revises: zt2a3b4c5d6e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "zt3a4b5c6d7e"
down_revision: Union[str, None] = "zt2a3b4c5d6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        UPDATE guilds
        SET settings = settings
            - 'regear'
            - 'regear_thread_channel_id'
            - 'regear_channel_id'
            - 'regear_coverage_pct'
            - 'regear_enabled_categories'
            - 'regear_disabled_items'
            - 'regear_require_approval'
            - 'regear_approver_role_ids'
    """))
    with op.batch_alter_table("regear_requests") as batch:
        batch.add_column(sa.Column("source_message_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("source_attachment_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("source_attachment_index", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("payment_message_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("payment_message_channel_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("requester_role_ids_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
        batch.add_column(sa.Column("event_participation_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch.add_column(sa.Column("economy_transaction_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_regear_economy_transaction", "economy_transactions", ["economy_transaction_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_regear_source_message_id", ["source_message_id"], unique=False)
        batch.create_index("ix_regear_payment_message_id", ["payment_message_id"], unique=False)
        batch.create_index("ix_regear_economy_transaction_id", ["economy_transaction_id"], unique=False)
        batch.create_unique_constraint("uq_regear_source_attachment", ["guild_id", "source_message_id", "source_attachment_id"])
        batch.create_unique_constraint("uq_regear_source_attachment_index", ["guild_id", "source_message_id", "source_attachment_index"])


def downgrade() -> None:
    with op.batch_alter_table("regear_requests") as batch:
        batch.drop_constraint("uq_regear_source_attachment_index", type_="unique")
        batch.drop_constraint("uq_regear_source_attachment", type_="unique")
        batch.drop_index("ix_regear_economy_transaction_id")
        batch.drop_index("ix_regear_payment_message_id")
        batch.drop_index("ix_regear_source_message_id")
        batch.drop_constraint("fk_regear_economy_transaction", type_="foreignkey")
        batch.drop_column("economy_transaction_id")
        batch.drop_column("event_participation_snapshot")
        batch.drop_column("requester_role_ids_snapshot")
        batch.drop_column("payment_message_channel_id")
        batch.drop_column("payment_message_id")
        batch.drop_column("source_attachment_index")
        batch.drop_column("source_attachment_id")
        batch.drop_column("source_message_id")
