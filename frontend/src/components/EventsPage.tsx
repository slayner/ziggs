import { useEffect, useMemo, useRef, useState } from "react";
import { api, g, type CatalogRole, type EventDetail, type EventSummary, type NodeEventLog, type Participant, type Permissions, type RegearEstimate, type RegearItemEstimate, type RegearRequest, type VerificationStep } from "../api";
import { useT, type TKey } from "../i18n";
import { navigate } from "../router";
import { RoleIcon } from "./RoleIcons";

// Fluxo: rascunho → agendado → andamento → revisão → concluido.
const PIPELINE = ["draft", "scheduled", "in_progress", "review", "finalized"];
// Eventos "vivos" ficam expandidos por padrão na lista; finalizado/cancelado/
// excluído entram colapsados (só a linha resumo) — não pedem mais atenção.
const ACTIVE_STATES = new Set(["draft", "scheduled", "in_progress", "review"]);
const STATE_KEYS: Record<string, TKey> = {
  draft: "stateDraft", scheduled: "stateScheduled", in_progress: "stateInProgress", review: "stateReview",
  finalized: "stateFinalized", cancelled: "stateCancelled", deleted: "stateDeleted",
};
// review usa só 2 marcadores opcionais (tab_value + nodes).
const STEP_KEYS: Record<string, TKey> = {
  tab_value: "stepTabValue", nodes: "stepNodes",
};

function fmt(n: number): string {
  return n.toLocaleString("pt-BR");
}

// Indicador de origem além da escalação — sempre visível ao lado do nome (não
// some na bracket mínima). Um ícone+cor por caso, com tooltip explicativo.
const ORIGIN_META: Record<string, { icon: string; color: string; key: TKey }> = {
  battle_no_call: { icon: "ti-swords", color: "#e06565", key: "originBattleNoCall" },
  signup_no_call: { icon: "ti-notebook-off", color: "var(--gold)", key: "originSignupNoCall" },
  call_outsider: { icon: "ti-eye", color: "var(--hint)", key: "originCallOutsider" },
  call_no_signup: { icon: "ti-phone", color: "var(--info)", key: "originCallNoSignup" },
  call_signup: { icon: "ti-notebook", color: "#6bbf73", key: "originCallSignup" },
  manual: { icon: "ti-user-plus", color: "var(--gold)", key: "originManual" },
};
function OriginBadge({ origin }: { origin: string }) {
  const t = useT();
  const meta = ORIGIN_META[origin];
  if (!meta) return null;
  return (
    <i className={"ti " + meta.icon} aria-hidden
      title={t(meta.key)}
      style={{ color: meta.color, fontSize: 11, flexShrink: 0, marginLeft: 4 }} />
  );
}

