import { useEffect, useState } from "react";
import { api, BOT_INVITE, type DiscordGuild, type SiteGuild } from "../api";

interface Props {
  onSelect: (guildId: string, name: string, botPresent: boolean) => void;
}

type Item = { id: string; name: string; icon: string | null | undefined; isAdmin?: boolean };

function iconUrl(id: number | string, icon?: string | null) {
  return icon ? `https://cdn.discordapp.com/icons/${id}/${icon}.png?size=64` : null;
}

export default function GuildPicker({ onSelect }: Props) {
  const [items, setItems]   = useState<Item[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState<string | null>(null);
  const [picking, setPicking] = useState<string | null>(null);
  // setupMode: fluxo para adicionar um servidor novo (chama Discord API)
  const [setupMode, setSetupMode] = useState(false);

  useEffect(() => {
    setLoading(true); setError(null); setItems(null);

    if (setupMode) {
      // Caminho de primeiro uso: busca todos os servidores Discord do usuário
      api.myDiscordGuilds()
        .then((gs: DiscordGuild[]) => {
          setItems(
            gs
              .filter(g => g.bot_present)
              .sort((a, b) => a.name.localeCompare(b.name))
              .map(g => ({ id: g.id, name: g.name, icon: g.icon, isAdmin: g.is_admin }))
          );
        })
        .catch(e => setError(e.message))
        .finally(() => setLoading(false));
    } else {
      // Caminho padrão: servidores já cadastrados no site (sem rate limit de usuário)
      api.mySiteGuilds()
        .then((gs: SiteGuild[]) => {
          const bot = gs.filter(g => g.bot_present).sort((a, b) => a.name.localeCompare(b.name));
          setItems(bot.map(g => ({ id: String(g.id), name: g.name, icon: g.icon })));
        })
        .catch(e => setError(e.message))
        .finally(() => setLoading(false));
    }
  }, [setupMode]);

  async function pick(item: Item) {
    setPicking(item.id);
    try {
      let guildId: string;
      let botPresent: boolean;

      if (setupMode && item.isAdmin !== undefined) {
        const res = await api.selectGuild(item.id, item.name, item.icon ?? null, item.isAdmin);
        guildId    = res.guild_id;
        botPresent = res.bot_present;
      } else {
        const res = await api.switchGuild(item.id);
        guildId    = res.guild_id;
        botPresent = res.bot_present;
      }

      onSelect(String(guildId), item.name, botPresent);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPicking(null);
    }
  }

  const isEmpty = !loading && !error && items?.length === 0;

  return (
    <div className="guild-picker">
      <div className="guild-picker-card">
        <i className="ti ti-shield-bolt guild-picker-icon" aria-hidden="true" />
        <h2 className="guild-picker-title">Selecione seu servidor</h2>
        <p className="guild-picker-sub">
          {setupMode
            ? "Servidores Discord com o Ziggs ativo."
            : "Servidores onde o Ziggs está ativo."}
        </p>

        {loading && <p className="hint" style={{ marginTop: 16 }}>Buscando servidores…</p>}
        {error   && <p style={{ color: "var(--red)", marginTop: 16 }}>{error}</p>}
        {isEmpty && !setupMode && (
          <p className="hint" style={{ marginTop: 16 }}>
            Nenhum servidor com o Ziggs encontrado.
          </p>
        )}
        {isEmpty && setupMode && (
          <p className="hint" style={{ marginTop: 16 }}>
            Nenhum servidor Discord com o bot ativo.
          </p>
        )}

        <div className="guild-picker-list">
          {items?.map(item => (
            <button
              key={item.id}
              onClick={() => pick(item)}
              disabled={picking !== null}
              className={`guild-picker-item${picking === item.id ? " picking" : ""}`}
            >
              {iconUrl(item.id, item.icon)
                ? <img src={iconUrl(item.id, item.icon)!} alt="" className="guild-picker-avatar" />
                : <div className="guild-picker-avatar guild-picker-avatar-placeholder">{item.name[0]}</div>
              }
              <span className="guild-picker-name">{item.name}</span>
              {picking === item.id
                ? <i className="ti ti-loader-2 spin" aria-hidden="true" />
                : <i className="ti ti-chevron-right" style={{ color: "var(--hint)" }} aria-hidden="true" />
              }
            </button>
          ))}
        </div>

        <a href={BOT_INVITE} target="_blank" rel="noreferrer" className="guild-picker-invite">
          <i className="ti ti-plus" /> Adicionar Ziggs a um servidor
        </a>

        {!setupMode && (
          <button
            className="guild-picker-invite"
            style={{ background: "none", border: "none", cursor: "pointer", marginTop: 4 }}
            onClick={() => setSetupMode(true)}
          >
            <i className="ti ti-refresh" /> Já adicionou? Atualizar lista
          </button>
        )}
        {setupMode && (
          <button
            className="guild-picker-invite"
            style={{ background: "none", border: "none", cursor: "pointer", marginTop: 4 }}
            onClick={() => setSetupMode(false)}
          >
            <i className="ti ti-arrow-left" /> Voltar
          </button>
        )}
      </div>
    </div>
  );
}
