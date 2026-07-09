// Normalização de busca para nomes de entidade (jogador/guilda/aliança).
//
// Nomes do Albion são frequentemente estilizados com espaços entre letras
// ("S I G H T"), leet ("R E Q U 1 3 M") e trocas de 1 letra ("PLVAS" ↔
// "pivas"). normSearch normaliza (lower + remove espaços + leet);
// searchMatch ainda aplica distância de edição ≤ 1 pra tolerar typos.
//
// SÓ usar em nomes de entidade. Itens/IDs do catálogo têm dígitos de tier
// significativos ("T5_HEAD_PLATE") — leet neles quebraria o matching.

const LEET: Record<string, string> = {
  "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "$": "s", "@": "a",
};
const LEET_RE = /[0-9@$]/g;

export function normSearch(s: string | null | undefined): string {
  if (!s) return "";
  return s.toLowerCase().replace(/\s+/g, "").replace(LEET_RE, (c) => LEET[c] ?? c);
}

// ponytail: distância de edição limitada; retorna maxDist+1 se exceder (poda).
function levenshtein(a: string, b: string, maxDist: number): number {
  if (a === b) return 0;
  const la = a.length, lb = b.length;
  if (Math.abs(la - lb) > maxDist) return maxDist + 1;
  if (la === 0) return lb;
  if (lb === 0) return la;
  let prev = Array.from({ length: lb + 1 }, (_, j) => j);
  for (let i = 1; i <= la; i++) {
    const cur = new Array<number>(lb + 1);
    cur[0] = i;
    const ai = a.charCodeAt(i - 1);
    let rowMin = cur[0];
    for (let j = 1; j <= lb; j++) {
      const cost = ai === b.charCodeAt(j - 1) ? 0 : 1;
      const v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      cur[j] = v;
      if (v < rowMin) rowMin = v;
    }
    if (rowMin > maxDist) return maxDist + 1;
    prev = cur;
  }
  return prev[lb];
}

/**
 * True se `query` casa `name` após normalização (substring) ou por distância
 * de edição ≤ maxDist (só p/ queries de entidade ≥ 4 chars e comprimento
 * parecido — evita falsos positivos em queries curtas). P/ nomes de
 * entidade (guilda/aliança/jogador).
 */
export function searchMatch(query: string, name: string, maxDist = 1): boolean {
  const nq = normSearch(query), nn = normSearch(name);
  if (!nq) return true;
  if (nn.includes(nq)) return true;
  if (nq.length >= 4 && Math.abs(nq.length - nn.length) <= 2 && levenshtein(nq, nn, maxDist) <= maxDist) return true;
  return false;
}

/**
 * Substring normalizado (sem distância de edição) — para haystacks que
 * concatenam várias entidades (nome + guilda + aliança + itens), onde
 * edit-distance contra a string inteira não faz sentido.
 */
export function searchIncludes(query: string, haystack: string): boolean {
  if (!query) return true;
  return normSearch(haystack).includes(normSearch(query));
}