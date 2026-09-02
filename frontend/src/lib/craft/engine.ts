/**
 * Albion crafting economics engine.
 *
 * The formulas here are a faithful re-implementation of the well-known
 * FallenAng3L crafting spreadsheet (see /reference/craft.xlsx, sheet
 * "Crafting"). Cell references from that sheet are noted in comments so the
 * math stays auditable. All values verified against row 9 (Miner Workboots,
 * T4.0) — see engine.test.ts.
 *
 * Two profit modes matter in Albion:
 *  - WITHOUT focus: maximise raw silver profit.
 *  - WITH focus: maximise SPF (silver per focus), because focus is the scarce
 *    resource. A craft that loses silver at base return can still be the best
 *    use of a focus point.
 */

/** A single material line in a recipe. */
export interface MaterialInput {
  /** Albion item id, e.g. "T4_METALBAR". Optional for ad-hoc calc. */
  uniqueName?: string;
  /** Buy price per unit (silver). */
  unitPrice: number;
  /** Units consumed per single craft action. */
  countPerCraft: number;
  /** Material never returned by the return rate (artifacts, faction tokens). */
  noReturn?: boolean;
}

export interface CraftInput {
  /** Number of items to craft (sheet K2). */
  quantity: number;
  /** Recipe materials. */
  materials: MaterialInput[];
  /** Unit sell price of the crafted item (sheet U9). */
  sellPrice: number;
  /** Items produced per craft action (food/potions yield several; default 1). */
  outputPerCraft?: number;

  /**
   * Resource Return Rate without focus, 0..1 (sheet CR257, ~0.1525 baseline
   * with a royal-city production bonus). Base + spec + city bonus, no focus.
   */
  returnRateNoFocus: number;
  /** Resource Return Rate with focus, 0..1 (sheet CS257, e.g. 0.435). */
  returnRateFocus: number;

  /** Base focus cost per craft before spec reduction (sheet BA9). */
  focusCostBase: number;
  /**
   * Focus efficiency points from masteries/specs. The cost multiplier is
   * 0.5 ^ (efficiency / 10000) (sheet CO259). 40000 pts => x0.0625.
   */
  focusEfficiency: number;

  /** Item value / nutrition used for the station usage fee (sheet AZ9). */
  itemValue: number;
  /** Station fee set by the owner, silver per 100 nutrition (sheet K5). */
  stationFeePer100: number;

  /** Market sales tax rate, 0..1. Premium 0.04, non-premium 0.08. */
  salesTaxRate: number;
  /** Sell order setup fee rate, 0..1 (e.g. 0.025, or 0.015). */
  setupFeeRate: number;
  /** Optional tax/fee applied to material purchase (buy orders). Default 0. */
  buySideTaxRate?: number;

  /** Journal handling (optional). */
  fillJournals?: boolean;
  /** Cost of empty journals over the whole batch (sheet C9*K2*CP246). */
  journalCost?: number;
  /** Number of full journals produced by the batch (sheet AQ9). */
  bookCount?: number;
  /** Sell price of one full journal (sheet AQ37). */
  bookPrice?: number;
}

export interface CraftResult {
  /** Gross material cost for the batch, before return rate (sheet AX9). */
  matCost: number;
  /** Total revenue from selling the crafted items (U9*K2). */
  revenue: number;
  /** Station usage fee for the batch (sheet AY9). */
  stationFee: number;
  /** Market taxes + setup fee on the sale (sheet AW9). */
  taxes: number;
  /** Empty-journal cost included in the run. */
  journalCost: number;
  /** Income from selling full journals. */
  journalIncome: number;
  /** Focus cost multiplier from spec, 0..1. */
  focusMultiplier: number;
  /** Total focus spent on the batch (sheet AL9). */
  focusCost: number;

  /** Profit crafting without focus (sheet AB9). */
  profitNoFocus: number;
  /** Profit crafting with focus (sheet AE9). */
  profitFocus: number;
  /** Profit margin without focus, fraction (sheet AD9). */
  marginNoFocus: number;
  /** Profit margin with focus, fraction (sheet AH9). */
  marginFocus: number;
  /** Silver per focus (sheet AG9) — the key metric for focus crafting. */
  silverPerFocus: number;
}

/** Nutrition coefficient consumed per point of item value when crafting. */
const NUTRITION_COEFFICIENT = 0.1125;

/** Round up like the sheet's CEILING(x, 1). */
const ceil = (x: number) => Math.ceil(x);

/** Gross material cost for one craft action (all materials). */
function matCostPerCraft(materials: MaterialInput[]): number {
  return materials.reduce((sum, m) => sum + m.unitPrice * m.countPerCraft, 0);
}

/** Cost of materials that the return rate can hand back (excludes artifacts). */
function returnableCostPerCraft(materials: MaterialInput[]): number {
  return materials.reduce(
    (sum, m) => sum + (m.noReturn ? 0 : m.unitPrice * m.countPerCraft),
    0,
  );
}

