import EN_NAMES from "./en-names.json";

export type ItemSlot = "weapon" | "offhand" | "helmet" | "armor" | "boots" | "cape" | "food" | "potion";

export interface AlbionItem {
  id: string;
  name: string;
  nameEn?: string;
  useEnRender?: true; // só crystal weapons (artAll) usam o render por nome EN
  slot: ItemSlot;
}

// Tier prefix para render URL por nome EN
const TIER_PREFIX: Record<number, string> = {
  4: "Adept's", 5: "Expert's", 6: "Master's", 7: "Grandmaster's", 8: "Elder's",
};

// Gera variantes de tier × encantamento para um item base.
function gen(
  tiers: readonly number[],
  enchants: readonly number[],
  baseId: string,
  baseName: string,
  slot: ItemSlot,
  nameEn?: string,
  useEnRender?: true,
): AlbionItem[] {
  const resolvedNameEn = nameEn ?? (EN_NAMES as Record<string, string>)[baseId];
  return tiers.flatMap(t =>
    enchants.map(e => ({
      id:   e === 0 ? `T${t}_${baseId}` : `T${t}_${baseId}@${e}`,
      name: `${t}.${e} ${baseName}`,
      nameEn: resolvedNameEn,
      slot,
      ...(useEnRender ? { useEnRender: true as const } : {}),
    }))
  );
}

const T = [4, 5, 6, 7, 8] as const;
const E = [0, 1, 2, 3, 4] as const;

// Artefato — T4-T8, todos os tiers
function art(baseId: string, name: string, slot: ItemSlot, nameEn?: string) {
  return gen(T, E, baseId, name, slot, nameEn);
}

// Artefato crystal — T4-T8, usa render por nome EN
function artAll(baseId: string, name: string, slot: ItemSlot, nameEn?: string): AlbionItem[] {
  return gen(T, E, baseId, name, slot, nameEn).map(i => ({ ...i, useEnRender: true as const }));
}

