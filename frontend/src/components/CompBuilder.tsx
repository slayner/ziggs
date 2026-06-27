import { Fragment, useEffect, useRef, useState, type CSSProperties } from "react";
import { silverShort } from "../lib/format";
import {
  api, fetchRetry, imgRetry, type ApiComp, type ApiRole, type GameRoleDetail,
  type RegearItem, type WeaponOut, type WeaponSpell, type Permissions,
} from "../api";
import { ItemPicker } from "./ItemPicker";
import { RENDER_URL, ITEM_BY_ID, itemRenderUrl, type ItemSlot } from "../data/albion-items";
import { MOCK_API_COMP } from "../mock";
import { useServer } from "../i18n";

// ── Types ────────────────────────────────────────────────────
type FnTypeDef  = { key: string; label: string; color: string; emoji?: string };
export type EquipItem  = { id: string; name: string };
export type DraftEquip = {
  weapon?: EquipItem; offhand?: EquipItem; helmet?: EquipItem;
  armor?: EquipItem;  boots?: EquipItem;  cape?: EquipItem; food?: EquipItem;
  potion?: EquipItem;
  offhand_alt?: EquipItem[]; helmet_alt?: EquipItem[]; armor_alt?: EquipItem[];
  boots_alt?: EquipItem[];   cape_alt?: EquipItem[];
};
type DraftRole = {
  catalog_id:    number | null;
  name:          string;
  fn:            string | null;
  color:         string | null;
  weapon_db_id:  number | null;
  equip:         DraftEquip;
  equip_loaded:  boolean;
  play_style:    string | null;
  abilities:     string | null;
  obs:           string | null;
  q_spell:       string | null;
  w_spell:       string | null;
  passive_spell: string | null;
  gear_spells:   Record<string, string | null>;
  potion_qty:    number;
  food_qty:      number;
  flex_of?:      string;
};
type DraftSlot  = { fn: string | null; roles: DraftRole[] };
type DraftParty = { name: string; slots: DraftSlot[] };
type Draft      = { id: number; name: string; parties: DraftParty[] };

// ── Constants ────────────────────────────────────────────────
const MAX_SLOTS = 20;

const DEFAULT_FN_TYPES: FnTypeDef[] = [
  { key: "tank",    label: "Tank",    color: "#1399FF", emoji: "🛡️" },
  { key: "healer",  label: "Healer",  color: "#43B80E", emoji: "🕊️" },
  { key: "support", label: "Suporte", color: "#FFE04D", emoji: "✨" },
  { key: "dps",     label: "DPS",     color: "#FF2025", emoji: "🏹" },
  { key: "pierce",  label: "Pierce",  color: "#06b6d4", emoji: "🎯" },
];


function getFnDef(fn: string | null, types: FnTypeDef[]): FnTypeDef | undefined {
  if (!fn) return undefined;
  return types.find(t => t.key === fn);
}
function fnLabel(ft: FnTypeDef): string {
  return ft.emoji ? `${ft.emoji} ${ft.label}` : ft.label;
}

const EQUIP_SLOTS = [
  { key: "weapon",  label: "Arma" },
  { key: "offhand", label: "Off-hand" },
  { key: "helmet",  label: "Capacete" },
  { key: "armor",   label: "Armadura" },
  { key: "boots",   label: "Botas" },
  { key: "cape",    label: "Capa" },
  { key: "food",    label: "Comida" },
  { key: "potion",  label: "Poção" },
] as { key: ItemSlot; label: string }[];

const ALT_CAPABLE = new Set(["offhand", "helmet", "armor", "boots", "cape"]);

function safeAltArr(v: unknown): EquipItem[] {
  if (!v) return [];
  if (Array.isArray(v)) return v.filter((x): x is EquipItem => !!(x?.id));
  if (typeof v === "object" && (v as EquipItem).id) return [v as EquipItem];
  return [];
}

const SPELL_COLORS: Record<string, string> = {
  Q: "#5b8def", W: "#a26dbf", passive: "#d4a832", active: "#5b8def",
};

export function is2H(weaponId: string | undefined): boolean {
  if (!weaponId) return false;
  return wBase(weaponId).startsWith("2H_");
}

function itemUrl(id: string, quality = 0): string {
  const item = ITEM_BY_ID.get(id);
  return item ? itemRenderUrl(item, quality) : RENDER_URL(id, quality);
}

// ── Helpers ──────────────────────────────────────────────────
function wBase(id: string) { return id.replace(/^T\d+_/, "").replace(/@\d+$/, ""); }

// ── Conversions ──────────────────────────────────────────────
function buildItemsToEquip(items: RegearItem[]): DraftEquip {
  const eq: DraftEquip = {};
  const rec = eq as Record<string, unknown>;
  for (const bi of items ?? []) {
    const m = bi.slot.match(/^(.+)_alt_(\d+)$/);
    if (m) {
      const key = `${m[1]}_alt`;
      if (!rec[key]) rec[key] = [];
      (rec[key] as EquipItem[])[parseInt(m[2])] = { id: bi.item_id, name: bi.name };
    } else {
      (rec as Record<string, EquipItem>)[bi.slot] = { id: bi.item_id, name: bi.name };
    }
  }
  return eq;
}

function apiRoleToDraft(r: ApiRole): DraftRole {
  const hasItems = (r.build_items?.length ?? 0) > 0;
  const eq: DraftEquip = hasItems
    ? buildItemsToEquip(r.build_items!)
    : (() => {
        const q: DraftEquip = {};
        for (const f of ["offhand", "helmet", "armor", "boots", "cape", "food"] as const) {
          const v = r[f as keyof ApiRole] as string | null;
          if (v) (q as Record<string, EquipItem>)[f] = { id: "", name: v };
        }
        return q;
      })();
  return {
    catalog_id: r.id, name: r.name, fn: r.invisible_function,
    color: r.color ?? null, weapon_db_id: r.weapon_id ?? null,
    equip: eq, equip_loaded: hasItems,
    play_style: r.play_style ?? null, abilities: r.abilities ?? null, obs: r.obs ?? null,
    q_spell: r.q_spell ?? null, w_spell: r.w_spell ?? null, passive_spell: r.passive_spell ?? null,
    gear_spells: r.gear_spells ?? {},
    potion_qty: r.build_items?.find(bi => bi.slot === "potion")?.quantity ?? 10,
    food_qty:   r.build_items?.find(bi => bi.slot === "food")?.quantity ?? 1,
  };
}

function compToDraft(c: ApiComp): Draft {
  return {
    id: c.id, name: c.name,
    parties: c.parties.map(p => ({
      name: p.name ?? "",
      slots: p.slots.map(s => ({
        fn: s.fn ?? null,
        roles: s.roles.map(apiRoleToDraft),
      })),
    })),
  };
}

function emptyRole(): DraftRole {
  return {
    catalog_id: null, name: "", fn: null, color: null, weapon_db_id: null,
    equip: { potion: { id: "T7_POTION_REVIVE", name: "7.0 Gigantify" } }, equip_loaded: true,
    play_style: null, abilities: null, obs: null,
    q_spell: null, w_spell: null, passive_spell: null,
    gear_spells: {},
    potion_qty: 10,
    food_qty: 1,
  };
}

function roleToPayload(role: DraftRole) {
  const build_items: RegearItem[] = [];
  for (const s of ["weapon", "offhand", "helmet", "armor", "boots", "cape"] as const) {
    const eq = role.equip as Record<string, unknown>;
    const item = eq[s] as EquipItem | undefined;
    if (item?.id) build_items.push({ slot: s, item_id: item.id, name: item.name, quality: 1, quantity: 1 });
    if (ALT_CAPABLE.has(s)) {
      const alts = (eq[`${s}_alt`] as EquipItem[] | undefined) ?? [];
      alts.forEach((a, i) => { if (a?.id) build_items.push({ slot: `${s}_alt_${i}`, item_id: a.id, name: a.name, quality: 1, quantity: 1 }); });
    }
  }
  if (role.equip.food?.id)
    build_items.push({ slot: "food", item_id: role.equip.food.id, name: role.equip.food.name, quality: 1, quantity: role.food_qty });
  if (role.equip.potion?.id)
    build_items.push({ slot: "potion", item_id: role.equip.potion.id, name: role.equip.potion.name, quality: 1, quantity: role.potion_qty });
  return {
    build_items, weapon_id: role.weapon_db_id,
    invisible_function: role.fn,
    play_style: role.play_style, abilities: role.abilities, obs: role.obs,
    color: role.color,
    q_spell: role.q_spell, w_spell: role.w_spell, passive_spell: role.passive_spell,
    gear_spells: Object.keys(role.gear_spells).length ? role.gear_spells : undefined,
  };
}

// ── SpellPicker ──────────────────────────────────────────────
function SpellPicker({
  spells, slot, selected, onChange, readonly = false,
}: {
  spells: WeaponSpell[];
  slot: string;
  selected: string | null;
  onChange?: (id: string | null) => void;
  readonly?: boolean;
}) {
  const options = spells.filter(s => s.slot === slot);
  if (!options.length) return null;
  const color = SPELL_COLORS[slot] ?? "var(--info)";
  return (
    <div className="rc-spell-row">
      <span className="rc-spell-label">{slot === "passive" ? "P" : slot === "active" ? "A" : slot}</span>
      {options.map(spell => {
        const sel = selected === spell.spell_id;
        const spriteSrc = `https://render.albiononline.com/v1/spell/${spell.uisprite ?? spell.spell_id}.png`;
        return (
          <button key={spell.spell_id} title={spell.name}
            disabled={readonly}
            className={`spell-dot${sel ? " sd-sel" : ""}${readonly ? " sd-readonly" : ""}`}
            style={{ "--spell-color": color } as CSSProperties}
            onClick={e => { e.stopPropagation(); onChange?.(sel ? null : spell.spell_id); }}>
            <img src={spriteSrc} alt={spell.name} style={{ width: "100%", height: "100%", objectFit: "contain" }}
              onError={imgRetry(img => { img.style.display = "none"; })} />
          </button>
        );
      })}
    </div>
  );
}

