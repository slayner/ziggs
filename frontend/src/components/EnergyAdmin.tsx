// Painel administrativo de energia — visível só pra quem tem energy.manage
// (council/lead). Overview de saldos + importar log + ajuste manual + whitelist.
import { useEffect, useState } from "react";
import { api, setGuild, type EnergyAdminOverview } from "../api";
import { useT } from "../i18n";

const fmt = (n: number) => n.toLocaleString("pt-BR");

export default function EnergyAdmin({ guildId }: { guildId: string }) {
  const t = useT();
  useEffect(() => { setGuild(guildId); }, [guildId]);

  const [overview, setOverview] = useState<EnergyAdminOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Import de log
  const [logText, setLogText] = useState("");
  const [importResult, setImportResult] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);

  // Ajuste manual
  const [setUserId, setSetUserId] = useState("");
  const [setValue, setSetValue] = useState("");
  const [setReason, setSetReason] = useState("");
  const [setting, setSetting] = useState(false);

  function refresh() {
    let dead = false;
    setLoading(true); setError(null);
    api.energyAdminOverview()
      .then(o => { if (!dead) setOverview(o); })
      .catch(e => { if (!dead) setError(String(e.message)); })
      .finally(() => { if (!dead) setLoading(false); });
    return () => { dead = true; };
  }
  useEffect(refresh, [guildId]);

  function doImport() {
    if (!logText.trim()) return;
    setImporting(true); setImportResult(null);
    api.energyAdminLogImport(logText)
      .then(r => {
        const res = r.result;
        const parts = [`${res.applied} ${t("energyApplied")}`];
        if (res.duplicates) parts.push(`${res.duplicates} ${t("energyDuplicates")}`);
        if (res.whitelisted_applied) parts.push(`${res.whitelisted_applied} ${t("energyWlApplied")}`);
        const unreg = Object.keys(res.unregistered);
        if (unreg.length) parts.push(`${t("energyUnregistered")}: ${unreg.map(n => `${n}×${res.unregistered[n]}`).join(", ")}`);
        setImportResult(parts.join(" · "));
        setLogText("");
        refresh();
      })
      .catch(e => setImportResult(String(e.message)))
      .finally(() => setImporting(false));
  }

  function doSet() {
    const uid = Number(setUserId);
    const val = Number(setValue);
    if (!uid || isNaN(val)) return;
    setSetting(true);
    api.energyAdminSet(uid, val, setReason || undefined)
      .then(() => {
        setSetUserId(""); setSetValue(""); setSetReason("");
        refresh();
      })
      .catch(e => setError(String(e.message)))
      .finally(() => setSetting(false));
  }

  function toggleWl(uid: number) {
    api.energyAdminWhitelistToggle(uid)
      .then(() => refresh())
      .catch(e => setError(String(e.message)));
  }

  return (
    <div className="container">
      <header className="events-command-head" style={{ marginBottom: 16 }}>
        <div>
          <small>{t("managementKicker")}</small>
          <h2>{t("energyAdminTitle")}</h2>
          <p>{t("energyAdminIntro")}</p>
        </div>
      </header>

      {error && <p className="mp-error">{error}</p>}
      {loading && <p className="hint">{t("loading")}</p>}

      {/* Overview */}
      {overview && !loading && (
        <section className="card" style={{ marginBottom: 16 }}>
          <div className="mp-head">
            <i className="ti ti-bolt" aria-hidden="true" />
            <div>
              <h3>{t("energyOverviewTitle")}</h3>
              <p className="hint">{t("energyOverviewSubtitle").replace("{0}", String(overview.threshold))}</p>
            </div>
          </div>
          {overview.members.length === 0 ? (
            <p className="hint">{t("energyNoMembers")}</p>
          ) : (
            <table className="mp-ledger">
              <thead>
                <tr>
                  <th>{t("payoutColName")}</th>
                  <th className="ev-num-col">{t("energyBalance")}</th>
                  <th>{t("energyWhitelist")}</th>
                </tr>
              </thead>
              <tbody>
                {overview.members.map(m => (
                  <tr key={m.user_id} className={m.low_energy ? "mc-low" : undefined}>
                    <td>
                      {m.display_name}
                      {m.low_energy && <span className="state-pill bad" style={{ marginLeft: 6 }}>{t("energyLow")}</span>}
                    </td>
                    <td className="ev-num-col">{fmt(m.balance)}</td>
                    <td>
                      <button
                        className={"btn" + (m.whitelisted ? " primary" : "")}
                        style={{ fontSize: 11, padding: "3px 10px" }}
                        onClick={() => toggleWl(m.user_id)}
                      >
                        {m.whitelisted ? t("energyWlOn") : t("energyWlOff")}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {/* Importar log */}
      <section className="card" style={{ marginBottom: 16 }}>
        <div className="mp-head">
          <i className="ti ti-file-import" aria-hidden="true" />
          <div>
            <h3>{t("energyImportTitle")}</h3>
            <p className="hint">{t("energyImportSubtitle")}</p>
          </div>
        </div>
        <textarea
          className="input"
          style={{ width: "100%", minHeight: 120, fontFamily: "monospace", fontSize: 12 }}
          placeholder={t("energyImportPlaceholder")}
          value={logText}
          onChange={e => setLogText(e.target.value)}
        />
        <div className="mp-actions">
          <button className="btn primary" onClick={doImport} disabled={importing || !logText.trim()}>
            {importing ? t("saving") : t("energyImportBtn")}
          </button>
        </div>
        {importResult && <p className="hint" style={{ marginTop: 8 }}>{importResult}</p>}
      </section>

      {/* Ajuste manual */}
      <section className="card">
        <div className="mp-head">
          <i className="ti ti-edit" aria-hidden="true" />
          <div>
            <h3>{t("energySetTitle")}</h3>
            <p className="hint">{t("energySetSubtitle")}</p>
          </div>
        </div>
        {overview && overview.members.length > 0 && (
          <div className="mp-add">
            <label className="cs-field">
              <span className="cs-field-lbl">{t("payoutColName")}</span>
              <select className="cs-select" value={setUserId} onChange={e => setSetUserId(e.target.value)}>
                <option value="">—</option>
                {overview.members.map(m => <option key={m.user_id} value={String(m.user_id)}>{m.display_name}</option>)}
              </select>
            </label>
            <label className="cs-field">
              <span className="cs-field-lbl">{t("energySetValue")}</span>
              <input className="input" type="number" value={setValue} onChange={e => setSetValue(e.target.value)} placeholder="0" />
            </label>
            <label className="cs-field">
              <span className="cs-field-lbl">{t("energySetReason")}</span>
              <input className="input" value={setReason} onChange={e => setSetReason(e.target.value)} placeholder={t("energySetReasonPh")} />
            </label>
            <button className="btn" onClick={doSet} disabled={setting || !setUserId || setValue === ""}>
              {setting ? t("saving") : t("save")}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}