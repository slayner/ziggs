import { useEffect, useState } from "react";
import { useT, useLang, REGION_LABELS, type TKey } from "../i18n";
import { Panel } from "./Panel";

const DOWNLOAD_URL_WINDOWS = "https://ziggs.xyz/companion/Ziggs-Companion_0.2.0_x64-setup.exe";
const MANIFEST_URL = "/vps-manifest.json";

const ALBION_REGIONS = ["americas", "europe", "asia"] as const;
type AlbionRegion = typeof ALBION_REGIONS[number];

type VpsEntry = {
  id: string;
  label: string;
  country: string;
  endpoint: string;
  server_pubkey: string;
  ping_url: string;
};

type VpsPing = {
  americas: number;
  asia: number;
  europe: number;
};

type VpsRow = {
  vps: VpsEntry;
  pings: VpsPing | null;
  loading: boolean;
  error: boolean;
};

const FEATURES: { icon: string; title: TKey; desc: TKey }[] = [
  { icon: "ti-radar-2",          title: "companionFeatScanTitle",    desc: "companionFeatScanDesc" },
  { icon: "ti-route",            title: "companionFeatTunnelTitle",  desc: "companionFeatTunnelDesc" },
  { icon: "ti-world-bolt",       title: "companionFeatDnsTitle",     desc: "companionFeatDnsDesc" },
  { icon: "ti-swords",           title: "companionFeatDmgTitle",     desc: "companionFeatDmgDesc" },
  { icon: "ti-clipboard-text",   title: "companionFeatLootlogTitle", desc: "companionFeatLootlogDesc" },
  { icon: "ti-coins",            title: "companionFeatPricesTitle",  desc: "companionFeatPricesDesc" },
  { icon: "ti-list-details",     title: "companionFeatAutoLootTitle",desc: "companionFeatAutoLootDesc" },
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
    <Panel className="p-4">
      <div className="flex items-center gap-2">
        <i className={`ti ${icon}`} style={{ fontSize: 20, color: "var(--gold)" }} />
        <span className="font-semibold">{t(title)}</span>
      </div>
      <p className="mt-2 text-sm text-zinc-400">{t(desc)}</p>
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
    <div className="mx-auto w-full max-w-5xl px-4 py-8">
      <div className="mb-8 text-center">
        <i className="ti ti-device-desktop" style={{ fontSize: 44, color: "var(--gold)" }} />
        <h1 className="mt-2 text-2xl font-bold">Ziggs Companion</h1>
        <p className="mx-auto mt-2 max-w-xl text-sm text-zinc-400">{t("companionTagline")}</p>
        <div className="mt-5 flex justify-center">
          {DOWNLOAD_URL_WINDOWS ? (
            <a className="btn" href={DOWNLOAD_URL_WINDOWS}>
              <i className="ti ti-brand-windows" /> {t("companionDownloadWin")}
            </a>
          ) : (
            <button className="btn" disabled style={{ opacity: 0.6, cursor: "default" }}>
              <i className="ti ti-brand-windows" /> {t("companionDownloadWin")} — {t("companionComingSoon").toLowerCase()}
            </button>
          )}
        </div>
        <p className="mt-3 text-xs text-zinc-500">{t("companionRequirements")}</p>
      </div>

      <div className="mb-8">
        <PingMatrix rows={vpsRows} />
      </div>

      <Panel className="mb-8 p-4">
        <div className="flex items-center gap-2 font-semibold">
          <span style={{ fontSize: 18 }}>{t("companionHelpTitle")}</span>
        </div>
        <p className="mt-2 text-sm text-zinc-400">{t("companionHelpText")}</p>
      </Panel>

      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500">{t("companionFeaturesNow")}</h2>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {FEATURES.map(f => <FeatureCard key={f.title} {...f} />)}
      </div>
    </div>
  );
}