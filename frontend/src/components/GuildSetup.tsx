import { BOT_INVITE } from "../api";

interface Props {
  guildName: string;
  onSwitch: () => void;
}

export default function GuildSetup({ guildName, onSwitch }: Props) {
  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-10">
      <h1 className="text-lg font-bold text-zinc-100 mb-1">Configuração</h1>
      <p className="text-sm text-zinc-500 mb-8">
        Servidor ativo: <strong className="text-zinc-300">{guildName}</strong>
      </p>

      <div className="space-y-4">
        {/* Bot */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-2">
            <i className="ti ti-brand-discord text-indigo-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-zinc-100">Bot do Discord</h2>
          </div>
          <p className="text-xs text-zinc-500 mb-4">
            Adicione o Ziggs ao seu servidor para sincronizar eventos, regears e membros automaticamente.
          </p>
          <a
            href={BOT_INVITE}
            target="_blank"
            rel="noopener noreferrer"
            className="btn btn-discord inline-flex items-center gap-2 text-sm"
          >
            <i className="ti ti-plus" aria-hidden="true" /> Convidar o bot
          </a>
        </div>

        {/* Trocar servidor */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-2">
            <i className="ti ti-switch-horizontal text-zinc-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-zinc-100">Trocar servidor</h2>
          </div>
          <p className="text-xs text-zinc-500 mb-4">
            Selecione outro servidor Discord para gerenciar com o Ziggs.
          </p>
          <button className="btn" onClick={onSwitch}>
            <i className="ti ti-refresh" aria-hidden="true" /> Trocar servidor
          </button>
        </div>
      </div>
    </div>
  );
}
