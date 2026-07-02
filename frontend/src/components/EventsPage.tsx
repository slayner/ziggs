import { useEffect, useState } from "react";
import { api, type CatalogRole, type Death, type EventDetail, type EventSummary, type LootReconcile, type MissingItem, type Participant, type PayoutPreview, type Permissions, type RegearEstimate, type RegearItemEstimate, type VerificationStep } from "../api";
import { useT, type TKey } from "../i18n";

const PIPELINE = ["scheduled", "in_progress", "definition", "verification", "waiting", "finalized"];
const TYPE_LABELS: Record<string, string> = {
  lootsplit: "Lootsplit", regear: "Regear", lootsplit_regear: "Lootsplit + Regear",
};
const STATE_KEYS: Record<string, TKey> = {
  scheduled: "stateScheduled", in_progress: "stateInProgress", definition: "stateDefinition",
  verification: "stateVerification", waiting: "stateWaiting", finalized: "stateFinalized",
  cancelled: "stateCancelled", deleted: "stateDeleted",
};
const STEP_KEYS: Record<string, TKey> = {
  participation: "stepParticipation", missing_loots: "stepMissingLoots",
  tab_value: "stepTabValue", tab_image: "stepTabImage",
  battles: "stepBattles", nodes: "stepNodes",
};

function fmt(n: number): string {
  return n.toLocaleString("pt-BR");
}

export default function EventsPage({ perms }: { perms: Permissions }) {
  const t = useT();
  const [events, setEvents] = useState<EventSummary[]>([]);
  const [sel, setSel] = useState<number | null>(null);
  const [detail, setDetail] = useState<EventDetail | null>(null);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);

  function refreshList() {
    api.listEvents().then(setEvents).catch((e) => setError(String(e.message)));
  }
  useEffect(refreshList, []);
  useEffect(() => {
    if (sel != null) api.getEvent(sel).then(setDetail).catch(() => setDetail(null));
    else setDetail(null);
  }, [sel]);

  async function create() {
    if (!title.trim()) return;
    const ev = await api.createEvent(title.trim());
    setTitle("");
    refreshList();
    setSel(ev.id);
  }

  function act(p: Promise<EventDetail>) {
    setError(null);
    p.then((d) => { setDetail(d); refreshList(); }).catch((e) => setError(String(e.message)));
  }

  return (
    <div className="container">
      <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
        {/* Lista de eventos */}
        <div style={{ flex: "1 1 280px" }}>
          <div className="card">
            {perms["events.create"] && (
              <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
                <input
                  className="input" style={{ flex: 1 }} placeholder={t("evCtaNamePlaceholder")}
                  value={title} onChange={(e) => setTitle(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && create()}
                />
                <button className="btn primary" onClick={create}>
                  <i className="ti ti-plus" aria-hidden /> {t("createBtn")}
                </button>
              </div>
            )}

            {error && <p style={{ color: "#e07a7a", fontSize: 13, marginBottom: 10 }}>{error}</p>}
            {events.length === 0 && <p className="muted">{t("noEventsYet")}</p>}
            {events.map((e) => (
              <button key={e.id} className={"event-row" + (sel === e.id ? " sel" : "")} onClick={() => setSel(e.id)}>
                <i className="ti ti-calendar-event" style={{ color: "var(--muted)" }} aria-hidden />
                <span style={{ flex: 1 }}>{e.title || `CTA #${e.id}`}</span>
                {e.type && <span className="hint">{TYPE_LABELS[e.type] ?? e.type}</span>}
                <StatePill state={e.state} />
              </button>
            ))}
          </div>
        </div>

        {/* Detalhe do evento */}
        <div style={{ flex: "2 1 360px" }}>
          {detail ? (
            <EventDetailCard detail={detail} act={act} canManage={perms["events.manage"]} />
          ) : (
            <div className="card"><p className="muted">{t("selectEventHint")}</p></div>
          )}
        </div>
      </div>
    </div>
  );
}

function StatePill({ state }: { state: string }) {
  const t = useT();
  const cls = state === "cancelled" || state === "deleted" ? "bad"
    : state === "finalized" ? "done" : "cur";
  const key = STATE_KEYS[state];
  return <span className={"state-pill " + cls}>{key ? t(key) : state}</span>;
}

