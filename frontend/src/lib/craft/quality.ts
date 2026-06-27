/**
 * Crafting quality probability model.
 *
 * Albion rolls on a base quality table; a crafting quality bonus of X% grants
 * `1 + floor(X/100)` guaranteed rolls plus a `(X mod 100)%` chance of one extra
 * roll, and the crafted item takes the BEST quality rolled.
 *
 * Base per-roll table (Normal..Masterpiece):
 *   68.8% / 25% / 5% / 1.1% / 0.1%
 * Sources: Albion Wiki "Quality", forum thread 67684.
 */

export const QUALITY_BASE = [0.688, 0.25, 0.05, 0.011, 0.001];
export const QUALITY_LABELS = ["Normal", "Bom", "Excepcional", "Excelente", "Obra-prima"];
export const QUALITY_COLORS = ["#a1a1aa", "#4ade80", "#38bdf8", "#a78bfa", "#fbbf24"];

/** Distribution of the best quality over `rolls` independent rolls. */
function takeMaxDistribution(rolls: number): number[] {
  const cdf: number[] = [];
  let acc = 0;
  for (const b of QUALITY_BASE) {
    acc += b;
    cdf.push(acc);
  }
  const dist: number[] = [];
  let prev = 0;
  for (let q = 0; q < QUALITY_BASE.length; q++) {
    const c = Math.pow(cdf[q], rolls);
    dist.push(c - prev);
    prev = c;
  }
  return dist;
}

/**
 * Probability of each quality (index 0..4) given a crafting quality bonus in
 * percent. Returns an array summing to 1.
 */
export function qualityDistribution(bonusPercent: number): number[] {
  const x = Math.max(0, bonusPercent);
  const base = 1 + Math.floor(x / 100);
  const pExtra = (x % 100) / 100;
  const a = takeMaxDistribution(base);
  const b = takeMaxDistribution(base + 1);
  return a.map((v, i) => (1 - pExtra) * v + pExtra * b[i]);
}
