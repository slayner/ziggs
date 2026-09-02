"""scan_workers: tunnel metadata (label, country, endpoint, pubkey, ping_url)

Permite que a VPS se registre com os dados do tunnel WireGuard e apareça
automaticamente no /vps-manifest.json (companion + site), sem editar JSON a mão.

Revision ID: zb3c4d5e6f7a
Revises: za2b3c4d5e6f
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zb3c4d5e6f7a"
down_revision: str = "za2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scan_workers", sa.Column("vps_label", sa.String(64), nullable=True))
    op.add_column("scan_workers", sa.Column("vps_country", sa.String(64), nullable=True))
    op.add_column("scan_workers", sa.Column("vps_endpoint", sa.String(128), nullable=True))
    op.add_column("scan_workers", sa.Column("vps_server_pubkey", sa.String(128), nullable=True))
    op.add_column("scan_workers", sa.Column("vps_ping_url", sa.String(256), nullable=True))


def downgrade() -> None:
    op.drop_column("scan_workers", "vps_ping_url")
    op.drop_column("scan_workers", "vps_server_pubkey")
    op.drop_column("scan_workers", "vps_endpoint")
    op.drop_column("scan_workers", "vps_country")
    op.drop_column("scan_workers", "vps_label")