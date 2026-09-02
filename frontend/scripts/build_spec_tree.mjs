// One-off: maps the user-provided destiny-board spec tree to catalog familyKeys.
// Items absent from catalog.json (Crystal weapons, Tracking Toolkit, Siege
// Banner) and Chef/Alchemist spec GROUPS (Soup, Heal, … — one node covers many
// catalog families) get a synthetic familyKey + minimal CatalogFamily so they
// still render in the Specialization panel (icon + name + 0-100 spec input).
//
// Rules from the user:
//  - Items in () after the base name are RELATED to the tree but generate NO
//    spec row (parser strips them). e.g. Base Harvester (sickle, avalonian sickle).
//  - No Spec Items (capes/schnapps/raw food/Royal gear) belong to no tree → no
//    panel. Royal gear was removed from the armor trees (now No Spec).
//  - Chef/Alchemist spec nodes are the GROUP names (Soup, Salad, Heal, …), not
//    the individual recipes. OWN_SPEC_NODE maps each catalog food/potion family
//    to its group so FCE picks the right ownSpec.
import { readFileSync, writeFileSync } from "node:fs";

const TREE_TXT = `
Warrior Tree
Base Sword > Broadsword, Claymore, Dual Sword, Clarent Blade, Carving Blade, Galatine Pair, Kingmaker, Infinity Blade
Base Axe > Battleaxe, Greataxe, Halberd, Carrioncaller, Infernal Scythe, Bear Paws, Realmbreaker, Crystal Reaper
Base Mace > Mace, Heavy Mace, Morning Star, Bedrock Mace, Incubus Mace, Camlann Mace, Oathkeepers, Dreadstorm Monarch
Base Hammer > Hammer, Polehammer, Great Hammer, Tombhammer, Forge Hammers, Grovekeeper, Hand of Justice, Truebolt Hammer
Base War Gloves > Brawler Gloves, Battle Bracers, Spiked Gauntlets, Ursine Maulers, Hellfire Hands, Ravenstrike Cestus, Fists of Avalon, Forcepulse Bracers
Base Crossbow > Crossbow, Heavy Crossbow, Light Crossbow, Weeping Repeater, Boltcasters, Siegebow, Energy Shaper, Arclight Blasters
Base Shield > Shield, Sarcophagus, Caitiff Shield, Facebreaker, Astral Aegis, Unbreakable Ward
Base Plate Boots > Soldier Boots, Knight Boots, Guardian Boots, Graveguard Boots, Demon Boots, Judicator Boots, Duskweaver Boots, Boots of Valor
Base Plate Armor > Soldier Armor, Knight Armor, Guardian Armor, Graveguard Armor, Demon Armor, Judicator Armor, Duskweaver Armor, Armor of Valor
Base Plate Helmet > Soldier Helmet, Knight Helmet, Guardian Helmet, Graveguard Helmet, Demon Helmet, Judicator Helmet, Duskweaver Helmet, Helmet of Valor
Hunter Tree
Base Bow > Bow, Warbow, Longbow, Whispering Bow, Wailing Bow, Bow of Badon, Mistpiercer, Skystrider Bow
Base Dagger > Dagger, Dagger Pair, Claws, Bloodletter, Demonfang, Deathgivers, Bridled Fury, Twin Slayers
Base Spear > Spear, Pike, Glaive, Heron Spear, Spirithunter, Trinity Spear, Daybreaker, Rift Glaive
Base Quarterstaff > Quarterstaff, Iron-clad Staff, Double Bladed Staff, Black Monk Staff, Soulscythe, Staff of Balance, Grailseeker, Phantom Twinblade
Base Shapeshifter > Prowling Staff, Rootbound Staff, Primal Staff, Bloodmoon Staff, Hellspawn Staff, Earthrune Staff, Lightcaller, Stillgaze Staff
Base Nature Staff > Nature Staff, Great Nature Staff, Wild Staff, Druidic Staff, Blight Staff, Rampant Staff, Ironroot Staff, Forgebark Staff
Base Torch > Torch, Mistcaller, Leering Cane, Cryptcandle, Sacred Scepter, Blueflame Torch
Base Leather Hood > Mercenary Hood, Hunter Hood, Assassin Hood, Stalker Hood, Hellion Hood, Specter Hood, Mistwalker Hood, Hood of Tenacity
Base Leather Jacket > Mercenary Jacket, Hunter Jacket, Assassin Jacket, Stalker Jacket, Hellion Jacket, Specter Jacket, Mistwalker Jacket, Jacket of Tenacity
Base Leather Shoes > Mercenary Shoes, Hunter Shoes, Assassin Shoes, Stalker Shoes, Hellion Shoes, Specter Shoes, Mistwalker Shoes, Shoes of Tenacity
Mage Tree
Base Cursed Staff > Cursed Staff, Great Cursed Staff, Demonic Staff, Lifecurse Staff, Cursed Skull, Damnation Staff, Shadowcaller, Rotcaller Staff
Base Frost Staff > Frost Staff, Great Frost Staff, Glacial Staff, Hoarfrost Staff, Icicle Staff, Permafrost Prism, Chillhowl, Arctic Staff
Base Arcane Staff > Arcane Staff, Great Arcane Staff, Enigmatic Staff, Witchwork Staff, Occult Staff, Malevolent Locus, Evensong, Astral Staff
Base Holy Staff > Holy Staff, Great Holy Staff, Divine Staff, Lifetouch Staff, Fallen Staff, Redemption Staff, Hallowfall, Exalted Staff
Base Fire Staff > Fire Staff, Great Fire Staff, Infernal Staff, Wildfire Staff, Brimstone Staff, Blazing Staff, Dawnsong, Flamewalker Staff
Base Tome > Tome of Spells, Eye of Secrets, Muisak, Taproot, Celestial Censer, Timelocked Grimoire
Base Cloth Cowl > Scholar Cowl, Cleric Cowl, Mage Cowl, Druid Cowl, Fiend Cowl, Cultist Cowl, Feyscale Cowl, Cowl of Purity
Base Cloth Robe > Scholar Robe, Cleric Robe, Mage Robe, Druid Robe, Fiend Robe, Cultist Robe, Feyscale Robe, Robe of Purity
Base Cloth Sandals > Scholar Sandals, Cleric Sandals, Mage Sandals, Druid Sandals, Fiend Sandals, Cultist Sandals, Feyscale Sandals, Sandals of Purity
Toolmaker Tree
Base Tracking > Tracking Toolkit
Base Harvester (sickle, avalonian sickle) > Harvester Workboots, Harvester Garb, Harvester Cap
Base Skinner (skinning knife, avalonian skinning knife) > Skinner Workboots, Skinner Garb, Skinner Cap
Base Miner (pickaxe, avalonian pickaxe) > Miner Workboots, Miner Garb, Miner Cap
Base Quarrier (stone hammer, avalonian stone hammer) > Quarrier Workboots, Quarrier Garb, Quarrier Cap
Base Lumberjack (axe, avalonian axe) > Lumberjack Workboots, Lumberjack Garb, Lumberjack Cap
Base Fishing (fishing rod, avalonian fishing rod) > Fisherman Workboots, Fisherman Garb, Fisherman Cap
Base Cape > Cape
Base Bag > Bag, Satchel of Insight
Base Siege (avalonian siege hammer) > Siege Hammer, Siege Banner
Base Chef (Turnip Salad, Midwater Octopus Salad, Goat Sandwich, Avalonian Goat Sandwich, Stonestream Lurcher Sandwich, Goat Stew, Avalonian Goat Stew, Greenriver Eel Stew, Goose Omelette, Avalonian Goose Omelette, Drybrook Crab Omelette, Goose Pie, Mountain Blindeye Pie, Roast Goose, Roasted Clearhaze Snapper, Cabbage Soup, Blackbog Clam Soup, Potato Salad, Deepwater Kraken Salad, Mutton Sandwich, Avalonian Mutton Sandwich, Rushwater Lurcher Sandwich, Mutton Stew, Avalonian Mutton Stew, Redspring Eel Stew, Pork Omelette, Avalonian Pork Omelette, Dusthole Crab Omelette, Pork Pie, Frostpeak Deadeye Pie, Roast Pork, Roasted Puremist Snapper, Beef Sandwich, Avalonian Beef Sandwich, Thunderfall Lurcher Sandwich, Beef Stew, Avalonian Beef Stew, Deadwater Eel Stew) > Soup, Salad, Pie, Roast, Omelette, Stew, Sandwich, Ingredient, Butcher
Base Alchemist > Heal, Energy, Gigantify, Resistance, Sticky, Poison, Invisibility, Calming, Cleansing, Acid, Berserk, Hellfire, Gathering, Tornado, Bootlegger
`;

