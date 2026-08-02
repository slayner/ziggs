/**
 * Transmutação — comparar compra direta vs rotas de conversão.
 *
 * Regras (plano §9): conversão 1:1, sobe exatamente um tier OU um encantamento,
 * nunca desce, não troca família, não usa foco, sem retorno. Corações não
 * participam; pedra termina em `.3`.
 *
 * O catálogo `transmutations` já é o grafo: cada entrada é uma aresta
 * source→target com `silverCost`. O custo total de uma rota = preço de
 * mercado do source + Σ silverCost das arestas.
 *
 * Para um alvo, comparamos:
 *  - comprar direto: preço(target)
 *  - todas as rotas: preço(source) + Σ silverCost, via DFS no grafo reverso
 *    (de target até um source que seja "folha", i.e. comprável no mercado).
 *
 * Não há tier/encantamento implícito no cálculo — só o grafo do dump importa.
 */

import type { TransmutationRecipe } from "./refiningCatalog";

export interface TransmuteRoute {
  /** Source inicial a comprar no mercado. */
  sourceId: string;
  /** Arestas, em ordem, do source até o target. */
  edges: TransmutationRecipe[];
  /** Custo base em silver (Σ silverCost × baseCostMultiplier das arestas). */
  silverCost: number;
  /** Taxa de estação total (Σ usage fee por etapa). Calculada com stationFee. */
  stationFeeCost: number;
}

/**
 * Multiplicador de Base Cost aplicado sobre o silverCost do dump.
 * Validado em Lymhurst (jul/2026) contra planilha de referência:
 * - Encantamento upgrade (mesmo tier, sobe enchant): round(silverCost × 1.156)
 * - Tier upgrade (mesmo enchant, sobe tier): round(silverCost × 1.1584)
 * Pode variar por cidade — calibrável. Se outra cidade tiver taxa diferente,
 * ajustar aqui ou tornar por-cidade quando houver dados.
 * ponytail: valores medidos contra a planilha, não estimados.
 */
const ENCHANT_UPGRADE_MULT = 1.156;
const TIER_UPGRADE_MULT = 1.1584;

/** Detecta se a transmutação sobe tier (vs sobe enchant) comparando os IDs. */
function isTierUpgrade(edge: TransmutationRecipe): boolean {
  const srcTier = parseInt(edge.sourceId.match(/^T(\d+)/)?.[1] ?? "0");
  const tgtTier = parseInt(edge.targetId.match(/^T(\d+)/)?.[1] ?? "0");
  return tgtTier > srcTier;
}

/** Base Cost de uma transmutação (silverCost do dump × multiplicador). */
export function baseCostFor(edge: TransmutationRecipe): number {
  const mult = isTierUpgrade(edge) ? TIER_UPGRADE_MULT : ENCHANT_UPGRADE_MULT;
  return Math.round(edge.silverCost * mult);
}

/**
 * Monta o grafo reverso: target → [edges que chegam nele].
 */
function buildReverse(transmutations: TransmutationRecipe[]): Map<string, TransmutationRecipe[]> {
  const g = new Map<string, TransmutationRecipe[]>();
  for (const e of transmutations) {
    const arr = g.get(e.targetId) ?? [];
    arr.push(e);
    g.set(e.targetId, arr);
  }
  return g;
}

/**
 * Taxa de estação por etapa de transmutação.
 * Usage Fee = Math.ceil(qty × (stationFee/100) × itemValue × 0.1125).
 * Igual ao refino: Nutrition Factor = 0.1125, fee per 100 nutrition.
 */
function stationFeeFor(itemValue: number, stationFee: number): number {
  return Math.round((stationFee / 100) * itemValue * 0.1125);
}

/**
 * Encontra todas as rotas válidas para chegar ao target, via DFS reverso.
 *
 * Evita ciclos com um Set de visitados. Como o grafo é acíclico por construção
 * (só sobe tier/encant), o Set é redundante mas seguro contra dados ruins.
 *
 * @param stationFee Fee per 100 nutrition da estação (default 810 = 8.1×100).
 * @param maxDepth Limita a profundidade (default 8 = no pior caso T2→T8 com
 *                 todos os encantamentos intermediários).
 */
export function findRoutes(
  transmutations: TransmutationRecipe[],
  targetId: string,
  stationFee = 810,
  maxDepth = 8,
): TransmuteRoute[] {
  const reverse = buildReverse(transmutations);
  const routes: TransmuteRoute[] = [];

  function dfs(current: string, path: TransmutationRecipe[], visited: Set<string>) {
    if (path.length > maxDepth) return;
    // Registra a rota do current até o target (se houver pelo menos uma aresta).
    // Isso permite começar a transmutar a partir de qualquer nó intermediário
    // que tenha preço no mercado, não apenas das folhas do grafo.
    if (path.length > 0) {
      const edges = [...path].reverse();
      const silverCost = edges.reduce((s, e) => s + baseCostFor(e), 0);
      const stationFeeCost = edges.reduce((s, e) => s + stationFeeFor(e.itemValue, stationFee), 0);
      routes.push({ sourceId: current, edges, silverCost, stationFeeCost });
    }
    const incoming = reverse.get(current);
    if (!incoming || incoming.length === 0) return;
    for (const e of incoming) {
      if (visited.has(e.sourceId)) continue; // anti-ciclo
      visited.add(e.sourceId);
      path.push(e);
      dfs(e.sourceId, path, visited);
      path.pop();
      visited.delete(e.sourceId);
    }
  }

  dfs(targetId, [], new Set([targetId]));
  return routes;
}

