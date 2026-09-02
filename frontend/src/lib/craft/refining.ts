/**
 * Fórmulas puras de refino — return rate, focus cost e especialização.
 *
 * Refino tem bônus de especialização de cidade +40 (vs +15 do craft),
 * bônus base de cidade de 18 (igual ao craft), e foco +59 (igual).
 * Rests têm base 15 e NÃO têm bônus de refino (só craft).
 *
 * Especialização de refino (Destiny Board): 5 nodes por família (T4-T8).
 * Cada nível dá 250 pontos de efficiency para o próprio tier + 30 para todos.
 */

import { pointsToReturnRate, FOCUS_BONUS_PTS } from "./returnRate";
import { isRest } from "./location";

/** Bônus base de refino em cidade real (igual ao craft: 18 pontos). */
const CITY_REFINE_BASE_PTS = 18;
/** Especialização de refino da cidade (+40, vs +15 do craft). */
const REFINE_SPEC_PTS = 40;

/** Foco adiciona +59 pontos (igual ao craft). */
export { FOCUS_BONUS_PTS };

/** Cidades de bônus de refino por família. */
export const REFINING_CITIES: Record<string, string> = {
  fiber: "Lymhurst",
  hide: "Martlock",
  ore: "Thetford",
  wood: "Fort Sterling",
  stone: "Bridgewatch",
};

/**
 * Pontos de produção de refino (sem foco).
 *
 * Cidade real: 18 base + 40 se especializada (cidade de bônus do recurso).
 * Rest: 15 base, SEM especialização de refino.
 * Ilha: 0 base + 40 se especializada (vinculada à cidade de bônus).
 * Hideout: 15 base + bônus de zona/power (igual ao craft, sem o +40 de refino).
 */
export function refiningBonusPoints(
  place: "city" | "island" | "hideout",
  city: string | undefined,
  family: string,
  eventBonus: number,
  hideoutQuality?: number,
  hideoutLevel?: number,
): number {
  let pts = eventBonus * 100;
  const bonusCity = REFINING_CITIES[family];
  const specialized = city === bonusCity;

  if (place === "island") {
    pts += specialized ? REFINE_SPEC_PTS : 0;
  } else if (place === "city") {
    const base = city && isRest(city) ? 15 : CITY_REFINE_BASE_PTS;
    // Rests NÃO têm bônus de refino (só craft).
    const spec = city && isRest(city) ? 0 : (specialized ? REFINE_SPEC_PTS : 0);
    pts += base + spec;
  } else {
    // Hideout: 15 de refino + zona/power (sem o +40 de cidade).
    const HIDEOUT_QUALITY = [0, 3, 6, 9, 12, 15];
    const HIDEOUT_LEVEL = [1, 2, 3, 4, 5, 6, 7, 8, 9];
    const quality = HIDEOUT_QUALITY[(hideoutQuality ?? 1) - 1] ?? 0;
    const level = HIDEOUT_LEVEL[(hideoutLevel ?? 1) - 1] ?? 0;
    pts += 15 + quality + level; // refino em hideout: 15 base, sem spec +40
  }
  return pts;
}

/** Return rate de refino sem foco. */
export function refiningReturnRateNoFocus(
  place: "city" | "island" | "hideout",
  city: string | undefined,
  family: string,
  eventBonus: number,
  hideoutQuality?: number,
  hideoutLevel?: number,
): number {
  return pointsToReturnRate(refiningBonusPoints(place, city, family, eventBonus, hideoutQuality, hideoutLevel));
}

/** Return rate de refino com foco. */
export function refiningReturnRateFocus(
  place: "city" | "island" | "hideout",
  city: string | undefined,
  family: string,
  eventBonus: number,
  hideoutQuality?: number,
  hideoutLevel?: number,
): number {
  return pointsToReturnRate(refiningBonusPoints(place, city, family, eventBonus, hideoutQuality, hideoutLevel) + FOCUS_BONUS_PTS);
}

