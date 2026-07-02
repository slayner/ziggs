import { BOT_INVITE } from "../api";
import { useT } from "../i18n";

interface Props {
  guildName: string;
  onSwitch: () => void;
}

export default function GuildSetup({ guildName, onSwitch }: Props) {
  const t = useT();
  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-10">
      <h1 className="text-lg font-bold text-zinc-100 mb-1">{t("setupTitle")}</h1>
      <p className="text-sm text-zinc-500 mb-8">
        {t("activeServer")} <strong className="text-zinc-300">{guildName}</strong>
      </p>

      <div className="space-y-4">
        {/* Bot */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-2">
            <i className="ti ti-brand-discord text-indigo-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-zinc-100">{t("discordBot")}</h2>
          </div>
          <p className="text-xs text-zinc-500 mb-4">
            {t("discordBotSetupDesc")}
          </p>
          <a
            href={BOT_INVITE}
            target="_blank"
            rel="noopener noreferrer"
            className="btn btn-discord inline-flex items-center gap-2 text-sm"
          >
            <i className="ti ti-plus" aria-hidden="true" /> {t("inviteBot")}
          </a>
        </div>

        {/* Trocar servidor */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-2">
            <i className="ti ti-switch-horizontal text-zinc-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-zinc-100">{t("switchServer")}</h2>
          </div>
          <p className="text-xs text-zinc-500 mb-4">
            {t("switchServerDescSetup")}
          </p>
          <button className="btn" onClick={onSwitch}>
            <i className="ti ti-refresh" aria-hidden="true" /> {t("switchServer")}
          </button>
        </div>
      </div>
    </div>
  );
}
