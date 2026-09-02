import { type CSSProperties } from "react";
import { imgRetry, type WeaponSpell } from "../../api";
import { SPELL_COLORS } from "./helpers";

export function SpellPicker({
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
        const spriteSrc = `/render/spell/${encodeURIComponent(spell.uisprite ?? spell.spell_id)}`;
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
