/**
 * Refino encadeado — expande a receita recursivamente, produzindo o refinado
 * do tier anterior em vez de comprá-lo.
 *
 * T2 é bruto (sem refinado anterior). T3+ tem 1× refinado T(n-1) como input;
 * encadear = refinar esse T(n-1) a partir de T(n-2), recursivamente até T2.
 *
 * O refinado anterior é sempre a receita (tier-1, enchant=0), mesmo para
 * receitas encantadas — confirmado pelo dump e pelo plano §6.1
 * ("T4 encantado usa o refinado T3 comum como material anterior").
 *
 * Pedra: outputCount multiplica os blocos, mas o refinado anterior continua
 * sendo a receita flat (enchant=0). Cada etapa aplica seu próprio retorno,
 * taxa e foco.
 */

import type { RefiningCatalog, RefiningRecipe, RefiningVariant } from "./refiningCatalog";
import { refiningFocusCost } from "./refining";

export interface ChainStep {
  recipeKey: string;
  tier: number;
  enchant: number;
  /** Quantas unidades desta receita foram executadas. */
  crafts: number;
  /** Output produzido nesta etapa. */
  outputId: string;
  outputCount: number;
  /** Foco gasto nesta etapa (já com efficiency). */
  focus: number;
}

export interface ChainResult {
  /** Materiais brutos a comprar, por itemId (já descontado o retorno de cada etapa). */
  rawMaterials: Record<string, number>;
  /** Etapas executadas em ordem (T2 → Tn). */
  steps: ChainStep[];
  /** Foco total de todas as etapas. */
  focusTotal: number;
}

/**
 * Verifica se um itemId é um refinado (output de alguma receita da família).
 * Se for, devolve a receita que o produz; senão é bruto.
 */
function recipeOfOutput(catalog: RefiningCatalog, itemId: string): RefiningRecipe | null {
  return catalog.recipes.find((r) => r.outputId === itemId) ?? null;
}

/**
 * Expande uma receita de refino recursivamente.
 *
 * @param qty Quantas unidades do OUTPUT final deseja produzir.
 * @param rr Return rate aplicável (fração 0..1) — todas as etapas usam o mesmo rr.
 * @param focusEff Efficiency de foco (pontos) — aplicado a todas as etapas.
 */
export function expandChain(
  catalog: RefiningCatalog,
  recipe: RefiningRecipe,
  variant: RefiningVariant,
  qty: number,
  rr: number,
  focusEff: number,
): ChainResult {
  const rawMaterials: Record<string, number> = {};
  const steps: ChainStep[] = [];

  /**
   * Produz `needed` unidades do outputId, recursivamente.
   * Acumula materiais brutos, foco e etapas.
   */
  function produce(itemId: string, needed: number) {
    const r = recipeOfOutput(catalog, itemId);
    if (!r) {
      // Bruto — comprar.
      rawMaterials[itemId] = (rawMaterials[itemId] ?? 0) + needed;
      return;
    }
    // É refinado — refinar `needed` unidades (variant normal).
    // Quantas receitas executar = ceil(needed / outputCount).
    // ponytail: ceil pra não subproduzir; sobra é descartada (conservador).
    const oc = variant.outputCount; // mesma variante da receita alvo
    const crafts = Math.ceil(needed / oc);
    const actualOutput = crafts * oc;

    // Inputs desta etapa (variante normal do refinado intermediário).
    // ponytail: intermediários sempre usam variant normal (coração só no alvo).
    const v = r.variants.find((x) => x.kind === "normal") ?? r.variants[0];
    for (const input of v.inputs) {
      const totalNeeded = input.count * crafts;
      if (input.returnable) {
        // Retorno reduz o que preciso comprar/produzir.
        const afterReturn = totalNeeded * (1 - rr);
        // Se for refinado, produz afterReturn (o retorno devolve pra estocar,
        // mas pra simplificar o planejamento: produzo o líquido necessário).
        // ponytail: para materiais brutos, afterReturn é o que compro.
        produce(input.itemId, afterReturn);
      } else {
        // Não-returnable (coração): compra o cheio, sem retorno.
        // Mas coração só existe na variant heart; normal não tem isHeart.
        produce(input.itemId, totalNeeded);
      }
    }

    steps.push({
      recipeKey: r.key,
      tier: r.tier,
      enchant: r.enchant,
      crafts,
      outputId: r.outputId,
      outputCount: actualOutput,
      focus: refiningFocusCost(v.focus, v.outputCount, focusEff) * crafts,
    });
  }

  produce(recipe.outputId, qty);

  const focusTotal = steps.reduce((s, st) => s + st.focus, 0);
  return { rawMaterials, steps, focusTotal };
}

