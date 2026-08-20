"""energy portal foundation: balances, immutable ledger, whitelist

Revision ID: zy9a0b1c2d3e
Revises: zx4a5b6c7d8e
"""
from alembic import op
import sqlalchemy as sa


revision = "zy9a0b1c2d3e"
down_revision = "zx4a5b6c7d8e"
branch_labels = None
depends_on = None


def _bigint():
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "energy_balances",
        sa.Column("id", _bigint(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), sa.ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column("balance", _bigint(), nullable=False, server_default="0"),
        sa.UniqueConstraint("guild_id", "discord_user_id", name="uq_energy_balance_member"),
    )
    op.create_index("ix_energy_balances_guild_id", "energy_balances", ["guild_id"])
    op.create_index("ix_energy_balances_discord_user_id", "energy_balances", ["discord_user_id"])

    op.create_table(
        "energy_entries",
        sa.Column("id", _bigint(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), sa.ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="log"),
        sa.Column("ts", sa.String(32), nullable=False),
        sa.Column("player", sa.String(64), nullable=False, server_default=""),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("amount", _bigint(), nullable=False),
        sa.Column("actor_discord_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_energy_entries_guild_id", "energy_entries", ["guild_id"])
    op.create_index("ix_energy_entries_discord_user_id", "energy_entries", ["discord_user_id"])
    # Dedup da log do jogo — mesma chave UNIQUE(ts, player, amount) do bot
    # legado, escopada por guilda e só pra kind='log' (ajustes manuais ficam fora).
    op.create_index(
        "uq_energy_entry_log_dedup", "energy_entries",
        ["guild_id", "ts", "player", "amount"], unique=True,
        sqlite_where=sa.text("kind = 'log'"),
        postgresql_where=sa.text("kind = 'log'"),
    )

    op.create_table(
        "energy_whitelist",
        sa.Column("id", _bigint(), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), sa.ForeignKey("guilds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column("added_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("guild_id", "discord_user_id", name="uq_energy_whitelist_member"),
    )
    op.create_index("ix_energy_whitelist_guild_id", "energy_whitelist", ["guild_id"])
    op.create_index("ix_energy_whitelist_discord_user_id", "energy_whitelist", ["discord_user_id"])


def downgrade() -> None:
    op.drop_index("ix_energy_whitelist_discord_user_id", table_name="energy_whitelist")
    op.drop_index("ix_energy_whitelist_guild_id", table_name="energy_whitelist")
    op.drop_table("energy_whitelist")
    op.drop_index("uq_energy_entry_log_dedup", table_name="energy_entries")
    op.drop_index("ix_energy_entries_discord_user_id", table_name="energy_entries")
    op.drop_index("ix_energy_entries_guild_id", table_name="energy_entries")
    op.drop_table("energy_entries")
    op.drop_index("ix_energy_balances_discord_user_id", table_name="energy_balances")
    op.drop_index("ix_energy_balances_guild_id", table_name="energy_balances")
    op.drop_table("energy_balances")