const catalog = JSON.parse(readFileSync(new URL("../public/data/catalog.json", import.meta.url), "utf8"));

// names.json → max tier per base id. Food/potions top out at T5–T7 (no T8), so
// synthetic spec nodes for MEAL_*/POTION_* render at their real top tier instead
// of a broken T8. Equipment & crystal stay T8.
const names = JSON.parse(readFileSync(new URL("../public/data/names.json", import.meta.url), "utf8"));
const tierSets = new Map();
for (const id of Object.keys(names)) {
  const m = id.match(/^T(\d+)_(.+)$/);
  if (!m) continue;
  const base = m[2].replace(/@\d+$/, "");
  if (!tierSets.has(base)) tierSets.set(base, new Set());
  tierSets.get(base).add(+m[1]);
}
const maxTierOf = (baseId) => {
  const s = tierSets.get(baseId);
  return s && s.size ? Math.max(...s) : 8;
};
// ponytail: crystal weapon VARIANTS render T7 (base synth is T8, so the base
// row stands apart). Food/potion nodes use their real top tier; gear stays T8.
const tierOf = (renderId, crystal) => {
  if (crystal) return 7;
  if (/^(MEAL|POTION)/.test(renderId)) return maxTierOf(renderId);
  return 8;
};

// Gathering tools (Axe, Pickaxe, Sickle, …) have generic names that collide
// with weapon tree bases — "Axe" the tool must NOT be the base of the Warrior
// Axe weapon tree. Exclude them from the main name index (so "Base Axe" falls
// back to its first weapon variant) and keep a separate tool index for the
// () items, which ARE tools (sickle, fishing rod, avalonian siege hammer, …).
// Siege Hammer stays in the main index (it's a real Siege-tree variant).
const isGatherTool = (k) => /^2H_TOOL_(AXE|PICK|SICKLE|KNIFE|HAMMER|FISHINGROD)(_AVALON)?$/.test(k);
const byName = new Map();
const byNorm = new Map();
const toolByNorm = new Map();
const norm = (s) => s.toLowerCase().replace(/[^a-z0-9 ]/g, "").trim();
for (const f of catalog) {
  if (isGatherTool(f.familyKey)) { toolByNorm.set(norm(f.name), f.familyKey); continue; }
  byName.set(f.name, f.familyKey);
  byNorm.set(norm(f.name), f.familyKey);
}
const resolveTool = (n) => toolByNorm.get(norm(n)) ?? null;