/** Focus cost multiplier from spec/mastery efficiency (sheet 0.5^(eff/10000)). */
export function focusCostMultiplier(focusEfficiency: number): number {
  return Math.pow(0.5, focusEfficiency / 10000);
}

/**
 * Crafting journals filled by a batch. Each craft pours its crafting fame into
 * journals; journals/item = nonArtifactCount * 22.5 * 2^((tier-4)+enchant) / 3600.
 * Verified vs the reference sheet (T4.0 => 3.6 and T5.0 => 7.2 for 72 crafts).
 */
export function journalsFilled(
  resources: { count: number; noReturn?: boolean }[],
  tier: number,
  enchant: number,
  quantity: number,
): number {
  const count = resources.reduce((s, r) => s + (r.noReturn ? 0 : r.count), 0);
  return (quantity * count * 22.5 * Math.pow(2, tier - 4 + enchant)) / 3600;
}

/** One line in a material shopping list. */
export interface MaterialNeed {
  uniqueName: string;
  /** Materials physically consumed before any return (quantity * count). */
  rawCount: number;
  /** Materials you actually need to buy, after return rate hands some back. */
  buyCount: number;
}

/**
 * Materials needed to craft `quantity` items. The return rate feeds recovered
 * materials back into the run, so the amount you must *buy* shrinks with bonus:
 * buy = ceil(quantity * count * (1 - returnRate)). Higher city/event/focus bonus
 * => fewer materials to source for the same number of crafted items.
 */
export function materialNeeds(
  resources: { uniqueName: string; count: number; noReturn?: boolean }[],
  quantity: number,
  returnRate: number,
): MaterialNeed[] {
  return resources.map((r) => {
    const rawCount = quantity * r.count;
    return {
      uniqueName: r.uniqueName,
      rawCount,
      // Artifacts/tokens are never returned, so you must buy the full amount.
      buyCount: r.noReturn ? rawCount : Math.ceil(rawCount * (1 - returnRate)),
    };
  });
}

export function computeCraft(input: CraftInput): CraftResult {
  const {
    quantity: q,
    materials,
    sellPrice,
    outputPerCraft = 1,
    returnRateNoFocus,
    returnRateFocus,
    focusCostBase,
    focusEfficiency,
    itemValue,
    stationFeePer100,
    salesTaxRate,
    setupFeeRate,
    buySideTaxRate = 0,
    fillJournals = false,
    journalCost: rawJournalCost = 0,
    bookCount = 0,
    bookPrice = 0,
  } = input;

  const matCost = q * matCostPerCraft(materials);
  const returnableMatCost = q * returnableCostPerCraft(materials);
  // A craft action yields `outputPerCraft` items (food/potions yield several).
  const revenue = q * outputPerCraft * sellPrice;

  // Station fee (AY9): qty * (feePer100/100) * itemValue * 0.1125, rounded up.
  const stationFee = ceil(q * (stationFeePer100 / 100) * itemValue * NUTRITION_COEFFICIENT);

  // Taxes (AW9): sell-side (sales tax + setup fee) on revenue, plus an optional
  // buy-side tax on materials. Rounded up as a whole.
  const taxes = ceil(revenue * (salesTaxRate + setupFeeRate) + matCost * buySideTaxRate);

  const journalCost = fillJournals ? rawJournalCost : 0;
  const journalIncome = fillJournals ? bookCount * bookPrice : 0;

  const focusMultiplier = focusCostMultiplier(focusEfficiency);
  const focusCost = q * focusCostBase * focusMultiplier;

  // Profit (AB9 / AE9): revenue minus return-adjusted mats, fee, taxes and
  // journal cost, plus journal income. The return rate only gives back
  // returnable materials (artifacts/tokens are always fully consumed).
  const profitAt = (returnRate: number) =>
    revenue - (matCost - returnableMatCost * returnRate + stationFee + taxes + journalCost) + journalIncome;

  const profitNoFocus = profitAt(returnRateNoFocus);
  const profitFocus = profitAt(returnRateFocus);

  // Margin (AD9): profit over effective invested silver (gross cost minus the
  // value the return rate hands back).
  const silverCost = matCost + journalCost + stationFee + taxes;
  const marginAt = (profit: number, returnRate: number) => {
    const denom = silverCost - returnableMatCost * returnRate;
    return denom !== 0 ? profit / denom : 0;
  };

  // SPF (AG9): when the focus-free craft is already profitable, focus only earns
  // the *extra* profit; when it loses silver, focus carries the whole profit.
  const silverPerFocus =
    focusCost === 0
      ? 0
      : profitNoFocus < 0
        ? profitFocus / focusCost
        : (profitFocus - profitNoFocus) / focusCost;

  return {
    matCost,
    revenue,
    stationFee,
    taxes,
    journalCost,
    journalIncome,
    focusMultiplier,
    focusCost,
    profitNoFocus,
    profitFocus,
    marginNoFocus: marginAt(profitNoFocus, returnRateNoFocus),
    marginFocus: marginAt(profitFocus, returnRateFocus),
    silverPerFocus,
  };
}
