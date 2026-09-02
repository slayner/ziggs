import { useEffect, useState } from "react";
import { silverShort } from "../../lib/format";
import { fetchRetry } from "../../api";
import { useServer } from "../../i18n";
import { itemUrl } from "./helpers";

const HISTORY_CITIES = ["Lymhurst", "Martlock", "Fort Sterling", "Bridgewatch", "Thetford"];
const LINE_COLORS = ["#38bdf8","#34d399","#fbbf24","#f87171","#a78bfa","#fb923c","#4ade80","#e879f9"];

type HistPt    = { avg_price: number; timestamp: string };
type HistEntry = { item_id: string; location: string; data: HistPt[] };
type ItemSeries = { id: string; name: string; pts: (number | null)[] };

async function fetchItemHistories(items: { id: string; name: string }[], server: string): Promise<{
  dates: string[];
  series: ItemSeries[];
}> {
  const ids = items.map(i => i.id);
  const url =
    `https://${server}.albion-online-data.com/api/v2/stats/history/${ids.join(",")}` +
    `?time-scale=24&qualities=1,2,3,4&locations=${HISTORY_CITIES.map(c => encodeURIComponent(c)).join(",")}`;
  const res = await fetchRetry(url);
  if (!res.ok) return { dates: [], series: [] };
  const entries: HistEntry[] = await res.json();

  // item_id -> date -> all (city × quality) prices
  const byItemDate = new Map<string, Map<string, number[]>>();
  for (const e of entries) {
    if (!HISTORY_CITIES.includes(e.location)) continue;
    if (!ids.includes(e.item_id)) continue;
    if (!byItemDate.has(e.item_id)) byItemDate.set(e.item_id, new Map());
    const dm = byItemDate.get(e.item_id)!;
    for (const pt of e.data) {
      if (!pt.avg_price) continue;
      const d = pt.timestamp.slice(0, 10);
      if (!dm.has(d)) dm.set(d, []);
      dm.get(d)!.push(pt.avg_price);
    }
  }

  const allDates = new Set<string>();
  for (const dm of byItemDate.values()) for (const d of dm.keys()) allDates.add(d);
  const dates = [...allDates].sort().slice(-28);

  const series: ItemSeries[] = items.map(({ id, name }) => {
    const dm = byItemDate.get(id);
    return {
      id, name,
      pts: dates.map(d => {
        const ps = dm?.get(d);
        return ps?.length ? Math.round(ps.reduce((a, b) => a + b, 0) / ps.length) : null;
      }),
    };
  });

  return { dates, series };
}

