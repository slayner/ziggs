"""kill_id_probes: memoria de sondagem do kill_sweeper.

Revision ID: a0b1c2d3e4f5
Revises: z12d5e6f7a8b
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a0b1c2d3e4f5"
down_revision: Union[str, tuple[str, str], None] = "z12d5e6f7a8b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kill_id_probes",
        sa.Column("albion_event_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("region", sa.String(length=16), nullable=True),
        sa.Column("probed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("albion_event_id"),
    )
    op.create_index("ix_kill_id_probes_status", "kill_id_probes", ["status"])


def downgrade() -> None:
    op.drop_index("ix_kill_id_probes_status", table_name="kill_id_probes")
    op.drop_table("kill_id_probes")
