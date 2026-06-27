import { useEffect, useState } from "react";
import { api, BOT_INVITE, type DiscordRole, type Permissions, type SiteGuild } from "../api";

interface BotCommand { name: string; description: string; enabled: boolean; }

const PERM_COLS: { key: keyof Permissions; label: string }[] = [
  { key: "events.view",   label: "Ev: Ver"   },
  { key: "events.create", label: "Ev: Criar" },
  { key: "events.manage", label: "Ev: Gerir" },
  { key: "comps.view",    label: "Co: Ver"   },
  { key: "comps.create",  label: "Co: Criar" },
  { key: "comps.manage",  label: "Co: Gerir" },
  { key: "guild.admin",   label: "Admin"     },
];

interface Props {
  guildId: string;
  onSwitch: () => void;
}

export default function GuildConfig({ guildId, onSwitch }: Props) {
  const [guild, setGuild] = useState<(SiteGuild & { settings: Record<string, unknown> }) | null>(null);
  const [albionName, setAlbionName] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [roles, setRoles] = useState<DiscordRole[] | null>(null);
  const [rolesErr, setRolesErr] = useState<string | null>(null);
  const [commands, setCommands] = useState<BotCommand[] | null>(null);

  useEffect(() => {
    api.guildInfo(guildId).then(g => {
      setGuild(g);
      setAlbionName(g.albion_guild_name ?? "");
    });
    api.guildDiscordRoles(guildId)
      .then(setRoles)
      .catch(e => setRolesErr(e.message));
    api.guildCommands(guildId)
      .then(setCommands)
      .catch(() => setCommands([]));
  }, [guildId]);

  async function toggleCommand(name: string, enabled: boolean) {
    setCommands(prev => prev?.map(c => c.name === name ? { ...c, enabled } : c) ?? prev);
    await api.toggleGuildCommand(guildId, name, enabled).catch(() => {
      setCommands(prev => prev?.map(c => c.name === name ? { ...c, enabled: !enabled } : c) ?? prev);
    });
  }

  async function togglePerm(roleId: string, permKey: keyof Permissions, value: boolean) {
    const role = roles?.find(r => r.id === roleId);
    if (!role) return;
    const newPerms: Partial<Permissions> = { ...role.permissions, [permKey]: value };
    setRoles(prev => prev?.map(r => r.id !== roleId ? r : { ...r, permissions: newPerms }) ?? prev);
    await api.updateRolePermissions(guildId, roleId, role.name, newPerms).catch(() => {
      setRoles(prev => prev?.map(r => r.id !== roleId ? r : { ...r, permissions: role.permissions }) ?? prev);
    });
  }

  async function saveAlbion() {
    if (!guild) return;
    setSaving(true);
    try {
      await api.updateGuildSettings(guildId, { albion_guild_name: albionName || null });
      setGuild(prev => prev ? { ...prev, albion_guild_name: albionName || null } : prev);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  }

  if (!guild) return <div className="py-16 text-center text-zinc-500">Carregando…</div>;

  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-10">
      <h1 className="text-lg font-bold text-zinc-100 mb-1">Configurações</h1>
      <p className="text-sm text-zinc-500 mb-8">
        Servidor: <strong className="text-zinc-300">{guild.name}</strong>
      </p>

      <div className="space-y-4">

        {/* Status do bot */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-3">
            <i className="ti ti-brand-discord text-indigo-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-zinc-100">Bot do Discord</h2>
            <span className={`ml-auto text-xs font-medium px-2 py-0.5 rounded-full ${
              guild.bot_present
                ? "bg-emerald-500/15 text-emerald-400"
                : "bg-red-500/15 text-red-400"
            }`}>
              {guild.bot_present ? "Ativo" : "Não encontrado"}
            </span>
          </div>

          {guild.bot_present ? (
            <p className="text-xs text-zinc-500">
              O bot está presente no servidor e sincronizando dados automaticamente.
            </p>
          ) : (
            <>
              <p className="text-xs text-zinc-500 mb-4">
                O bot não foi encontrado neste servidor. Convide-o para ativar a sincronização de eventos, regears e membros.
              </p>
              <a
                href={`${BOT_INVITE}&guild_id=${guildId}`}
                target="_blank"
                rel="noopener noreferrer"
                className="btn btn-discord inline-flex items-center gap-2 text-sm"
              >
                <i className="ti ti-plus" aria-hidden="true" /> Convidar o bot
              </a>
            </>
          )}
        </div>

        {/* Guilda de Albion */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-3">
            <i className="ti ti-sword text-amber-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-zinc-100">Guilda de Albion Online</h2>
          </div>
          <p className="text-xs text-zinc-500 mb-3">
            Nome exato da guilda no jogo. Usado pelo bot para validar membros no <code>/register</code>.
          </p>
          <div className="flex gap-2">
            <input
              value={albionName}
              onChange={e => setAlbionName(e.target.value)}
              placeholder="Nome da guilda no jogo…"
              className="flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-amber-500 placeholder:text-zinc-600"
            />
            <button
              onClick={saveAlbion}
              disabled={saving}
              className="btn shrink-0"
            >
              {saved ? <><i className="ti ti-check" /> Salvo</> : saving ? "Salvando…" : "Salvar"}
            </button>
          </div>
        </div>

        {/* Permissões por cargo */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-3">
            <i className="ti ti-key text-violet-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-zinc-100">Permissões por cargo</h2>
          </div>
          <p className="text-xs text-zinc-500 mb-4">
            Defina o que cada cargo do servidor pode fazer no site. Admins do servidor têm acesso total automaticamente.
          </p>
          {rolesErr && <p style={{ color: "var(--red)", fontSize: 12, marginBottom: 8 }}>{rolesErr}</p>}
          {roles === null && !rolesErr && <p className="text-xs text-zinc-500">Carregando cargos…</p>}
          {roles !== null && roles.length === 0 && <p className="text-xs text-zinc-500">Nenhum cargo encontrado no servidor.</p>}
          {roles !== null && roles.length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: "left", padding: "6px 8px", color: "var(--hint)", fontWeight: 500 }}>Cargo</th>
                    {PERM_COLS.map(col => (
                      <th key={col.key} style={{ padding: "4px 6px", color: "var(--hint)", fontWeight: 500, textAlign: "center", whiteSpace: "nowrap" }}>
                        {col.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {roles.map(role => (
                    <tr key={role.id} style={{ borderTop: "1px solid var(--border)" }}>
                      <td style={{ padding: "6px 8px", color: "var(--text)", whiteSpace: "nowrap" }}>
                        {role.color !== 0 && (
                          <span style={{
                            display: "inline-block", width: 8, height: 8, borderRadius: "50%",
                            background: `#${role.color.toString(16).padStart(6, "0")}`,
                            marginRight: 6, verticalAlign: "middle",
                          }} />
                        )}
                        {role.name}
                      </td>
                      {PERM_COLS.map(col => (
                        <td key={col.key} style={{ padding: "4px 6px", textAlign: "center" }}>
                          <input
                            type="checkbox"
                            checked={!!role.permissions[col.key]}
                            onChange={e => togglePerm(role.id, col.key, e.target.checked)}
                          />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Comandos do bot */}
        {commands !== null && (
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
            <div className="flex items-center gap-3 mb-3">
              <i className="ti ti-terminal text-cyan-400 text-xl" aria-hidden="true" />
              <h2 className="text-sm font-semibold text-zinc-100">Comandos</h2>
            </div>
            <p className="text-xs text-zinc-500 mb-4">
              Ative ou desative cada comando do Ziggs neste servidor.
            </p>
            {commands.length === 0 ? (
              <p className="text-xs text-zinc-500">Nenhum comando registrado.</p>
            ) : (
              <div className="flex flex-col gap-3">
                {commands.map(cmd => (
                  <label key={cmd.name} className="flex items-center gap-3 cursor-pointer">
                    <span className="relative inline-flex h-5 w-9 shrink-0">
                      <input
                        type="checkbox"
                        checked={cmd.enabled}
                        onChange={e => toggleCommand(cmd.name, e.target.checked)}
                        className="peer sr-only"
                      />
                      <span className="
                        absolute inset-0 rounded-full transition-colors
                        bg-zinc-700 peer-checked:bg-cyan-600
                        after:absolute after:left-0.5 after:top-0.5
                        after:h-4 after:w-4 after:rounded-full after:bg-white
                        after:transition-transform peer-checked:after:translate-x-4
                      " />
                    </span>
                    <span>
                      <span className="text-sm text-zinc-100 font-mono">/{cmd.name}</span>
                      <span className="text-xs text-zinc-500 ml-2">{cmd.description}</span>
                    </span>
                  </label>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Trocar servidor */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-3">
            <i className="ti ti-switch-horizontal text-zinc-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-zinc-100">Trocar servidor</h2>
          </div>
          <p className="text-xs text-zinc-500 mb-4">
            Selecione outro servidor Discord para gerenciar.
          </p>
          <button className="btn" onClick={onSwitch}>
            <i className="ti ti-refresh" aria-hidden="true" /> Trocar servidor
          </button>
        </div>

      </div>
    </div>
  );
}
