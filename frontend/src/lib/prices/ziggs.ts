/**
 * Ziggs backend price source — preços capturados pelo companion (packet capture).
 * Disputa com ADP pelo mais fresco (resolveFreshest). Requer sessão do site.
 *
 * O frontend manda UniqueNames (catalogIds) direto. O backend converte pra
 * game_name (formato do DB) internamente. O PriceQuote.itemId volta como
 * game_name — o CraftCalculator usa o game_name pra buscar no Map de quotes.
 */
import type { PriceQuote } from "./types";

const API = "";  // caminho relativo — mesma origem em prod

interface ZiggsPriceRow {
  item_id: string;
  city: string;
  quality: number;
  sell_price_min: number;
  price_date: string | null;
}

function parseDate(s: string | null): number {
  if (!s || s.startsWith("0001")) return 0;
  const t = Date.parse(s);
  return Number.isNaN(t) ? 0 : t;
}

export async function fetchZiggsPrices(catalogItemIds: string[]): Promise<PriceQuote[]> {
  if (!catalogItemIds.length) return [];
  const out: PriceQuote[] = [];
  for (let i = 0; i < catalogItemIds.length; i += 200) {
    const chunk = catalogItemIds.slice(i, i + 200);
    const url = `${API}/companion/price-quotes?items=${encodeURIComponent(chunk.join(","))}`;
    const res = await fetch(url, { credentials: "include" });
    if (!res.ok) {
      console.warn(`[ziggs] price-quotes HTTP ${res.status} for ${chunk.length} items`);
      continue;
    }
    const data = (await res.json()) as { prices: ZiggsPriceRow[] };
    for (const r of data.prices) {
      const updatedAt = parseDate(r.price_date);
      if (!updatedAt || !r.sell_price_min) continue;
      out.push({
        itemId: r.item_id,  // game_name (formato do DB)
        city: r.city,
        quality: r.quality,
        sellMin: r.sell_price_min,
        buyMax: 0,
        updatedAt,
        source: "local",
      });
    }
  }
  return out;
}