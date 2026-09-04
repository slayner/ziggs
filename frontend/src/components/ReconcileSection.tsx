import { useEffect, useMemo, useState } from "react";
import { api, setGuild, type EventSummary, type UnifiedReconcile, type ReconcileLooterItem } from "../api";
import { useT, useLang, itemLocalName, type TKey } from "../i18n";
import { itemRenderUrl, ITEM_BY_ID } from "../data/albion-items";
import { prioritizeEvents } from "../lib/events";
import LootReconcileBody from "./LootReconcilePage";
import LootLogBody from "./LootLogPage";

interface Props { guildId: string }

type SubTab = "log" | "lootlog";

function fmt(n: number): string { return n.toLocaleString("pt-BR"); }
function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

function ItemIcon({ itemId, itemName, size }: { itemId: string; itemName: string; size: number }) {
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!failed) return;
    const timer = window.setInterval(() => {
      setFailed(false);
      setAttempt(current => current + 1);
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [failed]);
  const src = `${itemRenderUrl(itemId)}${itemRenderUrl(itemId).includes("?") ? "&" : "?"}retry=${attempt}`;
  if (failed) return <span title={itemName || itemId} style={{ width: size, height: size, display: "inline-flex", alignItems: "center", justifyContent: "center", border: "1px solid var(--border)", borderRadius: 3, color: "var(--hint)", fontSize: Math.max(11, size - 6) }}>?</span>;
  return <img src={src} alt="" width={size} height={size} onLoad={() => setLoaded(true)} onError={() => setFailed(true)} style={{ opacity: loaded ? 1 : 0.45 }} />;
}

/** Orquestra Log (reconcile/baú) e Lootlog como sub-abas do mesmo evento:
 * seletor de evento único (compartilhado), as próprias abas SÃO o header do
 * quadrante (ocupam a largura toda, centralizadas — nada de botões soltos
 * acima dele), e a lista de quem não depositou fica sempre visível embaixo,
 * independente da sub-aba ativa. */
