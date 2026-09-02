/**
 * Mapeamento UniqueName ↔ game_name.
 *Embutido no bundle (não baixado via fetch — evita dependência de rede).
 * O game_name é o ID canônico do sistema de preços (nome em inglês do jogo).
 */
import _raw from "./item_names.json";

const _uniqueToGame: Map<string, string> = new Map(Object.entries(_raw as Record<string, string>));
// Reverso: 235 game_names mapeiam pra >1 UniqueName (ex: "Rare Hemp" ←
// T4_FIBER_LEVEL2 e T4_FIBER_LEVEL2@2). Prefere a versão COM @enchant —
// é o que a AODP usa. Sem isto, encantados viravam flat na ida pra AODP.
const _gameToUnique: Map<string, string> = new Map();
for (const [uid, gname] of Object.entries(_raw as Record<string, string>)) {
  const existing = _gameToUnique.get(gname);
  if (existing === undefined || (uid.includes("@") && !existing.includes("@"))) {
    _gameToUnique.set(gname, uid);
  }
}

/** UniqueName → game_name. Ex: T4_FIBER_LEVEL2@2 → "Rare Hemp". */
export function toGameName(uniqueName: string): string {
  return _uniqueToGame.get(uniqueName) ?? uniqueName;
}

/** game_name → UniqueName. Ex: "Rare Hemp" → T4_FIBER_LEVEL2@2. */
export function toUniqueId(gameName: string): string {
  return _gameToUnique.get(gameName) ?? gameName;
}

/** Converte uma lista de UniqueNames pra game_names. */
export function toGameNames(uniqueNames: string[]): string[] {
  return uniqueNames.map((u) => _uniqueToGame.get(u) ?? u);
}

/** Converte uma lista de game_names pra UniqueNames. */
export function toUniqueIds(gameNames: string[]): string[] {
  return gameNames.map((g) => _gameToUnique.get(g) ?? g);
}