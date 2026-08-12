"""individual scan worker credentials

Revision ID: zk0f1a2b3c4d
Revises: zj9e0f1a2b3c
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "zk0f1a2b3c4d"
down_revision: Union[str, tuple[str, str], None] = "zj9e0f1a2b3c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scan_workers", sa.Column("api_token_hash", sa.String(64)))
    op.add_column("scan_workers", sa.Column(
        "credential_revoked", sa.Boolean(), nullable=False, server_default=sa.false()
    ))
    op.create_index("ix_scan_workers_api_token_hash", "scan_workers", ["api_token_hash"])


def downgrade() -> None:
    op.drop_index("ix_scan_workers_api_token_hash", table_name="scan_workers")
    op.drop_column("scan_workers", "credential_revoked")
    op.drop_column("scan_workers", "api_token_hash")
