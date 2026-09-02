import type { EventSummary } from "../api";

/** Ordena eventos por ID decrescente (mais recente primeiro). Antes usava
 *  prioridade por estado (waiting/verification/definition) que não existem
 *  mais — foram unificados em `review` na refactor do state machine, e a
 *  ordenação ficou arbitrária. ID decrescente é o que o usuário pediu: o
 *  evento mais novo no topo do seletor. */
export function prioritizeEvents(events: EventSummary[]): EventSummary[] {
  return [...events].sort((a, b) => b.id - a.id);
}