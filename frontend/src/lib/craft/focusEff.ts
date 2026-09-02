/**
 * Albion Focus Cost Efficiency (FCE) — turns Destiny-Board spec/mastery levels
 * into the `focusEfficiency` points the engine plugs into 0.5^(FCE/10000).
 *
 * Model (decoded from the FallenAng3L sheet, reference/craft.xlsx):
 *   FCE = ownSpec   × (primary + ownSecondary)
 *       + Σ siblings (siblingSpec × siblingSecondary)
 *       + mastery   × (mastery + mastery2)
 * where each level is 0..100, "secondary" is `secArti` for artifact items, and a
 * group = the items sharing the mastery node (≈ craftingcategory). The in-game
 * floor is focus ×1/16, i.e. FCE caps at 40000 (0.5^4). Verified: the gathering
 * group (Miner Workboots/Cap/Garb) maxes at exactly 40000.
 */

export type CraftType =
  | "MAIN" | "2H" | "HEAD" | "SHOES" | "ARMOR" | "OFF" | "BAG" | "CAPE"
  | "TOOL" | "GATHERER" | "BACKPACK";

interface Coeff {
  primary: number;
  secondary: number;
  secArti: number;
  mastery: number;
  mastery2: number;
}

/** Per-category coefficients (Crafting!CD247:CI258). */
const COEFF: Record<CraftType, Coeff> = {
  MAIN: { primary: 250, secondary: 30, secArti: 15, mastery: 30, mastery2: 0 },
  "2H": { primary: 250, secondary: 30, secArti: 15, mastery: 30, mastery2: 0 },
  HEAD: { primary: 250, secondary: 30, secArti: 15, mastery: 30, mastery2: 0 },
  SHOES: { primary: 250, secondary: 30, secArti: 15, mastery: 30, mastery2: 0 },
  ARMOR: { primary: 250, secondary: 30, secArti: 15, mastery: 30, mastery2: 0 },
  OFF: { primary: 250, secondary: 90, secArti: 15, mastery: 30, mastery2: 0 },
  BAG: { primary: 310, secondary: 30, secArti: 30, mastery: 30, mastery2: 0 },
  CAPE: { primary: 370, secondary: 0, secArti: 0, mastery: 30, mastery2: 0 },
  TOOL: { primary: 0, secondary: 30, secArti: 0, mastery: 60, mastery2: 250 },
  GATHERER: { primary: 250, secondary: 30, secArti: 0, mastery: 60, mastery2: 0 },
  BACKPACK: { primary: 0, secondary: 30, secArti: 0, mastery: 60, mastery2: 250 },
};

/** Maximum FCE — the in-game floor of focus ×1/16 (0.5^4). */
export const MAX_FCE = 40000;

/** Resolve the FCE category from an item id (tier/enchant prefix stripped). */
export function craftTypeOf(uniqueName: string): CraftType | null {
  const id = uniqueName.replace(/^T\d+_/, "").replace(/@\d+$/, "");
  if (/^BACKPACK_GATHERER/.test(id)) return "BACKPACK";
  if (/GATHERER/.test(id)) return "GATHERER"; // gatherer head/armor/shoes
  if (/^2H_TOOL_/.test(id)) return "TOOL";
  if (/^MAIN_/.test(id)) return "MAIN";
  if (/^2H_/.test(id)) return "2H";
  if (/^HEAD_/.test(id)) return "HEAD";
  if (/^SHOES_/.test(id)) return "SHOES";
  if (/^ARMOR_/.test(id)) return "ARMOR";
  if (/^OFF_/.test(id)) return "OFF";
  if (/^BAG/.test(id)) return "BAG";
  if (/^CAPE/.test(id)) return "CAPE";
  return null;
}

export interface FocusEffInput {
  type: CraftType;
  /** The crafted item's own specialization, 0..100. */
  ownSpec: number;
  /** True if the crafted item is an artifact recipe. */
  ownIsArtifact?: boolean;
  /** Mastery node level, 0..100. */
  mastery: number;
  /** Other items in the same mastery group and their specs. */
  siblings?: { spec: number; isArtifact?: boolean }[];
}

const clamp = (n: number) => Math.max(0, Math.min(100, n || 0));

/** Total focus efficiency (FCE) points for one item, capped at MAX_FCE. */
export function computeFocusEfficiency(input: FocusEffInput): number {
  const c = COEFF[input.type];
  const ownSec = input.ownIsArtifact ? c.secArti : c.secondary;
  let fce = clamp(input.ownSpec) * (c.primary + ownSec);
  for (const s of input.siblings ?? []) {
    fce += clamp(s.spec) * (s.isArtifact ? c.secArti : c.secondary);
  }
  fce += clamp(input.mastery) * (c.mastery + c.mastery2);
  return Math.min(MAX_FCE, Math.round(fce));
}
