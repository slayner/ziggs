"""lootlog_submissions: logs anônimos do lootlogger por CTA (envio via bot-v2).

Revision ID: b9e4d2a1f8c3
Revises: a8f3c2d1e7b9
Create Date: 2026-07-03 00:00:00.000000

Sistema de lootlog anônimo (igual bot-v1, mas no site em área só-admin). O
logger envia o .csv do lootlogger pelo bot; o backend guarda as coletas aqui.
`logger_percent` da tab do CTA é separado pra loggers, dividido pelo peso
(silver_total da submissão). Sem co-relação/cópias (dropado).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b9e4d2a1f8c3'
down_revision: Union[str, None] = 'a8f3c2d1e7b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'lootlog_submissions',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), primary_key=True),
        sa.Column('guild_id', sa.BigInteger(), nullable=False),
        sa.Column('event_id', sa.BigInteger(), nullable=False),
        sa.Column('submitter_user_id', sa.BigInteger(), nullable=True),
        sa.Column('submitter_name', sa.String(255), nullable=True),
        sa.Column('file_name', sa.String(255), nullable=False),
        sa.Column('file_hash', sa.String(64), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('loot_rows', sa.JSON().with_variant(sa.JSON(), 'sqlite'), nullable=False),
        sa.Column('silver_total', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['guild_id'], ['guilds.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('guild_id', 'event_id', 'submitter_user_id',
                            name='uq_lootlog_guild_event_submitter'),
    )
    op.create_index('ix_lootlog_submissions_guild_id', 'lootlog_submissions', ['guild_id'])
    op.create_index('ix_lootlog_submissions_event_id', 'lootlog_submissions', ['event_id'])
    op.create_index('ix_lootlog_submissions_submitter_user_id', 'lootlog_submissions', ['submitter_user_id'])
    op.create_index('ix_lootlog_submissions_file_hash', 'lootlog_submissions', ['file_hash'])


def downgrade() -> None:
    op.drop_index('ix_lootlog_submissions_file_hash', table_name='lootlog_submissions')
    op.drop_index('ix_lootlog_submissions_submitter_user_id', table_name='lootlog_submissions')
    op.drop_index('ix_lootlog_submissions_event_id', table_name='lootlog_submissions')
    op.drop_index('ix_lootlog_submissions_guild_id', table_name='lootlog_submissions')
    op.drop_table('lootlog_submissions')