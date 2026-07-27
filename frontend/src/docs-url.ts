// Local fallback keeps the second entrypoint testable before DNS/proxy setup.
// Production should set VITE_DOCS_URL to the public docs subdomain.
export const DOCS_URL = import.meta.env.VITE_DOCS_URL || "/docs.html";