// User name -> catalog name (item exists in catalog under a different name).
const SYN_NAMES = {
  "Carving Blade": "Carving Sword",
  "Dual Sword": "Dual Swords",
  "Black Monk Staff": "Black Monk Stave",
  "Feyscale Cowl": "Feyscale Hat",
};

// User name -> Albion base id for items NOT in catalog.json (Crystal weapons,
// Tracking Toolkit, Siege Banner). Royal gear was removed (now No Spec).
const SYN_KEYS = {
  // Crystal (Avalonian Crystal) weapons — render by EN name.
  "Infinity Blade": "MAIN_SWORD_CRYSTAL",
  "Crystal Reaper": "2H_SCYTHE_CRYSTAL",
  "Dreadstorm Monarch": "MAIN_MACE_CRYSTAL",
  "Truebolt Hammer": "2H_HAMMER_CRYSTAL",
  "Forcepulse Bracers": "2H_KNUCKLES_CRYSTAL",
  "Arclight Blasters": "2H_DUALCROSSBOW_CRYSTAL",
  "Skystrider Bow": "2H_BOW_CRYSTAL",
  "Twin Slayers": "2H_DAGGERPAIR_CRYSTAL",
  "Rift Glaive": "2H_GLAIVE_CRYSTAL",
  "Phantom Twinblade": "2H_DOUBLEBLADEDSTAFF_CRYSTAL",
  "Astral Staff": "2H_ARCANESTAFF_CRYSTAL",
  "Arctic Staff": "2H_FROSTSTAFF_CRYSTAL",
  "Exalted Staff": "2H_HOLYSTAFF_CRYSTAL",
  "Flamewalker Staff": "MAIN_FIRESTAFF_CRYSTAL",
  "Rotcaller Staff": "MAIN_CURSEDSTAFF_CRYSTAL",
  "Forgebark Staff": "MAIN_NATURESTAFF_CRYSTAL",
  "Unbreakable Ward": "OFF_SHIELD_CRYSTAL",
  "Timelocked Grimoire": "OFF_TOME_CRYSTAL",
  "Blueflame Torch": "OFF_TORCH_CRYSTAL",
  "Stillgaze Staff": "2H_SHAPESHIFTER_CRYSTAL",
  // Shapeshifter staves (not in catalog at all; not Royal)
  "Prowling Staff": "2H_SHAPESHIFTER_SET1",
  "Rootbound Staff": "2H_SHAPESHIFTER_SET2",
  "Primal Staff": "2H_SHAPESHIFTER_SET3",
  "Bloodmoon Staff": "2H_SHAPESHIFTER_MORGANA",
  "Hellspawn Staff": "2H_SHAPESHIFTER_HELL",
  "Earthrune Staff": "2H_SHAPESHIFTER_KEEPER",
  "Lightcaller": "2H_SHAPESHIFTER_AVALON",
  // Toolmaker items absent from catalog.json
  "Tracking Toolkit": "TRACKING_KIT",
  "Siege Banner": "SIEGE_BANNER",
};

