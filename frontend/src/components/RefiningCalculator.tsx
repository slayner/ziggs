"use client";

import { useEffect, useMemo, useState } from "react";
import {
  loadRefiningCatalog,
  type RefiningRecipe,
  type RefiningVariant,
  type RefiningCatalog,
} from "../lib/craft/refiningCatalog";
import {
  refiningReturnRateNoFocus,
  refiningReturnRateFocus,
  refiningFocusEfficiency,
  refiningFocusCost,
  REFINING_CITIES,
} from "../lib/craft/refining";
import { findRoutes, cheapestOption } from "../lib/craft/transmutation";
import {
  PRODUCTION_CITIES,
  type ProductionCity,
  type ProductionLocation,
  loadLocation,
  saveLocation,
  loadRefiningSpecs,
  saveRefiningSpecs,
} from "../lib/craft/location";
import type { PriceServer, PriceQuote } from "../lib/prices/types";
import { fetchAdpPrices, fetchAdpGold } from "../lib/prices/adp";
import { useLang, useT } from "../i18n";
import { silver, silverShort, decimal, percent } from "../lib/format";

const iconUrl = (id: string, size = 64) =>
  `/render/item/${encodeURIComponent(id)}?size=${size}&v=2`;

const SELL_CITIES = [
  "Caerleon", "Bridgewatch", "Martlock", "Thetford", "Fort Sterling",
  "Lymhurst", "Brecilien", "Arthur's Rest", "Merlyn's Rest", "Morgana's Rest",
  "Black Market",
];

const SHADOWHEART_ID = "T1_FACTION_CAERLEON_TOKEN_1";

export type FamilyKey = "fiber" | "hide" | "ore" | "wood" | "stone";

interface RefiningOrder {
  id: string;
  recipeKey: string;
  outputId: string;
  tier: number;
  enchant: number;
  variantKind: "normal" | "heart";
  qty: number;
  rr: number;
  profitNoF: number;
  profitF: number;
  spf: number;
  placeLabel: string;
}