// ─── Self-check ──────────────────────────────────────────────────────────────

if (import.meta.env?.vitest) {
  // Catálogo mínimo: ore T2/T3/T4, normal apenas.
  const catalog: RefiningCatalog = {
    dump_source: "test",
    recipes: [
      {
        key: "ore_2_0", family: "ore", tier: 2, enchant: 0,
        outputId: "T2_METALBAR", outputCount: 1, itemValue: 4, baseFame: 5,
        variants: [{ kind: "normal", focus: 20, outputCount: 1, inputs: [
          { itemId: "T2_ORE", count: 1, enchant: 0, returnable: true, isHeart: false },
        ] }],
      },
      {
        key: "ore_3_0", family: "ore", tier: 3, enchant: 0,
        outputId: "T3_METALBAR", outputCount: 1, itemValue: 8, baseFame: 10,
        variants: [{ kind: "normal", focus: 40, outputCount: 1, inputs: [
          { itemId: "T3_ORE", count: 2, enchant: 0, returnable: true, isHeart: false },
          { itemId: "T2_METALBAR", count: 1, enchant: 0, returnable: true, isHeart: false },
        ] }],
      },
      {
        key: "ore_4_0", family: "ore", tier: 4, enchant: 0,
        outputId: "T4_METALBAR", outputCount: 1, itemValue: 16, baseFame: 22.5,
        variants: [{ kind: "normal", focus: 54, outputCount: 1, inputs: [
          { itemId: "T4_ORE", count: 2, enchant: 0, returnable: true, isHeart: false },
          { itemId: "T3_METALBAR", count: 1, enchant: 0, returnable: true, isHeart: false },
        ] }],
      },
    ],
    transmutations: [],
    heartConversions: [],
    refiningCities: {},
    names: {},
  };
  // rr=0, focusEff=0: 1× T4 = 2 T4_ORE + 1 T3_METALBAR (→ 2 T3_ORE + 1 T2_METALBAR → 1 T2_ORE).
  const res = expandChain(catalog, catalog.recipes[2], catalog.recipes[2].variants[0], 1, 0, 0);
  console.assert(res.steps.length === 3, `steps: ${res.steps.length}`);
  console.assert(res.rawMaterials["T4_ORE"] === 2, `T4_ORE: ${res.rawMaterials["T4_ORE"]}`);
  console.assert(res.rawMaterials["T3_ORE"] === 2, `T3_ORE: ${res.rawMaterials["T3_ORE"]}`);
  console.assert(res.rawMaterials["T2_ORE"] === 1, `T2_ORE: ${res.rawMaterials["T2_ORE"]}`);
  // Com rr=0.5: cada etapa compra metade. T4_ORE: 2×0.5=1. T3_ORE: 2×0.5=1. T2_ORE: 1×0.5=0.5.
  const res2 = expandChain(catalog, catalog.recipes[2], catalog.recipes[2].variants[0], 1, 0.5, 0);
  console.assert(Math.abs(res2.rawMaterials["T4_ORE"] - 1) < 0.001, `T4_ORE rr: ${res2.rawMaterials["T4_ORE"]}`);
  console.assert(Math.abs(res2.rawMaterials["T2_ORE"] - 0.5) < 0.001, `T2_ORE rr: ${res2.rawMaterials["T2_ORE"]}`);
  console.log("refiningChain.ts: self-check OK");
}