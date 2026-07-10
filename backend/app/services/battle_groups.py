"""Códigos curtos compartilháveis (7 chars, ex. 'k3j9xq2') — o sublink público
de uma batalha (ou de várias batalhas combinadas numa KB só).

O código é criado on-demand: quando uma batalha entra no feed ZvZ, ou quando
alguém resolve um ID cru do Albion (ver battle_tracker.resolve_by_albion_id).
Combinar batalhas reusa o mesmo código se a combinação exata já existir
(achado pelo fingerprint), nunca duplica grupo pra mesma combinação.
"""
from __future__ import annotations

import secrets
import time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
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

    for attempt in range(2):
        try:
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
        except OperationalError:
            # contenção passageira (escrita de fundo de outros serviços) — pode
            # bater tanto no flush (dentro do savepoint) quanto no commit, por
            # isso os dois ficam dentro do mesmo retry. 1 retry curto.
            db.rollback()
            if attempt == 1:
                raise
            time.sleep(1.0)
    raise AssertionError("unreachable")


def get_or_create_groups_bulk(db: Session, battle_ids: list[int]) -> dict[int, BattleGroup]:
    """Versão em lote de get_or_create_group pra grupos "solo" (1 batalha =
    1 grupo) — usado pela listagem (list_battles.py), que soma até `limit`
    chamadas por request (uma por batalha da página). Um commit só no fim,
    não um por batalha: comitar a cada item da página (10-25 por request)
    já causou "database is locked" sob a carga de escrita de fundo dos
    outros serviços — mesma classe de bug já corrigida em
    battle_tracker.upsert_battle_light.

    O commit em si ainda pode topar com contenção passageira (vários
    serviços de fundo escrevendo ao mesmo tempo) mesmo já sendo um só —
    2 tentativas curtas aqui é bem mais barato que deixar a página inteira
    voltar 500 pro usuário por causa de um pico momentâneo de escrita.
    Depois de rollback os objetos ainda-não-commitados são descartados da
    sessão, então cada tentativa refaz a checagem+criação do zero — só
    repetir o commit sozinho não reinsere nada.

    Corrida concorrente: duas requisições podem pegar a mesma battle_id ainda
    sem grupo ao mesmo tempo (o SELECT de `existing` acima não vê o INSERT da
    outra até ela commitar) — a perdedora bate no UNIQUE de fingerprint.
    get_or_create_group (versão singular) já tratava isso com savepoint +
    IntegrityError; aqui precisa do mesmo tratamento por item, sem descartar
    o resto do lote — só essa criação é refeita como um SELECT do vencedor."""
    fingerprints = {bid: _fingerprint([bid]) for bid in battle_ids}

    for attempt in range(2):
        try:
            existing = {
                g.fingerprint: g for g in db.scalars(
                    select(BattleGroup).where(BattleGroup.fingerprint.in_(fingerprints.values()))
                )
            }
            out: dict[int, BattleGroup] = {}
            for bid in battle_ids:
                fp = fingerprints[bid]
                group = existing.get(fp)
                if group is None:
                    try:
                        with db.begin_nested():  # savepoint — rollback só deste item se perder a corrida
                            group = BattleGroup(public_id=_generate_code(db), fingerprint=fp)
                            db.add(group)
                            db.flush()
                            db.add(BattleGroupMember(group_id=group.id, battle_id=bid, position=0))
                    except IntegrityError:
                        group = db.scalar(select(BattleGroup).where(BattleGroup.fingerprint == fp))
                    existing[fp] = group
                out[bid] = group

            db.commit()
            return out
        except OperationalError:
            # contenção passageira (escrita de fundo de outros serviços) — pode
            # bater tanto num flush (dentro do savepoint) quanto no commit, por
            # isso a tentativa inteira fica dentro do mesmo retry.
            db.rollback()
            if attempt == 1:
                raise
            time.sleep(1.0)
    raise AssertionError("unreachable")  # o laço sempre retorna ou levanta antes de chegar aqui


def get_existing_groups_bulk(db: Session, battle_ids: list[int]) -> dict[int, BattleGroup]:
    """Só lê — nunca cria (sem flush/commit, sem risco de 'database is
    locked'). Usado por refresh de background (dashboard_cache) que roda
    sem pedido de usuário: batalha sem grupo ainda fica de fora do cache
    dessa passada e some assim que alguém (ou outro serviço) criar o grupo
    dela — não vale forçar escrita extra num loop periódico só por isso."""
    fingerprints = {bid: _fingerprint([bid]) for bid in battle_ids}
    existing = {
        g.fingerprint: g for g in db.scalars(
            select(BattleGroup).where(BattleGroup.fingerprint.in_(fingerprints.values()))
        )
    }
    return {bid: existing[fp] for bid, fp in fingerprints.items() if fp in existing}


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
