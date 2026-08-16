"""bot_registrations: mesmo personagem em múltiplos Discords

Revision ID: zq6f7a8b9c0d
Revises: zp5e6f7a8b9c
Create Date: 2026-08-15 00:00:00.000000

A constraint única era (guild_id, albion_player_id) — um personagem só podia
estar registrado pra UM usuário Discord por guilda. Mas existem pessoas com
vários Discords (main + alt), e a verificação retroativa dos registros
"manual:" (feitos com a vigilância desligada) converge vários registros pro
mesmo player ID real. Nova constraint: (guild_id, albion_player_id,
discord_user_id) — um registro = (guilda, personagem, conta Discord).

Idempotente: se já existem linhas duplicadas de (guild, player, discord) —
não deveria, a constraint antiga impunha player único — a nova só trava
duplicatas exatas.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'zq6f7a8b9c0d'
down_revision: Union[str, None] = 'zp5e6f7a8b9c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('bot_registrations') as b:
        b.drop_constraint('uq_bot_reg_character', type_='unique')
        b.create_unique_constraint(
            'uq_bot_reg_character_user',
            ['guild_id', 'albion_player_id', 'discord_user_id'],
        )


def downgrade() -> None:
    with op.batch_alter_table('bot_registrations') as b:
        b.drop_constraint('uq_bot_reg_character_user', type_='unique')
        b.create_unique_constraint('uq_bot_reg_character', ['guild_id', 'albion_player_id'])
