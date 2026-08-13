"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  computeCraft,
  focusCostMultiplier,
  materialNeeds,
  journalsFilled,
  type CraftResult,
} from "../lib/craft/engine";
import {
  returnRateNoFocus,
  returnRateFocus,
  HIDEOUT_QUALITY,
  HIDEOUT_LEVEL,
  type LocationConfig,
  type CraftPlace,
} from "../lib/craft/returnRate";
import {
  PRODUCTION_CITIES,
  type ProductionCity,
  type ProductionLocation,
  isSpecialized,
  loadRefiningSpecs,
  saveRefiningSpecs,
  loadPerItemConfig,
  savePerItemConfig,
  getPerItem,
  type PerItemConfig,
} from "../lib/craft/location";
import {
  loadCatalog,
  loadNames,
  loadFamilyNames,
  loadWeights,
  loadJournalBase,
  shortName,
  tierLabel,
  distinctMaterials,
  type CatalogFamily,
  type CatalogVariation,
  type JournalBase,
  type FamilyNameMap,
} from "../lib/craft/catalog";
import { computeFocusEfficiency, craftTypeOf } from "../lib/craft/focusEff";
import { specTreeFor, SPEC_EXTRA_FAMILIES, OWN_SPEC_NODE, RECIPE_ALIASES } from "../lib/craft/specTree";
import type { PriceServer, PriceQuote } from "../lib/prices/types";
import { fetchAdpPrices, fetchAdpDemand, fetchAdpPriceSeries, fetchAdpGold } from "../lib/prices/adp";
import { fetchZiggsPrices } from "../lib/prices/ziggs";
import { resolveFreshest } from "../lib/prices/types";
import { toGameName } from "../lib/prices/itemMap";
import { useLang, useT, type TKey } from "../i18n";
import { silver, silverShort, decimal, percent } from "../lib/format";
import { api, type Me } from "../api";
import {
  loadRefiningCatalog,
  type RefiningCatalog,
  type RefiningVariant,
} from "../lib/craft/refiningCatalog";
import {
  refiningReturnRateNoFocus,
  refiningReturnRateFocus,
  refiningFocusEfficiency,
  refiningFocusMultiplier,
  REFINING_CITIES,
} from "../lib/craft/refining";
import { findRoutes, transmuteOptions, baseCostFor, type TransmuteRoute, type TransmuteOption } from "../lib/craft/transmutation";
import AdBanner from "./AdBanner";

const iconUrl = (id: string, size = 64, quality?: number) =>
  `/render/item/${encodeURIComponent(id)}?size=${size}${quality ? `&quality=${quality}` : ""}`;
const EXCELLENT = 4;

const selectAllText = (e: React.MouseEvent<HTMLElement>) => {
  const sel = window.getSelection();
  if (!sel) return;
  const range = document.createRange();
  range.selectNodeContents(e.currentTarget);
  sel.removeAllRanges();
  sel.addRange(range);
};

const CITIES = ["Caerleon", "Bridgewatch", "Martlock", "Thetford", "Fort Sterling", "Lymhurst", "Brecilien", "Arthur's Rest", "Merlyn's Rest", "Morgana's Rest", "Smuggler's Den", "Black Market"];
const CITY_ABBR: Record<string, string> = {
  Lymhurst: "LH", "Fort Sterling": "FS", Thetford: "TF", Caerleon: "CN",
  "Black Market": "BM", Brecilien: "BC", Bridgewatch: "BW", Martlock: "ML",
  "Arthur's Rest": "AR", "Merlyn's Rest": "MR", "Morgana's Rest": "MG",
  "Smuggler's Den": "SD",
};
const cityAbbr = (city?: string) => (city ? CITY_ABBR[city] ?? city : "");
const CITY_COLOR: Record<string, string> = {
  "Fort Sterling": "#d8dadf",
  Lymhurst: "#a7d8a0",
  Thetford: "#c3b0e0",
  Martlock: "#a7c5e6",
  Bridgewatch: "#e6c79c",
  Caerleon: "#e0a9a3",
  Brecilien: "#a3d8d0",
  "Arthur's Rest": "#e6c79c",
  "Merlyn's Rest": "#a7c5e6",
  "Morgana's Rest": "#c3b0e0",
  "Smuggler's Den": "#b8b8bd",
  "Black Market": "#b8b8bd",
};
const cityColor = (city?: string) => (city ? CITY_COLOR[city] : undefined);
function useCityBiome(): Record<string, string> {
  const t = useT();
  return {
    Lymhurst: t("biomeForest"),
    "Fort Sterling": t("biomeMountain"),
    Bridgewatch: t("biomeSteppe"),
    Martlock: t("biomeHighland"),
    Thetford: t("biomeSwamp"),
  };
}
const DEMAND_CITIES = ["Lymhurst", "Fort Sterling", "Bridgewatch", "Martlock", "Thetford", "Arthur's Rest", "Merlyn's Rest", "Morgana's Rest", "Smuggler's Den"];
const SELL_QUALITIES = [1, 2, 3, 4];  // q5 (Masterpiece) raríssima — descartada da média
const FETCH_QUALITIES = [1, 2, 3, 4]; // busca todas as qualities necessárias (1 pra materiais, 2-4 pra venda)
const ALL_QUALITIES = [1, 2, 3, 4, 5];
const TIERS = [4, 5, 6, 7, 8];
const SETUP_FEE = 0.025;

const DAY_MS = 86_400_000;
const WEEK_MS = 7 * DAY_MS;
const nowMs = () => Date.now();
const timeAgo = (ms: number) => {
  const h = Math.floor((nowMs() - ms) / 3_600_000);
  if (h < 1) return "1m";
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
};
const isFresh = (date?: number) => !!date && nowMs() - date <= WEEK_MS;
const isStale = (date?: number) => (date ? nowMs() - date > DAY_MS : false);

const DEFAULT_GROUP_CITY: Record<string, string[]> = {
  PLANKS: ["Fort Sterling"], METALBAR: ["Thetford"], CLOTH: ["Lymhurst"], LEATHER: ["Martlock"], STONEBLOCK: ["Bridgewatch"],
  WOOD: ["Fort Sterling", "Lymhurst", "Bridgewatch", "Thetford", "Martlock"],
  ORE: ["Fort Sterling", "Lymhurst", "Bridgewatch", "Thetford", "Martlock"],
  ROCK: ["Fort Sterling", "Lymhurst", "Bridgewatch", "Thetford", "Martlock"],
  FIBER: ["Fort Sterling", "Lymhurst", "Bridgewatch", "Thetford", "Martlock"],
  HIDE: ["Fort Sterling", "Lymhurst", "Bridgewatch", "Thetford", "Martlock"],
  HEART: ["Fort Sterling", "Lymhurst", "Bridgewatch", "Thetford", "Martlock"],
  // Artefatos (incluindo runas raras de polimorfos) são itens de retorno zero e
  // precisam de liquidez em todas as cidades azuis para formar preço justo.
  Artefatos: ["Lymhurst", "Fort Sterling", "Bridgewatch", "Martlock", "Thetford"],
  POTATO: ["Martlock"], WHEAT: ["Martlock"], FOXGLOVE: ["Martlock"], MILK: ["Martlock"], BUTTER: ["Martlock"],
  CABBAGE: ["Thetford"], AGARIC: ["Thetford"], MULLEIN: ["Thetford"],
  PUMPKIN: ["Lymhurst"], CARROT: ["Lymhurst"], BURDOCK: ["Lymhurst"],
  CORN: ["Bridgewatch"], BEAN: ["Bridgewatch"], TEASEL: ["Bridgewatch"],
  TURNIP: ["Fort Sterling"], YARROW: ["Fort Sterling"], EGG: ["Fort Sterling"],
  FLOUR: ["Martlock"], BREAD: ["Martlock"], COMFREY: ["Caerleon"],
};
  const isArtifact = (id: string) => id.includes("ARTEFACT");
  // ponytail: runas raras de polimorfos (T3_ALCHEMY_RARE_*) são noReturn no
  // catalog. Pro mercado elas se comportam como artefatos: devem buscar preço nas
  // 5 cidades azuis. Já a flag de FCE/artefato permanece estrita (só ARTEFACT),
  // porque SET1/2/3 são armas base, não artefatos.
  const isArtifactLike = (id: string) => isArtifact(id) || id.includes("ALCHEMY_RARE");
const isJournal = (id: string) => id.includes("JOURNAL");
function marketGroup(id: string): string {
  if (isArtifactLike(id)) return "Artefatos";
  if (isJournal(id)) return "Jornais";
  if (id.includes("FACTION_") && id.includes("TOKEN")) return "HEART";
  return id.replace(/^T\d+_/, "").replace(/_LEVEL\d$/, "");
}
const GROUP_ICON: Record<string, string> = {
  PLANKS: "T6_PLANKS", METALBAR: "T6_METALBAR", LEATHER: "T6_LEATHER", CLOTH: "T6_CLOTH", STONEBLOCK: "T6_STONEBLOCK",
  HEART: "T1_FACTION_TOKEN_1", Artefatos: "T4_ARTEFACT_1", Jornais: "T6_JOURNAL_MAGE",
  WOOD: "T6_WOOD", ORE: "T6_ORE", ROCK: "T6_ROCK", FIBER: "T6_FIBER", HIDE: "T6_HIDE",
};
function groupIcon(g: string): string | null {
  return GROUP_ICON[g] ?? null;
}
// ponytail: stone refined output has no enchanted market listing (T4_STONEBLOCK@1
// doesn't exist in the API). All enchants share the base outputId price.
// Outros refinados (CLOTH, LEATHER, METALBAR, PLANKS) e equipamentos MANTÊM @N —
// o ADP tem preço distinto por encantamento pra esses.
const sellPriceId = (uniqueName: string) =>
  uniqueName.includes("_STONEBLOCK") ? uniqueName.replace(/@\d+$/, "") : uniqueName;
// ponytail: extract "4.0" from "T4_CLOTH_LEVEL1@1" or "T4_CLOTH@1"
function tierDot(id: string): string {
  const t = parseInt(id.match(/^T(\d+)/)?.[1] ?? "0");
  const ench = id.includes("_LEVEL") ? parseInt(id.match(/_LEVEL(\d+)/)?.[1] ?? "0") : (parseInt(id.match(/@(\d+)/)?.[1] ?? "0") || 0);
  return `${t}.${ench}`;
}

const PROFESSION_BY_CATEGORY: Record<string, string> = {
  cloth_armor: "MAGE", cloth_helmet: "MAGE", cloth_shoes: "MAGE",
  firestaff: "MAGE", holystaff: "MAGE", arcanestaff: "MAGE", froststaff: "MAGE", cursestaff: "MAGE",
  leather_armor: "HUNTER", leather_helmet: "HUNTER", leather_shoes: "HUNTER",
  bow: "HUNTER", spear: "HUNTER", naturestaff: "HUNTER", dagger: "HUNTER", quarterstaff: "HUNTER",
  plate_armor: "WARRIOR", plate_helmet: "WARRIOR", plate_shoes: "WARRIOR",
  sword: "WARRIOR", axe: "WARRIOR", mace: "WARRIOR", hammer: "WARRIOR", crossbow: "WARRIOR", knuckles: "WARRIOR",
  tools: "TOOLMAKER", bag: "TOOLMAKER", cape: "TOOLMAKER", gatherergear: "TOOLMAKER", offhand: "TOOLMAKER",
};
const professionOf = (cat: string | null) => (cat ? PROFESSION_BY_CATEGORY[cat] ?? null : null);
function professionFromId(id: string): string | null {
  if (id.includes("CAPE") || id.includes("BAG") || id.includes("TOOL")) return "TOOLMAKER";
  if (id.includes("PLATE")) return "WARRIOR";
  if (id.includes("LEATHER")) return "HUNTER";
  if (id.includes("CLOTH")) return "MAGE";
  return null;
}
const journalId = (tier: number, prof: string) => `T${tier}_JOURNAL_${prof}`;
const t7Variation = (f: CatalogFamily) =>
  f.variations.find((v) => v.tier === 7 && v.enchant === 0) ?? f.variations.find((v) => v.tier === 7) ?? f.variations[0];

const isCityBonusKind = (f: CatalogFamily) => f.kind === "consumable" || f.category === "gathering";

// Famílias de refino sintéticas para aparecerem no picker do craft.
// O familyKey usa prefixo "refining:" pra detectar.
const REFINING_FAMILY_KEYS = ["refining:fiber", "refining:hide", "refining:ore", "refining:wood", "refining:stone"] as const;
const REFINING_FAMILY_NAMES: Record<string, { pt: string; en: string; es: string; icon: string }> = {
  "refining:fiber": { pt: "Fibra", en: "Fiber", es: "Fibra", icon: "T6_CLOTH" },
  "refining:hide": { pt: "Couro", en: "Hide", es: "Cuero", icon: "T6_LEATHER" },
  "refining:ore": { pt: "Minério", en: "Ore", es: "Mineral", icon: "T6_METALBAR" },
  "refining:wood": { pt: "Madeira", en: "Wood", es: "Madera", icon: "T6_PLANKS" },
  "refining:stone": { pt: "Pedra", en: "Stone", es: "Piedra", icon: "T6_STONEBLOCK" },
};
function refiningFamilies(lang: "pt" | "en" | "es"): CatalogFamily[] {
  return REFINING_FAMILY_KEYS.map((key) => {
    const info = REFINING_FAMILY_NAMES[key];
    const fam = key.replace("refining:", "");
    return {
      familyKey: key,
      name: info[lang],
      slot: null,
      category: "refining",
      subcategory: null,
      craftCategory: null,
      bonusCity: REFINING_CITIES[fam] ?? null,
      kind: "consumable" as const,
      variations: [{ uniqueName: info.icon, tier: 6, enchant: 0, itemPower: 0, focus: 0, itemValue: 0, resources: [] }],
    };
  });
}
function refiningFamilyFromKey(key: string): "fiber" | "hide" | "ore" | "wood" | "stone" | null {
  const fam = key.replace("refining:", "");
  if (["fiber", "hide", "ore", "wood", "stone"].includes(fam)) return fam as "fiber" | "hide" | "ore" | "wood" | "stone";
  return null;
}

