import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { LangProvider, useLang } from "./i18n";
import { COMMANDS, DOCS_LOCALES, DOCS_PAGES, GUIDE_COPY, localized } from "./docs-content";
import { docsPath, readDocsLocation } from "./docs-router";
import { SITE_URL } from "./docs-url";
import type { DocsLocale } from "./docs-types";
import "./styles.css";
import "./docs.css";

const PAGE_BY_SLUG = new Map(DOCS_PAGES.map(page => [page.slug, page]));
const CATEGORY_LABELS: Record<string, Record<DocsLocale, string>> = {
  general: { en: "General", pt: "Geral", es: "General" },
  registration: { en: "Registration", pt: "Registro", es: "Registro" },
  economy: { en: "Economy", pt: "Economia", es: "Economía" },
  events: { en: "Events", pt: "Eventos", es: "Eventos" },
};

function siteRoot(): URL {
  return new URL(SITE_URL, window.location.origin);
}

function readDocsReturnTarget(): string {
  const raw = new URLSearchParams(window.location.search).get("from");
  const fallback = siteRoot();
  if (!raw) return fallback.toString();
  try {
    const target = new URL(raw);
    const isManagementReturn =
      target.pathname === fallback.pathname && target.searchParams.get("view") === "management";
    if (!/^https?:$/.test(target.protocol) || target.origin !== fallback.origin || !isManagementReturn) {
      return fallback.toString();
    }
    return target.toString();
  } catch {
    return fallback.toString();
  }
}

