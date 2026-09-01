import { useEffect, useMemo, useRef, useState } from "react";
import { api, setGuild, type RegearRequest, type RegearSettings, type RegearQueueItem } from "../api";
import { navigate } from "../router";
import { useT, useLang, itemLocalName } from "../i18n";
import { itemRenderUrl, ITEM_BY_ID } from "../data/albion-items";

interface Props { guildId: string; initialRequestId?: number; eventId?: number; active?: boolean }

type Filter = "all" | "pending" | "paid" | "denied";

const RECOG_ICON: Record<string, string> = {
  recognized: "✅", manual: "⚠️", error: "❌",
};
const KILLBOARD_URL = (id: string) => `https://albiononline.com/en/killboard/event/${id}`;

function fmt(n: number): string {
  return n.toLocaleString("pt-BR");
}
function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

export default function RegearPage({ guildId, initialRequestId, eventId, active = true }: Props) {
  const t = useT();
  const { lang } = useLang();
  const [filter, setFilter] = useState<Filter>(initialRequestId ? "all" : "pending");
  // Filtro por evento (deep link /regears?event=ID vindo do review). Null = fila
  // geral. Setado via prop; limpar volta pra fila geral (navega pra /regears).
  const eventFilter = eventId ?? null;
  const [list, setList] = useState<RegearRequest[] | null>(null);
  const [settings, setSettings] = useState<RegearSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [bankBalance, setBankBalance] = useState<number | null>(null);
  // edits por request id: { finalText, notes }
  const [edits, setEdits] = useState<Record<number, { finalText: string; notes: string }>>({});
  const [busy, setBusy] = useState<Record<number, string>>({});  // id -> action in progress
  const [zoom, setZoom] = useState<RegearRequest | null>(null);

  // Deep link: o backend resolve pelo guild_id do cookie. Sincroniza o guild
  // atual com o da URL pra o `g()` do api.ts bater (snowflake vem como string).
  useEffect(() => { setGuild(guildId); }, [guildId]);

  // Deep link do bot: abre direto a screenshot do pedido recebido.
  const didAutoZoom = useRef(false);
  // ponytail: keep-alive reusa esta instância entre deep links de regear;
  // resetar o guard quando o requestId muda, senão um pedido diferente não
  // auto-zoomaria (didAutoZoom já true do link anterior).
  useEffect(() => { didAutoZoom.current = false; }, [initialRequestId]);
  useEffect(() => {
    if (!list || didAutoZoom.current || !initialRequestId) return;
    const r = list.find(x => x.id === initialRequestId);
    if (r) { setZoom(r); didAutoZoom.current = true; }
  }, [list, initialRequestId]);

  async function load() {
    try {
      const [data, cfg] = await Promise.all([
        api.listRegear(filter === "all" ? undefined : filter, eventFilter ?? undefined),
        settings ? Promise.resolve(settings) : api.getRegearSettings(),
      ]);
      setList(data.requests);
      setSettings(cfg);
      // inicializa edits p/ pedidos novos
      setEdits(prev => {
        const next = { ...prev };
        for (const r of data.requests) {
          if (!next[r.id]) {
            next[r.id] = {
              finalText: r.final_total != null ? String(r.final_total) : "",
              notes: r.notes ?? "",
            };
          }
        }
        return next;
      });
      setError(null);
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
    }
  }

  async function loadBank() {
    try {
      const g = await api.guildInfo(guildId);
      setBankBalance(g.bank_balance ?? 0);
    } catch { /* silencioso — saldo é informativo, não crítico */ }
  }

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [filter, eventFilter]);
  useEffect(() => { loadBank(); }, [guildId]);
  // Polling leve só na fila pendente — pedidos pagos/negados mudam pouco.
  // ponytail: !active pausa quando a página tá escondida (keep-alive no App e
  // no ManagementPage) — sem isso o poll de 15s rodaria em background.
  useEffect(() => {
    if (filter !== "pending" || !active) return;
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [filter, active]);
  // Saldo do banco: poll de 30s (mais leve que a fila — só muda em approve/tax)
  useEffect(() => {
    if (!active) return;
    const id = setInterval(loadBank, 30000);
    return () => clearInterval(id);
  }, [active, guildId]);

  const canManage = true; // a view só é renderizada pra quem tem events.manage (ver App.tsx)

  async function act(id: number, action: string, fn: () => Promise<unknown>) {
    setBusy(b => ({ ...b, [id]: action }));
    try {
      await fn();
      await load();
    } catch (e) {
      alert(String((e as Error)?.message ?? e));
    } finally {
      setBusy(b => { const n = { ...b }; delete n[id]; return n; });
    }
  }

  function effectiveFinal(ed: { finalText: string } | undefined): number | undefined {
    const txt = (ed?.finalText ?? "").trim();
    if (txt === "") return undefined;          // vazio → usa sugerido
    const n = Number(txt);
    return Number.isFinite(n) && n >= 0 ? Math.round(n) : undefined;
  }

  function isEdited(r: RegearRequest, ed: { finalText: string } | undefined): boolean {
    const v = effectiveFinal(ed);
    return v != null && v !== r.suggested_total;
  }

  async function saveEdits(r: RegearRequest) {
    const ed = edits[r.id];
    const payload: Record<string, unknown> = { notes: ed?.notes ?? "" };
    const v = effectiveFinal(ed);
    if (v != null) payload.final_total = v;
    await act(r.id, "save", () => api.updateRegear(r.id, payload));
  }
  async function approve(r: RegearRequest) {
    const ed = edits[r.id];
    const payload: Record<string, unknown> = { status: "paid", notes: ed?.notes ?? "" };
    const v = effectiveFinal(ed);
    if (v != null) payload.final_total = v;
    await act(r.id, "pay", () => api.updateRegear(r.id, payload));
  }
  async function deny(r: RegearRequest) {
    await act(r.id, "deny", () => api.updateRegear(r.id, { status: "denied" }));
  }
  async function removeReq(r: RegearRequest) {
    if (!confirm(t("regearConfirmRemove"))) return;
    await act(r.id, "remove", () => api.removeRegear(r.id));
  }
  async function excludeItem(r: RegearRequest, idx: number) {
    // remove o item da lista → backend re-soma base/suggested só dos elegíveis.
    const items = r.detected_items.filter((_, i) => i !== idx);
    await act(r.id, "edit", () => api.updateRegear(r.id, { detected_items: items }));
  }

  const statusBadge = (s: string) => {
    const map: Record<string, [string, string]> = {
      pending: ["regearPending", "badge"],
      paid: ["regearPaid", "badge badge-green"],
      denied: ["regearDenied", "badge badge-red"],
      removed: ["regearRemoved", "badge"],
    };
    const [key, cls] = map[s] ?? ["regearPending", "badge"];
    return <span className={cls}>{t(key as never)}</span>;
  };

  const filters: Filter[] = ["pending", "all", "paid", "denied"];
  const filterBtn = (f: Filter) => (
    <button key={f} className={filter === f ? "active" : ""} onClick={() => setFilter(f)}>
      {t(`regearFilter${f.charAt(0).toUpperCase() + f.slice(1)}` as never)}
    </button>
  );

  return (
    <div className="regear-page">
      <div className="regear-head">
        <h2><i className="ti ti-receipt-refund" /> {t("regearTitle")}</h2>
        {bankBalance != null && (
          <span className="regear-bank-balance">
            <i className="ti ti-building-bank" aria-hidden /> {t("guildBankBalance")}: <strong>{fmt(bankBalance)}</strong>
          </span>
        )}
        {settings && settings.payment_pct < 100 && (
          <span className="regear-cov-hint">{t("regearCoverage")}: {settings.payment_pct}%</span>
        )}
      </div>
      <p className="regear-channel-hint"><i className="ti ti-info-circle" /> {t("regearChannelHint")}</p>

      <div className="regear-filters">{filters.map(filterBtn)}</div>

      {eventFilter && (
        <div className="regear-event-tag">
          <i className="ti ti-calendar" aria-hidden />
          <span>{t("regearEventFilter")}: #{eventFilter}</span>
          <button className="btn" style={{ fontSize: 11, padding: "2px 8px" }}
            onClick={() => navigate(`/regears`)}
            title={t("regearEventFilterClear")}>
            <i className="ti ti-x" aria-hidden />
          </button>
        </div>
      )}

      {error && <div className="regear-error">⚠️ {error}</div>}

      {list === null
        ? <p className="muted">{t("loading")}</p>
        : list.length === 0
          ? <p className="muted">{t("regearQueueEmpty")}</p>
          : <div className="regear-list">
              {list.map(r => {
                const ed = edits[r.id];
                const edited = isEdited(r, ed);
                const eff = effectiveFinal(ed);
                return (
                  <div key={r.id} className={`regear-card regear-${r.status}`}>
                    <div className="regear-card-top">
                      <div className="regear-shot">
                        <img src={r.screenshot_url} alt="screenshot"
                          onClick={() => setZoom(r)} loading="lazy" />
                      </div>
                      <div className="regear-meta">
                        <div className="regear-meta-row">
                          {statusBadge(r.status)}
                          <span className="regear-recog" title={r.recognition_status}>
                            {RECOG_ICON[r.recognition_status] ?? "⚠️"} {t(`regear${cap(r.recognition_status)}` as never)}
                          </span>
                        </div>
                        <div className="regear-meta-line"><b>{t("regearRequestId")}:</b> #{r.id}</div>
                        {r.economy_transaction_id != null && <div className="regear-meta-line"><b>{t("regearTransactionId")}:</b> #{r.economy_transaction_id}</div>}
                        <div className="regear-meta-line"><b>{t("regearRequester")}:</b> {r.requester_name ?? "—"}</div>
                        {r.requester_role_ids_snapshot.length > 0 && <div className="regear-meta-line"><b>{t("regearRole")}:</b> {r.requester_role_ids_snapshot.map(roleId => `@${roleId}`).join(", ")}</div>}
                        {r.event_participation_snapshot.percent != null && <div className="regear-meta-line"><b>{t("regearEventParticipation")}:</b> {r.event_participation_snapshot.percent}%</div>}
                        <div className="regear-meta-line"><b>{t("regearDeathAt")}:</b> {fmtDate(r.death_timestamp)}</div>
                        {r.event_id && (
                          <div className="regear-meta-line">
                            <button className="regear-event-link" title={t("regearOpenEvent")}
                              onClick={() => navigate(`/events/${guildId}/${r.event_id}`)}>
                              <i className="ti ti-calendar" aria-hidden /> #{r.event_id}{r.event_title ? ` · ${r.event_title}` : ""}
                            </button>
                          </div>
                        )}
                        {r.ocr_name && <div className="regear-meta-line"><b>{t("regearOcrName")}:</b> {r.ocr_name}</div>}
                        {r.albion_event_id
                          ? <a className="regear-kb" href={KILLBOARD_URL(r.albion_event_id)} target="_blank" rel="noreferrer">
                              <i className="ti ti-external-link" /> {t("regearKillboard")}
                            </a>
                          : <div className="regear-meta-line muted">{t("regearNoEvent")}</div>}
                      </div>
                    </div>

                    <table className="regear-items">
                      <thead><tr>
                        <th>{t("regearItem")}</th>
                        <th className="num">{t("regearUnit")}</th>
                        <th className="num">{t("regearTotal")}</th>
                        {canManage && <th></th>}
                      </tr></thead>
                      <tbody>
                        {r.detected_items.length === 0 && (
                          <tr><td colSpan={canManage ? 4 : 3} className="muted">{t("regearNoEvent")}</td></tr>
                        )}
                        {r.detected_items.map((it, i) => (
                          <ItemRow key={i} it={it} lang={lang}
                            canManage={canManage} onExclude={() => excludeItem(r, i)} />
                        ))}
                      </tbody>
                    </table>

                    <div className="regear-totals">
                      <div><b>{t("regearBaseTotal")}:</b> {fmt(r.base_total)}</div>
                      <div><b>{t("regearCoverage")}:</b> {r.coverage_pct}%</div>
                      <div>
                        <b>{edited ? t("regearEdited") : t("regearSuggested")}:</b>{" "}
                        <span className={edited ? "regear-edited" : ""}>{fmt(edited ? (eff ?? 0) : r.suggested_total)}</span>
                      </div>
                    </div>

                    {canManage && r.status !== "removed" && (
                      <div className="regear-actions">
                        <label className="regear-final">
                          {t("regearFinalTotal")}
                          <input
                            type="number" min={0} step={1}
                            placeholder={t("regearFinalPlaceholder")}
                            value={ed?.finalText ?? ""}
                            onChange={e => setEdits(p => ({ ...p, [r.id]: { ...(p[r.id] ?? { finalText: "", notes: "" }), finalText: e.target.value } }))}
                          />
                        </label>
                        <textarea
                          className="regear-notes" rows={1} placeholder={t("regearNotesPlaceholder")}
                          value={ed?.notes ?? ""}
                          onChange={e => setEdits(p => ({ ...p, [r.id]: { ...(p[r.id] ?? { finalText: "", notes: "" }), notes: e.target.value } }))}
                        />
                        <button className="btn" disabled={!!busy[r.id]} onClick={() => saveEdits(r)}>
                          {busy[r.id] === "save" ? "…" : t("regearSave")}
                        </button>
                        {r.status !== "paid" && r.status !== "denied" && (
                          <button className="btn btn-green" disabled={!!busy[r.id]} onClick={() => approve(r)}>
                            {busy[r.id] === "pay" ? t("regearApproving") : t("regearApprove")}
                          </button>
                        )}
                        {r.status !== "denied" && (
                          <button className="btn" disabled={!!busy[r.id]} onClick={() => deny(r)}>
                            {t("regearDeny")}
                          </button>
                        )}
                        <button className="btn btn-ghost danger" disabled={!!busy[r.id]} onClick={() => removeReq(r)}>
                          {t("regearRemove")}
                        </button>
                      </div>
                    )}
                    {r.handled_at && (r.status === "paid" || r.status === "denied") && (
                      <div className="regear-handled muted">
                        {t("regearUpdatedJustNow")} {fmtDate(r.handled_at)}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>}

      {zoom && (
        <div className="regear-zoom" onClick={() => setZoom(null)}>
          <img src={zoom.screenshot_url} alt="screenshot" />
          <span className="regear-zoom-close"><i className="ti ti-x" /> {t("regearOpenFull")}</span>
        </div>
      )}
    </div>
  );
}

function cap(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

function ItemRow({ it, lang, canManage, onExclude }: {
  it: RegearQueueItem; lang: ReturnType<typeof useLang>["lang"]; canManage: boolean; onExclude: () => void;
}) {
  const t = useT();
  const localName = useMemo(() => {
    const item = ITEM_BY_ID.get(it.item_id);
    return item ? itemLocalName(item, lang) : it.name;
  }, [it.item_id, it.name, lang]);
  return (
    <tr className={it.eligible ? "" : "regear-not-covered"}>
      <td className="regear-item-cell">
        <img src={itemRenderUrl(it.item_id, it.quality || 1)} alt="" />
        <span>{localName}</span>
        {!it.eligible
          ? <span className="badge badge-muted">{t("regearNotCovered")}</span>
          : <span className="badge badge-green">{t("regearEligible")}</span>}
      </td>
      <td className="num">{it.unit_price ? fmt(it.unit_price) : "—"}</td>
      <td className="num">{it.total_price ? fmt(it.total_price) : "—"}</td>
      {canManage && <td className="regear-exclude">
        <button className="btn btn-ghost" title="—" onClick={onExclude}><i className="ti ti-x" /></button>
      </td>}
    </tr>
  );
}