export const ALBION_ITEMS: AlbionItem[] = [

  // ═══════════════════════════════════════════════════════════
  // CAPACETES
  // ═══════════════════════════════════════════════════════════

  // ─── Placa ────────────────────────────────────────────────
  ...gen(T, E, "HEAD_PLATE_SET1", "Capacete do Soldado",   "helmet"),
  ...gen(T, E, "HEAD_PLATE_SET2", "Capacete do Cavaleiro",  "helmet"),
  ...gen(T, E, "HEAD_PLATE_SET3", "Capacete do Guardião",   "helmet"),
  ...art("HEAD_PLATE_HELL",     "Elmo Infernal",                  "helmet"),
  ...art("HEAD_PLATE_KEEPER",   "Elmo do Guardião da Natureza",   "helmet"),
  ...art("HEAD_PLATE_MORGANA",  "Elmo de Morgana",                "helmet"),
  ...art("HEAD_PLATE_UNDEAD",   "Elmo dos Mortos-Vivos",          "helmet"),
  ...art("HEAD_PLATE_ROYAL",    "Elmo Real",                      "helmet"),
  ...art("HEAD_PLATE_AVALON",   "Elmo de Avalon",                 "helmet"),
  ...art("HEAD_PLATE_CAERLEON", "Elmo de Caerleon",               "helmet"),
  ...art("HEAD_PLATE_FEY",     "Elmo Duskweaver",                "helmet"),

  // ─── Pano ─────────────────────────────────────────────────
  ...gen(T, E, "HEAD_CLOTH_SET1", "Capuz do Erudito",   "helmet"),
  ...gen(T, E, "HEAD_CLOTH_SET2", "Capuz do Clérigo",   "helmet"),
  ...gen(T, E, "HEAD_CLOTH_SET3", "Capuz do Mago",      "helmet"),
  ...art("HEAD_CLOTH_HELL",     "Capuz Infernal do Mago",          "helmet"),
  ...art("HEAD_CLOTH_KEEPER",   "Capuz do Druida",                 "helmet"),
  ...art("HEAD_CLOTH_MORGANA",  "Capuz de Morgana",                "helmet"),
  ...art("HEAD_CLOTH_UNDEAD",   "Capuz dos Mortos-Vivos",          "helmet"),
  ...art("HEAD_CLOTH_ROYAL",    "Capuz Real do Mago",              "helmet"),
  ...art("HEAD_CLOTH_AVALON",   "Capuz de Avalon (Pano)",          "helmet"),
  ...art("HEAD_CLOTH_CAERLEON", "Capuz de Caerleon (Pano)",        "helmet"),
  ...art("HEAD_CLOTH_FEY",     "Capuz Feyscale",                  "helmet"),

  // ─── Couro ────────────────────────────────────────────────
  ...gen(T, E, "HEAD_LEATHER_SET1", "Capuz do Mercenário",  "helmet"),
  ...gen(T, E, "HEAD_LEATHER_SET2", "Capuz do Caçador",     "helmet"),
  ...gen(T, E, "HEAD_LEATHER_SET3", "Capuz do Assassino",   "helmet"),
  ...art("HEAD_LEATHER_HELL",     "Capuz Infernal do Caçador",     "helmet"),
  ...art("HEAD_LEATHER_KEEPER",   "Capuz do Guardião (Couro)",     "helmet"),
  ...art("HEAD_LEATHER_MORGANA",  "Capuz de Morgana (Couro)",      "helmet"),
  ...art("HEAD_LEATHER_UNDEAD",   "Capuz dos Mortos-Vivos (Couro)","helmet"),
  ...art("HEAD_LEATHER_ROYAL",    "Capuz Real do Caçador",         "helmet"),
  ...art("HEAD_LEATHER_AVALON",   "Capuz de Avalon (Couro)",       "helmet"),
  ...art("HEAD_LEATHER_CAERLEON", "Capuz de Caerleon (Couro)",     "helmet"),
  ...art("HEAD_LEATHER_FEY",     "Capuz Mistwalker",              "helmet"),

  // ═══════════════════════════════════════════════════════════
  // ARMADURAS
  // ═══════════════════════════════════════════════════════════

  // ─── Placa ────────────────────────────────────────────────
  ...gen(T, E, "ARMOR_PLATE_SET1", "Armadura do Soldado",   "armor"),
  ...gen(T, E, "ARMOR_PLATE_SET2", "Armadura do Cavaleiro",  "armor"),
  ...gen(T, E, "ARMOR_PLATE_SET3", "Armadura do Guardião",   "armor"),
  ...art("ARMOR_PLATE_HELL",     "Armadura Infernal",                    "armor"),
  ...art("ARMOR_PLATE_KEEPER",   "Armadura do Guardião da Natureza",     "armor"),
  ...art("ARMOR_PLATE_MORGANA",  "Armadura de Morgana",                  "armor"),
  ...art("ARMOR_PLATE_UNDEAD",   "Armadura dos Mortos-Vivos",            "armor"),
  ...art("ARMOR_PLATE_ROYAL",    "Armadura Real",                        "armor"),
  ...art("ARMOR_PLATE_AVALON",   "Armadura de Avalon",                   "armor"),
  ...art("ARMOR_PLATE_CAERLEON", "Armadura de Caerleon",                 "armor"),
  ...art("ARMOR_PLATE_FEY",     "Armadura Duskweaver",                  "armor"),

  // ─── Pano ─────────────────────────────────────────────────
  ...gen(T, E, "ARMOR_CLOTH_SET1", "Túnica do Erudito",   "armor"),
  ...gen(T, E, "ARMOR_CLOTH_SET2", "Túnica do Clérigo",   "armor"),
  ...gen(T, E, "ARMOR_CLOTH_SET3", "Túnica do Mago",      "armor"),
  ...art("ARMOR_CLOTH_HELL",     "Túnica Infernal",                      "armor"),
  ...art("ARMOR_CLOTH_KEEPER",   "Túnica do Druida",                     "armor"),
  ...art("ARMOR_CLOTH_MORGANA",  "Túnica de Morgana",                    "armor"),
  ...art("ARMOR_CLOTH_UNDEAD",   "Túnica dos Mortos-Vivos",              "armor"),
  ...art("ARMOR_CLOTH_ROYAL",    "Túnica Real",                          "armor"),
  ...art("ARMOR_CLOTH_AVALON",   "Túnica de Avalon",                     "armor"),
  ...art("ARMOR_CLOTH_CAERLEON", "Túnica de Caerleon",                   "armor"),
  ...art("ARMOR_CLOTH_FEY",     "Túnica Feyscale",                      "armor"),

  // ─── Couro ────────────────────────────────────────────────
  ...gen(T, E, "ARMOR_LEATHER_SET1", "Jaqueta do Mercenário",  "armor"),
  ...gen(T, E, "ARMOR_LEATHER_SET2", "Jaqueta do Caçador",     "armor"),
  ...gen(T, E, "ARMOR_LEATHER_SET3", "Jaqueta do Assassino",   "armor"),
  ...art("ARMOR_LEATHER_HELL",     "Jaqueta Infernal",                   "armor"),
  ...art("ARMOR_LEATHER_KEEPER",   "Jaqueta do Guardião da Natureza",    "armor"),
  ...art("ARMOR_LEATHER_MORGANA",  "Jaqueta de Morgana",                 "armor"),
  ...art("ARMOR_LEATHER_UNDEAD",   "Jaqueta dos Mortos-Vivos",           "armor"),
  ...art("ARMOR_LEATHER_ROYAL",    "Jaqueta Real",                       "armor"),
  ...art("ARMOR_LEATHER_AVALON",   "Jaqueta de Avalon",                  "armor"),
  ...art("ARMOR_LEATHER_CAERLEON", "Jaqueta de Caerleon",                "armor"),
  ...art("ARMOR_LEATHER_FEY",     "Jaqueta Mistwalker",                 "armor"),

  // ═══════════════════════════════════════════════════════════
  // BOTAS
  // ═══════════════════════════════════════════════════════════

  // ─── Placa ────────────────────────────────────────────────
  ...gen(T, E, "SHOES_PLATE_SET1", "Botas do Soldado",   "boots"),
  ...gen(T, E, "SHOES_PLATE_SET2", "Botas do Cavaleiro",  "boots"),
  ...gen(T, E, "SHOES_PLATE_SET3", "Botas do Guardião",   "boots"),
  ...art("SHOES_PLATE_HELL",     "Botas Infernais (Placa)",          "boots"),
  ...art("SHOES_PLATE_KEEPER",   "Botas do Guardião da Natureza",    "boots"),
  ...art("SHOES_PLATE_MORGANA",  "Botas de Morgana (Placa)",         "boots"),
  ...art("SHOES_PLATE_UNDEAD",   "Botas dos Mortos-Vivos (Placa)",   "boots"),
  ...art("SHOES_PLATE_ROYAL",    "Botas Reais (Placa)",              "boots"),
  ...art("SHOES_PLATE_AVALON",   "Botas de Avalon (Placa)",          "boots"),
  ...art("SHOES_PLATE_CAERLEON", "Botas de Caerleon (Placa)",        "boots"),
  ...art("SHOES_PLATE_FEY",     "Botas Duskweaver",                 "boots"),

  // ─── Pano ─────────────────────────────────────────────────
  ...gen(T, E, "SHOES_CLOTH_SET1", "Sandálias do Erudito",   "boots"),
  ...gen(T, E, "SHOES_CLOTH_SET2", "Sandálias do Clérigo",   "boots"),
  ...gen(T, E, "SHOES_CLOTH_SET3", "Sandálias do Mago",      "boots"),
  ...art("SHOES_CLOTH_HELL",     "Sandálias Infernais",              "boots"),
  ...art("SHOES_CLOTH_KEEPER",   "Sandálias do Druida",              "boots"),
  ...art("SHOES_CLOTH_MORGANA",  "Sandálias de Morgana",             "boots"),
  ...art("SHOES_CLOTH_UNDEAD",   "Sandálias dos Mortos-Vivos",       "boots"),
  ...art("SHOES_CLOTH_ROYAL",    "Sandálias Reais",                  "boots"),
  ...art("SHOES_CLOTH_AVALON",   "Sandálias de Avalon",              "boots"),
  ...art("SHOES_CLOTH_CAERLEON", "Sandálias de Caerleon",            "boots"),
  ...art("SHOES_CLOTH_FEY",     "Sandálias Feyscale",               "boots"),

  // ─── Couro ────────────────────────────────────────────────
  ...gen(T, E, "SHOES_LEATHER_SET1", "Sapatos do Mercenário",  "boots"),
  ...gen(T, E, "SHOES_LEATHER_SET2", "Sapatos do Caçador",     "boots"),
  ...gen(T, E, "SHOES_LEATHER_SET3", "Sapatos do Assassino",   "boots"),
  ...art("SHOES_LEATHER_HELL",     "Sapatos Infernais (Couro)",      "boots"),
  ...art("SHOES_LEATHER_KEEPER",   "Sapatos do Guardião (Couro)",    "boots"),
  ...art("SHOES_LEATHER_MORGANA",  "Sapatos de Morgana (Couro)",     "boots"),
  ...art("SHOES_LEATHER_UNDEAD",   "Sapatos dos Mortos-Vivos (Couro)","boots"),
  ...art("SHOES_LEATHER_ROYAL",    "Sapatos Reais (Couro)",          "boots"),
  ...art("SHOES_LEATHER_AVALON",   "Sapatos de Avalon (Couro)",      "boots"),
  ...art("SHOES_LEATHER_CAERLEON", "Sapatos de Caerleon (Couro)",    "boots"),
  ...art("SHOES_LEATHER_FEY",     "Sapatos Mistwalker",             "boots"),

  // ═══════════════════════════════════════════════════════════
  // OFFHANDS
  // ═══════════════════════════════════════════════════════════

  // ─── Escudos ──────────────────────────────────────────────
  ...gen(T, E, "OFF_SHIELD",           "Escudo",             "offhand"),
  ...art("OFF_SHIELD_HELL",            "Escudo Vampírico",   "offhand"),
  ...art("OFF_SPIKEDSHIELD_MORGANA",   "Quebra-rostos",      "offhand"),
  ...art("OFF_TOWERSHIELD_UNDEAD",     "Sarcófago",          "offhand"),
  ...art("OFF_SHIELD_AVALON",          "Égide Astral",       "offhand"),
  ...artAll("OFF_SHIELD_CRYSTAL",      "Barreira Inquebrantável", "offhand", "Unbreakable Ward"),

  // ─── Tomos ────────────────────────────────────────────────
  ...gen(T, E, "OFF_BOOK",      "Tomo de Feitiços",    "offhand"),
  ...art("OFF_BOOK_HELL",       "Tomo Infernal",        "offhand"),
  ...art("OFF_BOOK_MORGANA",    "Tomo de Morgana",      "offhand"),
  ...art("OFF_BOOK_UNDEAD",     "Tomo dos Mortos-Vivos","offhand"),
  ...art("OFF_BOOK_AVALON",     "Tomo de Avalon",       "offhand"),
  ...artAll("OFF_TOME_CRYSTAL", "Grimório Estagnado",   "offhand", "Timelocked Grimoire"),

  // ─── Outros ───────────────────────────────────────────────
  ...gen(T, E, "OFF_TORCH",           "Tocha",              "offhand"),
  ...art("OFF_JESTERCANE_HELL",        "Bengala Maligna",    "offhand"),
  ...art("OFF_DEMONSKULL_HELL",        "Muisec",             "offhand"),
  ...art("OFF_HORN_KEEPER",            "Brumário",           "offhand"),
  ...art("OFF_LAMP_UNDEAD",            "Lume Críptico",      "offhand"),
  ...art("OFF_ORB_MORGANA",            "Olho dos Segredos",  "offhand"),
  ...art("OFF_CENSER_AVALON",          "Incensário Celeste", "offhand"),
  ...art("OFF_TALISMAN_AVALON",        "Cetro Sagrado",      "offhand"),
  ...art("OFF_TOTEM_KEEPER",           "Raiz Mestra",        "offhand"),
  ...artAll("OFF_TORCH_CRYSTAL",       "Tocha Chama Azul",    "offhand", "Blueflame Torch"),
  ...artAll("OFF_SHIELD_CRYSTAL",      "Escudo Inquebrável",  "offhand", "Unbreakable Ward"),
  ...artAll("OFF_TOME_CRYSTAL",        "Grimório Bloqueado",  "offhand", "Timelocked Grimoire"),

  // ═══════════════════════════════════════════════════════════
  // CAPAS
  // ═══════════════════════════════════════════════════════════

  ...gen(T, [0,1,2,3], "CAPE",             "Capa Padrão",           "cape", "Cape",               true),
  ...gen(T, [0,1,2,3], "CAPE_BRIDGEWATCH", "Capa de Bridgewatch",  "cape", "Bridgewatch Cape",   true),
  ...gen(T, [0,1,2,3], "CAPE_CAERLEON",    "Capa de Caerleon",     "cape", "Caerleon Cape",      true),
  ...gen(T, [0,1,2,3], "CAPE_FORTSTERLING","Capa de Fort Sterling", "cape", "Fort Sterling Cape", true),
  ...gen(T, [0,1,2,3], "CAPE_LYMHURST",    "Capa de Lymhurst",     "cape", "Lymhurst Cape",      true),
  ...gen(T, [0,1,2,3], "CAPE_MARTLOCK",    "Capa de Martlock",     "cape", "Martlock Cape",      true),
  ...gen(T, [0,1,2,3], "CAPE_THETFORD",    "Capa de Thetford",     "cape", "Thetford Cape",      true),
  ...gen(T, [0,1,2,3], "CAPE_HELL",    "Capa Infernal",         "cape", "Demon Cape",         true),
  ...gen(T, [0,1,2,3], "CAPE_KEEPER",  "Capa Guardiã",          "cape", "Keeper Cape",        true),
  ...gen(T, [0,1,2,3], "CAPE_MORGANA", "Capa de Morgana",       "cape", "Morgana Cape",       true),
  ...gen(T, [0,1,2,3], "CAPE_UNDEAD",  "Capa dos Mortos-Vivos", "cape", "Undead Cape",        true),
  ...gen(T, [0,1,2,3], "CAPE_ROYAL",   "Capa Real",             "cape", "Royal Cape",         true),
  ...gen(T, [0,1,2,3], "CAPE_AVALON",  "Capa de Avalon",        "cape", "Avalonian Cape",     true),
  ...gen(T, [0,1,2,3], "CAPEITEM_SMUGGLER", "Capa Contrabandista", "cape", "Smuggler Cape",      true),

  // ═══════════════════════════════════════════════════════════
  // COMIDA
  // ═══════════════════════════════════════════════════════════

  ...gen(T, [0,1,2,3], "MEAL_GRILLEDFISH",      "Peixe Grelhado",     "food", "Grilled Fish"),
  ...gen(T, [0,1,2,3], "MEAL_SEAWEEDSALAD",    "Salada de Algas",    "food", "Seaweed Salad"),
  ...gen(T, [0,1,2,3], "MEAL_SOUP",            "Sopa",               "food", "Wheat Soup Cabbage Soup"),
  ...gen(T, [0,1,2,3], "MEAL_SOUP_FISH",       "Sopa de Peixe",      "food", "Blackbog Clam Soup Fish Soup"),
  ...gen(T, [0,1,2,3], "MEAL_SALAD",           "Salada",             "food", "Turnip Salad Potato Salad"),
  ...gen(T, [0,1,2,3], "MEAL_SALAD_FISH",      "Salada de Peixe",    "food", "Midwater Octopus Salad Deepwater Kraken Salad"),
  ...gen(T, [0,1,2,3], "MEAL_PIE",             "Torta",              "food", "Chicken Pie Goose Pie Pork Pie"),
  ...gen(T, [0,1,2,3], "MEAL_PIE_FISH",        "Torta de Peixe",     "food", "Fish Pie Frostpeak Deadeye Pie Mountain Blindeye Pie"),
  ...gen(T, [0,1,2,3], "MEAL_OMELETTE",        "Omelete",            "food", "Chicken Omelette Goose Omelette Pork Omelette"),
  ...gen(T, [0,1,2,3], "MEAL_OMELETTE_FISH",   "Omelete de Caranguejo", "food", "Crab Omelette Dusthole Drybrook"),
  ...gen(T, [0,1,2,3], "MEAL_OMELETTE_AVALON", "Omelete Avaloniana", "food", "Avalonian Omelette Avalonian Pork Omelette Avalonian Goose Omelette"),
  ...gen(T, [0,1,2,3], "MEAL_STEW",            "Ensopado",           "food", "Goat Stew Mutton Stew Beef Stew"),
  ...gen(T, [0,1,2,3], "MEAL_STEW_FISH",       "Ensopado de Enguia", "food", "Eel Stew Greenriver Redspring Deadwater"),
  ...gen(T, [0,1,2,3], "MEAL_STEW_AVALON",     "Ensopado Avaloniano","food", "Avalonian Goat Stew Avalonian Mutton Stew Avalonian Beef Stew"),
  ...gen(T, [0,1,2,3], "MEAL_SANDWICH",        "Sanduíche",          "food", "Goat Sandwich Mutton Sandwich Beef Sandwich"),
  ...gen(T, [0,1,2,3], "MEAL_SANDWICH_FISH",   "Sanduíche de Peixe", "food", "Lurcher Sandwich Stonestream Rushwater Thunderfall"),
  ...gen(T, [0,1,2,3], "MEAL_SANDWICH_AVALON", "Sanduíche Avaloniano","food", "Avalonian Goat Sandwich Avalonian Mutton Sandwich Avalonian Beef Sandwich"),
  ...gen(T, [0,1,2,3], "MEAL_ROAST",           "Assado",             "food", "Roast Chicken Roast Pork Roast Goose"),
  ...gen(T, [0,1,2,3], "MEAL_ROAST_FISH",      "Peixe Assado",       "food", "Roasted Snapper Puremist Clearhaze"),

  // ═══════════════════════════════════════════════════════════
  // POÇÕES
  // ═══════════════════════════════════════════════════════════

  ...gen(T, [0], "POTION_REVIVE",    "Gigantify",              "potion", "Gigantify Potion"),
  ...gen(T, [0], "POTION_STONESKIN", "Resistência",            "potion", "Resistance Potion"),
  ...gen(T, [0], "POTION_BERSERK",   "Berserk",                "potion", "Berserk Potion"),
  ...gen(T, [0], "POTION_CLEANSE",   "Invisibilidade",         "potion", "Invisibility Potion"),
  ...gen(T, [0], "POTION_CLEANSE2",  "Limpeza",                "potion", "Cleansing Potion"),
  ...gen(T, [0], "POTION_ENERGY",    "Energia",                "potion", "Energy Potion"),
  ...gen(T, [0], "POTION_HEAL",      "Cura",                   "potion", "Healing Potion"),
  ...gen(T, [0], "POTION_LAVA",      "Hellfire",               "potion", "Hellfire Potion"),
  ...gen(T, [0], "POTION_TORNADO",   "Tornado",                "potion", "Tornado in a Bottle"),
  ...gen(T, [0], "POTION_GATHER",   "Coleta",                 "potion", "Gathering Potion"),
  ...gen(T, [0], "POTION_SLOWFIELD","Pegajosa",               "potion", "Sticky Potion"),
  ...gen(T, [0], "POTION_COOLDOWN", "Veneno",                 "potion", "Poison Potion"),

  // ═══════════════════════════════════════════════════════════
  // ARMAS
  // ═══════════════════════════════════════════════════════════

  // ─── Cajados Sagrados ─────────────────────────────────────
  ...gen(T, E, "MAIN_HOLYSTAFF",  "Cajado Sagrado",         "weapon"),
  ...gen(T, E, "2H_HOLYSTAFF",    "Cajado Sagrado Elevado", "weapon"),
  ...gen(T, E, "2H_DIVINESTAFF",  "Cajado Divino",          "weapon"),
  ...art("2H_HOLYSTAFF_HELL",     "Cajado Corrompido",      "weapon"),
  ...art("2H_HOLYSTAFF_UNDEAD",   "Cajado da Redenção",     "weapon"),
  ...art("MAIN_HOLYSTAFF_AVALON", "Queda Santa",            "weapon"),
  ...art("MAIN_HOLYSTAFF_MORGANA","Cajado Avivador",        "weapon"),
  ...artAll("2H_HOLYSTAFF_CRYSTAL","Cajado Exaltado",       "weapon", "Exalted Staff"),

  // ─── Cajados da Natureza ──────────────────────────────────
  ...gen(T, E, "MAIN_NATURESTAFF",  "Cajado da Natureza",         "weapon"),
  ...gen(T, E, "2H_NATURESTAFF",    "Cajado da Natureza Elevado", "weapon"),
  ...gen(T, E, "2H_WILDSTAFF",      "Cajado Selvagem",            "weapon"),
  ...art("2H_NATURESTAFF_HELL",    "Cajado Pustulento",           "weapon"),
  ...art("2H_NATURESTAFF_KEEPER",  "Cajado Rampante",             "weapon"),
  ...art("2H_ROCKSTAFF_KEEPER",    "Cajado do Equilíbrio",        "weapon"),
  ...art("MAIN_NATURESTAFF_AVALON","Raiz Férrea",                 "weapon"),
  ...art("MAIN_NATURESTAFF_KEEPER","Cajado Druídico",             "weapon"),
  ...artAll("MAIN_NATURESTAFF_CRYSTAL","Cajado de Crosta Forjada","weapon", "Forgebark Staff"),

  // ─── Cajados Arcanos ──────────────────────────────────────
  ...gen(T, E, "MAIN_ARCANESTAFF",  "Cajado Arcano",         "weapon"),
  ...gen(T, E, "2H_ARCANESTAFF",    "Cajado Arcano Elevado", "weapon"),
  ...gen(T, E, "2H_ENIGMATICSTAFF", "Cajado Enigmático",     "weapon"),
  ...art("2H_ARCANE_RINGPAIR_AVALON","Som Equilibrado",       "weapon"),
  ...art("MAIN_ARCANESTAFF_UNDEAD", "Cajado Feiticeiro",      "weapon"),
  ...art("2H_ARCANESTAFF_HELL",     "Cajado Oculto",          "weapon"),
  ...art("2H_ENIGMATICORB_MORGANA", "Local Malévolo",         "weapon"),
  ...artAll("2H_ARCANESTAFF_CRYSTAL","Cajado Astral",         "weapon", "Astral Staff"),

  // ─── Cajados de Gelo ──────────────────────────────────────
  ...gen(T, E, "MAIN_FROSTSTAFF",  "Cajado de Gelo",         "weapon"),
  ...gen(T, E, "2H_FROSTSTAFF",    "Cajado de Gelo Elevado", "weapon"),
  ...gen(T, E, "2H_GLACIALSTAFF",  "Cajado Glacial",         "weapon"),
  ...art("2H_ICECRYSTAL_UNDEAD",   "Prisma Geleterno",       "weapon"),
  ...art("2H_ICEGAUNTLETS_HELL",   "Cajado de Sincelo",      "weapon"),
  ...art("MAIN_FROSTSTAFF_AVALON", "Uivo Frio",              "weapon"),
  ...art("MAIN_FROSTSTAFF_KEEPER", "Cajado Enregelante",     "weapon"),
  ...artAll("2H_FROSTSTAFF_CRYSTAL","Cajado Ártico",         "weapon", "Arctic Staff"),

  // ─── Cajados de Fogo ──────────────────────────────────────
  ...gen(T, E, "MAIN_FIRESTAFF",   "Cajado de Fogo",         "weapon"),
  ...gen(T, E, "2H_FIRESTAFF",     "Cajado de Fogo Elevado", "weapon"),
  ...gen(T, E, "2H_INFERNOSTAFF",  "Cajado Infernal",        "weapon"),
  ...art("2H_FIRESTAFF_HELL",      "Cajado Sulfuroso",       "weapon"),
  ...art("2H_INFERNOSTAFF_MORGANA","Cajado Fulgurante",      "weapon"),
  ...art("2H_FIRE_RINGPAIR_AVALON","Canção da Alvorada",     "weapon"),
  ...art("MAIN_FIRESTAFF_KEEPER",  "Cajado Incendiário",     "weapon"),
  ...artAll("MAIN_FIRESTAFF_CRYSTAL","Cajado do Andarilho Flamejante","weapon","Flamewalker Staff"),

  // ─── Cajados de Maldição ──────────────────────────────────
  ...gen(T, E, "MAIN_CURSEDSTAFF", "Cajado Amaldiçoado",          "weapon"),
  ...gen(T, E, "2H_CURSEDSTAFF",   "Cajado Amaldiçoado Elevado",  "weapon"),
  ...gen(T, E, "2H_DEMONICSTAFF",  "Cajado Demoníaco",            "weapon"),
  ...art("2H_SKULLORB_HELL",       "Caveira Amaldiçoada",         "weapon"),
  ...art("2H_CURSEDSTAFF_MORGANA", "Cajado da Danação",           "weapon"),
  ...art("MAIN_CURSEDSTAFF_AVALON","Chama-sombra",                "weapon"),
  ...art("MAIN_CURSEDSTAFF_UNDEAD","Cajado Execrado",             "weapon"),
  ...artAll("MAIN_CURSEDSTAFF_CRYSTAL","Cajado Pútrido",          "weapon", "Rotcaller Staff"),

  // ─── Cajados Metamorfos ───────────────────────────────────
  ...gen(T, E, "2H_SHAPESHIFTER_SET1","Cajado de Predador",       "weapon"),
  ...gen(T, E, "2H_SHAPESHIFTER_SET2","Cajado Enraizado",         "weapon"),
  ...gen(T, E, "2H_SHAPESHIFTER_SET3","Cajado Primitivo",         "weapon"),
  ...art("2H_SHAPESHIFTER_AVALON",  "Cajado Invocador da Luz",    "weapon"),
  ...art("2H_SHAPESHIFTER_HELL",    "Cajado Endemoniado",         "weapon"),
  ...art("2H_SHAPESHIFTER_KEEPER",  "Cajado Rúnico da Terra",     "weapon"),
  ...art("2H_SHAPESHIFTER_MORGANA", "Cajado da Lua de Sangue",    "weapon"),
  ...artAll("2H_SHAPESHIFTER_CRYSTAL","Cajado Petrificante",      "weapon", "Stillgaze Staff"),

  // ─── Espadas ──────────────────────────────────────────────
  ...gen(T, E, "MAIN_SWORD",   "Espada Larga",  "weapon"),
  ...gen(T, E, "2H_CLAYMORE",  "Montante",      "weapon"),
  ...gen(T, E, "2H_DUALSWORD", "Espadas Duplas","weapon"),
  ...art("2H_CLAYMORE_AVALON",    "Cria-reis",        "weapon"),
  ...art("2H_CLEAVER_HELL",       "Espada Entalhada", "weapon"),
  ...art("2H_DUALSCIMITAR_UNDEAD","Par de Galatinas", "weapon"),
  ...art("MAIN_SCIMITAR_MORGANA", "Lâmina Aclarada",  "weapon"),
  ...artAll("MAIN_SWORD_CRYSTAL", "Lâmina da Infinidade","weapon","Infinity Blade"),

  // ─── Maças e Martelos ─────────────────────────────────────
  ...gen(T, E, "MAIN_MACE",     "Maça",              "weapon"),
  ...gen(T, E, "MAIN_HAMMER",   "Martelo",           "weapon"),
  ...gen(T, E, "2H_POLEHAMMER", "Martelo de Batalha","weapon"),
  ...gen(T, E, "2H_HAMMER",     "Martelo Elevado",   "weapon"),
  ...gen(T, E, "2H_FLAIL",      "Mangual",           "weapon"),
  ...gen(T, E, "2H_MACE",       "Maça Pesada",       "weapon"),
  ...art("2H_HAMMER_AVALON",   "Mão da Justiça",    "weapon"),
  ...art("2H_HAMMER_UNDEAD",   "Martelo Fúnebre",   "weapon"),
  ...art("2H_DUALHAMMER_HELL", "Martelos de Forja", "weapon"),
  ...art("2H_DUALMACE_AVALON", "Jurador",           "weapon"),
  ...art("2H_MACE_MORGANA",    "Maça Cambriana",    "weapon"),
  ...art("MAIN_MACE_HELL",     "Maça de Íncubo",    "weapon"),
  ...art("MAIN_ROCKMACE_KEEPER","Maça Pétrea",      "weapon"),
  ...artAll("2H_HAMMER_CRYSTAL","Martelo Estrondoso","weapon","Truebolt Hammer"),
  ...artAll("MAIN_MACE_CRYSTAL","Monarca Tempestuoso","weapon","Dreadstorm Monarch"),

  // ─── Machados ─────────────────────────────────────────────
  ...gen(T, E, "MAIN_AXE",  "Machado de Guerra","weapon"),
  ...gen(T, E, "2H_AXE",    "Machadão",         "weapon"),
  ...gen(T, E, "2H_HALBERD","Alabarda",          "weapon"),
  ...art("2H_AXE_AVALON",     "Quebra-reino", "weapon"),
  ...art("2H_HALBERD_MORGANA","Chama-corpos", "weapon"),

  // ─── Lanças ───────────────────────────────────────────────
  ...gen(T, E, "MAIN_SPEAR","Lança","weapon"),
  ...gen(T, E, "2H_GLAIVE", "Archa","weapon"),
  ...gen(T, E, "2H_SPEAR",  "Pique","weapon"),
  ...art("2H_HARPOON_HELL",      "Caça-espíritos","weapon"),
  ...art("2H_RAM_KEEPER",        "Guarda-bosques","weapon"),
  ...art("2H_TRIDENT_UNDEAD",    "Lança Trina",   "weapon"),
  ...art("MAIN_SPEAR_KEEPER",    "Lança Garceira","weapon"),
  ...art("MAIN_SPEAR_LANCE_AVALON","Alvorada",    "weapon"),
  ...artAll("2H_GLAIVE_CRYSTAL", "Archa Fraturada","weapon","Rift Glaive"),

  // ─── Arcos ────────────────────────────────────────────────
  ...gen(T, E, "2H_BOW",    "Arco",          "weapon"),
  ...gen(T, E, "2H_WARBOW", "Arco de Guerra","weapon"),
  ...gen(T, E, "2H_LONGBOW","Arco Longo",    "weapon"),
  ...art("2H_BOW_AVALON",    "Fura-bruma",        "weapon"),
  ...art("2H_BOW_HELL",      "Arco Plangente",    "weapon"),
  ...art("2H_BOW_KEEPER",    "Arco Badônico",     "weapon"),
  ...art("2H_LONGBOW_UNDEAD","Arco Sussurrante",  "weapon"),
  ...artAll("2H_BOW_CRYSTAL","Arco do Andarilho Celeste","weapon","Skystrider Bow"),

  // ─── Bestas ───────────────────────────────────────────────
  ...gen(T, E, "MAIN_1HCROSSBOW",  "Besta Leve",  "weapon"),
  ...gen(T, E, "2H_CROSSBOW",      "Besta",        "weapon"),
  ...gen(T, E, "2H_CROSSBOWLARGE", "Besta Pesada", "weapon"),
  ...art("2H_CROSSBOWLARGE_MORGANA",  "Arco de Cerco",        "weapon"),
  ...art("2H_CROSSBOW_CANNON_AVALON", "Modelador de Energia", "weapon"),
  ...art("2H_DUALCROSSBOW_HELL",      "Lança-virotes",        "weapon"),
  ...art("2H_REPEATINGCROSSBOW_UNDEAD","Repetidor Lamentoso", "weapon"),
  ...artAll("2H_DUALCROSSBOW_CRYSTAL","Detonadores Reluzentes","weapon","Arclight Blasters"),

  // ─── Adagas ───────────────────────────────────────────────
  ...gen(T, E, "MAIN_DAGGER",  "Adaga",        "weapon"),
  ...gen(T, E, "2H_DAGGERPAIR","Par de Adagas","weapon"),
  ...gen(T, E, "2H_CLAWPAIR",  "Garras",       "weapon"),
  ...art("MAIN_DAGGER_HELL",     "Presa Demoníaca",   "weapon"),
  ...art("2H_SCYTHE_HELL",       "Segadeira Infernal","weapon"),
  ...art("2H_TWINSCYTHE_HELL",   "Segandâmica",       "weapon"),
  ...art("2H_DAGGER_KATAR_AVALON","Fúria Contida",    "weapon"),
  ...art("2H_DUALSICKLE_UNDEAD", "Mortíficos",        "weapon"),
  ...art("MAIN_RAPIER_MORGANA",  "Dessangrador",      "weapon"),
  ...artAll("2H_DAGGERPAIR_CRYSTAL","Gêmeas Aniquiladoras","weapon","Twin Slayers"),
  ...artAll("2H_SCYTHE_CRYSTAL",    "Foice de Cristal",   "weapon","Crystal Reaper"),

  // ─── Socos / Luvas ────────────────────────────────────────
  ...gen(T, E, "2H_KNUCKLES_SET1","Luvas de Lutador",     "weapon"),
  ...gen(T, E, "2H_KNUCKLES_SET2","Braçadeiras de Batalha","weapon"),
  ...gen(T, E, "2H_KNUCKLES_SET3","Manoplas Cravadas",    "weapon"),
  ...art("2H_KNUCKLES_AVALON",   "Punhos de Avalon",    "weapon"),
  ...art("2H_KNUCKLES_HELL",     "Mãos Infernais",      "weapon"),
  ...art("2H_KNUCKLES_KEEPER",   "Luvas Ursinas",       "weapon"),
  ...art("2H_KNUCKLES_MORGANA",  "Cestus Golpeadores",  "weapon"),
  ...art("2H_DUALAXE_KEEPER",    "Patas de Urso",       "weapon"),
  ...art("2H_IRONGAUNTLETS_HELL","Mãos Pretas",         "weapon"),
  ...artAll("2H_KNUCKLES_CRYSTAL","Braçadeiras Pulsantes","weapon","Forcepulse Bracers"),

  // ─── Cajados de Batalha ───────────────────────────────────
  ...gen(T, E, "2H_QUARTERSTAFF",     "Bordão",            "weapon"),
  ...gen(T, E, "2H_IRONCLADEDSTAFF",  "Cajado Férreo",     "weapon"),
  ...gen(T, E, "2H_DOUBLEBLADEDSTAFF","Cajado Bilaminado",  "weapon"),
  ...art("2H_QUARTERSTAFF_AVALON", "Buscador do Graal",    "weapon"),
  ...art("2H_COMBATSTAFF_MORGANA", "Cajado de Monge Negro","weapon"),
  ...artAll("2H_DOUBLEBLADEDSTAFF_CRYSTAL","Lâminas Gêmeas Fantasmagóricas","weapon","Phantom Twinblade"),
];

