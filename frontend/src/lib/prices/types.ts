/** Shared price-layer types. Designed so multiple sources can coexist. */

export type PriceServer = "west" | "east" | "europe";

/** A normalized market quote for one item in one city at one quality. */
export interface PriceQuote {
  /** Catalog item id (e.g. "T4_METALBAR" or "T4_2H_AXE@1"). */
  itemId: string;
  city: string;
  quality: number;
  /** Lowest sell-order price (instant buy cost). 0 = no data. */
  sellMin: number;
  /** Highest buy-order price (instant sell income). 0 = no data. */
  buyMax: number;
  /** Epoch ms of the freshest underlying datapoint; 0 when no data. */
  updatedAt: number;
  /** Where this quote came from: "adp" today, "local" (own DB) in the future. */
  source: string;
}

/** Recent demand for an item in a city (avg units sold per day). */
export interface DemandInfo {
  itemId: string;
  city: string;
  /** Average daily units sold over the sampled window. */
  avgDailySold: number;
}

/** Key identifying a unique quote slot. */
export const quoteKey = (itemId: string, city: string, quality: number) =>
  `${itemId}|${city}|${quality}`;

/**
 * Merge quotes from several sources, keeping the freshest per item/city/quality.
 * This is the hook for the future: a local (in-game scraped) DB can be passed
 * first and ADP second — the newer `updatedAt` wins, so ADP is used only as a
 * backup when our own price is staler.
 */
export function resolveFreshest(...lists: PriceQuote[][]): PriceQuote[] {
  const best = new Map<string, PriceQuote>();
  for (const list of lists) {
    for (const q of list) {
      const k = quoteKey(q.itemId, q.city, q.quality);
      const cur = best.get(k);
      if (!cur || q.updatedAt > cur.updatedAt) best.set(k, q);
    }
  }
  return [...best.values()];
}
