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
  loadLocation,
  saveLocation,
} from "../lib/craft/location";
import {
  loadCatalog,
  loadNames,
  loadWeights,
  loadJournalBase,
  shortName,
  tierLabel,
  distinctMaterials,
  type CatalogFamily,
  type CatalogVariation,
  type JournalBase,
} from "../lib/craft/catalog";
import { computeFocusEfficiency, craftTypeOf } from "../lib/craft/focusEff";
import { specTreeFor, SPEC_EXTRA_FAMILIES, OWN_SPEC_NODE, RECIPE_ALIASES } from "../lib/craft/specTree";
import type { PriceServer, PriceQuote } from "../lib/prices/types";
import { fetchAdpPrices, fetchAdpDemand, fetchAdpPriceSeries, fetchAdpGold } from "../lib/prices/adp";
import { useLang, useT, type TKey } from "../i18n";
import { silver, silverShort, decimal, percent } from "../lib/format";
import { api, type Me } from "../api";

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

const CITIES = ["Caerleon", "Bridgewatch", "Martlock", "Thetford", "Fort Sterling", "Lymhurst", "Brecilien", "Black Market"];
const CITY_ABBR: Record<string, string> = {
  Lymhurst: "LH", "Fort Sterling": "FS", Thetford: "TF", Caerleon: "CN",
  "Black Market": "BM", Brecilien: "BC", Bridgewatch: "BW", Martlock: "ML",
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
const DEMAND_CITIES = ["Lymhurst", "Fort Sterling", "Bridgewatch", "Martlock", "Thetford"];
const SELL_QUALITIES = [1, 2, 3, 4];
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

function useGroupLabels(): Record<string, string> {
  const t = useT();
  return {
    METALBAR: t("groupMetal"), PLANKS: t("groupWood"), LEATHER: t("groupLeather"),
    CLOTH: t("groupCloth"), STONEBLOCK: t("groupStone"),
    Artefatos: t("groupArtifacts"), Jornais: t("groupJournals"),
  };
}
const DEFAULT_GROUP_CITY: Record<string, string> = {
  PLANKS: "Fort Sterling", METALBAR: "Thetford", CLOTH: "Lymhurst", LEATHER: "Martlock", STONEBLOCK: "Bridgewatch",
  POTATO: "Martlock", WHEAT: "Martlock", FOXGLOVE: "Martlock", MILK: "Martlock", BUTTER: "Martlock",
  CABBAGE: "Thetford", AGARIC: "Thetford", MULLEIN: "Thetford",
  PUMPKIN: "Lymhurst", CARROT: "Lymhurst", BURDOCK: "Lymhurst",
  CORN: "Bridgewatch", BEAN: "Bridgewatch", TEASEL: "Bridgewatch",
  TURNIP: "Fort Sterling", YARROW: "Fort Sterling", EGG: "Fort Sterling",
  FLOUR: "Martlock", BREAD: "Martlock", COMFREY: "Caerleon",
};
const isArtifact = (id: string) => id.includes("ARTEFACT");
const isJournal = (id: string) => id.includes("JOURNAL");
function marketGroup(id: string): string {
  if (isArtifact(id)) return "Artefatos";
  if (isJournal(id)) return "Jornais";
  return id.replace(/^T\d+_/, "").replace(/_LEVEL\d$/, "");
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
}

function placeLabel(loc: ProductionLocation, t: (key: TKey) => string): string {
  if (loc.kind === "city") return loc.city;
  if (loc.kind === "island") return `${t("placeIsland")} ${loc.city}`;
  return `HO Q${loc.quality}/Nv${loc.power}`;
}

export default function CraftCalculator() {
  const t = useT();
  const { server } = useLang();
  const CITY_BIOME = useCityBiome();
  const GROUP_LABELS = useGroupLabels();
  const cityBiome = (city?: string) => (city ? CITY_BIOME[city] : undefined);
  const groupLabel = (g: string) => GROUP_LABELS[g] ?? g.charAt(0) + g.slice(1).toLowerCase();
  const [families, setFamilies] = useState<CatalogFamily[] | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [weights, setWeights] = useState<Record<string, number>>({});
  const [journalBase, setJournalBase] = useState<Record<string, JournalBase>>({});
  const [familyKey, setFamilyKey] = useState("MAIN_AXE");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [search, setSearch] = useState("");

  const [batchQty, setBatchQty] = useState(30);
  const useFocus = true;

  // Local de produção real — persiste no localStorage. Default: null (exige escolha).
  const [productionLocation, setProductionLocation] = useState<ProductionLocation>(() => {
    return loadLocation() ?? { kind: "city", city: "Caerleon" };
  });
  const place: CraftPlace = productionLocation.kind;
  const craftCity: ProductionCity | undefined =
    productionLocation.kind !== "hideout" ? productionLocation.city : undefined;

  const [eventBonus, setEventBonus] = useState(0);
  const [hoQuality, setHoQuality] = useState(6);
  const [hoLevel, setHoLevel] = useState(8);

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
  const [sellCity, setSellCity] = useState("Black Market");
  const [groupMarket, setGroupMarket] = useState<Record<string, string>>({});
  const [groupOrder, setGroupOrder] = useState<Record<string, OrderMode>>({});
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
  const matMetaRef = useRef(matMeta);
  const sellMetaRef = useRef(sellMeta);
  useEffect(() => {
    matMetaRef.current = matMeta;
    sellMetaRef.current = sellMeta;
  }, [matMeta, sellMeta]);

  const nameOf = (id: string) => shortName(names[id.split("@")[0]] ?? id);
  const cityForGroup = (g: string) => groupMarket[g] ?? DEFAULT_GROUP_CITY[g] ?? "Caerleon";
  const orderForGroup = (g: string): OrderMode => groupOrder[g] ?? "sell";
  const cityForMat = (id: string) => cityForGroup(marketGroup(id));

  useEffect(() => {
    loadCatalog().then((fams) => {
      setFamilies(fams);
      if (!fams.some((f) => f.familyKey === familyKey)) setFamilyKey(fams[0]?.familyKey);
    });
    loadNames().then(setNames);
    loadWeights().then(setWeights);
    loadJournalBase().then(setJournalBase);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  // Persiste a localização de produção no localStorage a cada mudança.
  useEffect(() => { saveLocation(productionLocation); }, [productionLocation]);

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
      if (pickerRef.current && !pickerRef.current.contains(t)) setPickerOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const family = useMemo(() => families?.find((f) => f.familyKey === familyKey) ?? null, [families, familyKey]);
  const variations = useMemo(
    () => (family ? [...family.variations].sort((a, b) => a.tier - b.tier || a.enchant - b.enchant) : []),
    [family],
  );
  const prof = professionOf(family?.craftCategory ?? null) ?? (family ? professionFromId(family.variations[0].uniqueName) : null);
  const isConsumable = family?.kind === "consumable";

  const artifactByTier = useMemo(() => {
    const m = new Map<number, string>();
    for (const v of variations) {
      if (v.enchant !== 0) continue;
      const art = v.resources.find((r) => isArtifact(r.uniqueName));
      if (art) m.set(v.tier, art.uniqueName);
    }
    return m;
  }, [variations]);

  const mats = family ? distinctMaterials(family.variations) : [];
  const journalIds = prof ? [...new Set(variations.map((v) => journalId(v.tier, prof)))] : [];
  const groups = [...new Set([...mats, ...journalIds].map(marketGroup))];

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
  const autoSpecialized = isSpecialized(productionLocation, family?.bonusCity, hideoutEligible);
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
  const rrNoFocus = returnRateNoFocus(location);
  const rrFocus = returnRateFocus(location);
  // ponytail: spec é 0..100 por arma; FCE real vem da árvore (irmãs + mastery)
  // via focusEff.ts. mastery=0 porque o usuário só informa spec de armas.
  const focusFce = useMemo(() => {
    if (!family) return 0;
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
  }, [family, familyKey, siblings, focusEfficiencyByFamily]);
  const focusMult = focusCostMultiplier(focusFce);
  const settings: Settings = { premium, stationFeePer100 };
  const PREMIUM_GOLD = 3750;
  const MONTHLY_FOCUS = 300_000;
  const minSpf = goldPrice > 0 ? (goldPrice * PREMIUM_GOLD) / MONTHLY_FOCUS : 0;

  const compute = (v: CatalogVariation): CraftResult =>
    computeCraft({
      quantity: batchQty,
      materials: v.resources.map((r) => ({ uniqueName: r.uniqueName, unitPrice: matPrices[r.uniqueName] ?? 0, countPerCraft: r.count, noReturn: r.noReturn })),
      sellPrice: sellPrices[v.uniqueName] ?? 0,
      outputPerCraft: v.outputPerCraft ?? 1,
      returnRateNoFocus: rrNoFocus,
      returnRateFocus: rrFocus,
      focusCostBase: v.focus,
      focusEfficiency: focusFce,
      itemValue: v.itemValue,
      stationFeePer100,
      salesTaxRate: premium ? 0.04 : 0.08,
      setupFeeRate: SETUP_FEE,
    });

  const filteredFamilies = useMemo(() => {
    if (!families) return [];
    const q = search.trim().toLowerCase();
    if (!q) return families;
    return families.filter((f) =>
      f.name.toLowerCase().includes(q) ||
      f.familyKey.toLowerCase().includes(q) ||
      (RECIPE_ALIASES[f.familyKey] ?? []).some((a) => a.includes(q))
    );
  }, [families, search]);

  function setMat(id: string, value: number | undefined) {
    setMatPrices((p) => ({ ...p, [id]: value }));
    setMatMeta((m) => ({ ...m, [id]: { source: "manual", date: Date.now() } }));
  }
  function setSell(id: string, value: number | undefined) {
    setSellPrices((p) => ({ ...p, [id]: value }));
    setSellMeta((m) => ({ ...m, [id]: { source: "manual", date: Date.now() } }));
  }

  const priced = (v: CatalogVariation) =>
    sellPrices[v.uniqueName] != null && v.resources.every((r) => matPrices[r.uniqueName] != null);

  function addOrder(v: CatalogVariation) {
    if (!family) return;
    setCart((c) => [
      ...c,
      {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        name: family.name,
        variation: v,
        qty: batchQty,
        useFocus,
        rr: useFocus ? rrFocus : rrNoFocus,
        placeLabel: placeLabel(productionLocation, t),
        journalId: prof ? journalId(v.tier, prof) : null,
        focusEfficiency: focusFce,
      },
    ]);
  }

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
    const matIds = [...distinctMaterials(family.variations), ...journalIds];
    const varIds = variations.map((v) => v.uniqueName);
    const fullIds = journalIds.map((id) => `${id}_FULL`);
    const allItems = [...new Set([...matIds, ...varIds, ...fullIds])];
    const locs = [...new Set([sellCity, ...matIds.map(cityForMat)])];

    setLoadingPrices(true);
    setFetchError(null);
    try {
      const adpQuotes = await fetchAdpPrices(server, allItems, locs, SELL_QUALITIES);
      const q = new Map<string, PriceQuote>();
      for (const x of adpQuotes) q.set(`${x.itemId}|${x.city}|${x.quality}`, x);

      const curMatMeta = matMetaRef.current;
      const curSellMeta = sellMetaRef.current;

      const matVal = (id: string) => {
        const quote = q.get(`${id}|${cityForMat(id)}|1`);
        if (!quote || !isFresh(quote.updatedAt)) return null;
        const v = orderForGroup(marketGroup(id)) === "buy" ? quote.buyMax : quote.sellMin;
        return v ? { v, date: quote.updatedAt } : null;
      };
      setMatPrices((prev) => {
        const next = { ...prev };
        for (const id of matIds) {
          if (curMatMeta[id]?.source === "manual") continue;
          const r = matVal(id);
          if (r) next[id] = r.v;
        }
        return next;
      });
      setMatMeta((prev) => {
        const next = { ...prev };
        for (const id of matIds) {
          if (prev[id]?.source === "manual") continue;
          const r = matVal(id);
          if (r) next[id] = { source: "api", date: r.date, city: cityForMat(id) };
        }
        return next;
      });

      const useBuyOrder = sellCity === "Black Market";
      const avgSell: Record<string, number> = {};
      const sellDate: Record<string, number> = {};
      for (const id of varIds) {
        const vals: number[] = [];
        let latest = 0;
        for (const ql of SELL_QUALITIES) {
          const quote = q.get(`${id}|${sellCity}|${ql}`);
          if (!quote || !isFresh(quote.updatedAt)) continue;
          const v = useBuyOrder ? quote.buyMax : quote.sellMin;
          if (v) {
            vals.push(v);
            latest = Math.max(latest, quote.updatedAt);
          }
        }
        if (vals.length) {
          avgSell[id] = Math.round(vals.reduce((s, n) => s + n, 0) / vals.length);
          sellDate[id] = latest;
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
          if (avgSell[id]) next[id] = { source: "api", date: sellDate[id], city: sellCity };
        }
        return next;
      });

      setFullJournalPrices((prev) => {
        const next = { ...prev };
        for (const id of journalIds) {
          const quote = q.get(`${id}_FULL|${sellCity}|1`);
          if (!quote || !isFresh(quote.updatedAt)) continue;
          const v = useBuyOrder ? quote.buyMax : quote.sellMin;
          if (v) next[id] = v;
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
  }, [family, server, sellCity, groupMarket, groupOrder]);

  const rows: React.ReactNode[] = [];
  let lastTier = -1;
  for (const v of variations) {
    if (v.tier !== lastTier) {
      lastTier = v.tier;
      const art = artifactByTier.get(v.tier);
      if (art) {
        rows.push(
          <tr key={`art-${v.tier}`} className="border-t border-zinc-700 bg-purple-900/10">
            <td colSpan={9} className="px-2 py-1.5">
              <div className="flex flex-nowrap items-center gap-2 text-xs">
                <ItemIcon id={art} size={22} city={cityForMat(art)} server={server} name={nameOf(art)} />
                <span className="font-semibold text-purple-300">{nameOf(art)}</span>
                <PriceField value={matPrices[art]} meta={matMeta[art]} onChange={(val) => setMat(art, val)} w="w-24" />
              </div>
            </td>
          </tr>,
        );
      }
    }
    const r = compute(v);
    const ok = priced(v);
    const isProfit = ok && (useFocus ? r.profitFocus > 0 && r.silverPerFocus >= minSpf : r.profitNoFocus > 0);
    const journals = journalsFilled(v.resources, v.tier, v.enchant, batchQty);
    const visibleMats = v.resources.filter((res) => !isArtifact(res.uniqueName));
    rows.push(
      <tr key={v.uniqueName} onDoubleClick={() => addOrder(v)} className="border-b border-zinc-900 hover:bg-zinc-800/40">
        <td className="whitespace-nowrap px-2 py-1.5">
          <div className="flex items-center gap-2">
            <ItemIcon id={v.uniqueName} size={30} city={sellCity} server={server} name={`${nameOf(v.uniqueName)} ${tierLabel(v)}`} />
            <span className={`font-medium ${isProfit ? "text-emerald-400" : "text-zinc-200"}`}>{tierLabel(v)}</span>
            {v.outputPerCraft && v.outputPerCraft > 1 && <span className="rounded bg-zinc-800 px-1 text-[10px] text-zinc-400" title={t("itemsPerCraftTitle")}>×{v.outputPerCraft}</span>}
          </div>
        </td>
        <td className="px-2 py-1.5">
          <div className="flex flex-nowrap items-center gap-1.5">
            {visibleMats.map((res) => (
              <div key={res.uniqueName} className="flex items-center gap-1">
                <span className="relative shrink-0">
                  <ItemIcon id={res.uniqueName} size={22} city={cityForMat(res.uniqueName)} server={server} name={nameOf(res.uniqueName)} />
                  <span className="absolute bottom-0.5 right-0 rounded bg-zinc-900/85 px-0.5 text-[9px] leading-tight tabular-nums text-zinc-300" title={t("qtyPerCraftTitle")}>{res.count}</span>
                </span>
                <PriceField value={matPrices[res.uniqueName]} meta={matMeta[res.uniqueName]} onChange={(val) => setMat(res.uniqueName, val)} w="w-16" />
              </div>
            ))}
          </div>
        </td>
        <Td right muted>{silver(v.focus * focusMult)}</Td>
        <td className="px-2 py-1.5">
          <div className="flex justify-end">
            <PriceField value={sellPrices[v.uniqueName]} meta={sellMeta[v.uniqueName]} onChange={(val) => setSell(v.uniqueName, val)} w="w-24" />
          </div>
        </td>
        <Td right value={ok ? r.profitNoFocus : undefined} sub={ok ? percent(r.marginNoFocus) : undefined}>{ok ? silverShort(r.profitNoFocus) : "—"}</Td>
        <Td right value={ok ? r.profitFocus : undefined} sub={ok ? percent(r.marginFocus) : undefined}>{ok ? silverShort(r.profitFocus) : "—"}</Td>
        <Td right value={ok ? r.silverPerFocus : undefined}>{ok ? decimal(r.silverPerFocus) : "—"}</Td>
        <Td right muted>{isConsumable ? "—" : decimal(journals)}</Td>
        <td className="px-2 py-1.5 text-right">
          {demand[v.uniqueName] != null ? <span className={demand[v.uniqueName] < 5 ? "text-red-400" : "text-zinc-300"}>{silver(demand[v.uniqueName])}</span> : <span className="text-zinc-600">—</span>}
        </td>
      </tr>,
    );
  }

  return (
    <div className="mx-auto w-full max-w-[1800px] px-4 py-5">
      {/* Control bar */}
      <div className="mb-4 flex flex-wrap items-start gap-3">
        {/* Item dropdown */}
        <div className="relative min-w-64 flex-1" ref={pickerRef}>
          <button onClick={() => setPickerOpen((o) => !o)} className="flex h-[36px] w-full items-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900 px-2.5 text-left hover:border-zinc-600">
            {family && (
              <img src={iconUrl(displayVariation(family).uniqueName, 96, displayQuality(family))} alt="" width={28} height={28} />
            )}
            <span className="flex-1 truncate text-sm font-semibold text-zinc-100">{family?.name ?? t("loading")}</span>
            {family?.bonusCity && <span className="hidden rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] font-medium sm:inline" style={{ color: cityColor(family.bonusCity) }} title={bonusBiome ? `${t("craftBonusBiomeTitle")} · ${family.bonusCity}` : t("craftBonusCityTitle")}>{bonusBiome ?? family.bonusCity}</span>}
            <span className="text-zinc-500">▾</span>
          </button>
          {pickerOpen && (
            <div className="absolute z-40 mt-1 w-96 rounded-lg border border-zinc-700 bg-zinc-900 p-2 shadow-xl">
              <input autoFocus value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t("searchItemPlaceholder")} className="mb-2 w-full rounded-md border border-zinc-700 bg-zinc-950 px-2.5 py-1.5 text-sm outline-none focus:border-amber-500" />
              <div className="max-h-96 space-y-0.5 overflow-y-auto">
                {filteredFamilies.map((f) => (
                  <button key={f.familyKey} onClick={() => { setFamilyKey(f.familyKey); setPickerOpen(false); setSearch(""); }} className={`flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-sm ${f.familyKey === familyKey ? "bg-amber-500/15 text-amber-300" : "text-zinc-300 hover:bg-zinc-800"}`}>
                    <img src={iconUrl(displayVariation(f).uniqueName, 64, displayQuality(f))} alt="" width={30} height={30} />
                    <span className="truncate">{f.name}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Production controls */}
        <div className="flex flex-wrap items-center gap-2">
          <InlineNum label={t("qtyLabel")} value={batchQty} onChange={setBatchQty} w="w-28" />
          <ToggleBtn active={premium} on="Premium" off="Premium" onClick={() => setPremium((p) => !p)} />
          <InlineNum label={t("feePerHundredLabel")} value={stationFeePer100} onChange={setStationFeePer100} w="w-32" />
          <div className="flex h-[36px] items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-900 px-2.5" title={t("goldPriceTooltip")}>
            <span className="text-[11px] text-zinc-500">🪙</span>
            <span className="text-sm tabular-nums text-zinc-100">{goldPrice ? silver(goldPrice) : "—"}</span>
          </div>
          <IconButton onClick={fetchMarket} disabled={loadingPrices || !family} title={fetchError ?? t("updatePricesTitle")} spinning={loadingPrices}>⟳</IconButton>
          {fetchError && <span className="text-xs text-red-400" title={fetchError}>⚠</span>}
        </div>
      </div>

      {/* Main grid: config | lista | carrinho */}
      <div className="grid items-start gap-5 min-[1500px]:grid-cols-[300px_1fr_300px]">
        <SettingsPanel
          productionLocation={productionLocation} setProductionLocation={setProductionLocation}
          hideoutEligible={hideoutEligible}
          hoQuality={hoQuality} setHoQuality={setHoQuality} hoLevel={hoLevel} setHoLevel={setHoLevel}
          eventBonus={eventBonus} setEventBonus={setEventBonus}
          bonusCity={family?.bonusCity ?? null} autoSpecialized={autoSpecialized}
          craftCity={craftCity}
          sellCity={sellCity} setSellCity={setSellCity}
          minSpf={minSpf}
          groups={groups} groupLabel={groupLabel} cityForGroup={cityForGroup} setGroupMarket={setGroupMarket}
          orderForGroup={orderForGroup} setGroupOrder={setGroupOrder}
          baseVar={baseVar}
          siblings={siblings}
          focusEfficiencyByFamily={focusEfficiencyByFamily} setFocusEff={setFocusEff} commitFocusEfficiency={commitFocusEfficiency}
          ignoredJournalTiers={ignoredJournalTiers} toggleJournalTier={toggleJournalTier}
        />

        <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-900/40">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-left text-[11px] uppercase tracking-wide text-zinc-500">
                <Th>Item</Th>
                <Th>{t("colMaterials")}</Th>
                <Th right>Focus cost</Th>
                <Th right>{t("colSellAvg")}</Th>
                <Th right><span title={t("colProfitNoFocusTitle")}>{t("colProfitNoFocus")}</span></Th>
                <Th right><span title={t("colProfitFocusTitle")}>{t("colProfitFocus")}</span></Th>
                <Th right>SPF</Th>
                <Th right>{t("colJournals")}</Th>
                <Th right>
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

        <Cart cart={cart} matPrices={matPrices} sellPrices={sellPrices} weights={weights} journalBase={journalBase} fullJournalPrices={fullJournalPrices} settings={settings} ignoredTiers={ignoredJournalTiers} rrNoFocus={rrNoFocus} rrFocus={rrFocus} nameOf={nameOf} onRemove={(id) => setCart((c) => c.filter((o) => o.id !== id))} onClear={() => setCart([])} />
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
  sellCity, setSellCity, minSpf,
  groups, groupLabel, cityForGroup, setGroupMarket, orderForGroup, setGroupOrder,
  baseVar,
  siblings,
  focusEfficiencyByFamily, setFocusEff, commitFocusEfficiency,
  ignoredJournalTiers, toggleJournalTier,
}: {
  productionLocation: ProductionLocation; setProductionLocation: (l: ProductionLocation) => void; hideoutEligible: boolean;
  hoQuality: number; setHoQuality: (v: number) => void; hoLevel: number; setHoLevel: (v: number) => void;
  eventBonus: number; setEventBonus: (v: number) => void;
  bonusCity: string | null; autoSpecialized: boolean;
  craftCity: ProductionCity | undefined;
  sellCity: string; setSellCity: (v: string) => void; minSpf: number;
  groups: string[]; groupLabel: (g: string) => string; cityForGroup: (g: string) => string;
  setGroupMarket: React.Dispatch<React.SetStateAction<Record<string, string>>>;
  orderForGroup: (g: string) => OrderMode; setGroupOrder: React.Dispatch<React.SetStateAction<Record<string, OrderMode>>>;
  baseVar: CatalogVariation | null;
  siblings: CatalogFamily[];
  focusEfficiencyByFamily: Record<string, number>; setFocusEff: (key: string, value: number) => void; commitFocusEfficiency: () => void;
  ignoredJournalTiers: Set<number>; toggleJournalTier: (t: number) => void;
}) {
  const t = useT();
  const [marketOpen, setMarketOpen] = useState(false);
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
              ✓ {t("craftBonusActive")}: {bonusCity} +15%
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

      {/* Mercado (venda + por material, colapsável) */}
      <div className="space-y-2 border-t border-zinc-800 pt-3">
        <button onClick={() => setMarketOpen((o) => !o)} className="flex w-full items-center justify-between text-xs font-semibold uppercase tracking-wide text-zinc-500 hover:text-zinc-300">
          <span>{t("marketHeader")}</span>
          <span className="text-zinc-400">{marketOpen ? "▾" : "▸"}</span>
        </button>
        {marketOpen && (
          <>
            <Field label={t("sellAtLabel")}>
              <select value={sellCity} onChange={(e) => setSellCity(e.target.value)} className={`${selectCls} w-full`}>
                {CITIES.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </Field>
            {minSpf > 0 && <p className="text-xs text-zinc-500">{t("minSpfLabel")} <b className="text-zinc-300">{decimal(minSpf)}</b> {t("minSpfSuffix")}</p>}
            {groups.map((g) => (
              <div key={g} className="flex items-end gap-2">
                <Field label={groupLabel(g)}>
                  <select value={cityForGroup(g)} onChange={(e) => setGroupMarket((m) => ({ ...m, [g]: e.target.value }))} className={`${selectCls} w-full`}>
                    {CITIES.filter((c) => c !== "Black Market").map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </Field>
                <select value={orderForGroup(g)} onChange={(e) => setGroupOrder((m) => ({ ...m, [g]: e.target.value as OrderMode }))} className={selectCls} title={t("orderTypeTitle")}>
                  <option value="sell">{t("sellOrderOption")}</option>
                  <option value="buy">{t("buyOrderOption")}</option>
                </select>
              </div>
            ))}
          </>
        )}
      </div>

      {/* Focus efficiency */}
      {siblings.length > 0 && (
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
                <span className="flex-1 truncate text-xs text-zinc-300">{f.name}</span>
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
      )}

      {/* Ignorar jornais */}
      <div className="space-y-2 border-t border-zinc-800 pt-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">{t("ignoreJournalsHeader")}</h3>
        <p className="text-xs text-zinc-500">{t("ignoreJournalsHint")}</p>
        <div className="flex flex-wrap gap-1.5">
          {TIERS.map((tier) => (
            <button key={tier} onClick={() => toggleJournalTier(tier)} className={`rounded-md border px-2 py-1 text-xs ${ignoredJournalTiers.has(tier) ? "border-red-500/60 bg-red-900/20 text-red-300" : "border-zinc-700 text-zinc-300"}`}>T{tier}</button>
          ))}
        </div>
      </div>
    </aside>
  );
}

/* ---------------- Cart ---------------- */

function Cart({
  cart, matPrices, sellPrices, weights, journalBase, fullJournalPrices, settings, ignoredTiers, rrNoFocus, rrFocus, nameOf, onRemove, onClear,
}: {
  cart: Order[];
  matPrices: Record<string, number | undefined>;
  sellPrices: Record<string, number | undefined>;
  weights: Record<string, number>;
  journalBase: Record<string, JournalBase>;
  fullJournalPrices: Record<string, number>;
  settings: Settings;
  ignoredTiers: Set<number>;
  rrNoFocus: number;
  rrFocus: number;
  nameOf: (id: string) => string;
  onRemove: (id: string) => void;
  onClear: () => void;
}) {
  const t = useT();
  const journalUnit = (id: string) => matPrices[id] ?? journalBase[id]?.base ?? 0;
  const orderProfit = (o: Order) =>
    computeCraft({
      quantity: o.qty,
      materials: o.variation.resources.map((r) => ({ uniqueName: r.uniqueName, unitPrice: matPrices[r.uniqueName] ?? 0, countPerCraft: r.count, noReturn: r.noReturn })),
      sellPrice: sellPrices[o.variation.uniqueName] ?? 0,
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
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-zinc-200">{t("craftCartTitle")}</h2>
        <div className="flex items-center gap-2 text-xs text-zinc-400">
          <span>{t("retNoFocusAbbr")} <b className="text-zinc-100">{percent(rrNoFocus)}</b></span>
          <span>{t("retFocusAbbr")} <b className="text-zinc-100">{percent(rrFocus)}</b></span>
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
            <button onClick={onClear} className="mb-2 text-xs text-zinc-500 hover:text-red-400">{t("clearAllBtn")}</button>
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

          {cart.map((o) => {
            const journals = journalsFilled(o.variation.resources, o.variation.tier, o.variation.enchant, o.qty);
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
                      <span className="rounded bg-zinc-800 px-1.5 py-0.5">{decimal(journals)} {t("journalsBadgeSuffix")}</span>
                      <span className="rounded bg-zinc-800 px-1.5 py-0.5">{silver(total)}</span>
                    </div>
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

function ToggleBtn({ active, on, off, onClick }: { active: boolean; on: string; off: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`h-[36px] rounded-md border px-3 text-sm font-medium ${active ? "border-amber-500 bg-amber-500 text-zinc-950" : "border-zinc-700 bg-zinc-900 text-zinc-400"}`}>
      {active ? on : off}
    </button>
  );
}

function Field({ label, children }: { label: React.ReactNode; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-zinc-400">{label}</span>
      {children}
    </label>
  );
}

function Th({ children, right }: { children?: React.ReactNode; right?: boolean }) {
  return <th className={`sticky top-0 z-20 bg-zinc-900 px-2 py-1.5 font-medium ${right ? "text-right" : ""}`}>{children}</th>;
}

function Td({ children, right, muted, value, sub }: { children: React.ReactNode; right?: boolean; muted?: boolean; value?: number; sub?: React.ReactNode }) {
  const color = value === undefined ? "" : value > 0 ? "text-emerald-400" : value < 0 ? "text-red-400" : "";
  return (
    <td className={`whitespace-nowrap px-2 py-1.5 ${right ? "text-right" : ""} ${muted ? "text-zinc-500" : ""} ${color}`}>
      {sub != null ? (
        <div className={`flex flex-col leading-tight ${right ? "items-end" : ""}`}>
          <span>{children}</span>
          <span className="text-[10px] opacity-70">{sub}</span>
        </div>
      ) : (
        children
      )}
    </td>
  );
}

function MetaInline({ meta }: { meta?: PriceInfo }) {
  const t = useT();
  if (!meta) return null;
  if (meta.source === "manual")
    return <span className="flex w-7 shrink-0 items-center justify-center text-[10px] text-amber-400" title={t("manuallyEditedTitle")}>✎</span>;
  const stale = isStale(meta.date);
  return (
    <span className={`flex w-7 shrink-0 flex-col items-center justify-center leading-none ${stale ? "text-orange-400" : "text-sky-400"}`} title={`${t("apiPriceTitle")}${meta.city ? ` ${t("inCityWord")} ${meta.city}` : ""}`}>
      <span className="text-[9px]" style={{ color: cityColor(meta.city) }}>{meta.date ? timeAgo(meta.date) : "—"}</span>
      <span className="text-[9px] font-semibold" style={{ color: cityColor(meta.city) }}>{cityAbbr(meta.city)}</span>
    </span>
  );
}

function PriceField({ value, meta, onChange, w = "w-28" }: { value?: number; meta?: PriceInfo; onChange: (v: number | undefined) => void; w?: string }) {
  return (
    <div className={`flex items-center rounded-md border border-zinc-700 bg-zinc-950 focus-within:border-amber-500 ${w}`}>
      <MetaInline meta={meta} />
      <input
        type="number"
        value={value ?? ""}
        placeholder="—"
        onChange={(e) => onChange(e.target.value === "" ? undefined : +e.target.value)}
        className="min-w-0 flex-1 bg-transparent px-1.5 py-1 text-right text-sm text-zinc-100 outline-none placeholder:text-zinc-600"
      />
    </div>
  );
}
