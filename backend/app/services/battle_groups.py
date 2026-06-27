"""Códigos curtos compartilháveis (7 chars, ex. 'k3j9xq2') — o sublink público
de uma batalha (ou de várias batalhas combinadas numa KB só).

O código é criado on-demand: quando uma batalha entra no feed ZvZ, ou quando
alguém resolve um ID cru do Albion (ver battle_tracker.resolve_by_albion_id).
Combinar batalhas reusa o mesmo código se a combinação exata já existir
(achado pelo fingerprint), nunca duplica grupo pra mesma combinação.
"""
from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.battles import BattleGroup, BattleGroupMember

# Sem maiúsculas/símbolos — fácil de copiar e colar, e dá pra "tentar a sorte"
# digitando combinações por curiosidade (pedido explícito do produto).
CODE_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
CODE_LENGTH = 7


def _generate_code(db: Session) -> str:
    # ponytail: 36^7 ~= 78 bilhões de combinações — colisão é praticamente
    # impossível, o retry é só rede de segurança.
    for _ in range(20):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        # nunca só dígitos: assim um código nunca é ambíguo com um ID cru do
        # Albion (que o front trata como rota separada, ver router.ts).
        if code.isdigit():
            continue
        if not db.scalar(select(BattleGroup).where(BattleGroup.public_id == code)):
            return code
    raise RuntimeError("battle_groups: não consegui gerar um código único")


def _fingerprint(battle_ids: list[int]) -> str:
    return ",".join(str(i) for i in sorted(set(battle_ids)))


def get_or_create_group(db: Session, battle_ids: list[int]) -> BattleGroup:
    fingerprint = _fingerprint(battle_ids)
    group = db.scalar(select(BattleGroup).where(BattleGroup.fingerprint == fingerprint))
    if group is not None:
        return group

    try:
        with db.begin_nested():  # savepoint — rollback parcial se houver corrida
            group = BattleGroup(public_id=_generate_code(db), fingerprint=fingerprint)
            db.add(group)
            db.flush()
            for position, battle_id in enumerate(sorted(set(battle_ids))):
                db.add(BattleGroupMember(group_id=group.id, battle_id=battle_id, position=position))
    except IntegrityError:
        group = db.scalar(select(BattleGroup).where(BattleGroup.fingerprint == fingerprint))

    db.commit()
    return group


def get_group_battle_ids(db: Session, public_id: str) -> list[int] | None:
    group = db.scalar(select(BattleGroup).where(BattleGroup.public_id == public_id))
    if group is None:
        return None
    members = db.scalars(
        select(BattleGroupMember)
        .where(BattleGroupMember.group_id == group.id)
        .order_by(BattleGroupMember.position)
    ).all()
    return [m.battle_id for m in members]
