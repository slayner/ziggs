"""
Aplicação de transições de estado de um evento.

Ponto único por onde site e bot mudam o estado de um evento. Faz, em UMA
transação:
  1. valida a aresta (states.can_transition)
  2. grava `events.state`
  3. insere uma linha em `event_state_transitions`
  4. escreve no audit log append-only

Fluxo novo (4 estados): scheduled → in_progress → review → finalized
(+ cancelled/deleted terminais). O botão concluir (review → finalized) está
sempre disponível: sem guard, sem checklist obrigatório. Se finalizado sem
valor de tab ou nodes capturados, assume tudo 0.

Quem chama é responsável por dar `session.commit()`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.domain.states import (
    EventState,
    InvalidTransition,
    can_transition,
)


class TransitionDenied(Exception):
    """Aresta existe, mas um guard reprovou."""


@dataclass(frozen=True)
class Actor:
    """Quem está causando a mudança. `source` distingue site x bot x sistema."""
    id: int | None          # discord user id (None p/ ações automáticas do sistema)
    source: str             # "site" | "bot" | "system"
    display: str | None = None


# ponytail: sem guards — review → finalized é sempre livre (assume 0 se faltar).
_GUARDS: dict[tuple[EventState, EventState], list[Callable]] = {}


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
