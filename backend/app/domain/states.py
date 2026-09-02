"""
Máquina de estados de um EVENTO (CTA).

Esta é a fonte da verdade do ciclo de vida. O site e o bot só mudam o estado de
um evento passando por aqui — nunca escrevendo `events.state` na mão. Toda
transição válida gera (1) uma linha em `event_state_transitions` e (2) um registro
no audit log append-only. Transição inválida levanta `InvalidTransition`.

Estados (rascunho + 4 + 2 terminais de limpeza):
  rascunho       draft        — ainda não publicado
  agendado     scheduled    — criado para começar no futuro
  andamento    in_progress  — CTA acontecendo (pingado por site ou comando)
  revisao      review       — deu /callout; freeze voice%, abre thread de embed,
                               verificações básicas (presença, valor da tab, nodes)
  finalizado   finalized    — pagamentos/logs gerados; terminal
  cancelado    cancelled    — abortado antes de pagar; terminal
  excluido     deleted      — apagado/estornado; terminal

`review` substitui as antigas definition+verification+waiting. O botão concluir
(review → finalized) está SEMPRE disponível — sem guard, sem checklist obrigatório.
Se finalizado sem valor de tab ou nodes capturados, assume tudo 0.
"""
from __future__ import annotations

import enum


class EventState(str, enum.Enum):
    DRAFT = "draft"                # rascunho
    SCHEDULED = "scheduled"        # agendado
    IN_PROGRESS = "in_progress"    # andamento
    REVIEW = "review"              # revisão (antigo definition+verification+waiting)
    FINALIZED = "finalized"        # finalizado
    CANCELLED = "cancelled"        # cancelado
    DELETED = "deleted"            # excluído


class EventType(str, enum.Enum):
    """LEGADO — eventos não têm mais tipo (regear e lootsplit são sempre
    calculados; o que muda é o lootsplit_mode da guilda, ver
    events.get_lootsplit_mode). Mantido só pra ler Event.type de eventos
    antigos já finalizados; não é mais escrito em eventos novos."""
    LOOTSPLIT = "lootsplit"
    REGEAR = "regear"
    LOOTSPLIT_REGEAR = "lootsplit_regear"


class ParticipationMode(str, enum.Enum):
    """Como a participação é capturada. voice_percent existe no modelo desde já,
    mas só presence tem a captura implementada por enquanto — ver
    app/services/event_signups.py."""
    PRESENCE = "presence"
    VOICE_PERCENT = "voice_percent"


# Estados terminais: não saem para lugar nenhum (exceto excluído, ver abaixo).
TERMINAL: frozenset[EventState] = frozenset(
    {EventState.FINALIZED, EventState.CANCELLED, EventState.DELETED}
)


# Transições do "caminho feliz" + ramos de cancelamento.
# Origem -> conjunto de destinos permitidos.
_TRANSITIONS: dict[EventState, frozenset[EventState]] = {
    EventState.DRAFT: frozenset({EventState.SCHEDULED, EventState.DELETED}),
    EventState.SCHEDULED: frozenset(
        {EventState.DRAFT, EventState.IN_PROGRESS, EventState.CANCELLED, EventState.DELETED}
    ),
    EventState.IN_PROGRESS: frozenset(
        {EventState.REVIEW, EventState.CANCELLED, EventState.DELETED}
    ),
    # review -> finalized SEM guard (concluir sempre disponível; assume 0 se faltar).
    EventState.REVIEW: frozenset(
        {EventState.FINALIZED, EventState.CANCELLED, EventState.DELETED}
    ),
    # Um evento já finalizado ainda pode ser EXCLUÍDO (estorna pagamentos), como o
    # /deleteevent do bot faz hoje. Cancelado também pode virar excluído (limpeza).
    EventState.FINALIZED: frozenset({EventState.DELETED}),
    EventState.CANCELLED: frozenset({EventState.DELETED}),
    EventState.DELETED: frozenset(),
}


class InvalidTransition(Exception):
    """Tentativa de mover um evento por uma aresta que não existe."""

    def __init__(self, src: EventState, dst: EventState):
        self.src = src
        self.dst = dst
        super().__init__(f"transição inválida: {src.value} -> {dst.value}")


def allowed_targets(src: EventState) -> frozenset[EventState]:
    """Estados para onde `src` pode ir."""
    return _TRANSITIONS.get(src, frozenset())


def can_transition(src: EventState, dst: EventState) -> bool:
    return dst in allowed_targets(src)


# ---------------------------------------------------------------------------
# Passos de verificação — agora OPCIONAIS (marcadores, sem gating).
#
# `review` faz só verificações básicas: presença (editada direto nos participantes),
# valor da tab e nodes capturados. Só TAB_VALUE e NODES sobrevivem como marcadores
# de "já preenchido"; os outros (participation/missing_loots/tab_image/battles)
# ficam no enum por compatibilidade com dados antigos mas não são mais usados.
# ---------------------------------------------------------------------------
class VerificationStep(str, enum.Enum):
    PARTICIPATION = "participation"   # legado — presença agora via participantes
    MISSING_LOOTS = "missing_loots"   # legado — não usado no fluxo novo
    TAB_VALUE = "tab_value"           # definir o valor da tab
    TAB_IMAGE = "tab_image"           # legado — não usado no fluxo novo
    BATTLES = "battles"               # legado — não usado no fluxo novo
    NODES = "nodes"                   # marcar nodes capturados + valor vendido


# ponytail: sem gating — review → finalized é sempre livre.
REQUIRED_VERIFICATION_STEPS: frozenset[VerificationStep] = frozenset()
