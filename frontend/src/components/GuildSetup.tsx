import { useState } from "react";
import { api, BOT_INVITE } from "../api";
import { useT, REGION_LABELS, useLang } from "../i18n";
import { ALBION_REGIONS } from "./GuildConfig";

interface Props {
  guildId: string;
  guildName: string;
  botPresent: boolean;
  hasAlbionName: boolean;
  onSwitch: () => void;
  onComplete: () => void;
}

// Defined OUTSIDE GuildSetup — defining a component inside another causes
// React to treat it as a new type each render, unmounting/remounting the
// subtree and stealing focus from the input on every keystroke.
function Step({ done, n, icon, title, children }: { done: boolean; n: number; icon: string; title: string; children?: React.ReactNode }) {
  return (
    <div className={`rounded-xl border p-5 transition-colors ${done ? "border-emerald-700/40 bg-emerald-950/10" : "border-zinc-800 bg-zinc-900/60"}`}>
      <div className="flex items-center gap-3 mb-2">
        <span className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold ${done ? "bg-emerald-500/20 text-emerald-400" : "bg-zinc-800 text-zinc-400"}`}>
          {done ? "✓" : n}
        </span>
        <i className={`ti ${icon} text-lg ${done ? "text-emerald-400" : "text-zinc-400"}`} aria-hidden="true" />
        <h2 className="text-sm font-semibold text-zinc-100">{title}</h2>
      </div>
      {children}
    </div>
  );
}

export default function GuildSetup({ guildId, guildName, botPresent, hasAlbionName, onSwitch, onComplete }: Props) {
  const t = useT();
  const { lang } = useLang();
  const [albionName, setAlbionName] = useState("");
  const [region, setRegion] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const stepBot = botPresent;
  const stepName = (hasAlbionName || albionName.trim().length > 0) && region.length > 0;
  const allDone = stepBot && stepName;

  async function complete() {
    setSaving(true);
    setError(null);
    try {
      await api.updateGuildSettings(guildId, {
        albion_guild_name: albionName.trim() || null,
        albion_guild_region: region || null,
      });
      onComplete();
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-10">
      <h1 className="text-lg font-bold text-zinc-100 mb-1">{t("setupTitle")}</h1>
      <p className="text-sm text-zinc-500 mb-8">
        {t("activeServer")} <strong className="text-zinc-300">{guildName}</strong>
      </p>

      <div className="space-y-4">
        {/* Step 1: Bot */}
        <Step done={stepBot} n={1} icon="ti-brand-discord" title={t("discordBot")}>
          {botPresent ? (
            <p className="text-xs text-emerald-400 ml-10">{t("botActive")}</p>
          ) : (
            <div className="ml-10">
              <p className="text-xs text-zinc-500 mb-3">{t("discordBotSetupDesc")}</p>
              <a href={BOT_INVITE} target="_blank" rel="noopener noreferrer" className="btn btn-discord inline-flex items-center gap-2 text-sm">
                <i className="ti ti-plus" aria-hidden="true" /> {t("inviteBot")}
              </a>
            </div>
          )}
        </Step>

        {/* Step 2: Albion guild name + region */}
        <Step done={stepName} n={2} icon="ti-sword" title={t("setupAlbionGuild")}>
          <div className="ml-10 space-y-3">
            <p className="text-xs text-zinc-500">{t("albionGuildDesc")}</p>
            {hasAlbionName ? (
              <p className="text-xs text-emerald-400">{t("setupAlreadyConfigured")}</p>
            ) : (
              <>
                <div className="flex gap-2">
                  <input
                    value={albionName}
                    onChange={e => setAlbionName(e.target.value)}
                    placeholder={t("guildNamePlaceholder")}
                    className="flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-amber-500 placeholder:text-zinc-600"
                  />
                  <select
                    value={region}
                    onChange={e => setRegion(e.target.value)}
                    required
                    className="w-36 shrink-0 rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-2 text-sm text-zinc-100 outline-none focus:border-amber-500"
                  >
                    <option value="">{t("albionRegionPlaceholder")}</option>
                    {ALBION_REGIONS.map(r => (
                      <option key={r} value={r}>{REGION_LABELS[lang][r]}</option>
                    ))}
                  </select>
                </div>
                {error && <p className="text-xs text-red-400">{error}</p>}
              </>
            )}
          </div>
        </Step>

        {/* Step 3: Complete */}
        <Step done={false} n={3} icon="ti-check" title={t("setupFinish")}>
          <div className="ml-10">
            {allDone ? (
              <button className="btn btn-primary" onClick={complete} disabled={saving}>
                {saving ? "..." : t("setupFinishBtn")}
              </button>
            ) : (
              <p className="text-xs text-zinc-600">{t("setupFinishPending")}</p>
            )}
          </div>
        </Step>

        {/* Switch server */}
        <div className="pt-2">
          <button onClick={onSwitch} className="text-xs text-zinc-500 hover:text-zinc-300">
            <i className="ti ti-switch-horizontal" aria-hidden="true" /> {t("switchServer")}
          </button>
        </div>
      </div>
    </div>
  );
}
