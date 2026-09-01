import { useEffect, useState } from "react";
import { useT, useLang, REGION_LABELS, type TKey } from "../i18n";
import { Panel } from "./Panel";

const GITHUB_URL = "https://github.com/slayner/ziggs";
const PINGS_URL = "/companion/vps-pings";
const RELEASE_URL = "/companion/latest.json";
const ALBION_REGIONS = ["americas", "europe", "asia"] as const;

type VpsPing = { americas: number; asia: number; europe: number };
type VpsRow = { label: string; country: string; pings: VpsPing | null; loading: boolean; error: boolean };
type Release = {
  platforms?: Record<string, { url?: string }>;
  downloads?: Record<string, { url?: string }>;
};

const PLATFORM = {
  "windows-x86_64": { icon: "ti-brand-windows", label: "Windows" },
  "linux-x86_64": { icon: "ti-brand-linux", label: "Linux" },
  "darwin-x86_64": { icon: "ti-brand-apple", label: "macOS (Intel)" },
  "darwin-aarch64": { icon: "ti-brand-apple", label: "macOS (Apple Silicon)" },
} as const;

type BrowserPlatform = "windows" | "linux" | "darwin" | null;

function detectedPlatform(): BrowserPlatform {
  if (typeof navigator === "undefined") return null;
  const platform = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform
    ?? navigator.platform
    ?? navigator.userAgent;
  if (/Windows/i.test(platform)) return "windows";
  if (/Macintosh|Mac OS X|MacIntel/i.test(platform)) return "darwin";
  if (/Linux/i.test(platform)) return "linux";
  return null;
}

function availableDownload(platform: BrowserPlatform, downloads: Record<string, string>): [string, string] | null {
  if (!platform) return null;
  return Object.entries(downloads).find(([key]) => key.startsWith(platform)) ?? null;
}

const FEATURES: { icon: string; title: TKey; desc: TKey }[] = [
  { icon: "ti-swords",         title: "companionFeatDmgTitle",     desc: "companionFeatDmgDesc" },
  { icon: "ti-clipboard-text", title: "companionFeatLootlogTitle", desc: "companionFeatLootlogDesc" },
  { icon: "ti-route",          title: "companionFeatTunnelTitle",  desc: "companionFeatTunnelDesc" },
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
            {rows.map(({ label, country, pings, loading, error }, i) => (
              <tr key={i}>
                <td className="vps-mx-row">
                  <div className="vps-mx-row-inner">
                    <span className="vps-mx-flag">
                      <i className="ti ti-route" />
                    </span>
                    <div>
                      <span>{label}</span>
                      <small>{country}</small>
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
  const [downloads, setDownloads] = useState<Record<string, string>>({});
  const [browserPlatform, setBrowserPlatform] = useState<BrowserPlatform>(null);

  useEffect(() => {
    setBrowserPlatform(detectedPlatform());
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      let data: { label: string; country: string; pings: VpsPing | null }[];
      try {
        const res = await fetch(PINGS_URL, { cache: "no-store" });
        if (!res.ok) return;
        data = await res.json();
      } catch {
        return;
      }
      if (!alive || !Array.isArray(data) || data.length === 0) return;
      setVpsRows(data.map(d => ({
        label: d.label,
        country: d.country,
        pings: d.pings,
        loading: false,
        error: d.pings == null,
      })));
    })();
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    fetch(RELEASE_URL, { cache: "no-store" })
      .then(res => res.ok ? res.json() as Promise<Release> : null)
      .then(release => {
        const manifest = release?.downloads ?? release?.platforms;
        if (!alive || !manifest) return;
        const urls: Record<string, string> = {};
        for (const [platform, item] of Object.entries(manifest)) {
          if (platform in PLATFORM && typeof item.url === "string") urls[platform] = item.url;
        }
        setDownloads(urls);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  const download = availableDownload(browserPlatform, downloads);

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
          {download ? (() => {
            const [platform, url] = download;
            const item = PLATFORM[platform as keyof typeof PLATFORM];
            return <a className="btn btn-lg" href={url}>
              <i className={`ti ${item.icon}`} /> {t("companionDownload")} {item.label}
            </a>;
          })() : (
            <span className="cp-badge cp-badge-mut">
              <i className="ti ti-info-circle" /> {t("companionNoCompatibleDownload")}
            </span>
          )}
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
