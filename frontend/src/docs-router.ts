import type { DocsLocale } from "./docs-types";

const LOCALES = new Set<DocsLocale>(["en", "pt", "es"]);

function cleanPath(pathname: string, hash: string): string {
  if (pathname === "/docs.html" && hash.startsWith("#/")) return hash.slice(1);
  return pathname.replace(/^\/docs\.html(?=\/|$)/, "") || "/";
}

export function readDocsLocation(pathname = window.location.pathname, hash = window.location.hash): { lang: DocsLocale | null; slug: string } {
  const parts = cleanPath(pathname, hash).split("/").filter(Boolean);
  const lang = LOCALES.has(parts[0] as DocsLocale) ? parts.shift() as DocsLocale : null;
  return { lang, slug: parts.join("/") };
}

export function docsPath(lang: DocsLocale, slug = ""): string {
  const path = `/${lang}${slug ? `/${slug}` : "/"}`;
  const from = new URLSearchParams(window.location.search).get("from");
  const query = from ? `?${new URLSearchParams({ from })}` : "";
  return window.location.pathname === "/docs.html" ? `/docs.html${query}#${path}` : `${path}${query}`;
}