export default function RefiningCalculator({
  family: initialFamily,
  onAddToCart,
}: {
  family?: FamilyKey;
  onAddToCart?: (order: RefiningOrder) => void;
} = {}) {
  const t = useT();
  const { server } = useLang();

  const [catalog, setCatalog] = useState<RefiningCatalog | null>(null);
  const [family, setFamily] = useState<FamilyKey>(initialFamily ?? "ore");
  const [batchQty, setBatchQty] = useState(30);
  const [premium, setPremium] = useState(true);
  const [stationFee, setStationFee] = useState(1000);
  const [goldPrice, setGoldPrice] = useState(0);
  const [eventBonus, setEventBonus] = useState(0);
  const [allSpecs, setAllSpecs] = useState<Record<string, Record<number, number>>>(() => loadRefiningSpecs());
  const specs = allSpecs[family] ?? { 4: 0, 5: 0, 6: 0, 7: 0, 8: 0 };
  const [productionLocation, setProductionLocation] = useState<ProductionLocation>(
    () => loadLocation() ?? { kind: "city", city: "Thetford" },
  );
  const [sellCity, setSellCity] = useState("Black Market");
  const [matCity, setMatCity] = useState("Thetford");
  const [matPrices, setMatPrices] = useState<Record<string, number>>({});
  const [sellPrices, setSellPrices] = useState<Record<string, number>>({});
  const [loadingPrices, setLoadingPrices] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  useEffect(() => {
    loadRefiningCatalog().then(setCatalog).catch(console.error);
  }, []);

  useEffect(() => { saveLocation(productionLocation); }, [productionLocation]);
  useEffect(() => { saveRefiningSpecs(allSpecs); }, [allSpecs]);

  function setSpec(tier: number, value: number) {
    setAllSpecs((prev) => ({ ...prev, [family]: { ...(prev[family] ?? {}), [tier]: value } }));
  }

  useEffect(() => { fetchAdpGold(server).then(setGoldPrice).catch(() => {}); }, [server]);

  const place = productionLocation.kind;
  const craftCity = productionLocation.kind !== "hideout" ? productionLocation.city : undefined;
  const minSpf = goldPrice > 0 ? (goldPrice * 3750) / 300_000 : 0;

  // Todas as receitas da família
  const familyRecipes = useMemo(() => {
    if (!catalog) return [];
    return catalog.recipes
      .filter((r) => r.family === family)
      .sort((a, b) => a.tier - b.tier || a.enchant - b.enchant);
  }, [catalog, family]);

  // Return rate (igual pra todas as receitas da mesma família)
  const rrNoFocus = useMemo(() => {
    return refiningReturnRateNoFocus(place, craftCity, family, eventBonus);
  }, [place, craftCity, family, eventBonus]);

  const rrFocus = useMemo(() => {
    return refiningReturnRateFocus(place, craftCity, family, eventBonus);
  }, [place, craftCity, family, eventBonus]);

  // Focus efficiency por tier (spec varia por tier)
  const focusEffByTier = useMemo(() => {
    const m = new Map<number, number>();
    for (const tier of [4, 5, 6, 7, 8]) {
      m.set(tier, refiningFocusEfficiency(family, tier, specs));
    }
    return m;
  }, [family, specs]);

  const nameOf = (id: string) => {
    if (!catalog) return id;
    return catalog.names[id]?.en || id;
  };

  // IDs de itens que precisam de preço: todos os outputs + inputs + hearts + shadowheart + transmutação sources
  const allPriceIds = useMemo(() => {
    const ids = new Set<string>();
    for (const r of familyRecipes) {
      ids.add(r.outputId);
      for (const v of r.variants) {
        for (const inp of v.inputs) ids.add(inp.itemId);
      }
    }
    // Shadowheart pra comparação de coração
    if (familyRecipes.some((r) => r.variants.some((v) => v.kind === "heart"))) {
      ids.add(SHADOWHEART_ID);
    }
    // Sources de transmutação (pra comparar compra direta vs transmutar)
    if (catalog) {
      for (const r of familyRecipes) {
        const routes = findRoutes(catalog.transmutations, r.outputId, stationFee);
        for (const route of routes) ids.add(route.sourceId);
      }
    }
    return [...ids];
  }, [familyRecipes, catalog, stationFee]);

  async function fetchMarket() {
    if (allPriceIds.length === 0 || !catalog) return;
    setLoadingPrices(true);
    setFetchError(null);
    try {
      const locs = [sellCity, matCity];
      const quotes = await fetchAdpPrices(server as PriceServer, allPriceIds, locs, [1]);
      const q = new Map<string, PriceQuote>();
      for (const x of quotes) q.set(`${x.itemId}|${x.city}|1`, x);

      setMatPrices(() => {
        const next: Record<string, number> = {};
        for (const id of allPriceIds) {
          const quote = q.get(`${id}|${matCity}|1`);
          if (quote && quote.sellMin > 0) next[id] = quote.sellMin;
        }
        return next;
      });
      setSellPrices(() => {
        const next: Record<string, number> = {};
        for (const r of familyRecipes) {
          const quote = q.get(`${r.outputId}|${sellCity}|1`);
          if (quote) {
            next[r.outputId] = sellCity === "Black Market" ? quote.buyMax || quote.sellMin : quote.sellMin;
          }
        }
        return next;
      });
    } catch (e: any) {
      setFetchError(String(e?.message ?? e));
    } finally {
      setLoadingPrices(false);
    }
  }

  useEffect(() => {
    if (catalog) fetchMarket();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [family, sellCity, matCity, server, catalog, stationFee]);

  // Calcula lucro de uma variante
  function calcProfit(recipe: RefiningRecipe, variant: RefiningVariant) {
    const q = batchQty;
    const sellPrice = sellPrices[recipe.outputId] ?? 0;
    const matCost = variant.inputs.reduce((s, r) => s + (matPrices[r.itemId] ?? 0) * r.count * q, 0);
    const returnableCost = variant.inputs.filter((r) => r.returnable)
      .reduce((s, r) => s + (matPrices[r.itemId] ?? 0) * r.count * q, 0);
    const revenue = q * variant.outputCount * sellPrice;
    const stationFeeAmt = Math.ceil(q * (stationFee / 100) * recipe.itemValue * 0.1125);
    const taxes = Math.ceil(revenue * (premium ? 0.04 : 0.08) + revenue * 0.025);
    const focusEff = focusEffByTier.get(recipe.tier) ?? 0;
    const focusCost = refiningFocusCost(variant.focus, variant.outputCount, focusEff) * q;

    const profitNoF = revenue - (matCost - returnableCost * rrNoFocus + stationFeeAmt + taxes);
    const profitF = revenue - (matCost - returnableCost * rrFocus + stationFeeAmt + taxes);
    const spf = focusCost > 0 ? (profitNoF < 0 ? profitF / focusCost : (profitF - profitNoF) / focusCost) : 0;

    return { matCost, revenue, stationFeeAmt, taxes, focusCost, profitNoF, profitF, spf, sellPrice };
  }

  // Sugestão de transmutação: se comprar source + transmutar é mais barato que comprar direto
  function transmuteHint(recipe: RefiningRecipe): string | null {
    if (!catalog) return null;
    const directPrice = matPrices[recipe.outputId] ?? 0;
    if (directPrice <= 0) return null;
    const routes = findRoutes(catalog.transmutations, recipe.outputId, stationFee);
    const best = cheapestOption(routes, directPrice, matPrices);
    if (best && best.kind === "route" && best.route) {
      const sourcePrice = matPrices[best.route.sourceId] ?? 0;
      if (sourcePrice > 0 && best.totalCost < directPrice * 0.95) {
        return `${nameOf(best.route.sourceId)} → ${silver(best.totalCost)} (${silver(directPrice)} direto)`;
      }
    }
    return null;
  }

  // Sugestão de coração: se heart é mais barato que normal
  function heartHint(recipe: RefiningRecipe): { cheaper: boolean; heartProfit: number; normalProfit: number } | null {
    const normal = recipe.variants.find((v) => v.kind === "normal");
    const heart = recipe.variants.find((v) => v.kind === "heart");
    if (!normal || !heart) return null;
    const normalR = calcProfit(recipe, normal);
    const heartR = calcProfit(recipe, heart);
    return { cheaper: heartR.profitF > normalR.profitF, heartProfit: heartR.profitF, normalProfit: normalR.profitF };
  }

  const bonusCity = REFINING_CITIES[family];
  const isSpecializedCity = craftCity === bonusCity;
  const langKey = server === "west" || server === "east" ? "en" : "pt";

  const FAMILIES: Record<string, { pt: string; en: string; es: string }> = {
    fiber: { pt: "Fibra", en: "Fiber", es: "Fibra" },
    hide: { pt: "Couro", en: "Hide", es: "Cuero" },
    ore: { pt: "Minério", en: "Ore", es: "Mineral" },
    wood: { pt: "Madeira", en: "Wood", es: "Madera" },
    stone: { pt: "Pedra", en: "Stone", es: "Piedra" },
  };

  function addOrder(recipe: RefiningRecipe, variant: RefiningVariant, result: ReturnType<typeof calcProfit>) {
    if (!onAddToCart) return;
    const placeLabel = craftCity ?? "Thetford";
    onAddToCart({
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      recipeKey: recipe.key,
      outputId: recipe.outputId,
      tier: recipe.tier,
      enchant: recipe.enchant,
      variantKind: variant.kind,
      qty: batchQty,
      rr: rrFocus,
      profitNoF: result.profitNoF,
      profitF: result.profitF,
      spf: result.spf,
      placeLabel,
    });
  }

  // Render das linhas da tabela
  const rows: React.ReactNode[] = [];
  for (const recipe of familyRecipes) {
    if (recipe.tier < 4) continue; // T2/T3 sem specs, menos relevantes
    const normal = recipe.variants.find((v) => v.kind === "normal");
    const heart = recipe.variants.find((v) => v.kind === "heart");
    const variants = [normal, heart].filter((v): v is RefiningVariant => v != null);

    for (const v of variants) {
      const r = calcProfit(recipe, v);
      const hasPrice = (matPrices[recipe.outputId] ?? 0) > 0 || (sellPrices[recipe.outputId] ?? 0) > 0;
      const isProfit = hasPrice && r.profitF > 0;
      const isHeart = v.kind === "heart";
      const transmute = transmuteHint(recipe);
      const heartInfo = heartHint(recipe);

      rows.push(
        <tr
          key={`${recipe.key}-${v.kind}`}
          onDoubleClick={() => addOrder(recipe, v, r)}
          className={`border-b border-zinc-900 hover:bg-zinc-800/40 cursor-pointer ${isProfit ? "" : "opacity-60"}`}
        >
          <td className="whitespace-nowrap px-2 py-1.5">
            <div className="flex items-center gap-2">
              <img src={iconUrl(recipe.outputId, 64)} alt="" width={28} height={28} className="rounded" />
              <span className={`font-medium ${isProfit ? "text-emerald-400" : "text-zinc-200"}`}>
                T{recipe.tier}.{recipe.enchant}
                {isHeart && <span className="ml-1 text-amber-400">♥</span>}
              </span>
            </div>
          </td>
          <td className="px-2 py-1.5">
            <div className="flex flex-nowrap items-center gap-1.5">
              {v.inputs.map((inp, i) => (
                <div key={i} className="flex items-center gap-1">
                  <span className="relative shrink-0">
                    <img src={iconUrl(inp.itemId, 32)} alt="" width={22} height={22} className="rounded" />
                    <span className="absolute bottom-0.5 right-0 rounded bg-zinc-900/85 px-0.5 text-[9px] leading-tight tabular-nums text-zinc-300">{inp.count}</span>
                    {inp.isHeart && <span className="absolute -top-1 -right-1 text-[10px] text-amber-400">♥</span>}
                  </span>
                </div>
              ))}
            </div>
          </td>
          <td className="px-2 py-1.5 text-right text-zinc-400 tabular-nums">
            {r.focusCost > 0 ? silver(r.focusCost) : "—"}
          </td>
          <td className="px-2 py-1.5 text-right tabular-nums">
            {hasPrice ? silver(r.sellPrice) : "—"}
          </td>
          <td className={`px-2 py-1.5 text-right font-semibold tabular-nums ${hasPrice ? (r.profitNoF >= 0 ? "text-emerald-400" : "text-red-400") : "text-zinc-600"}`}>
            {hasPrice ? silverShort(r.profitNoF) : "—"}
          </td>
          <td className={`px-2 py-1.5 text-right font-semibold tabular-nums ${hasPrice ? (r.profitF >= 0 ? "text-emerald-400" : "text-red-400") : "text-zinc-600"}`}>
            {hasPrice ? silverShort(r.profitF) : "—"}
          </td>
          <td className={`px-2 py-1.5 text-right tabular-nums ${hasPrice ? (r.spf >= 0 ? "text-amber-300" : "text-zinc-500") : "text-zinc-600"}`}>
            {hasPrice && r.spf !== 0 ? decimal(r.spf) : "—"}
          </td>
          <td className="px-2 py-1.5 text-right">
            {transmute && (
              <span className="text-[10px] text-sky-400" title={transmute}>⚡</span>
            )}
            {isHeart && heartInfo?.cheaper && (
              <span className="ml-1 text-[10px] text-amber-400" title={`♥ ${silver(heartInfo.heartProfit)} vs ${silver(heartInfo.normalProfit)}`}>♥</span>
            )}
          </td>
        </tr>,
      );
    }
  }

  return (
    <div>
      {/* Controles compartilhados */}
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div>
          <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-zinc-500">{t("refiningFamily")}</label>
          <select
            value={family}
            onChange={(e) => { setFamily(e.target.value as FamilyKey); }}
            className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-sm text-zinc-100 outline-none focus:border-amber-500"
          >
            {(Object.keys(FAMILIES) as FamilyKey[]).map((f) => (
              <option key={f} value={f}>{FAMILIES[f][langKey]}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-zinc-500">{t("qtyLabel")}</label>
          <input
            type="number"
            value={batchQty}
            onChange={(e) => setBatchQty(Math.max(1, +e.target.value))}
            className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-sm text-zinc-100 outline-none focus:border-amber-500"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-zinc-500">{t("craftLocationHeader")}</label>
          <select
            value={productionLocation.kind}
            onChange={(e) => {
              const kind = e.target.value as "city" | "island";
              setProductionLocation({ kind, city: craftCity ?? "Thetford" });
            }}
            className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-sm text-zinc-100 outline-none focus:border-amber-500"
          >
            <option value="city">🏛️ {t("placeCity")}</option>
            <option value="island">🏝️ {t("placeIsland")}</option>
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-zinc-500">{t("craftCityLabel")}</label>
          <select
            value={craftCity ?? "Thetford"}
            onChange={(e) => setProductionLocation({ kind: "city", city: e.target.value as ProductionCity })}
            className="w-full rounded-md border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-sm text-zinc-100 outline-none focus:border-amber-500"
          >
            {PRODUCTION_CITIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
      </div>

      {/* Mercados + bônus + fee */}
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div>
          <label className="mb-1 block text-xs text-zinc-500">{t("sellAtLabel")}</label>
          <select value={sellCity} onChange={(e) => setSellCity(e.target.value)} className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-100">
            {SELL_CITIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">{t("refiningMatCity")}</label>
          <select value={matCity} onChange={(e) => setMatCity(e.target.value)} className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-100">
            {SELL_CITIES.filter((c) => c !== "Black Market").map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div className="text-xs">
          {isSpecializedCity ? (
            <span className="text-emerald-400">✓ {bonusCity} +40%</span>
          ) : (
            <span className="text-zinc-500">{t("craftBonusNoBonus")} <span className="text-zinc-300">{bonusCity}</span></span>
          )}
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">{t("bonusLabel")}</label>
          <select value={eventBonus} onChange={(e) => setEventBonus(+e.target.value)} className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-100">
            <option value={0}>0%</option>
            <option value={0.1}>10%</option>
            <option value={0.2}>20%</option>
          </select>
        </div>
        <button
          onClick={() => setPremium((p) => !p)}
          className={`rounded-md border px-3 py-1.5 text-sm ${premium ? "border-amber-500 bg-amber-500/10 text-amber-300" : "border-zinc-700 bg-zinc-900 text-zinc-400"}`}
        >Premium</button>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">{t("feePerHundredLabel")}</label>
          <input type="number" value={stationFee} onChange={(e) => setStationFee(Math.max(0, +e.target.value))} className="w-24 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-100" />
        </div>
        <button
          onClick={fetchMarket}
          disabled={loadingPrices}
          className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm text-zinc-300 hover:border-zinc-600 disabled:opacity-50"
          title={fetchError ?? t("updatePricesTitle")}
        >
          {loadingPrices ? "⟳" : "↻"} {t("updatePricesTitle")}
        </button>
        {fetchError && <span className="text-xs text-red-400">⚠</span>}
        <div className="text-xs text-zinc-500">
          {t("retNoFocusAbbr")} <b className="text-zinc-200">{percent(rrNoFocus)}</b> · {t("retFocusAbbr")} <b className="text-zinc-200">{percent(rrFocus)}</b>
        </div>
        {minSpf > 0 && (
          <div className="text-xs text-zinc-500">
            {t("minSpfLabel")} <b className="text-zinc-300">{decimal(minSpf)}</b> {t("minSpfSuffix")}
          </div>
        )}
      </div>

      {/* Specs T4-T8 */}
      <div className="mb-4 rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">{t("refiningSpecs")}</h3>
        <div className="flex flex-wrap gap-3">
          {[4, 5, 6, 7, 8].map((t2) => (
            <div key={t2} className="flex items-center gap-1.5">
              <span className="text-xs text-zinc-500">T{t2}</span>
              <input
                type="number"
                min={0}
                max={100}
                value={specs[t2] ?? 0}
                onChange={(e) => setSpec(t2, Math.min(100, Math.max(0, +e.target.value)))}
                className="w-14 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-100"
              />
            </div>
          ))}
        </div>
        <p className="mt-2 text-xs text-zinc-500">{t("refiningSpecsHint")}</p>
      </div>

      {/* Tabela de refino — todas as receitas */}
      <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-900/40">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-800 text-left text-[11px] uppercase tracking-wide text-zinc-500">
              <th className="px-2 py-1.5">Item</th>
              <th className="px-2 py-1.5">{t("colMaterials")}</th>
              <th className="px-2 py-1.5 text-right">Focus</th>
              <th className="px-2 py-1.5 text-right">{t("colSellAvg")}</th>
              <th className="px-2 py-1.5 text-right">{t("colProfit")}</th>
              <th className="px-2 py-1.5 text-right">SPF</th>
              <th className="px-2 py-1.5 text-right">⚡</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
      <p className="mt-1 text-xs text-zinc-600">
        {t("cartEmptyHint")} <b>{t("cartEmptyHintBold")}</b> {t("cartEmptyHintSuffix")} · ⚡ = {t("transmutationTitle")}
      </p>
    </div>
  );
}
