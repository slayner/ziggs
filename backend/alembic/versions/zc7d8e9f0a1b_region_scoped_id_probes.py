"""Escopa checkpoints de sondagem por região.

Revision ID: zc7d8e9f0a1b
Revises: za5b1c2d3e4f
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zc7d8e9f0a1b"
down_revision: Union[str, tuple[str, str], None] = "za5b1c2d3e4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("battle_id_probes", "kill_id_probes"):
        op.execute(sa.text(f"UPDATE {table} SET region = '__legacy__' WHERE region IS NULL"))
        op.alter_column(table, "region", existing_type=sa.String(length=16), nullable=False)
    op.drop_constraint("battle_id_probes_pkey", "battle_id_probes", type_="primary")
    op.create_primary_key("battle_id_probes_pkey", "battle_id_probes", ["region", "albion_id"])
    op.drop_constraint("kill_id_probes_pkey", "kill_id_probes", type_="primary")
    op.create_primary_key("kill_id_probes_pkey", "kill_id_probes", ["region", "albion_event_id"])


def downgrade() -> None:
    op.drop_constraint("battle_id_probes_pkey", "battle_id_probes", type_="primary")
    op.create_primary_key("battle_id_probes_pkey", "battle_id_probes", ["albion_id"])
    op.drop_constraint("kill_id_probes_pkey", "kill_id_probes", type_="primary")
    op.create_primary_key("kill_id_probes_pkey", "kill_id_probes", ["albion_event_id"])
    for table in ("battle_id_probes", "kill_id_probes"):
        op.alter_column(table, "region", existing_type=sa.String(length=16), nullable=True)