// ── EquipStrip ───────────────────────────────────────────────
// Arma | Offhand | Capacete+Armadura+Bota | Capa+Comida  (inline para caber em rc-head)
function EquipStrip({ equip, weaponIs2H }: { equip: DraftEquip; weaponIs2H?: boolean }) {
  const hasWeapon = !!(equip.weapon?.id);
  const hasOffhand = !!(equip.offhand?.id);
  const bodyItems = [equip.helmet, equip.armor, equip.boots].filter((i): i is EquipItem => !!(i?.id));
  const utilItems = [equip.cape, equip.food].filter((i): i is EquipItem => !!(i?.id));
  const hasAny = hasWeapon || hasOffhand || bodyItems.length || utilItems.length;
  if (!hasAny) return null;

  const icon = (item: EquipItem, key: number) => (
    <img key={key} className="rc-strip-icon"
      src={itemUrl(item.id)} alt="" title={item.name}
      onError={imgRetry(img => { img.style.opacity = "0.15"; })} />
  );

  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 2, flexShrink: 0 }}>
      {/* 2H: espaço antes da arma; 1H: arma depois do offhand */}
      {weaponIs2H
        ? <><div style={{ width: 22, height: 22, flexShrink: 0 }} />{hasWeapon && icon(equip.weapon!, 0)}</>
        : <>{hasWeapon && icon(equip.weapon!, 0)}{hasOffhand && icon(equip.offhand!, 1)}</>}
      {bodyItems.length > 0 && <><span className="rc-strip-sep" />{bodyItems.map(icon)}</>}
      {utilItems.length > 0 && <><span className="rc-strip-sep" />{utilItems.map(icon)}</>}
    </div>
  );
}

// ── EquipGrid ────────────────────────────────────────────────
// ── Price history chart ──────────────────────────────────────
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

function RoleViewBlock({ r, spells, onTotal, label }: {
  r: DraftRole & { equip_loaded: true };
  spells?: WeaponSpell[];
  onTotal?: (v: number) => void;
  label?: string;
}) {
  const [focusedId, setFocusedId] = useState<string | undefined>(undefined);
  const [localTotal, setLocalTotal] = useState(0);
  const [swapMap, setSwapMap] = useState<Record<string, number>>({});
  const hasEquip = EQUIP_SLOTS.some(s => r.equip[s.key]);
  const isFlex = !onTotal;
  const handleTotal = onTotal ?? setLocalTotal;

  const eq = r.equip as Record<string, unknown>;
  const totalAlts = (["offhand","helmet","armor","boots","cape"] as const)
    .reduce((n, s) => n + safeAltArr(eq[`${s}_alt`]).length, 0);

  function handleSwap(slot: string, altIdx: number) {
    setSwapMap(prev => {
      const n = { ...prev };
      if (n[slot] === altIdx) { delete n[slot]; } else { n[slot] = altIdx; }
      return n;
    });
    setFocusedId(undefined);
  }

  // Build display equip with swapped alts in primary slots
  const altMap: Record<string, number> = {};
  const displayEquip: DraftEquip = { ...r.equip };
  for (const s of ["offhand", "helmet", "armor", "boots", "cape"] as const) {
    const alts = safeAltArr(eq[`${s}_alt`]);
    const idx = swapMap[s];
    if (idx !== undefined && alts[idx]) {
      (displayEquip as Record<string, unknown>)[s] = alts[idx];
      altMap[s] = idx;
    }
  }

  const chartItems = EQUIP_SLOTS
    .map(s => ({ id: displayEquip[s.key]?.id ?? "", name: displayEquip[s.key]?.name ?? "", slot: s.key }))
    .filter(i => i.id);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 8, flexShrink: 0, width: 212 }}>
          {hasEquip && (
            <EquipGrid equip={displayEquip}
              weaponIs2H={is2H(displayEquip.weapon?.id)}
              weaponSpells={spells}
              selectedQ={r.q_spell}
              selectedW={r.w_spell}
              selectedPassive={r.passive_spell}
              potionQty={r.potion_qty}
              foodQty={r.food_qty}
              gearSpells={r.gear_spells}
              altMap={altMap}
              onFocus={setFocusedId} />
          )}
          {r.play_style && (
            <div className="sd-section">
              <h4>Modo de jogo</h4>
              <p>{r.play_style}</p>
            </div>
          )}
          {r.obs && (
            <div className="sd-section">
              <h4>Observações</h4>
              <p>{r.obs}</p>
            </div>
          )}
          {totalAlts > 0 && totalAlts < 5 && (
            <p style={{ fontSize: 10, fontWeight: 700, color: "var(--hint)", textTransform: "uppercase", letterSpacing: ".08em", margin: 0 }}>
              Equipamentos Alternativos
            </p>
          )}
        </div>
        {hasEquip && (
          <div style={{ flex: 1, minWidth: 0, position: "relative", aspectRatio: "2 / 1" }}>
            {isFlex && (label || localTotal > 0) && (
              <div style={{ position: "absolute", top: 2, right: 0, zIndex: 1, textAlign: "right", lineHeight: 1.3 }}>
                {label && <div style={{ fontSize: 10, color: "var(--hint)" }}>{label}</div>}
                {localTotal > 0 && <div style={{ fontSize: 11, color: "var(--muted)" }}>{silverShort(localTotal)}</div>}
              </div>
            )}
            <PriceHistoryChart
              items={chartItems}
              potionQty={r.potion_qty ?? 10}
              foodQty={r.food_qty ?? 1}
              onTotal={handleTotal}
              focusedId={focusedId}
              onFocus={setFocusedId} />
          </div>
        )}
      </div>
      <AltEquipSection equip={r.equip} gearSpells={r.gear_spells}
        swapMap={swapMap} onSwap={handleSwap} titleInSidebar={totalAlts < 5} />
    </div>
  );
}

const GRID_LAYOUT: (ItemSlot | null)[][] = [
  [null,     "helmet", "cape"   ],
  ["weapon", "armor",  "offhand"],
  ["potion", "boots",  "food"   ],
];

function SpellOverlay({ ids, centered, row }: { ids: (string | null)[]; centered?: boolean; row?: boolean }) {
  const present = ids.filter(Boolean);
  if (!present.length) return null;
  return (
    <div className={"rc-equip-spell-overlay" + (centered ? " rc-equip-spell-overlay-center" : "") + (row ? " rc-equip-spell-overlay-row" : "")}>
      {present.map(id => (
        <div key={id} className="rc-equip-spell-dot">
          <img src={`https://render.albiononline.com/v1/spell/${id}.png`} alt=""
            onError={imgRetry(img => { img.style.opacity = "0.2"; })} />
        </div>
      ))}
    </div>
  );
}

