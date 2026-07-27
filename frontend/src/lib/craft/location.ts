/**
 * Local de produção real — a cidade onde o usuário está produzindo.
 *
 * Substitui o antigo `CraftPlace` + toggle `specialized` abstrato. O bônus
 * agora é derivado da combinação receita + cidade escolhida, nunca de um
 * toggle manual. Cajados de gelo em Thetford não recebem +15; em Martlock sim.
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
] as const;

export type ProductionCity = (typeof PRODUCTION_CITIES)[number];

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
 * Cidade/ilha: especializado quando a cidade coincide com `bonusCity`.
 * Hideout: especializado quando o item é elegível (não consumível/gathering).
 */
export function isSpecialized(
  loc: ProductionLocation,
  bonusCity: string | null | undefined,
  hideoutEligible: boolean,
): boolean {
  if (loc.kind === "hideout") return hideoutEligible;
  return bonusCity != null && loc.city === bonusCity;
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