/**
 * Eficiência de focus de refino (FCE) por família.
 *
 * Cada família tem 5 specs (T4-T8). Para o tier alvo:
 *   eficiência = 250 × spec[tier] + 30 × (spec[4] + spec[5] + ... + spec[8])
 *
 * Com 5 specs em 100: 25000 + 15000 = 40000.
 * Custo efetivo = foco base × 0.5 ^ (eficiência / 10000).
 */
export function refiningFocusEfficiency(
  _family: string,
  targetTier: number,
  specs: Record<number, number>, // { 4: 100, 5: 80, ... }
): number {
  const own = (specs[targetTier] ?? 0) * 250;
  const shared = [4, 5, 6, 7, 8].reduce((s, t) => s + (specs[t] ?? 0) * 30, 0);
  return own + shared;
}

/** Multiplicador de custo de focus (0..1). 40000 efficiency => ×0.0625. */
export function refiningFocusMultiplier(efficiency: number): number {
  return Math.pow(0.5, efficiency / 10000);
}

/**
 * Custo de focus de uma receita de refino.
 *
 * Para pedra, o foco base acompanha amountcrafted (cada bloco custa o foco
 * do tier). O foco retornado é o total da operação.
 */
export function refiningFocusCost(
  baseFocus: number,
  outputCount: number,
  efficiency: number,
): number {
  // ponytail: ceil é conservador — o jogo arredonda o foco final pra inteiro,
  // e arredondar pra baixo poderia subestimar o custo. Validado contra a
  // planilha de referência (Base focus cost / 2^((specs)/10000)).
  return Math.ceil(outputCount * baseFocus * refiningFocusMultiplier(efficiency));
}

// ─── Testes canônicos ───────────────────────────────────────────────────────

if (import.meta.env?.vitest) {
  // Valores canônicos da wiki (julho 2026):
  // Cidade real sem especialização: 18 pts => 15.25%
  // Cidade real com especialização de refino: 58 pts => 36.71%
  // Cidade real sem spec + foco: 77 => 43.50%
  // Cidade real com spec + foco: 117 => 53.92%
  const eps = 0.001;

  function approx(a: number, b: number) { return Math.abs(a - b) < eps; }

  // Cidade real sem spec
  const r1 = refiningReturnRateNoFocus("city", "Caerleon", "ore", 0);
  console.assert(approx(r1, 0.1525), `sem spec: ${r1}`);

  // Cidade real com spec (Thetford é cidade de bônus de ore)
  const r2 = refiningReturnRateNoFocus("city", "Thetford", "ore", 0);
  console.assert(approx(r2, 0.3671), `com spec: ${r2}`);

  // Cidade real sem spec + foco
  const r3 = refiningReturnRateFocus("city", "Caerleon", "ore", 0);
  console.assert(approx(r3, 0.4350), `sem spec + foco: ${r3}`);

  // Cidade real com spec + foco
  const r4 = refiningReturnRateFocus("city", "Thetford", "ore", 0);
  console.assert(approx(r4, 0.5392), `com spec + foco: ${r4}`);

  // Rest (Arthur's Rest, ore) — base 15, sem spec de refino
  const r5 = refiningReturnRateNoFocus("city", "Arthur's Rest", "ore", 0);
  console.assert(approx(r5, 0.1304), `Rest: ${r5}`);

  // Focus efficiency: 5 specs em 100 => 40000
  const fce = refiningFocusEfficiency("ore", 6, { 4: 100, 5: 100, 6: 100, 7: 100, 8: 100 });
  console.assert(fce === 40000, `FCE: ${fce}`);

  // Focus multiplier em 40000 => 0.0625
  const mult = refiningFocusMultiplier(40000);
  console.assert(approx(mult, 0.0625), `mult: ${mult}`);

  console.log("refining.ts: testes canonicos OK");
}