export default function EventsPage({ perms, active = true }: { perms: Permissions; active?: boolean }) {
  const t = useT();
  const [events, setEvents] = useState<EventSummary[]>([]);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  // undefined = ainda não buscado, null = falhou, EventDetail = carregado.
  const [details, setDetails] = useState<Record<number, EventDetail | null | undefined>>({});
  const [error, setError] = useState<string | null>(null);
  // IDs já vistos ao menos uma vez — evita reabrir à força um card que o
  // usuário fechou manualmente num refresh seguinte (só o 1º avistamento
  // decide o estado inicial de expandido/colapsado).
  const seenIds = useRef<Set<number>>(new Set());
  const fetchStarted = useRef<Set<number>>(new Set());
  // Refs espelhando estado pro poll ler valor fresco dentro do interval
  // (sem precisar reiniciar o timer a cada toggle de expansão).
  const expandedRef = useRef(expanded); expandedRef.current = expanded;
  const detailsRef = useRef(details); detailsRef.current = details;

  function refreshList() {
    api.listEvents().then(setEvents).catch((e) => setError(String(e.message)));
  }
  useEffect(refreshList, []);

  // ponytail: poll live — a cada 10s refaz a lista (novos eventos / transições
  // de estado aparecem sozinhos) e os detalhes dos eventos abertos que ainda
  // estão ativos (inscritos/participantes chegando ao vivo). Terminais
  // (finalizado/cancelado/excluído) não são re-buscados: não mudam mais. `active`
  // pausa o poll quando a aba tá escondida (keep-alive no ManagementPage).
  useEffect(() => {
    if (!active) return;
    const iv = setInterval(() => {
      api.listEvents().then(setEvents).catch(() => {});
      for (const id of expandedRef.current) {
        const d = detailsRef.current[id];
        if (d && !ACTIVE_STATES.has(d.state)) continue;
        api.getEvent(id)
          .then((dd) => setDetails((prev) => ({ ...prev, [id]: dd })))
          .catch(() => {});
      }
    }, 10000);
    return () => clearInterval(iv);
  }, [active]);

  function ensureDetail(id: number) {
    if (fetchStarted.current.has(id)) return;
    fetchStarted.current.add(id);
    api.getEvent(id)
      .then((d) => setDetails((prev) => ({ ...prev, [id]: d })))
      .catch(() => setDetails((prev) => ({ ...prev, [id]: null })));
  }

  // Ao chegar uma lista nova, expande (e carrega) todo evento "vivo" ainda
  // não visto — finalizado/cancelado/excluído entra colapsado, sem fetch.
  useEffect(() => {
    const newlyActive: number[] = [];
    for (const e of events) {
      if (seenIds.current.has(e.id)) continue;
      seenIds.current.add(e.id);
      if (ACTIVE_STATES.has(e.state)) newlyActive.push(e.id);
    }
    if (newlyActive.length) {
      setExpanded((prev) => {
        const next = new Set(prev);
        newlyActive.forEach((id) => next.add(id));
        return next;
      });
      newlyActive.forEach(ensureDetail);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events]);

  function toggle(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else { next.add(id); ensureDetail(id); }
      return next;
    });
  }

  function actFor(eventId: number, p: Promise<EventDetail>) {
    setError(null);
    p.then((d) => {
      setDetails((prev) => ({ ...prev, [eventId]: d }));
      refreshList();
    }).catch((e) => setError(String(e.message)));
  }

  return (
    <div className="container">
      {perms["events.create"] && (
        <div className="card" style={{ marginBottom: 10 }}>
          <CreateEventForm onCreated={(ev) => {
            refreshList();
            seenIds.current.add(ev.id);
            fetchStarted.current.add(ev.id);
            setExpanded((prev) => new Set(prev).add(ev.id));
            setDetails((prev) => ({ ...prev, [ev.id]: ev }));
          }} />
        </div>
      )}

      {error && <p style={{ color: "#e07a7a", fontSize: 13, marginBottom: 10 }}>{error}</p>}
      {events.length === 0 && <div className="card"><p className="muted">{t("noEventsYet")}</p></div>}

      {events.map((e) => {
        const isExpanded = expanded.has(e.id);
        const det = details[e.id];
        return (
          <div key={e.id} className="card" style={{ marginBottom: 10, padding: 0, overflow: "hidden" }}>
            <button
              className="event-row"
              style={{ border: "none", marginBottom: 0, borderRadius: 0 }}
              onClick={() => toggle(e.id)}
            >
              <i className="ti ti-calendar-event" style={{ color: "var(--muted)" }} aria-hidden />
              <span style={{ flex: 1 }}>{e.title || `Event #${e.id}`}</span>
              {e.seriousness === "serious" && (
                <i className="ti ti-alert-triangle" style={{ color: "var(--gold)" }} title={t("evSeriousnessSerious")} aria-hidden />
              )}
              <StatePill state={e.state} />
              <i className={"ti " + (isExpanded ? "ti-chevron-up" : "ti-chevron-down")} style={{ color: "var(--hint)" }} aria-hidden />
            </button>
            {isExpanded && (
              <div style={{ padding: "0 14px 14px", borderTop: "1px solid var(--border)" }}>
                {det === undefined && <p className="muted" style={{ marginTop: 12 }}>{t("loading")}</p>}
                {det === null && <p style={{ color: "#e07a7a", marginTop: 12 }}>{t("evLoadError")}</p>}
                {det && (
                  <EventDetailCard detail={det} act={(p) => actFor(e.id, p)} canManage={perms["events.manage"]} />
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Formulário de criação ────────────────────────────────────────────────

const EVENT_TZ_MINUTES: Record<string, number> = {
  UTC: 0, GMT: 0, BRT: -180, BRST: -120, CET: 60, CEST: 120,
};
const two = (n: number) => String(n).padStart(2, "0");
const wallLabel = (y: number, m: number, d: number, h: number, min: number) =>
  `${two(d)}/${two(m)}/${y} ${two(h)}:${two(min)}`;

type ParsedEventTime = { iso: string; source: string; utc: string; localZone: boolean };

function parseEventTime(raw: string, now = new Date()): ParsedEventTime | null {
  const m = raw.trim().match(
    /^(?:(?:(\d{4})-(\d{1,2})-(\d{1,2})|(\d{1,2})[/.](\d{1,2})(?:[/.](\d{4}))?)\s+)?(\d{1,2})(?:h(\d{1,2})?|:(\d{1,2}))?\s*(BRT|BRST|UTC|GMT|CET|CEST)?$/i,
  );
  if (!m) return null;

  const hour = Number(m[7]);
  const minute = Number(m[8] ?? m[9] ?? 0);
  if (hour > 23 || minute > 59) return null;

  const zone = m[10]?.toUpperCase();
  const offset = zone ? EVENT_TZ_MINUTES[zone] : null;
  const shiftedNow = offset == null ? now : new Date(now.getTime() + offset * 60_000);
  let year = Number(m[1] ?? m[6] ?? (offset == null ? shiftedNow.getFullYear() : shiftedNow.getUTCFullYear()));
  const month = Number(m[2] ?? m[5] ?? ((offset == null ? shiftedNow.getMonth() : shiftedNow.getUTCMonth()) + 1));
  const day = Number(m[3] ?? m[4] ?? (offset == null ? shiftedNow.getDate() : shiftedNow.getUTCDate()));
  const hasDate = Boolean(m[1] || m[4]);
  const hasYear = Boolean(m[1] || m[6]);

  const make = (y: number) => offset == null
    ? new Date(y, month - 1, day, hour, minute)
    : new Date(Date.UTC(y, month - 1, day, hour, minute) - offset * 60_000);
  let parsed = make(year);
  const validWall = offset == null
    ? parsed.getFullYear() === year && parsed.getMonth() === month - 1 && parsed.getDate() === day
    : new Date(parsed.getTime() + offset * 60_000).getUTCFullYear() === year
      && new Date(parsed.getTime() + offset * 60_000).getUTCMonth() === month - 1
      && new Date(parsed.getTime() + offset * 60_000).getUTCDate() === day;
  if (!validWall) return null;

  if (parsed <= now) {
    if (hasYear) return null;
    if (hasDate) {
      year += 1;
      parsed = make(year);
    } else {
      parsed = offset == null
        ? new Date(year, month - 1, day + 1, hour, minute)
        : new Date(Date.UTC(year, month - 1, day + 1, hour, minute) - offset * 60_000);
      const nextWall = offset == null ? parsed : new Date(parsed.getTime() + offset * 60_000);
      year = offset == null ? nextWall.getFullYear() : nextWall.getUTCFullYear();
    }
  }

  const utc = parsed.toISOString();
  const finalWall = offset == null ? parsed : new Date(parsed.getTime() + offset * 60_000);
  const sourceYear = hasDate ? year : (offset == null ? finalWall.getFullYear() : finalWall.getUTCFullYear());
  const sourceMonth = hasDate ? month : ((offset == null ? finalWall.getMonth() : finalWall.getUTCMonth()) + 1);
  const sourceDay = hasDate ? day : (offset == null ? finalWall.getDate() : finalWall.getUTCDate());
  return {
    iso: utc,
    source: `${wallLabel(sourceYear, sourceMonth, sourceDay, hour, minute)} ${zone ?? ""}`.trim(),
    utc: `${two(parsed.getUTCDate())}/${two(parsed.getUTCMonth() + 1)}/${parsed.getUTCFullYear()} ${two(parsed.getUTCHours())}:${two(parsed.getUTCMinutes())}`,
    localZone: !zone,
  };
}

if (import.meta.env.DEV) {
  const checkNow = new Date("2026-07-24T12:00:00Z");
  console.assert(parseEventTime("21h", checkNow) !== null);
  console.assert(parseEventTime("21:30 BRT", checkNow)?.iso === "2026-07-25T00:30:00.000Z");
  console.assert(parseEventTime("24/07/2026 21h BRT", checkNow)?.iso === "2026-07-25T00:00:00.000Z");
  console.assert(parseEventTime("31/02/2026 21h BRT", checkNow) === null);
}

function CreateEventForm({ onCreated }: { onCreated: (ev: EventDetail) => void }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [whenInput, setWhenInput] = useState("");
  const [comps, setComps] = useState<{ id: number; name: string }[]>([]);
  const [compId, setCompId] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) api.listComps().then(setComps).catch(() => {});
  }, [open]);
  const when = parseEventTime(whenInput);

  async function submit() {
    if (!when) { setError(t("evTimeRequired")); return; }
    setBusy(true); setError(null);
    try {
      const ev = await api.createEvent({
        title: title.trim() || null,
        scheduled_at: when.iso,
        comp_id: compId ? Number(compId) : null,
        message: msg.trim() || null,
      });
      setTitle(""); setWhenInput(""); setCompId(""); setMsg("");
      setOpen(false);
      onCreated(ev);
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button className="btn primary" style={{ width: "100%", marginBottom: 14 }} onClick={() => setOpen(true)}>
        <i className="ti ti-plus" aria-hidden /> {t("evNewEventBtn")}
      </button>
    );
  }

  const field = { marginBottom: 10 } as const;
  const label = { display: "block", marginBottom: 4 } as const;

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: 12, marginBottom: 14 }}>
      <div style={field}>
        <input
          className="input" style={{ width: "100%" }} placeholder={t("evCtaNamePlaceholder")}
          value={title} onChange={(e) => setTitle(e.target.value)} autoFocus
        />
      </div>
      <div style={field}>
        <span className="hint" style={label}>{t("evScheduledAtLabel")}</span>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 10, alignItems: "center" }}>
          <input
            className="input" style={{ width: "100%" }}
            value={whenInput} onChange={(e) => setWhenInput(e.target.value)}
            placeholder={t("evTimeInputPlaceholder")}
            aria-describedby="event-time-preview"
          />
          <div
            id="event-time-preview" aria-live="polite"
            style={{ fontSize: 12, color: when ? "var(--green)" : whenInput ? "#e07a7a" : "var(--hint)" }}
          >
            {!whenInput
              ? t("evTimeInputHint")
              : when
                ? `${when.utc} UTC`
                : t("evTimeInvalid")}
          </div>
        </div>
      </div>
      <div style={field}>
        <span className="hint" style={label}>{t("evCompLabel")}</span>
        <select className="cs-select" style={{ width: "100%" }} value={compId} onChange={(e) => setCompId(e.target.value)}>
          <option value="">{t("evNoComp")}</option>
          {comps.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>
      <div style={field}>
        <span className="hint" style={label}>{t("evMessageLabel")}</span>
        <textarea
          className="input" style={{ width: "100%", minHeight: 52, resize: "vertical" }}
          placeholder={t("evMessagePlaceholder")}
          value={msg} onChange={(e) => setMsg(e.target.value)}
        />
      </div>
      {error && <p style={{ color: "#e07a7a", fontSize: 13, marginBottom: 8 }}>{error}</p>}
      <div style={{ display: "flex", gap: 8 }}>
        <button
          className="btn primary" style={{ flex: 1 }} onClick={submit}
          disabled={busy}
        >
          <i className="ti ti-plus" aria-hidden /> {t("createBtn")}
        </button>
        <button className="btn" onClick={() => setOpen(false)} disabled={busy}>{t("closeBtn")}</button>
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

function utcEventInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${two(d.getUTCDate())}/${two(d.getUTCMonth() + 1)}/${d.getUTCFullYear()} ${two(d.getUTCHours())}:${two(d.getUTCMinutes())} UTC`;
}

function EventEditControls({ detail, act }: {
  detail: EventDetail;
  act: (p: Promise<EventDetail>) => void;
}) {
  const t = useT();
  const [comps, setComps] = useState<{ id: number; name: string }[]>([]);
  const [whenInput, setWhenInput] = useState(() => utcEventInput(detail.scheduled_at));
  const when = parseEventTime(whenInput);
  const currentIso = detail.scheduled_at ? new Date(detail.scheduled_at).toISOString() : null;

  useEffect(() => {
    api.listComps().then(setComps).catch(() => {});
  }, [detail.id]);
  useEffect(() => {
    setWhenInput(utcEventInput(detail.scheduled_at));
  }, [detail.id, detail.scheduled_at]);

  return (
    <div className="card" style={{ padding: 10, marginBottom: 14 }}>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(240px, 1.4fr) minmax(180px, 1fr)", gap: 10 }}>
        <div>
          <span className="hint" style={{ display: "block", marginBottom: 4 }}>{t("evScheduledAtLabel")}</span>
          <div style={{ display: "flex", gap: 6 }}>
            <input
              className="input"
              style={{ width: "100%" }}
              value={whenInput}
              onChange={(e) => setWhenInput(e.target.value)}
              placeholder={t("evTimeInputPlaceholder")}
            />
            <button
              className="btn"
              disabled={!when || when.iso === currentIso}
              onClick={() => when && act(api.updateEvent(detail.id, { scheduled_at: when.iso }))}
            >
              {t("save")}
            </button>
          </div>
          <div
            aria-live="polite"
            style={{ marginTop: 4, fontSize: 12, color: when ? "var(--green)" : whenInput ? "#e07a7a" : "var(--hint)" }}
          >
            {!whenInput ? t("evTimeInputHint") : when ? `${when.utc} UTC` : t("evTimeInvalid")}
          </div>
        </div>
        <div>
          <span className="hint" style={{ display: "block", marginBottom: 4 }}>{t("evCompLabel")}</span>
          <select
            className="cs-select"
            style={{ width: "100%" }}
            value={detail.comp_id ?? ""}
            onChange={(e) => {
              const compId = e.target.value ? Number(e.target.value) : null;
              if (compId === detail.comp_id || !window.confirm(t("evCompChangeConfirm"))) return;
              act(api.updateEvent(detail.id, { comp_id: compId }));
            }}
          >
            <option value="">{t("evNoComp")}</option>
            {comps.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
      </div>
    </div>
  );
}

function EventDetailCard({ detail, act, canManage }: { detail: EventDetail; act: (p: Promise<EventDetail>) => void; canManage: boolean }) {
  const t = useT();
  const curIdx = PIPELINE.indexOf(detail.state);

  // X = excluir. Cancelado/excluído são a mesma coisa na prática pro usuário
  // (dois jeitos de matar o evento) — a UI oferece só o excluir, que o backend
  // aceita de QUALQUER estado (de finalizado, estorna pagamentos — daí o confirm).
  const canDelete = canManage && detail.allowed_transitions.includes("deleted");
  // Um único botão primário por estado: a próxima etapa do pipeline.
  const forward = curIdx >= 0 ? PIPELINE[curIdx + 1] : undefined;
  const canForward = !!(canManage && forward && detail.allowed_transitions.includes(forward));
  const forwardLabel = detail.state === "draft" ? t("publishEventBtn")
    : forward === "review" ? t("endEventBtn")
    : forward === "finalized" ? t("finalizeBtn")
    : t("startEventBtn");

  return (
    <>
      {/* Título/estado/tipo já aparecem na linha de cabeçalho da lista (fora
          deste componente) — aqui só o que é específico do detalhe.
          Pipeline à esquerda, X de excluir no canto superior direito. */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 10, marginTop: 12 }}>
        <div className="pipe" style={{ flex: 1, margin: "0 0 16px" }}>
          {PIPELINE.map((s, i) => (
            <span key={s} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <span className={"state-pill " + (i === curIdx ? "cur" : curIdx >= 0 && i < curIdx ? "done" : "")}>
                {t(STATE_KEYS[s])}
              </span>
              {i < PIPELINE.length - 1 && <i className="ti ti-chevron-right arrow" aria-hidden />}
            </span>
          ))}
        </div>
        {canDelete && (
          <button
            title={t("deleteEventTitle")}
            style={{ background: "none", border: "none", color: "var(--hint)", cursor: "pointer", padding: "2px 4px", flexShrink: 0 }}
            onClick={() => { if (confirm(t("deleteEventConfirm"))) act(api.transition(detail.id, "deleted")); }}
          >
            <i className="ti ti-x" style={{ fontSize: 16 }} aria-hidden />
          </button>
        )}
      </div>

      {canManage && ["scheduled", "in_progress"].includes(detail.state) && (
        <EventEditControls detail={detail} act={act} />
      )}

      {/* Attendance: movido para a barra de ações, ao lado do botão de
          finalizar (todo mundo que participou recebe a mesma quantidade). */}

      {/* Revisão: valor da tab + nodes lado a lado, payout, concluir. */}
      {detail.state === "review" && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 12 }}>
            <div className="card" style={{ padding: 10 }}>
              {/* Valor da tab (marcador opcional). */}
              {detail.verification.map((v) =>
                v.step === "tab_value" ? (
                  <TabValueStep key={v.step} v={v} eventId={detail.id} act={canManage ? act : () => {}} />
                ) : null
              )}
            </div>
            <div className="card" style={{ padding: 10 }}>
              {/* Nodes próximos do timer: capturamos? quanto vendemos? (% do
                  scout sai do valor vendido aqui — ver NodeDef.weight). */}
              <NodeClaimSection detail={detail} act={act} canManage={canManage} />
            </div>
          </div>
          <EventRegearsRow detail={detail} act={act} canManage={canManage} />
          {/* Concluir vive na barra de ações no rodapé — um botão só. */}
        </div>
      )}

      {/* Finalizado: resumo */}
      {detail.state === "finalized" && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontWeight: 600, marginBottom: 10, display: "flex", alignItems: "center", gap: 8 }}>
            <i className="ti ti-circle-check" aria-hidden style={{ color: "#6bbf73" }} />
            {t("eventFinalized")}
          </div>
          <EventRegearsRow detail={detail} act={act} canManage={canManage} />
        </div>
      )}

      <ParticipantsSection detail={detail} act={act} canManage={canManage} />

      {/* Barra de ações: roster + liberar funções + UM botão primário (a
          próxima etapa do pipeline). Cancelar/excluir viraram o X lá em cima. */}
      {(detail.comp_id || canManage) && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", borderTop: "1px solid var(--border)", paddingTop: 12, marginTop: 4 }}>
          {detail.comp_id && (
            <button className="btn" style={{ fontSize: 12 }}
              title={t("rosterSignupCountTitle")}
              onClick={() => navigate(`/events/${g()}/${detail.id}/escalation`)}>
              <i className="ti ti-users" aria-hidden /> {t("escBtn")}
              {/* Inscritos (Discord), não escalados — o roster pode ter mais
                  gente esperando vaga do que slots preenchidos. */}
              {detail.signups.length > 0 && <span className="badge info" style={{ marginLeft: 4 }}>{detail.signups.length}</span>}
            </button>
          )}
          {canManage && ["scheduled", "in_progress"].includes(detail.state) && (
            <button
              className={"btn" + (detail.functions_released ? " primary" : "")}
              style={{ fontSize: 12 }}
              onClick={() => act(api.releaseFunctions(detail.id, !detail.functions_released))}
            >
              <i className={"ti " + (detail.functions_released ? "ti-lock-open" : "ti-lock")} aria-hidden />{" "}
              {detail.functions_released ? t("functionsReleasedOn") : t("releaseFunctionsBtn")}
            </button>
          )}
          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
            {/* Attendance: valor único do evento, sentado à esquerda do finalizar. */}
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12.5 }}>
              <i className="ti ti-info-circle" style={{ color: "var(--hint)", fontSize: 12 }} title={t("eventAttendanceHint")} aria-hidden />
              <span className="hint">{t("eventAttendanceLabel")}</span>
              {canManage ? (
                <input
                  type="text" inputMode="decimal"
                  defaultValue={String(detail.attendance).replace(".", ",")}
                  style={{ width: 48, fontSize: 12.5, padding: "2px 5px", background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text)", textAlign: "right" }}
                  onBlur={(e) => {
                    const v = parseFloat(e.target.value.replace(",", ".").trim());
                    if (Number.isFinite(v) && v !== detail.attendance) act(api.setEventAttendance(detail.id, v));
                  }}
                  onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                />
              ) : (
                <strong>{detail.attendance}</strong>
              )}
            </span>
            {canForward && (
              <button className="btn primary"
                onClick={() => act(api.transition(detail.id, forward!))}>
                <i className={"ti " + (forward === "finalized" ? "ti-circle-check" : "ti-player-play")} aria-hidden />{" "}
                {forwardLabel}
              </button>
            )}
          </div>
        </div>
      )}
    </>
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

// ── Captura de nodes em review (scout payout) ───────────────────────────────

function NodeClaimSection({ detail, act, canManage }: {
  detail: EventDetail; act: (p: Promise<EventDetail>) => void; canManage: boolean;
}) {
  const t = useT();
  const [nodes, setNodes] = useState<NodeEventLog[]>([]);
  // Valor digitado por node_log_id (string p/ o input; converte no claim).
  const [vals, setVals] = useState<Record<number, string>>({});

  // Janela ±30min do callout (ou started_at, ou agora). Rebusca quando o evento
  // muda (ex.: acabou de entrar em review). detail.id no deps p/ re-fetch ao trocar evento.
  const ts = detail.callout_at ?? detail.started_at ?? undefined;
  useEffect(() => {
    if (detail.state !== "review") return;
    api.nearNodes(ts).then(r => {
      setNodes(r.nodes);
      // Pré-preenche o input com o sold_value já gravado (se capturado neste evento).
      const prefilled: Record<number, string> = {};
      for (const n of r.nodes) {
        if (n.captured && n.event_id === detail.id && n.sold_value > 0) {
          prefilled[n.id] = String(n.sold_value);
        }
      }
      setVals(prefilled);
    }).catch(() => setNodes([]));
  }, [detail.id, detail.state, ts]);

  function claim(node: NodeEventLog, captured: boolean) {
    const raw = String(vals[node.id] ?? "").replace(/\D/g, "");
    const sold = Number(raw) || 0;
    act(api.claimNode(detail.id, node.id, { captured, sold_value: sold }).then(d => {
      // Refetch p/ refletir o estado capturado (event_id vinculado ao evento).
      api.nearNodes(ts).then(r => setNodes(r.nodes)).catch(() => {});
      return d;
    }));
  }

  return (
    <div>
      <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8, display: "flex", alignItems: "center", gap: 6 }}>
        <i className="ti ti-plant" aria-hidden style={{ color: "var(--green)" }} />
        {t("nodeClaimTitle")}
      </div>
      {nodes.length === 0 && <p className="hint" style={{ margin: 0 }}>{t("noNodesNearBy")}</p>}
      {nodes.map((n) => {
        // Capturado por ESTE evento (event_id casa). Capturado por outro evento
        // não conta — mostra como disponível.
        const mine = n.captured && n.event_id === detail.id;
        return (
          <div key={n.id} style={{
            display: "flex", alignItems: "center", gap: 8, padding: "6px 8px",
            background: "var(--surface-2)", borderRadius: "var(--radius-sm)", marginBottom: 4, fontSize: 12,
            flexWrap: "wrap",
          }}>
            <span style={{ flex: 1, minWidth: 140 }}>
              <strong>{n.node_type}</strong> · 🗺️ {n.map_name}
              {n.scout_name && <span className="hint"> · 🔎 {n.scout_name}</span>}
            </span>
            <label style={{ display: "flex", alignItems: "center", gap: 4, color: "var(--hint)", cursor: canManage ? "pointer" : "default" }}>
              <input type="checkbox" disabled={!canManage} checked={mine}
                onChange={(e) => canManage && claim(n, e.target.checked)} />
              {t("nodeCapturedLabel")}
            </label>
            {(canManage || mine) && (
              <input className="input" style={{ width: 130, fontSize: 12, padding: "3px 6px" }}
                placeholder={t("soldValueLabel")}
                value={vals[n.id] ?? ""}
                disabled={!canManage}
                onChange={(e) => setVals(v => ({ ...v, [n.id]: e.target.value }))}
                onKeyDown={(e) => e.key === "Enter" && canManage && claim(n, true)}
              />
            )}
            {mine && (
              <span style={{ color: "#6bbf73", fontSize: 12 }}>
                {fmt(n.sold_value)} {t("silverWord")}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Mortes / Regear ───────────────────────────────────────────────────────

function EventRegearsRow({ detail, act, canManage }: {
  detail: EventDetail; act: (p: Promise<EventDetail>) => void; canManage: boolean;
}) {
  const t = useT();
  const s = detail.regear_summary;
  // Lista dos pendentes carregada à parte do summary (que só tem contagens) —
  // só o necessário pra editar/aprovar/negar sem sair do card de review. A
  // tela cheia (screenshot, itens, notas) continua só no "Abrir no site".
  const [pending, setPending] = useState<RegearRequest[] | null>(null);
  const [edits, setEdits] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<Record<number, string>>({});

  useEffect(() => {
    if (!canManage || !s || s.pending === 0) { setPending(null); return; }
    api.eventRegears(detail.id)
      .then(r => setPending(r.requests.filter(x => x.status === "pending")))
      .catch(() => setPending(null));
  }, [detail.id, s?.pending, canManage]);

  if (!s) return null;
  const blocked = s.pending > 0;

  async function actOn(r: RegearRequest, status: "paid" | "denied") {
    setBusy(b => ({ ...b, [r.id]: status }));
    const payload: Record<string, unknown> = { status };
    const txt = (edits[r.id] ?? "").trim();
    if (txt !== "") {
      const n = Number(txt);
      if (Number.isFinite(n) && n >= 0) payload.final_total = Math.round(n);
    }
    try {
      await api.updateRegear(r.id, payload);
      const fresh = await api.eventRegears(detail.id);
      setPending(fresh.requests.filter(x => x.status === "pending"));
      act(api.getEvent(detail.id));
    } catch (e) {
      alert(String((e as Error)?.message ?? e));
    } finally {
      setBusy(b => { const n = { ...b }; delete n[r.id]; return n; });
    }
  }

  return (
    <div style={{ marginTop: 14 }}>
      <div style={{
        padding: "9px 12px", background: "var(--surface-2)",
        borderRadius: "var(--radius-sm)", display: "flex", alignItems: "center",
        gap: 10, flexWrap: "wrap",
      }}>
        <i className="ti ti-tools" aria-hidden style={{ color: "var(--gold)" }} />
        <strong style={{ fontSize: 13 }}>{t("evRegearsRowTitle")}</strong>
        <span className="hint" style={{ fontSize: 12 }}>
          {t("regCountPending")}: {s.pending} · {t("regCountApproved")}: {s.approved} ·{" "}
          {t("regCountDenied")}: {s.denied}
          {s.approved_total > 0 && <> · {fmt(s.approved_total)} {t("silverWord")}</>}
        </span>
        <button className="btn" style={{ fontSize: 11, padding: "3px 10px", marginLeft: "auto" }}
          onClick={() => navigate(`/regears?event=${detail.id}`)}>
          <i className="ti ti-external-link" aria-hidden /> {t("evRegearsOpen")}
        </button>
        {blocked && (
          <span style={{ width: "100%", fontSize: 12, color: "#e0a070" }}>
            <i className="ti ti-alert-triangle" aria-hidden /> {t("evRegearsFinalizeBlocked")}
          </span>
        )}
      </div>

      {canManage && pending && pending.length > 0 && (
        <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 6 }}>
          {pending.map(r => (
            <div key={r.id} style={{
              display: "flex", alignItems: "center", gap: 8, fontSize: 12,
              background: "var(--surface)", border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)", padding: "6px 10px",
            }}>
              <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {r.requester_name ?? "—"}
              </span>
              <input
                type="number" min={0} step={1}
                placeholder={String(r.suggested_total)}
                value={edits[r.id] ?? ""}
                onChange={e => setEdits(p => ({ ...p, [r.id]: e.target.value }))}
                style={{ width: 92 }}
              />
              <button className="btn btn-green" disabled={!!busy[r.id]} onClick={() => actOn(r, "paid")}>
                {busy[r.id] === "paid" ? "…" : t("regearApprove")}
              </button>
              <button className="btn" disabled={!!busy[r.id]} onClick={() => actOn(r, "denied")}>
                {busy[r.id] === "denied" ? "…" : t("regearDeny")}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Inscrições (auto-inscrição via Discord, só leitura) ──────────────────

// ── Participantes (lista única, ordenada por percent) ─────────────────────
// Inscritos sem presença e ausentes-em-batalha NÃO são mais seções à parte —
// contam como participante (percent 0) na MESMA lista, misturados por %.
// Editar o % de uma linha virtual materializa ela (api.addParticipant).

interface VirtualParticipantRow {
  virtual: true;
  rowKey: string;
  user_id: number;
  user_name: string | null;
  percent: number;
  origin: string;
  functions?: string[];
  // ponytail: só existe pra satisfazer o narrowing do TS — o acesso a
  // game_role_name no renderRow já está guardado por `fn` (só truthy quando
  // !p.virtual); linhas virtuais nunca populam este campo.
  game_role_name?: string | null;
}
type ParticipantRow = (Participant & { virtual?: false }) | VirtualParticipantRow;

function ParticipantsSection({ detail, act, canManage }: {
  detail: EventDetail; act: (p: Promise<EventDetail>) => void; canManage: boolean;
}) {
  const t = useT();
  const [roles, setRoles] = useState<CatalogRole[]>([]);
  const [regearPanel, setRegearPanel] = useState<{ pid: number; est: RegearEstimate | null; loading: boolean } | null>(null);
  const [newName, setNewName] = useState("");
  const [newPct, setNewPct] = useState("100");
  const isActive = !["finalized", "cancelled", "deleted"].includes(detail.state);
  const assignedIds = useMemo(() => new Set(detail.participants.map((p) => p.user_id)), [detail.participants]);
  const unassignedSignups = detail.signups.filter((s) => !assignedIds.has(s.user_id));
  const roleFnById = useMemo(
    () => Object.fromEntries(roles.map((r) => [r.id, r.invisible_function])) as Record<number, string | null>,
    [roles],
  );
  // Lista única (sem seções à parte) — participantes de verdade + inscritos
  // sem presença + ausentes-em-batalha, todos como uma linha (virtual=true
  // pros dois últimos, percent 0), ordenada junto por % desc / nome.
  const rows = useMemo<ParticipantRow[]>(() => {
    const real: ParticipantRow[] = detail.participants.map((p) => ({ ...p, virtual: false }));
    const signupRows: ParticipantRow[] = unassignedSignups.map((s) => ({
      virtual: true, rowKey: `signup-${s.user_id}`, user_id: s.user_id, user_name: s.user_name,
      percent: 0, origin: "signup_no_call", functions: s.functions,
    }));
    const absenteeRows: ParticipantRow[] = detail.battle_absentees
      .filter((a) => !assignedIds.has(a.user_id))
      .map((a) => ({
        virtual: true, rowKey: `absentee-${a.user_id}`, user_id: a.user_id, user_name: a.user_name,
        percent: 0, origin: "battle_no_call",
      }));
    return [...real, ...signupRows, ...absenteeRows].sort((a, b) =>
      b.percent - a.percent ||
      (a.user_name || "").localeCompare(b.user_name || "", undefined, { sensitivity: "base" })
    );
  }, [detail.participants, unassignedSignups, detail.battle_absentees, assignedIds]);

  useEffect(() => {
    api.listRoles().then(setRoles).catch(() => {});
  }, []);

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

  function submitAdd() {
    if (!newName.trim()) return;
    act(api.addParticipant(detail.id, {
      user_id: Date.now(), user_name: newName.trim(), percent: Number(newPct) || 0, is_valid: true,
    }));
    setNewName(""); setNewPct("100");
  }

  function renderRow(p: ParticipantRow) {
    const fn = !p.virtual && p.game_role_id ? roleFnById[p.game_role_id] : null;
    const rowKey = p.virtual ? p.rowKey : p.id;
    const active = !p.virtual && regearPanel?.pid === p.id;

    function commitPercent(n: number) {
      if (p.virtual) {
        // Linha virtual (inscrito sem presença / ausente em batalha): editar o
        // % materializa ela como EventParticipant de verdade — mesma ação que
        // o antigo botão "+" das seções à parte, só que embutida no input.
        act(api.addParticipant(detail.id, { user_id: p.user_id, user_name: p.user_name ?? undefined, percent: n, is_valid: true }));
      } else if (n !== p.percent) {
        act(api.updateParticipant(detail.id, p.id, { percent: n, is_valid: true }));
      }
    }

    return (
      <div key={rowKey}>
        <div style={{
          display: "flex", alignItems: "center", gap: 6, padding: "3px 6px",
          background: active ? "var(--surface)" : "var(--surface-2)",
          borderRadius: "var(--radius-sm)", marginBottom: 2, fontSize: 12.5,
          border: active ? "1px solid var(--info)44" : "1px solid transparent",
        }}>
          {/* Crucial: nome, % e remover — SEMPRE visíveis, mesmo na bracket mínima. */}
          <span style={{ flex: 1, fontWeight: 500, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {p.user_name || `#${p.user_id}`}
          </span>
          {/* Origem além-da-escalação (sempre visível, fora do ev-participant-extra
              que some na bracket mínima). Tooltip explica o porquê mesmo a 100%. */}
          {p.origin && <OriginBadge origin={p.origin} />}
          <span className="ev-participant-extra">
            {!p.virtual && p.is_trial && <span className="hint" style={{ fontSize: 10 }} title={t("trialBadge")}>T</span>}
            {/* Função: só o ícone do tipo (tank/healer/...) da planilha; nome no
                hover. A escolha da função acontece na aba de escalação, não aqui. */}
            {fn && (
              <span title={p.game_role_name ?? ""} style={{ display: "inline-flex", flexShrink: 0 }}>
                <RoleIcon role={fn} size={12} className="hint" />
              </span>
            )}
            {/* Inscrito sem presença: funções que escolheu na inscrição. */}
            {p.virtual && p.functions && p.functions.length > 0 && (
              <span className="hint" style={{ fontSize: 10 }}>{p.functions.join(", ")}</span>
            )}
            {!p.virtual && p.silver_received > 0 && (
              <span style={{ color: "#6bbf73", fontSize: 11, flexShrink: 0 }}>{fmt(p.silver_received)}</span>
            )}
            {!p.virtual && p.game_role_id && (
              <button
                style={{ background: "none", border: "none", color: "var(--info)", cursor: "pointer", padding: "1px 3px", fontSize: 11, flexShrink: 0 }}
                title={t("calcRegearTitle")}
                onClick={() => !p.virtual && (regearPanel?.pid === p.id ? setRegearPanel(null) : showRegear(p))}
              >
                <i className="ti ti-calculator" aria-hidden />
              </button>
            )}
          </span>
          {/* % — sempre um campo aberto (não é mais clique-pra-editar). Editar
              o valor já implica válido (entra no split, mesmo que fosse irregular;
              numa linha virtual, cria o participante). */}
          {isActive && canManage ? (
            <span className="ev-pct-input" style={{ flexShrink: 0 }}>
              <input
                type="text" inputMode="numeric"
                defaultValue={p.percent}
                style={{ width: 30, fontSize: 11, padding: "1px 4px", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text)", textAlign: "right" }}
                onBlur={(e) => commitPercent(Number(e.target.value) || 0)}
                onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
              />
              <span className="ev-pct-suffix">%</span>
            </span>
          ) : (
            <span className="hint" style={{ flexShrink: 0 }}>{p.percent}%</span>
          )}
          {isActive && canManage && !p.virtual && (
            <button
              style={{ background: "none", border: "none", color: "var(--hint)", cursor: "pointer", padding: "1px 3px", flexShrink: 0 }}
              title={t("removeParticipantTitle")}
              onClick={() => !p.virtual && act(api.removeParticipant(detail.id, p.id))}
            >
              <i className="ti ti-x" aria-hidden />
            </button>
          )}
        </div>
        {active && (
          <RegearEstimatePanel
            estimate={regearPanel!.est} loading={regearPanel!.loading}
            onApply={applyRegearAsDeath} onClose={() => setRegearPanel(null)}
          />
        )}
      </div>
    );
  }

  return (
    <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12, marginTop: 4, marginBottom: 12 }}>
      <div style={{ color: "var(--muted)", fontSize: 13, display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
        <i className="ti ti-users" aria-hidden />
        {t("participantsLabel")}
        {rows.length > 0 && <span className="badge info" style={{ marginLeft: 4 }}>{rows.length}</span>}
      </div>
      {rows.length === 0 ? (
        <p className="hint" style={{ marginBottom: 8 }}>{t("participantsAutoAddHint")}</p>
      ) : (
        <div className="ev-participants-grid">
          {rows.map(renderRow)}
        </div>
      )}
      {isActive && canManage && (
        <div style={{ display: "flex", gap: 4, marginTop: 8 }}>
          <input className="input" style={{ flex: 1, minWidth: 0, fontSize: 12, padding: "3px 6px" }} placeholder={t("nameWord")}
            value={newName} onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitAdd()} />
          <input className="input" style={{ width: 44, fontSize: 12, padding: "3px 4px" }} placeholder="%"
            value={newPct} onChange={(e) => setNewPct(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitAdd()} />
          <button className="btn" style={{ padding: "3px 8px" }} onClick={submitAdd} disabled={!newName.trim()} title={t("addBtn")}>
            <i className="ti ti-plus" aria-hidden />
          </button>
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
