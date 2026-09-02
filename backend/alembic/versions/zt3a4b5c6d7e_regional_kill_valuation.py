"""regionaliza cache de preços e vincula valuation de batalha.

Revision ID: zu3a4b5c6d7e
Revises: zr1a2b3c4d5e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "zu3a4b5c6d7e"
down_revision: Union[str, None] = "zr1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "item_prices",
        sa.Column("region", sa.String(length=16), nullable=False, server_default="west"),
    )
    op.create_index("ix_item_prices_region", "item_prices", ["region"])
    op.add_column(
        "item_prices_latest",
        sa.Column("region", sa.String(length=16), nullable=False, server_default="west"),
    )
    op.drop_constraint("item_prices_latest_item_id_city_quality_key", "item_prices_latest", type_="unique")
    op.create_unique_constraint(
        "uq_item_prices_latest_region",
        "item_prices_latest",
        ["item_id", "city", "quality", "region"],
    )
    op.add_column(
        "battle_kill_events",
        sa.Column("player_kill_event_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_bke_player_kill_event",
        "battle_kill_events",
        "player_kill_events",
        ["player_kill_event_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_bke_player_kill_event_id", "battle_kill_events", ["player_kill_event_id"])
    op.execute(
        """
        UPDATE battle_kill_events AS bke
        SET player_kill_event_id = pke.id,
            silver_dropped = pke.silver_dropped
        FROM battles AS battle
        JOIN player_kill_events AS pke
          ON pke.region = battle.region
         AND pke.albion_event_id = bke.albion_event_id
        WHERE battle.id = bke.battle_id
          AND bke.player_kill_event_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_bke_player_kill_event_id", table_name="battle_kill_events")
    op.drop_constraint("fk_bke_player_kill_event", "battle_kill_events", type_="foreignkey")
    op.drop_column("battle_kill_events", "player_kill_event_id")
    op.drop_constraint("uq_item_prices_latest_region", "item_prices_latest", type_="unique")
    op.create_unique_constraint(
        "item_prices_latest_item_id_city_quality_key",
        "item_prices_latest",
        ["item_id", "city", "quality"],
    )
    op.drop_column("item_prices_latest", "region")
    op.drop_index("ix_item_prices_region", table_name="item_prices")
    op.drop_column("item_prices", "region")