// Lookup por ID (para exibição rápida)
export const ITEM_BY_ID = new Map<string, AlbionItem>(
  ALBION_ITEMS.map(i => [i.id, i])
);

// Render via cache local do backend (/render/item/*) — evita bater direto na
// CDN da Albion a cada carregamento e mantém os ícones de pé mesmo se ela cair.
export const RENDER_URL = (id: string, quality = 0): string => {
  const q = quality ? `?quality=${quality}` : "";
  return `/render/item/${encodeURIComponent(id)}${q}`;
};

// Render URL usando nome em inglês (para crystal weapons)
export const RENDER_URL_EN = (nameEn: string, itemId: string, quality = 0): string => {
  const q = quality ? `?quality=${quality}` : "";
  const tier = parseInt(itemId.match(/^T(\d+)_/)?.[1] ?? "8");
  const enchant = itemId.match(/@(\d+)$/)?.[1];
  const prefix = TIER_PREFIX[tier] ?? "Elder's";
  const name = `${prefix} ${nameEn}${enchant ? `@${enchant}` : ""}`;
  return `/render/item/${encodeURIComponent(name)}${q}`;
};

// Escolhe o melhor render URL para um item
export function itemRenderUrl(item: AlbionItem | string, quality = 0): string {
  if (typeof item === "string") return RENDER_URL(item, quality);
  if (item.useEnRender && item.nameEn) return RENDER_URL_EN(item.nameEn, item.id, quality);
  return RENDER_URL(item.id, quality);
}

// Ícone usado no lugar da arma em kills/mortes onde o jogador lutou desarmado
// (sem MainHand equipado) — em todo o site, atacante ou vítima.
export const NO_WEAPON_ICON_ID = "T3_FARM_OX_BABY";
