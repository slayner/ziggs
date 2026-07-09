import type { ApiComp, ApiRole, RegearItem } from "./api";
import type { Lang } from "./i18n";

// Fallback usado SÓ se o backend estiver offline. Mesmo formato da API.
// Builds com item_id REAL do Albion pra demo mostrar a experiência completa
// (ícones, grade de equipamento, alts, gráfico de preço) — não só texto.
function role(p: Partial<ApiRole> & { id: number; name: string }): ApiRole {
  return {
    weapon_id: null, invisible_function: null, offhand: null, helmet: null,
    armor: null, boots: null, cape: null, food: null, abilities: null,
    play_style: null, obs: null, ...p,
  };
}

const bi = (slot: string, item_id: string, name: string, quantity = 1): RegearItem =>
  ({ slot, item_id, name, quality: 1, quantity });

const MOCK_TEXT: Record<Lang, {
  compName: string;
  names: [string, string, string, string, string];
  playStyle: [string, string, string, string, string];
}> = {
  pt: {
    compName: "ZvZ Padrão (offline)",
    names: ["Mão da Justiça", "Local Malévolo", "Cajado Oculto", "Queda Santa", "Sincelo"],
    playStyle: [
      "Segura a frente e abre espaço pro engage",
      "Fica atrás do grupo, purga e rouba buff",
      "Silencia a backline inimiga no engage",
      "Cura a zerg agrupada",
      "Foca aglomerados no clump inimigo",
    ],
  },
  en: {
    compName: "Standard ZvZ (offline)",
    names: ["Hand of Justice", "Malevolent Locus", "Occult Staff", "Hallowfall", "Icicle"],
    playStyle: [
      "Holds the frontline and opens engages",
      "Stays behind the group, purges and steals buffs",
      "Silences the enemy backline on engage",
      "Heals the clumped zerg",
      "Focuses clustered targets in the enemy clump",
    ],
  },
  es: {
    compName: "ZvZ Estándar (offline)",
    names: ["Mano de la Justicia", "Locus Malévolo", "Bastón Oculto", "Caída Sagrada", "Carámbano"],
    playStyle: [
      "Sostiene el frente y abre el engage",
      "Se queda detrás del grupo, purga y roba buffs",
      "Silencia la backline enemiga en el engage",
      "Cura a la zerg agrupada",
      "Enfoca aglomeraciones en el clump enemigo",
    ],
  },
};

let sid = 0;
const slot = (label: string, fn: string | null, ...roles: ApiRole[]) => ({
  id: ++sid, position: 0, label, notes: null, fn, roles,
});

