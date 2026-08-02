/**
 * Local de produção real — a cidade onde o usuário está produzindo.
 *
 * Substitui o antigo `CraftPlace` + toggle `specialized` abstrato. O bônus
 * agora é derivado da combinação receita + cidade escolhida, nunca de um
 * toggle manual. Cajados de gelo em Thetford não recebem +15; em Martlock sim.
 *
 * Os três Rests (Arthur's, Merlyn's, Morgana's) têm bônus de craft próprios
 * (base 15, não 18) e shareiam o mercado do Smuggler's Network. Smuggler's
 * Den não é local de craft, apenas origem de preço.
 */

/** Cidades onde é possível produzir (estação de craft/refino). */
export const PRODUCTION_CITIES = [
  "Bridgewatch",
  "Martlock",
  "Thetford",
  "Fort Sterling",
  "Lymhurst",
  "Caerleon",
  "Brecilien",
  "Arthur's Rest",
  "Merlyn's Rest",
  "Morgana's Rest",
] as const;

export type ProductionCity = (typeof PRODUCTION_CITIES)[number];

/** Rests — bônus base 15 (não 18 como cidades reais), sem bônus de refino. */
export const REST_CITIES: ProductionCity[] = ["Arthur's Rest", "Merlyn's Rest", "Morgana's Rest"];

export function isRest(city: string): boolean {
  return (REST_CITIES as readonly string[]).includes(city);
}

/**
 * Categorias de craft especializadas por Rest (verificado na wiki
 * "Resource return rate", Outlands Rests Bonuses, julho 2026).
 * `craftCategory` do CatalogFamily é comparado contra estas listas.
 */
export const REST_CRAFT_CATEGORIES: Record<string, string[]> = {
  "Arthur's Rest": [
    "axe", "crossbow", "hammer", "mace", "sword", "knuckles",
    "plate_helmet", "plate_armor", "plate_shoes",
  ],
  "Merlyn's Rest": [
    "bow", "dagger", "quarterstaff", "spear", "naturestaff", "shapeshifter",
    "leather_helmet", "leather_armor", "leather_shoes",
  ],
  "Morgana's Rest": [
    "arcanestaff", "cursedstaff", "firestaff", "froststaff", "holystaff",
    "cloth_helmet", "cloth_armor", "cloth_shoes",
  ],
};

/** Local de produção: cidade, ilha (vinculada a uma cidade) ou hideout. */
export type ProductionLocation =
  | { kind: "city"; city: ProductionCity }
  | { kind: "island"; city: ProductionCity }
  | { kind: "hideout"; quality: number; power: number };

export type CraftPlace = "city" | "island" | "hideout";

/** Converte ProductionLocation no CraftPlace legado (returnRate.ts ainda usa). */
export function craftPlaceOf(loc: ProductionLocation): CraftPlace {
  return loc.kind;
}

/**
 * Verifica se a receita é especializada na localização escolhida.
 *
 * Cidade/ilha real: especializado quando a cidade coincide com `bonusCity`.
 * Rest: especializado quando o craftCategory da receita está na lista do Rest.
 * Hideout: especializado quando o item é elegível (não consumível/gathering).
 */
export function isSpecialized(
  loc: ProductionLocation,
  bonusCity: string | null | undefined,
  hideoutEligible: boolean,
  craftCategory: string | null | undefined,
): boolean {
  if (loc.kind === "hideout") return hideoutEligible;
  const city = loc.city;
  // Cidade real: compara com bonusCity do catálogo
  if (!isRest(city)) return bonusCity != null && city === bonusCity;
  // Rest: compara craftCategory com a lista de categorias do Rest
  if (!craftCategory) return false;
  return (REST_CRAFT_CATEGORIES[city] ?? []).includes(craftCategory);
}

/** Nome legível da localização para exibir no carrinho e painel. */
export function locationLabel(loc: ProductionLocation): string {
  if (loc.kind === "hideout") return `HO Q${loc.quality}/Nv${loc.power}`;
  return loc.city;
}

/** Chave para persistir no localStorage. */
export const LOCATION_STORAGE_KEY = "ziggs:craft-location";

/** Lê a última localização do localStorage, ou null se nunca escolheu. */
export function loadLocation(): ProductionLocation | null {
  try {
    const raw = localStorage.getItem(LOCATION_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ProductionLocation;
    if (parsed.kind === "hideout") return parsed;
    if (parsed.kind === "city" || parsed.kind === "island") {
      if (PRODUCTION_CITIES.includes(parsed.city)) return parsed;
    }
    return null;
  } catch {
    return null;
  }
}

/** Persiste a localização no localStorage. */
export function saveLocation(loc: ProductionLocation): void {
  try {
    localStorage.setItem(LOCATION_STORAGE_KEY, JSON.stringify(loc));
  } catch {
    // localStorage indisponível (modo privado etc) — não bloqueia
  }
}