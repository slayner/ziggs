import { imgRetry } from "../../api";
import { useT } from "../../i18n";
import { SpellOverlay } from "./EquipGrid";
import { itemUrl, safeAltArr } from "./helpers";
import type { DraftEquip, EquipItem } from "./types";

const ALT_SLOTS_VIEW = ["offhand", "helmet", "armor", "boots", "cape"] as const;
function useAltSlotLabels(): Record<string, string> {
  const t = useT();
  return { offhand: t("cbSlotShieldAlt"), helmet: t("cbSlotHelmet"), armor: t("cbSlotArmor"), boots: t("cbSlotBoots"), cape: t("cbSlotCape") };
}

export function AltEquipSection({ equip, gearSpells, swapMap, onSwap, titleInSidebar }: {
  equip: DraftEquip;
  gearSpells?: Record<string, string | null>;
  swapMap: Record<string, number>;
  onSwap: (slot: string, altIdx: number) => void;
  titleInSidebar?: boolean;
}) {
  const t = useT();
  const ALT_SLOT_LABELS = useAltSlotLabels();
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
          {t("altEquipTitle")}
        </div>
      )}
      {items}
    </div>
  );
}
