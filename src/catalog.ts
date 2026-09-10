import type { Lang } from "./i18n";

export type SkillNameRow = {
  id: number;
  name: string | null;
  name_pt: string | null;
  name_es: string | null;
  unique_name?: string | null;
};

export type ItemNameRow = {
  item_id: string;
  item_name: string;
  item_name_pt: string;
  item_name_es: string;
};

function nonEmpty(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed || null;
}

export function skillDisplayName(
  row: SkillNameRow,
  lang: Lang,
  fallback: (id: number) => string,
): string {
  const localized = lang === "pt" ? row.name_pt : lang === "es" ? row.name_es : row.name;
  return nonEmpty(localized) ?? nonEmpty(row.name) ?? nonEmpty(row.unique_name) ?? fallback(row.id);
}

export function itemDisplayName(row: ItemNameRow, lang: Lang): string {
  const localized = lang === "pt" ? row.item_name_pt : lang === "es" ? row.item_name_es : row.item_name;
  return nonEmpty(localized) ?? nonEmpty(row.item_name) ?? nonEmpty(row.item_id) ?? "IDX_unknown";
}