function DocsApp() {
  const { lang, setLang } = useLang();
  const [location, setLocation] = useState(readDocsLocation);
  const { slug } = location;
  const [returnTarget] = useState(readDocsReturnTarget);
  const selectedLang = location.lang ?? lang;

  useEffect(() => {
    if (location.lang && location.lang !== lang) setLang(location.lang);
  }, [lang, location.lang, setLang]);

  useEffect(() => {
    const onPopState = () => setLocation(readDocsLocation());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  function go(nextSlug: string, nextLang = selectedLang) {
    window.history.pushState({}, "", docsPath(nextLang, nextSlug));
    setLocation({ lang: nextLang, slug: nextSlug });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function changeLang(nextLang: DocsLocale) {
    setLang(nextLang);
    go(slug, nextLang);
  }

  const page = PAGE_BY_SLUG.get(slug);
  const fromManagement = new URL(returnTarget).searchParams.get("view") === "management";
  const returnLabel = fromManagement
    ? selectedLang === "pt" ? "Voltar ao gerenciamento" : selectedLang === "es" ? "Volver a gestión" : "Back to management"
    : selectedLang === "pt" ? "Voltar ao site" : selectedLang === "es" ? "Volver al sitio" : "Back to site";
  return (
    <div className="docs-shell">
      <header className="docs-header">
        <a className="docs-brand" href={docsPath(selectedLang)} onClick={e => { e.preventDefault(); go(""); }}>
          <img className="docs-brand-mark" src="/logo.png" alt="Ziggs" />
          <span>Ziggs <small>DOCS</small></span>
        </a>
        <nav className="docs-header-actions" aria-label="Documentation actions">
          <a href={returnTarget} className="docs-site-link"><i className="ti ti-arrow-left" aria-hidden="true" /> <span>{returnLabel}</span></a>
          <select value={selectedLang} onChange={e => changeLang(e.target.value as DocsLocale)} aria-label="Language">
            {DOCS_LOCALES.map(locale => <option key={locale} value={locale}>{locale.toUpperCase()}</option>)}
          </select>
        </nav>
      </header>

      <div className="docs-layout">
        <aside className="docs-sidebar">
          <p className="docs-kicker">{selectedLang === "pt" ? "GUIA DO ZIGGS" : selectedLang === "es" ? "GUÍA DE ZIGGS" : "ZIGGS GUIDE"}</p>
          <button className={!slug ? "active" : ""} onClick={() => go("")}>{selectedLang === "pt" ? "Visão geral" : selectedLang === "es" ? "Resumen" : "Overview"}</button>
          {DOCS_PAGES.map(item => (
            <button key={item.slug} className={item.slug === slug ? "active" : ""} onClick={() => go(item.slug)}>
              {localized(item.title, selectedLang)}
            </button>
          ))}
          <div className="docs-sidebar-note">
            {selectedLang === "pt" ? "Mais guias serão adicionados por fluxo, não apenas por comando." : selectedLang === "es" ? "Se añadirán más guías por flujo, no solo por comando." : "More guides will be added by workflow, not only by command."}
          </div>
        </aside>

        <main className="docs-main">
          {!page ? <Overview lang={selectedLang} go={go} /> : page.slug === "commands" ? <CommandsPage lang={selectedLang} /> : GUIDE_COPY[page.slug] ? <FeatureGuidePage lang={selectedLang} page={page} /> : <GuidePage lang={selectedLang} page={page} />}
        </main>
      </div>
    </div>
  );
}

function Overview({ lang, go }: { lang: DocsLocale; go: (slug: string) => void }) {
  const copy = {
    en: { eyebrow: "PUBLIC DOCUMENTATION", title: "Run your guild with less friction.", intro: "A practical guide to Ziggs, its Discord bot and the workflows around events, comps, regears and loot.", start: "Start here", commands: "Browse commands", note: "The command names stay the same in every language. Explanations and examples follow your selected language." },
    pt: { eyebrow: "DOCUMENTAÇÃO PÚBLICA", title: "Gerencie sua guilda com menos atrito.", intro: "Um guia prático do Ziggs, do bot Discord e dos fluxos de eventos, comps, regears e loot.", start: "Começar", commands: "Ver comandos", note: "Os nomes dos comandos permanecem iguais em todos os idiomas. Explicações e exemplos seguem o idioma selecionado." },
    es: { eyebrow: "DOCUMENTACIÓN PÚBLICA", title: "Gestiona tu guild con menos fricción.", intro: "Una guía práctica de Ziggs, su bot de Discord y los flujos de eventos, comps, regears y loot.", start: "Empezar", commands: "Ver comandos", note: "Los nombres de los comandos son iguales en todos los idiomas. Las explicaciones y ejemplos siguen el idioma seleccionado." },
  }[lang];
  return (
    <>
      <p className="docs-eyebrow">{copy.eyebrow}</p>
      <h1>{copy.title}</h1>
      <p className="docs-lead">{copy.intro}</p>
      <div className="docs-overview-actions">
        <button className="docs-primary" onClick={() => go("getting-started")}>{copy.start} <i className="ti ti-arrow-right" /></button>
        <button className="docs-secondary" onClick={() => go("commands")}>{copy.commands}</button>
      </div>
      <div className="docs-note"><i className="ti ti-language" /> {copy.note}</div>
      <div className="docs-card-grid">
        {DOCS_PAGES.map(page => <button key={page.slug} className="docs-card" onClick={() => go(page.slug)}><strong>{localized(page.title, lang)}</strong><span>{localized(page.description, lang)}</span><i className="ti ti-arrow-up-right" /></button>)}
      </div>
    </>
  );
}

function GuidePage({ lang, page }: { lang: DocsLocale; page: (typeof DOCS_PAGES)[number] }) {
  const copy = {
    "getting-started": {
      en: { body: "The shortest path from a fresh Discord server to a working guild workflow.", steps: ["Invite the Ziggs bot and grant only the Discord permissions your chosen features need.", "Log in to the Ziggs site with Discord and select the server where the bot is active.", "Set the Albion guild name and region before testing registration or event features.", "Configure the event channel and any optional regear, lootlog, nodes, voice or audit channels.", "Run /balance to verify the bot connection, then register a character with /register register:CharacterName."] },
      pt: { body: "O caminho mais curto de um servidor Discord novo até um fluxo de guilda funcionando.", steps: ["Convide o bot Ziggs e conceda apenas as permissões Discord necessárias para as features escolhidas.", "Entre no site Ziggs com Discord e selecione o servidor onde o bot está ativo.", "Configure o nome e a região da guilda Albion antes de testar registro ou eventos.", "Configure o canal de eventos e, se necessário, os canais de regear, lootlog, nodes, voz ou auditoria.", "Execute /balance para verificar a conexão e registre um personagem com /register register:NomeDoPersonagem."] },
      es: { body: "El camino más corto desde un servidor Discord nuevo hasta un flujo de guild funcionando.", steps: ["Invita al bot Ziggs y concede solo los permisos de Discord que necesiten las funciones elegidas.", "Inicia sesión en el sitio Ziggs con Discord y selecciona el servidor donde está activo el bot.", "Configura el nombre y la región de la guild de Albion antes de probar registros o eventos.", "Configura el canal de eventos y, si hace falta, los canales de regear, lootlog, nodes, voz o auditoría.", "Ejecuta /balance para comprobar la conexión y registra un personaje con /register register:NombreDelPersonaje."] },
    }[lang],
    "guild-setup": {
      en: { body: "Most bot behavior is controlled by guild configuration. Start with the required pieces, then enable optional workflows.", steps: ["Set the Albion guild name and region so character lookups use the correct server.", "Choose the register role and make sure the bot's highest role is above it.", "Set the events channel for mass-info and configure review/thread channels only for workflows you use.", "Use command permissions to disable a command, allow everyone, restrict roles or require admin.", "Remember that Discord permissions, Ziggs command permissions and event role gates are separate checks."] },
      pt: { body: "A maior parte do comportamento do bot é controlada pela configuração da guilda. Comece pelo obrigatório e depois habilite os fluxos opcionais.", steps: ["Defina o nome e a região da guilda Albion para que as buscas usem o servidor correto.", "Escolha o cargo de registro e confirme que o cargo mais alto do bot está acima dele.", "Defina o canal de eventos para o mass-info e configure canais de review/threads só para os fluxos usados.", "Use as permissões de comando para desativar, liberar para todos, restringir cargos ou exigir admin.", "Lembre que permissões Discord, permissões de comando do Ziggs e gates de função são verificações separadas."] },
      es: { body: "La mayor parte del comportamiento del bot se controla desde la configuración de la guild. Empieza por lo obligatorio y activa después los flujos opcionales.", steps: ["Define el nombre y la región de la guild de Albion para usar el servidor correcto.", "Elige el rol de registro y confirma que el rol más alto del bot esté por encima.", "Define el canal de eventos para el mass-info y configura canales de review/hilos solo para los flujos que uses.", "Usa los permisos de comandos para desactivar, permitir a todos, restringir roles o exigir admin.", "Recuerda que los permisos de Discord, los permisos de Ziggs y los gates de función son comprobaciones separadas."] },
    }[lang],
  }[page.slug as "getting-started" | "guild-setup"];
  return (
    <>
      <p className="docs-eyebrow">{localized(page.title, lang)}</p>
      <h1>{localized(page.title, lang)}</h1>
      <p className="docs-lead">{copy.body}</p>
      <ol className="docs-steps">{copy.steps.map((step, i) => <li key={step}><span>{String(i + 1).padStart(2, "0")}</span><p>{step}</p></li>)}</ol>
    </>
  );
}

function FeatureGuidePage({ lang, page }: { lang: DocsLocale; page: (typeof DOCS_PAGES)[number] }) {
  const copy = GUIDE_COPY[page.slug];
  return (
    <>
      <p className="docs-eyebrow">{localized(page.title, lang)}</p>
      <h1>{localized(page.title, lang)}</h1>
      <p className="docs-lead">{localized(copy.body, lang)}</p>
      <ol className="docs-steps">{copy.steps[lang].map((step, i) => <li key={step}><span>{String(i + 1).padStart(2, "0")}</span><p>{step}</p></li>)}</ol>
    </>
  );
}

function CommandsPage({ lang }: { lang: DocsLocale }) {
  const headings = {
    en: { eyebrow: "REFERENCE", title: "Commands that are actually available.", intro: "Canonical slash command names are kept unchanged. Check permissions and prerequisites before testing them in production.", permission: "Permission", prerequisites: "Before you start", examples: "Examples", syntax: "Syntax" },
    pt: { eyebrow: "REFERÊNCIA", title: "Comandos que estão realmente disponíveis.", intro: "Os nomes canônicos dos slash commands permanecem iguais. Confira permissões e pré-requisitos antes de testar em produção.", permission: "Permissão", prerequisites: "Antes de começar", examples: "Exemplos", syntax: "Sintaxe" },
    es: { eyebrow: "REFERENCIA", title: "Comandos que están realmente disponibles.", intro: "Los nombres canónicos de los slash commands permanecen iguales. Revisa permisos y requisitos antes de probarlos en producción.", permission: "Permiso", prerequisites: "Antes de empezar", examples: "Ejemplos", syntax: "Sintaxis" },
  }[lang];
  return (
    <>
      <p className="docs-eyebrow">{headings.eyebrow}</p>
      <h1>{headings.title}</h1>
      <p className="docs-lead">{headings.intro}</p>
      {COMMANDS.map(command => <article className="docs-command" key={command.id}>
        <div className="docs-command-head"><span className="docs-category">{localized(CATEGORY_LABELS[command.category], lang)}</span><h2>{command.command}</h2></div>
        <p>{localized(command.description, lang)}</p>
        <dl className="docs-meta"><div><dt>{headings.permission}</dt><dd>{localized(command.permission, lang)}</dd></div><div><dt>{headings.prerequisites}</dt><dd>{localized(command.prerequisites, lang)}</dd></div></dl>
        <h3>{headings.syntax}</h3>
        <div className="docs-code-list">{command.syntax.map(line => <code key={line}>{line}</code>)}</div>
        <h3>{headings.examples}</h3>
        <div className="docs-examples">{command.examples.map(example => <div key={example.input}><code>{example.input}</code><span>{localized(example.result, lang)}</span></div>)}</div>
      </article>)}
    </>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><LangProvider><DocsApp /></LangProvider></React.StrictMode>,
);
