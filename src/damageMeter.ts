import { skillRenderCandidates } from "./render";

// Weapon family of each player, inferred by the app from their skills. It plays the part
// of the class in a WoW meter and sets the color of the bar (styles.css).
export const WEAPON_FAMILIES: ReadonlySet<string> = new Set([
  "sword", "axe", "mace", "hammer", "quarterstaff", "spear", "dagger", "knuckles", "bow", "crossbow",
  "firestaff", "froststaff", "arcanestaff", "cursestaff", "holystaff", "naturestaff", "shapeshifterstaff",
]);

const MELEE_FAMILIES = new Set(["sword", "axe", "mace", "hammer", "quarterstaff", "spear", "dagger", "knuckles"]);
const RANGED_FAMILIES = new Set(["bow", "crossbow"]);

// Auto-attack has no art in the game, so it borrows a skill icon of the same attack type.
// These are stable ids, not names, in case the game renames the skills.
function autoAttackIcons(weapon: string | null): string[] {
  if (!weapon) return [];
  if (MELEE_FAMILIES.has(weapon)) return ["PASSIVE_KNOCKBACKCHANCE"];
  if (RANGED_FAMILIES.has(weapon)) return ["SPEEDSHOT2"];
  return ["PASSIVE_ATTACKBUFF_ARCANESTAFF"];
}

type SkillIconSource = { id: number; icon?: string | null; unique_name?: string | null; name?: string | null };

export function skillIconCandidates(skill: SkillIconSource, weapon: string | null): string[] {
  return skill.id < 0 ? autoAttackIcons(weapon) : skillRenderCandidates(skill);
}

// The local player is always part of it, and is not always in the list the game sends.
export function partyRows<Row extends { name: string }>(
  rows: readonly Row[],
  playerName: string | null | undefined,
  partyMembers: readonly string[] | null | undefined,
): Row[] {
  const party = new Set(partyMembers ?? []);
  if (playerName) party.add(playerName);
  return rows.filter(row => party.has(row.name));
}

// Three significant digits: enough to compare players, short enough for a row or the chat.
export function shortNumber(value: number): string {
  const short = (scaled: number, suffix: string) => `${Number(scaled.toPrecision(3))}${suffix}`;
  // The thresholds come early so that 999500 reads 1M, not 1000K.
  if (value >= 999_500_000) return short(value / 1e9, "B");
  if (value >= 999_500) return short(value / 1e6, "M");
  if (value >= 999.5) return short(value / 1e3, "K");
  return String(Math.round(value));
}

export function shareOf(value: number, total: number): string {
  return total > 0 ? ((value / total) * 100).toFixed(1) : "0.0";
}

// The ranking as text for the game chat. No "#": the Albion chat reads it as a command.
export function meterText(rows: ReadonlyArray<{ name: string; damage: number }>): string {
  const total = rows.reduce((sum, row) => sum + row.damage, 0);
  return rows
    .map((row, index) => `${index + 1}. ${row.name} — ${shortNumber(row.damage)} (${shareOf(row.damage, total)}%)`)
    .join("\n");
}
