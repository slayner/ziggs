/** Number formatting helpers (pt-BR), used across the calculator UI. */

const silverFmt = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const decimalFmt = new Intl.NumberFormat("pt-BR", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 2,
});
const percentFmt = new Intl.NumberFormat("pt-BR", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

/** Whole-silver value, e.g. 1.234.567. */
export function silver(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return silverFmt.format(Math.round(n));
}

/**
 * Compact silver. Millions get 2 decimals (e.g. 2,69mi); 10k–1M is shown as a
 * whole-thousand "k" (e.g. 97k); under 10k shows the full number (e.g. 2.002),
 * since abbreviating small thousands lost too much precision to be useful.
 */
export function silverShort(n: number): string {
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${decimalFmt.format(n / 1_000_000)}mi`;
  if (abs >= 10_000) return `${silverFmt.format(Math.round(n / 1_000))}k`;
  return silverFmt.format(Math.round(n));
}

/** Decimal value with 1–2 places. */
export function decimal(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return decimalFmt.format(n);
}

/** Fraction as a percentage, e.g. 0.153 -> 15,3%. */
export function percent(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return percentFmt.format(n);
}

const MONTHS_PT = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];

// O site trabalha sempre em UTC — nunca usar toLocaleDateString/getMonth() aqui,
// que leem o fuso do navegador do usuário.

/** Data dd/mm/yyyy em UTC, independente do fuso do navegador. */
export function dateUTC(ts: string): string {
  const d = new Date(ts);
  const dd = String(d.getUTCDate()).padStart(2, "0");
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  return `${dd}/${mm}/${d.getUTCFullYear()}`;
}

/** Mês/ano abreviado em UTC, ex. "fev 2025". */
export function monthYearUTC(ts: string): string {
  const d = new Date(ts);
  return `${MONTHS_PT[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/** "5m atrás" / "3h ago" / etc — unidades já traduzidas via i18n (agoMinutes/agoHours/agoDays). */
export function timeAgo(ts: string, units: { min: string; hour: string; day: string }): string {
  const diff = Date.now() - new Date(ts).getTime();
  const m = Math.floor(diff / 60_000);
  if (m < 60) return `${m}${units.min}`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}${units.hour}`;
  return `${Math.floor(h / 24)}${units.day}`;
}
