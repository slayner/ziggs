type RenderKind = "spell" | "item";

type RenderOptions = {
  quality?: number;
  size?: number;
};

type SkillRenderSource = {
  icon?: string | null;
  unique_name?: string | null;
  name?: string | null;
};

type ItemRenderSource = {
  item_id?: string | null;
  item_name?: string | null;
};

function nonEmpty(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed || null;
}

function uniqueCandidates(values: Array<string | null | undefined>): string[] {
  const seen = new Set<string>();
  const candidates: string[] = [];

  for (const value of values) {
    const candidate = nonEmpty(value);
    if (candidate && !seen.has(candidate)) {
      seen.add(candidate);
      candidates.push(candidate);
    }
  }

  return candidates;
}

export function buildRenderUrl(
  apiBaseUrl: string,
  kind: RenderKind,
  key: string,
  options: RenderOptions = {},
): string {
  const base = apiBaseUrl.replace(/\/+$/, "");
  const params = new URLSearchParams();

  if (options.quality !== undefined) params.set("quality", String(options.quality));
  if (options.size !== undefined) params.set("size", String(options.size));

  const query = params.toString();
  return `${base}/render/${kind}/${encodeURIComponent(key)}${query ? `?${query}` : ""}`;
}

export function skillRenderCandidates(source: SkillRenderSource): string[] {
  return uniqueCandidates([source.unique_name, source.icon, source.name]);
}

export function itemRenderCandidates(source: ItemRenderSource): string[] {
  return uniqueCandidates([source.item_id, source.item_name]).filter(candidate => !candidate.startsWith("IDX_"));
}