export function mockApiComp(lang: Lang): ApiComp {
  sid = 0;
  const tx = MOCK_TEXT[lang];

  const tank = role({
    id: 1, name: tx.names[0], invisible_function: "tank", play_style: tx.playStyle[0],
    build_items: [
      bi("weapon", "T8_2H_HAMMER_AVALON", "Mão da Justiça"),
      bi("helmet", "T8_HEAD_PLATE_SET3@2", "8.2 Capacete do Guardião"),
      bi("helmet_alt_0", "T8_HEAD_PLATE_SET2@2", "8.2 Capacete do Cavaleiro"),
      bi("armor", "T8_ARMOR_PLATE_SET3@2", "8.2 Armadura do Guardião"),
      bi("boots", "T8_SHOES_PLATE_SET3@2", "8.2 Botas do Guardião"),
      bi("cape", "T8_CAPE_MARTLOCK@2", "8.2 Capa de Martlock"),
      bi("food", "T8_MEAL_STEW@1", "8.1 Ensopado", 2),
      bi("potion", "T7_POTION_REVIVE", "7.0 Gigantify", 10),
    ],
  });
  const locus = role({
    id: 2, name: tx.names[1], invisible_function: "support", play_style: tx.playStyle[1],
    build_items: [
      bi("weapon", "T8_2H_ENIGMATICORB_MORGANA", "Local Malévolo"),
      bi("helmet", "T8_HEAD_CLOTH_SET2@2", "8.2 Capuz do Clérigo"),
      bi("armor", "T8_ARMOR_CLOTH_SET2@2", "8.2 Túnica do Clérigo"),
      bi("boots", "T8_SHOES_CLOTH_SET2@2", "8.2 Sandálias do Clérigo"),
      bi("cape", "T8_CAPE_THETFORD@2", "8.2 Capa de Thetford"),
      bi("food", "T8_MEAL_PIE_FISH@1", "8.1 Torta de Peixe", 2),
      bi("potion", "T7_POTION_REVIVE", "7.0 Gigantify", 10),
    ],
  });
  const oculto = role({
    id: 3, name: tx.names[2], invisible_function: "support", play_style: tx.playStyle[2],
    build_items: [
      bi("weapon", "T8_2H_ARCANESTAFF_HELL", "Cajado Oculto"),
      bi("helmet", "T8_HEAD_CLOTH_SET2@2", "8.2 Capuz do Clérigo"),
      bi("armor", "T8_ARMOR_CLOTH_SET2@2", "8.2 Túnica do Clérigo"),
      bi("boots", "T8_SHOES_CLOTH_SET2@2", "8.2 Sandálias do Clérigo"),
      bi("cape", "T8_CAPE_THETFORD@2", "8.2 Capa de Thetford"),
      bi("food", "T8_MEAL_PIE_FISH@1", "8.1 Torta de Peixe", 2),
      bi("potion", "T7_POTION_REVIVE", "7.0 Gigantify", 10),
    ],
  });
  const healer = role({
    id: 4, name: tx.names[3], invisible_function: "healer", play_style: tx.playStyle[3],
    build_items: [
      bi("weapon", "T8_MAIN_HOLYSTAFF_AVALON", "Queda Santa"),
      bi("offhand", "T8_OFF_TOTEM_KEEPER", "Raiz Mestra"),
      bi("helmet", "T8_HEAD_CLOTH_SET2@2", "8.2 Capuz do Clérigo"),
      bi("armor", "T8_ARMOR_CLOTH_SET2@2", "8.2 Túnica do Clérigo"),
      bi("boots", "T8_SHOES_CLOTH_SET2@2", "8.2 Sandálias do Clérigo"),
      bi("boots_alt_0", "T8_SHOES_PLATE_SET2@2", "8.2 Botas do Cavaleiro"),
      bi("cape", "T8_CAPE_LYMHURST@2", "8.2 Capa de Lymhurst"),
      bi("food", "T8_MEAL_STEW_AVALON@1", "8.1 Ensopado Avaloniano", 2),
      bi("potion", "T7_POTION_REVIVE", "7.0 Gigantify", 10),
    ],
  });
  const dps = role({
    id: 5, name: tx.names[4], invisible_function: "dps", play_style: tx.playStyle[4],
    build_items: [
      bi("weapon", "T8_2H_ICEGAUNTLETS_HELL", "Cajado de Sincelo"),
      bi("helmet", "T8_HEAD_CLOTH_SET3@2", "8.2 Capuz do Mago"),
      bi("armor", "T8_ARMOR_CLOTH_SET3@2", "8.2 Túnica do Mago"),
      bi("boots", "T8_SHOES_CLOTH_SET3@2", "8.2 Sapatos do Mago"),
      bi("cape", "T8_CAPE_BRIDGEWATCH@2", "8.2 Capa de Bridgewatch"),
      bi("food", "T8_MEAL_OMELETTE@1", "8.1 Omelete", 2),
      bi("potion", "T7_POTION_REVIVE", "7.0 Gigantify", 10),
    ],
  });

  return {
    id: 1, name: tx.compName, description: "fallback", archived: false,
    parties: [
      { id: 1, position: 0, name: "Party 1", slots: [
        slot("Tank", "tank", tank),
        slot("Suporte", "support", locus, oculto),
        slot("Healer", "healer", healer),
        slot("DPS", "dps", dps),
      ] },
      { id: 2, position: 1, name: "Party 2", slots: [
        slot("Tank", "tank", tank),
        slot("Suporte", "support", oculto, locus),
        slot("Healer", "healer", healer),
        slot("DPS", "dps", dps),
      ] },
    ],
  };
}
