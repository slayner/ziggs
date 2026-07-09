/**
 * Albion Online Data Project (ADP) price source — browser-side client.
 * Calls the ADP API directly (CORS-enabled). No User-Agent header is sent
 * since browsers block setting it from JS.
 */
import type { PriceQuote, PriceServer } from "./types";

const BASE: Record<PriceServer, string> = {
  west: "https://west.albion-online-data.com",
  east: "https://east.albion-online-data.com",
  europe: "https://europe.albion-online-data.com",
};

export function toMarketId(catalogId: string): string {
  const m = catalogId.match(/_LEVEL(\d)$/);
  return m ? `${catalogId}@${m[1]}` : catalogId;
}
export function fromMarketId(marketId: string): string {
  const m = marketId.match(/^(.*_LEVEL\d)@\d$/);
  return m ? m[1] : marketId;
}

function parseDate(s: string | undefined): number {
  if (!s || s.startsWith("0001")) return 0;
  const t = Date.parse(s.endsWith("Z") ? s : `${s}Z`);
  return Number.isNaN(t) ? 0 : t;
}

const chunk = <T>(arr: T[], n: number): T[][] => {
  const out: T[][] = [];
  for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n));
  return out;
};

interface AdpPriceRow {
  item_id: string;
  city: string;
  quality: number;
  sell_price_min: number;
  sell_price_min_date: string;
  buy_price_max: number;
  buy_price_max_date: string;
}

export async function fetchAdpPrices(
  server: PriceServer,
  catalogItemIds: string[],
  locations: string[],
  qualities: number[],
): Promise<PriceQuote[]> {
  const base = BASE[server];
  const loc = encodeURIComponent(locations.join(","));
  const qual = encodeURIComponent(qualities.join(","));
  const out: PriceQuote[] = [];

  for (const ids of chunk(catalogItemIds.map(toMarketId), 100)) {
    const url = `${base}/api/v2/stats/prices/${ids.join(",")}.json?locations=${loc}&qualities=${qual}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`ADP prices ${res.status}`);
    const rows = (await res.json()) as AdpPriceRow[];
    for (const r of rows) {
      out.push({
        itemId: fromMarketId(r.item_id),
        city: r.city,
        quality: r.quality,
        sellMin: r.sell_price_min || 0,
        buyMax: r.buy_price_max || 0,
        updatedAt: Math.max(parseDate(r.sell_price_min_date), parseDate(r.buy_price_max_date)),
        source: "adp",
      });
    }
  }
  return out;
}

interface AdpHistoryRow {
  location: string;
  item_id: string;
  data: { item_count: number; avg_price: number; timestamp: string }[];
}

export async function fetchAdpDemand(
  server: PriceServer,
  catalogItemIds: string[],
  locations: string[],
  qualities: number[],
  days = 7,
): Promise<Record<string, number>> {
  const base = BASE[server];
  const loc = encodeURIComponent(locations.join(","));
  const qual = encodeURIComponent(qualities.join(","));
  const totals: Record<string, number> = {};

  for (const ids of chunk(catalogItemIds.map(toMarketId), 100)) {
    const url = `${base}/api/v2/stats/history/${ids.join(",")}.json?locations=${loc}&time-scale=24&qualities=${qual}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`ADP history ${res.status}`);
    const rows = (await res.json()) as AdpHistoryRow[];
    for (const r of rows) {
      const recent = r.data.slice(-days);
      const avg = recent.length ? recent.reduce((s, d) => s + d.item_count, 0) / recent.length : 0;
      const id = fromMarketId(r.item_id);
      totals[id] = (totals[id] ?? 0) + avg;
    }
  }
  for (const k of Object.keys(totals)) totals[k] = Math.round(totals[k]);
  return totals;
}

export async function fetchAdpGold(server: PriceServer): Promise<number> {
  const base = BASE[server];
  const res = await fetch(`${base}/api/v2/stats/gold?count=1`);
  if (!res.ok) throw new Error(`ADP gold ${res.status}`);
  const rows = (await res.json()) as { price: number }[];
  return rows[0]?.price ?? 0;
}

export async function fetchAdpPriceSeries(
  server: PriceServer,
  catalogItemId: string,
  location: string,
  qualities: number[],
  days = 28,
): Promise<{ series: { t: string; price: number }[]; avg: number }> {
  const base = BASE[server];
  const url = `${base}/api/v2/stats/history/${toMarketId(catalogItemId)}.json?locations=${encodeURIComponent(location)}&time-scale=24&qualities=${encodeURIComponent(qualities.join(","))}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`ADP history ${res.status}`);
  const rows = (await res.json()) as AdpHistoryRow[];

  const byDay = new Map<string, { sum: number; count: number }>();
  for (const r of rows) {
    for (const d of r.data) {
      const cur = byDay.get(d.timestamp) ?? { sum: 0, count: 0 };
      cur.sum += d.avg_price * d.item_count;
      cur.count += d.item_count;
      byDay.set(d.timestamp, cur);
    }
  }
  const series = [...byDay.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .slice(-days)
    .map(([t, v]) => ({ t, price: v.count ? Math.round(v.sum / v.count) : 0 }))
    .filter((p) => p.price > 0);
  const avg = series.length ? Math.round(series.reduce((s, p) => s + p.price, 0) / series.length) : 0;
  return { series, avg };
}