const isRefiningFamilyCat = (f: CatalogFamily | null | undefined) => f?.category === "refining";

// Token de coração por família — derivado do catálogo de refino em runtime
// (não há mapeamento 1:1 com bioma-cidade; vem direto das receitas do dump).
function heartTokenForFamily(cat: RefiningCatalog | null, fam: string): string | null {
  if (!cat) return null;
  const recipe = cat.recipes.find((r) => r.family === fam && r.variants.some((v) => v.kind === "heart"));
  if (!recipe) return null;
  const heart = recipe.variants.find((v) => v.kind === "heart");
  if (!heart) return null;
  const inp = heart.inputs.find((i) => i.isHeart);
  return inp?.itemId ?? null;
}

const displayVariation = (f: CatalogFamily) => {
  if (!isCityBonusKind(f)) return t7Variation(f);
  const noEnch = f.variations.filter((v) => v.enchant === 0);
  return noEnch.reduce((b, v) => (v.tier > b.tier ? v : b), noEnch[0] ?? f.variations[0]);
};
const displayQuality = (f: CatalogFamily) => (isCityBonusKind(f) ? undefined : EXCELLENT);

type Source = "manual" | "api";
type OrderMode = "buy" | "sell";
interface PriceInfo {
  source: Source;
  date?: number;
  city?: string;
  apiValue?: number;
  apiDate?: number;
  apiCity?: string;
  /** De onde o preço veio: "adp" (Albion Data Project) ou "ziggs" (nosso DB). */
  origin?: "adp" | "ziggs";
}

interface Settings {
  premium: boolean;
  stationFeePer100: number;
}

interface Order {
  id: string;
  name: string;
  variation: CatalogVariation;
  qty: number;
  useFocus: boolean;
  rr: number;
  placeLabel: string;
  journalId: string | null;
  // Focus efficiency da família no momento do add — famílias diferentes têm
  // valores diferentes, então o carrinho não pode usar o valor "atual".
  focusEfficiency: number;
  // Custo de focus por item no momento do add — para mostrar o bracket no carrinho.
  focusCostPerItem: number;
  // Rota de transmutação ativa no momento do add (refino) — pra desenhar
  // o caminho na lista de compras. Null se sem transmute.
  transmuteRoute: TransmuteRoute | null;
  transmuteTargetId: string | null;
}

function placeLabel(loc: ProductionLocation, t: (key: TKey) => string): string {
  if (loc.kind === "city") return loc.city;
  if (loc.kind === "island") return `${t("placeIsland")} ${loc.city}`;
  return `HO Q${loc.quality}/Nv${loc.power}`;
}

export default function CraftCalculator({ initialCartCode }: { initialCartCode?: string } = {}) {
  const t = useT();
  const { server, lang } = useLang();
  return <CraftMode t={t} server={server} lang={lang} initialCartCode={initialCartCode} />;
}