// Chef/Alchemist spec GROUP nodes — one node covers many catalog families.
// renderId = a primary catalog family whose T8 icon represents the group.
// Nodes with no catalog family (Ingredient/Butcher/Bootlegger) reuse a sibling's
// renderId as a placeholder icon.
const GROUP_NODES = {
  // Chef (cooking)
  "Soup": { key: "CHEF_SOUP", renderId: "MEAL_SOUP" },
  "Salad": { key: "CHEF_SALAD", renderId: "MEAL_SALAD" },
  "Pie": { key: "CHEF_PIE", renderId: "MEAL_PIE" },
  "Roast": { key: "CHEF_ROAST", renderId: "MEAL_GRILLEDFISH" },
  "Omelette": { key: "CHEF_OMELETTE", renderId: "MEAL_OMELETTE" },
  "Stew": { key: "CHEF_STEW", renderId: "MEAL_STEW" },
  "Sandwich": { key: "CHEF_SANDWICH", renderId: "MEAL_SANDWICH" },
  "Ingredient": { key: "CHEF_INGREDIENT", renderId: "MEAL_SOUP" },
  "Butcher": { key: "CHEF_BUTCHER", renderId: "MEAL_SOUP" },
  // Alchemist (potions)
  "Heal": { key: "ALCH_HEAL", renderId: "POTION_HEAL" },
  "Energy": { key: "ALCH_ENERGY", renderId: "POTION_ENERGY" },
  "Gigantify": { key: "ALCH_GIGANTIFY", renderId: "POTION_REVIVE" },
  "Resistance": { key: "ALCH_RESISTANCE", renderId: "POTION_STONESKIN" },
  "Sticky": { key: "ALCH_STICKY", renderId: "POTION_SLOWFIELD" },
  "Poison": { key: "ALCH_POISON", renderId: "POTION_COOLDOWN" },
  "Invisibility": { key: "ALCH_INVISIBILITY", renderId: "POTION_CLEANSE" },
  "Calming": { key: "ALCH_CALMING", renderId: "POTION_MOB_RESET" },
  "Cleansing": { key: "ALCH_CLEANSING", renderId: "POTION_CLEANSE2" },
  "Acid": { key: "ALCH_ACID", renderId: "POTION_ACID" },
  "Berserk": { key: "ALCH_BERSERK", renderId: "POTION_BERSERK" },
  "Hellfire": { key: "ALCH_HELLFIRE", renderId: "POTION_LAVA" },
  "Gathering": { key: "ALCH_GATHERING", renderId: "POTION_GATHER" },
  "Tornado": { key: "ALCH_TORNADO", renderId: "POTION_TORNADO" },
  "Bootlegger": { key: "ALCH_BOOTLEGGER", renderId: "POTION_HEAL" },
};