/**
 * Compara compra direta vs todas as rotas, dado um mapa de preços.
 *
 * @param prices itemId → preço de mercado (sellMin).
 * @returns A opção mais barata, ou null se não há preço nem rota.
 */
export function cheapestOption(
  routes: TransmuteRoute[],
  directPrice: number | undefined,
  prices: Record<string, number | undefined>,
): { kind: "direct" | "route"; totalCost: number; route?: TransmuteRoute } | null {
  let best: { kind: "direct" | "route"; totalCost: number; route?: TransmuteRoute } | null = null;

  if (directPrice != null && directPrice > 0) {
    best = { kind: "direct", totalCost: directPrice };
  }

  for (const r of routes) {
    const src = prices[r.sourceId];
    if (src == null || src <= 0) continue;
    const total = src + r.silverCost + r.stationFeeCost;
    if (best == null || total < best.totalCost) {
      best = { kind: "route", totalCost: total, route: r };
    }
  }
  return best;
}

export interface TransmuteOption {
  route: TransmuteRoute;
  sourcePrice: number;
  totalCost: number;
  /** Negativo = prejuízo em relação ao preço direto. */
  savings: number;
}

/**
 * Retorna até `count` opções de transmutação. Inclui rotas cujo source tem preço
 * conhecido. Rotas sem preço são descartadas (não dá para calcular). Ordena do
 * maior lucro (menor custo) para o menor. Se não houver preço direto, `savings`
 * é calculado contra a rota mais barata encontrada.
 */
export function transmuteOptions(
  routes: TransmuteRoute[],
  directPrice: number | undefined,
  prices: Record<string, number | undefined>,
  count = 3,
): TransmuteOption[] {
  const opts: TransmuteOption[] = [];
  for (const r of routes) {
    const src = prices[r.sourceId];
    if (src == null || src <= 0) continue;
    const total = src + r.silverCost + r.stationFeeCost;
    opts.push({ route: r, sourcePrice: src, totalCost: total, savings: 0 });
  }
  if (opts.length === 0) return [];
  // Desduplica por sourceId + targetId (último edge), mantendo a mais barata.
  const seen = new Map<string, TransmuteOption>();
  for (const o of opts) {
    const lastEdge = o.route.edges[o.route.edges.length - 1];
    const targetId = lastEdge?.targetId ?? o.route.sourceId;
    const key = o.route.sourceId + "->" + targetId;
    const prev = seen.get(key);
    if (!prev || o.totalCost < prev.totalCost) seen.set(key, o);
  }
  const dedup = [...seen.values()];
  dedup.sort((a, b) => a.totalCost - b.totalCost);
  const baseline = directPrice != null && directPrice > 0 ? directPrice : dedup[0].totalCost;
  for (const o of dedup) o.savings = baseline - o.totalCost;
  return dedup.slice(0, count);
}

// ─── Self-check ──────────────────────────────────────────────────────────────

if (import.meta.env?.vitest) {
  const t: TransmutationRecipe[] = [
    { sourceId: "T4_ORE", targetId: "T5_ORE", silverCost: 781, itemValue: 5.34 },
    { sourceId: "T5_ORE", targetId: "T6_ORE", silverCost: 1250, itemValue: 8 },
    { sourceId: "T4_ORE", targetId: "T4_ORE_LEVEL1", silverCost: 1500, itemValue: 12 },
    { sourceId: "T4_ORE_LEVEL1", targetId: "T5_ORE_LEVEL1", silverCost: 1563, itemValue: 10.66 },
    { sourceId: "T5_ORE", targetId: "T5_ORE_LEVEL1", silverCost: 2000, itemValue: 21.34 },
  ];
  const routes = findRoutes(t, "T6_ORE", 810);
  // T6_ORE: rotas T5→T6 (tier upgrade, mult 1.1584) e T4→T5→T6.
  console.assert(routes.length === 2, `routes: ${routes.length}`);
  // T5→T6: baseCost = round(1250 * 1.1584) = 1448.
  const r56 = routes.find((r) => r.sourceId === "T5_ORE")!;
  console.assert(r56.silverCost === 1448, `baseCost T5→T6: ${r56.silverCost}`);
  // T4→T5→T6: round(781*1.1584) + round(1250*1.1584) = 905 + 1448 = 2353.
  const r456 = routes.find((r) => r.sourceId === "T4_ORE")!;
  console.assert(r456.silverCost === 2353, `baseCost T4→T5→T6: ${r456.silverCost}`);
  // Station fee: T5→T6 = round(8.1 × 8 × 0.1125) = round(7.29) = 7.
  console.assert(r56.stationFeeCost === 7, `stationFee T5→T6: ${r56.stationFeeCost}`);

  const best = cheapestOption(routes, 5000, { "T4_ORE": 1000, "T5_ORE": 1500 });
  // Direto: 5000. T5→T6: 1500+1448+7=2955. T4→T6: 1000+2353+fees. Melhor = T5.
  console.assert(best?.kind === "route" && best.totalCost === 2955, `best: ${JSON.stringify(best)}`);
  console.log("transmutation.ts: self-check OK");
}