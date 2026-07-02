import type { ApiComp, ApiRole } from "./api";
import type { Lang } from "./i18n";

// Fallback usado SÓ se o backend estiver offline. Mesmo formato da API.
function role(p: Partial<ApiRole> & { id: number; name: string }): ApiRole {
  return {
    weapon_id: null, invisible_function: null, offhand: null, helmet: null,
    armor: null, boots: null, cape: null, food: null, abilities: null,
    play_style: null, obs: null, ...p,
  };
}

const MOCK_TEXT: Record<Lang, {
  compName: string; supportFlex: string; dpsFlex: string;
  names: [string, string, string, string, string];
  gear: { helmet: [string, string, string, string]; armor: [string, string, string, string]; boots: [string, string, string, string]; cape: [string, string, string, string]; food: [string, string, string, string] };
  playStyle: [string, string, string, string];
}> = {
  pt: {
    compName: "ZvZ Padrão (offline)", supportFlex: "Suporte (flex)", dpsFlex: "DPS (flex)",
    names: ["Bruxa", "Locus", "Oculto", "Cajado Sagrado", "Águia"],
    gear: {
      helmet: ["8.4 Capuz de Aço", "8.4 Capuz do Asceta", "8.4 Capuz do Clérigo", "8.4 Capuz de Mago"],
      armor: ["8.4 Armadura de Guardião", "8.4 Túnica do Clérigo", "8.4 Túnica do Clérigo", "8.4 Túnica de Mago"],
      boots: ["8.4 Botas de Aço", "8.4 Sandálias do Clérigo", "8.4 Sandálias do Clérigo", "8.4 Sapatos de Mago"],
      cape: ["8.3 Capa de Lymhurst", "8.3 Capa de Thetford", "8.3 Capa de Martlock", "8.3 Capa de Bridgewatch"],
      food: ["8.0 Ensopado de Carne", "8.0 Torta de Peixe", "8.0 Sopa de Algas", "8.0 Omelete"],
    },
    playStyle: ["Segura a frente", "Fica atrás do grupo", "Cura a zerg", "Foca aglomerados"],
  },
  en: {
    compName: "Standard ZvZ (offline)", supportFlex: "Support (flex)", dpsFlex: "DPS (flex)",
    names: ["Witch", "Locus", "Hidden", "Sacred Staff", "Eagle"],
    gear: {
      helmet: ["8.4 Steel Hood", "8.4 Ascetic Hood", "8.4 Cleric Hood", "8.4 Mage Hood"],
      armor: ["8.4 Guardian Armor", "8.4 Cleric Robe", "8.4 Cleric Robe", "8.4 Mage Robe"],
      boots: ["8.4 Steel Boots", "8.4 Cleric Sandals", "8.4 Cleric Sandals", "8.4 Mage Shoes"],
      cape: ["8.3 Lymhurst Cape", "8.3 Thetford Cape", "8.3 Martlock Cape", "8.3 Bridgewatch Cape"],
      food: ["8.0 Beef Stew", "8.0 Fish Pie", "8.0 Seaweed Soup", "8.0 Omelette"],
    },
    playStyle: ["Holds the frontline", "Stays behind the group", "Heals the zerg", "Focuses clustered targets"],
  },
  es: {
    compName: "ZvZ Estándar (offline)", supportFlex: "Soporte (flex)", dpsFlex: "DPS (flex)",
    names: ["Bruja", "Locus", "Oculto", "Bastón Sagrado", "Águila"],
    gear: {
      helmet: ["8.4 Capucha de Acero", "8.4 Capucha del Ascético", "8.4 Capucha del Clérigo", "8.4 Capucha de Mago"],
      armor: ["8.4 Armadura del Guardián", "8.4 Túnica del Clérigo", "8.4 Túnica del Clérigo", "8.4 Túnica de Mago"],
      boots: ["8.4 Botas de Acero", "8.4 Sandalias del Clérigo", "8.4 Sandalias del Clérigo", "8.4 Zapatos de Mago"],
      cape: ["8.3 Capa de Lymhurst", "8.3 Capa de Thetford", "8.3 Capa de Martlock", "8.3 Capa de Bridgewatch"],
      food: ["8.0 Estofado de Carne", "8.0 Pastel de Pescado", "8.0 Sopa de Algas", "8.0 Tortilla"],
    },
    playStyle: ["Sostiene el frente", "Se queda detrás del grupo", "Cura a la zerg", "Enfoca aglomeraciones"],
  },
};

let sid = 0;
const slot = (label: string, ...roles: ApiRole[]) => ({
  id: ++sid, position: 0, label, notes: null, fn: null, roles,
});

export function mockApiComp(lang: Lang): ApiComp {
  sid = 0;
  const tx = MOCK_TEXT[lang];
  const bruxa = role({ id: 1, name: tx.names[0], invisible_function: "tank",
    helmet: tx.gear.helmet[0], armor: tx.gear.armor[0],
    boots: tx.gear.boots[0], cape: tx.gear.cape[0],
    food: tx.gear.food[0], play_style: tx.playStyle[0] });
  const locus = role({ id: 2, name: tx.names[1], invisible_function: "support",
    helmet: tx.gear.helmet[1], armor: tx.gear.armor[1],
    boots: tx.gear.boots[1], cape: tx.gear.cape[1],
    food: tx.gear.food[1], play_style: tx.playStyle[1] });
  const oculto = role({ id: 3, name: tx.names[2], invisible_function: "support" });
  const sagrado = role({ id: 4, name: tx.names[3], invisible_function: "healer",
    helmet: tx.gear.helmet[2], armor: tx.gear.armor[2],
    boots: tx.gear.boots[2], cape: tx.gear.cape[2],
    food: tx.gear.food[2], play_style: tx.playStyle[2] });
  const aguia = role({ id: 5, name: tx.names[4], invisible_function: "dps_ranged",
    helmet: tx.gear.helmet[3], armor: tx.gear.armor[3],
    boots: tx.gear.boots[3], cape: tx.gear.cape[3],
    food: tx.gear.food[3], play_style: tx.playStyle[3] });

  return {
    id: 1, name: tx.compName, description: "fallback", archived: false,
    parties: [
      { id: 1, position: 0, name: "Party 1", slots: [
        slot("Tank", bruxa), slot(tx.supportFlex, locus, oculto),
        slot("Healer", sagrado), slot("DPS", aguia),
      ] },
      { id: 2, position: 1, name: "Party 2", slots: [
        slot("Tank", bruxa), slot(tx.supportFlex, oculto, locus),
        slot("Healer", sagrado), slot(tx.dpsFlex, aguia, oculto),
      ] },
    ],
  };
}
