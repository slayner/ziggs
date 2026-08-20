// Eventos do membro — lista de eventos publicados + auto-inscrição pelo site
// + divulgação de settlement de eventos finalizados. Rotas /member/* exigem
// seulement membresia ativa; sem perms admin. Mesma fonte de verdade do bot:
// event_signups.upsert_signup com actor_source="site".
import { useEffect, useState } from "react";
import {
  api, setGuild,
  type MemberEventSummary, type MemberEventDetail,
  type MemberSignupOptions,
} from "../api";
import { useLang, useT, type TKey } from "../i18n";

const ACTIVE = new Set(["scheduled", "in_progress", "review"]);
const STATE_KEYS: Record<string, TKey> = {
  scheduled: "stateScheduled", in_progress: "stateInProgress",
  review: "stateReview", finalized: "stateFinalized",
  cancelled: "stateCancelled", deleted: "stateDeleted",
};

const fmt = (n: number) => n.toLocaleString("pt-BR");
function eventTime(iso: string | null, locale: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat(locale, {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC",
  }).format(d) + " UTC";
}

export default function MemberEvents({ guildId }: { guildId: string }) {
  const t = useT();
  const { lang } = useLang();
  useEffect(() => { setGuild(guildId); }, [guildId]);

  const [events, setEvents] = useState<MemberEventSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  useEffect(() => {
    let dead = false;
    setLoading(true); setError(null);
    api.memberEvents()
      .then(e => { if (!dead) setEvents(e); })
      .catch(e => { if (!dead) setError(String(e.message)); })
      .finally(() => { if (!dead) setLoading(false); });
    return () => { dead = true; };
  }, [guildId]);

  const active = events.filter(e => ACTIVE.has(e.state));
  const history = events.filter(e => !ACTIVE.has(e.state));

  function toggle(id: number) {
    setExpanded(prev => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  }

  function renderCard(e: MemberEventSummary) {
    const isOpen = expanded.has(e.id);
    const cls = e.state === "cancelled" || e.state === "deleted" ? "bad"
      : e.state === "finalized" ? "done" : "cur";
    return (
      <article key={e.id} className={`ev-card state-${e.state}`}>
        <button className="event-row ev-card-trigger" onClick={() => toggle(e.id)} aria-expanded={isOpen}>
          <span className="ev-card-symbol"><i className="ti ti-calendar-event" aria-hidden="true" /></span>
          <span className="ev-card-copy">
            <strong>{e.title || `Event #${e.id}`}</strong>
            <small>
              <span><i className="ti ti-clock" aria-hidden="true" /> {eventTime(e.scheduled_at ?? e.started_at, lang)}</span>
              {e.caller_name && <span><i className="ti ti-user" aria-hidden="true" /> {e.caller_name}</span>}
            </small>
          </span>
          <span className={"state-pill " + cls}>{t(STATE_KEYS[e.state] ?? ("state" + e.state))}</span>
          <i className={"ti ev-card-chevron " + (isOpen ? "ti-chevron-up" : "ti-chevron-down")} aria-hidden="true" />
        </button>
        {isOpen && <MemberEventBody eventId={e.id} guildId={guildId} />}
      </article>
    );
  }

  return (
    <div className="container events-command">
      <header className="events-command-head">
        <div>
          <small>{t("managementKicker")}</small>
          <h2>{t("memberEventsTitle")}</h2>
          <p>{t("memberEventsIntro")}</p>
        </div>
        <dl className="events-command-stats">
          <div><dt>{t("eventsActiveGroup")}</dt><dd>{active.length}</dd></div>
          <div><dt>{t("eventsHistoryGroup")}</dt><dd>{history.length}</dd></div>
        </dl>
      </header>

      {error && <div className="ev-list-error" role="alert"><i className="ti ti-alert-circle" aria-hidden="true" /> {error}</div>}
      {loading && <div className="ev-list-loading"><i className="ti ti-loader-2 spin" aria-hidden="true" /> {t("loading")}</div>}
      {!loading && !error && events.length === 0 && <div className="card ev-empty"><i className="ti ti-calendar-off" aria-hidden="true" /><p className="muted">{t("noEventsYet")}</p></div>}

      {active.length > 0 && (
        <section className="ev-group ev-group-active">
          <div className="ev-group-title"><span>{t("eventsActiveGroup")}</span><i /><b>{active.length}</b></div>
          <div className="ev-card-list">{active.map(renderCard)}</div>
        </section>
      )}
      {history.length > 0 && (
        <section className="ev-group history">
          <div className="ev-group-title"><span>{t("eventsHistoryGroup")}</span><i /><b>{history.length}</b></div>
          <div className="ev-card-list">{history.map(renderCard)}</div>
        </section>
      )}
    </div>
  );
}

// ── Corpo do card (detalhe + signup + settlement) ──────────────────────────

function MemberEventBody({ eventId, guildId }: { eventId: number; guildId: string }) {
  const t = useT();
  const { lang } = useLang();
  const [detail, setDetail] = useState<MemberEventDetail | null | undefined>(undefined);
  const [opts, setOpts] = useState<MemberSignupOptions | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    let dead = false;
    setDetail(undefined); setOpts(null); setMsg(null);
    api.memberEvent(eventId)
      .then(d => { if (!dead) setDetail(d); })
      .catch(() => { if (!dead) setDetail(null); });
    api.memberSignupOptions(eventId)
      .then(o => {
        if (dead) return;
        setOpts(o);
        // Pré-seleção: preselected + current weapon_fns → keys.
        const init = new Set<string>(o.preselected);
        if (o.current) {
          for (const wf of o.current.weapon_fns) {
            // casa por weapon_id + fn (fn pode ser null → "other").
            const match = o.eligible.find(e => e.weapon_id === wf.weapon_id && (e.fn ?? "other") === (wf.fn ?? "other"));
            if (match) init.add(match.key);
          }
        }
        setSelected(init);
      })
      .catch(() => {});
    return () => { dead = true; };
  }, [eventId, guildId]);

  function toggleOpt(key: string) {
    setSelected(prev => {
      const n = new Set(prev);
      if (n.has(key)) n.delete(key); else n.add(key);
      return n;
    });
  }

  function submitSignup() {
    setBusy(true); setMsg(null);
    api.memberSignup(eventId, [...selected])
      .then(() => api.memberSignupOptions(eventId))
      .then(o => { setOpts(o); setMsg(t("signupSaved")); })
      .catch(e => setMsg(String(e.message)))
      .finally(() => setBusy(false));
  }

  function removeSignup() {
    setBusy(true); setMsg(null);
    api.memberSignupDelete(eventId)
      .then(() => {
        setSelected(new Set());
        return api.memberSignupOptions(eventId);
      })
      .then(o => { setOpts(o); setMsg(t("signupRemoved")); })
      .catch(e => setMsg(String(e.message)))
      .finally(() => setBusy(false));
  }

  if (detail === undefined) return <div className="ev-card-body"><p className="hint">{t("loading")}</p></div>;
  if (detail === null) return <div className="ev-card-body"><p className="mp-error">{t("evLoadError")}</p></div>;

  // can_signup vem do backend (MemberEventSummary.can_signup) — só true em
  // scheduled/in_progress. Em review/finalized o membro vê o evento mas não
  // pode se inscrever.
  const canSignup = !!(opts && detail.state !== "review" && detail.state !== "finalized");

  return (
    <div className="ev-card-body">
      {detail.message && (
        <div className="ev-detail-message">
          <i className="ti ti-megaphone" aria-hidden="true" />
          <span>{detail.message}</span>
        </div>
      )}
      <div className="ev-detail-meta">
        <span className="ev-meta-chip" title={t("evScheduledAtLabel")}>
          <i className="ti ti-clock" aria-hidden="true" />
          {eventTime(detail.scheduled_at ?? detail.started_at, lang)}
        </span>
        {detail.comp && (
          <span className="ev-meta-chip" title={t("evCompLabel")}>
            <i className="ti ti-layout-grid" aria-hidden="true" />
            {detail.comp.name}
          </span>
        )}
      </div>

      {/* Inscrição (scheduled/in_progress) */}
      {canSignup && opts && (
        <div className="card" style={{ padding: 12, marginBottom: 12 }}>
          <div className="ev-stage-h"><i className="ti ti-notebook" aria-hidden="true" />{t("signupTitle")}</div>
          {opts.block_reason && <p className="hint" style={{ marginBottom: 8 }}>{opts.block_reason}</p>}
          {opts.eligible.length > 0 ? (
            <>
              <p className="hint" style={{ marginBottom: 8 }}>{t("signupPickPrompt")}</p>
              <div className="mp-chips" style={{ marginBottom: 10 }}>
                {opts.eligible.map(o => (
                  <button
                    key={o.key}
                    className={"mp-chip" + (selected.has(o.key) ? " selected" : "")}
                    style={selected.has(o.key) ? { borderColor: "var(--gold)", background: "var(--gold-soft)" } : undefined}
                    onClick={() => toggleOpt(o.key)}
                  >
                    {o.weapon_name} · {o.fn}
                  </button>
                ))}
              </div>
              {opts.min_builds && selected.size < opts.min_builds && (
                <p className="mp-error">{t("signupMinBuilds").replace("{n}", String(opts.min_builds))}</p>
              )}
              <div className="mp-actions">
                <button className="btn primary" onClick={submitSignup} disabled={busy || (opts.min_builds != null && selected.size < opts.min_builds)}>
                  {busy ? t("saving") : t("signupConfirm")}
                </button>
                {opts.current && (
                  <button className="btn" onClick={removeSignup} disabled={busy}>
                    {t("signupRemove")}
                  </button>
                )}
              </div>
            </>
          ) : (
            <>
              <p className="hint" style={{ marginBottom: 8 }}>{t("signupNoOptions")}</p>
              <div className="mp-actions">
                <button className="btn primary" onClick={submitSignup} disabled={busy}>
                  {busy ? t("saving") : t("signupConfirmPresence")}
                </button>
                {opts.current && (
                  <button className="btn" onClick={removeSignup} disabled={busy}>
                    {t("signupRemove")}
                  </button>
                )}
              </div>
            </>
          )}
          {msg && <p className="hint" style={{ marginTop: 8, color: "var(--green)" }}>{msg}</p>}
        </div>
      )}

      {/* Review — evento em verificação, sem signup nem settlement */}
      {detail.state === "review" && (
        <div className="card" style={{ padding: 12, marginBottom: 12 }}>
          <p className="hint" style={{ margin: 0 }}>{t("memberEventReviewHint")}</p>
        </div>
      )}

      {/* Settlement (finalized) — divulgação de valores */}
      {detail.state === "finalized" && detail.settlement && (
        <div className="card ev-payout-summary">
          <div className="ev-stage-h"><i className="ti ti-coins" aria-hidden="true" />{t("settlementTitle")}</div>
          <div className="ev-payout-totals">
            <span className="ev-payout-tot"><i className="ti ti-vault" aria-hidden="true" /> {t("payoutTab")}: <b>{fmt(detail.settlement.tab_value)}</b></span>
            <span className="ev-payout-tot"><i className="ti ti-coins" aria-hidden="true" /> {t("settlementTotalPaid")}: <b>{fmt(detail.settlement.total_paid)}</b></span>
          </div>
          <table className="ev-payout-table">
            <thead>
              <tr>
                <th>{t("payoutColName")}</th>
                <th className="ev-num-col">{t("payoutColTotal")}</th>
              </tr>
            </thead>
            <tbody>
              {detail.settlement.participants.map(p => (
                <tr key={p.user_id}>
                  <td className="ev-payout-name">{p.display_name}</td>
                  <td className="ev-num-col">{fmt(p.silver_received)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}