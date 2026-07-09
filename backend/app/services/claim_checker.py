"""Background task que verifica claims pendentes contra kill events."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import get_session
from app.models.claims import CLAIM_EXPIRY, CharacterClaim, RegisteredCharacter
from app.models.players import AlbionPlayer, PlayerKillEvent
from app.services.albion_gate import CLAIM_VERIFY, albion_scope, slot
from app.services.challenge_pool import check_inventory
from app.services.player_tracker import HOSTS, make_client, sync_player_kills, upsert_player

log = logging.getLogger(__name__)
CHECK_INTERVAL = 120  # segundos


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _sync_pending_players(db, pending: list[CharacterClaim]) -> None:
    """Busca ATIVA do jogador de cada claim pendente direto na API do Albion.

    Sem isso, dependeríamos só do feed global passivo (battle_tracker), que só
    vê quem está nos ~51 eventos mais recentes da região no momento exato do
    poll (a cada 5min) — uma morte feita só pra verificação, sem outros
    jogadores by-standers, facilmente nunca aparece nele e a claim travaria pra
    sempre mesmo com o usuário fazendo tudo certo. Mesmo padrão do
    profile_warmer, só que disparado por claim pendente em vez de batalha nova.
    """
    seen: set[tuple[str, str]] = set()
    async with make_client() as client:
        async with albion_scope(CLAIM_VERIFY):
            for claim in pending:
                key = (claim.region, claim.albion_player_id)
                if key in seen:
                    continue
                seen.add(key)
                host = HOSTS.get(claim.region)
                if not host:
                    continue
                try:
                    async with slot():
                        resp = await client.get(f"https://{host}/api/gameinfo/players/{claim.albion_player_id}")
                    if resp.status_code != 200:
                        continue
                    raw = resp.json()
                    if not (isinstance(raw, dict) and raw.get("Id")):
                        continue
                    upsert_player(db, raw, claim.region)
                    await sync_player_kills(client, db, host, claim.region, claim.albion_player_id)
                except Exception as e:
                    log.debug("claim_checker: falha ao sincronizar %s (%s): %s", claim.albion_player_id, claim.region, e)


async def _check_once() -> None:
    db = next(get_session())
    try:
        pending = db.scalars(
            select(CharacterClaim).where(CharacterClaim.status == "pending")
        ).all()

        if not pending:
            return

        await _sync_pending_players(db, pending)

        # Resolve albion_player_id → AlbionPlayer.id de uma vez
        pids = list({c.albion_player_id for c in pending})
        player_map: dict[str, int] = {
            ap.albion_id: ap.id
            for ap in db.scalars(
                select(AlbionPlayer).where(AlbionPlayer.albion_id.in_(pids))
            ).all()
        }

        now = datetime.now(timezone.utc)

        for claim in pending:
            ap_id = player_map.get(claim.albion_player_id)
            if ap_id is not None:
                # Só MORTES do jogador após o claim — verificamos a bag da morte
                # (Victim.Inventory), não kills.
                events = db.scalars(
                    select(PlayerKillEvent).where(
                        PlayerKillEvent.victim_player_id == ap_id,
                        PlayerKillEvent.timestamp > claim.created_at,
                    )
                ).all()

                for ev in events:
                    # A bag da morte tem que conter EXATAMENTE os itens do desafio.
                    if not check_inventory(ev.victim_inventory, claim.challenge):
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

            # Não verificou e passou da janela de 24h -> expira (libera o
            # personagem pra outros tentarem, mas o cooldown de 30 dias desse
            # USUÁRIO pra esse personagem é aplicado na criação de uma nova claim).
            if claim.status == "pending" and now - _aware(claim.created_at) > CLAIM_EXPIRY:
                claim.status = "expired"
                log.info(
                    "Claim #%d expirou (sem verificar em 24h): %s (user_id=%d)",
                    claim.id, claim.albion_player_name, claim.user_id,
                )

        db.commit()
    except Exception:
        log.exception("Erro no claim_checker")
        db.rollback()
    finally:
        db.close()


async def run_forever() -> None:
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        await _check_once()
