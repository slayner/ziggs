import { useEffect, useState } from "react";
import { useT, useLang, REGION_LABELS, type TKey } from "../i18n";
import { Panel } from "./Panel";

const DOWNLOAD_URL_WINDOWS = "https://ziggs.xyz/companion/Ziggs-Companion_0.2.0_x64-setup.exe";
const GITHUB_URL = "https://github.com/slayner/ziggs";
const MANIFEST_URL = "/vps-manifest.json";

const ALBION_REGIONS = ["americas", "europe", "asia"] as const;

type VpsEntry = {
  id: string;
  label: string;
  country: string;
  endpoint: string;
  server_pubkey: string;
  ping_url: string;
};

type VpsPing = { americas: number; asia: number; europe: number };

type VpsRow = {
  vps: VpsEntry;
  pings: VpsPing | null;
  loading: boolean;
  error: boolean;
};

const FEATURES: { icon: string; title: TKey; desc: TKey }[] = [
  { icon: "ti-radar-2",          title: "companionFeatScanTitle",    desc: "companionFeatScanDesc" },
  { icon: "ti-route",            title: "companionFeatTunnelTitle",  desc: "companionFeatTunnelDesc" },
  { icon: "ti-swords",           title: "companionFeatDmgTitle",     desc: "companionFeatDmgDesc" },
  { icon: "ti-clipboard-text",   title: "companionFeatLootlogTitle", desc: "companionFeatLootlogDesc" },
  { icon: "ti-coins",            title: "companionFeatPricesTitle",  desc: "companionFeatPricesDesc" },
  { icon: "ti-list-details",     title: "companionFeatAutoLootTitle",desc: "companionFeatAutoLootDesc" },
  { icon: "ti-world-bolt",       title: "companionFeatDnsTitle",     desc: "companionFeatDnsDesc" },
  { icon: "ti-layout-bottombar", title: "companionFeatTrayTitle",    desc: "companionFeatTrayDesc" },
];

function pingColor(ms: number | null): string {
  if (ms == null) return "var(--muted)";
  if (ms < 80) return "var(--green)";
  if (ms < 180) return "#e0a23b";
  return "#ef4444";
}

function regionShort(r: string): string {
  if (r === "americas") return "AM";
  if (r === "europe") return "EU";
  if (r === "asia") return "AS";
  return r;
}

function FeatureCard({ icon, title, desc }: { icon: string; title: TKey; desc: TKey }) {
  const t = useT();
  return (
    <Panel className="cp-feat">
      <div className="cp-feat-icon">
        <i className={`ti ${icon}`} />
      </div>
      <h3 className="cp-feat-title">{t(title)}</h3>
      <p className="cp-feat-desc">{t(desc)}</p>
    </Panel>
  );
}

function PingMatrix({ rows }: { rows: VpsRow[] }) {
  const t = useT();
  const { lang } = useLang();
  if (rows.length === 0) return null;
  return (
    <Panel className="p-0">
      <div className="dash-panel-h">
        <h2 className="dash-panel-title">{t("companionNetTitle")}</h2>
        <span className="dash-panel-rule" />
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="vps-matrix">
          <thead>
            <tr>
              <th className="vps-mx-corner">{t("companionNetConn")}</th>
              {ALBION_REGIONS.map(r => (
                <th key={r} className="vps-mx-col">
                  <span className="vps-mx-region">{regionShort(r)}</span>
                  <small>{REGION_LABELS[lang][r] ?? r}</small>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ vps, pings, loading, error }) => (
              <tr key={vps.id}>
                <td className="vps-mx-row">
                  <div className="vps-mx-row-inner">
                    <span className="vps-mx-flag">
                      <i className="ti ti-route" />
                    </span>
                    <div>
                      <span>{vps.label}</span>
                      <small>{vps.country}</small>
                    </div>
                  </div>
                </td>
                {ALBION_REGIONS.map(r => {
                  const ms = pings ? pings[r] : null;
                  return (
                    <td key={r} className="vps-mx-cell">
                      {loading ? (
                        <span className="vps-mx-ping vps-mx-loading">
                          <i className="ti ti-loader-2 spin" />
                        </span>
                      ) : error ? (
                        <span className="vps-mx-ping vps-mx-error">—</span>
                      ) : (
                        <span
                          className="vps-mx-ping"
                          style={{ color: pingColor(ms) }}
                        >
                          {ms != null ? `${ms.toFixed(0)}ms` : "—"}
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="vps-mx-hint">{t("companionNetHint")}</p>
    </Panel>
  );
}

export default function CompanionPage() {
  const t = useT();
  const [vpsRows, setVpsRows] = useState<VpsRow[]>([]);

  useEffect(() => {
    let alive = true;
    (async () => {
      let manifest: VpsEntry[];
      try {
        const res = await fetch(MANIFEST_URL);
        if (!res.ok) return;
        const data = await res.json();
        manifest = data.vps ?? [];
      } catch {
        return;
      }
      if (!alive || manifest.length === 0) return;
      setVpsRows(manifest.map(v => ({ vps: v, pings: null, loading: true, error: false })));
      const results = await Promise.allSettled(
        manifest.map(async v => {
          const res = await fetch(v.ping_url, { cache: "no-store" });
          if (!res.ok) throw new Error(`ping ${v.id}`);
          return (await res.json()) as VpsPing;
        }),
      );
      if (!alive) return;
      setVpsRows(prev =>
        prev.map((row, i) => {
          const r = results[i];
          if (r.status === "fulfilled") {
            return { ...row, pings: r.value, loading: false, error: false };
          }
          return { ...row, loading: false, error: true };
        }),
      );
    })();
    return () => { alive = false; };
  }, []);

  return (
    <div className="cp-page">
      {/* Hero */}
      <div className="cp-hero">
        <div className="cp-hero-badges">
          <a className="cp-badge" href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
            <i className="ti ti-brand-github" /> {t("companionOpenSource")}
          </a>
          <span className="cp-badge cp-badge-mut">
            <i className="ti ti-license" /> MIT
          </span>
        </div>
        <h1 className="cp-hero-title">Ziggs Companion</h1>
        <p className="cp-hero-tagline">{t("companionTagline")}</p>
        <div className="cp-hero-cta">
          <a className="btn btn-lg" href={DOWNLOAD_URL_WINDOWS}>
            <i className="ti ti-brand-windows" /> {t("companionDownloadWin")}
          </a>
          <a className="cp-github-link" href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
            <i className="ti ti-brand-github" /> {t("companionViewSource")}
          </a>
        </div>
        <p className="cp-hero-meta">{t("companionRequirements")}</p>
      </div>

      {/* Ping matrix */}
      <PingMatrix rows={vpsRows} />

      {/* Features */}
      <div className="cp-feat-grid">
        {FEATURES.map(f => <FeatureCard key={f.title} {...f} />)}
      </div>

      {/* Privacy note */}
      <p className="cp-privacy-note">{t("companionPrivacyShort")}</p>
    </div>
  );
}