// Helpers puros do comp builder (constantes, conversões, identidade de build,
// código de build) — extraído de CompBuilder.tsx (WS6 fase 1).
import { type ApiComp, type ApiRole, type RegearItem } from "../../api";
import { RENDER_URL, ITEM_BY_ID, itemRenderUrl, type ItemSlot } from "../../data/albion-items";
import { useT } from "../../i18n";
import type { CompCode, Draft, DraftEquip, DraftRole, EquipItem, FnTypeDef } from "./types";

export const MAX_SLOTS = 20;

export const DEFAULT_FN_TYPES: FnTypeDef[] = [
  { key: "tank",    label: "Tank",    color: "#1399FF", emoji: "🛡️" },
  { key: "healer",  label: "Healer",  color: "#43B80E", emoji: "🕊️" },
  { key: "support", label: "Suporte", color: "#FFE04D", emoji: "✨" },
  { key: "dps",     label: "DPS",     color: "#FF2025", emoji: "🏹" },
  { key: "pierce",  label: "Pierce",  color: "#06b6d4", emoji: "🎯" },
];

export function getFnDef(fn: string | null, types: FnTypeDef[]): FnTypeDef | undefined {
  if (!fn) return undefined;
  return types.find(t => t.key === fn);
}
export function fnLabel(ft: FnTypeDef): string {
  return ft.emoji ? `${ft.emoji} ${ft.label}` : ft.label;
}

export function useEquipSlots(): { key: ItemSlot; label: string }[] {
  const t = useT();
  return [
    { key: "weapon",  label: t("cbSlotWeapon") },
    { key: "offhand", label: "Off-hand" },
    { key: "helmet",  label: t("cbSlotHelmet") },
    { key: "armor",   label: t("cbSlotArmor") },
    { key: "boots",   label: t("cbSlotBoots") },
    { key: "cape",    label: t("cbSlotCape") },
    { key: "food",    label: t("cbSlotFood") },
    { key: "potion",  label: t("cbSlotPotion") },
  ] as { key: ItemSlot; label: string }[];
}

export const ALT_CAPABLE = new Set(["offhand", "helmet", "armor", "boots", "cape"]);

export function safeAltArr(v: unknown): EquipItem[] {
  if (!v) return [];
  if (Array.isArray(v)) return v.filter((x): x is EquipItem => !!(x?.id));
  if (typeof v === "object" && (v as EquipItem).id) return [v as EquipItem];
  return [];
}

export const SPELL_COLORS: Record<string, string> = {
  Q: "#5b8def", W: "#a26dbf", passive: "#d4a832", active: "#5b8def",
};

export function itemUrl(id: string, quality = 0): string {
  const item = ITEM_BY_ID.get(id);
  return item ? itemRenderUrl(item, quality) : RENDER_URL(id, quality);
}

// ── Conversions ──────────────────────────────────────────────
export function buildItemsToEquip(items: RegearItem[]): DraftEquip {
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

export function apiRoleToDraft(r: ApiRole): DraftRole {
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

export function compToDraft(c: ApiComp): Draft {
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

export function emptyRole(): DraftRole {
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

export function roleToPayload(role: DraftRole) {
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

// ── Identidade de build ────────────────────────────────────────
// ID de uma build = os itens de verdade equipados (weapon/offhand/gear/
// consumíveis), não o nome — duas roles com nome igual e gear diferente são
// builds DIFERENTES; usado pra deduplicar sugestões de cópia (getPickableRoles).
export function buildSignature(equip: DraftEquip): string {
  const slots: (keyof DraftEquip)[] = ["weapon", "offhand", "helmet", "armor", "boots", "cape", "food", "potion"];
  return slots.map(k => (equip[k] as EquipItem | undefined)?.id ?? "").join("|");
}

export function roleIdentityKey(r: DraftRole): string {
  return r.catalog_id != null ? `id:${r.catalog_id}` : `build:${r.weapon_db_id ?? ""}:${buildSignature(r.equip)}`;
}

export function encodeCompCode(draft: Draft): string {
  const payload: CompCode = {
    v: 1,
    parties: draft.parties.map(p => ({
      name: p.name,
      slots: p.slots.map(s => ({
        fn: s.fn,
        roles: s.roles.map((r: DraftRole) => ({
          name: r.name, fn: r.fn, equip: r.equip,
          q_spell: r.q_spell, w_spell: r.w_spell, passive_spell: r.passive_spell,
          gear_spells: r.gear_spells, play_style: r.play_style, abilities: r.abilities,
          potion_qty: r.potion_qty, food_qty: r.food_qty,
          ...(r.flex_of ? { flex_of: r.flex_of } : {}),
        })),
      })),
    })),
  };
  return btoa(encodeURIComponent(JSON.stringify(payload)));
}

export function decodeCompCode(code: string): CompCode | null {
  try {
    const obj = JSON.parse(decodeURIComponent(atob(code.trim())));
    return obj && obj.v === 1 && Array.isArray(obj.parties) ? obj as CompCode : null;
  } catch {
    return null;
  }
}
