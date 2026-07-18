import { imgRetry } from "../../api";
import { itemUrl } from "./helpers";
import type { DraftEquip, EquipItem } from "./types";

// Arma | Offhand | Capacete+Armadura+Bota | Capa+Comida  (inline para caber em rc-head)
export function EquipStrip({ equip, weaponIs2H }: { equip: DraftEquip; weaponIs2H?: boolean }) {
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