function EventDetailCard({ detail, act, canManage }: { detail: EventDetail; act: (p: Promise<EventDetail>) => void; canManage: boolean }) {
  const t = useT();
  const curIdx = PIPELINE.indexOf(detail.state);
  const [pName, setPName] = useState("");
  const [pPct, setPPct] = useState("");

  function addParticipant() {
    if (!pName.trim()) return;
    const uid = Date.now();
    act(api.addParticipant(detail.id, { user_id: uid, user_name: pName.trim(), percent: Number(pPct) || 0 }));
    setPName(""); setPPct("");
  }

  return (
    <div className="card">
      {/* Cabeçalho */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
        <span style={{ fontWeight: 600 }}>{detail.title || `CTA #${detail.id}`}</span>
        <StatePill state={detail.state} />
        {detail.type && <span className="hint">· {TYPE_LABELS[detail.type] ?? detail.type}</span>}
      </div>

      {/* Pipeline */}
      <div className="pipe">
        {PIPELINE.map((s, i) => (
          <span key={s} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span className={"state-pill " + (i === curIdx ? "cur" : curIdx >= 0 && i < curIdx ? "done" : "")}>
              {t(STATE_KEYS[s])}
            </span>
            {i < PIPELINE.length - 1 && <i className="ti ti-chevron-right arrow" aria-hidden />}
          </span>
        ))}
      </div>

      {/* Definição: escolher tipo */}
      {detail.state === "definition" && (
        <div style={{ marginBottom: 16 }}>
          <div className="hint" style={{ marginBottom: 6 }}>{t("ctaTypeLabel")}</div>
          {canManage ? (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {Object.entries(TYPE_LABELS).map(([v, label]) => (
                <button key={v} className={"btn" + (detail.type === v ? " primary" : "")}
                  onClick={() => act(api.setType(detail.id, v))}>
                  {label}
                </button>
              ))}
            </div>
          ) : (
            <span className="hint">{detail.type ? TYPE_LABELS[detail.type] ?? detail.type : "—"}</span>
          )}
          {!detail.type && canManage && (
            <p className="hint" style={{ marginTop: 6 }}>
              <i className="ti ti-info-circle" aria-hidden /> {t("defineTypeHint")}
            </p>
          )}
        </div>
      )}

      {/* Verificação: passos */}
      {detail.state === "verification" && (
        <div style={{ marginBottom: 16 }}>
          <div className="hint" style={{ marginBottom: 8 }}>{t("requiredStepsLabel")}</div>
          {detail.verification.map((v) =>
            v.step === "tab_value" ? (
              <TabValueStep key={v.step} v={v} eventId={detail.id} act={canManage ? act : () => {}} />
            ) : v.step === "missing_loots" ? (
              <LootStep key={v.step} v={v} eventId={detail.id} act={act} />
            ) : (
              <button key={v.step} className="step-check" disabled={!canManage}
                onClick={() => canManage && act(api.setStep(detail.id, v.step, !v.completed))}>
                <span className={"box" + (v.completed ? " on" : "")}>
                  {v.completed && <i className="ti ti-check" aria-hidden />}
                </span>
                {STEP_KEYS[v.step] ? t(STEP_KEYS[v.step]) : v.step}
              </button>
            )
          )}
        </div>
      )}

      {/* Espera: revisão + preview de payout */}
      {detail.state === "waiting" && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontWeight: 600, marginBottom: 10, display: "flex", alignItems: "center", gap: 8 }}>
            <i className="ti ti-clipboard-check" aria-hidden style={{ color: "var(--gold)" }} />
            {t("reviewBeforeFinalize")}
          </div>
          {detail.payout ? (
            <PayoutTable payout={detail.payout} type={detail.type} />
          ) : (
            <p className="hint">{t("addParticipantsForPayout")}</p>
          )}
          <DeathsSection detail={detail} act={act} />
        </div>
      )}

      {/* Finalizado: resumo */}
      {detail.state === "finalized" && detail.payout && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontWeight: 600, marginBottom: 10, display: "flex", alignItems: "center", gap: 8 }}>
            <i className="ti ti-circle-check" aria-hidden style={{ color: "#6bbf73" }} />
            {t("eventFinalized")}
          </div>
          <PayoutTable payout={detail.payout} type={detail.type} />
        </div>
      )}

      {/* Participantes (colapsável) */}
      <ParticipantsSection
        detail={detail} act={act} canManage={canManage}
        pName={pName} setPName={setPName}
        pPct={pPct} setPPct={setPPct}
        onAdd={addParticipant}
      />

      {/* Botões de transição */}
      {canManage && (
        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12, marginTop: 4 }}>
          <div className="hint" style={{ marginBottom: 6 }}>{t("moveToLabel")}</div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {detail.allowed_transitions.length === 0 && <span className="muted">{t("finalStateLabel")}</span>}
            {detail.allowed_transitions.map((to) => (
              <button key={to}
                className={"btn" + (to === "cancelled" || to === "deleted" ? "" : " primary")}
                onClick={() => act(api.transition(detail.id, to))}>
                {STATE_KEYS[to] ? t(STATE_KEYS[to]) : to}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Passo especial: valor da tab ─────────────────────────────────────────

function TabValueStep({ v, eventId, act }: {
  v: VerificationStep;
  eventId: number;
  act: (p: Promise<EventDetail>) => void;
}) {
  const t = useT();
  const [val, setVal] = useState(String(v.data?.value ?? ""));
  const savedVal = v.data?.value as number | undefined;

  function save() {
    const n = Number(String(val).replace(/\D/g, ""));
    if (!n) return;
    act(api.setStep(eventId, "tab_value", true, { value: n }));
  }

  return (
    <div className="step-check" style={{ flexDirection: "column", alignItems: "flex-start", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, width: "100%" }}>
        <span className={"box" + (v.completed ? " on" : "")}>
          {v.completed && <i className="ti ti-check" aria-hidden />}
        </span>
        <span>{t(STEP_KEYS.tab_value)}</span>
        {v.completed && savedVal !== undefined && (
          <span className="hint" style={{ marginLeft: "auto" }}>{fmt(savedVal)} {t("silverWord")}</span>
        )}
      </div>
      {!v.completed && (
        <div style={{ display: "flex", gap: 6, paddingLeft: 26 }}>
          <input
            className="input" style={{ width: 180 }}
            placeholder={t("tabValuePlaceholder")}
            value={val}
            onChange={(e) => setVal(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && save()}
          />
          <button className="btn primary" onClick={save} disabled={!val}>{t("defineBtn")}</button>
        </div>
      )}
      {v.completed && (
        <button className="btn" style={{ marginLeft: 26, fontSize: 12, padding: "3px 8px" }}
          onClick={() => { act(api.setStep(eventId, "tab_value", false)); setVal(""); }}>
          <i className="ti ti-edit" aria-hidden /> {t("changeBtn")}
        </button>
      )}
    </div>
  );
}

// ── Passo: loots faltantes ───────────────────────────────────────────────

function LootStep({ v, eventId, act }: {
  v: VerificationStep; eventId: number; act: (p: Promise<EventDetail>) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [loot, setLoot] = useState<LootReconcile | null>(null);
  const [loading, setLoading] = useState(false);

  function toggle() {
    if (!open && !loot) {
      setLoading(true);
      api.getLoot(eventId).then(r => { setLoot(r); setLoading(false); }).catch(() => setLoading(false));
    }
    setOpen(o => !o);
  }

  function refresh() {
    api.getLoot(eventId).then(setLoot);
  }

  return (
    <div style={{ marginBottom: 4 }}>
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <button className="step-check" style={{ flex: 1 }} onClick={toggle}>
          <span className={"box" + (v.completed ? " on" : "")}>
            {v.completed && <i className="ti ti-check" aria-hidden />}
          </span>
          {t(STEP_KEYS.missing_loots)}
          <i className={"ti " + (open ? "ti-chevron-down" : "ti-chevron-right")} style={{ marginLeft: "auto", color: "var(--hint)" }} aria-hidden />
        </button>
        <button className="btn" style={{ fontSize: 11, padding: "3px 8px", flexShrink: 0 }}
          onClick={() => act(api.setStep(eventId, "missing_loots", !v.completed))}>
          {v.completed ? t("uncheckBtn") : t("markOkBtn")}
        </button>
      </div>
      {open && (
        <div style={{ paddingLeft: 26, marginTop: 8 }}>
          {loading && <p className="hint">{t("loading")}</p>}
          {loot && <LootPanel reconcile={loot} onRefresh={refresh} />}
        </div>
      )}
    </div>
  );
}

function LootPanel({ reconcile, onRefresh }: { reconcile: LootReconcile; onRefresh: () => void }) {
  const t = useT();
  return (
    <div>
      {/* Status dos uploads */}
      <div style={{ display: "flex", gap: 6, marginBottom: 10, flexWrap: "wrap", alignItems: "center" }}>
        <span className={"badge " + (reconcile.has_loot_log ? "done" : "bad")}>
          <i className={"ti " + (reconcile.has_loot_log ? "ti-check" : "ti-x")} aria-hidden />
          {" "}{t("lootLogLabel")}
        </span>
        <span className={"badge " + (reconcile.has_chest_log ? "done" : "bad")}>
          <i className={"ti " + (reconcile.has_chest_log ? "ti-check" : "ti-x")} aria-hidden />
          {" "}{t("chestInventoryLabel")}
        </span>
        <button className="btn" style={{ fontSize: 11, padding: "3px 8px", marginLeft: "auto" }} onClick={onRefresh}>
          <i className="ti ti-refresh" aria-hidden /> {t("updateBtn")}
        </button>
      </div>

      {!reconcile.has_loot_log ? (
        <p className="hint" style={{ fontSize: 12 }}>
          <i className="ti ti-info-circle" aria-hidden /> {t("waitingLootLogBot")}
        </p>
      ) : reconcile.missing.length === 0 ? (
        <p style={{ color: "#6bbf73", fontSize: 13 }}>
          <i className="ti ti-circle-check" aria-hidden /> {t("allItemsCoveredInChest")}
        </p>
      ) : (
        <MissingItemsTable missing={reconcile.missing} missingValue={reconcile.missing_value} />
      )}

      {reconcile.has_loot_log && reconcile.looted.length > 0 && (
        <details style={{ marginTop: 10 }}>
          <summary style={{ fontSize: 12, color: "var(--hint)", cursor: "pointer", userSelect: "none" }}>
            {t("allLootsLabel")} ({reconcile.looted.length} {t("entriesWord")} — {fmt(reconcile.total_looted_value)} {t("silverWord")})
          </summary>
          <div style={{ marginTop: 6, maxHeight: 220, overflowY: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border)" }}>
                  <th style={{ textAlign: "left", padding: "3px 6px", color: "var(--hint)", fontWeight: 500 }}>{t("playerWord")}</th>
                  <th style={{ textAlign: "left", padding: "3px 6px", color: "var(--hint)", fontWeight: 500 }}>Item</th>
                  <th style={{ textAlign: "right", padding: "3px 6px", color: "var(--hint)", fontWeight: 500 }}>{t("qtyAbbrev")}</th>
                  <th style={{ textAlign: "right", padding: "3px 6px", color: "var(--hint)", fontWeight: 500 }}>{t("colChest")}</th>
                </tr>
              </thead>
              <tbody>
                {reconcile.looted.map((r) => (
                  <tr key={r.id} style={{ borderBottom: "1px solid var(--border)", opacity: r.in_chest ? 0.65 : 1 }}>
                    <td style={{ padding: "3px 6px" }}>{r.looted_by_name}</td>
                    <td style={{ padding: "3px 6px" }}>{r.item_name}</td>
                    <td style={{ textAlign: "right", padding: "3px 6px" }}>{r.quantity}</td>
                    <td style={{ textAlign: "right", padding: "3px 6px", color: r.in_chest ? "#6bbf73" : "#e07a7a", fontWeight: 600 }}>
                      {r.in_chest ? "✓" : "✗"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  );
}

function MissingItemsTable({ missing, missingValue }: { missing: MissingItem[]; missingValue: number }) {
  const t = useT();
  return (
    <div>
      <p style={{ color: "#e07a7a", fontSize: 13, marginBottom: 6 }}>
        <i className="ti ti-alert-triangle" aria-hidden />{" "}
        {missing.length} {missing.length === 1 ? t("missingItemSingular") : t("missingItemPlural")} — {fmt(missingValue)} {t("silverWord")}
      </p>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)" }}>
            <th style={{ textAlign: "left", padding: "4px 6px", color: "var(--hint)", fontWeight: 500 }}>Item</th>
            <th style={{ textAlign: "right", padding: "4px 6px", color: "var(--hint)", fontWeight: 500 }}>{t("colLooted")}</th>
            <th style={{ textAlign: "right", padding: "4px 6px", color: "var(--hint)", fontWeight: 500 }}>{t("colInChest")}</th>
            <th style={{ textAlign: "right", padding: "4px 6px", color: "#e07a7a", fontWeight: 500 }}>{t("colMissing")}</th>
            <th style={{ textAlign: "right", padding: "4px 6px", color: "var(--hint)", fontWeight: 500 }}>{t("colValue")}</th>
          </tr>
        </thead>
        <tbody>
          {missing.map((m, i) => (
            <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
              <td style={{ padding: "4px 6px" }}>{m.item_name}</td>
              <td style={{ textAlign: "right", padding: "4px 6px", color: "var(--muted)" }}>{m.looted_qty}</td>
              <td style={{ textAlign: "right", padding: "4px 6px", color: "var(--muted)" }}>{m.chest_qty}</td>
              <td style={{ textAlign: "right", padding: "4px 6px", color: "#e07a7a", fontWeight: 600 }}>{m.missing_qty}</td>
              <td style={{ textAlign: "right", padding: "4px 6px", color: "var(--muted)" }}>{fmt(m.missing_value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Mortes / Regear ───────────────────────────────────────────────────────

function DeathsSection({ detail, act }: { detail: EventDetail; act: (p: Promise<EventDetail>) => void }) {
  const t = useT();
  const [dName, setDName] = useState("");
  const [dVal, setDVal] = useState("");
  const hasRegear = detail.type === "regear" || detail.type === "lootsplit_regear";
  if (!hasRegear) return null;

  function addDeath() {
    if (!dName.trim()) return;
    const silver = Number(dVal.replace(/\D/g, "")) || 0;
    act(api.addDeath(detail.id, { display_name: dName.trim(), silver_value: silver }));
    setDName(""); setDVal("");
  }

  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8, display: "flex", alignItems: "center", gap: 6 }}>
        <i className="ti ti-skull" aria-hidden style={{ color: "#e07a7a" }} />
        {t("deathsRegearTitle")}
      </div>
      {detail.deaths.length === 0 && (
        <p className="hint" style={{ marginBottom: 8 }}>{t("noDeathsRecorded")}</p>
      )}
      {detail.deaths.map((d) => (
        <DeathRow key={d.id} death={d} eventId={detail.id} act={act} />
      ))}
      <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
        <input className="input" style={{ flex: 1, minWidth: 120 }} placeholder={t("playerNamePlaceholder")}
          value={dName} onChange={(e) => setDName(e.target.value)} />
        <input className="input" style={{ width: 140 }} placeholder={t("silverValuePlaceholder")}
          value={dVal} onChange={(e) => setDVal(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && addDeath()} />
        <button className="btn primary" onClick={addDeath} disabled={!dName.trim()}>
          <i className="ti ti-plus" aria-hidden /> {t("addBtn")}
        </button>
      </div>
    </div>
  );
}

function DeathRow({ death, eventId, act }: {
  death: Death; eventId: number; act: (p: Promise<EventDetail>) => void;
}) {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(String(death.silver_value));

  function saveVal() {
    const n = Number(val.replace(/\D/g, ""));
    act(api.updateDeath(eventId, death.id, { silver_value: n }));
    setEditing(false);
  }

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8, padding: "7px 10px",
      background: "var(--surface-2)", borderRadius: "var(--radius-sm)", marginBottom: 6,
    }}>
      <span style={{ flex: 1, fontSize: 13 }}>{death.display_name}</span>
      {editing ? (
        <>
          <input className="input" style={{ width: 130 }} value={val}
            onChange={(e) => setVal(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && saveVal()} />
          <button className="btn primary" style={{ fontSize: 12, padding: "4px 8px" }} onClick={saveVal}>OK</button>
        </>
      ) : (
        <span style={{ color: "var(--muted)", fontSize: 13, cursor: "pointer" }}
          onClick={() => setEditing(true)} title={t("clickToEditTitle")}>
          {fmt(death.silver_value)} {t("silverWord")}
        </span>
      )}
      <button
        className={"btn" + (death.approved ? " primary" : "")}
        style={{ fontSize: 11, padding: "3px 8px" }}
        onClick={() => act(api.updateDeath(eventId, death.id, { approved: !death.approved }))}>
        {death.approved ? <><i className="ti ti-check" aria-hidden /> {t("approvedBtn")}</> : t("approveBtn")}
      </button>
      <button style={{ background: "none", border: "none", color: "var(--hint)", cursor: "pointer", padding: "2px 4px" }}
        onClick={() => act(api.removeDeath(eventId, death.id))}>
        <i className="ti ti-x" aria-hidden />
      </button>
    </div>
  );
}

// ── Tabela de payout ──────────────────────────────────────────────────────

function PayoutTable({ payout, type }: { payout: PayoutPreview; type: string | null }) {
  const t = useT();
  const showLS = type === "lootsplit" || type === "lootsplit_regear";
  const showRG = type === "regear" || type === "lootsplit_regear";
  const showTotal = type === "lootsplit_regear";

  return (
    <div style={{ marginBottom: 12 }}>
      {showLS && (
        <p style={{ fontSize: 13, marginBottom: 8 }}>
          <span className="hint">{t("tabValueLabel")} </span>
          <strong>{fmt(payout.tab_value)} {t("silverWord")}</strong>
        </p>
      )}
      {payout.payouts.length === 0 ? (
        <p className="hint">{t("addParticipantsForPayoutSimple")}</p>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              <th style={{ textAlign: "left", padding: "4px 8px", color: "var(--hint)", fontWeight: 600 }}>{t("playerWord")}</th>
              {showLS && <>
                <th style={{ textAlign: "right", padding: "4px 8px", color: "var(--hint)", fontWeight: 600 }}>%</th>
                <th style={{ textAlign: "right", padding: "4px 8px", color: "var(--hint)", fontWeight: 600 }}>Lootsplit</th>
              </>}
              {showRG && <th style={{ textAlign: "right", padding: "4px 8px", color: "#e07a7a", fontWeight: 600 }}>Regear</th>}
              {showTotal && <th style={{ textAlign: "right", padding: "4px 8px", color: "var(--gold)", fontWeight: 600 }}>{t("totalWord")}</th>}
            </tr>
          </thead>
          <tbody>
            {payout.payouts.map((row, i) => (
              <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={{ padding: "5px 8px" }}>{row.display_name}</td>
                {showLS && <>
                  <td style={{ textAlign: "right", padding: "5px 8px", color: "var(--muted)" }}>{row.percent}%</td>
                  <td style={{ textAlign: "right", padding: "5px 8px" }}>{fmt(row.lootsplit)}</td>
                </>}
                {showRG && (
                  <td style={{ textAlign: "right", padding: "5px 8px", color: row.regear > 0 ? "#e07a7a" : "var(--hint)" }}>
                    {row.regear > 0 ? fmt(row.regear) : "—"}
                  </td>
                )}
                {showTotal && (
                  <td style={{ textAlign: "right", padding: "5px 8px", fontWeight: 600, color: "var(--gold)" }}>
                    {fmt(row.total)}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
          {(payout.total_lootsplit > 0 || payout.total_regear > 0) && (
            <tfoot>
              <tr style={{ borderTop: "1px solid var(--border-strong)" }}>
                <td style={{ padding: "5px 8px", color: "var(--hint)", fontSize: 11 }}>{t("totalWord")}</td>
                {showLS && <>
                  <td />
                  <td style={{ textAlign: "right", padding: "5px 8px", fontWeight: 600 }}>{fmt(payout.total_lootsplit)}</td>
                </>}
                {showRG && (
                  <td style={{ textAlign: "right", padding: "5px 8px", fontWeight: 600 }}>{fmt(payout.total_regear)}</td>
                )}
                {showTotal && (
                  <td style={{ textAlign: "right", padding: "5px 8px", fontWeight: 600, color: "var(--gold)" }}>
                    {fmt(payout.total_lootsplit + payout.total_regear)}
                  </td>
                )}
              </tr>
            </tfoot>
          )}
        </table>
      )}
    </div>
  );
}

// ── Participantes (colapsável) ────────────────────────────────────────────

function ParticipantsSection({ detail, act, canManage, pName, setPName, pPct, setPPct, onAdd }: {
  detail: EventDetail; act: (p: Promise<EventDetail>) => void; canManage: boolean;
  pName: string; setPName: (v: string) => void;
  pPct: string; setPPct: (v: string) => void;
  onAdd: () => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [roles, setRoles] = useState<CatalogRole[]>([]);
  const [regearPanel, setRegearPanel] = useState<{ pid: number; est: RegearEstimate | null; loading: boolean } | null>(null);
  const count = detail.participants.length;
  const isActive = !["finalized", "cancelled", "deleted"].includes(detail.state);

  useEffect(() => {
    if (open) api.listRoles().then(setRoles).catch(() => {});
  }, [open]);

  async function showRegear(p: Participant) {
    setRegearPanel({ pid: p.id, est: null, loading: true });
    try {
      const est = await api.getRegearEstimate(detail.id, p.id);
      setRegearPanel({ pid: p.id, est, loading: false });
    } catch {
      setRegearPanel({ pid: p.id, est: null, loading: false });
    }
  }

  function applyRegearAsDeath(est: RegearEstimate) {
    const p = detail.participants.find(x => x.id === est.participant_id);
    if (!p || !est.total) return;
    act(api.addDeath(detail.id, {
      display_name: p.user_name || `#${p.user_id}`,
      silver_value: est.total,
      notes: `${t("autoRegearNote")} (${est.game_role_name ?? t("noRoleFallback")}) — ${est.price_basis}`,
    }));
    setRegearPanel(null);
  }

  return (
    <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12, marginTop: 4, marginBottom: 12 }}>
      <button
        style={{ background: "none", border: "none", cursor: "pointer", color: "var(--muted)", fontSize: 13, padding: 0, display: "flex", alignItems: "center", gap: 6 }}
        onClick={() => setOpen(o => !o)}
      >
        <i className={"ti " + (open ? "ti-chevron-down" : "ti-chevron-right")} aria-hidden />
        <i className="ti ti-users" aria-hidden />
        {t("participantsLabel")}
        {count > 0 && <span className="badge info" style={{ marginLeft: 4 }}>{count}</span>}
      </button>

      {open && (
        <div style={{ marginTop: 10 }}>
          {count === 0 && (
            <p className="hint" style={{ marginBottom: 8 }}>
              {t("participantsAutoAddHint")}
            </p>
          )}
          {detail.participants.map((p) => (
            <div key={p.id}>
              <div style={{
                display: "flex", alignItems: "center", gap: 8, padding: "5px 8px",
                background: regearPanel?.pid === p.id ? "var(--surface)" : "var(--surface-2)",
                borderRadius: "var(--radius-sm)", marginBottom: 4, fontSize: 13,
                border: regearPanel?.pid === p.id ? "1px solid var(--info)44" : "1px solid transparent",
              }}>
                <span style={{ flex: 1, fontWeight: 500 }}>{p.user_name || `#${p.user_id}`}</span>
                {/* Seletor de função */}
                {isActive && canManage && (
                  <select
                    className="cs-select"
                    style={{ fontSize: 11, padding: "2px 4px", maxWidth: 130 }}
                    value={p.game_role_id ?? ""}
                    onChange={e => act(api.updateParticipant(detail.id, p.id, {
                      game_role_id: e.target.value ? Number(e.target.value) : null,
                    }))}
                  >
                    <option value="">{t("roleSelectPlaceholder")}</option>
                    {roles.map(r => (
                      <option key={r.id} value={r.id}>{r.name}</option>
                    ))}
                  </select>
                )}
                {!isActive && p.game_role_name && (
                  <span style={{ fontSize: 11, color: "var(--info)" }}>{p.game_role_name}</span>
                )}
                {p.is_trial && <span className="hint" style={{ fontSize: 11 }}>{t("trialBadge")}</span>}
                <span className="hint">{p.percent}%</span>
                {p.silver_received > 0 && (
                  <span style={{ color: "#6bbf73", fontSize: 12 }}>{fmt(p.silver_received)} {t("silverWord")}</span>
                )}
                {/* Botão de regear por função */}
                {(isActive || detail.state === "definition") && p.game_role_id && (
                  <button
                    style={{ background: "none", border: "none", color: "var(--info)", cursor: "pointer", padding: "2px 4px", fontSize: 12 }}
                    title={t("calcRegearTitle")}
                    onClick={() => regearPanel?.pid === p.id ? setRegearPanel(null) : showRegear(p)}
                  >
                    <i className="ti ti-calculator" aria-hidden />
                  </button>
                )}
                {isActive && canManage && (
                  <button style={{ background: "none", border: "none", color: "var(--hint)", cursor: "pointer", padding: "2px 4px" }}
                    onClick={() => act(api.removeParticipant(detail.id, p.id))}>
                    <i className="ti ti-x" aria-hidden />
                  </button>
                )}
              </div>
              {/* Painel de estimativa de regear */}
              {regearPanel?.pid === p.id && (
                <RegearEstimatePanel
                  estimate={regearPanel.est}
                  loading={regearPanel.loading}
                  onApply={applyRegearAsDeath}
                  onClose={() => setRegearPanel(null)}
                />
              )}
            </div>
          ))}
          {isActive && canManage && (
            <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
              <input className="input" style={{ flex: 1, minWidth: 120 }} placeholder={t("nameWord")}
                value={pName} onChange={(e) => setPName(e.target.value)} />
              <input className="input" style={{ width: 56 }} placeholder="%"
                value={pPct} onChange={(e) => setPPct(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && onAdd()} />
              <button className="btn" onClick={onAdd} disabled={!pName.trim()}>
                <i className="ti ti-plus" aria-hidden /> {t("addBtn")}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Painel de estimativa de regear ────────────────────────────────────────────

function RegearEstimatePanel({
  estimate, loading, onApply, onClose,
}: {
  estimate: RegearEstimate | null;
  loading: boolean;
  onApply: (est: RegearEstimate) => void;
  onClose: () => void;
}) {
  const t = useT();
  return (
    <div style={{
      background: "var(--surface)", border: "1px solid var(--info)44",
      borderRadius: "var(--radius-sm)", padding: "10px 14px", marginBottom: 6,
      fontSize: 12,
    }}>
      {loading && <p className="hint" style={{ margin: 0 }}>{t("fetchingPricesLabel")}</p>}
      {!loading && !estimate && <p style={{ color: "#e07a7a", margin: 0 }}>{t("estimateFetchError")}</p>}
      {!loading && estimate && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <span style={{ fontWeight: 600 }}>
              {t("regearRoleLabel")} {estimate.game_role_name ?? t("noRoleFallback")} — {estimate.price_basis}
            </span>
            <span style={{ marginLeft: "auto", fontWeight: 700, color: "#6bbf73" }}>
              {fmt(estimate.total)} {t("silverWord")}
            </span>
          </div>
          {estimate.items.length === 0 && (
            <p className="hint" style={{ margin: 0 }}>
              {t("noRegearItemsConfigured")}
            </p>
          )}
          {estimate.items.length > 0 && (
            <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: 8 }}>
              <thead>
                <tr style={{ color: "var(--muted)", fontSize: 11 }}>
                  <th style={{ textAlign: "left", paddingBottom: 4 }}>{t("colSlot")}</th>
                  <th style={{ textAlign: "left" }}>Item</th>
                  <th style={{ textAlign: "right" }}>{t("qtyAbbrev")}</th>
                  <th style={{ textAlign: "right" }}>{t("colUnitPrice")}</th>
                  <th style={{ textAlign: "right" }}>{t("totalWord")}</th>
                </tr>
              </thead>
              <tbody>
                {estimate.items.map((item: RegearItemEstimate) => (
                  <tr key={item.slot} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ padding: "3px 0", color: "var(--muted)", textTransform: "capitalize" }}>{item.slot}</td>
                    <td style={{ padding: "3px 8px" }}>
                      <span style={{ fontFamily: "monospace", fontSize: 10, color: "var(--hint)" }}>{item.item_id}</span>
                      {item.name && <span style={{ marginLeft: 4 }}>{item.name}</span>}
                    </td>
                    <td style={{ textAlign: "right" }}>{item.quantity}</td>
                    <td style={{ textAlign: "right" }}>
                      {item.unit_price ? fmt(item.unit_price) : <span className="hint">{t("noPriceFallback")}</span>}
                    </td>
                    <td style={{ textAlign: "right", fontWeight: item.total_price ? 600 : 400 }}>
                      {item.total_price ? fmt(item.total_price) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div style={{ display: "flex", gap: 8 }}>
            {estimate.total > 0 && (
              <button className="btn primary" style={{ fontSize: 11, padding: "4px 10px" }}
                onClick={() => onApply(estimate)}>
                <i className="ti ti-plus" aria-hidden /> {t("createDeathRecordBtn")} ({fmt(estimate.total)})
              </button>
            )}
            <button className="btn" style={{ fontSize: 11, padding: "4px 10px" }} onClick={onClose}>
              {t("closeBtn")}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