export function PriceHistoryChart({ items, potionQty, foodQty, onTotal, focusedId, onFocus }: {
  items: { id: string; name: string; slot: string }[];
  potionQty: number;
  foodQty: number;
  onTotal?: (v: number) => void;
  focusedId?: string;
  onFocus?: (id: string) => void;
}) {
  const { server } = useServer();
  const [data, setData] = useState<{ dates: string[]; series: ItemSeries[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [internalFocus, setInternalFocus] = useState("");
  const key = items.map(i => i.id).join(",") + server;

  useEffect(() => {
    if (!items.length) { setData(null); return; }
    setLoading(true);
    fetchItemHistories(items, server).then(setData).catch(() => setData(null)).finally(() => setLoading(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  // report build total to parent whenever data/qty changes
  useEffect(() => {
    if (!data || !onTotal) return;
    const avg = (pts: (number | null)[]) => {
      const v = pts.filter((p): p is number => p !== null);
      return v.length ? Math.round(v.reduce((a, b) => a + b, 0) / v.length) : 0;
    };
    const total = data.series.reduce((sum, s) => {
      const it = items.find(i => i.id === s.id);
      const q = !it ? 1 : it.slot === "potion" ? potionQty : it.slot === "food" ? foodQty : 1;
      return sum + avg(s.pts.map(p => p !== null ? p * q : null));
    }, 0);
    onTotal(total);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, potionQty, foodQty]);

  if (loading) {
    const VW = 320, H = 160, padL = 20, padR = 52, padTop = 8, padBot = 22;
    const plotW = VW - padL - padR, plotH = H - padTop - padBot;
    const baseY = padTop + plotH;
    const n = 28;
    const skCx = (i: number) => padL + (i / (n - 1)) * plotW;
    return (
      <div style={{ flex: 1, minWidth: 0 }}>
        <svg viewBox={`0 0 ${VW} ${H}`} width="100%" style={{ display: "block", overflow: "visible" }}>
          {Array.from({ length: n }, (_, i) => (
            <line key={i} x1={skCx(i)} x2={skCx(i)} y1={padTop} y2={baseY}
              stroke="var(--border)" strokeWidth="0.3" opacity="0.5" />
          ))}
          {[1, 2, 3, 4].map(w => {
            const wx = skCx(Math.max(0, n - 1 - w * 7));
            return (
              <g key={w}>
                <line x1={wx} x2={wx} y1={padTop} y2={baseY}
                  stroke="var(--hint)" strokeWidth="0.8" strokeDasharray="2 2" opacity="0.7" />
                <text x={wx} y={baseY + 14} textAnchor="middle" fontSize="9" fill="var(--muted)">{w}w</text>
              </g>
            );
          })}
        </svg>
      </div>
    );
  }
  if (!data || data.dates.length < 2) return null;

  const { dates, series } = data;
  const active = series.filter(s => s.pts.some(p => p !== null));
  if (!active.length) return null;

  const slotQty = (slot: string) => slot === "potion" ? potionQty : slot === "food" ? foodQty : 1;
  const scaled = active.map(s => {
    const item = items.find(i => i.id === s.id);
    const q = item ? slotQty(item.slot) : 1;
    return { ...s, pts: s.pts.map(p => p !== null ? p * q : null) };
  });

  const itemAvg = (pts: (number | null)[]) => {
    const valid = pts.filter((p): p is number => p !== null);
    return valid.length ? Math.round(valid.reduce((a, b) => a + b, 0) / valid.length) : 0;
  };

  // Normalize each series to % deviation from its own mean so all lines share one axis
  const withNorm = scaled.map(s => {
    const vals = s.pts.filter((p): p is number => p !== null);
    const mean = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 1;
    return { ...s, normPts: s.pts.map(p => p !== null ? ((p - mean) / mean) * 100 : null) };
  });

  const allNorm = withNorm.flatMap(s => s.normPts.filter((p): p is number => p !== null));
  const nLo = allNorm.length ? Math.min(...allNorm) : -10;
  const nHi = allNorm.length ? Math.max(...allNorm) : 10;
  const nPad = Math.max((nHi - nLo) * 0.15, 3);
  const yLo = nLo - nPad, yHi = nHi + nPad, yRange = yHi - yLo;

  const H = 160;
  const padL = 20, padR = 52, padTop = 8, padBot = 22;
  const plotH = H - padTop - padBot;
  const baseY = padTop + plotH;
  const VW = 320;
  const plotW = VW - padL - padR;
  const n = dates.length;

  const cx = (i: number) => padL + (n > 1 ? (i / (n - 1)) * plotW : plotW / 2);
  const cy = (v: number) => padTop + (1 - (v - yLo) / yRange) * plotH;

  const lastDate = dates[n - 1];
  const weekX = (w: number) => {
    const t = new Date(lastDate + "T00:00:00Z");
    t.setUTCDate(t.getUTCDate() - w * 7);
    const tStr = t.toISOString().slice(0, 10);
    let best = 0;
    for (let i = 0; i < n; i++) { if (dates[i] <= tStr) best = i; }
    return cx(best);
  };

  const buildPath = (pts: (number | null)[]) => {
    let d = "";
    for (let i = 0; i < pts.length; i++) {
      const v = pts[i];
      if (v === null) continue;
      d += `${(d === "" || pts[i - 1] === null) ? "M" : "L"}${cx(i).toFixed(1)},${cy(v).toFixed(1)} `;
    }
    return d.trim();
  };

  const zeroY = cy(0);
  const activeFocus = focusedId !== undefined ? focusedId : (internalFocus || items.find(i => i.slot === "weapon")?.id || items[0]?.id || "");

  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <svg viewBox={`0 0 ${VW} ${H}`} width="100%" style={{ display: "block", overflow: "visible" }}>
        {/* vertical grid */}
        {dates.map((_, i) => (
          <line key={`vg${i}`} x1={cx(i)} x2={cx(i)} y1={padTop} y2={baseY}
            stroke="var(--border)" strokeWidth="0.3" opacity="0.5" />
        ))}
        {/* zero baseline */}
        <line x1={padL} x2={padL + plotW} y1={zeroY} y2={zeroY}
          stroke="var(--border)" strokeWidth="0.8" opacity="0.9" />
        {/* week markers */}
        {[1, 2, 3, 4].map(w => {
          const wx = weekX(w);
          return (
            <g key={w}>
              <line x1={wx} x2={wx} y1={padTop} y2={baseY}
                stroke="var(--hint)" strokeWidth="0.8" strokeDasharray="2 2" opacity="0.7" />
              <text x={wx} y={baseY + 14} textAnchor="middle" fontSize="9" fill="var(--muted)">{w}w</text>
            </g>
          );
        })}
        {/* dim lines first, focused on top */}
        {[false, true].map(pass =>
          withNorm.map((s, si) => {
            const isFocused = s.id === activeFocus;
            if (isFocused !== pass) return null;
            const color = LINE_COLORS[si % LINE_COLORS.length];
            const d = buildPath(s.normPts);
            if (!d) return null;
            const item = items.find(i => i.id === s.id);
            if (!item) return null;
            const q = slotQty(item.slot);
            const firstI = s.normPts.findIndex(p => p !== null);
            const firstV = firstI >= 0 ? s.normPts[firstI]! : null;
            const avg = itemAvg(s.pts);
            const lastNormV = [...s.normPts].reverse().find(p => p !== null) ?? 0;
            const opacity = isFocused ? 0.85 : 0.22;
            const sw = isFocused ? 1.5 : 1;
            return (
              <g key={s.id} opacity={opacity} style={{ cursor: "pointer" }}
                onClick={() => { onFocus?.(s.id); if (focusedId === undefined) setInternalFocus(s.id); }}>
                {isFocused && firstV !== null && (
                  <image href={itemUrl(s.id)} x={2} y={cy(firstV) - 8} width={16} height={16} />
                )}
                <path d={d} fill="none" stroke={color} strokeWidth={sw} strokeLinejoin="round" />
                {s.normPts.map((v, i) => v !== null ? (
                  <circle key={i} cx={cx(i)} cy={cy(v)} r="1.8" fill={color}>
                    <title>{s.name}{q > 1 ? ` ×${q}` : ""} · {dates[i].slice(5)}: {silverShort(s.pts[i]!)} ({v >= 0 ? "+" : ""}{v.toFixed(1)}%)</title>
                  </circle>
                ) : null)}
                {isFocused && avg > 0 && (
                  <text x={padL + plotW + 4} y={cy(lastNormV) + 4} fontSize="10" fill={color} fontWeight="700">
                    {silverShort(avg)}
                  </text>
                )}
              </g>
            );
          })
        )}
      </svg>
    </div>
  );
}
