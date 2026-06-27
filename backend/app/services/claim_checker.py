"""Background task que verifica claims pendentes contra kill events."""
from __future__ import annotations

import asyncio
import logging
from datetime import timezone

from sqlalchemy import select

from app.db import get_session
from app.models.claims import CharacterClaim, RegisteredCharacter
from app.models.players import AlbionPlayer, PlayerKillEvent
from app.services.challenge_pool import check_equipment

log = logging.getLogger(__name__)
CHECK_INTERVAL = 120  # segundos


def _check_once() -> None:
    db = next(get_session())
    try:
        pending = db.scalars(
            select(CharacterClaim).where(CharacterClaim.status == "pending")
        ).all()

        if not pending:
            return

        # Resolve albion_player_id → AlbionPlayer.id de uma vez
        pids = list({c.albion_player_id for c in pending})
        player_map: dict[str, int] = {
            ap.albion_id: ap.id
            for ap in db.scalars(
                select(AlbionPlayer).where(AlbionPlayer.albion_id.in_(pids))
            ).all()
        }

        for claim in pending:
            ap_id = player_map.get(claim.albion_player_id)
            if ap_id is None:
                continue  # jogador ainda não apareceu em nenhum evento

            # Kill events desse jogador (como killer ou victim) após a criação do claim
            events = db.scalars(
                select(PlayerKillEvent).where(
                    (
                        (PlayerKillEvent.killer_player_id == ap_id)
                        | (PlayerKillEvent.victim_player_id == ap_id)
                    ),
                    PlayerKillEvent.timestamp > claim.created_at,
                )
            ).all()

            for ev in events:
                # Verifica o equipamento do nosso jogador (pode ser killer ou victim)
                equip = (
                    ev.killer_equipment
                    if ev.killer_player_id == ap_id
                    else ev.victim_equipment
                )
                if not check_equipment(equip, claim.challenge):
                    continue

                # Verificado!
                claim.status = "verified"
                claim.verified_at = ev.timestamp.replace(tzinfo=timezone.utc) if ev.timestamp.tzinfo is None else ev.timestamp
                claim.verified_event_id = ev.albion_event_id

                # Upsert em registered_characters (sobrescreve registro anterior)
                existing = db.scalar(
                    select(RegisteredCharacter).where(
                        RegisteredCharacter.albion_player_id == claim.albion_player_id
                    )
                )
                if existing:
                    existing.user_id = claim.user_id
                    existing.albion_player_name = claim.albion_player_name
                    existing.region = claim.region
                    existing.claim_id = claim.id
                else:
                    db.add(RegisteredCharacter(
                        user_id=claim.user_id,
                        albion_player_id=claim.albion_player_id,
                        albion_player_name=claim.albion_player_name,
                        region=claim.region,
                        claim_id=claim.id,
                    ))

                log.info(
                    "Claim #%d verificado: %s → user_id=%d (evento %s)",
                    claim.id, claim.albion_player_name, claim.user_id, ev.albion_event_id,
                )
                break  # claim verificado, passa para o próximo

        db.commit()
    except Exception:
        log.exception("Erro no claim_checker")
        db.rollback()
    finally:
        db.close()


async def run_forever() -> None:
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        await asyncio.get_event_loop().run_in_executor(None, _check_once)