function CraftMode({ t, server, lang, initialCartCode }: { t: (key: TKey) => string; server: PriceServer; lang: "pt" | "en" | "es"; initialCartCode?: string }) {
  const CITY_BIOME = useCityBiome();
  const cityBiome = (city?: string) => (city ? CITY_BIOME[city] : undefined);
  const [families, setFamilies] = useState<CatalogFamily[] | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [familyNames, setFamilyNames] = useState<FamilyNameMap>({});
  const [weights, setWeights] = useState<Record<string, number>>({});
  const [journalBase, setJournalBase] = useState<Record<string, JournalBase>>({});
  const [refiningCatalog, setRefiningCatalog] = useState<RefiningCatalog | null>(null);
  // ponytail: specs T4..T8 por família, gravadas em localStorage
  const [refiningSpecs, setRefiningSpecs] = useState<Record<string, Record<number, number>>>(() => loadRefiningSpecs());
  // Coração: preço manual por família (fiber/hide/...). Não-confundir: preço
  // do heart token (T1_FACTION_X_TOKEN_1), NÃO do shadowheart.
  const [heartPriceByFamily, setHeartPriceByFamily] = useState<Record<string, number>>({});
  const [heartPriceManual, setHeartPriceManual] = useState<Record<string, boolean>>({});
  // ponytail: escolhas de refino ativadas pelo usuário por variante (sessão).
  const [activeTransmute, setActiveTransmute] = useState<Record<string, TransmuteRoute>>({});
  const [activeHeart, setActiveHeart] = useState<Record<string, boolean>>({});
  const [familyKey, setFamilyKey] = useState("MAIN_AXE");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [search, setSearch] = useState("");

  const [batchQty, setBatchQty] = useState(30);
  const [useFocus, setUseFocus] = useState(true);

  const [perItemConfig, setPerItemConfig] = useState<Record<string, PerItemConfig>>(() => loadPerItemConfig());

  const [premium, setPremium] = useState(true);
  const [stationFeePer100, setStationFeePer100] = useState(1000);
  const [goldPrice, setGoldPrice] = useState(0);
  const [ignoredJournalTiers, setIgnoredJournalTiers] = useState<Set<number>>(new Set([4, 5]));
  // Focus efficiency é por arma (familyKey), não global — reflete a
  // especialização real do jogo (cada arma tem a própria). Persistido no
  // backend só quando logado (ver useEffect de "me" abaixo).
  const [focusEfficiencyByFamily, setFocusEfficiencyByFamily] = useState<Record<string, number>>({});
  const [me, setMe] = useState<Me | null>(null);

  // ponytail: server now comes from global context (topbar dropdown)
  const [sellCities, setSellCitiesState] = useState<string[]>(["Black Market"]);
  const sellCitiesUserOverrideRef = useRef(false);
  const setSellCities = (v: string[]) => {
    sellCitiesUserOverrideRef.current = true;
    setSellCitiesState(v);
  };
  const [groupBuyCities, setGroupBuyCities] = useState<Record<string, string[]>>({});
  const [groupOrder, setGroupOrder] = useState<Record<string, OrderMode>>({});
  const [sellOrderMode, setSellOrderMode] = useState<OrderMode>("buy"); // Black Market = buy order default
  const [matPrices, setMatPrices] = useState<Record<string, number | undefined>>({});
  const [sellPrices, setSellPrices] = useState<Record<string, number | undefined>>({});
  const [fullJournalPrices, setFullJournalPrices] = useState<Record<string, number>>({});
  const [matMeta, setMatMeta] = useState<Record<string, PriceInfo>>({});
  const [sellMeta, setSellMeta] = useState<Record<string, PriceInfo>>({});
  const [demand, setDemand] = useState<Record<string, number>>({});
  const [loadingPrices, setLoadingPrices] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const [cart, setCart] = useState<Order[]>([]);

  const pickerRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const matMetaRef = useRef(matMeta);
  const sellMetaRef = useRef(sellMeta);
  useEffect(() => {
    matMetaRef.current = matMeta;
    sellMetaRef.current = sellMeta;
  }, [matMeta, sellMeta]);

  // ponytail: refining names keyed by `@N` suffix (e.g. T4_LEATHER_LEVEL1@1),
  // but outputId has no suffix. Pre-build a lookup by stripping @N from keys.
  const refiningNameByOutput = useMemo(() => {
    const m: Record<string, { en: string; pt: string; es: string }> = {};
    if (!refiningCatalog?.names) return m;
    for (const [k, v] of Object.entries(refiningCatalog.names)) {
      const stripped = k.replace(/@\d+$/, "");
      if (stripped !== k && !m[stripped]) m[stripped] = v;
    }
    return m;
  }, [refiningCatalog]);
  const nameOf = (id: string) => {
    const base = id.split("@")[0];
    const refName = refiningNameByOutput[id] ?? refiningCatalog?.names?.[id] ?? refiningCatalog?.names?.[base];
    const langName = refName ? (refName[lang] ?? refName.en) : names[base];
    return shortName(langName ?? id);
  };
  const citiesForGroup = (g: string): string[] =>
    groupBuyCities[g]?.length ? groupBuyCities[g] : (DEFAULT_GROUP_CITY[g] ?? ["Caerleon"]);
  const orderForGroup = (g: string): OrderMode => groupOrder[g] ?? "sell";
  const citiesForMat = (id: string): string[] => citiesForGroup(marketGroup(id));

  useEffect(() => {
    const lang = server === "west" || server === "east" ? "en" : "pt";
    loadCatalog().then((fams) => {
      const all = [...fams, ...refiningFamilies(lang)];
      setFamilies(all);
      if (!all.some((f) => f.familyKey === familyKey)) setFamilyKey(all[0]?.familyKey);
      loadRefiningCatalog().then(setRefiningCatalog).catch(() => {});
    });
    loadNames().then(setNames);
    loadFamilyNames().then(setFamilyNames);
    loadWeights().then(setWeights);
    loadJournalBase().then(setJournalBase);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Deep link ?view=craft&cart=CODE: recupera um carrinho compartilhado.
  // Roda quando o catálogo terminou de carregar (variações disponíveis pra
  // casar uniqueName). Itens de famílias que sumiram do catálogo são pulados.
  // rr/focus são aproximados do estado atual (o usuário pode re-fetchar preços).
  useEffect(() => {
    if (!initialCartCode || !families) return;
    let alive = true;
    api.loadCraftCart(initialCartCode)
      .then((res) => {
        if (!alive || !Array.isArray(res.items) || !res.items.length) return;
        const restored: Order[] = [];
        for (const it of res.items) {
          const fam = families.find((f) => f.variations.some((v) => v.uniqueName === it.uniqueName));
          const variation = fam?.variations.find((v) => v.uniqueName === it.uniqueName);
          if (!fam || !variation) continue;
          restored.push({
            id: `restore-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
            name: familyDisplayName(fam),
            variation,
            qty: it.qty,
            useFocus: it.useFocus,
            rr: it.useFocus ? rrFocus : rrNoFocus,
            placeLabel: it.placeLabel,
            journalId: it.journalId,
            focusEfficiency: focusFce,
            focusCostPerItem: variation.focus * focusMult,
            transmuteRoute: null,
            transmuteTargetId: it.transmuteTargetId,
          });
        }
        if (restored.length) setCart(restored);
      })
      .catch(() => {});
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialCartCode, families]);

  useEffect(() => {
    api.me().then((m) => {
      setMe(m);
      if (m) api.getCraftFocusEfficiency().then(setFocusEfficiencyByFamily);
    });
  }, []);

  // ponytail: gold price auto-fetched from ADP (no manual input). Refreshes
  // on server switch and on every market refresh.
  useEffect(() => {
    fetchAdpGold(server).then(setGoldPrice).catch(() => {});
  }, [server]);

  // ponytail: quando o catálogo de refino carrega, reescreve as famílias
  // sintéticas com variações reais (uma CatalogVariation por tier×encant,
  // usando a variante "normal" da receita). Variante "heart" fica guardada
  // separada — aparece como dica visual, não como linha própria.
  useEffect(() => {
    if (!refiningCatalog || !families) return;
    const famKeyByRefiningKey: Record<string, string> = {};
    for (const k of REFINING_FAMILY_KEYS) famKeyByRefiningKey[k] = k.replace("refining:", "");
    setFamilies((prev) => (prev ?? []).map((f) => {
      const fam = famKeyByRefiningKey[f.familyKey];
      if (!fam) return f;
      const recipes = refiningCatalog.recipes.filter((r) => r.family === fam);
      const variations: CatalogVariation[] = recipes.map((r) => {
        const normalV = r.variants.find((v) => v.kind === "normal");
        const v = normalV ?? r.variants[0];
        // ponytail: stone refining has same outputId for all enchants (T4_STONEBLOCK),
        // unlike leather/cloth which use _LEVEL1/_LEVEL2 suffixes. Append @enchant
        // to guarantee unique React keys and correct price lookups.
        const uniqueName = r.enchant > 0 && !r.outputId.includes("_LEVEL") && !r.outputId.includes("@")
          ? `${r.outputId}@${r.enchant}`
          : r.outputId;
        return {
          uniqueName,
          tier: r.tier,
          enchant: r.enchant,
          itemPower: 0,
          focus: v.focus,
          itemValue: r.itemValue,
          outputPerCraft: r.outputCount,
          resources: v.inputs.map((inp) => ({
            uniqueName: inp.itemId,
            count: inp.count,
            noReturn: !inp.returnable,
          })),
        };
      });
      return { ...f, variations };
    }));
  }, [refiningCatalog]);

  useEffect(() => { saveRefiningSpecs(refiningSpecs); }, [refiningSpecs]);

  // Atualiza sempre (funciona sem login); só persiste no blur, e só se
  // logado, pra não disparar um PUT por tecla digitada.
  function setFocusEff(key: string, value: number) {
    setFocusEfficiencyByFamily((prev) => ({ ...prev, [key]: value }));
  }
  function commitFocusEfficiency() {
    if (me) api.setCraftFocusEfficiency(focusEfficiencyByFamily);
  }

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      const t = e.target as Node;
      if (pickerRef.current?.contains(t)) return;
      if (searchRef.current?.contains(t)) return;
      setPickerOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const family = useMemo(() => families?.find((f) => f.familyKey === familyKey) ?? null, [families, familyKey]);

  // Local de produção por item (familyKey). Cada item tem sua própria cidade
  // e bônus. O bônus expira às 10 UTC (horário do jogo). Default de item novo
  // = cidade bônus da família (ex: adaga → Bridgewatch).
  const familyBonusCity = family?.bonusCity ?? null;
  const currentPerItem: PerItemConfig = useMemo(
    () => getPerItem(perItemConfig, familyKey, familyBonusCity),
    [perItemConfig, familyKey, familyBonusCity],
  );
  const productionLocation: ProductionLocation = currentPerItem.location;
  const eventBonus: number = currentPerItem.eventBonus;
  const hoQuality: number = currentPerItem.hoQuality;
  const hoLevel: number = currentPerItem.hoLevel;
  const setProductionLocation = (loc: ProductionLocation) => {
    setPerItemConfig((prev) => ({
      ...prev,
      [familyKey]: { ...getPerItem(prev, familyKey, familyBonusCity), location: loc },
    }));
  };
  const setEventBonus = (v: number) => {
    setPerItemConfig((prev) => ({
      ...prev,
      [familyKey]: { ...getPerItem(prev, familyKey, familyBonusCity), eventBonus: v, bonusSetAt: Date.now() },
    }));
  };
  const setHoQuality = (v: number) => {
    setPerItemConfig((prev) => ({ ...prev, [familyKey]: { ...getPerItem(prev, familyKey, familyBonusCity), hoQuality: v } }));
  };
  const setHoLevel = (v: number) => {
    setPerItemConfig((prev) => ({ ...prev, [familyKey]: { ...getPerItem(prev, familyKey, familyBonusCity), hoLevel: v } }));
  };
  useEffect(() => { savePerItemConfig(perItemConfig); }, [perItemConfig]);
  const place: CraftPlace = productionLocation.kind;
  const craftCity: ProductionCity | undefined =
    productionLocation.kind !== "hideout" ? productionLocation.city : undefined;

  // ponytail: smart sell-city default — segue a cidade bônus da família (fallback Lymhurst).
  // Só atualiza se o usuário não mexeu manualmente (sellCitiesUserOverrideRef).
  useEffect(() => {
    if (sellCitiesUserOverrideRef.current) return;
    setSellCitiesState([family?.bonusCity ?? "Lymhurst"]);
  }, [family?.bonusCity]);

  const variations = useMemo(
    () => (family ? [...family.variations].sort((a, b) => a.tier - b.tier || a.enchant - b.enchant) : []),
    [family],
  );
  const prof = professionOf(family?.craftCategory ?? null) ?? (family ? professionFromId(family.variations[0].uniqueName) : null);
  const isConsumable = family?.kind === "consumable";

  const artifactByTier = useMemo(() => {
    const m = new Map<number, string[]>();
    for (const v of variations) {
      if (v.enchant !== 0) continue;
      const arts = v.resources.filter((r) => r.noReturn);
      if (arts.length) m.set(v.tier, arts.map((r) => r.uniqueName));
    }
    return m;
  }, [variations]);

  const mats = family ? distinctMaterials(family.variations) : [];
  // Refino não usa jornais — o foco compensa mais sem eles.
  const journalIds = prof && !isRefiningFamilyCat(family) ? [...new Set(variations.map((v) => journalId(v.tier, prof)))] : [];

  // "Árvore" de focus efficiency: base + variantes da mesma árvore do
  // destiny board (specTree.ts), indexada por familyKey. Itens fora de qualquer
  // árvore (ferramentas, bolsas de coletor, No Spec, Royal) não têm grupo —
  // painel não aparece. Comida/poção usam a árvore Chef/Alchemist (o ownSpec
  // vem de OWN_SPEC_NODE, que mapeia a família ao seu grupo).
  const familyMap = useMemo(() => {
    const m = new Map<string, CatalogFamily>();
    for (const f of families ?? []) m.set(f.familyKey, f);
    for (const f of SPEC_EXTRA_FAMILIES) m.set(f.familyKey, f);
    return m;
  }, [families]);
  const siblings = useMemo(() => {
    if (!family) return [];
    const tree = specTreeFor(familyKey);
    if (!tree) return [];
    const ordered = tree.order.map((k) => familyMap.get(k)).filter(Boolean) as CatalogFamily[];
    return ordered;
  }, [family, familyMap, familyKey]);

  const baseFamily = siblings[0];
  const baseVar = baseFamily ? baseFamily.variations.find((v) => v.tier === 8 && v.enchant === 0) ?? null : null;

  const hideoutEligible = !!family && !isCityBonusKind(family);
  // Bônus derivado da cidade real, não de um toggle abstrato:
  // cajados de gelo em Thetford = sem bônus; em Martlock = +15.
  const autoSpecialized = isSpecialized(productionLocation, family?.bonusCity, hideoutEligible, family?.craftCategory);
  const bonusBiome = hideoutEligible ? cityBiome(family?.bonusCity ?? undefined) : undefined;
  const location: LocationConfig = {
    place,
    city: craftCity,
    specialized: autoSpecialized,
    eventBonus,
    hideoutQuality: hoQuality,
    hideoutLevel: hoLevel,
  };
  useEffect(() => {
    if (productionLocation.kind === "hideout" && family && !hideoutEligible) {
      setProductionLocation({ kind: "city", city: "Caerleon" });
    }
  }, [productionLocation, hideoutEligible, family]);
  const refiningFamKey = isRefiningFamilyCat(family) ? refiningFamilyFromKey(familyKey) : null;
  const heartId = refiningFamKey ? heartTokenForFamily(refiningCatalog, refiningFamKey) : null;
  // ponytail: heart token é tratado como material normal — entra no fetch de preços
  // e no painel de mercado (grupo HEART com 5 cidades reais).
  const matsWithHeart = heartId ? [...mats, heartId] : mats;
  const groups = [...new Set([...matsWithHeart, ...journalIds].map(marketGroup))];
  const rrNoFocus = useMemo(() =>
    refiningFamKey
      ? refiningReturnRateNoFocus(place, craftCity, refiningFamKey, eventBonus, hoQuality, hoLevel)
      : returnRateNoFocus(location),
  [refiningFamKey, place, craftCity, eventBonus, hoQuality, hoLevel]);
  const rrFocus = useMemo(() =>
    refiningFamKey
      ? refiningReturnRateFocus(place, craftCity, refiningFamKey, eventBonus, hoQuality, hoLevel)
      : returnRateFocus(location),
  [refiningFamKey, place, craftCity, eventBonus, hoQuality, hoLevel, location]);
  // ponytail: spec é 0..100 por arma; FCE real vem da árvore (irmãs + mastery)
  // via focusEff.ts. mastery=0 porque o usuário só informa spec de armas.
  // Refino: FCE é por tier (T4..T8), não por família — diferente de craft.
  const focusFce = useMemo(() => {
    if (!family) return 0;
    if (refiningFamKey) {
      const specs = refiningSpecs[refiningFamKey] ?? {};
      // FCE "representativo" = tier 7 (mesma convenção do painel de siblings base)
      return refiningFocusEfficiency(refiningFamKey, 7, specs);
    }
    const type = craftTypeOf(family.variations[0].uniqueName);
    // ponytail: ownSpecNode = o próprio familyKey (armas/armaduras/coleta) ou o
    // grupo (CHEF_SOUP, ALCH_HEAL) quando o item é comida/poção. FCE usa o spec
    // do nó, e exclui esse nó das irmãs.
    const ownSpecNode = OWN_SPEC_NODE[familyKey] ?? familyKey;
    const ownSpec = focusEfficiencyByFamily[ownSpecNode] ?? 0;
    if (!type) return ownSpec * 100;
    const familyIsArtifact = (f: CatalogFamily) =>
      f.variations.some((v) => v.resources.some((r) => isArtifact(r.uniqueName)));
    return computeFocusEfficiency({
      type,
      ownSpec,
      ownIsArtifact: familyIsArtifact(family),
      mastery: 0,
      siblings: siblings
        .filter((f) => f.familyKey !== ownSpecNode)
        .map((f) => ({ spec: focusEfficiencyByFamily[f.familyKey] ?? 0, isArtifact: familyIsArtifact(f) })),
    });
  }, [family, familyKey, siblings, focusEfficiencyByFamily, refiningFamKey, refiningSpecs]);
  // FCE por tier, usado só em refino (o craft tem valor único).
  const refiningFceByTier = useMemo(() => {
    if (!refiningFamKey) return null;
    const specs = refiningSpecs[refiningFamKey] ?? {};
    const m: Record<number, number> = {};
    for (const t of [4, 5, 6, 7, 8]) m[t] = refiningFocusEfficiency(refiningFamKey, t, specs);
    return m;
  }, [refiningFamKey, refiningSpecs]);
  // Render do item refinado por tier (sem encantamento) — ícone no painel de specs.
  const refiningIconByTier = useMemo(() => {
    const m: Record<number, string> = {};
    for (const v of variations) if (v.enchant === 0) m[v.tier] ??= v.uniqueName;
    return m;
  }, [variations]);
  const focusMult = refiningFamKey
    ? refiningFocusMultiplier(refiningFceByTier?.[7] ?? 0)
    : focusCostMultiplier(focusFce);

  // ponytail: Map<outputId+enchant, RefiningVariant> — variante heart por receita.
  // Stone refining reuses the same outputId for all enchants, so key must include enchant.
  const heartVariantByKey = useMemo(() => {
    const m = new Map<string, RefiningVariant>();
    if (!refiningCatalog) return m;
    for (const r of refiningCatalog.recipes) {
      const hv = r.variants.find((v) => v.kind === "heart");
      if (hv) m.set(`${r.outputId}@${r.enchant}`, hv);
    }
    return m;
  }, [refiningCatalog]);

  // Rotas de transmutação por outputId do material bruto primário.
  // horse: só precisamos das rotas; o cheapestOption compara com preço direto.
  const transmuteRoutesByTarget = useMemo(() => {
    const m = new Map<string, ReturnType<typeof findRoutes>>();
    if (!refiningCatalog || !refiningFamKey) return m;
    for (const r of refiningCatalog.recipes) {
      if (r.family !== refiningFamKey) continue;
      const normalV = r.variants.find((v) => v.kind === "normal");
      if (!normalV) continue;
      // Material bruto primário = primeiro input não-heart, maior count.
      const primary = normalV.inputs
        .filter((i) => !i.isHeart)
        .sort((a, b) => b.count - a.count)[0];
      if (!primary) continue;
      if (m.has(primary.itemId)) continue;
      m.set(primary.itemId, findRoutes(refiningCatalog.transmutations, primary.itemId, stationFeePer100));
    }
    return m;
  }, [refiningCatalog, refiningFamKey, stationFeePer100]);

  // Preço efetivo do coração: manual tem precedência sobre mercado.
  const effectiveHeartPrice = refiningFamKey
    ? (heartPriceByFamily[refiningFamKey] ?? matPrices[heartTokenForFamily(refiningCatalog, refiningFamKey) ?? ""] ?? 0)
    : 0;

  const settings: Settings = { premium, stationFeePer100 };
  const PREMIUM_GOLD = 3750;
  const MONTHLY_FOCUS = 300_000;
  const minSpf = goldPrice > 0 ? (goldPrice * PREMIUM_GOLD) / MONTHLY_FOCUS : 0;

  const computeWithPrices = (v: CatalogVariation, prices: Record<string, number | undefined>): CraftResult =>
    computeCraft({
      quantity: batchQty,
      materials: v.resources.map((r) => ({ uniqueName: r.uniqueName, unitPrice: prices[r.uniqueName] ?? 0, countPerCraft: r.count, noReturn: r.noReturn })),
      sellPrice: sellPrices[sellPriceId(v.uniqueName)] ?? 0,
      outputPerCraft: v.outputPerCraft ?? 1,
      returnRateNoFocus: rrNoFocus,
      returnRateFocus: rrFocus,
      focusCostBase: v.focus,
      // Refino: FCE é por tier (pc material diferente por tier awakens).
      focusEfficiency: refiningFceByTier?.[v.tier] ?? focusFce,
      itemValue: v.itemValue,
      stationFeePer100,
      salesTaxRate: premium ? 0.04 : 0.08,
      setupFeeRate: SETUP_FEE,
    });

  const familyDisplayName = (f: CatalogFamily) =>
    familyNames[f.familyKey]?.[lang] ?? familyNames[f.familyKey]?.en ?? f.name;

  const filteredFamilies = useMemo(() => {
    if (!families) return [];
    const sorted = [...families].sort((a, b) =>
      familyDisplayName(a).localeCompare(familyDisplayName(b), lang, { sensitivity: "base" })
    );
    const q = search.trim().toLowerCase();
    if (!q) return sorted;
    return sorted.filter((f) =>
      familyDisplayName(f).toLowerCase().includes(q) ||
      f.familyKey.toLowerCase().includes(q) ||
      (RECIPE_ALIASES[f.familyKey] ?? []).some((a) => a.includes(q))
    );
  }, [families, search, familyNames, lang]);

  function setMat(id: string, value: number | undefined) {
    const m = matMeta[id];
    if (value == null) {
      if (m?.apiValue != null) {
        setMatPrices((p) => ({ ...p, [id]: m.apiValue! }));
        setMatMeta((prev) => ({ ...prev, [id]: { source: "api", date: m.apiDate, city: m.apiCity, apiValue: m.apiValue, apiDate: m.apiDate, apiCity: m.apiCity } }));
        return;
      }
    }
    setMatPrices((p) => ({ ...p, [id]: value }));
    setMatMeta((prev) => ({ ...prev, [id]: { source: "manual", date: Date.now(), apiValue: m?.apiValue, apiDate: m?.apiDate, apiCity: m?.apiCity } }));
    // ponytail: se o usuário alterar o preço do material bruto primário, limpa
    // transmutação ativa para a linha. O badge volta a ser sugestão.
    setActiveTransmute((prev) => {
      const next = { ...prev };
      for (const key of Object.keys(next)) {
        if (next[key]?.sourceId === id) delete next[key];
      }
      return next;
    });
  }
  function setSell(id: string, value: number | undefined) {
    const m = sellMeta[id];
    if (value == null) {
      if (m?.apiValue != null) {
        setSellPrices((p) => ({ ...p, [id]: m.apiValue! }));
        setSellMeta((prev) => ({ ...prev, [id]: { source: "api", date: m.apiDate, city: m.apiCity, apiValue: m.apiValue, apiDate: m.apiDate, apiCity: m.apiCity } }));
        return;
      }
    }
    setSellPrices((p) => ({ ...p, [id]: value }));
    setSellMeta((prev) => ({ ...prev, [id]: { source: "manual", date: Date.now(), apiValue: m?.apiValue, apiDate: m?.apiDate, apiCity: m?.apiCity } }));
  }

  const priced = (v: CatalogVariation) =>
    sellPrices[sellPriceId(v.uniqueName)] != null && v.resources.every((r) => matPrices[r.uniqueName] != null);

  function addOrder(v: CatalogVariation, transmuteRoute: TransmuteRoute | null = null, transmuteTargetId: string | null = null) {
    if (!family) return;
    setCart((c) => [
      ...c,
      {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        name: familyDisplayName(family),
        variation: v,
        qty: batchQty,
        useFocus,
        rr: useFocus ? rrFocus : rrNoFocus,
        placeLabel: placeLabel(productionLocation, t),
        journalId: prof ? journalId(v.tier, prof) : null,
        focusEfficiency: focusFce,
        focusCostPerItem: v.focus * focusMult,
        transmuteRoute,
        transmuteTargetId,
      },
    ]);
  }

  // Auto-save do carrinho: debounce de 1.5s após a última mudança. Salva no
  // backend e põe o código na URL (replaceState) — o link fica compartilhável
  // a qualquer momento, sem botão. Carrinho vazio limpa o ?cart= da URL.
  const cartRef = useRef(cart);
  cartRef.current = cart;
  useEffect(() => {
    if (!cart.length) {
      const sp = new URLSearchParams(window.location.search);
      if (sp.has("cart")) {
        sp.delete("cart");
        const qs = sp.toString();
        history.replaceState(history.state, "", qs ? `/?${qs}` : "/");
      }
      return;
    }
    const t = setTimeout(async () => {
      try {
        const { code } = await api.saveCraftCart(
          cartRef.current.map((o) => ({
            uniqueName: o.variation.uniqueName,
            qty: o.qty,
            useFocus: o.useFocus,
            placeLabel: o.placeLabel,
            journalId: o.journalId,
            transmuteTargetId: o.transmuteTargetId,
          })),
        );
        const sp = new URLSearchParams(window.location.search);
        sp.set("view", "craft");
        sp.set("cart", code);
        history.replaceState(history.state, "", `/?${sp.toString()}`);
      } catch { /* best-effort — o link só não atualiza, usuário não percebe */ }
    }, 300);
    return () => clearTimeout(t);
  }, [cart]);

  function toggleJournalTier(t: number) {
    setIgnoredJournalTiers((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  }

  async function fetchMarket() {
    if (!family) return;
    const matIds = [...matsWithHeart, ...journalIds];
    const varIds = [...new Set(variations.map((v) => sellPriceId(v.uniqueName)))];
    const fullIds = journalIds.map((id) => `${id}_FULL`);
    const allItems = [...new Set([...matIds, ...varIds, ...fullIds])];
    const allMatCities = [...new Set(matIds.flatMap(citiesForMat))];
    const JOURNAL_5CITY = ["Lymhurst", "Fort Sterling", "Bridgewatch", "Martlock", "Thetford"];
    const locs = [...new Set([...sellCities, ...allMatCities, ...JOURNAL_5CITY])];

    setLoadingPrices(true);
    setFetchError(null);
    try {
      // Busca ADP e nosso backend em paralelo — resolveFreshest fica com o
      // preço de price_date mais recente pra cada (item, city, quality).
      const [adpQuotes, ziggsQuotes] = await Promise.all([
        fetchAdpPrices(server, allItems, locs, FETCH_QUALITIES),
        fetchZiggsPrices(allItems).catch((e) => {
          console.warn("[craft] fetchZiggsPrices falhou:", e);
          return [] as PriceQuote[];
        }),
      ]);
      console.log("[craft] prices: adp=", adpQuotes.length, "ziggs=", ziggsQuotes.length,
        "ziggs sample:", ziggsQuotes.slice(0, 3));
      const merged = resolveFreshest(adpQuotes, ziggsQuotes);
      const q = new Map<string, PriceQuote>();
      for (const x of merged) q.set(`${x.itemId}|${x.city}|${x.quality}`, x);

      const curMatMeta = matMetaRef.current;
      const curSellMeta = sellMetaRef.current;

      // Materials: pick the cheapest city (min price across selected buy cities)
      const matVal = (id: string) => {
        const gameId = toGameName(id);
        const cities = citiesForMat(id);
        const useBuy = orderForGroup(marketGroup(id)) === "buy";
        let best: { v: number; date: number; city: string; origin: "adp" | "ziggs" } | null = null;
        for (const city of cities) {
          const quote = q.get(`${gameId}|${city}|1`);
          if (!quote || !isFresh(quote.updatedAt)) continue;
          const v = useBuy ? quote.buyMax : quote.sellMin;
          if (!v) continue;
          if (!best || v < best.v) best = { v, date: quote.updatedAt, city, origin: quote.source === "local" ? "ziggs" : "adp" };
        }
        return best;
      };
      setMatPrices((prev) => {
        const next = { ...prev };
        for (const id of matIds) {
          if (curMatMeta[id]?.source === "manual") continue;
          if (isJournal(id)) continue; // jornais: 5-city avg abaixo
          const r = matVal(id);
          if (r) next[id] = r.v;
        }
        return next;
      });
      setMatMeta((prev) => {
        const next = { ...prev };
        for (const id of matIds) {
          if (prev[id]?.source === "manual") continue;
          if (isJournal(id)) continue; // jornais: 5-city avg abaixo
          const r = matVal(id);
          if (r) next[id] = { source: "api", date: r.date, city: r.city, apiValue: r.v, apiDate: r.date, apiCity: r.city, origin: r.origin };
        }
        return next;
      });

      // Sell: pick the best city (max avg across selected sell cities)
      const avgSell: Record<string, number> = {};
      const sellDate: Record<string, number> = {};
      const sellCfg: Record<string, string> = {};
      const sellOrigin: Record<string, "adp" | "ziggs"> = {};
      for (const id of varIds) {
        const gameId = toGameName(id);
        let bestAvg = 0;
        let bestCity = "";
        let bestDate = 0;
        let bestOrigin: "adp" | "ziggs" = "adp";
        for (const city of sellCities) {
          const useBuyOrder = sellOrderMode === "buy" || city === "Black Market";
          const vals: number[] = [];
          let latest = 0;
          let cityOrigin: "adp" | "ziggs" = "adp";
          for (const ql of SELL_QUALITIES) {
            const quote = q.get(`${gameId}|${city}|${ql}`);
            if (!quote || !isFresh(quote.updatedAt)) continue;
            const v = useBuyOrder ? quote.buyMax : quote.sellMin;
            if (v) {
              vals.push(v);
              if (quote.updatedAt > latest) {
                latest = quote.updatedAt;
                cityOrigin = quote.source === "local" ? "ziggs" : "adp";
              }
            }
          }
          if (vals.length) {
            const avg = Math.round(vals.reduce((s, n) => s + n, 0) / vals.length);
            if (avg > bestAvg) {
              bestAvg = avg;
              bestCity = city;
              bestDate = latest;
              bestOrigin = cityOrigin;
            }
          }
        }
        if (bestAvg) {
          avgSell[id] = bestAvg;
          sellDate[id] = bestDate;
          sellCfg[id] = bestCity;
          sellOrigin[id] = bestOrigin;
        }
      }
      setSellPrices((prev) => {
        const next = { ...prev };
        for (const id of varIds) {
          if (curSellMeta[id]?.source === "manual") continue;
          if (avgSell[id]) next[id] = avgSell[id];
        }
        return next;
      });
      setSellMeta((prev) => {
        const next = { ...prev };
        for (const id of varIds) {
          if (prev[id]?.source === "manual") continue;
          if (avgSell[id]) next[id] = { source: "api", date: sellDate[id], city: sellCfg[id], apiValue: avgSell[id], apiDate: sellDate[id], apiCity: sellCfg[id], origin: sellOrigin[id] };
        }
        return next;
      });

      // Jornais: preço de compra (vazio) e venda (cheio) pela MÉDIA das 5
      // cidades reais — igual ao refino. Não usa sellCities (que pode ser BM).
      const JOURNAL_5CITY = ["Lymhurst", "Fort Sterling", "Bridgewatch", "Martlock", "Thetford"];
      setMatPrices((prev) => {
        const next = { ...prev };
        for (const id of journalIds) {
          if (curMatMeta[id]?.source === "manual") continue;
          const gameId = toGameName(id);
          const vals: number[] = [];
          for (const city of JOURNAL_5CITY) {
            const quote = q.get(`${gameId}|${city}|1`);
            if (!quote || !isFresh(quote.updatedAt)) continue;
            const v = quote.sellMin;
            if (v) vals.push(v);
          }
          if (vals.length) next[id] = Math.round(vals.reduce((s, n) => s + n, 0) / vals.length);
        }
        return next;
      });
      setMatMeta((prev) => {
        const next = { ...prev };
        for (const id of journalIds) {
          if (prev[id]?.source === "manual") continue;
          next[id] = { source: "api", date: Date.now(), city: "5-city avg", apiValue: next[id]?.apiValue, apiDate: Date.now(), apiCity: "5-city avg", origin: "adp" };
        }
        return next;
      });
      setFullJournalPrices((prev) => {
        const next = { ...prev };
        for (const id of journalIds) {
          const fullId = `${toGameName(id)}_FULL`;
          let best = 0;
        for (const city of JOURNAL_5CITY) {
          for (const ql of SELL_QUALITIES) {
            const quote = q.get(`${fullId}|${city}|${ql}`);
              if (!quote || !isFresh(quote.updatedAt)) continue;
              const v = quote.sellMin;
              if (v && v > best) best = v;
            }
          }
          if (best) next[id] = best;
        }
        return next;
      });

      const demandData = await fetchAdpDemand(server, varIds, DEMAND_CITIES, ALL_QUALITIES);
      setDemand((prev) => ({ ...prev, ...demandData }));

      fetchAdpGold(server).then(setGoldPrice).catch(() => {});
    } catch (e) {
      setFetchError(String(e instanceof Error ? e.message : e));
    } finally {
      setLoadingPrices(false);
    }
  }

  useEffect(() => {
    if (family) fetchMarket();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [family, server, sellCities, groupBuyCities, groupOrder]);

  const rows: React.ReactNode[] = [];
  let lastTier = -1;
  const isRef = !!refiningFamKey;
  // ponytail: pré-calcula lucro de todas as variações pra normalizar opacidade
  // (mais lucro = mais opaco, menos lucro = mais translúcido) e achar o max.
  const profitsByVar = new Map<string, number>();
  for (const v of variations) {
    const r = computeWithPrices(v, matPrices);
    profitsByVar.set(v.uniqueName, useFocus ? r.profitFocus : r.profitNoFocus);
  }
  const maxProfit = Math.max(1, ...[...profitsByVar.values()].filter((p) => p > 0));
  const profitOpacity = (p: number) => {
    if (p <= 0) return 1;
    // Escala log-like: opacidade cai suave, não linear (senão só o top fica visível).
    return 0.35 + 0.65 * Math.min(1, Math.sqrt(p / maxProfit));
  };
  const profitColor = (p: number, belowMinSpf: boolean): string => {
    if (p <= 0) return "text-red-400";
    if (useFocus && belowMinSpf) return "text-emerald-400/40";
    return "text-emerald-400";
  };
  const profitStyle = (p: number, belowMinSpf: boolean): React.CSSProperties => {
    if (p <= 0) return {};
    if (useFocus && belowMinSpf) return { opacity: 0.4 };
    return { opacity: profitOpacity(p) };
  };
  for (const v of variations) {
    if (v.tier !== lastTier) {
      lastTier = v.tier;
      const arts = artifactByTier.get(v.tier);
      if (arts && arts.length) {
        rows.push(
          <tr key={`art-${v.tier}`} className="border-t border-zinc-700 bg-purple-900/10">
            <td colSpan={9} className="px-2 py-1.5">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                {arts.map((art) => (
                  <div key={art} className="flex flex-nowrap items-center gap-2">
                    <ItemIcon id={art} size={22} city={matMeta[art]?.city ?? citiesForMat(art)[0]} server={server} name={nameOf(art)} />
                    <span className="font-semibold text-purple-300">{nameOf(art)}</span>
                    <PriceField value={matPrices[art]} meta={matMeta[art]} onChange={(val) => setMat(art, val)} w="w-24" />
                  </div>
                ))}
              </div>
            </td>
          </tr>,
        );
      }
    }

    // ponytail: refino — transmutação do material bruto primário.
    // Agora é uma SUGESTÃO: o badge pulsa até o usuário clicar e escolher
    // uma das rotas. Enquanto não ativado, mantém o preço direto no cálculo.
    const rowKey = v.uniqueName;
    let transmuteInfo: { sourceId: string; targetId: string; cost: number; direct: number; route: TransmuteRoute | null; options: TransmuteOption[]; hasProfit: boolean } | null = null;
    let calcPrices = matPrices;
    let primaryMatPrice = 0;
    if (isRef) {
      const normalV = refiningCatalog?.recipes.find((rr) => rr.outputId === v.uniqueName)?.variants.find((vv) => vv.kind === "normal");
      const primary = normalV?.inputs.filter((i) => !i.isHeart).sort((a, b) => b.count - a.count)[0];
      if (primary) {
        const routes = transmuteRoutesByTarget.get(primary.itemId);
        primaryMatPrice = matPrices[primary.itemId] ?? 0;
        if (routes && routes.length > 0) {
          const direct = matPrices[primary.itemId];
          const options = transmuteOptions(routes, direct, matPrices, 3);
          const hasProfit = options.some((o) => direct != null && direct > 0 && o.totalCost < direct);
          const chosen = activeTransmute[rowKey];
          if (chosen) {
            // Usuário já escolheu uma rota: sempre mostra o caminho dela no popover.
            // Só aplica no cálculo se o source tiver preço.
            const src = matPrices[chosen.sourceId] ?? 0;
            const total = src > 0 ? src + chosen.silverCost + chosen.stationFeeCost : 0;
            transmuteInfo = { sourceId: chosen.sourceId, targetId: primary.itemId, cost: total, direct: direct ?? 0, route: chosen, options, hasProfit };
            if (src > 0) {
              calcPrices = { ...matPrices, [primary.itemId]: total };
            }
          } else if (options.length > 0) {
            // Sem rota ativa: mostra as 3 opções no popover.
            transmuteInfo = { sourceId: options[0].route.sourceId, targetId: primary.itemId, cost: options[0].totalCost, direct: direct ?? 0, route: null, options, hasProfit };
          }
        }
      }
    }

    // ponytail: refino — variante heart vs normal. Só aplica quando o usuário
    // clicou no ícone de coração. Economia aparece no tooltip de hover.
    let heartActive = !!activeHeart[rowKey];
    let heartWins = false;
    let heartSavings = 0;
    let computeResult = computeWithPrices(v, calcPrices);
    let effectiveV = v;
    const baseResult = computeResult;
    if (isRef && heartActive && effectiveHeartPrice > 0) {
      const hv = heartVariantByKey.get(`${v.uniqueName.split("@")[0]}@${v.enchant}`);
      if (hv) {
        const heartMatPrices = { ...calcPrices };
        const heartInput = hv.inputs.find((i) => i.isHeart);
        if (heartInput) heartMatPrices[heartInput.itemId] = effectiveHeartPrice;
        const heartV: CatalogVariation = {
          ...v,
          focus: hv.focus,
          outputPerCraft: hv.outputCount,
          resources: hv.inputs.map((inp) => ({ uniqueName: inp.itemId, count: inp.count, noReturn: !inp.returnable })),
        };
        const heartResult = computeWithPrices(heartV, heartMatPrices);
        heartSavings = heartResult.profitFocus - baseResult.profitFocus;
        heartWins = true;
        computeResult = heartResult;
        effectiveV = heartV;
      }
    }

    const r = computeResult;
    const ok = priced(v);
    const journals = isRef ? 0 : Math.ceil(journalsFilled(effectiveV.resources, effectiveV.tier, effectiveV.enchant, batchQty));
    const visibleMats = effectiveV.resources.filter((res) => !res.noReturn);
    const chosen = activeTransmute[rowKey];

    rows.push(
      <tr key={v.uniqueName} onDoubleClick={() => addOrder(effectiveV, chosen ?? null, chosen ? transmuteInfo?.targetId ?? null : null)} className="border-b border-zinc-900 hover:bg-zinc-800/40">
        <td className="whitespace-nowrap px-2 py-1.5">
          <div className="flex items-center gap-2">
            <span className="font-medium text-zinc-200">{tierLabel(v)}</span>
            {isRef && transmuteInfo && transmuteInfo.options.length > 0 && (
              <TransmuteBadge
                active={!!activeTransmute[rowKey]}
                options={transmuteInfo.options}
                hasProfit={transmuteInfo.hasProfit}
                activeRoute={activeTransmute[rowKey] ?? null}
                onSelect={(opt) => setActiveTransmute((p) => ({ ...p, [rowKey]: opt.route }))}
                onClear={() => setActiveTransmute((p) => { const n = { ...p }; delete n[rowKey]; return n; })}
                tierDot={tierDot}
                direct={transmuteInfo.direct}
                nameOf={nameOf}
                matPrices={matPrices}
              />
            )}
            {isRef && heartId && v.enchant < 4 && (
              <HeartBadge
                active={heartActive}
                onClick={() => setActiveHeart((p) => ({ ...p, [rowKey]: !p[rowKey] }))}
                price={effectiveHeartPrice}
                savings={heartSavings}
                matPrice={primaryMatPrice}
              />
            )}
            {v.outputPerCraft && v.outputPerCraft > 1 && <span className="rounded bg-zinc-800 px-1 text-[10px] text-zinc-400" title={t("itemsPerCraftTitle")}>×{v.outputPerCraft}</span>}
          </div>
        </td>
        <td className="px-2 py-1.5">
          <div className="flex flex-nowrap items-center gap-1.5">
            {visibleMats.map((res) => {
              const isHeart = heartWins && res.uniqueName === heartTokenForFamily(refiningCatalog, refiningFamKey ?? "");
              const isTransmuted = !!activeTransmute[rowKey] && transmuteInfo && transmuteInfo.targetId === res.uniqueName;
              const displayValue = isTransmuted && transmuteInfo ? transmuteInfo.cost : matPrices[res.uniqueName];
              return (
                <div key={res.uniqueName} className="flex items-center gap-1">
                  <span className="relative shrink-0">
                    <ItemIcon id={res.uniqueName} size={22} city={isHeart ? "Heart" : (matMeta[res.uniqueName]?.city ?? citiesForMat(res.uniqueName)[0])} server={server} name={nameOf(res.uniqueName)} />
                    <span className="absolute bottom-0.5 right-0 rounded bg-zinc-900/85 px-0.5 text-[9px] leading-tight tabular-nums text-zinc-300" title={t("qtyPerCraftTitle")}>{res.count}</span>
                  </span>
                  <PriceField value={isHeart ? (heartPriceByFamily[refiningFamKey!] ?? matPrices[res.uniqueName] ?? 0) : displayValue} meta={isHeart ? (heartPriceManual[refiningFamKey!] ? { source: "manual" as const } : matMeta[res.uniqueName]) : matMeta[res.uniqueName]} onChange={(val) => { if (isHeart && refiningFamKey) { setHeartPriceByFamily((p) => ({ ...p, [refiningFamKey]: val ?? 0 })); setHeartPriceManual((p) => ({ ...p, [refiningFamKey]: true })); } else { if (isTransmuted) setActiveTransmute((p) => { const n = { ...p }; delete n[rowKey]; return n; }); const apiVal = matMeta[res.uniqueName]?.apiValue; setMat(res.uniqueName, val != null && apiVal != null && val === apiVal ? undefined : val); } }} w="w-28" className={isHeart ? "text-red-300" : isTransmuted ? "text-purple-300" : undefined} />
                </div>
              );
            })}
          </div>
        </td>
        <td className="px-2 py-1.5">
          <div className="flex justify-end items-center gap-1">
            <ItemIcon id={v.uniqueName} size={22} city={sellMeta[sellPriceId(v.uniqueName)]?.city ?? sellCities[0] ?? "Black Market"} server={server} name={nameOf(v.uniqueName)} />
            <PriceField value={sellPrices[sellPriceId(v.uniqueName)]} meta={sellMeta[sellPriceId(v.uniqueName)]} onChange={(val) => setSell(sellPriceId(v.uniqueName), val)} w="w-24" />
          </div>
        </td>
        {(() => {
          const profit = ok ? (useFocus ? r.profitFocus : r.profitNoFocus) : undefined;
          const spf = ok ? r.silverPerFocus : undefined;
          const belowMinSpf = useFocus && spf != null && spf < minSpf;
          const colorClass = profit == null ? "" : profit <= 0 ? "text-red-400" : profitColor(profit, belowMinSpf);
          return (
            <td className={`whitespace-nowrap px-2 py-1.5 text-right ${colorClass}`} style={profit != null ? profitStyle(profit, belowMinSpf) : {}}>
              {profit != null ? (
                <div className="flex flex-col leading-tight items-end">
                  <span>{silverShort(profit)}</span>
                  <span className="text-[10px] opacity-70">{percent(useFocus ? r.marginFocus : r.marginNoFocus)}</span>
                </div>
              ) : "—"}
            </td>
          );
        })()}
        <td className="whitespace-nowrap px-2 py-1.5 text-right text-zinc-500">{silver(v.focus * focusMult * batchQty)}</td>
        <td className={`whitespace-nowrap px-2 py-1.5 text-right ${ok ? (r.silverPerFocus <= 0 ? "text-red-400" : r.silverPerFocus < minSpf ? "text-amber-400" : "text-zinc-300") : "text-zinc-300"}`}>
          {ok ? (
            <div className="flex flex-col leading-tight items-end">
              <span>{decimal(r.silverPerFocus)}</span>
              <span className="text-[9px] text-zinc-600">{decimal(minSpf)}</span>
            </div>
          ) : "—"}
        </td>
        {isRef ? null : <td className="whitespace-nowrap px-2 py-1.5 text-right text-zinc-500">{isConsumable ? "—" : silver(journals)}</td>}
        <td className="px-2 py-1.5 text-right">
          {demand[sellPriceId(v.uniqueName)] != null ? <span className={demand[sellPriceId(v.uniqueName)] < 5 ? "text-red-400" : "text-zinc-300"}>{silver(demand[sellPriceId(v.uniqueName)])}</span> : <span className="text-zinc-600">—</span>}
        </td>
      </tr>,
    );
  }

  return (
    <div className="mx-auto w-full max-w-[1800px] px-4 py-5">
      {/* Main grid: configuração + anúncio | preços | carrinho */}
      <div className="grid items-start gap-4 min-[1200px]:grid-cols-[320px_minmax(0,1fr)_320px]">
        <div className="flex min-w-0 flex-col gap-4">
          <SettingsPanel
            productionLocation={productionLocation} setProductionLocation={setProductionLocation}
            hideoutEligible={hideoutEligible}
            hoQuality={hoQuality} setHoQuality={setHoQuality} hoLevel={hoLevel} setHoLevel={setHoLevel}
            eventBonus={eventBonus} setEventBonus={setEventBonus}
            bonusCity={family?.bonusCity ?? null} autoSpecialized={autoSpecialized}
            craftCity={craftCity}
            baseVar={baseVar}
            siblings={siblings}
            focusEfficiencyByFamily={focusEfficiencyByFamily} setFocusEff={setFocusEff} commitFocusEfficiency={commitFocusEfficiency}
            ignoredJournalTiers={ignoredJournalTiers} toggleJournalTier={toggleJournalTier}
            isRefining={!!refiningFamKey}
            refiningIconByTier={refiningIconByTier}
            refiningSpecs={refiningFamKey ? (refiningSpecs[refiningFamKey] ?? {}) : {}}
            setRefiningSpec={(tier, value) => {
              if (!refiningFamKey) return;
              setRefiningSpecs((prev) => ({ ...prev, [refiningFamKey]: { ...(prev[refiningFamKey] ?? {}), [tier]: value } }));
            }}
            familyDisplayName={familyDisplayName}
          />
        </div>

        <div className="flex min-w-0 flex-col gap-3">
          {/* Dropdown de receitas: a busca fica dentro do menu de itens craftáveis. */}
          <div className="relative z-30 w-full" ref={pickerRef}>
            <button onClick={() => setPickerOpen((o) => !o)} className="flex h-[44px] w-full items-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900 px-2.5 text-left hover:border-zinc-600">
              {family && (
                <img src={iconUrl(displayVariation(family).uniqueName, 96, displayQuality(family))} alt="" width={32} height={32} />
              )}
              <span className="flex-1 truncate text-sm font-semibold text-zinc-100">{family ? familyDisplayName(family) : t("loading")}</span>
              {family?.bonusCity && <span className="hidden rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] font-medium sm:inline" style={{ color: cityColor(family.bonusCity) }} title={bonusBiome ? `${t("craftBonusBiomeTitle")} · ${family.bonusCity}` : t("craftBonusCityTitle")}>{bonusBiome ?? family.bonusCity}</span>}
              <span className="text-zinc-500">▾</span>
            </button>
            {pickerOpen && (
              <div className="absolute left-0 right-0 top-full mt-1 rounded-lg border border-zinc-700 bg-zinc-900 p-2 shadow-xl">
                <div className="mb-2 flex items-center gap-2 rounded-md border border-zinc-700 bg-zinc-950 px-2.5 py-1.5">
                  <i className="ti ti-search text-zinc-600" />
                  <input
                    ref={searchRef}
                    autoFocus
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder={t("searchItemPlaceholder")}
                    className="w-full bg-transparent text-sm text-zinc-200 outline-none placeholder:text-zinc-600"
                  />
                  {search && <button onClick={() => setSearch("")} className="text-zinc-600 hover:text-zinc-300"><i className="ti ti-x" /></button>}
                </div>
                <div className="max-h-96 space-y-0.5 overflow-y-auto">
                  {filteredFamilies.map((f) => (
                    <button key={f.familyKey} onClick={() => { setFamilyKey(f.familyKey); setPickerOpen(false); setSearch(""); }} className={`flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-sm ${f.familyKey === familyKey ? "bg-amber-500/15 text-amber-300" : "text-zinc-300 hover:bg-zinc-800"}`}>
                      <img src={iconUrl(displayVariation(f).uniqueName, 64, displayQuality(f))} alt="" width={30} height={30} />
                      <span className="truncate">{familyDisplayName(f)}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
          {fetchError && <span className="text-xs text-red-400" title={fetchError}>⚠</span>}

          <div className="min-w-0 overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-900/40">
          <table className="w-full min-w-[980px] text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-left text-[11px] uppercase tracking-wide text-zinc-500">
                <Th>Item</Th>
                <Th subtitle={t("colMaterialsSub")}>{t("colMaterials")}</Th>
                <Th right subtitle={t("colSellAvgSub")}>{t("colSellAvg")}</Th>
                <Th right subtitle={percent(useFocus ? rrFocus : rrNoFocus)}>{t("colProfit")}</Th>
                <Th right>Focus cost</Th>
                <Th right subtitle={decimal(minSpf)}>SPF</Th>
                {isRef ? null : <Th right>{t("colJournals")}</Th>}
                <Th right subtitle={t("colDemandSub")}>
                  <span className="inline-flex items-center gap-1">
                    {t("colDemand")}
                    <span className="cursor-help text-zinc-600" title={t("demandTooltip")}>ⓘ</span>
                  </span>
                </Th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>

        </div>

        {/* Coluna direita: mercado (aba) + carrinho */}
        <div className="space-y-3">
          <MarketPanel
            sellCities={sellCities} setSellCities={setSellCities}
            sellOrderMode={sellOrderMode} setSellOrderMode={setSellOrderMode}
            groups={groups.filter((g) => g !== "Jornais" || !isRef)}
            citiesForGroup={citiesForGroup} setGroupBuyCities={setGroupBuyCities}
            orderForGroup={orderForGroup} setGroupOrder={setGroupOrder}
          />
          <Cart cart={cart} matPrices={matPrices} sellPrices={sellPrices} weights={weights} journalBase={journalBase} fullJournalPrices={fullJournalPrices} settings={settings} ignoredTiers={ignoredJournalTiers} nameOf={nameOf} onRemove={(id) => setCart((c) => c.filter((o) => o.id !== id))} onClear={() => setCart([])} premium={premium} useFocus={useFocus} setPremium={setPremium} setUseFocus={setUseFocus} batchQty={batchQty} setBatchQty={setBatchQty} stationFeePer100={stationFeePer100} setStationFeePer100={setStationFeePer100} onRefresh={fetchMarket} loadingPrices={loadingPrices} />
        </div>
      </div>
    </div>
  );
}

/* ---------------- SettingsPanel (coluna esquerda) ---------------- */

function SettingsPanel({
  productionLocation, setProductionLocation, hideoutEligible,
  hoQuality, setHoQuality, hoLevel, setHoLevel,
  eventBonus, setEventBonus,
  bonusCity, autoSpecialized,
  craftCity,
  baseVar,
  siblings,
  focusEfficiencyByFamily, setFocusEff, commitFocusEfficiency,
  ignoredJournalTiers, toggleJournalTier,
  isRefining,
  refiningIconByTier,
  refiningSpecs, setRefiningSpec,
  familyDisplayName,
}: {
  productionLocation: ProductionLocation; setProductionLocation: (l: ProductionLocation) => void; hideoutEligible: boolean;
  hoQuality: number; setHoQuality: (v: number) => void; hoLevel: number; setHoLevel: (v: number) => void;
  eventBonus: number; setEventBonus: (v: number) => void;
  bonusCity: string | null; autoSpecialized: boolean;
  craftCity: ProductionCity | undefined;
  baseVar: CatalogVariation | null;
  siblings: CatalogFamily[];
  focusEfficiencyByFamily: Record<string, number>; setFocusEff: (key: string, value: number) => void; commitFocusEfficiency: () => void;
  ignoredJournalTiers: Set<number>; toggleJournalTier: (t: number) => void;
  isRefining?: boolean;
  familyDisplayName: (f: CatalogFamily) => string;
  refiningIconByTier?: Record<number, string>;
  refiningSpecs?: Record<number, number>;
  setRefiningSpec?: (tier: number, value: number) => void;
}) {
  const t = useT();
  const place = productionLocation.kind;
  const setPlace = (p: "city" | "island" | "hideout") => {
    if (p === "hideout") {
      setProductionLocation({ kind: "hideout", quality: hoQuality, power: hoLevel });
    } else {
      const city = productionLocation.kind !== "hideout" ? productionLocation.city : "Caerleon";
      setProductionLocation({ kind: p, city });
    }
  };
  return (
    <>
    <aside className="flex flex-col gap-4 rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
      {/* Local & Bônus — cidade real determina o bônus */}
      <div className="space-y-1.5">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">{t("craftLocationHeader")} & {t("bonusLabel")}</h3>
        <div className="flex gap-1.5">
          <select value={place} onChange={(e) => setPlace(e.target.value as "city" | "island" | "hideout")} className={`${selectCls} h-9 min-w-0 flex-1`}>
            <option value="city">🏛️ {t("placeCity")}</option>
            <option value="island">🏝️ {t("placeIsland")}</option>
            {hideoutEligible && <option value="hideout">🏠 Hideout</option>}
          </select>
          <select value={eventBonus} onChange={(e) => setEventBonus(+e.target.value)} className={`${selectCls} h-9 w-28`} title={t("bonusEventTitle")}>
            <option value={0}>0%</option>
            <option value={0.1}>10%</option>
            <option value={0.2}>20%</option>
          </select>
        </div>
        {place !== "hideout" && (
          <select
            value={craftCity ?? "Caerleon"}
            onChange={(e) => setProductionLocation({ kind: place, city: e.target.value as ProductionCity })}
            className={`${selectCls} h-9 w-full`}
          >
            {PRODUCTION_CITIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        )}
        {place === "hideout" && hideoutEligible && (
          <div className="flex gap-1.5">
            <select value={hoQuality} onChange={(e) => setHoQuality(+e.target.value)} className={`${selectCls} h-9 min-w-0 flex-1`} title={t("hoZoneQualityTitle")}>{HIDEOUT_QUALITY.map((_, i) => <option key={i} value={i + 1}>{`Q${i + 1}`}</option>)}</select>
            <select value={hoLevel} onChange={(e) => setHoLevel(+e.target.value)} className={`${selectCls} h-9 min-w-0 flex-1`} title={t("hoPowerLevelTitle")}>{HIDEOUT_LEVEL.map((_, i) => <option key={i} value={i + 1}>{`Nv${i + 1}`}</option>)}</select>
          </div>
        )}
        {/* Bônus local: derivado da cidade + receita */}
        <div className="text-xs">
          {autoSpecialized ? (
            <span className="text-emerald-400">
              ✓ {t("craftBonusActive")}: {bonusCity} {isRefining ? "+40%" : "+15%"}
            </span>
          ) : bonusCity ? (
            <span className="text-zinc-500">
              {t("craftBonusNoBonus")} {t("craftBonusInCity")} <span style={{ color: cityColor(bonusCity) }}>{bonusCity}</span>
            </span>
          ) : (
            <span className="text-zinc-500">{t("craftBonusNoCity")}</span>
          )}
        </div>
      </div>

      {/* Focus efficiency — craft: árvore de irmãos; refino: specs T4-T8 */}
      {isRefining ? (
        <div className="space-y-2 border-t border-zinc-800 pt-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">{t("focusEfficiencyLabel")}</h3>
          <p className="text-xs text-zinc-500">{t("refiningSpecsHint")}</p>
          <div className="space-y-1.5">
            {TIERS.map((tier) => (
              <div key={tier} className="flex items-center gap-2 rounded-md border border-zinc-800 px-2 py-1">
                {(() => {
                  const id = refiningIconByTier?.[tier];
                  return id ? <img src={iconUrl(id, 32)} alt="" width={22} height={22} className="shrink-0" /> : null;
                })()}
                <span className="flex-1 text-xs text-zinc-300">T{tier}</span>
                <input
                  type="number" min={0} max={100}
                  value={refiningSpecs?.[tier] ?? ""}
                  placeholder="0"
                  onChange={(e) => setRefiningSpec?.(tier, e.target.value === "" ? 0 : Math.min(100, Math.max(0, +e.target.value)))}
                  className={`${selectCls} w-20 shrink-0`}
                />
              </div>
            ))}
          </div>
        </div>
      ) : (
        siblings.length > 0 && (
          <div className="space-y-2 border-t border-zinc-800 pt-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">{t("focusEfficiencyLabel")}</h3>
            <p className="text-xs text-zinc-500">{t("focusEffTreeHint")}</p>
            {(() => {
              const baseFam = siblings[0];
              if (!baseFam) return null;
              const baseKey = baseFam.familyKey;
              return (
                <div className="flex items-center gap-2 rounded-md border border-zinc-600 bg-zinc-800/40 px-2 py-1">
                  <img
                    src={iconUrl(baseVar?.uniqueName ?? displayVariation(baseFam).uniqueName, 128, baseVar ? EXCELLENT : displayQuality(baseFam))}
                    alt="" width={22} height={22} className="shrink-0"
                  />
                  <span className="flex-1 truncate text-xs font-semibold text-zinc-100">Base</span>
                  <input
                    type="number" min={0} max={100}
                    value={focusEfficiencyByFamily[baseKey] || ""}
                    placeholder="0"
                    onChange={(e) => setFocusEff(baseKey, e.target.value === "" ? 0 : Math.min(100, Math.max(0, +e.target.value)))}
                    onBlur={commitFocusEfficiency}
                    className={`${selectCls} w-20 shrink-0`}
                  />
                </div>
              );
            })()}
            <div className="space-y-1.5">
              {siblings.slice(1).map((f) => (
                <div key={f.familyKey} className="flex items-center gap-2 rounded-md border border-zinc-800 px-2 py-1">
                  <img src={iconUrl(displayVariation(f).uniqueName, 48, displayQuality(f))} alt="" width={22} height={22} className="shrink-0" />
                  <span className="flex-1 truncate text-xs text-zinc-300">{familyDisplayName(f)}</span>
                  <input
                    type="number" min={0} max={100}
                    value={focusEfficiencyByFamily[f.familyKey] || ""}
                    placeholder="0"
                    onChange={(e) => setFocusEff(f.familyKey, e.target.value === "" ? 0 : Math.min(100, Math.max(0, +e.target.value)))}
                    onBlur={commitFocusEfficiency}
                    className={`${selectCls} w-20 shrink-0`}
                  />
                </div>
              ))}
            </div>
          </div>
        )
      )}

      {/* Craft: Ignorar jornais */}
      {isRefining ? null : (
        <div className="space-y-2 border-t border-zinc-800 pt-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">{t("ignoreJournalsHeader")}</h3>
          <p className="text-xs text-zinc-500">{t("ignoreJournalsHint")}</p>
          <div className="flex flex-wrap gap-1.5">
            {TIERS.map((tier) => (
              <button key={tier} onClick={() => toggleJournalTier(tier)} className={`rounded-md border px-2 py-1 text-xs ${ignoredJournalTiers.has(tier) ? "border-red-500/60 bg-red-900/20 text-red-300" : "border-zinc-700 text-zinc-300"}`}>T{tier}</button>
            ))}
          </div>
        </div>
      )}

    </aside>
    {/* Anúncio 300×250 — fora de qualquer quadrante, abaixo de especializações */}
    <div className="mt-4">
      <AdBanner slot="craft" variant="mediumRectangle" />
    </div>
    </>
  );
}

/* ---------------- MarketPanel (coluna direita, acima do carrinho) --------- */

function MarketPanel({
  sellCities, setSellCities,
  sellOrderMode, setSellOrderMode,
  groups, citiesForGroup, setGroupBuyCities, orderForGroup, setGroupOrder,
}: {
  sellCities: string[]; setSellCities: (v: string[]) => void;
  sellOrderMode: OrderMode; setSellOrderMode: (v: OrderMode) => void;
  groups: string[]; citiesForGroup: (g: string) => string[];
  setGroupBuyCities: React.Dispatch<React.SetStateAction<Record<string, string[]>>>;
  orderForGroup: (g: string) => OrderMode; setGroupOrder: React.Dispatch<React.SetStateAction<Record<string, OrderMode>>>;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center justify-between px-4 py-2 text-xs font-semibold uppercase tracking-wide text-zinc-500 hover:text-zinc-300">
        <span>{t("marketHeader")}</span>
        <span className="text-zinc-400">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-zinc-800 p-3">
          {/* Vender em — render do item de venda + alavanca buy/sell + menu cidades */}
          <div className="flex items-center gap-2">
            <img src={iconUrl("T6_JOURNAL_MAGE", 48)} alt="" width={28} height={28} className="shrink-0 rounded" />
            <OrderToggle value={sellOrderMode} onChange={setSellOrderMode} />
            <MultiCitySelect cities={CITIES} selected={sellCities} onChange={setSellCities} />
          </div>
          {/* Cada categoria = uma linha: render + alavanca + menu cidades, sem texto */}
          {groups.map((g) => {
            const catIcon = groupIcon(g);
            return (
              <div key={g} className="flex items-center gap-2">
                {catIcon ? (
                  <img src={iconUrl(catIcon, 48)} alt="" width={28} height={28} className="shrink-0 rounded" />
                ) : (
                  <span className="h-7 w-7 shrink-0 rounded bg-zinc-800" />
                )}
                <OrderToggle value={orderForGroup(g)} onChange={(v) => setGroupOrder((m) => ({ ...m, [g]: v }))} />
                <MultiCitySelect cities={CITIES.filter((c) => c !== "Black Market")} selected={citiesForGroup(g)} onChange={(v) => setGroupBuyCities((m) => ({ ...m, [g]: v }))} />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ---------------- Cart ---------------- */

function Cart({
  cart, matPrices, sellPrices, weights, journalBase, fullJournalPrices, settings, ignoredTiers, nameOf, onRemove, onClear,
  premium, useFocus, setPremium, setUseFocus, batchQty, setBatchQty, stationFeePer100, setStationFeePer100, onRefresh, loadingPrices,
}: {
  cart: Order[];
  matPrices: Record<string, number | undefined>;
  sellPrices: Record<string, number | undefined>;
  weights: Record<string, number>;
  journalBase: Record<string, JournalBase>;
  fullJournalPrices: Record<string, number>;
  settings: Settings;
  ignoredTiers: Set<number>;
  nameOf: (id: string) => string;
  onRemove: (id: string) => void;
  onClear: () => void;
  premium: boolean; useFocus: boolean; setPremium: (v: boolean) => void; setUseFocus: (v: boolean) => void;
  batchQty: number; setBatchQty: (v: number) => void;
  stationFeePer100: number; setStationFeePer100: (v: number) => void;
  onRefresh: () => void; loadingPrices: boolean;
}) {
  const t = useT();
  const journalUnit = (id: string) => matPrices[id] ?? journalBase[id]?.base ?? 0;
  const orderProfit = (o: Order) =>
    computeCraft({
      quantity: o.qty,
      materials: o.variation.resources.map((r) => ({ uniqueName: r.uniqueName, unitPrice: matPrices[r.uniqueName] ?? 0, countPerCraft: r.count, noReturn: r.noReturn })),
      sellPrice: sellPrices[sellPriceId(o.variation.uniqueName)] ?? 0,
      outputPerCraft: o.variation.outputPerCraft ?? 1,
      returnRateNoFocus: o.rr,
      returnRateFocus: o.rr,
      focusCostBase: o.variation.focus,
      focusEfficiency: o.focusEfficiency,
      itemValue: o.variation.itemValue,
      stationFeePer100: settings.stationFeePer100,
      salesTaxRate: settings.premium ? 0.04 : 0.08,
      setupFeeRate: SETUP_FEE,
    }).profitNoFocus;

  const combined = new Map<string, { buyCount: number; subtotal: number }>();
  const journalCount = new Map<string, number>();
  let grandTotal = 0;
  let plannedProfit = 0;
  let totalLoad = 0;
  let totalFocus = 0;

  for (const o of cart) {
    plannedProfit += orderProfit(o);
    for (const n of materialNeeds(o.variation.resources, o.qty, o.rr)) {
      const unit = matPrices[n.uniqueName] ?? 0;
      const sub = n.buyCount * unit;
      grandTotal += sub;
      totalLoad += n.buyCount * (weights[n.uniqueName] ?? 0);
      const cur = combined.get(n.uniqueName) ?? { buyCount: 0, subtotal: 0 };
      combined.set(n.uniqueName, { buyCount: cur.buyCount + n.buyCount, subtotal: cur.subtotal + sub });
    }
    if (o.journalId && !ignoredTiers.has(o.variation.tier)) {
      const j = journalsFilled(o.variation.resources, o.variation.tier, o.variation.enchant, o.qty);
      journalCount.set(o.journalId, (journalCount.get(o.journalId) ?? 0) + j);
    }
    if (o.useFocus) totalFocus += o.focusCostPerItem * o.qty;
  }
  let journalProfit = 0;
  let journalTotal = 0;
  let journalPriced = true;
  for (const [id, raw] of journalCount) {
    const count = Math.ceil(raw);
    grandTotal += count * journalUnit(id);
    totalLoad += count * (weights[id] ?? 0);
    journalTotal += count;
    const full = fullJournalPrices[id];
    if (full == null) journalPriced = false;
    else journalProfit += count * (full - journalUnit(id));
  }

  return (
    <aside className="space-y-3 xl:sticky xl:top-4 xl:self-start">
      {/* Canto título: 2 linhas — linha 1: Premium | Focus | Qtd | Fee
          linha 2: lixeira + refresh. Inputs esticam pra não deixar gap. */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-3">
        <div className="grid grid-cols-2 gap-2">
          <div className="flex flex-col gap-2">
            <VToggle active={premium} on="Premium" onClick={() => setPremium(!premium)} />
            <VToggle active={useFocus} on="Focus" onClick={() => setUseFocus(!useFocus)} />
          </div>
          <div className="flex flex-col gap-2">
            <InlineNum label={t("qtyLabel")} value={batchQty} onChange={setBatchQty} w="w-full" />
            <InlineNum label={t("feePerHundredLabel")} value={stationFeePer100} onChange={setStationFeePer100} w="w-full" />
          </div>
        </div>
        <div className="mt-2 flex items-center gap-2 border-t border-zinc-800 pt-2">
          <IconButton onClick={onRefresh} disabled={loadingPrices} title={t("updatePricesTitle")} spinning={loadingPrices}>⟳</IconButton>
          <button onClick={onClear} className="ml-auto text-sm text-zinc-500 hover:text-red-400" title={t("clearAllBtn")}><i className="ti ti-trash" /></button>
        </div>
      </div>

      {cart.length === 0 ? (
        <div className="rounded-xl border border-dashed border-zinc-700 bg-zinc-900/30 p-6 text-center text-sm text-zinc-500">
          {t("cartEmptyHint")} <b className="text-zinc-300">{t("cartEmptyHintBold")}</b> {t("cartEmptyHintSuffix")}
        </div>
      ) : (
        <>
          <div className="rounded-xl border border-zinc-700 bg-zinc-900 p-3">
            <div className="mb-2 grid grid-cols-2 gap-2">
              <div>
                <div className="text-[11px] uppercase tracking-wide text-zinc-500">{t("totalToBuyLabel")}</div>
                <div className="text-base font-bold text-amber-300">{silver(grandTotal)}</div>
              </div>
              <div className="text-right">
                <div className="text-[11px] uppercase tracking-wide text-zinc-500">{t("plannedProfitLabel")}</div>
                <div className={`text-base font-bold ${plannedProfit >= 0 ? "text-emerald-400" : "text-red-400"}`}>{silver(plannedProfit)}</div>
              </div>
            </div>
            <div className="mb-2 flex items-center justify-between border-t border-zinc-800 pt-2 text-xs">
              <span className="text-zinc-500">{t("weightLabel")}</span>
              <span onDoubleClick={selectAllText} className="font-semibold text-zinc-300">{decimal(totalLoad)} kg</span>
            </div>
            {totalFocus > 0 && (
              <div className="mb-2 flex items-center justify-between text-xs">
                <span className="text-zinc-500">{t("totalFocusLabel")}</span>
                <span onDoubleClick={selectAllText} className="font-semibold text-zinc-300">{silver(totalFocus)}</span>
              </div>
            )}
            {journalTotal > 0 && (
              <div className="mb-2 flex items-center justify-between text-xs">
                <span className="inline-flex items-center gap-1 text-zinc-500">
                  {t("journalProfitLabel")}
                  <span className="cursor-help text-zinc-600" title={t("journalProfitTooltip")}>ⓘ</span>
                </span>
                {journalPriced ? (
                  <span onDoubleClick={selectAllText} className={`font-semibold ${journalProfit >= 0 ? "text-emerald-400" : "text-red-400"}`}>{silver(journalProfit)} ({journalTotal})</span>
                ) : (
                  <span className="text-zinc-600">— ({journalTotal})</span>
                )}
              </div>
            )}
            <table className="w-full table-fixed text-xs">
              <tbody>
                {[
                  ...[...combined.entries()].map(([id, v]) => ({ id, count: v.buyCount, subtotal: v.subtotal })),
                  ...[...journalCount.entries()].map(([id, raw]) => {
                    const count = Math.ceil(raw);
                    return { id, count, subtotal: count * journalUnit(id) };
                  }),
                ].map((it) => <CartItemRow key={it.id} id={it.id} count={it.count} subtotal={it.subtotal} name={nameOf(it.id)} />)}
              </tbody>
            </table>
          </div>

          {[...cart].reverse().map((o) => {
            const journals = o.journalId !== null ? Math.ceil(journalsFilled(o.variation.resources, o.variation.tier, o.variation.enchant, o.qty)) : 0;
            const needs = materialNeeds(o.variation.resources, o.qty, o.rr);
            const total = needs.reduce((s, n) => s + n.buyCount * (matPrices[n.uniqueName] ?? 0), 0);
            return (
              <div key={o.id} className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-3">
                <div className="flex items-start gap-2">
                  <img src={iconUrl(o.variation.uniqueName)} alt="" width={38} height={38} />
                  <div className="flex-1">
                    <div onDoubleClick={selectAllText} className="text-sm font-medium text-zinc-100">{o.qty}× {o.name} {tierLabel(o.variation)}</div>
                    <div className="flex flex-wrap gap-1.5 text-[11px] text-zinc-400">
                      <span className="rounded bg-zinc-800 px-1.5 py-0.5">{o.placeLabel}</span>
                      <span className="rounded bg-zinc-800 px-1.5 py-0.5">{o.useFocus ? t("withFocusBadge") : t("withoutFocusBadge")}</span>
                      {o.journalId !== null && <span className="rounded bg-zinc-800 px-1.5 py-0.5">{silver(journals)} {t("journalsBadgeSuffix")}</span>}
                      {o.useFocus && <span className="rounded bg-zinc-800 px-1.5 py-0.5">Focus: {silver(o.focusCostPerItem * o.qty)}</span>}
                      <span className="rounded bg-zinc-800 px-1.5 py-0.5">Custo: {silver(total)}</span>
                    </div>
                    {/* Caminho de transmutação — render base + seta 90° + sequência → render final destacado */}
                    {o.transmuteRoute && o.transmuteRoute.edges.length > 0 && (
                      <div className="mt-2 flex items-center gap-2">
                        <img src={iconUrl(o.transmuteRoute.sourceId, 48)} alt="" width={28} height={28} />
                        <span className="text-lg text-zinc-500 leading-none">⤷</span>
                        <div className="flex items-center gap-1.5">
                          {o.transmuteRoute.edges.map((edge, i) => {
                            const isLast = i === o.transmuteRoute!.edges.length - 1;
                            return (
                              <div key={i} className="flex items-center gap-1.5">
                                <img
                                  src={iconUrl(edge.targetId, 48)}
                                  alt=""
                                  width={isLast ? 36 : 24}
                                  height={isLast ? 36 : 24}
                                  className={isLast ? "rounded border-2 border-purple-500" : "rounded"}
                                />
                                {i < o.transmuteRoute!.edges.length - 1 && <span className="text-sm text-zinc-600">→</span>}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                  <button onClick={() => onRemove(o.id)} className="text-zinc-500 hover:text-red-400" title={t("deleteOrderTitle")}>✕</button>
                </div>
              </div>
            );
          })}
        </>
      )}
    </aside>
  );
}

function CartItemRow({ id, count, subtotal, name }: { id: string; count: number; subtotal: number; name: string }) {
  return (
    <tr>
      <td className="w-full max-w-0 py-1">
        <div className="flex min-w-0 items-center gap-1.5">
          <img src={iconUrl(id)} alt="" width={24} height={24} className="shrink-0" />
          <span onDoubleClick={selectAllText} className="truncate text-zinc-300">{name}</span>
        </div>
      </td>
      <td onDoubleClick={selectAllText} className="w-16 whitespace-nowrap py-1 pl-2 text-right text-zinc-300">{silver(count)}</td>
      <td onDoubleClick={selectAllText} className="w-16 whitespace-nowrap py-1 pl-2 text-right text-zinc-500">{subtotal ? silverShort(subtotal) : "—"}</td>
    </tr>
  );
}

/* ---------------- Item icon with hover price history ---------------- */

function ItemIcon({ id, size, city, server, name }: { id: string; size: number; city: string; server: PriceServer; name: string }) {
  const t = useT();
  const [show, setShow] = useState(false);
  const [data, setData] = useState<{ series: { t: string; price: number }[]; avg: number } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const enter = () => {
    timer.current = setTimeout(async () => {
      setShow(true);
      if (!data) {
        try {
          const result = await fetchAdpPriceSeries(server, id, city, [1, 2, 3, 4]);
          setData(result);
        } catch {
          setData({ series: [], avg: 0 });
        }
      }
    }, 2000);
  };
  const leave = () => {
    if (timer.current) clearTimeout(timer.current);
    setShow(false);
  };

  return (
    <span className="relative inline-flex" onMouseEnter={enter} onMouseLeave={leave}>
      <img src={iconUrl(id, size * 2)} alt="" width={size} height={size} />
      {show && (
        <div className="absolute left-0 top-full z-30 mt-1 w-96 rounded-lg border border-zinc-700 bg-zinc-900 p-3 shadow-xl">
          <div className="mb-1 flex items-center justify-between gap-2 text-sm">
            <span className="truncate font-semibold text-zinc-200">{name}</span>
            <span className="shrink-0 text-zinc-500">{t("weeksAgoSuffix")} {cityAbbr(city)}</span>
          </div>
          {!data ? (
            <div className="py-6 text-center text-xs text-zinc-500">{t("loadingHistory")}</div>
          ) : data.series.length === 0 ? (
            <div className="py-6 text-center text-xs text-zinc-500">{t("noHistoryInCity")} {city}</div>
          ) : (
            <PriceHistory series={data.series} avg={data.avg} />
          )}
        </div>
      )}
    </span>
  );
}

function PriceHistory({ series, avg }: { series: { t: string; price: number }[]; avg: number }) {
  const t = useT();
  const W = 320, H = 132;
  const padL = 6, padR = 70, padTop = 8, padBottom = 24;
  const plotW = W - padL - padR;
  const plotH = H - padTop - padBottom;
  const prices = series.map((p) => p.price);
  const max = Math.max(...prices);
  const min = Math.min(...prices);
  const range = Math.max(1, max - min);
  const n = series.length;
  const x = (i: number) => padL + (n > 1 ? (i / (n - 1)) * plotW : plotW / 2);
  const y = (v: number) => padTop + (1 - (v - min) / range) * plotH;
  const baseY = padTop + plotH;
  const line = series.map((p, i) => `${x(i).toFixed(1)},${y(p.price).toFixed(1)}`).join(" ");
  const refs = [
    { v: max, c: "#34d399", label: t("chartMax"), strong: false },
    { v: avg, c: "#fbbf24", label: t("chartAvg"), strong: true },
    { v: min, c: "#f87171", label: t("chartMin"), strong: false },
  ];
  const weeks = [1, 2, 3, 4];
  const weekX = (w: number) => padL + (1 - w / 4) * plotW;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" preserveAspectRatio="xMidYMid meet">
      {series.map((_, i) => (
        <line key={`d${i}`} x1={x(i)} x2={x(i)} y1={padTop} y2={baseY} stroke="#3f3f46" strokeWidth="0.3" opacity="0.4" />
      ))}
      {weeks.map((w) => (
        <g key={`w${w}`}>
          <line x1={weekX(w)} x2={weekX(w)} y1={padTop} y2={baseY} stroke="#52525b" strokeWidth="0.6" strokeDasharray="2 2" opacity="0.7" />
          <text x={weekX(w)} y={baseY + 16} textAnchor="middle" fontSize="11" fill="#71717a">{`${w}${t("weekAbbr")}`}</text>
        </g>
      ))}
      {refs.map((r, i) => (
        <g key={i}>
          <line x1={padL} x2={padL + plotW} y1={y(r.v)} y2={y(r.v)} stroke={r.c} strokeWidth={r.strong ? 0.9 : 0.6} strokeDasharray="3 3" opacity="0.6" />
          <text x={padL + plotW + 4} y={y(r.v) + 3} fontSize={r.strong ? 13 : 11} fontWeight={r.strong ? 700 : 400} fill={r.c}>{silverShort(r.v)}</text>
        </g>
      ))}
      <polyline points={line} fill="none" stroke="#38bdf8" strokeWidth="1.4" />
      {series.map((p, i) => (
        <circle key={i} cx={x(i)} cy={y(p.price)} r="1.6" fill="#7dd3fc">
          <title>{`${p.t.slice(5, 10)}: ${silver(p.price)}`}</title>
        </circle>
      ))}
    </svg>
  );
}

/* ---------------- UI primitives ---------------- */

function IconButton({ children, onClick, title, disabled, spinning }: { children: React.ReactNode; onClick: () => void; title: string; active?: boolean; disabled?: boolean; spinning?: boolean }) {
  return (
    <button onClick={onClick} disabled={disabled} title={title} className={`relative flex h-9 items-center rounded-lg border px-3 text-base disabled:opacity-50 border-zinc-700 text-zinc-300 hover:border-zinc-600`}>
      <span className={spinning ? "inline-block animate-spin" : ""}>{children}</span>
    </button>
  );
}

const selectCls = "rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm text-zinc-100 outline-none focus:border-amber-500";

function InlineNum({ label, value, onChange, w = "w-24" }: { label: string; value: number; onChange: (v: number) => void; w?: string }) {
  return (
    <div className={`flex h-[36px] items-center rounded-md border border-zinc-700 bg-zinc-900 ${w}`}>
      <span className="pl-2 text-[11px] text-zinc-500">{label}</span>
      <input type="number" value={value} onChange={(e) => onChange(+e.target.value)} className="min-w-0 flex-1 bg-transparent px-2 text-right text-sm text-zinc-100 outline-none" />
    </div>
  );
}

function OrderToggle({ value, onChange, compact }: { value: OrderMode; onChange: (v: OrderMode) => void; compact?: boolean }) {
  const t = useT();
  return (
    <button
      onClick={() => onChange(value === "buy" ? "sell" : "buy")}
      className={`shrink-0 rounded-md border text-xs font-semibold transition-colors ${compact ? "px-1.5 py-0.5" : "px-2 py-1"} ${value === "buy" ? "border-sky-500/60 bg-sky-900/30 text-sky-300" : "border-emerald-500/60 bg-emerald-900/30 text-emerald-300"}`}
      title={value === "buy" ? t("buyOrderOption") : t("sellOrderOption")}
    >
      {value === "buy" ? "BUY" : "SELL"}
    </button>
  );
}

function VToggle({ active, on, onClick }: { active: boolean; on: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-semibold transition-colors ${active ? "border-amber-500 bg-amber-500 text-zinc-950" : "border-zinc-700 bg-zinc-900 text-zinc-500"}`}
    >
      <span className={`inline-block h-3 w-3 rounded-full transition-colors ${active ? "bg-zinc-900" : "bg-zinc-700"}`} />
      {on}
    </button>
  );
}

function Th({ children, right, subtitle }: { children?: React.ReactNode; right?: boolean; subtitle?: string }) {
  return (
    <th className={`sticky top-0 z-20 whitespace-nowrap bg-zinc-900 px-2 py-1.5 font-medium ${right ? "text-right" : ""}`}>
      <div className={`flex flex-col leading-tight ${right ? "items-end" : ""}`}>
        <span>{children}</span>
        {subtitle && <span className="text-[9px] font-normal normal-case text-zinc-600">{subtitle}</span>}
      </div>
    </th>
  );
}

function MetaInline({ meta }: { meta?: PriceInfo }) {
  const t = useT();
  if (!meta) return null;
  if (meta.source === "manual")
    return <span className="flex w-7 shrink-0 items-center justify-center text-[10px] text-amber-400" title={t("manuallyEditedTitle")}>✎</span>;
  const stale = isStale(meta.date);
  return (
    <span
      className={`flex w-7 shrink-0 flex-col items-center justify-center leading-none ${stale ? "text-orange-400" : "text-sky-400"}`}
      title={`${t("apiPriceTitle")}${meta.city ? ` ${t("inCityWord")} ${meta.city}` : ""}`}
    >
      <span className="text-[9px]" style={{ color: cityColor(meta.city) }}>{meta.date ? timeAgo(meta.date) : "—"}</span>
      <span className="text-[9px] font-semibold" style={{ color: cityColor(meta.city) }}>{cityAbbr(meta.city)}</span>
    </span>
  );
}

function TransmuteBadge({
  active,
  options,
  hasProfit,
  activeRoute,
  onSelect,
  onClear,
  tierDot,
  direct,
  nameOf,
  matPrices,
}: {
  active: boolean;
  options: TransmuteOption[];
  hasProfit: boolean;
  activeRoute: TransmuteRoute | null;
  onSelect: (opt: TransmuteOption) => void;
  onClear: () => void;
  tierDot: (id: string) => string;
  direct: number;
  nameOf: (id: string) => string;
  matPrices: Record<string, number | undefined>;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);
  const label = "T";
  const pulse = !active && hasProfit;
  return (
    <span ref={ref} className="relative">
      <button
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
        className={`rounded px-1 text-[10px] font-bold ${active ? "bg-purple-900/30 text-purple-300" : pulse ? "bg-purple-900/15 text-purple-300/50 craft-suggest-pulse" : "bg-zinc-800/50 text-zinc-600"}`}
      >
        {label}
      </button>
      {open && (
        <span className="absolute left-0 bottom-full z-50 mb-1 block w-[22rem] rounded-lg border border-zinc-700 bg-zinc-900 p-2.5 shadow-xl">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-[11px] font-semibold text-purple-300">
              {active && activeRoute ? "Caminho de transmutação" : "Escolha a transmutação"}
            </span>
            <div className="flex items-center gap-2">
              {active && (
                <button onClick={() => onClear()} className="text-[11px] text-zinc-500 hover:text-purple-300" title="Trocar rota">⇄</button>
              )}
              <button onClick={() => setOpen(false)} className="text-[11px] text-zinc-500 hover:text-zinc-300">✕</button>
            </div>
          </div>
          {active && activeRoute ? (
            <div className="space-y-2">
              {/* Source inicial */}
              <div className="flex items-center justify-between gap-3 text-[11px]">
                <div className="flex items-center gap-1.5">
                  <img src={iconUrl(activeRoute.sourceId, 32)} alt="" width={18} height={18} />
                  <span className="text-zinc-300">{tierDot(activeRoute.sourceId)} {nameOf(activeRoute.sourceId)}</span>
                </div>
                <span className="font-semibold text-zinc-200">{silver(matPrices[activeRoute.sourceId] ?? 0)}</span>
              </div>
              {/* Cada edge da rota */}
              {activeRoute.edges.map((edge, i) => (
                <div key={i}>
                  <div className="flex items-center justify-center text-sm leading-none text-zinc-600">↓</div>
                  <div className="flex items-center justify-between gap-3 text-[11px]">
                    <div className="flex items-center gap-1.5">
                      <img src={iconUrl(edge.targetId, 32)} alt="" width={18} height={18} />
                      <span className="text-zinc-300">{tierDot(edge.targetId)} {nameOf(edge.targetId)}</span>
                    </div>
                    <span className="font-semibold text-zinc-500">+{silver(baseCostFor(edge))}</span>
                  </div>
                </div>
              ))}
              {/* Totais */}
              <div className="border-t border-zinc-700 pt-1.5 text-[11px]">
                <div className="flex justify-between gap-4"><span className="text-zinc-500">Custo total:</span><span className="font-semibold text-purple-300">{silver(activeRoute.silverCost + activeRoute.stationFeeCost + (matPrices[activeRoute.sourceId] ?? 0))}</span></div>
                <div className="flex justify-between gap-4"><span className="text-zinc-500">Direto:</span><span className="font-semibold text-zinc-200">{direct > 0 ? silver(direct) : "—"}</span></div>
                {direct > 0 && (matPrices[activeRoute.sourceId] ?? 0) + activeRoute.silverCost + activeRoute.stationFeeCost < direct && <div className="flex justify-between gap-4"><span className="text-emerald-400">Economia:</span><span className="font-semibold text-emerald-400">{silver(direct - ((matPrices[activeRoute.sourceId] ?? 0) + activeRoute.silverCost + activeRoute.stationFeeCost))}</span></div>}
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-3 gap-2">
              {options.slice(0, 3).map((opt) => {
                const lastEdge = opt.route.edges[opt.route.edges.length - 1];
                const targetId = lastEdge?.targetId ?? opt.route.sourceId;
                const isProfit = direct > 0 && opt.totalCost < direct;
                return (
                  <button
                    key={opt.route.sourceId + "-" + targetId}
                    onClick={() => onSelect(opt)}
                    className="flex flex-col items-center gap-1 rounded-md border border-zinc-700 bg-zinc-800/50 p-2 text-center hover:border-purple-500/50 hover:bg-zinc-800"
                  >
                    <div className="flex items-center gap-1">
                      <img src={iconUrl(opt.route.sourceId, 32)} alt="" width={18} height={18} />
                      <span className="text-zinc-500">→</span>
                      <img src={iconUrl(targetId, 32)} alt="" width={18} height={18} />
                    </div>
                    <div className="text-[10px] text-zinc-400">{tierDot(opt.route.sourceId)} → {tierDot(targetId)}</div>
                    <div className={`text-[11px] font-semibold ${isProfit ? "text-emerald-400" : "text-red-400"}`}>
                      {isProfit ? "−" + silver(direct - opt.totalCost) : "+" + silver(opt.totalCost - direct)}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </span>
      )}
    </span>
  );
}

function HeartBadge({ active, onClick, price, savings, matPrice }: { active: boolean; onClick: () => void; price: number; savings: number; matPrice: number }) {
  const [showTip, setShowTip] = useState(false);
  const pulse = !active && price > 0 && matPrice >= price * 0.9;
  return (
    <span className="group relative">
      <button
        onClick={(e) => { e.stopPropagation(); onClick(); }}
        onMouseEnter={() => setShowTip(true)}
        onMouseLeave={() => setShowTip(false)}
        className={`rounded px-1 text-[10px] font-bold ${active ? "bg-red-900/30 text-red-300" : pulse ? "bg-red-900/15 text-red-300/50 craft-suggest-pulse" : "bg-zinc-800/50 text-zinc-600"}`}
      >
        H
      </button>
      {showTip && (
        <span className="absolute left-0 bottom-full z-50 mb-1 block min-w-[12rem] rounded-lg border border-zinc-700 bg-zinc-900 p-2 shadow-xl">
          <div className="text-[11px] font-semibold text-red-300">Coração</div>
          <div className="text-[11px] text-zinc-400">Substitui 1 material bruto pelo token de coração.</div>
          <div className="mt-1 border-t border-zinc-700 pt-1 text-[11px]">
            <div className="flex justify-between gap-4"><span className="text-zinc-500">Coração:</span><span className="font-semibold text-zinc-200">{silver(price)}</span></div>
            {savings !== 0 && (
              <div className={`flex justify-between gap-4 ${savings > 0 ? "text-emerald-400" : "text-red-400"}`}>
                <span>{savings > 0 ? "Economia" : "Prejuízo"}:</span>
                <span className="font-semibold">{silver(Math.abs(savings))}</span>
              </div>
            )}
          </div>
        </span>
      )}
    </span>
  );
}

function PriceField({ value, meta, onChange, w = "w-28", className }: { value?: number; meta?: PriceInfo; onChange: (v: number | undefined) => void; w?: string; className?: string }) {
  const t = useT();
  return (
    <div
      onDoubleClick={(e) => {
        e.stopPropagation();
        if (meta?.apiValue != null && value !== meta.apiValue) onChange(meta.apiValue);
      }}
      title={meta?.apiValue != null ? t("doubleClickResetTitle") : undefined}
      className={`flex items-center rounded-md border border-zinc-700 bg-zinc-950 focus-within:border-amber-500 ${w}`}
    >
      <MetaInline meta={meta} />
      <input
        type="number"
        value={value ?? ""}
        placeholder="—"
        onChange={(e) => onChange(e.target.value === "" ? undefined : +e.target.value)}
        className={`min-w-0 flex-1 bg-transparent px-1.5 py-1 text-right text-sm outline-none placeholder:text-zinc-600 ${className ?? "text-zinc-100"}`}
      />
    </div>
  );
}

function MultiCitySelect({
  cities,
  selected,
  onChange,
}: {
  cities: string[];
  selected: string[];
  onChange: (v: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);
  const toggle = (c: string) => {
    if (selected.includes(c)) onChange(selected.filter((x) => x !== c));
    else onChange([...selected, c]);
  };
  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        className={`${selectCls} flex h-9 w-full items-center justify-between`}
      >
        <span className="truncate text-xs">
          {selected.length === 0
            ? "—"
            : selected.map(cityAbbr).join(", ")}
        </span>
        <span className="ml-1 shrink-0 text-zinc-500 text-xs">▾</span>
      </button>
      {open && (
        <div className="absolute z-40 mt-1 w-56 rounded-lg border border-zinc-700 bg-zinc-900 p-2 shadow-xl">
          <div className="max-h-64 space-y-0.5 overflow-y-auto">
            {cities.map((c) => (
              <label key={c} className="flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-1 text-sm hover:bg-zinc-800">
                <input
                  type="checkbox"
                  checked={selected.includes(c)}
                  onChange={() => toggle(c)}
                  className="h-3.5 w-3.5 accent-amber-500"
                />
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ background: cityColor(c) }}
                />
                <span className="truncate text-zinc-300">{c}</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
