import { useT, type TKey } from "../i18n";
import { Panel } from "./Panel";

// URL do instalador Windows (.msi/.exe gerado por `npm run tauri build`).
// null = ainda sem release publicado → botão vira "Em breve" desabilitado.
const DOWNLOAD_URL_WINDOWS: string | null = null;

const FEATURES_NOW: { icon: string; title: TKey; desc: TKey }[] = [
  { icon: "ti-radar-2",         title: "companionFeatScanTitle",    desc: "companionFeatScanDesc" },
  { icon: "ti-world-bolt",      title: "companionFeatDnsTitle",     desc: "companionFeatDnsDesc" },
  { icon: "ti-route",           title: "companionFeatTunnelTitle",  desc: "companionFeatTunnelDesc" },
  { icon: "ti-clipboard-text",  title: "companionFeatLootlogTitle", desc: "companionFeatLootlogDesc" },
  { icon: "ti-layout-bottombar",title: "companionFeatTrayTitle",    desc: "companionFeatTrayDesc" },
];

const FEATURES_FUTURE: { icon: string; title: TKey; desc: TKey }[] = [
  { icon: "ti-coins",        title: "companionFeatPricesTitle",   desc: "companionFeatPricesDesc" },
  { icon: "ti-swords",       title: "companionFeatDmgTitle",      desc: "companionFeatDmgDesc" },
  { icon: "ti-list-details", title: "companionFeatAutoLootTitle", desc: "companionFeatAutoLootDesc" },
];

function FeatureCard({ icon, title, desc, future }: { icon: string; title: TKey; desc: TKey; future?: boolean }) {
  const t = useT();
  return (
    <Panel className={"p-4" + (future ? " opacity-70" : "")}>
      <div className="flex items-center gap-2">
        <i className={`ti ${icon}`} style={{ fontSize: 20, color: "var(--gold)" }} />
        <span className="font-semibold">{t(title)}</span>
        {future && (
          <span className="ml-auto rounded border border-zinc-700 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-zinc-500">
            {t("companionComingSoon")}
          </span>
        )}
      </div>
      <p className="mt-2 text-sm text-zinc-400">{t(desc)}</p>
    </Panel>
  );
}

export default function CompanionPage() {
  const t = useT();
  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-8">
      {/* hero */}
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

      {/* como você ajuda a comunidade */}
      <Panel className="mb-8 p-4">
        <div className="flex items-center gap-2 font-semibold">
          <span style={{ fontSize: 18 }}>🤝</span> {t("companionHelpTitle")}
        </div>
        <p className="mt-2 text-sm text-zinc-400">{t("companionHelpText")}</p>
      </Panel>

      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500">{t("companionFeaturesNow")}</h2>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {FEATURES_NOW.map(f => <FeatureCard key={f.title} {...f} />)}
      </div>

      <h2 className="mb-3 mt-8 text-sm font-semibold uppercase tracking-wide text-zinc-500">{t("companionFeaturesFuture")}</h2>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {FEATURES_FUTURE.map(f => <FeatureCard key={f.title} {...f} future />)}
      </div>
    </div>
  );
}
