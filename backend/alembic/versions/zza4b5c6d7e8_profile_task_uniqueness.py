"""unicidade de tarefas de perfil por jogador

Revision ID: zza4b5c6d7e8
Revises: zz3a4b5c6d7e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zza4b5c6d7e8"
down_revision: Union[str, None] = "zz3a4b5c6d7e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("uq_scan_work_tasks_active_page", table_name="scan_work_tasks")
    op.create_index(
        "uq_scan_work_tasks_active_page",
        "scan_work_tasks",
        ["region", "feed_type", "page_offset"],
        unique=True,
        postgresql_where=sa.text("feed_type <> 'profile' AND status IN ('pending', 'claimed', 'reported')"),
    )
    op.create_index(
        "uq_scan_work_tasks_active_profile",
        "scan_work_tasks",
        ["region", "feed_type", "subject_id"],
        unique=True,
        postgresql_where=sa.text("feed_type = 'profile' AND status IN ('pending', 'claimed', 'reported')"),
    )


def downgrade() -> None:
    op.drop_index("uq_scan_work_tasks_active_profile", table_name="scan_work_tasks")
    op.drop_index("uq_scan_work_tasks_active_page", table_name="scan_work_tasks")
    op.create_index(
        "uq_scan_work_tasks_active_page",
        "scan_work_tasks",
        ["region", "feed_type", "page_offset"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'claimed', 'reported')"),
    )
