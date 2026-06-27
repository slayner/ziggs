"""
Máquina de estados de um EVENTO (CTA).

Esta é a fonte da verdade do ciclo de vida. O site e o bot só mudam o estado de
um evento passando por aqui — nunca escrevendo `events.state` na mão. Toda
transição válida gera (1) uma linha em `event_state_transitions` e (2) um registro
no audit log append-only. Transição inválida levanta `InvalidTransition`.

Estados (em PT, como o usuário descreveu):
  agendado     scheduled    — criado para começar no futuro
  andamento    in_progress  — CTA acontecendo (pingado por site ou comando)
  definicao    definition   — deu /callout; threads abrem, logística define o tipo
  verificacao  verification  — os 7 passos de conferência
  espera       waiting      — tudo conferido; ÚNICO estado que libera "finalizar"
  finalizado   finalized    — pagamentos/logs gerados; terminal
  cancelado    cancelled    — abortado antes de pagar; terminal
  excluido     deleted      — apagado/estornado; terminal

Não há ordem fixa imposta além das arestas abaixo: a partir de quase qualquer
estado não-terminal dá para cancelar/excluir, e de `espera` dá para voltar a
`verificacao` se a logística achar algo errado.
"""
from __future__ import annotations

import enum


class EventState(str, enum.Enum):
    SCHEDULED = "scheduled"        # agendado
    IN_PROGRESS = "in_progress"    # andamento
    DEFINITION = "definition"      # definição
    VERIFICATION = "verification"  # verificação
    WAITING = "waiting"            # espera
    FINALIZED = "finalized"        # finalizado
    CANCELLED = "cancelled"        # cancelado
    DELETED = "deleted"            # excluído


class EventType(str, enum.Enum):
    """Definido na fase de `definicao` pela logística — não na criação."""
    LOOTSPLIT = "lootsplit"
    REGEAR = "regear"
    LOOTSPLIT_REGEAR = "lootsplit_regear"


# Estados terminais: não saem para lugar nenhum (exceto excluído, ver abaixo).
TERMINAL: frozenset[EventState] = frozenset(
    {EventState.FINALIZED, EventState.CANCELLED, EventState.DELETED}
)


# Transições do "caminho feliz" + ramos de cancelamento/volta.
# Origem -> conjunto de destinos permitidos.
_TRANSITIONS: dict[EventState, frozenset[EventState]] = {
    EventState.SCHEDULED: frozenset(
        {EventState.IN_PROGRESS, EventState.CANCELLED, EventState.DELETED}
    ),
    EventState.IN_PROGRESS: frozenset(
        {EventState.DEFINITION, EventState.CANCELLED, EventState.DELETED}
    ),
    EventState.DEFINITION: frozenset(
        {EventState.VERIFICATION, EventState.CANCELLED, EventState.DELETED}
    ),
    EventState.VERIFICATION: frozenset(
        {EventState.WAITING, EventState.CANCELLED, EventState.DELETED}
    ),
    EventState.WAITING: frozenset(
        # espera -> verificação (voltar p/ corrigir) | finalizado | cancelado | excluído
        {EventState.VERIFICATION, EventState.FINALIZED,
         EventState.CANCELLED, EventState.DELETED}
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
# Os 7 passos da fase de VERIFICAÇÃO.
#
# A fase só pode avançar para `espera` quando todos os passos OBRIGATÓRIOS estão
# concluídos. A ordem aqui é a ordem em que aparecem na UI; o usuário pode marcar
# em qualquer ordem, mas todos precisam estar OK para liberar `espera`.
# ---------------------------------------------------------------------------
class VerificationStep(str, enum.Enum):
    PARTICIPATION = "participation"   # 1. verificar as participações (%)
    MISSING_LOOTS = "missing_loots"   # 2. verificar loots faltantes (desvios)
    TAB_VALUE = "tab_value"           # 3. definir o valor da tab
    TAB_IMAGE = "tab_image"           # 4. print da tab
    BATTLES = "battles"               # 5. registrar as batalhas do horário do CTA
    NODES = "nodes"                   # 6. definir nodes capturados no evento


# Observação: o usuário citou "7 passos" numerados 1,2,4,5,6,7 (sem 3) na visão.
# Consolidei em 6 passos canônicos sem buraco; "valor da tab" e "print da tab"
# ficaram separados (eram os antigos 4 e 5). Se quiser exatamente 7, é só
# adicionar um passo extra aqui — a lógica de gating não muda.

REQUIRED_VERIFICATION_STEPS: frozenset[VerificationStep] = frozenset(VerificationStep)
