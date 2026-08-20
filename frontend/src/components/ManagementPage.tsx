import { lazy, useEffect, useState, type ReactNode } from "react";
import { useT, type TKey } from "../i18n";
import { api, type Permissions } from "../api";
import { DOCS_URL } from "../docs-url";
import AdBanner from "./AdBanner";

const CompBuilder = lazy(() => import("./CompBuilder"));
const EventsPage = lazy(() => import("./EventsPage"));
const RegearPage = lazy(() => import("./RegearPage"));
const ReconcileSection = lazy(() => import("./ReconcileSection"));
const AuditLogConsole = lazy(() => import("./AuditLogConsole"));
const MemberPanel = lazy(() => import("./MemberPanel"));
const MemberEvents = lazy(() => import("./MemberEvents"));
const MemberComps = lazy(() => import("./MemberComps"));
const EnergyAdmin = lazy(() => import("./EnergyAdmin"));

interface Props { guildId?: string; perms: Permissions; active?: boolean; empty?: ReactNode }

type Tab = "meu" | "m_events" | "m_comps" | "comps" | "events" | "regear" | "reconcile" | "logs" | "energy";

// Abas member-facing ("meu", "m_events", "m_comps") são visíveis a qualquer
// membro logado, MAS as de eventos/comps read-only são escondidas quando o
// usuário já tem a permissão admin correspondente (events.view ou comps.view)
// — pra ele a versão admin é a certa, não a duplicata read-only. "meu" sempre
// aparece. "energy" exige energy.manage. As abas admin legadas mantêm gates.
const TABS: { id: Tab; icon: string; label: TKey; desc: TKey; show: (p: Permissions, eventsActive: boolean) => boolean }[] = [
  { id: "meu",       icon: "ti-user",            label: "memberPanel", desc: "managementMeDesc", show: () => true },
  { id: "m_events",  icon: "ti-calendar-event",  label: "memberEventsLabel", desc: "managementMemberEventsDesc", show: p => !p["events.view"] },
  { id: "m_comps",   icon: "ti-layout-grid",     label: "memberCompsLabel", desc: "managementMemberCompsDesc", show: p => !p["comps.view"] },
  { id: "comps",     icon: "ti-layout-grid",     label: "comps",  desc: "managementCompsDesc", show: p => p["comps.view"] },
  { id: "events",    icon: "ti-calendar-event",  label: "events", desc: "managementEventsDesc", show: p => p["events.view"] },
  { id: "regear",    icon: "ti-receipt-refund",  label: "regear", desc: "managementRegearDesc", show: (p, ev) => p["events.manage"] && ev },
  { id: "reconcile", icon: "ti-scale",           label: "rec",    desc: "managementReconcileDesc", show: (p, ev) => p["events.manage"] && ev },
  { id: "energy",   icon: "ti-bolt",             label: "energyAdminLabel", desc: "managementEnergyDesc", show: p => !!p["energy.manage"] },
  { id: "logs",      icon: "ti-terminal-2",      label: "managementLogs", desc: "managementLogsDesc", show: p => p["guild.admin"] },
];

export default function ManagementPage({ guildId, perms, active = true, empty }: Props) {
  const t = useT();
  const [eventsActive, setEventsActive] = useState(true); // otimista até o fetch resolver — evita esconder a aba num flash
  useEffect(() => {
    if (!guildId) return;
    api.guildInfo(guildId).then(g => setEventsActive(!!g.settings.events_channel_id)).catch(() => {});
  }, [guildId]);
  const visible = guildId ? TABS.filter(tb => tb.show(perms, eventsActive)) : [];
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

  const docsLink = (index: number) => (
    <a className="management-docs-link" href={docsHref}
      onClick={() => sessionStorage.setItem("ziggs-docs-origin", docsOrigin)}>
      <span className="management-rail-index">{String(index).padStart(2, "0")}</span>
      <i className="ti ti-book-2" aria-hidden="true" />
      <span><strong>{t("docsNav")}</strong><small>{t("managementDocsDesc")}</small></span>
      <i className="ti ti-arrow-up-right management-external" aria-hidden="true" />
    </a>
  );

  if (visible.length === 0) {
    return (
      <div className="lootlog-page management-shell">
        <div className="management-layout">
          <aside className="management-rail" aria-label={t("management")}>{docsLink(1)}</aside>
          <div className="management-workspace">{empty ?? <p className="hint">{t("escNoAccess")}</p>}</div>
        </div>
      </div>
    );
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
              {docsLink(visible.length + 1)}
            </aside>
            <AdBanner slot="management" variant="skyscraper" />
          </div>
        )}
        <div className="management-workspace">
          {/* Logs: o console já tem header próprio — sem quadrante de título redundante */}
          {!hideTabs && activeMeta && activeTab !== "logs" && (
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
                {tb.id === "meu" && <MemberPanel guildId={guildId!} />}
                {tb.id === "m_events" && <MemberEvents guildId={guildId!} />}
                {tb.id === "m_comps" && <MemberComps guildId={guildId!} />}
                {tb.id === "comps" && <CompBuilder perms={perms} onOpenChange={setCompOpen} />}
                {tb.id === "events" && <EventsPage perms={perms} active={active && activeTab === tb.id} />}
                {tb.id === "regear" && <RegearPage guildId={guildId!} active={active && activeTab === tb.id} />}
                {tb.id === "reconcile" && <ReconcileSection guildId={guildId!} />}
                {tb.id === "energy" && <EnergyAdmin guildId={guildId!} />}
                {tb.id === "logs" && <AuditLogConsole guildId={guildId!} active={active && activeTab === tb.id} />}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
