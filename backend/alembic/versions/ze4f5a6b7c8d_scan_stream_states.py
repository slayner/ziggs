"""scan stream circuit state and fairness

Revision ID: ze4f5a6b7c8d
Revises: zd3e4f5a6b7c
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ze4f5a6b7c8d"
down_revision: Union[str, tuple[str, str], None] = "zd3e4f5a6b7c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    table = op.create_table(
        "scan_stream_states",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("region", sa.String(16), nullable=False),
        sa.Column("feed_type", sa.String(16), nullable=False),
        sa.Column("circuit_state", sa.String(16), nullable=False, server_default="closed"),
        sa.Column("consecutive_errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("opened_until", sa.DateTime(timezone=True)),
        sa.Column("last_claimed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("region", "feed_type", name="uq_scan_stream_state"),
    )
    op.create_index("ix_scan_stream_states_region", "scan_stream_states", ["region"])
    op.create_index("ix_scan_stream_states_feed_type", "scan_stream_states", ["feed_type"])
    op.create_index("ix_scan_stream_states_circuit_state", "scan_stream_states", ["circuit_state"])
    op.create_index("ix_scan_stream_states_opened_until", "scan_stream_states", ["opened_until"])
    op.create_index("ix_scan_stream_states_last_claimed_at", "scan_stream_states", ["last_claimed_at"])
    op.bulk_insert(table, [
        {"region": region, "feed_type": feed_type}
        for region in ("americas", "europe", "asia")
        for feed_type in ("battles", "kills")
    ])


def downgrade() -> None:
    op.drop_table("scan_stream_states")