// Catalog food/potion familyKey prefix -> group key (drives OWN_SPEC_NODE).
// Order matters: more specific prefixes first.
const FOOD_PREFIX = [
  { re: /^MEAL_SEAWEEDSALAD$/, key: "CHEF_SALAD" },
  { re: /^MEAL_GRILLEDFISH/, key: "CHEF_ROAST" },
  { re: /^MEAL_SOUP/, key: "CHEF_SOUP" },
  { re: /^MEAL_SALAD/, key: "CHEF_SALAD" },
  { re: /^MEAL_PIE/, key: "CHEF_PIE" },
  { re: /^MEAL_OMELETTE/, key: "CHEF_OMELETTE" },
  { re: /^MEAL_STEW/, key: "CHEF_STEW" },
  { re: /^MEAL_SANDWICH/, key: "CHEF_SANDWICH" },
];
const POTION_KEY = {
  POTION_HEAL: "ALCH_HEAL",
  POTION_ENERGY: "ALCH_ENERGY",
  POTION_REVIVE: "ALCH_GIGANTIFY",
  POTION_STONESKIN: "ALCH_RESISTANCE",
  POTION_SLOWFIELD: "ALCH_STICKY",
  POTION_COOLDOWN: "ALCH_POISON",
  POTION_CLEANSE: "ALCH_INVISIBILITY",
  POTION_MOB_RESET: "ALCH_CALMING",
  POTION_CLEANSE2: "ALCH_CLEANSING",
  POTION_ACID: "ALCH_ACID",
  POTION_BERSERK: "ALCH_BERSERK",
  POTION_LAVA: "ALCH_HELLFIRE",
  POTION_GATHER: "ALCH_GATHERING",
  POTION_TORNADO: "ALCH_TORNADO",
};

// Per-node render info: key -> { name, renderId, crystal, artifact, slot }
const nodeInfo = new Map();
const synthetic = new Map(); // familyKey -> nodeInfo (for SPEC_EXTRA_FAMILIES)

const ARTIFACT_RE = /(CRYSTAL|AVALON|HELL|KEEPER|MORGANA|UNDEAD)$/;

const slotOf = (baseId) => {
  if (baseId.startsWith("HEAD_") || baseId.startsWith("HEAD")) return "helmet";
  if (baseId.startsWith("ARMOR_")) return "armor";
  if (baseId.startsWith("SHOES_")) return "boots";
  if (baseId.startsWith("OFF_")) return "offhand";
  if (baseId.startsWith("CAPE")) return "cape";
  if (baseId.startsWith("BAG") || baseId.startsWith("BACKPACK")) return "bag";
  if (baseId.startsWith("MEAL")) return "food";
  if (baseId.startsWith("POTION")) return "potion";
  return "mainhand";
};

const register = (key, info) => {
  if (!nodeInfo.has(key)) nodeInfo.set(key, { ...info, slot: info.slot ?? slotOf(info.renderId) });
};

