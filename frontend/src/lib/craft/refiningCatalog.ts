/** Tipos do catálogo de refino (public/data/refining.json). */

export interface RefiningResource {
  itemId: string;
  count: number;
  enchant: number;
  returnable: boolean;
  isHeart: boolean;
}

export interface RefiningVariant {
  kind: "normal" | "heart";
  focus: number;
  outputCount: number;
  inputs: RefiningResource[];
}

export interface RefiningRecipe {
  key: string;
  family: "fiber" | "hide" | "ore" | "wood" | "stone";
  tier: number;
  enchant: number;
  outputId: string;
  outputCount: number;
  itemValue: number;
  baseFame: number;
  variants: RefiningVariant[];
}

export interface TransmutationRecipe {
  sourceId: string;
  targetId: string;
  silverCost: number;
  itemValue: number;
}

export interface HeartConversion {
  shadowheartTo: string;
  silverCost: number;
}

export interface RefiningCatalog {
  dump_source: string;
  recipes: RefiningRecipe[];
  transmutations: TransmutationRecipe[];
  heartConversions: HeartConversion[];
  refiningCities: Record<string, string>;
  names: Record<string, { en: string; pt: string; es: string }>;
}

let cache: Promise<RefiningCatalog> | null = null;

/** Fetch e cacheia o catálogo de refino (client-side). */
export function loadRefiningCatalog(): Promise<RefiningCatalog> {
  if (!cache) {
    cache = fetch("/data/refining.json").then((r) => {
      if (!r.ok) throw new Error(`Failed to load refining catalog: ${r.status}`);
      return r.json() as Promise<RefiningCatalog>;
    });
  }
  return cache;
}