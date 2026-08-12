"""guild_albion_links: suporte a multi-guilda por servidor Discord.

Revision ID: zc1d2e3f4a5b
Revises: zn3c4d5e6f7a
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zc1d2e3f4a5b"
down_revision: Union[str, None] = "zn3c4d5e6f7a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "guild_albion_links",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("albion_guild_id", sa.String(length=64), nullable=False),
        sa.Column("albion_guild_name", sa.String(length=255), nullable=False),
        sa.Column("region", sa.String(length=16), nullable=False),
        sa.Column("alliance_id", sa.String(length=64)),
        sa.Column("alliance_name", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("guild_id", "albion_guild_id", name="uq_guild_albion_links_guild_albion"),
    )
    op.create_index("ix_guild_albion_links_guild_id", "guild_albion_links", ["guild_id"])
    op.create_index("ix_guild_albion_links_albion_guild_id", "guild_albion_links", ["albion_guild_id"])

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, albion_guild_id, albion_guild_name, albion_alliance_id, albion_alliance_name "
        "FROM guilds WHERE albion_guild_id IS NOT NULL AND albion_guild_id <> ''"
    )).fetchall()
    for gid, agid, agname, aid, aname in rows:
        region = (bind.execute(sa.text(
            "SELECT settings->>'albion_guild_region' FROM guilds WHERE id = :g"
        ), {"g": gid}).scalar()) or "americas"
        bind.execute(sa.text(
            "INSERT INTO guild_albion_links (guild_id, albion_guild_id, albion_guild_name, "
            "region, alliance_id, alliance_name) VALUES (:g, :ag, :agn, :r, :ai, :an) "
            "ON CONFLICT DO NOTHING"
        ), {"g": gid, "ag": agid, "agn": agname, "r": region, "ai": aid, "an": aname})


def downgrade() -> None:
    op.drop_index("ix_guild_albion_links_albion_guild_id", table_name="guild_albion_links")
    op.drop_index("ix_guild_albion_links_guild_id", table_name="guild_albion_links")
    op.drop_table("guild_albion_links")