const resolve = (n) => {
  if (SYN_NAMES[n]) {
    const k = byName.get(SYN_NAMES[n]);
    if (k) { register(k, { name: n, renderId: k, crystal: false, artifact: ARTIFACT_RE.test(k) }); return k; }
  }
  const direct = byName.get(n) || byNorm.get(norm(n));
  if (direct) { register(direct, { name: n, renderId: direct, crystal: false, artifact: ARTIFACT_RE.test(direct) }); return direct; }
  if (GROUP_NODES[n]) {
    const g = GROUP_NODES[n];
    const crystal = g.renderId.endsWith("_CRYSTAL");
    register(g.key, { name: n, renderId: g.renderId, crystal, artifact: false });
    synthetic.set(g.key, nodeInfo.get(g.key));
    return g.key;
  }
  if (SYN_KEYS[n]) {
    const baseId = SYN_KEYS[n];
    const crystal = baseId.endsWith("_CRYSTAL");
    register(baseId, { name: n, renderId: baseId, crystal, artifact: ARTIFACT_RE.test(baseId) });
    synthetic.set(baseId, nodeInfo.get(baseId));
    return baseId;
  }
  return null;
};

// Parse trees. "Base NAME (paren, may, Have, Commas) > variant, variant, …"
const trees = [];
const miss = [];
const dup = [];
for (const line of TREE_TXT.trim().split("\n")) {
  if (!line.includes(">")) continue;
  const gt = line.indexOf(">");
  const left = line.slice(0, gt).trim();
  const variantStr = line.slice(gt + 1).trim();
  const paren = left.match(/^(.*?)\s*\((.*)\)\s*$/s); // () content: related items, no spec row
  const baseName = (paren ? paren[1] : left).replace(/^Base\s+/i, "").trim();
  // ponytail: () items don't get a spec row, but if they're catalog tools
  // (sickle, fishing rod, avalonian siege hammer, …) they're selectable, so
  // index them into the tree and map them to the base synth node — selecting
  // one shows the panel and benefits from the base spec.
  const parenItems = paren ? paren[2].split(",").map((s) => s.trim()).filter(Boolean) : [];
  const parenKeys = [];
  for (const p of parenItems) {
    const k = resolve(p) ?? resolveTool(p);
    if (k) parenKeys.push(k);
  }
  const variantNames = variantStr.split(",").map((v) => v.trim()).filter(Boolean);
  const baseKey = resolve(baseName) ?? resolve(variantNames[0]);
  if (!baseKey) { miss.push(`BASE: ${baseName}`); continue; }
  const seen = new Map();
  const members = [];
  for (const v of variantNames) {
    const k = resolve(v);
    if (!k) { miss.push(`${baseName} :: ${v}`); continue; }
    if (k === baseKey) continue; // first variant IS the base key
    if (!seen.has(k)) seen.set(k, []);
    seen.get(k).push(v);
    members.push({ name: v, key: k });
  }
  for (const [k, names] of seen) if (names.length > 1) dup.push(`${baseName}: ${names.join(" == ")} -> ${k}`);
  trees.push({ baseName, baseKey, members, parenKeys, parenRaw: parenItems });
}

// Build familyKey -> ordered tree familyKeys, with a synthetic BASE node per
// tree (own key `${baseKey}__BASE`, T8 render of the first variant, own 0-100
// spec input). The first variant then also appears as a normal variant row.
const index = new Map();
const bases = new Map(); // baseSynthKey -> baseKey
const ownSpecNode = {}; // familyKey -> spec node key (tools→base synth, food/potion→group)
for (const t of trees) {
  if (!t.baseKey) continue;
  const baseSynthKey = `${t.baseKey}__BASE`;
  const firstRender = nodeInfo.get(t.baseKey)?.renderId ?? t.baseKey;
  bases.set(baseSynthKey, { baseKey: t.baseKey, renderId: firstRender });
  const order = [baseSynthKey, ...new Set([t.baseKey, ...t.members.map((m) => m.key)])];
  const entry = { baseKey: baseSynthKey, order };
  index.set(baseSynthKey, entry);
  index.set(t.baseKey, entry);
  for (const m of t.members) index.set(m.key, entry);
  // () tools (sickle, fishing rod, avalonian siege hammer, …): index into the
  // tree so selecting one shows the panel; map to base synth so ownSpec = base.
  for (const pk of t.parenKeys) {
    index.set(pk, entry);
    ownSpecNode[pk] = baseSynthKey;
  }
}

