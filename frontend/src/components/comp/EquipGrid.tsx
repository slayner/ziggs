import { imgRetry, type WeaponSpell } from "../../api";
import { type ItemSlot } from "../../data/albion-items";
import { useT } from "../../i18n";
import { itemUrl } from "./helpers";
import type { DraftEquip } from "./types";

const GRID_LAYOUT: (ItemSlot | null)[][] = [
  [null,     "helmet", "cape"   ],
  ["weapon", "armor",  "offhand"],
  ["potion", "boots",  "food"   ],
];

export function SpellOverlay({ ids, centered, row }: { ids: (string | null)[]; centered?: boolean; row?: boolean }) {
  const present = ids.filter(Boolean);
  if (!present.length) return null;
  return (
    <div className={"rc-equip-spell-overlay" + (centered ? " rc-equip-spell-overlay-center" : "") + (row ? " rc-equip-spell-overlay-row" : "")}>
      {present.map(id => (
        <div key={id} className="rc-equip-spell-dot">
          <img src={`/render/spell/${encodeURIComponent(id!)}`} alt=""
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
  const t = useT();
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
                <div key={ci} className="rc-equip-cell rc-equip-2h" title={t("twoHandedWeaponTitle")}>
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
