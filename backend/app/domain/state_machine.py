"""
Aplicação de transições de estado de um evento.

Ponto único por onde site e bot mudam o estado de um evento. Faz, em UMA
transação:
  1. valida a aresta (states.can_transition)
  2. roda os GUARDS da aresta (ex.: só vai p/ verificação se o tipo foi definido)
  3. grava `events.state`
  4. insere uma linha em `event_state_transitions`
  5. escreve no audit log append-only

Quem chama é responsável por dar `session.commit()`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.domain.states import (
    EventState,
    InvalidTransition,
    can_transition,
    REQUIRED_VERIFICATION_STEPS,
)


class TransitionDenied(Exception):
    """Aresta existe, mas um guard reprovou (ex.: tipo do CTA não definido)."""


@dataclass(frozen=True)
class Actor:
    """Quem está causando a mudança. `source` distingue site x bot x sistema."""
    id: int | None          # discord user id (None p/ ações automáticas do sistema)
    source: str             # "site" | "bot" | "system"
    display: str | None = None


# --- Guards -----------------------------------------------------------------
# Cada guard recebe (event, session) e levanta TransitionDenied se não puder.
def _require_type(event, session: Session) -> None:
    if event.type is None:
        raise TransitionDenied(
            "defina o tipo do CTA (lootsplit/regear/lootsplit+regear) antes de verificar"
        )


def _require_verification_complete(event, session: Session) -> None:
    from app.models.events import EventVerificationStep  # import tardio (evita ciclo)

    done = set(
        session.scalars(
            select(EventVerificationStep.step).where(
                EventVerificationStep.event_id == event.id,
                EventVerificationStep.completed.is_(True),
            )
        ).all()
    )
    faltando = {s.value for s in REQUIRED_VERIFICATION_STEPS} - {
        s.value if hasattr(s, "value") else s for s in done
    }
    if faltando:
        raise TransitionDenied(
            "passos de verificação pendentes: " + ", ".join(sorted(faltando))
        )


# Guards por aresta (origem, destino) -> lista de checagens.
_GUARDS: dict[tuple[EventState, EventState], list[Callable]] = {
    (EventState.DEFINITION, EventState.VERIFICATION): [_require_type],
    (EventState.VERIFICATION, EventState.WAITING): [_require_verification_complete],
    (EventState.WAITING, EventState.FINALIZED): [
        _require_type,
        _require_verification_complete,
    ],
}


def transition(
    session: Session,
    event,
    to: EventState,
    actor: Actor,
    reason: str | None = None,
) -> None:
    """Move `event` para `to`. Levanta InvalidTransition / TransitionDenied."""
    from app.models.events import EventStateTransition
    from app.models.audit import AuditLog

    src: EventState = event.state
    if not can_transition(src, to):
        raise InvalidTransition(src, to)

    for guard in _GUARDS.get((src, to), ()):
        guard(event, session)

    event.state = to

    session.add(
        EventStateTransition(
            guild_id=event.guild_id,
            event_id=event.id,
            from_state=src,
            to_state=to,
            actor_id=actor.id,
            source=actor.source,
            reason=reason,
        )
    )
    session.add(
        AuditLog(
            guild_id=event.guild_id,
            event_id=event.id,
            actor_id=actor.id,
            actor_type=actor.source,
            action="event.transition",
            entity="event",
            entity_id=str(event.id),
            before={"state": src.value},
            after={"state": to.value},
            source=actor.source,
            note=reason,
        )
    )