export function EquipGrid({
  equip, weaponIs2H, weaponSpells, selectedQ, selectedW, selectedPassive, potionQty, foodQty, gearSpells,
  altMap, onFocus, gearQuality,
}: {
  equip: DraftEquip;
  weaponIs2H: boolean;
  weaponSpells?: WeaponSpell[];
  selectedQ?: string | null;
  selectedW?: string | null;
  selectedPassive?: string | null;
  potionQty?: number;
  foodQty?: number;
  gearSpells?: Record<string, string | null>;
  altMap?: Record<string, number>;
  onFocus?: (id: string) => void;
  // pedido explícito (batalhas): qualidade real do item equipado naquele kill —
  // comps não têm isso (sempre quality 1 fixo), então fica opcional e sem
  // efeito quando omitido.
  gearQuality?: Record<string, number>;
}) {
  const weaponSpellIds = [
    selectedQ ? weaponSpells?.find(s => s.slot === "Q" && s.spell_id === selectedQ)?.uisprite ?? selectedQ : null,
    selectedW ? weaponSpells?.find(s => s.slot === "W" && s.spell_id === selectedW)?.uisprite ?? selectedW : null,
    selectedPassive ? weaponSpells?.find(s => s.slot === "passive" && s.spell_id === selectedPassive)?.uisprite ?? selectedPassive : null,
  ];
  return (
    <div className="rc-equip-grid">
      {GRID_LAYOUT.map((row, ri) => (
        <div key={ri} className="rc-equip-grid-row">
          {row.map((key, ci) => {
            if (!key) return <div key={ci} className="rc-equip-cell rc-equip-empty" />;
            if (key === "offhand" && weaponIs2H) {
              // Arma de duas mãos: igual o jogo, mostra a própria arma esmaecida
              // no slot de offhand — sem as skills (essas já aparecem no slot da arma).
              const weaponItem = equip.weapon;
              return (
                <div key={ci} className="rc-equip-cell rc-equip-2h" title="Arma de duas mãos">
                  {weaponItem?.id && <img src={itemUrl(weaponItem.id, gearQuality?.weapon ?? 0)} alt={weaponItem.name} onError={imgRetry()} />}
                </div>
              );
            }
            const item = equip[key];
            if (!item?.id) return <div key={ci} className="rc-equip-cell rc-equip-empty" />;
            const qty = key === "potion" ? (potionQty ?? 10) : key === "food" ? (foodQty ?? 1) : null;
            const altIdx = altMap?.[key];
            const gearIds = (key === "helmet" || key === "armor" || key === "boots") && gearSpells
              ? altIdx !== undefined
                ? Object.entries(gearSpells).filter(([k]) => k.startsWith(`${key}_alt_${altIdx}_`)).map(([, v]) => v)
                : Object.entries(gearSpells).filter(([k]) => k.startsWith(`${key}_`) && !k.startsWith(`${key}_alt_`)).map(([, v]) => v)
              : null;
            return (
              <div key={ci}
                className="rc-equip-cell"
                style={{ cursor: onFocus ? "pointer" : undefined }}
                title={item.name}
                onClick={e => { if (onFocus) { e.stopPropagation(); onFocus(item.id); } }}>
                <img src={itemUrl(item.id, gearQuality?.[key] ?? 0)} alt={item.name}
                  onError={imgRetry(img => { img.style.opacity = "0.2"; })} />
                {qty != null && <span className="rc-equip-qty">×{qty}</span>}
                {key === "weapon" && <SpellOverlay ids={weaponSpellIds} centered />}
                {gearIds && <SpellOverlay ids={gearIds} />}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

// ── AltEquipSection ──────────────────────────────────────────
const ALT_SLOTS_VIEW = ["offhand", "helmet", "armor", "boots", "cape"] as const;
const ALT_SLOT_LABELS: Record<string, string> = {
  offhand: "Escudo", helmet: "Capacete", armor: "Armadura", boots: "Botas", cape: "Capa",
};

function AltEquipSection({ equip, gearSpells, swapMap, onSwap, titleInSidebar }: {
  equip: DraftEquip;
  gearSpells?: Record<string, string | null>;
  swapMap: Record<string, number>;
  onSwap: (slot: string, altIdx: number) => void;
  titleInSidebar?: boolean;
}) {
  const eq = equip as Record<string, unknown>;
  const groups = ALT_SLOTS_VIEW.map(slot => {
    const alts = safeAltArr(eq[`${slot}_alt`]);
    if (!alts.length) return null;
    const primary = (eq[slot] as EquipItem | undefined);
    const swappedIdx = swapMap[slot];
    // each display cell: what item to show + what slot/index it maps to for spells
    const cells = alts.map((alt, idx) => {
      const isSwapped = swappedIdx === idx;
      const item = isSwapped ? primary : alt;
      const spellKey = isSwapped ? slot : `${slot}_alt_${idx}`;
      return { idx, item, isSwapped, spellKey };
    });
    return { slot, cells };
  }).filter((g): g is NonNullable<typeof g> => g !== null);

  if (!groups.length) return null;

  const items = (
    <div className="rc-alt-section">
      {groups.map(({ slot, cells }) => (
        <div key={slot} style={{ display: "flex", alignItems: "center", gap: 2 }}>
          <span className="rc-alt-label">{ALT_SLOT_LABELS[slot]}</span>
          <div className="rc-equip-grid-row">
            {cells.map(({ idx, item, isSwapped, spellKey }) => {
              if (!item?.id) return null;
              const spellIds = gearSpells
                ? Object.entries(gearSpells).filter(([k]) => k.startsWith(`${spellKey}_`) && !k.startsWith(`${spellKey}_alt_`)).map(([, v]) => v)
                : null;
              return (
                <div key={idx}
                  className={"rc-equip-cell rc-equip-alt-cell" + (isSwapped ? " rc-equip-alt-active" : "")}
                  title={item.name}
                  onClick={e => { e.stopPropagation(); onSwap(slot, idx); }}>
                  <img src={itemUrl(item.id)} alt={item.name}
                    onError={imgRetry(img => { img.style.opacity = "0.2"; })} />
                  {spellIds && <SpellOverlay ids={spellIds} row />}
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );

  return (
    <div onClick={e => e.stopPropagation()} style={titleInSidebar ? undefined : { width: "fit-content" }}>
      {!titleInSidebar && (
        <div style={{ textAlign: "center", width: "100%", fontSize: 10, fontWeight: 700, color: "var(--hint)", textTransform: "uppercase", letterSpacing: ".08em", marginBottom: 6 }}>
          Equipamentos Alternativos
        </div>
      )}
      {items}
    </div>
  );
}

// ── ColorPicker ─────────────────────────────────────────────
function ColorPicker({ value, onChange }: { value: string; onChange: (c: string) => void }) {
  const [open, setOpen] = useState(false);
  const [hex, setHex] = useState(value.replace("#", ""));
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => { setHex(value.replace("#", "")); }, [value]);
  useEffect(() => {
    if (!open) return;
    function close(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  function applyHex(raw: string) {
    const v = raw.replace(/[^0-9a-fA-F]/g, "").slice(0, 6);
    setHex(v);
    if (v.length === 6) onChange(`#${v}`);
  }

  function randomize() {
    const r = Math.floor(Math.random() * 0xffffff).toString(16).padStart(6, "0");
    onChange(`#${r}`);
    setHex(r);
  }

  return (
    <div ref={ref} style={{ position: "relative", flexShrink: 0 }}>
      <button className="color-swatch" style={{ background: value, width: 22, height: 22, flexShrink: 0 }}
        onClick={e => { e.stopPropagation(); setOpen(o => !o); }} />
      {open && (
        <div className="color-picker-popup" onClick={e => e.stopPropagation()}>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <span style={{ color: "var(--muted)", fontSize: 12, fontFamily: "monospace" }}>#</span>
            <input className="input" value={hex} maxLength={6} placeholder="1399FF"
              style={{ width: 68, fontFamily: "monospace", fontSize: 12, padding: "2px 6px" }}
              onChange={e => applyHex(e.target.value)} />
            <label style={{ width: 26, height: 26, cursor: "pointer", borderRadius: 4, overflow: "hidden",
              background: value, border: "1px solid var(--border-strong)", flexShrink: 0, display: "block" }}>
              <input type="color" value={value} style={{ opacity: 0, width: "100%", height: "100%", cursor: "pointer" }}
                onChange={e => { onChange(e.target.value); setHex(e.target.value.replace("#", "")); }} />
            </label>
            <button className="cs-xbtn" title="Cor aleatória"
              onClick={e => { e.stopPropagation(); randomize(); }}>
              <i className="ti ti-dice" aria-hidden />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════════════
export default function CompBuilder({ perms }: { perms: Permissions }) {
  const [draft,            setDraft]            = useState<Draft | null>(null);
  const [editing,          setEditing]          = useState(false);
  const [offline,          setOffline]          = useState(false);
  const [openCard,         setOpenCard]         = useState<[number, number] | null>(null);
  const [histTotal,        setHistTotal]        = useState(0);
  const [focusedItemId,    setFocusedItemId]    = useState("");
  const [dirty,            setDirty]            = useState(false);
  const [saving,           setSaving]           = useState(false);
  const [saveOk,           setSaveOk]           = useState(false);
  const [error,            setError]            = useState<string | null>(null);
  const [weapons,          setWeapons]          = useState<WeaponOut[]>([]);
  const [spellCache,       setSpellCache]       = useState<Record<string, WeaponSpell[]>>({});
  const [delConfirm,       setDelConfirm]       = useState<number | null>(null);
  const [fnTypes,          setFnTypes]          = useState<FnTypeDef[]>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("hideout_fn_types") ?? "null");
      if (!saved) return [...DEFAULT_FN_TYPES];
      if (Array.isArray(saved)) return saved as FnTypeDef[];
      // migrate from old Record<string, {label, color}> format
      return Object.entries(saved as Record<string, { label: string; color: string }>)
        .filter(([k]) => !["dps_melee","dps_ranged","utility"].includes(k))
        .map(([key, v]) => ({ key, label: v.label, color: v.color }));
    } catch { return [...DEFAULT_FN_TYPES]; }
  });
  const [showFnPanel,      setShowFnPanel]      = useState(false);
  const [collapsedParties, setCollapsedParties] = useState<Set<number>>(new Set());
  const [history,          setHistory]          = useState<Draft[]>([]);
  const [compList,         setCompList]         = useState<{ id: number; name: string }[] | null>(null);
  const [activeCompId,     setActiveCompId]     = useState<number | null>(null);
  const [creatingComp,     setCreatingComp]     = useState(false);
  const [newCompName,      setNewCompName]      = useState("");
  const [deletingCompId,   setDeletingCompId]   = useState<number | null>(null);
  const [flexMenu,         setFlexMenu]         = useState<[number, number] | null>(null);
  const [addSlotMenu,      setAddSlotMenu]      = useState<number | null>(null);
  const [editRi,           setEditRi]           = useState(0);

  // ── Initial load ──────────────────────────────────────────
  useEffect(() => {
    api.listComps()
      .then(list => setCompList(list))
      .catch(() => { setCompList([{ id: 1, name: "Demonstração" }]); setOffline(true); });
    api.listWeapons().then(setWeapons).catch(() => {});
  }, []);

  async function loadComp(id: number) {
    try {
      const c = await api.getComp(id);
      setDraft(compToDraft(c));
      setActiveCompId(id);
      setHistory([]); setOpenCard(null); setError(null);
    } catch {
      if (offline) {
        setDraft(compToDraft(MOCK_API_COMP));
        setActiveCompId(id);
        setHistory([]); setOpenCard(null);
      } else {
        setError("Erro ao carregar composição");
      }
    }
  }

  async function createCompFn() {
    if (!newCompName.trim()) return;
    try {
      const c = await api.createComp({ name: newCompName.trim() });
      setCompList(prev => [...(prev ?? []), { id: c.id, name: c.name }]);
      await loadComp(c.id);
      setCreatingComp(false); setNewCompName(""); setEditing(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Erro ao criar composição");
    }
  }

  async function deleteCompFn(id: number) {
    try {
      await api.deleteComp(id);
      setCompList(prev => prev?.filter(c => c.id !== id) ?? prev);
      setDeletingCompId(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Erro ao excluir composição");
    }
  }

  // ── Mutations ─────────────────────────────────────────────
  const histCaptured = useRef(false);

  function upd(fn: (d: Draft) => Draft) {
    setDraft(prev => {
      if (!prev) return prev;
      setHistory(h => [...h, prev]);
      return fn(prev);
    });
    setDirty(true); setSaveOk(false); setError(null);
  }
  // Atualiza sem empurrar ao histórico — usado em inputs de texto (onChange)
  function updQuiet(fn: (d: Draft) => Draft) {
    setDraft(prev => prev ? fn(prev) : prev);
    setDirty(true); setSaveOk(false); setError(null);
  }
  // Captura o estado atual no histórico uma vez por sessão de foco
  function captureHistory() {
    if (histCaptured.current) return;
    histCaptured.current = true;
    setDraft(prev => {
      if (!prev) return prev;
      setHistory(h => [...h, prev]);
      return prev;
    });
  }
  function releaseFocus() { histCaptured.current = false; }

  function getPickableRoles(pi: number, si: number): DraftRole[] {
    if (!draft) return [];
    const inSlot = new Set(
      draft.parties[pi]?.slots[si]?.roles.map(r =>
        r.catalog_id != null ? `id:${r.catalog_id}` : `name:${r.name}`
      ) ?? []
    );
    const seen = new Set<string>();
    const result: DraftRole[] = [];
    for (const p of draft.parties) {
      for (const s of p.slots) {
        for (const r of s.roles) {
          const key = r.catalog_id != null ? `id:${r.catalog_id}` : `name:${r.name}`;
          if (!seen.has(key) && !inSlot.has(key) && (r.catalog_id != null || r.name.trim())) {
            seen.add(key);
            result.push(r);
          }
        }
      }
    }
    return result;
  }

  function updSlot(pi: number, si: number, fn: (s: DraftSlot) => DraftSlot) {
    upd(d => ({
      ...d,
      parties: d.parties.map((p, i) =>
        i !== pi ? p : { ...p, slots: p.slots.map((s, j) => j !== si ? s : fn(s)) }
      ),
    }));
  }
  function updRole(pi: number, si: number, ri: number, fn: (r: DraftRole) => DraftRole) {
    updSlot(pi, si, s => ({ ...s, roles: s.roles.map((r, i) => i !== ri ? r : fn(r)) }));
  }
  function updSlotQuiet(pi: number, si: number, fn: (s: DraftSlot) => DraftSlot) {
    updQuiet(d => ({
      ...d,
      parties: d.parties.map((p, i) =>
        i !== pi ? p : { ...p, slots: p.slots.map((s, j) => j !== si ? s : fn(s)) }
      ),
    }));
  }
  function updRoleQuiet(pi: number, si: number, ri: number, fn: (r: DraftRole) => DraftRole) {
    updSlotQuiet(pi, si, s => ({ ...s, roles: s.roles.map((r, i) => i !== ri ? r : fn(r)) }));
  }
  function undo() {
    if (!history.length) return;
    setDraft(history[history.length - 1]);
    setHistory(h => h.slice(0, -1));
    setDirty(true); setSaveOk(false);
  }
  function saveFnTypes(next: FnTypeDef[]) {
    setFnTypes(next);
    localStorage.setItem("hideout_fn_types", JSON.stringify(next));
  }

  function deleteFnType(key: string) {
    saveFnTypes(fnTypes.filter(t => t.key !== key));
    if (draft) {
      upd(d => ({
        ...d,
        parties: d.parties.map(p => ({ ...p, slots: p.slots.filter(s => s.fn !== key) })),
      }));
      setOpenCard(null);
    }
  }
  function togglePartyCollapse(pi: number) {
    setCollapsedParties(prev => {
      const next = new Set(prev);
      if (next.has(pi)) next.delete(pi); else next.add(pi);
      return next;
    });
  }

  // ── Open / close card + lazy equip load ───────────────────
  async function toggleCard(pi: number, si: number) {
    if (openCard?.[0] === pi && openCard?.[1] === si) {
      setOpenCard(null); setHistTotal(0); setFlexMenu(null); return;
    }
    setOpenCard([pi, si]); setHistTotal(0); setFocusedItemId(""); setFlexMenu(null); setEditRi(0);
    if (!draft) return;
    const roles = draft.parties[pi]?.slots[si]?.roles ?? [];
    for (let ri = 0; ri < roles.length; ri++) {
      const role = roles[ri];
      if (role.catalog_id !== null && !role.equip_loaded) {
        try {
          const detail: GameRoleDetail = await api.getRole(role.catalog_id);
          const equip = detail.build_items?.length ? buildItemsToEquip(detail.build_items) : {};
          setDraft(prev => {
            if (!prev) return prev;
            return {
              ...prev,
              parties: prev.parties.map((p, i) =>
                i !== pi ? p : {
                  ...p,
                  slots: p.slots.map((s, j) =>
                    j !== si ? s : {
                      ...s,
                      roles: s.roles.map((r, k) =>
                        k !== ri ? r : {
                          ...r, equip,
                          color: detail.color ?? null,
                          play_style: detail.play_style,
                          abilities:  detail.abilities,
                          obs:        detail.obs,
                          q_spell:       detail.q_spell ?? null,
                          w_spell:       detail.w_spell ?? null,
                          passive_spell: detail.passive_spell ?? null,
                          gear_spells:   detail.gear_spells ?? {},
                          equip_loaded: true,
                        }
                      ),
                    }
                  ),
                }
              ),
            };
          });
          if (equip.weapon?.id) await loadSpells(wBase(equip.weapon.id));
          for (const gk of ["helmet", "armor", "boots"] as const)
            if (equip[gk]?.id) await loadSpells(wBase(equip[gk]!.id));
        } catch {
          setDraft(prev => {
            if (!prev) return prev;
            return {
              ...prev,
              parties: prev.parties.map((p, i) =>
                i !== pi ? p : {
                  ...p,
                  slots: p.slots.map((s, j) =>
                    j !== si ? s : {
                      ...s,
                      roles: s.roles.map((r, k) => k !== ri ? r : { ...r, equip_loaded: true }),
                    }
                  ),
                }
              ),
            };
          });
        }
      } else if (role.equip_loaded) {
        if (role.equip.weapon?.id) await loadSpells(wBase(role.equip.weapon.id));
        for (const gk of ["helmet", "armor", "boots"] as const)
          if (role.equip[gk]?.id) await loadSpells(wBase(role.equip[gk]!.id));
      }
    }
  }

  async function loadSpells(base: string) {
    if (!base || spellCache[base] !== undefined) return;
    try {
      const spells = await api.weaponSpells(base);
      setSpellCache(prev => ({ ...prev, [base]: spells }));
    } catch {
      setSpellCache(prev => ({ ...prev, [base]: [] }));
    }
  }

  // ── Weapon change ─────────────────────────────────────────
  function onWeaponChange(pi: number, si: number, ri: number, id: string, name: string) {
    const base = id ? wBase(id) : null;
    const dbWeapon = base ? weapons.find(w => wBase(w.item_id) === base) : null;
    updRole(pi, si, ri, r => ({
      ...r,
      equip: { ...r.equip, weapon: id ? { id, name } : undefined },
      fn: dbWeapon?.invisible_function ?? null,
      weapon_db_id: dbWeapon?.id ?? null,
      q_spell: null, w_spell: null, passive_spell: null,
    }));
    if (base && id) loadSpells(base);
  }

  // ── Party / slot management ───────────────────────────────
  function addParty() {
    upd(d => ({ ...d, parties: [...d.parties, { name: `Party ${d.parties.length + 1}`, slots: [] }] }));
  }
  function removeParty(pi: number) {
    if (!draft || draft.parties.length <= 1) return;
    upd(d => ({ ...d, parties: d.parties.filter((_, i) => i !== pi) }));
    if (openCard?.[0] === pi) setOpenCard(null);
    setCollapsedParties(prev => {
      const next = new Set<number>();
      for (const i of prev) {
        if (i < pi) next.add(i);
        else if (i > pi) next.add(i - 1);
      }
      return next;
    });
  }
  function addSlot(pi: number) {
    if (!draft || draft.parties[pi].slots.length >= MAX_SLOTS) return;
    const idx = draft.parties[pi].slots.length;
    upd(d => ({
      ...d,
      parties: d.parties.map((p, i) =>
        i !== pi ? p : { ...p, slots: [...p.slots, { fn: null, roles: [emptyRole()] }] }
      ),
    }));
    setOpenCard([pi, idx]);
  }
  function removeSlot(pi: number, si: number) {
    upd(d => ({
      ...d,
      parties: d.parties.map((p, i) =>
        i !== pi ? p : { ...p, slots: p.slots.filter((_, j) => j !== si) }
      ),
    }));
    if (openCard?.[0] === pi && openCard?.[1] === si) setOpenCard(null);
  }
  async function deleteFromCatalog(catalogId: number, pi: number, si: number) {
    try {
      await api.deleteRole(catalogId);
      removeSlot(pi, si);
      setDelConfirm(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  // ── Save ──────────────────────────────────────────────────
  async function save() {
    if (!draft) return;
    setSaving(true); setError(null);
    try {
      const newParties: DraftParty[] = JSON.parse(JSON.stringify(draft.parties));
      const updatedIds = new Set<number>();
      for (let pi = 0; pi < newParties.length; pi++) {
        for (let si = 0; si < newParties[pi].slots.length; si++) {
          for (let ri = 0; ri < newParties[pi].slots[si].roles.length; ri++) {
            const role = newParties[pi].slots[si].roles[ri];
            const payload = roleToPayload(role);
            if (role.catalog_id === null) {
              if (!role.name.trim()) continue;
              const created = await api.createRole({ name: role.name, ...payload });
              newParties[pi].slots[si].roles[ri].catalog_id = created.id;
            } else if (role.equip_loaded && !updatedIds.has(role.catalog_id)) {
              updatedIds.add(role.catalog_id);
              await api.updateRole(role.catalog_id, { name: role.name, ...payload });
            }
          }
        }
      }
      const updated = await api.updateComp(draft.id, {
        name: draft.name,
        parties: newParties.map(p => ({
          name: p.name || null,
          slots: p.slots.map(s => ({
            label: s.roles[0]?.name || null,
            fn: s.fn,
            role_ids: s.roles.map(r => r.catalog_id).filter((id): id is number => id !== null),
          })),
        })),
      });
      // Keep current draft (preserves fn, equip, spells, etc.) — only sync new catalog_ids
      void updated;
      setDraft(prev => {
        if (!prev) return prev;
        return {
          ...prev,
          parties: prev.parties.map((p, pi) => ({
            ...p,
            slots: p.slots.map((s, si) => ({
              ...s,
              roles: s.roles.map((r, ri) => ({
                ...r,
                catalog_id: newParties[pi]?.slots[si]?.roles[ri]?.catalog_id ?? r.catalog_id,
              })),
            })),
          })),
        };
      });
      setDirty(false); setSaveOk(true);
      setTimeout(() => setSaveOk(false), 2500);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  // ── Lista de comps ────────────────────────────────────────
  if (!activeCompId) {
    return (
      <div className="container">
        <div className="card">
          <div className="comp-header">
            <span style={{ fontWeight: 600, fontSize: 16 }}>Composições</span>
            {offline && <span className="badge">demonstração</span>}
            {perms["comps.create"] && (
              <button className="btn" style={{ marginLeft: "auto" }}
                onClick={() => { setCreatingComp(p => !p); setError(null); }}>
                <i className="ti ti-plus" aria-hidden /> Nova composição
              </button>
            )}
          </div>

          {creatingComp && (
            <div style={{ padding: "8px 14px", borderTop: "1px solid var(--border)", display: "flex", gap: 8 }}>
              <input className="input" style={{ flex: 1, fontSize: 13, padding: "6px 10px" }}
                placeholder="Nome da composição…"
                value={newCompName}
                autoFocus
                onChange={e => setNewCompName(e.target.value)}
                onKeyDown={e => {
                  if (e.key === "Enter") createCompFn();
                  if (e.key === "Escape") { setCreatingComp(false); setNewCompName(""); }
                }} />
              <button className="btn primary" onClick={createCompFn} disabled={!newCompName.trim()}>Criar</button>
              <button className="btn" onClick={() => { setCreatingComp(false); setNewCompName(""); }}>Cancelar</button>
            </div>
          )}

          {error && <p style={{ color: "#e07a7a", padding: "8px 16px", margin: 0, fontSize: 12 }}>{error}</p>}

          {!compList && <p className="muted" style={{ padding: "16px 14px" }}>Carregando…</p>}
          {compList?.length === 0 && !creatingComp && (
            <p className="muted" style={{ padding: "16px 14px" }}>
              Nenhuma composição.{perms["comps.create"] ? " Crie a primeira!" : ""}
            </p>
          )}
          {compList?.map(c => (
            <div key={c.id} className="comp-list-item" style={{ display: "flex", alignItems: "center" }}>
              <button style={{ flex: 1, display: "flex", alignItems: "center", gap: 8, background: "none", border: "none", cursor: "pointer", padding: "10px 14px", color: "inherit", textAlign: "left" }}
                onClick={() => { setDeletingCompId(null); loadComp(c.id); }}>
                <i className="ti ti-layout-list" style={{ color: "var(--hint)", flexShrink: 0 }} aria-hidden />
                <span style={{ flex: 1 }}>{c.name}</span>
                <i className="ti ti-chevron-right" style={{ color: "var(--hint)" }} aria-hidden />
              </button>
              {perms["comps.manage"] && (
                deletingCompId === c.id ? (
                  <div style={{ display: "flex", gap: 6, padding: "0 10px", flexShrink: 0 }}>
                    <button className="btn" style={{ fontSize: 11, padding: "3px 8px", color: "#e07a7a", borderColor: "#e07a7a" }}
                      onClick={() => deleteCompFn(c.id)}>
                      Confirmar
                    </button>
                    <button className="btn" style={{ fontSize: 11, padding: "3px 8px" }}
                      onClick={() => setDeletingCompId(null)}>
                      Cancelar
                    </button>
                  </div>
                ) : (
                  <button className="btn" style={{ fontSize: 12, padding: "3px 8px", marginRight: 10, flexShrink: 0, opacity: 0.5 }}
                    title="Excluir composição"
                    onClick={e => { e.stopPropagation(); setDeletingCompId(c.id); }}>
                    <i className="ti ti-trash" />
                  </button>
                )
              )}
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (!draft) return <div className="container"><p className="muted">Carregando…</p></div>;

  // ── Render (builder) ──────────────────────────────────────
  return (
    <div className="container">
      <div className="card">

        {/* Header */}
        <div className="comp-header">
          <button className="btn" style={{ padding: "5px 10px" }}
            onClick={() => { setActiveCompId(null); setDraft(null); setError(null); setEditing(false); }}
            title="Voltar para a lista">
            <i className="ti ti-arrow-left" aria-hidden />
          </button>
          {editing ? (
            <input className="comp-name-input" value={draft.name}
              onFocus={captureHistory} onBlur={releaseFocus}
              onChange={e => updQuiet(d => ({ ...d, name: e.target.value }))} />
          ) : (
            <span style={{ fontWeight: 600, fontSize: 16 }}>{draft.name}</span>
          )}
          {offline && <span className="badge">demonstração</span>}
          {error && <span style={{ color: "#e07a7a", fontSize: 12 }}>{error}</span>}

          <div className="comp-view-toggle">
            <button className={!editing ? "active" : ""} onClick={() => setEditing(false)}>
              <i className="ti ti-eye" aria-hidden /> Ver
            </button>
            {perms["comps.manage"] && (
              <button className={editing ? "active" : ""} onClick={() => setEditing(true)}>
                <i className="ti ti-pencil" aria-hidden /> Editar
              </button>
            )}
          </div>

          <button className="btn" style={{ marginLeft: "auto" }}
            onClick={() => setShowFnPanel(p => !p)} title="Configurar tipos de função">
            <i className="ti ti-palette" aria-hidden />
          </button>

          <button className="btn" onClick={undo} disabled={!history.length}
            title="Desfazer última modificação">
            <i className="ti ti-arrow-back-up" aria-hidden />
          </button>

          {editing && (
            <button className={"btn" + (dirty ? " primary" : "")}
              onClick={save} disabled={!dirty || saving}>
              {saveOk
                ? <><i className="ti ti-check" aria-hidden /> Salvo</>
                : saving ? "Salvando…"
                : <><i className="ti ti-device-floppy" aria-hidden /> Salvar</>}
            </button>
          )}
        </div>

        {/* Function type CRUD panel */}
        {showFnPanel && (
          <div className="fn-colors-panel">
            {fnTypes.map((ft, fti) => (
              <div key={ft.key} className="fn-color-row">
                <ColorPicker value={ft.color}
                  onChange={c => saveFnTypes(fnTypes.map((t, i) => i === fti ? { ...t, color: c } : t))} />
                <input className="fn-emoji-input" value={ft.emoji ?? ""} placeholder="😀" maxLength={4}
                  title="Emoji"
                  onChange={e => saveFnTypes(fnTypes.map((t, i) => i === fti ? { ...t, emoji: e.target.value || undefined } : t))} />
                <input className="fn-type-name-input" value={ft.label} style={{ color: ft.color }}
                  onChange={e => saveFnTypes(fnTypes.map((t, i) => i === fti ? { ...t, label: e.target.value } : t))} />
                <button className="cs-xbtn" title="Mover acima" disabled={fti === 0}
                  onClick={() => { if (fti > 0) { const n = [...fnTypes]; [n[fti-1], n[fti]] = [n[fti], n[fti-1]]; saveFnTypes(n); } }}>
                  <i className="ti ti-chevron-up" aria-hidden />
                </button>
                <button className="cs-xbtn" title="Mover abaixo" disabled={fti === fnTypes.length - 1}
                  onClick={() => { if (fti < fnTypes.length - 1) { const n = [...fnTypes]; [n[fti], n[fti+1]] = [n[fti+1], n[fti]]; saveFnTypes(n); } }}>
                  <i className="ti ti-chevron-down" aria-hidden />
                </button>
                <button className="cs-xbtn" title="Excluir tipo" onClick={() => deleteFnType(ft.key)}>
                  <i className="ti ti-x" aria-hidden />
                </button>
              </div>
            ))}
            <div className="fn-color-row" style={{ gap: 8 }}>
              <button className="btn" style={{ fontSize: 11, padding: "3px 9px" }}
                onClick={() => saveFnTypes([...fnTypes, { key: `custom_${Date.now()}`, label: "Novo", color: "#888888" }])}>
                <i className="ti ti-plus" aria-hidden /> Adicionar
              </button>
              <button className="btn" style={{ fontSize: 11, padding: "3px 9px" }}
                onClick={() => saveFnTypes([...DEFAULT_FN_TYPES])}>
                Restaurar padrões
              </button>
            </div>
          </div>
        )}

        {/* Parties */}
        <div className="comp-body">
          {draft.parties.map((party, pi) => {
            const isCollapsed = collapsedParties.has(pi);
            return (
              <div key={pi} className="party-card">

                {/* Party header */}
                <div className="party-card-head">
                  <button className="party-collapse-btn"
                    onClick={() => togglePartyCollapse(pi)}
                    title={isCollapsed ? "Expandir" : "Colapsar"}>
                    <i className={`ti ti-chevron-${isCollapsed ? "right" : "down"}`} aria-hidden />
                  </button>
                  {editing ? (
                    <input className="party-name-input" value={party.name}
                      placeholder={`Party ${pi + 1}`}
                      onFocus={captureHistory} onBlur={releaseFocus}
                      onChange={e => updQuiet(d => ({
                        ...d,
                        parties: d.parties.map((p, i) => i !== pi ? p : { ...p, name: e.target.value }),
                      }))} />
                  ) : (
                    <span className="party-name-label">{party.name || `Party ${pi + 1}`}</span>
                  )}
                  <span className={"party-count" + (party.slots.length >= MAX_SLOTS ? " full" : "")}>
                    {party.slots.length}/{MAX_SLOTS}
                  </span>
                  {editing && draft.parties.length > 1 && (
                    <button className="cs-xbtn" onClick={() => removeParty(pi)} title="Remover party">
                      <i className="ti ti-x" aria-hidden />
                    </button>
                  )}
                </div>

                {/* Party body */}
                {!isCollapsed && (
                  <div className="party-card-body">
                    {(editing
                      ? party.slots.map((slot, si) => ({ slot, si }))
                      : [...party.slots.map((slot, si) => ({ slot, si }))].sort((a, b) => {
                          const iA = fnTypes.findIndex(t => t.key === (a.slot.fn ?? ""));
                          const iB = fnTypes.findIndex(t => t.key === (b.slot.fn ?? ""));
                          return (iA < 0 ? 999 : iA) - (iB < 0 ? 999 : iB);
                        })
                    ).map(({ slot, si }) => {
                      const role    = slot.roles[0];
                      const isOpen  = openCard?.[0] === pi && openCard?.[1] === si;
                      const chipColor = slot.fn ? (getFnDef(slot.fn, fnTypes)?.color ?? "#888") : undefined;
                      const weapBase  = role?.equip_loaded && role.equip.weapon?.id
                        ? wBase(role.equip.weapon.id) : null;
                      const spells = weapBase ? spellCache[weapBase] : undefined;
                      const formRole = slot.roles[editRi] ?? role;
                      const formWeapBase = formRole?.equip_loaded && formRole.equip.weapon?.id
                        ? wBase(formRole.equip.weapon.id) : null;
                      const formSpells = formWeapBase ? spellCache[formWeapBase] : undefined;

                      return (
                        <div key={si}
                          className={`role-card${isOpen ? " rc-open" : ""}${isOpen && editing ? " rc-editing" : ""}`}
                          style={{ "--chip-color": chipColor ?? "var(--border-strong)" } as CSSProperties}
                          onClick={() => toggleCard(pi, si)}>

                          {/* Card head — weapon icon + name + equip strip + badge */}
                          <div className="rc-head">
                            {role ? (
                              <>
                                {/* Weapon icon */}
                                {role.equip.weapon?.id && (
                                  <img className="rc-weapon-icon"
                                    src={itemUrl(role.equip.weapon.id)} alt=""
                                    onError={imgRetry(img => { img.style.opacity = "0.15"; })} />
                                )}
                                {editing && isOpen ? (
                                  <input className="input"
                                    style={{ flex: 1, minWidth: 0, padding: "2px 6px", fontSize: 13, height: 26 }}
                                    placeholder="Sem nome"
                                    value={formRole.name}
                                    onClick={e => e.stopPropagation()}
                                    onFocus={captureHistory} onBlur={releaseFocus}
                                    onChange={e => updRoleQuiet(pi, si, editRi, r => ({ ...r, name: e.target.value }))} />
                                ) : (
                                  <span className="rc-name">{role.name || "Sem nome"}</span>
                                )}
                                {/* Build total price */}
                                {isOpen && histTotal > 0 && (
                                  <span className="rc-price">{silverShort(histTotal)}</span>
                                )}
                                {/* badge + strip primário */}
                                {slot.fn && !isOpen && (() => {
                                  const ft = getFnDef(slot.fn, fnTypes);
                                  return (
                                    <span className="rc-fn-badge"
                                      style={{
                                        background: (ft?.color ?? "#888") + "25",
                                        color: ft?.color ?? "#888",
                                      }}>
                                      {ft ? fnLabel(ft) : slot.fn}
                                    </span>
                                  );
                                })()}
                                {!isOpen && role.equip_loaded && (
                                  <div style={{ marginLeft: 6, flexShrink: 0 }}>
                                    <EquipStrip equip={role.equip} weaponIs2H={is2H(role.equip.weapon?.id)} />
                                  </div>
                                )}
                              </>
                            ) : (
                              <span className="chip-empty" style={{ fontSize: 12 }}>Sem papel</span>
                            )}
                            {editing && isOpen && (
                              <>
                                {/* Fn type selector */}
                                <select className="fn-type-select"
                                  value={slot.fn ?? ""}
                                  onClick={e => e.stopPropagation()}
                                  onChange={e => {
                                    e.stopPropagation();
                                    const v = e.target.value || null;
                                    updSlot(pi, si, s => ({ ...s, fn: v, roles: s.roles.map((r, i) => i === editRi ? { ...r, fn: v } : r) }));
                                  }}
                                  style={{
                                    color: getFnDef(slot.fn, fnTypes)?.color ?? "var(--muted)",
                                  }}>
                                  <option value="">—</option>
                                  {fnTypes.map(ft => (
                                    <option key={ft.key} value={ft.key}>{fnLabel(ft)}</option>
                                  ))}
                                </select>
                                <button className="cs-xbtn"
                                  onClick={e => { e.stopPropagation(); removeSlot(pi, si); }}
                                  title="Remover do comp">
                                  <i className="ti ti-x" aria-hidden />
                                </button>
                              </>
                            )}
                          </div>

                          {/* Flex alternatives (colapsado) — fora do rc-head para não afetar alinhamento do strip primário */}
                          {!isOpen && slot.roles.slice(1).some(fr => fr.equip_loaded) && (
                            <div style={{ display: "flex", flexDirection: "column", gap: 3, padding: "0 10px 6px", alignItems: "flex-start" }} onClick={e => e.stopPropagation()}>
                              {slot.roles.slice(1).map((fr, fri) => fr.equip_loaded ? (
                                <div key={fri} style={{ display: "flex", alignItems: "center", gap: 5 }}>
                                  <span style={{ fontSize: 8, color: "var(--muted)", flexShrink: 0 }}>OU</span>
                                  <EquipStrip equip={fr.equip} weaponIs2H={is2H(fr.equip.weapon?.id)} />
                                </div>
                              ) : null)}
                            </div>
                          )}

                          {/* Card body (expanded) */}
                          {isOpen && (
                            <div className="rc-body" onClick={e => e.stopPropagation()}>
                              {/* Fn type picker — shown when slot has no type yet */}
                              {editing && slot.fn === null ? (
                                <div className="fn-type-picker">
                                  <p className="fn-picker-hint">Selecione o tipo de função desta bracket:</p>
                                  {fnTypes.map((ft, fti) => (
                                    <div key={ft.key} className="fn-picker-row">
                                      <button className="fn-type-assign-btn"
                                        style={{ background: ft.color + "25", color: ft.color, borderColor: ft.color + "60" }}
                                        onClick={() => updSlot(pi, si, s => ({
                                          ...s, fn: ft.key,
                                          roles: s.roles.map((r, i) => i === 0 ? { ...r, fn: ft.key } : r),
                                        }))}>
                                        {fnLabel(ft)}
                                      </button>
                                      <ColorPicker value={ft.color}
                                        onChange={c => saveFnTypes(fnTypes.map((t, i) => i === fti ? { ...t, color: c } : t))} />
                                      <input className="fn-emoji-input" value={ft.emoji ?? ""} placeholder="😀" maxLength={4}
                                        title="Emoji"
                                        onChange={e => saveFnTypes(fnTypes.map((t, i) => i === fti ? { ...t, emoji: e.target.value || undefined } : t))} />
                                      <input className="fn-type-name-input" value={ft.label} style={{ color: ft.color, flex: 1 }}
                                        onChange={e => saveFnTypes(fnTypes.map((t, i) => i === fti ? { ...t, label: e.target.value } : t))} />
                                      <button className="cs-xbtn" title="Mover acima" disabled={fti === 0}
                                        onClick={() => { if (fti > 0) { const n = [...fnTypes]; [n[fti-1], n[fti]] = [n[fti], n[fti-1]]; saveFnTypes(n); } }}>
                                        <i className="ti ti-chevron-up" aria-hidden />
                                      </button>
                                      <button className="cs-xbtn" title="Mover abaixo" disabled={fti === fnTypes.length - 1}
                                        onClick={() => { if (fti < fnTypes.length - 1) { const n = [...fnTypes]; [n[fti], n[fti+1]] = [n[fti+1], n[fti]]; saveFnTypes(n); } }}>
                                        <i className="ti ti-chevron-down" aria-hidden />
                                      </button>
                                      <button className="cs-xbtn" title="Excluir tipo" onClick={() => deleteFnType(ft.key)}>
                                        <i className="ti ti-trash" aria-hidden />
                                      </button>
                                    </div>
                                  ))}
                                  <button className="btn" style={{ marginTop: 8, fontSize: 11, padding: "3px 9px" }}
                                    onClick={() => saveFnTypes([...fnTypes, { key: `custom_${Date.now()}`, label: "Novo", color: "#888888" }])}>
                                    <i className="ti ti-plus" aria-hidden /> Novo tipo
                                  </button>
                                </div>
                              ) : editing ? (

                                /* ── Edit form ─────────────────────────── */
                                <>
                                  {/* Tabs de flex */}
                                  {slot.roles.length > 1 && (
                                    <div className="flex-role-tabs">
                                      {slot.roles.map((r, ri) => (
                                        <button key={ri}
                                          className={`flex-role-tab${editRi === ri ? " act" : ""}`}
                                          onClick={() => setEditRi(ri)}>
                                          {ri === 0 ? "Principal" : (r.name
                                            ? (r.flex_of ? `${r.name} (flex de ${r.flex_of})` : r.name)
                                            : `Flex ${ri}`)}
                                        </button>
                                      ))}
                                    </div>
                                  )}

                                  {/* Weapon */}
                                  <div className="equip-field">
                                    <label className="equip-field-label">Arma</label>
                                    <ItemPicker slot="weapon"
                                      valueId={formRole?.equip.weapon?.id ?? ""}
                                      valueName={formRole?.equip.weapon?.name ?? ""}
                                      placeholder="Selecionar arma…"
                                      onChange={(id, name) => onWeaponChange(pi, si, editRi, id, name)} />
                                  </div>

                                  {/* Q / W / Passive spell picker */}
                                  {formSpells && formSpells.length > 0 && (
                                    <div className="equip-field">
                                      <label className="equip-field-label">Habilidades Q / W / Passiva</label>
                                      <div className="rc-spells">
                                        <SpellPicker spells={formSpells} slot="Q"
                                          selected={formRole?.q_spell ?? null}
                                          onChange={id => updRole(pi, si, editRi, r => ({ ...r, q_spell: id }))} />
                                        <SpellPicker spells={formSpells} slot="W"
                                          selected={formRole?.w_spell ?? null}
                                          onChange={id => updRole(pi, si, editRi, r => ({ ...r, w_spell: id }))} />
                                        <SpellPicker spells={formSpells} slot="passive"
                                          selected={formRole?.passive_spell ?? null}
                                          onChange={id => updRole(pi, si, editRi, r => ({ ...r, passive_spell: id }))} />
                                      </div>
                                    </div>
                                  )}

                                  {/* Other equipment */}
                                  <div className="equip-field">
                                    <label className="equip-field-label">Equipamento</label>
                                    <div className="equip-grid-narrow">
                                      {EQUIP_SLOTS.filter(s => s.key !== "weapon").filter(s => !(s.key === "offhand" && is2H(formRole?.equip.weapon?.id))).map(({ key, label }) => {
                                        const gearBase = formRole?.equip[key]?.id ? wBase(formRole.equip[key]!.id) : null;
                                        const gearSpells = gearBase ? spellCache[gearBase] : undefined;
                                        return (
                                          <div className="equip-field" key={key}>
                                            <label className="equip-field-label">{label}</label>
                                            <ItemPicker slot={key}
                                              valueId={formRole?.equip[key]?.id ?? ""}
                                              valueName={formRole?.equip[key]?.name ?? ""}
                                              placeholder={label}
                                              onChange={(id, name) => {
                                                updRole(pi, si, editRi, r => ({
                                                  ...r,
                                                  equip: {
                                                    ...r.equip,
                                                    [key]: id ? { id, name } : undefined,
                                                    ...(!id && ALT_CAPABLE.has(key) ? { [`${key}_alt`]: undefined as unknown } : {}),
                                                  },
                                                  gear_spells: Object.fromEntries(Object.entries(r.gear_spells).filter(([k]) => !k.startsWith(`${key}_`))),
                                                }));
                                                if (id) loadSpells(wBase(id));
                                              }} />
                                            {ALT_CAPABLE.has(key) && formRole?.equip[key]?.id && (() => {
                                              const rawAlt = (formRole.equip as Record<string, unknown>)[`${key}_alt`];
                                              const alts: (EquipItem | undefined)[] = Array.isArray(rawAlt) ? rawAlt : [];
                                              const usedIds = [formRole.equip[key]!.id, ...alts.map(a => a?.id).filter(Boolean) as string[]];
                                              return (
                                                <>
                                                  {alts.map((alt, ai) => (
                                                    <div key={ai} style={{ marginTop: 4 }}>
                                                      <ItemPicker slot={key}
                                                        valueId={alt?.id ?? ""}
                                                        valueName={alt?.name ?? ""}
                                                        placeholder="Alternativo…"
                                                        excludeIds={usedIds.filter(id => id !== alt?.id)}
                                                        onChange={(id, name) => {
                                                          updRole(pi, si, editRi, r => {
                                                            const rawA = (r.equip as Record<string, unknown>)[`${key}_alt`];
                                                            const prev: (EquipItem | undefined)[] = Array.isArray(rawA) ? rawA : [];
                                                            const next = [...prev];
                                                            next[ai] = id ? { id, name } : undefined;
                                                            const filtered = next.filter((a): a is EquipItem => !!(a?.id));
                                                            return { ...r, equip: { ...r.equip, [`${key}_alt`]: filtered.length ? filtered : undefined } };
                                                          });
                                                          if (id) loadSpells(wBase(id));
                                                        }} />
                                                    </div>
                                                  ))}
                                                  {alts.length < 2 && (
                                                    <button className="btn" style={{ fontSize: 11, padding: "2px 6px", marginTop: 4 }}
                                                      onClick={e => {
                                                        e.stopPropagation();
                                                        updRole(pi, si, editRi, r => {
                                                          const rawA = (r.equip as Record<string, unknown>)[`${key}_alt`];
                                                          const prev: (EquipItem | undefined)[] = Array.isArray(rawA) ? rawA : [];
                                                          return { ...r, equip: { ...r.equip, [`${key}_alt`]: [...prev, { id: "", name: "" }] } };
                                                        });
                                                      }}>
                                                      <i className="ti ti-plus" /> Alt
                                                    </button>
                                                  )}
                                                </>
                                              );
                                            })()}
                                            {(key === "potion" || key === "food") && (
                                              <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 4 }}>
                                                <label className="equip-field-label">Qtd.</label>
                                                <input type="number" className="input"
                                                  style={{ width: 60, fontSize: 13, padding: "2px 6px" }}
                                                  min={1} max={999}
                                                  value={key === "potion" ? (formRole?.potion_qty ?? 10) : (formRole?.food_qty ?? 1)}
                                                  onFocus={captureHistory} onBlur={releaseFocus}
                                                  onChange={e => {
                                                    const v = Math.max(1, parseInt(e.target.value) || 1);
                                                    updRoleQuiet(pi, si, editRi, r => key === "potion"
                                                      ? { ...r, potion_qty: v }
                                                      : { ...r, food_qty: v });
                                                  }} />
                                              </div>
                                            )}
                                            {gearSpells && gearSpells.length > 0 && (["helmet","armor","boots"] as const).includes(key as "helmet"|"armor"|"boots") && (() => {
                                              const alts = safeAltArr((formRole?.equip as Record<string, unknown> | undefined)?.[`${key}_alt`]);
                                              const altCols = alts.map((alt, ai) => {
                                                const sp = alt.id ? spellCache[wBase(alt.id)] : null;
                                                return sp?.length ? { ai, sp } : null;
                                              }).filter((x): x is { ai: number; sp: WeaponSpell[] } => x !== null);
                                              return (
                                                <div style={{ display: "flex", gap: 0, alignItems: "flex-start", marginTop: 4 }}>
                                                  <div className="rc-spells" style={{ paddingRight: altCols.length ? 10 : 0 }}>
                                                    {[...new Set(gearSpells.map(s => s.slot))].map(slotName => (
                                                      <SpellPicker key={slotName} spells={gearSpells} slot={slotName}
                                                        selected={formRole?.gear_spells[`${key}_${slotName}`] ?? null}
                                                        onChange={id => updRole(pi, si, editRi, r => ({
                                                          ...r, gear_spells: { ...r.gear_spells, [`${key}_${slotName}`]: id },
                                                        }))} />
                                                    ))}
                                                  </div>
                                                  {altCols.map(({ ai, sp }) => (
                                                    <div key={ai} style={{ borderLeft: "1px solid var(--border)", paddingLeft: 10 }}>
                                                      <div className="rc-spells">
                                                        {[...new Set(sp.map(s => s.slot))].map(slotName => (
                                                          <SpellPicker key={slotName} spells={sp} slot={slotName}
                                                            selected={formRole?.gear_spells[`${key}_alt_${ai}_${slotName}`] ?? null}
                                                            onChange={id => updRole(pi, si, editRi, r => ({
                                                              ...r, gear_spells: { ...r.gear_spells, [`${key}_alt_${ai}_${slotName}`]: id },
                                                            }))} />
                                                        ))}
                                                      </div>
                                                    </div>
                                                  ))}
                                                </div>
                                              );
                                            })()}
                                          </div>
                                        );
                                      })}
                                    </div>
                                  </div>

                                  {/* Playstyle */}
                                  <div className="equip-field">
                                    <label className="equip-field-label">Modo de jogo</label>
                                    <textarea className="input" rows={2}
                                      style={{ fontSize: 13, resize: "vertical", fontFamily: "inherit" }}
                                      placeholder="ex.: Fica atrás do grupo, cura o alvo principal…"
                                      value={formRole?.play_style ?? ""}
                                      onFocus={captureHistory} onBlur={releaseFocus}
                                      onChange={e => updRoleQuiet(pi, si, editRi, r => ({
                                        ...r, play_style: e.target.value || null,
                                      }))} />
                                  </div>

                                  {/* Observations */}
                                  <div className="equip-field">
                                    <label className="equip-field-label">Observações</label>
                                    <textarea className="input" rows={2}
                                      style={{ fontSize: 13, resize: "vertical", fontFamily: "inherit" }}
                                      placeholder="ex.: Prioridade de alvo, posicionamento…"
                                      value={formRole?.obs ?? ""}
                                      onFocus={captureHistory} onBlur={releaseFocus}
                                      onChange={e => updRoleQuiet(pi, si, editRi, r => ({
                                        ...r, obs: e.target.value || null,
                                      }))} />
                                  </div>

                                  {/* Actions */}
                                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                                    <button className="btn" style={{ fontSize: 11, padding: "3px 8px" }}
                                      onClick={() => setFlexMenu(prev =>
                                        prev?.[0] === pi && prev?.[1] === si ? null : [pi, si]
                                      )}>
                                      <i className="ti ti-stack-2" aria-hidden /> Flex
                                    </button>
                                    {editRi === 0 && role?.catalog_id !== null && role?.catalog_id !== undefined && (
                                      delConfirm === role.catalog_id ? (
                                        <>
                                          <button className="btn"
                                            style={{ fontSize: 11, padding: "3px 8px", color: "#e07a7a" }}
                                            onClick={() => deleteFromCatalog(role.catalog_id!, pi, si)}>
                                            Confirmar exclusão
                                          </button>
                                          <button className="btn"
                                            style={{ fontSize: 11, padding: "3px 8px" }}
                                            onClick={() => setDelConfirm(null)}>
                                            Cancelar
                                          </button>
                                        </>
                                      ) : (
                                        <button className="btn"
                                          style={{ fontSize: 11, padding: "3px 8px", color: "#e07a7a" }}
                                          onClick={() => setDelConfirm(role.catalog_id)}>
                                          <i className="ti ti-trash" aria-hidden /> Excluir do catálogo
                                        </button>
                                      )
                                    )}
                                    {editRi > 0 && (
                                      <button className="btn"
                                        style={{ fontSize: 11, padding: "3px 8px", color: "#e07a7a" }}
                                        onClick={() => {
                                          updSlot(pi, si, s => ({ ...s, roles: s.roles.filter((_, i) => i !== editRi) }));
                                          setEditRi(0);
                                        }}>
                                        <i className="ti ti-x" aria-hidden /> Remover flex
                                      </button>
                                    )}
                                  </div>

                                  {/* Flex picker menu */}
                                  {flexMenu?.[0] === pi && flexMenu?.[1] === si && (() => {
                                    const pickable = getPickableRoles(pi, si);
                                    return (
                                      <div className="flex-picker-menu">
                                        <button className="flex-picker-item flex-picker-new"
                                          onClick={() => {
                                            const newRi = slot.roles.length;
                                            updSlot(pi, si, s => ({ ...s, roles: [...s.roles, emptyRole()] }));
                                            setEditRi(newRi);
                                            setFlexMenu(null);
                                          }}>
                                          <i className="ti ti-plus" aria-hidden /> Nova build
                                        </button>
                                        {pickable.map((r, idx) => (
                                          <button key={idx} className="flex-picker-item"
                                            onClick={() => {
                                              const newRi = slot.roles.length;
                                              const copy = JSON.parse(JSON.stringify(r));
                                              copy.catalog_id = null;
                                              copy.flex_of = r.name || r.flex_of;
                                              copy.name = r.name ? `${r.name} (flex)` : "";
                                              updSlot(pi, si, s => ({ ...s, roles: [...s.roles, copy] }));
                                              setEditRi(newRi);
                                              setFlexMenu(null);
                                            }}>
                                            {r.equip.weapon?.id
                                              ? <img src={itemUrl(r.equip.weapon.id)} alt=""
                                                  style={{ width: 20, height: 20, flexShrink: 0 }}
                                                  onError={imgRetry(img => { img.style.opacity = "0.2"; })} />
                                              : <span style={{ width: 20, flexShrink: 0 }} />}
                                            <span className="flex-picker-name">
                                              {r.name || "Sem nome"}
                                              {r.flex_of && <span style={{ color: "var(--muted)", fontSize: 10 }}> (flex de {r.flex_of})</span>}
                                            </span>
                                            {r.fn && (() => { const ft = getFnDef(r.fn, fnTypes); return ft ? (
                                              <span className="rc-fn-badge" style={{
                                                background: ft.color + "25", color: ft.color, flexShrink: 0,
                                              }}>{fnLabel(ft)}</span>
                                            ) : null; })()}
                                            {r.equip_loaded && <EquipStrip equip={r.equip} />}
                                          </button>
                                        ))}
                                        {!pickable.length && (
                                          <span className="flex-picker-empty">Nenhuma outra build na comp</span>
                                        )}
                                      </div>
                                    );
                                  })()}

                                  {/* Flex alternatives */}
                                  {slot.roles.length > 1 && (
                                    <div style={{
                                      display: "flex", flexDirection: "column", gap: 4,
                                      paddingTop: 6, borderTop: "1px solid var(--border)",
                                    }}>
                                      <span className="equip-field-label">Alternativas (flex)</span>
                                      {slot.roles.slice(1).map((fr, fri) => (
                                        <div key={fri}
                                          style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                                          {fr.equip.weapon?.id && (
                                            <img src={itemUrl(fr.equip.weapon.id)} alt=""
                                              style={{ width: 18, height: 18, flexShrink: 0 }}
                                              onError={imgRetry(img => { img.style.opacity = "0.2"; })} />
                                          )}
                                          {fr.fn && (() => { const ft = getFnDef(fr.fn, fnTypes); return ft ? (
                                            <span className="rc-fn-badge" style={{ background: ft.color + "25", color: ft.color }}>
                                              {fnLabel(ft)}
                                            </span>
                                          ) : null; })()}
                                          <span style={{ flex: 1 }}>{fr.name || "—"}</span>
                                          {fr.equip_loaded && <EquipStrip equip={fr.equip} />}
                                          <button className="cs-xbtn"
                                            onClick={() => updSlot(pi, si, s => ({
                                              ...s, roles: s.roles.filter((_, i) => i !== fri + 1),
                                            }))}>
                                            <i className="ti ti-x" aria-hidden />
                                          </button>
                                        </div>
                                      ))}
                                    </div>
                                  )}
                                </>

                              ) : (

                                /* ── View detail ──────────────────────── */
                                <>
                                  {!role?.equip_loaded && (
                                    <p className="hint" style={{ fontSize: 12 }}>Carregando…</p>
                                  )}

                                  {slot.roles.map((r, ri) => {
                                    if (!r.equip_loaded) return null;
                                    const rWeapBase = r.equip.weapon?.id ? wBase(r.equip.weapon.id) : null;
                                    const rSpells = rWeapBase ? spellCache[rWeapBase] : undefined;
                                    const hasEquip = EQUIP_SLOTS.some(s => r.equip[s.key]);
                                    return (
                                      <Fragment key={ri}>
                                        {ri > 0 && (
                                          <div className="rc-ou-divider"><span>OU</span></div>
                                        )}
                                        <div>
                                          <RoleViewBlock r={r as DraftRole & { equip_loaded: true }} spells={rSpells} onTotal={ri === 0 ? setHistTotal : undefined} label={ri > 0 ? r.name : undefined} />
                                        </div>
                                        {ri === 0 && !hasEquip && !r.play_style && !r.obs && (
                                          <p className="hint" style={{ fontSize: 12 }}>
                                            Nenhum detalhe definido.{" "}
                                            {perms["comps.manage"] && (
                                              <button
                                                style={{ background: "none", border: "none", color: "var(--info)", cursor: "pointer", fontSize: 12, padding: 0 }}
                                                onClick={() => setEditing(true)}>
                                                Editar
                                              </button>
                                            )}
                                          </p>
                                        )}
                                      </Fragment>
                                    );
                                  })}
                                </>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}

                    {/* Add role button */}
                    {editing && (
                      <>
                        {addSlotMenu === pi && (() => {
                          const seen = new Set<string>();
                          const pickableSlots: { fn: string | null; roles: DraftRole[]; primary: DraftRole }[] = [];
                          for (const p of (draft?.parties ?? [])) {
                            for (const s of p.slots) {
                              const primary = s.roles[0];
                              if (!primary) continue;
                              const key = primary.catalog_id != null ? `id:${primary.catalog_id}` : `name:${primary.name}`;
                              if (!seen.has(key) && (primary.catalog_id != null || primary.name.trim())) {
                                seen.add(key);
                                pickableSlots.push({ fn: s.fn, roles: s.roles, primary });
                              }
                            }
                          }
                          return (
                            <div className="flex-picker-menu">
                              <button className="flex-picker-item flex-picker-new"
                                onClick={() => { addSlot(pi); setAddSlotMenu(null); }}>
                                <i className="ti ti-plus" aria-hidden /> Criar novo papel
                              </button>
                              {pickableSlots.map((item, idx) => (
                                <button key={idx} className="flex-picker-item"
                                  onClick={() => {
                                    if (!draft || draft.parties[pi].slots.length >= MAX_SLOTS) return;
                                    const newSlotIdx = draft.parties[pi].slots.length;
                                    const copiedRoles: DraftRole[] = JSON.parse(JSON.stringify(item.roles));
                                    copiedRoles.forEach(r => { r.catalog_id = null; });
                                    upd(d => ({
                                      ...d,
                                      parties: d.parties.map((p, i) =>
                                        i !== pi ? p : { ...p, slots: [...p.slots, { fn: item.fn, roles: copiedRoles }] }
                                      ),
                                    }));
                                    setOpenCard([pi, newSlotIdx]);
                                    setAddSlotMenu(null);
                                  }}>
                                  {item.primary.equip.weapon?.id
                                    ? <img src={itemUrl(item.primary.equip.weapon.id)} alt=""
                                        style={{ width: 20, height: 20, flexShrink: 0 }}
                                        onError={imgRetry(img => { img.style.opacity = "0.2"; })} />
                                    : <span style={{ width: 20, flexShrink: 0 }} />}
                                  <span className="flex-picker-name">{item.primary.name || "Sem nome"}</span>
                                  {item.fn && (() => { const ft = getFnDef(item.fn, fnTypes); return ft ? (
                                    <span className="rc-fn-badge" style={{ background: ft.color + "25", color: ft.color, flexShrink: 0 }}>
                                      {fnLabel(ft)}
                                    </span>
                                  ) : null; })()}
                                  {item.roles.length > 1 && (
                                    <span style={{ fontSize: 10, color: "var(--muted)", flexShrink: 0 }}>
                                      +{item.roles.length - 1} flex
                                    </span>
                                  )}
                                </button>
                              ))}
                              {!pickableSlots.length && (
                                <span className="flex-picker-empty">Nenhuma role na comp</span>
                              )}
                            </div>
                          );
                        })()}
                        <button className="party-col-add"
                          onClick={() => setAddSlotMenu(addSlotMenu === pi ? null : pi)}
                          disabled={party.slots.length >= MAX_SLOTS}>
                          <i className="ti ti-plus" aria-hidden />
                          {party.slots.length >= MAX_SLOTS ? "Cheio" : "Adicionar papel"}
                        </button>
                      </>
                    )}
                  </div>
                )}
              </div>
            );
          })}

          {/* Add party */}
          {editing && (
            <button className="party-col-add" onClick={addParty}>
              <i className="ti ti-plus" aria-hidden /> Nova party
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
