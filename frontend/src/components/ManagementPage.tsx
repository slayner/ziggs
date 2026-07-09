import { lazy, useEffect, useState } from "react";
import { useT, type TKey } from "../i18n";
import type { Permissions } from "../api";
import AdBanner from "./AdBanner";

const CompBuilder = lazy(() => import("./CompBuilder"));
const EventsPage = lazy(() => import("./EventsPage"));
const RegearPage = lazy(() => import("./RegearPage"));
const ReconcileSection = lazy(() => import("./ReconcileSection"));

interface Props { guildId: string; perms: Permissions; active?: boolean }

type Tab = "comps" | "events" | "regear" | "reconcile";

const TABS: { id: Tab; icon: string; label: TKey; show: (p: Permissions) => boolean }[] = [
  { id: "comps",     icon: "ti-layout-grid",     label: "comps",  show: p => p["comps.view"] },
  { id: "events",    icon: "ti-calendar-event",  label: "events", show: p => p["events.view"] },
  { id: "regear",    icon: "ti-receipt-refund",  label: "regear", show: p => p["events.manage"] },
  { id: "reconcile", icon: "ti-scale",           label: "rec",    show: p => p["events.manage"] },
];

export default function ManagementPage({ guildId, perms, active = true }: Props) {
  const t = useT();
  const visible = TABS.filter(tb => tb.show(perms));
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

  if (visible.length === 0) {
    return <div className="lootlog-page"><p className="hint">{t("escNoAccess")}</p></div>;
  }

  return (
    <div className="lootlog-page">
      <div className="grid items-start gap-5 md:grid-cols-[220px_1fr]">
        <aside className="flex flex-col gap-1 rounded-xl border border-zinc-800 bg-zinc-900/40 p-3">
          {visible.map(tb => (
            <button
              key={tb.id}
              className={`flex items-center gap-2 rounded-md px-3 py-2 text-sm text-left transition-colors ${
                activeTab === tb.id ? "bg-zinc-700/60 text-zinc-100" : "text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-200"
              }`}
              onClick={() => setTab(tb.id)}
            >
              <i className={`ti ${tb.icon}`} aria-hidden="true" /> {t(tb.label)}
            </button>
          ))}
          <div className="mt-2">
            <AdBanner size="rectangle" />
          </div>
        </aside>
        <div>
          {visible.map(tb => {
            // Só monta se já foi visitada (ou é a ativa); visited mantém
            // ela viva depois. Inativa → display:none, estado preservado.
            if (!(activeTab === tb.id || visited.has(tb.id))) return null;
            return (
              <div key={tb.id} style={activeTab === tb.id ? undefined : { display: "none" }}>
                {tb.id === "comps" && <CompBuilder perms={perms} />}
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
