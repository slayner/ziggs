import { useState } from "react";
import { useT } from "../../i18n";
import { AltEquipSection } from "./AltEquipSection";
import type { DraftRole } from "./types";

// Coluna única (sem preview de skills, gráfico de preço nem EquipGrid —
// pedido explícito: tirar esse peso do modo Ver, deixar só texto + alt
// equipment, e usar o espaço que sobrou pra alargar a LISTA à esquerda).
export function RoleViewBlock({ r }: {
  r: DraftRole & { equip_loaded: true };
}) {
  const t = useT();
  const [swapMap, setSwapMap] = useState<Record<string, number>>({});

  function handleSwap(slot: string, altIdx: number) {
    setSwapMap(prev => {
      const n = { ...prev };
      if (n[slot] === altIdx) { delete n[slot]; } else { n[slot] = altIdx; }
      return n;
    });
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {r.play_style && (
        <div className="sd-section">
          <h4>{t("playStyleTitle")}</h4>
          <p>{r.play_style}</p>
        </div>
      )}
      {r.obs && (
        <div className="sd-section">
          <h4>{t("obsTitle")}</h4>
          <p>{r.obs}</p>
        </div>
      )}
      <AltEquipSection equip={r.equip} gearSpells={r.gear_spells}
        swapMap={swapMap} onSwap={handleSwap} />
    </div>
  );
}