export default function ReconcileSection({ guildId }: Props) {
  const t = useT();
  const { lang } = useLang();
  const [events, setEvents] = useState<EventSummary[]>([]);
  const [eventId, setEventId] = useState<number | null>(null);
  const [rec, setRec] = useState<UnifiedReconcile | null>(null);
  const [busy, setBusy] = useState(false);
  const [sub, setSub] = useState<SubTab>("log");
  const [visibleStatuses, setVisibleStatuses] = useState(new Set(["missing", "verified", "deposited", "died"]));
  const [visibleGuilds, setVisibleGuilds] = useState<Set<string> | null>(null);

  useEffect(() => { setGuild(guildId); }, [guildId]);

  useEffect(() => {
    let alive = true;
    api.listEvents().then(evs => { if (alive) setEvents(prioritizeEvents(evs)); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (eventId == null && events.length > 0) setEventId(events[0].id);
  }, [events, eventId]);

  async function loadRec(id: number) {
    setBusy(true);
    try { setRec(await api.getReconcile(id)); }
    catch { setRec(null); }
    finally { setBusy(false); }
  }

  // Toggle "conferido" (vermelho ↔ amarelo). Otimista: flipa o item no estado
  // local e reverte se a chamada falhar. Só itens missing são clicáveis.
  async function toggleVerify(looter: string, it: ReconcileLooterItem) {
    if (it.status !== "missing" || eventId == null) return;
    const before = rec;
    setRec(r => r && {
      ...r,
      looters: r.looters.map(l => l.looted_by !== looter ? l : {
        ...l, items: l.items.map(x => x === it ? { ...x, verified: !x.verified } : x),
      }),
    });
    try { await api.verifyReconcileItem(eventId, looter, it.item_id); }
    catch { setRec(before); }
  }

  useEffect(() => {
    if (eventId != null) loadRec(eventId);
    else setRec(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventId]);

  const totals = useMemo(() => rec ? [
    { label: t("recTotalLooted"), value: rec.total_looted_value, color: "var(--text)" },
    { label: t("recTotalChest"), value: rec.total_chest_value, color: "var(--green)" },
    { label: t("recMissing"), value: rec.missing_value, color: "var(--gold)" },
    { label: t("recRegear"), value: rec.total_regear_value, color: "var(--info)" },
  ] : [], [rec, t]);

  const guilds = useMemo(() => rec ? [...new Set(
    rec.looters.flatMap(looter => looter.items.map(item => item.looted_by_guild || ""))
  )].sort((a, b) => a.localeCompare(b)) : [], [rec]);

  return (
    <div>
      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 14 }}>
        <label style={{ fontSize: 13, color: "var(--muted)" }}>{t("llEvent")}</label>
        <select
          value={eventId ?? ""} onChange={e => setEventId(e.target.value ? Number(e.target.value) : null)}
          style={{ minWidth: 360 }}
        >
          <option value="">{t("llEventNone")}</option>
          {events.map(ev => (
            <option key={ev.id} value={ev.id}>
              #{ev.id} {ev.title || ""} ({ev.state})
            </option>
          ))}
        </select>
      </div>

      {eventId != null && (
        <>
          {/* As abas SÃO o header do quadrante — ocupam a largura toda
              (flex:1 cada), centralizadas. Livres (sem bloco/fundo atrás do
              nome) — só a linha embaixo na ativa, igual nos perfis
              (PlayerProfilePage: border-b-2 + cor do texto, sem background). */}
          <div style={{
            border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden",
            background: "var(--surface)", marginBottom: 16,
          }}>
            <div style={{ display: "flex", borderBottom: "1px solid var(--border)" }}>
              {([["log", "recSubLog"], ["lootlog", "ll"]] as [SubTab, TKey][]).map(([id, label]) => (
                <button
                  key={id}
                  onClick={() => setSub(id)}
                  style={{
                    flex: 1, textAlign: "center", padding: "10px 4px", fontSize: 13,
                    fontWeight: sub === id ? 600 : 500,
                    background: "transparent",
                    color: sub === id ? "var(--gold)" : "var(--muted)",
                    border: "none", borderBottom: sub === id ? "2px solid var(--gold)" : "2px solid transparent",
                    marginBottom: -1, cursor: "pointer", transition: "color .15s",
                  }}
                >
                  {t(label)}
                </button>
              ))}
            </div>
            {/* Keep-alive: ambas as sub-abas ficam montadas, só alternamos
                visibilidade com display:none. Trocar de aba NÃO remonta →
                não refaz fetch. LootLogBody chama guildInfo (HTTP Discord ~5s)
                + listLootLog no mount; refazer isso a cada clique amontoava
                chamadas e congelava o site. Manter montada também evita o
                "Lootlog preso em Loading…" de um fetch velho sobrescrevendo
                o estado da nova instância. */}
            <div style={sub === "log" ? undefined : { display: "none" }}>
              <LootReconcileBody eventId={eventId} onReload={() => loadRec(eventId)} />
            </div>
            <div style={sub === "lootlog" ? undefined : { display: "none" }}>
              <LootLogBody guildId={guildId} eventId={eventId} events={events} />
            </div>
          </div>

          {/* Extras da sub-aba Log (totais/badges/tabelas) — fora do
              quadrante trocável, só fazem sentido pro reconcile em si. */}
          {sub === "log" && rec && (
            <>
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 16 }}>
                {totals.map((tt, i) => (
                  <div key={i} className="lootlog-card" style={{ flex: "1 1 140px" }}>
                    <div className="hint" style={{ fontSize: 12 }}>{tt.label}</div>
                    <div style={{ fontSize: 20, fontWeight: 700, color: tt.color }}>{fmt(tt.value)}</div>
                  </div>
                ))}
              </div>

              <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
                <span className={`badge ${rec.has_loot_log ? "badge-green" : "badge-muted"}`}>{t("recLogLoot")}: {rec.has_loot_log ? "✓" : "—"}</span>
                <span className={`badge ${rec.has_chest_log ? "badge-green" : "badge-muted"}`}>{t("recLogChest")}: {rec.has_chest_log ? "✓" : "—"}</span>
                <span className={`badge ${rec.has_deaths ? "badge-green" : "badge-muted"}`}>{t("recLogDeaths")}: {rec.has_deaths ? "✓" : "—"}</span>
              </div>

              {rec.died_with.length > 0 && (
                <div className="lootlog-card" style={{ marginBottom: 16 }}>
                  <strong style={{ color: "var(--info)" }}>{t("recDiedWith")} ({rec.died_with.length})</strong>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, marginTop: 10 }}>
                    <thead>
                      <tr style={{ borderBottom: "1px solid var(--border)" }}>
                        <th style={{ textAlign: "left", padding: "4px 8px", color: "var(--hint)" }}>{t("llFrom")}</th>
                        <th style={{ textAlign: "right", padding: "4px 8px", color: "var(--hint)" }}>{t("recRegear")}</th>
                        <th style={{ textAlign: "left", padding: "4px 8px", color: "var(--hint)" }}>{t("recRecovered")}</th>
                        <th style={{ textAlign: "left", padding: "4px 8px", color: "var(--hint)" }}>{t("llTime")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rec.died_with.map((d, i) => (
                        <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                          <td style={{ padding: "4px 8px" }}>{d.display_name}</td>
                          <td style={{ textAlign: "right", padding: "4px 8px", color: "var(--info)" }}>{fmt(d.silver_value)}</td>
                          <td style={{ padding: "4px 8px", fontSize: 11 }}>
                            {d.recovered_items.length === 0
                              ? <span className="hint">—</span>
                              : d.recovered_items.map((r, j) => (
                                <span key={j} style={{ display: "inline-flex", alignItems: "center", gap: 4, marginRight: 10 }}>
                                   {r.item_id && <ItemIcon itemId={r.item_id} itemName={r.item_name || r.item_id} size={18} />}
                                  {(r.item_id ? (ITEM_BY_ID.get(r.item_id) ? itemLocalName(ITEM_BY_ID.get(r.item_id)!, lang) : r.item_name) : r.item_name) || "?"}
                                  <span className="hint">×{r.quantity}</span>
                                </span>
                              ))}
                          </td>
                          <td style={{ padding: "4px 8px", color: "var(--muted)", fontSize: 11 }}>{d.notes || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {rec.deposited.length > 0 && (
                <div className="lootlog-card" style={{ marginBottom: 16 }}>
                  <strong style={{ color: "var(--green)" }}>{t("recDeposited")} ({rec.deposited.length})</strong>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, marginTop: 10 }}>
                    <thead>
                      <tr style={{ borderBottom: "1px solid var(--border)" }}>
                        <th style={{ textAlign: "left", padding: "4px 8px", color: "var(--hint)" }}>{t("llItem")}</th>
                        <th style={{ textAlign: "right", padding: "4px 8px", color: "var(--hint)" }}>{t("llQty")}</th>
                        <th style={{ textAlign: "left", padding: "4px 8px", color: "var(--hint)" }}>{t("recDepositedBy")}</th>
                        <th style={{ textAlign: "left", padding: "4px 8px", color: "var(--hint)" }}>{t("llTime")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rec.deposited.map((c, i) => {
                        const it = ITEM_BY_ID.get(c.item_type);
                        return (
                          <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                            <td style={{ padding: "4px 8px", display: "flex", alignItems: "center", gap: 6 }}>
                              <img src={itemRenderUrl(c.item_type)} alt="" width={20} height={20} />
                              {it ? itemLocalName(it, lang) : c.item_name}
                            </td>
                            <td style={{ textAlign: "right", padding: "4px 8px", color: "var(--muted)" }}>{c.quantity}</td>
                            <td style={{ padding: "4px 8px" }}>{c.deposited_by_name || "—"}</td>
                            <td style={{ padding: "4px 8px", color: "var(--muted)" }}>{fmtDate(c.snapshot_at)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}

              {!rec.has_loot_log && !rec.has_chest_log && !rec.has_deaths && (
                <p className="hint">{t("recEmpty")}</p>
              )}
            </>
          )}
        </>
      )}

      {busy && <p className="hint">{t("loading")}</p>}

      {/* Fixo abaixo, nas duas sub-abas: devido por JOGADOR (foco no looter,
          não no item). Cores: vermelho = roubado (sobreviveu, não depositou),
          amarelo = conferido, verde = depositado, cinza = morreu com (fora do
          devido). Só os vermelhos/amarelos são clicáveis (toggle conferido). */}
      {rec && (rec.looters.length > 0 || rec.has_loot_log) && (
        <div className="lootlog-card" style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
            <strong style={{ color: "var(--gold)" }}>{t("recByPlayer")}</strong>
            <span className="hint" style={{ fontSize: 11 }}>{t("recVerifyHint")}</span>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "8px 0 2px", fontSize: 11 }}>
            {([
              ["missing", "var(--red)", t("recItemMissing")],
              ["verified", "var(--gold)", t("recVerified")],
              ["deposited", "var(--green)", t("recDeposited")],
              ["died", "var(--muted)", t("recDiedWith")],
            ] as [string, string, string][]).map(([status, color, label]) => {
              const active = visibleStatuses.has(status);
              return <button
                key={status} type="button"
                onClick={() => setVisibleStatuses(current => {
                  const next = new Set(current);
                  if (next.has(status)) next.delete(status); else next.add(status);
                  return next;
                })}
                style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "3px 7px", borderRadius: 5, border: `1px solid ${color}`, background: "transparent", color: "var(--muted)", opacity: active ? 1 : 0.4, cursor: "pointer", fontSize: 11 }}
              >
                <span style={{ width: 9, height: 9, borderRadius: 2, background: color, display: "inline-block" }} />{label}
              </button>;
            })}
          </div>
          {guilds.length > 0 && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "8px 0 2px", fontSize: 11 }}>
              <span className="hint" style={{ alignSelf: "center" }}>{t("recGuildFilters")}</span>
              <button type="button" onClick={() => setVisibleGuilds(null)} style={{ padding: "3px 7px", borderRadius: 5, border: "1px solid var(--border)", background: "transparent", color: "var(--muted)", opacity: visibleGuilds === null ? 1 : 0.4, cursor: "pointer", fontSize: 11 }}>{t("recGuildAll")}</button>
              {guilds.map(guild => {
                const active = visibleGuilds === null || visibleGuilds.has(guild);
                const label = guild || t("recGuildUnknown");
                return <button key={guild || "unknown"} type="button" onClick={() => setVisibleGuilds(current => {
                  const next = new Set(current ?? guilds);
                  if (next.has(guild)) next.delete(guild); else next.add(guild);
                  return next;
                })} style={{ padding: "3px 7px", borderRadius: 5, border: "1px solid var(--border)", background: "transparent", color: "var(--muted)", opacity: active ? 1 : 0.4, cursor: "pointer", fontSize: 11 }}>{label}</button>;
              })}
            </div>
          )}

          {rec.looters.length === 0 ? (
            <p className="hint" style={{ marginTop: 10 }}>{t("recNoOwed")}</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
              {rec.looters.map(l => (
                <div key={l.looted_by} style={{ borderTop: "1px solid var(--border)", paddingTop: 8 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 6, gap: 10 }}>
                    <strong style={{ fontSize: 13 }}>{l.looted_by}</strong>
                    {l.missing_value > 0 && (
                      <span style={{ color: "var(--red)", fontWeight: 700, fontSize: 13 }}>{fmt(l.missing_value)}</span>
                    )}
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {l.items.map((it, j) => {
                      const displayStatus = it.status === "missing" && it.verified ? "verified" : it.status;
                       if (!visibleStatuses.has(displayStatus)) return null;
                       if (visibleGuilds !== null && !visibleGuilds.has(it.looted_by_guild || "")) return null;
                       const clickable = it.status === "missing";
                      const color = it.status === "deposited" ? "var(--green)"
                        : it.status === "died" ? "var(--muted)"
                        : it.verified ? "var(--gold)" : "var(--red)";
                      const known = ITEM_BY_ID.get(it.item_id);
                      const name = known ? itemLocalName(known, lang) : it.item_name;
                      const label = it.status === "deposited" ? t("recDeposited")
                        : it.status === "died" ? t("recDiedWith")
                        : it.verified ? t("recVerified") : t("recStolen");
                      return (
                        <button
                          key={j} type="button" disabled={!clickable} title={label}
                          onClick={clickable ? () => toggleVerify(l.looted_by, it) : undefined}
                          style={{
                            display: "inline-flex", alignItems: "center", gap: 5,
                            padding: "3px 8px 3px 4px", borderRadius: 6,
                            border: `1px solid ${color}`, background: "transparent",
                            opacity: it.status === "died" ? 0.55 : 1,
                            cursor: clickable ? "pointer" : "default", fontSize: 12,
                          }}
                        >
                          <ItemIcon itemId={it.item_id} itemName={name} size={22} />
                          <span title={known ? undefined : it.item_id} style={{ color: it.status === "died" ? "var(--muted)" : "var(--text)" }}>{name}</span>
                          <span style={{ color, fontWeight: 600 }}>×{it.quantity}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
