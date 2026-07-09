import type { EventSummary } from "../api";

// Prioridade por proximidade do finalize: `waiting` é o único estado que
// libera "finalizar", então vem primeiro; `verification` é o anterior, etc.
// Terminais por último — não dá pra reconciliar/fazer lootlog de algo excluído.
const STATE_PRIORITY: Record<string, number> = {
  waiting: 0, verification: 1, definition: 2, in_progress: 3, scheduled: 4,
  finalized: 5, cancelled: 6, deleted: 7,
};

/** Ordena CTAs "prestes a ser finalizados" primeiro (waiting → verification → …). */
export function prioritizeEvents(events: EventSummary[]): EventSummary[] {
  return [...events].sort((a, b) => {
    const pa = STATE_PRIORITY[a.state] ?? 99;
    const pb = STATE_PRIORITY[b.state] ?? 99;
    if (pa !== pb) return pa - pb;
    // mesma faixa: agendado mais recente primeiro (null por último).
    return (b.scheduled_at ?? "").localeCompare(a.scheduled_at ?? "");
  });
}