// OWN_SPEC_NODE: catalog food/potion family -> its group spec node key.
// (Tools → base synth, set above. Weapons/armor/gatherer map to themselves:
// no entry; CraftCalculator defaults to familyKey.) Food/potion → group key.
for (const f of catalog) {
  if (f.kind !== "consumable") continue;
  const k = f.familyKey;
  if (k.startsWith("MEAL")) {
    const m = FOOD_PREFIX.find((p) => p.re.test(k));
    if (m) ownSpecNode[k] = m.key;
  } else if (k.startsWith("POTION") && POTION_KEY[k]) {
    ownSpecNode[k] = POTION_KEY[k];
  }
}
// Each food/potion family indexes into its tree (so specTreeFor finds it).
for (const [famKey, groupKey] of Object.entries(ownSpecNode)) {
  const entry = index.get(groupKey);
  if (entry) index.set(famKey, entry);
}

// Food recipe aliases: the Chef () list has recipes that aren't catalog
// families (Beef Stew, Mutton Stew, …) but share a spec group with one. Map each
// recipe to its group's catalog family (base/avalon/fish by keyword) so the
// picker search "beef stew" surfaces the Goat Stew (MEAL_STEW) family.
const FOOD_GROUP_FAM = {
  Soup: { base: "MEAL_SOUP", fish: "MEAL_SOUP_FISH" },
  Salad: { base: "MEAL_SALAD", fish: "MEAL_SALAD_FISH", seaweed: "MEAL_SEAWEEDSALAD" },
  Pie: { base: "MEAL_PIE", fish: "MEAL_PIE_FISH" },
  Roast: { base: "MEAL_GRILLEDFISH" },
  Omelette: { base: "MEAL_OMELETTE", fish: "MEAL_OMELETTE_FISH", avalon: "MEAL_OMELETTE_AVALON" },
  Stew: { base: "MEAL_STEW", fish: "MEAL_STEW_FISH", avalon: "MEAL_STEW_AVALON" },
  Sandwich: { base: "MEAL_SANDWICH", fish: "MEAL_SANDWICH_FISH", avalon: "MEAL_SANDWICH_AVALON" },
};
const GROUP_RE = [
  [/\bsandwich\b/i, "Sandwich"], [/\bomelette\b/i, "Omelette"], [/\bstew\b/i, "Stew"],
  [/\bsalad\b/i, "Salad"], [/\bpie\b/i, "Pie"], [/\broast/i, "Roast"], [/\bsoup\b/i, "Soup"],
];
const FISH_RE = /(eel|clam|kraken|squid|crab|snapper|octopus|lurcher|blindeye|deadeye|coldeye|puremist|clearhaze|salmon|fish)/i;
const classifyRecipe = (recipe, group) => {
  const fams = FOOD_GROUP_FAM[group];
  if (!fams) return null;
  if (group === "Salad" && /seaweed/i.test(recipe)) return fams.seaweed;
  if (/avalonian/i.test(recipe) && fams.avalon) return fams.avalon;
  if (FISH_RE.test(recipe) && fams.fish) return fams.fish;
  return fams.base;
};
const recipeAliases = {}; // familyKey -> [lowercase recipe names]
for (const t of trees) {
  for (const r of t.parenRaw) {
    const g = GROUP_RE.find(([re]) => re.test(r));
    if (!g) continue;
    const fam = classifyRecipe(r, g[1]);
    if (!fam) continue;
    (recipeAliases[fam] = recipeAliases[fam] || []).push(r.toLowerCase());
  }
}
// Dedupe (a recipe may appear once; keep order stable).
for (const k of Object.keys(recipeAliases)) recipeAliases[k] = [...new Set(recipeAliases[k])];

