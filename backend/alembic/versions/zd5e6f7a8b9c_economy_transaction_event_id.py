"""economy_transactions: coluna event_id para ligar transações de
event_payout/event_deficit ao evento que as gerou.

Revision ID: zd5e6f7a8b9c
Revises: zc4d5e6f7g8b
"""
from alembic import op
import sqlalchemy as sa

revision = "zd5e6f7a8b9c"
down_revision = "zc4d5e6f7g8b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "economy_transactions",
        sa.Column("event_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_economy_transactions_event_id",
        "economy_transactions",
        "events",
        ["event_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_economy_transactions_event_id",
        "economy_transactions",
        ["event_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_economy_transactions_event_id", table_name="economy_transactions")
    op.drop_constraint("fk_economy_transactions_event_id", "economy_transactions", type_="foreignkey")
    op.drop_column("economy_transactions", "event_id")