// Local fallback keeps the second entrypoint testable before DNS/proxy setup.
// Production should set VITE_DOCS_URL to the public docs subdomain.
export const DOCS_URL = import.meta.env.VITE_DOCS_URL || "/docs.html";
// Obrigatório quando Docs roda em subdomínio: define o único host autorizado
// para o botão de retorno e o fallback de acesso direto.
export const SITE_URL = import.meta.env.VITE_SITE_URL || "/";
