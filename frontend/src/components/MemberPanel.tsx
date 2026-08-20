// Meu painel — portal do membro: carteira (prata), energia e preferências
// arma+fn. Rotas /member/* só exigem membresia ativa (403 caso contrário),
// então este tab é visível a qualquer membro logado, sem perms admin.
import { useEffect, useState } from "react";
import {
  api, setGuild,
  type MemberEnergy, type MemberEnergyEntry,
  type MemberWallet, type MemberWalletTx,
  type WeaponFnPref, type WeaponFnValidPair,
} from "../api";
import { useT, type TKey } from "../i18n";

const PAGE = 50;
const fmt = (n: number) => n.toLocaleString("pt-BR");
const dt = (s: string) => {
  const d = new Date(s);
  return isNaN(d.getTime()) ? s : d.toLocaleString();
};

const WALLET_KIND_KEYS: Record<string, TKey> = {
  event_payout: "walletKindEventPayout",
  event_deficit: "walletKindEventDeficit",
  pay: "walletKindPay",
  add: "walletKindAdd",
  remove: "walletKindRemove",
  forfeit: "walletKindForfeit",
  bank_adjust: "walletKindBankAdjust",
};

export default function MemberPanel({ guildId }: { guildId: string }) {
  const t = useT();
  // Bate o g() do api.ts com a guilda do tab (mesmo padrão do RegearPage).
  useEffect(() => { setGuild(guildId); }, [guildId]);

  // ── Prata ────────────────────────────────────────────────────────────────
  const [wallet, setWallet] = useState<MemberWallet | null>(null);
  const [txs, setTxs] = useState<MemberWalletTx[]>([]);
  const [wLoading, setWLoading] = useState(true);
  const [wMore, setWMore] = useState(false);
  const [wError, setWError] = useState<string | null>(null);

  useEffect(() => {
    let dead = false;
    setWLoading(true); setWError(null);
    api.memberWallet(PAGE, 0)
      .then(w => { if (!dead) { setWallet(w); setTxs(w.transactions); } })
      .catch(e => { if (!dead) setWError(String(e.message)); })
      .finally(() => { if (!dead) setWLoading(false); });
    return () => { dead = true; };
  }, [guildId]);

  function moreWallet() {
    if (!wallet || wMore || txs.length >= wallet.total) return;
    setWMore(true);
    api.memberWallet(PAGE, txs.length)
      .then(w => { setWallet(w); setTxs(cur => [...cur, ...w.transactions]); })
      .catch(e => setWError(String(e.message)))
      .finally(() => setWMore(false));
  }

  // ── Energia ──────────────────────────────────────────────────────────────
  const [energy, setEnergy] = useState<MemberEnergy | null>(null);
  const [entries, setEntries] = useState<MemberEnergyEntry[]>([]);
  const [eLoading, setELoading] = useState(true);
  const [eMore, setEMore] = useState(false);
  const [eError, setEError] = useState<string | null>(null);

  useEffect(() => {
    let dead = false;
    setELoading(true); setEError(null);
    api.memberEnergy(PAGE, 0)
      .then(en => { if (!dead) { setEnergy(en); setEntries(en.entries); } })
      .catch(e => { if (!dead) setEError(String(e.message)); })
      .finally(() => { if (!dead) setELoading(false); });
    return () => { dead = true; };
  }, [guildId]);

  function moreEnergy() {
    if (!energy || eMore || entries.length >= energy.total) return;
    setEMore(true);
    api.memberEnergy(PAGE, entries.length)
      .then(en => { setEnergy(en); setEntries(cur => [...cur, ...en.entries]); })
      .catch(e => setEError(String(e.message)))
      .finally(() => setEMore(false));
  }

  // ── Minhas roles (arma+fn) ────────────────────────────────────────────────
  const [prefs, setPrefs] = useState<WeaponFnPref[] | null>(null);
  const [validPairs, setValidPairs] = useState<WeaponFnValidPair[]>([]);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<WeaponFnPref[]>([]);
  const [saving, setSaving] = useState(false);
  const [rError, setRError] = useState<string | null>(null);

  useEffect(() => {
    let dead = false;
    setRError(null);
    api.memberWeaponFnPrefs()
      .then(p => { if (!dead) { setPrefs(p.preferences); setValidPairs(p.valid_pairs); } })
      .catch(e => { if (!dead) setRError(String(e.message)); });
    return () => { dead = true; };
  }, [guildId]);

  const shown = editing ? draft : (prefs ?? []);

  // Armas e fns VÁLIDOS = pares que aparecem em alguma comp ativa da guilda.
  // O backend devolve `valid_pairs` já filtrado — não usamos listWeapons()
  // (catálogo global) nem getCompFnTypes() (gated por comps.view).
  const weaponOptions = [...new Map(validPairs.map(p => [p.weapon_id, p.weapon_name])).entries()]
    .map(([id, name]) => ({ id, name }))
    .sort((a, b) => a.name.localeCompare(b.name));
  const fnOptionsFor = (wid: number) =>
    validPairs.filter(p => p.weapon_id === wid).map(p => ({ key: p.fn, label: p.fn }));

  const fnLabel = (fn: string) => fn;  // fn já é o label vindo do backend

  // Chips agrupadas por arma (weapon_name → pares).
  const byWeapon = new Map<string, WeaponFnPref[]>();
  for (const p of shown) {
    const arr = byWeapon.get(p.weapon_name) ?? [];
    if (!arr.some(x => x.weapon_id === p.weapon_id && x.fn === p.fn)) arr.push(p);
    byWeapon.set(p.weapon_name, arr);
  }

  const [addW, setAddW] = useState("");
  const [addFn, setAddFn] = useState("");

  function addPair() {
    const wid = Number(addW);
    const fn = addFn.trim();
    if (!wid || !fn || draft.some(p => p.weapon_id === wid && p.fn === fn)) return;
    const name = weaponOptions.find(w => w.id === wid)?.name ?? `w${wid}`;
    setDraft(d => [...d, { weapon_id: wid, fn, weapon_name: name }]);
    setAddW(""); setAddFn("");
  }

  function savePrefs() {
    setSaving(true); setRError(null);
    api.memberWeaponFnPrefsPut(draft.map(({ weapon_id, fn }) => ({ weapon_id, fn })))
      .then(p => { setPrefs(p.preferences); setEditing(false); })
      .catch(e => setRError(String(e.message)))
      .finally(() => setSaving(false));
  }

  return (
    <div className="mp">
      <section className="card">
        <header className="mp-head">
          <i className="ti ti-coins" aria-hidden="true" />
          <div>
            <h3>{t("walletTitle")}</h3>
            <p className="hint">{t("walletSubtitle")}</p>
          </div>
        </header>
        <div className="mp-balance-row">
          <div>
            <small>{t("walletBalance")}</small>
            <strong className="mp-balance">{fmt(wallet?.balance ?? 0)}</strong>
          </div>
          <div>
            <small>{t("walletTotalEarned")}</small>
            <strong className="mp-sub">{fmt(wallet?.total_earned ?? 0)}</strong>
          </div>
        </div>
        {wError && <p className="mp-error">{wError}</p>}
        {wLoading && <p className="hint">{t("loading")}</p>}
        {!wLoading && txs.length === 0 && !wError && <p className="hint">{t("walletEmpty")}</p>}
        {txs.length > 0 && (
          <table className="mp-ledger">
            <tbody>
              {txs.map(tx => {
                const kindKey = WALLET_KIND_KEYS[tx.kind];
                return (
                  <tr key={tx.id} className={tx.undone ? "mp-undone" : undefined}>
                    <td className="mp-date">{dt(tx.created_at)}</td>
                    <td>
                      <span className="state-pill">{kindKey ? t(kindKey) : tx.kind}</span>
                      {tx.undone && <span className="state-pill bad">{t("walletUndone")}</span>}
                    </td>
                    <td className={`mp-amt ${tx.direction}`}
                      title={tx.direction === "in" ? t("walletDirIn") : tx.direction === "out" ? t("walletDirOut") : undefined}>
                      {tx.direction === "in" ? "+" : tx.direction === "out" ? "−" : ""}{fmt(tx.amount)}
                    </td>
                    <td className="mp-cp">{tx.counterparty_name ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {wallet && txs.length < wallet.total && (
          <button className="btn mp-more" onClick={moreWallet} disabled={wMore}>
            {wMore ? t("loading") : t("walletLoadMore")}
          </button>
        )}
      </section>

      <section className="card">
        <header className="mp-head">
          <i className="ti ti-bolt" aria-hidden="true" />
          <div>
            <h3>{t("energyTitle")}</h3>
            <p className="hint">{t("energySubtitle")}</p>
          </div>
        </header>
        <div className="mp-balance-row">
          <div>
            <small>{t("energyBalance")}</small>
            <strong className="mp-balance">{fmt(energy?.balance ?? 0)}</strong>
          </div>
        </div>
        {eError && <p className="mp-error">{eError}</p>}
        {eLoading && <p className="hint">{t("loading")}</p>}
        {!eLoading && entries.length === 0 && !eError && <p className="hint">{t("energyEmpty")}</p>}
        {entries.map(en => (
          <div className="mp-erow" key={en.id}>
            <span className="mp-date">{en.ts}</span>
            <span className={`state-pill ${en.kind === "adjustment" ? "cur" : en.kind === "baseline" ? "future" : ""}`}>
              {en.kind === "adjustment" ? t("energyKindAdjustment") : en.kind === "baseline" ? t("energyKindBaseline") : t("energyKindLog")}
            </span>
            <span className="mp-player">{en.player}</span>
            {en.reason && <span className="mp-reason">{en.reason}</span>}
            <span className={`mp-amt ${en.amount >= 0 ? "in" : "out"}`}>{en.amount > 0 ? "+" : ""}{en.amount}</span>
          </div>
        ))}
        {energy && entries.length < energy.total && (
          <button className="btn mp-more" onClick={moreEnergy} disabled={eMore}>
            {eMore ? t("loading") : t("energyLoadMore")}
          </button>
        )}
      </section>

      <section className="card">
        <header className="mp-head">
          <i className="ti ti-swords" aria-hidden="true" />
          <div>
            <h3>{t("rolesTitle")}</h3>
            <p className="hint">{t("rolesSubtitle")}</p>
          </div>
        </header>
        {rError && <p className="mp-error">{rError}</p>}
        {prefs === null && !rError && <p className="hint">{t("loading")}</p>}
        {prefs !== null && shown.length === 0 && <p className="hint">{t("rolesEmpty")}</p>}
        {[...byWeapon.entries()].map(([wname, rows]) => (
          <div className="mp-wgroup" key={wname}>
            <small className="mp-wname">{wname}</small>
            <div className="mp-chips">
              {rows.map(p => (
                <span className="mp-chip" key={`${p.weapon_id}:${p.fn}`}>
                  {fnLabel(p.fn)}
                  {editing && (
                    <button className="chip-remove" title={t("remove")} aria-label={t("remove")}
                      onClick={() => setDraft(d => d.filter(x => !(x.weapon_id === p.weapon_id && x.fn === p.fn)))}>
                      <i className="ti ti-x" aria-hidden="true" />
                    </button>
                  )}
                </span>
              ))}
            </div>
          </div>
        ))}
        {editing && (
          weaponOptions.length > 0 ? (
            <div className="mp-add">
              <label className="cs-field">
                <span className="cs-field-lbl">{t("rolesWeapon")}</span>
                <select className="cs-select" value={addW} onChange={e => { setAddW(e.target.value); setAddFn(""); }}>
                  <option value="">{t("rolesWeapon")}</option>
                  {weaponOptions.map(w => <option key={w.id} value={String(w.id)}>{w.name}</option>)}
                </select>
              </label>
              <label className="cs-field">
                <span className="cs-field-lbl">{t("rolesFn")}</span>
                <select className="cs-select" value={addFn} onChange={e => setAddFn(e.target.value)} disabled={!addW}>
                  <option value="">{t("rolesFn")}</option>
                  {addW && fnOptionsFor(Number(addW)).map(f => <option key={f.key} value={f.key}>{f.label}</option>)}
                </select>
              </label>
              <button className="btn" onClick={addPair} disabled={!addW || !addFn}>{t("rolesAdd")}</button>
            </div>
          ) : (
            <p className="hint">{t("rolesNoOptions")}</p>
          )
        )}
        {prefs !== null && !editing && (
          <div className="mp-actions">
            <button className="btn" onClick={() => { setDraft(prefs); setEditing(true); setRError(null); }}>
              {t("rolesEdit")}
            </button>
          </div>
        )}
        {editing && (
          <div className="mp-actions">
            <button className="btn primary" onClick={savePrefs} disabled={saving}>
              {saving ? t("saving") : t("save")}
            </button>
            <button className="btn" onClick={() => { setEditing(false); setRError(null); }} disabled={saving}>
              {t("cancel")}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
