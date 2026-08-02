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

// ─── Specs de refino (Fase 4) ────────────────────────────────────────────────
// T4-T8 por família. Anônimo e logado usam localStorage; o backend já tem
// `craft_settings.focus_efficiency` (por familyKey) que cobre craft e refino
// juntos, mas refino tem chaves próprias (T4..T8 por família) pra não colidir.
export const REFINING_SPECS_STORAGE_KEY = "ziggs:refining-specs";

/** Lê specs de refino por família do localStorage, ou {} se vazio. */
export function loadRefiningSpecs(): Record<string, Record<number, number>> {
  try {
    const raw = localStorage.getItem(REFINING_SPECS_STORAGE_KEY);
    if (!raw) return {};
    return JSON.parse(raw) as Record<string, Record<number, number>>;
  } catch {
    return {};
  }
}

/** Persiste specs de refino por família no localStorage. */
export function saveRefiningSpecs(specs: Record<string, Record<number, number>>): void {
  try {
    localStorage.setItem(REFINING_SPECS_STORAGE_KEY, JSON.stringify(specs));
  } catch {
    // localStorage indisponível — não bloqueia
  }
}

// ─── Per-item location config (cidade onde crafta + bônus com expiração) ─────
// Cada item (familyKey) tem sua própria escolha de cidade/locação. O bônus
// (eventBonus) é persistente mas expira às 10 UTC do dia em que foi setado —
// horário em que os bônus de craft do jogo mudam. O default de toda arma nova
// é a sua cidade bônus (ex: adaga → Bridgewatch).
export interface PerItemConfig {
  location: ProductionLocation;
  eventBonus: number;
  bonusSetAt: number; // epoch ms — quando o bônus foi alterado pela última vez
  hoQuality: number;  // hideout quality (só relevante se location.kind === "hideout")
  hoLevel: number;    // hideout power level
}

export const PERITEM_STORAGE_KEY = "ziggs:craft-peritem";

type PerItemMap = Record<string, PerItemConfig>;

export function loadPerItemConfig(): PerItemMap {
  try {
    const raw = localStorage.getItem(PERITEM_STORAGE_KEY);
    if (!raw) return {};
    return JSON.parse(raw) as PerItemMap;
  } catch {
    return {};
  }
}

export function savePerItemConfig(cfg: PerItemMap): void {
  try {
    localStorage.setItem(PERITEM_STORAGE_KEY, JSON.stringify(cfg));
  } catch {
    // localStorage indisponível — não bloqueia
  }
}

/** Retorna 10:00 UTC do dia do timestamp dado (mesmo dia). */
function utc10OfSameDay(ts: number): number {
  const d = new Date(ts);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), 10, 0, 0);
}

/** True se o bônus setado em `bonusSetAt` já expirou (depois das 10 UTC hoje). */
export function bonusExpired(bonusSetAt: number, now = Date.now()): boolean {
  const today10 = utc10OfSameDay(now);
  // Se setou antes das 10 UTC de hoje, expira às 10 UTC de hoje.
  // Se setou depois das 10 UTC de hoje, expira às 10 UTC de amanhã.
  return bonusSetAt < today10;
}

/** Default config: cidade bônus da família, eventBonus 0, bonusSetAt 0 (expira já). */
export function defaultPerItem(bonusCity: string | null | undefined): PerItemConfig {
  const city: ProductionCity = (bonusCity && (PRODUCTION_CITIES as readonly string[]).includes(bonusCity))
    ? (bonusCity as ProductionCity)
    : "Lymhurst";
  return { location: { kind: "city", city }, eventBonus: 0, bonusSetAt: 0, hoQuality: 6, hoLevel: 8 };
}

/** Lê config de um item, aplicando expiração do bônus. */
export function getPerItem(cfg: PerItemMap, familyKey: string, bonusCity: string | null | undefined): PerItemConfig {
  const c = cfg[familyKey];
  if (!c) return defaultPerItem(bonusCity);
  if (bonusExpired(c.bonusSetAt)) {
    return { ...c, eventBonus: 0 };
  }
  return c;
}