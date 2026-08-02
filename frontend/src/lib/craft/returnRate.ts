/**
 * Resource Return Rate (RRR) for Albion crafting.
 *
 * Everything is built from additive *production bonus points* (percentages),
 * then converted with the canonical game formula:
 *     returnRate = points / (100 + points)        // == 1 - 1/(1 + points/100)
 *
 * Focus does NOT use a fitted curve — it simply adds a flat +59 production
 * points to the stack before the same conversion. Verified against the known
 * city values: 18 pts => 18/118 = 15.25% (no focus); (18+59)/177 = 43.5% (focus).
 * Bonus city 33 pts => 24.8% / 47.9%; refining 58 pts => 36.7% / 53.9%.
 * (Sources: Albion Online Wiki "Resource return rate"; albioncodex return-rate guide.)
 *
 * Production bonus points by location:
 *   City    : 18  (royal cities + Caerleon/Brecilien flat crafting bonus)
 *             +15 when crafting the item in its bonus city ("specialized").
 *   Island  : 0   (+15 if specialized — islands have no base production bonus).
 *   Hideout : no royal base. Zone quality + hideout power level (Power Cores
 *             rework, 2024): +1 general point per power level, +2 extra per
 *             level for specialized recipes, plus the zone-quality general bonus.
 *   Plus an event/daily bonus (+10 / +20 points) added on top everywhere.
 */

export type CraftPlace = "city" | "island" | "hideout";

/** Flat crafting production bonus in every royal city / Caerleon / Brecilien. */
const CITY_BASE_PTS = 18;
/** Outlands Rests (Arthur's/Merlyn's/Morgana's) têm base menor que cidades reais. */
const REST_BASE_PTS = 15;
/** Crafting an item in its bonus city (or refining biome), in points. */
const SPEC_CITY_PTS = 15;
/** Focus adds a flat +59 production points to the stack before conversion. */
export const FOCUS_BONUS_PTS = 59;

/**
 * Hideout zone-quality general production bonus, in points (index Q1..Q6).
 * Anchored to the documented zone values (high-tier zones ≈ +15 general).
 */
export const HIDEOUT_QUALITY = [0, 3, 6, 9, 12, 15];
/**
 * Hideout power level general production bonus, in points (index Nv1..Nv9):
 * +1 general per power level. Specialized recipes get +2 extra per level
 * (applied separately in bonusPoints).
 */
export const HIDEOUT_LEVEL = [1, 2, 3, 4, 5, 6, 7, 8, 9];
/** Event/daily crafting bonus options (added on top, in fraction form). */
export const EVENT_BONUS = [
  { value: 0, label: "Nenhum" },
  { value: 0.1, label: "+10% (evento)" },
  { value: 0.2, label: "+20% (evento)" },
];

export interface LocationConfig {
  place: CraftPlace;
  /** Cidade real onde o usuário está produzindo (city/island). */
  city?: string;
  /** Item is specialized in this city/biome (the +15 / hideout spec bonus). */
  specialized: boolean;
  /** Event/daily bonus fraction (0, 0.1, 0.2). */
  eventBonus: number;
  /** Hideout quality 1..6 (only when place === "hideout"). */
  hideoutQuality?: number;
  /** Hideout power level 1..9 (only when place === "hideout"). */
  hideoutLevel?: number;
}

import { isRest } from "./location";

/** Total additive production bonus, in percentage points (no focus). */
export function bonusPoints(loc: LocationConfig): number {
  let pts = loc.eventBonus * 100;
  if (loc.place === "island") {
    pts += loc.specialized ? SPEC_CITY_PTS : 0;
  } else if (loc.place === "city") {
    // Rests têm base 15, cidades reais têm base 18.
    const base = loc.city && isRest(loc.city) ? REST_BASE_PTS : CITY_BASE_PTS;
    pts += base + (loc.specialized ? SPEC_CITY_PTS : 0);
  } else {
    const quality = HIDEOUT_QUALITY[(loc.hideoutQuality ?? 1) - 1] ?? 0;
    const level = HIDEOUT_LEVEL[(loc.hideoutLevel ?? 1) - 1] ?? 0;
    // +1 general per power level, +2 extra per level for specialized recipes.
    pts += quality + level + (loc.specialized ? level * 2 : 0);
  }
  return pts;
}

/** Convert additive bonus points to an actual return rate (0..1). */
export function pointsToReturnRate(points: number): number {
  return points / (100 + points);
}

export function returnRateNoFocus(loc: LocationConfig): number {
  return pointsToReturnRate(bonusPoints(loc));
}

/** Focus adds a flat +59 production points before the standard conversion. */
export function focusRateFromPoints(points: number): number {
  return pointsToReturnRate(points + FOCUS_BONUS_PTS);
}

export function returnRateFocus(loc: LocationConfig): number {
  return focusRateFromPoints(bonusPoints(loc));
}
