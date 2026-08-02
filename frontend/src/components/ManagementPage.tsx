import { lazy, useEffect, useState } from "react";
import { useT, type TKey } from "../i18n";
import { api, type Permissions } from "../api";
import { DOCS_URL } from "../docs-url";
import AdBanner from "./AdBanner";

const CompBuilder = lazy(() => import("./CompBuilder"));
const EventsPage = lazy(() => import("./EventsPage"));
const RegearPage = lazy(() => import("./RegearPage"));
const ReconcileSection = lazy(() => import("./ReconcileSection"));

interface Props { guildId: string; perms: Permissions; active?: boolean }

type Tab = "comps" | "events" | "regear" | "reconcile";

// "comps" só faz sentido com Eventos ativo (é lá que uma comp é atribuída a
// um evento) — eventsActive vem de settings.events_channel_id configurado,
// mesmo sinal que GuildConfig usa pra "Eventos ligado".
const TABS: { id: Tab; icon: string; label: TKey; desc: TKey; show: (p: Permissions, eventsActive: boolean) => boolean }[] = [
  { id: "comps",     icon: "ti-layout-grid",     label: "comps",  desc: "managementCompsDesc", show: (p, ev) => p["comps.view"] && ev },
  { id: "events",    icon: "ti-calendar-event",  label: "events", desc: "managementEventsDesc", show: p => p["events.view"] },
  { id: "regear",    icon: "ti-receipt-refund",  label: "regear", desc: "managementRegearDesc", show: p => p["events.manage"] },
  { id: "reconcile", icon: "ti-scale",           label: "rec",    desc: "managementReconcileDesc", show: p => p["events.manage"] },
];

export default function ManagementPage({ guildId, perms, active = true }: Props) {
  const t = useT();
  const [eventsActive, setEventsActive] = useState(true); // otimista até o fetch resolver — evita esconder a aba num flash
  useEffect(() => {
    api.guildInfo(guildId).then(g => setEventsActive(!!g.settings.events_channel_id)).catch(() => {});
  }, [guildId]);
  const visible = TABS.filter(tb => tb.show(perms, eventsActive));
  const [tab, setTab] = useState<Tab | null>(null);
  // activeTab = sub-aba ativa (id). `active` (prop) = visibilidade no App
  // (keep-alive) — repassada pro RegearPage pra pausar o poll de 15s quando
  // o ManagementPage inteiro tá escondido.
  const activeTab = tab && visible.some(tb => tb.id === tab) ? tab : (visible[0]?.id ?? null);
  // Keep-alive: uma sub-aba monta na primeira visita e fica montada
  // (display:none quando inativa). Trocar de aba não refaz fetch — pra um
  // painel admin onde se mexe muito entre comps/events/regear/reconcile.
  const [visited, setVisited] = useState<Set<Tab>>(new Set());
  useEffect(() => {
    if (activeTab) setVisited(prev => prev.has(activeTab) ? prev : new Set(prev).add(activeTab));
  }, [activeTab]);
  // Uma comp aberta (master-detail: lista + painel de detalhe) já é larga —
  // espremida ao lado da barra de abas fica apertada. Some com a barra
  // enquanto uma comp estiver aberta, devolve a largura toda pro editor.
  const [compOpen, setCompOpen] = useState(false);
  const hideTabs = activeTab === "comps" && compOpen;
  const activeMeta = visible.find(tb => tb.id === activeTab);

  const docsOrigin = `${window.location.origin}/?view=management`;
  const docsHref = (() => {
    const url = new URL(DOCS_URL, window.location.href);
    url.searchParams.set("from", docsOrigin);
    return url.toString();
  })();

  if (visible.length === 0) {
    return <div className="lootlog-page"><p className="hint">{t("escNoAccess")}</p></div>;
  }

  return (
    <div className="lootlog-page management-shell">
      {!hideTabs && (
        <header className="management-hero">
          <span className="management-hero-icon"><i className="ti ti-command" aria-hidden="true" /></span>
          <span>
            <small>{t("managementKicker")}</small>
            <h1>{t("management")}</h1>
            <p>{t("managementIntro")}</p>
          </span>
        </header>
      )}
      <div className={hideTabs ? undefined : "management-layout"}>
        {!hideTabs && (
          <div className="management-rail-wrap">
            <aside className="management-rail" aria-label={t("management")}>
              {visible.map((tb, index) => (
                <button
                  key={tb.id}
                  className={activeTab === tb.id ? "active" : ""}
                  onClick={() => setTab(tb.id)}
                >
                  <span className="management-rail-index">{String(index + 1).padStart(2, "0")}</span>
                  <i className={`ti ${tb.icon}`} aria-hidden="true" />
                  <span><strong>{t(tb.label)}</strong><small>{t(tb.desc)}</small></span>
                </button>
              ))}
              <a className="management-docs-link" href={docsHref}
                onClick={() => sessionStorage.setItem("ziggs-docs-origin", docsOrigin)}>
                <span className="management-rail-index">{String(visible.length + 1).padStart(2, "0")}</span>
                <i className="ti ti-book-2" aria-hidden="true" />
                <span><strong>{t("docsNav")}</strong><small>{t("managementDocsDesc")}</small></span>
                <i className="ti ti-arrow-up-right management-external" aria-hidden="true" />
              </a>
            </aside>
            <AdBanner variant="skyscraper" />
          </div>
        )}
        <div className="management-workspace">
          {!hideTabs && activeMeta && (
            <div className="management-workspace-head">
              <span><i className={`ti ${activeMeta.icon}`} aria-hidden="true" /></span>
              <div><small>{t("managementWorkspace")}</small><h2>{t(activeMeta.label)}</h2></div>
              <p>{t(activeMeta.desc)}</p>
            </div>
          )}
          {visible.map(tb => {
            // Só monta se já foi visitada (ou é a ativa); visited mantém
            // ela viva depois. Inativa → display:none, estado preservado.
            if (!(activeTab === tb.id || visited.has(tb.id))) return null;
            return (
              <div key={tb.id} style={activeTab === tb.id ? undefined : { display: "none" }}>
                {tb.id === "comps" && <CompBuilder perms={perms} onOpenChange={setCompOpen} />}
                {tb.id === "events" && <EventsPage perms={perms} active={active && activeTab === tb.id} />}
                {tb.id === "regear" && <RegearPage guildId={guildId} active={active && activeTab === tb.id} />}
                {tb.id === "reconcile" && <ReconcileSection guildId={guildId} />}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
