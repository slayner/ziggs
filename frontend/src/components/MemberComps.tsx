// Comps da guilda — visualização read-only para membros. Sem botões de
// criar/editar/apagar; apenas a estrutura pública (parties/slots/roles).
// Não embute o CompBuilder (1900 linhas, editável por natureza).
import { useEffect, useState } from "react";
import { api, setGuild, type MemberCompSummary, type MemberCompDetail } from "../api";
import { useT } from "../i18n";

export default function MemberComps({ guildId }: { guildId: string }) {
  const t = useT();
  useEffect(() => { setGuild(guildId); }, [guildId]);

  const [comps, setComps] = useState<MemberCompSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [detail, setDetail] = useState<MemberCompDetail | null | undefined>(undefined);

  useEffect(() => {
    let dead = false;
    setLoading(true); setError(null);
    api.memberComps()
      .then(c => { if (!dead) setComps(c); })
      .catch(e => { if (!dead) setError(String(e.message)); })
      .finally(() => { if (!dead) setLoading(false); });
    return () => { dead = true; };
  }, [guildId]);

  useEffect(() => {
    if (openId === null) { setDetail(undefined); return; }
    let dead = false;
    setDetail(undefined);
    api.memberComp(openId)
      .then(d => { if (!dead) setDetail(d); })
      .catch(() => { if (!dead) setDetail(null); });
    return () => { dead = true; };
  }, [openId]);

  if (loading) return <div className="container"><p className="hint">{t("loading")}</p></div>;
  if (error) return <div className="container"><p className="mp-error">{error}</p></div>;
  if (comps.length === 0) return <div className="container"><p className="hint">{t("memberCompsEmpty")}</p></div>;

  return (
    <div className="container">
      <header className="events-command-head" style={{ marginBottom: 16 }}>
        <div>
          <small>{t("managementKicker")}</small>
          <h2>{t("memberCompsTitle")}</h2>
          <p>{t("memberCompsIntro")}</p>
        </div>
      </header>

      <div className="ev-card-list">
        {comps.map(c => (
          <article key={c.id} className="ev-card">
            <button className="event-row ev-card-trigger" onClick={() => setOpenId(openId === c.id ? null : c.id)} aria-expanded={openId === c.id}>
              <span className="ev-card-symbol"><i className="ti ti-layout-grid" aria-hidden="true" /></span>
              <span className="ev-card-copy">
                <strong>{c.name}</strong>
                <small>
                  {c.description && <span>{c.description}</span>}
                  <span>{c.party_count} {t("memberCompsParties")}</span>
                </small>
              </span>
              <i className={"ti ev-card-chevron " + (openId === c.id ? "ti-chevron-up" : "ti-chevron-down")} aria-hidden="true" />
            </button>
            {openId === c.id && (
              <div className="ev-card-body">
                {detail === undefined && <p className="hint">{t("loading")}</p>}
                {detail === null && <p className="mp-error">{t("evLoadError")}</p>}
                {detail && (
                  <div className="mc-parties">
                    {detail.parties.map((party, pi) => (
                      <div className="mc-party" key={party.id}>
                        <small className="mp-wname">{party.name || `${t("memberCompsParty")} ${pi + 1}`}</small>
                        <div className="mc-slots">
                          {party.slots.map(slot => (
                            <div className="mc-slot" key={slot.id}>
                              <span className="mc-slot-fn state-pill cur">{slot.fn || "—"}</span>
                              <div className="mc-slot-roles">
                                {slot.roles.map(r => (
                                  <span className="mp-chip" key={r.id}>{r.name}</span>
                                ))}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}