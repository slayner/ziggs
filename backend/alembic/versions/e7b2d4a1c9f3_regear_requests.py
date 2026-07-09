"""regear_requests: fila de regear por screenshot do canal de regear do Discord.

Revision ID: e7b2d4a1c9f3
Revises: f1a9c3e7b5d2
Create Date: 2026-07-03 00:00:00.000000

Um pedido por screenshot postada no canal de regear. pending → paid/denied/removed.
`paid` debita guild.bank_balance. `final_total` (editado) sobrescreve `suggested_total`.
`detected_items` é JSON (lista de dicts com item_id/quality/slot/eligible/prices).
Idempotência por (guild_id, screenshot_msg_id) — o bot reenvia o mesmo attachment.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.base import json_type

revision: str = 'e7b2d4a1c9f3'
down_revision: Union[str, None] = 'f1a9c3e7b5d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    items_default = sa.text("'[]'::jsonb") if bind.dialect.name == "postgresql" else sa.text("'[]'")
    op.create_table(
        'regear_requests',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  primary_key=True),
        sa.Column('guild_id', sa.BigInteger(),
                  sa.ForeignKey('guilds.id', ondelete='CASCADE'), nullable=False),
        sa.Column('requester_user_id', sa.BigInteger(), nullable=True),
        sa.Column('requester_name', sa.String(255), nullable=True),
        sa.Column('screenshot_path', sa.Text(), nullable=False),
        sa.Column('screenshot_msg_id', sa.String(64), nullable=True),
        sa.Column('ocr_name', sa.String(255), nullable=True),
        sa.Column('albion_event_id', sa.String(64), nullable=True),
        sa.Column('death_timestamp', sa.DateTime(timezone=True), nullable=True),
        sa.Column('recognition_status', sa.String(16), server_default='manual', nullable=False),
        sa.Column('detected_items', json_type(), server_default=items_default, nullable=False),
        sa.Column('base_total', sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  server_default='0', nullable=False),
        sa.Column('suggested_total', sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  server_default='0', nullable=False),
        sa.Column('coverage_pct', sa.Integer(), server_default='100', nullable=False),
        sa.Column('price_basis', sa.String(128), server_default='', nullable=False),
        sa.Column('final_total', sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column('status', sa.String(16), server_default='pending', nullable=False),
        sa.Column('handled_by_user_id', sa.BigInteger(), nullable=True),
        sa.Column('handled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_regear_requests_guild_id', 'regear_requests', ['guild_id'])
    op.create_index('ix_regear_requests_status', 'regear_requests', ['status'])
    op.create_index('ix_regear_requests_msg', 'regear_requests', ['guild_id', 'screenshot_msg_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_regear_requests_msg', table_name='regear_requests')
    op.drop_index('ix_regear_requests_status', table_name='regear_requests')
    op.drop_index('ix_regear_requests_guild_id', table_name='regear_requests')
    op.drop_table('regear_requests')