// Synthetic families emitted in SPEC_EXTRA: base synths + crystal + group nodes
// + tracking/siege-banner. (All nodeInfo entries flagged synthetic.)
const synthKeys = new Set([...synthetic.keys(), ...bases.keys()]);
// Emit TS module.
let out = `// AUTO-GENERATED by scripts/build_spec_tree.mjs — do not edit by hand.
// Destiny-board specialization trees: synthetic BASE node + the variants the
// user listed, mapped to catalog familyKeys. Drives the Specialization panel
// grouping & FCE siblings. Items absent from catalog.json (Crystal weapons,
// Tracking Toolkit, Siege Banner) and Chef/Alchemist spec GROUPS (Soup, Heal,
// … — one node covers many catalog families) get a synthetic familyKey + a
// minimal CatalogFamily below. OWN_SPEC_NODE maps each food/potion family to
// its group so the FCE picks the right ownSpec.

import type { CatalogFamily, CatalogVariation } from "./catalog";
import { crystalRenderName } from "../../data/albion-items";

export interface SpecTree { baseKey: string; order: string[] }

// ponytail: tier-aware via crystalRenderName (T4 Adept … T8 Elder). Synthetic
// families are T8.0 for the spec panel; never the selected family, never cart.
function v(renderId: string, name: string, crystal: boolean, artifact: boolean, tier = 8): CatalogVariation {
  return {
    uniqueName: crystal ? crystalRenderName(name, tier) : \`T\${tier}_\${renderId}\`,
    tier, enchant: 0,
    itemPower: 0, focus: 0, itemValue: 0,
    // ponytail: artifact marker only — drives FCE artifact flag for siblings.
    resources: artifact ? [{ uniqueName: "T8_ARTEFACT", count: 0, noReturn: true }] : [],
  };
}

function fam(key: string, name: string, renderId: string, crystal: boolean, artifact: boolean, tier: number): CatalogFamily {
  return { familyKey: key, name, slot: ${JSON.stringify(null)}, category: null, subcategory: null, craftCategory: null, bonusCity: null, kind: "equipment", variations: [v(renderId, name, crystal, artifact, tier)] };
}

export const SPEC_EXTRA_FAMILIES: CatalogFamily[] = [
`;
// Base synth nodes first (one per tree). Food/potion bases use their top tier.
for (const [baseSynthKey, b] of bases) {
  out += `  fam(${JSON.stringify(baseSynthKey)}, "Base", ${JSON.stringify(b.renderId)}, false, false, ${tierOf(b.renderId, false)}),\n`;
}
// Crystal / group / tracking / siege-banner synthetics.
for (const key of synthetic.keys()) {
  const info = nodeInfo.get(key);
  out += `  fam(${JSON.stringify(key)}, ${JSON.stringify(info.name)}, ${JSON.stringify(info.renderId)}, ${!!info.crystal}, ${!!info.artifact}, ${tierOf(info.renderId, !!info.crystal)}),\n`;
}
out += `];

export const OWN_SPEC_NODE: Record<string, string> = ${JSON.stringify(ownSpecNode, null, 0)};

// familyKey -> recipe names that share its spec group (picker search aliases).
export const RECIPE_ALIASES: Record<string, string[]> = ${JSON.stringify(recipeAliases, null, 0)};

const SPEC_TREE_INDEX: Record<string, SpecTree> = {
`;
for (const [k, val] of index) {
  out += `  ${JSON.stringify(k)}: { baseKey: ${JSON.stringify(val.baseKey)}, order: ${JSON.stringify(val.order)} },\n`;
}
out += `};

export function specTreeFor(familyKey: string): SpecTree | null {
  return SPEC_TREE_INDEX[familyKey] ?? null;
}
`;

writeFileSync(new URL("../src/lib/craft/specTree.ts", import.meta.url), out);

console.log("Trees:", trees.length);
console.log("Indexed familyKeys:", index.size);
console.log("Synthetic base nodes:", bases.size);
console.log("Synthetic other families:", synthetic.size);
console.log("OWN_SPEC_NODE entries:", Object.keys(ownSpecNode).length);
console.log("Recipe alias families:", Object.keys(recipeAliases).length, "aliases:", Object.values(recipeAliases).reduce((n, a) => n + a.length, 0));
console.log("Misses (" + miss.length + "):");
console.log(miss.join("\n"));
console.log("Dups (" + dup.length + "):");
console.log(dup.join("\n"));