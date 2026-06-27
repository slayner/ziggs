/** Types and helpers for the craftable-item catalog (public/data/catalog.json). */

export interface CatalogResource {
  uniqueName: string;
  count: number;
  /** True for artifacts/tokens that the return rate never gives back. */
  noReturn?: boolean;
}

export interface CatalogVariation {
  /** e.g. "T4_SHOES_GATHERER_ORE" or "T4_SHOES_GATHERER_ORE@2". */
  uniqueName: string;
  tier: number;
  enchant: number;
  itemPower: number;
  /** Base focus cost per craft, before spec reduction (from ao-bin-dumps). */
  focus: number;
  /** Item value used for the station nutrition fee. */
  itemValue: number;
  /** Items produced per craft action (food/potions yield several; default 1). */
  outputPerCraft?: number;
  resources: CatalogResource[];
}

export interface CatalogFamily {
  familyKey: string;
  name: string;
  slot: string | null;
  category: string | null;
  subcategory: string | null;
  /** ao-bin @craftingcategory (e.g. "sword", "plate_armor") → journal profession. */
  craftCategory: string | null;
  /** Royal city that grants the +15% crafting ("biome") bonus for this item. */
  bonusCity: string | null;
  /** "equipment" (tiers × enchant matrix) or "consumable" (food/potions). */
  kind?: "equipment" | "consumable";
  variations: CatalogVariation[];
}

/** Tier.enchant label, e.g. "T4.0", "T5.3". */
export function tierLabel(v: { tier: number; enchant: number }): string {
  return `T${v.tier}.${v.enchant}`;
}

/** Distinct material ids used across a set of variations. */
export function distinctMaterials(variations: CatalogVariation[]): string[] {
  const set = new Set<string>();
  for (const v of variations) for (const r of v.resources) set.add(r.uniqueName);
  return [...set];
}

let cache: Promise<CatalogFamily[]> | null = null;

/** Fetch and cache the catalog (client-side). */
export function loadCatalog(): Promise<CatalogFamily[]> {
  if (!cache) {
    cache = fetch("/data/catalog.json").then((r) => {
      if (!r.ok) throw new Error(`Failed to load catalog: ${r.status}`);
      return r.json() as Promise<CatalogFamily[]>;
    });
  }
  return cache;
}

let namesCache: Promise<Record<string, string>> | null = null;

/** Fetch and cache the id → localized name map. */
export function loadNames(): Promise<Record<string, string>> {
  if (!namesCache) {
    namesCache = fetch("/data/names.json").then((r) => (r.ok ? r.json() : {}));
  }
  return namesCache;
}

let weightsCache: Promise<Record<string, number>> | null = null;

/** Fetch and cache the id → weight (kg) map, for the shopping-list Load. */
export function loadWeights(): Promise<Record<string, number>> {
  if (!weightsCache) {
    weightsCache = fetch("/data/weights.json").then((r) => (r.ok ? r.json() : {}));
  }
  return weightsCache;
}

export interface JournalBase {
  /** Foreman (NPC) silver price of an empty journal. */
  base: number;
  /** Fame capacity of one journal. */
  maxFame: number;
}

let journalsCache: Promise<Record<string, JournalBase>> | null = null;

/** Fetch and cache the journal id → { base npc price, maxFame } map. */
export function loadJournalBase(): Promise<Record<string, JournalBase>> {
  if (!journalsCache) {
    journalsCache = fetch("/data/journals.json").then((r) => (r.ok ? r.json() : {}));
  }
  return journalsCache;
}

const TIER_ADJ = /^(Beginner's?|Novice'?s?|Journeyman'?s?|Adept'?s?|Expert'?s?|Master'?s?|Grandmaster'?s?|Elder'?s?)\s+/;
/** Strip the tier adjective from a localized name ("Adept's Battleaxe" → "Battleaxe"). */
export function shortName(name: string): string {
  return name.replace(TIER_ADJ, "");
}
