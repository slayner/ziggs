"""
Battleboard (/bb) — lê uma batalha do Albion (via API pública oficial, que o
albionbb também usa) e cruza com a presença do CTA.

A staff passa o link do albionbb OU o número da batalha. O bot busca a batalha na
API oficial (gameinfo.albiononline.com), pega os players das SUAS guilds (/setguild)
e cruza com:
  · quem estava na call do CTA (attendance, mapeado pra IGN via /register);
  · quem registrou funções na planilha (mass-info).

Gera sugestões no canal de late-attend (com botões pra aplicar) + um resumo na
embed do evento:
  · ADICIONAR: estava na batalha mas NÃO na call (jogou de outra call);
  · REMOVER: estava na call, NÃO na batalha e SEM registro (só assistindo).

Obs: a API é pública (sem key); o albionbb em si bloqueia bots, por isso usamos a
fonte oficial. A lista de players da batalha inclui quem teve crédito de kill/
death/fame — um suporte puro sem evento pode não aparecer.
"""
import os
import re

import aiohttp
import discord
from discord.ext import commands
from discord import app_commands, ui
from utils import EMBED_INFO, send_err, send_ok, send_warn
import utils
from dotenv import load_dotenv

from database import (
    is_server_activated, load_economy_config,
    get_event_by_id, get_non_finalized_events, get_event_attendances,
    get_registration, get_registration_by_nick, get_event_function_user_ids,
    add_battle_player, get_battle_players, update_event_meta, add_battle_mvp,
    enlist_member, delete_attendance,
)
from datetime import datetime, timezone, timedelta
from cogs.economy import has_configured_role

load_dotenv()
OWNER_ID = int(os.getenv('OWNER_ID', 0))

# Hosts da API oficial por região (Americas é o padrão da guilda).
DEFAULT_HOST = 'gameinfo.albiononline.com'           # Americas
_HOST_AMS    = 'gameinfo-ams.albiononline.com'        # Europe
_HOST_SGP    = 'gameinfo-sgp.albiononline.com'        # Asia
_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
_PERM_KEYS = ('role_council', 'role_logistic')        # +OWNER/lead via has_configured_role


def _parse_battle_ids(raw: str):
    """Extrai (lista_de_battle_ids:[str], host:str) de links do albionbb ou de números.

    Aceita os 2 formatos que os jogadores usam como guia (albionbb é só frontend):
      · 1 batalha:    https://albionbb.com/battles/1395996677
      · combinadas:   https://albionbb.com/battles/multi?ids=1396034215,1396034039
    Também aceita só o número, ou vários números separados por vírgula/espaço.
    """
    s = (raw or '').strip()
    low = s.lower()
    host = DEFAULT_HOST
    if 'europe' in low or '-ams' in low:
        host = _HOST_AMS
    elif 'asia' in low or '-sgp' in low:
        host = _HOST_SGP
    # Formato combinado: .../multi?ids=a,b,c  (pega o que vier depois de "ids=")
    m = re.search(r'ids=([\d,\s]+)', s)
    if m:
        ids = [n for n in re.split(r'[,\s]+', m.group(1)) if n.isdigit()]
    else:
        # 1 batalha (link ou número) — ou vários números soltos. IDs são longos (>=5 díg).
        ids = re.findall(r'\d{5,}', s)
    # dedup preservando ordem
    seen, out = set(), []
    for n in ids:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out, host


def albionbb_link(ids, host: str = DEFAULT_HOST) -> str:
    """Monta o link do albionbb (single ou multi) — é o que mostramos aos jogadores."""
    ids = [str(i) for i in (ids or []) if str(i).strip()]
    if not ids:
        return ''
    base = 'https://albionbb.com'
    if host == _HOST_AMS:
        base = 'https://europe.albionbb.com'
    elif host == _HOST_SGP:
        base = 'https://east.albionbb.com'
    if len(ids) == 1:
        return f"{base}/battles/{ids[0]}"
    return f"{base}/battles/multi?ids={','.join(ids)}"


def _parse_ts(value):
    """Parse de timestamp (API ou DB) -> datetime aware em UTC, ou None."""
    if not value:
        return None
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        base = s.replace('Z', '').split('+')[0].strip()
        dt = None
        for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S',
                    '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S'):
            try:
                dt = datetime.strptime(base, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_HEALER_NERF_KEYWORDS = {
    'main_naturestaff', '2h_naturestaff', '2h_wildstaff',
    'main_naturestaff_keeper', '2h_naturestaff_hell', '2h_naturestaff_keeper',
    'main_naturestaff_avalon', 'forgebark',
}


def _find_weapon_signature(player_data: dict) -> str | None:
    """Tenta inferir o nome/código da arma usada pelo jogador na batalha."""
    for key in ('weapon', 'weaponName', 'ItemType', 'itemType', 'itemName',
                'ability', 'spell', 'build', 'buildName', 'build_type', 'Build'):
        raw = player_data.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    # Também pode haver nomes dentro da nested equipment dict.
    for value in player_data.values():
        if isinstance(value, dict):
            for sub in value.values():
                if isinstance(sub, str) and sub.strip():
                    return sub.strip()
    return None


def _compute_healing_score(healing: int, weapon_code: str | None) -> float:
    """Aplica nerf para armas de cura específicas na hora de escolher o MVP healer."""
    score = float(healing or 0)
    if not weapon_code:
        return score
    weapon_code_norm = weapon_code.strip().lower()
    if any(keyword in weapon_code_norm for keyword in _HEALER_NERF_KEYWORDS):
        return score * 0.5
    return score


def _extract_players(data: dict, guild_set: set) -> list:
    """Players da batalha que pertencem às guilds configuradas. Defensivo c/ as keys."""
    raw = (data or {}).get('players')
    items = raw.values() if isinstance(raw, dict) else (raw or [])
    out = []
    for p in items:
        if not isinstance(p, dict):
            continue
        name = p.get('Name') or p.get('name')
        if not name:
            continue
        guild = (p.get('GuildName') or p.get('guildName') or '').strip()
        if guild_set and guild.lower() not in guild_set:
            continue
        try:
            kills = int(p.get('kills') or p.get('Kills') or 0)
            deaths = int(p.get('deaths') or p.get('Deaths') or 0)
            # healing: tentativa defensiva em múltiplas keys possíveis da API
            healing = int(
                p.get('healing') or p.get('heals') or p.get('healingDone')
                or p.get('Heals') or p.get('Healing') or 0
            )
        except (TypeError, ValueError):
            kills = deaths = 0
            healing = 0
        weapon_code = _find_weapon_signature(p)
        healing_score = _compute_healing_score(healing, weapon_code)
        out.append({
            'name': name.strip(),
            'guild': guild,
            'kills': kills,
            'deaths': deaths,
            'healing': healing,
            'healing_score': healing_score,
            'weapon_code': weapon_code,
        })
    return out


async def bb_event_autocomplete(interaction: discord.Interaction, current: str):
    """Retorna todos os eventos (finalizados ou não) para que /bb possa ajustar KB sempre."""
    from database import get_event_by_id
    cur = (current or '').strip().lower()
    out = []
    # Busca até 50 eventos recentes (do mais novo pro mais antigo)
    async with database._db() as db:
        db.row_factory = database.aiosqlite.Row
        cursor = await db.execute(
            '''SELECT * FROM cta_events
               ORDER BY id DESC
               LIMIT 50''',
        )
        evs = await cursor.fetchall()
    for r in evs:
        ev = dict(r)
        comp = ev.get('comp')
        label = f"#{ev['id']}" + (f" · {comp}" if comp else "")
        if cur and cur not in str(ev['id']) and cur not in label.lower():
            continue
        out.append(app_commands.Choice(name=label[:100], value=ev['id']))
    return out[:25]


# ==================================================================
# Selects de ação (no relatório do late-attend)
# ==================================================================
class _AddSelect(ui.Select):
    def __init__(self, event_id: int, suggest_add: list):
        self.event_id = event_id
        opts = [discord.SelectOption(label=ign[:100], value=str(uid))
                for ign, uid, *_ in suggest_add if uid]
        super().__init__(placeholder="➕ Adicionar ao evento (estavam na batalha)…",
                         min_values=1, max_values=len(opts), options=opts[:25])

    async def callback(self, interaction: discord.Interaction):
        if not await has_configured_role(interaction.user, *_PERM_KEYS):
            await send_err(interaction, "Sem permissão.")
            return
        added = []
        for v in self.values:
            uid = int(v)
            member = interaction.guild.get_member(uid) if interaction.guild else None
            name = member.display_name if member else str(uid)
            try:
                await enlist_member(self.event_id, uid, name, interaction.user.id, percent=100)
                added.append(uid)
            except Exception as e:
                print(f"✗ /bb add {uid}: {e}")
        await _refresh(interaction.client, self.event_id)
        await send_ok(interaction, "Adicionado(s) ao evento: "
                                   + ", ".join(f"<@{u}>" for u in added))


class _RemoveSelect(ui.Select):
    def __init__(self, event_id: int, suggest_remove: list):
        self.event_id = event_id
        opts = [discord.SelectOption(label=(name or str(uid))[:100], value=str(uid))
                for uid, name in suggest_remove]
        super().__init__(placeholder="➖ Remover do evento (não estavam na batalha)…",
                         min_values=1, max_values=len(opts), options=opts[:25])

    async def callback(self, interaction: discord.Interaction):
        if not await has_configured_role(interaction.user, *_PERM_KEYS):
            await send_err(interaction, "Sem permissão.")
            return
        removed = []
        for v in self.values:
            uid = int(v)
            try:
                if await delete_attendance(self.event_id, uid):
                    removed.append(uid)
            except Exception as e:
                print(f"✗ /bb remove {uid}: {e}")
        await _refresh(interaction.client, self.event_id)
        await interaction.response.send_message(
            "🗑️ Removido(s) do evento: " + ", ".join(f"<@{u}>" for u in removed),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )
        try:
            await utils.schedule_ephemeral_from_ctx(interaction)
        except Exception:
            pass


class BattleSuggestView(ui.View):
    def __init__(self, event_id: int, suggest_add: list, suggest_remove: list):
        super().__init__(timeout=3600)
        addable = [s for s in suggest_add if s[1]]   # só os com Discord (cadastrados)
        if addable:
            self.add_item(_AddSelect(event_id, addable))
        if suggest_remove:
            self.add_item(_RemoveSelect(event_id, suggest_remove))


async def _refresh(bot, event_id: int):
    cta = bot.cogs.get('CTACog')
    if cta:
        try:
            await cta._refresh_event_embed(event_id)
        except Exception as e:
            print(f"✗ /bb refresh embed: {e}")
    splits = bot.cogs.get('SplitsCog')
    if splits:
        try:
            await splits.refresh_splits()
        except Exception as e:
            print(f"✗ /bb refresh splits: {e}")


# ==================================================================
# Cog
# ==================================================================
class BattleboardCog(commands.Cog, name="BattleboardCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        print("✓ Battleboard Cog carregada")

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return True
        if not await is_server_activated(ctx.guild.id):
            await send_err(ctx, "Este servidor não está ativado!")
            return False
        return True

    async def _fetch_json(self, url: str):
        """GET genérico na API oficial (UA de browser). Retorna (data, err)."""
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20), headers={'User-Agent': _UA},
            ) as s:
                async with s.get(url) as r:
                    if r.status != 200:
                        return None, f"HTTP {r.status}"
                    data = await r.json(content_type=None)
            return data, None
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"

    async def _fetch_battle(self, battle_id: str, host: str):
        return await self._fetch_json(f"https://{host}/api/gameinfo/battles/{battle_id}")

    async def _crossref(self, event_id: int):
        """(suggest_add, suggest_remove, unregistered) cruzando batalha × call × planilha."""
        bplayers = await get_battle_players(event_id)
        battle_igns = {p['player'].strip().lower() for p in bplayers}
        attendance = await get_event_attendances(event_id)
        registered = await get_event_function_user_ids(event_id)

        att_igns, att_ign_by_uid = set(), {}
        for uid, name, _pct, _silver in attendance:
            reg = await get_registration(uid)
            ign = (reg.get('nick') if reg else None) or name or str(uid)
            att_igns.add(ign.strip().lower())
            att_ign_by_uid[uid] = ign

        # ADICIONAR: na batalha, fora da call.
        suggest_add, unregistered = [], []
        for p in bplayers:
            if p['player'].strip().lower() in att_igns:
                continue
            reg = await get_registration_by_nick(p['player'])
            if reg:
                suggest_add.append((p['player'], reg['user_id'], p['kills'], p['deaths']))
            else:
                unregistered.append(p['player'])

        # REMOVER: na call, fora da batalha, sem registro na planilha.
        suggest_remove = []
        for uid, name, _pct, _silver in attendance:
            ign = att_ign_by_uid.get(uid, name or '')
            if ign.strip().lower() not in battle_igns and uid not in registered:
                suggest_remove.append((uid, name))
        return suggest_add, suggest_remove, unregistered

    @commands.hybrid_command(
        name="bb",
        description="Lê uma batalha (albionbb/Albion) e cruza com a presença do CTA — lead/council/logistic",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        batalha="Link do albionbb OU o número da batalha",
        evento="CTA (padrão: o mais recente não finalizado)",
    )
    @app_commands.autocomplete(evento=bb_event_autocomplete)
    async def bb(self, ctx: commands.Context, batalha: str, evento: int = None):
        if not await has_configured_role(ctx.author, *_PERM_KEYS):
            await send_err(ctx, "Apenas lead, council ou logistic.")
            return
        await ctx.defer(ephemeral=True)

        # Evento alvo.
        if evento is None:
            nf = await get_non_finalized_events(limit=1)
            if not nf:
                await send_err(ctx, "Não há CTA não finalizado pra associar.")
                return
            event = nf[0]
        else:
            event = await get_event_by_id(evento)
            if not event:
                await send_err(ctx, f"CTA #{evento} não existe.")
                return
        event_id = event['id']

        battle_ids, host = _parse_battle_ids(batalha)
        if not battle_ids:
            await send_err(ctx, "Não achei o número da batalha. Passe o link do albionbb "
                                "(1 ou multi) ou o número.")
            return

        cfg = await load_economy_config()
        guild_set = {g.strip().lower() for g in (cfg.get('guild_ingame_name') or '').split(';') if g.strip()}
        if not guild_set:
            await send_err(ctx, "Defina a(s) guilda(s) em `/setup` → Guilda (jogo) antes.")
            return

        # Busca CADA batalha e ACUMULA os players (add_battle_player soma kills/deaths
        # por player, então batalhas combinadas viram um único conjunto).
        combined, ok_ids, errs = {}, [], []
        for bid in battle_ids:
            data, err = await self._fetch_battle(bid, host)
            if data is None:
                errs.append(f"{bid} ({err})")
                continue
            ok_ids.append(bid)
            for p in _extract_players(data, guild_set):
                key = p['name'].lower()
                if key in combined:
                    combined[key]['kills'] += p['kills']
                    combined[key]['deaths'] += p['deaths']
                    combined[key]['healing'] += p['healing']
                    # Recalcular healing_score com o total acumulado
                    combined[key]['healing_score'] = _compute_healing_score(
                        combined[key]['healing'], combined[key].get('weapon_code')
                    )
                else:
                    combined[key] = dict(p)

        if not ok_ids:
            await send_err(ctx, f"Não consegui ler nenhuma das batalhas ({'; '.join(errs)}).\n"
                                f"Confira o link/número e a região (host: `{host}`).")
            return

        players = list(combined.values())
        if not players:
            tail = f"\n⚠️ Falharam: {'; '.join(errs)}" if errs else ""
            await send_warn(ctx, f"Batalha(s) {', '.join(ok_ids)} lida(s), mas nenhum player das "
                                 f"suas guildas apareceu nelas.{tail}")
            return

        for p in players:
            await add_battle_player(event_id, p['name'], p['guild'], p['kills'], p['deaths'], p.get('healing', 0))

        # Calcula e registra MVPs para esta coleção de batalhas (2 MVPs: DPS e Healer)
        try:
            if players:
                top_dps = max(players, key=lambda x: int(x.get('kills', 0)))
                await add_battle_mvp(event_id, top_dps['name'], 'dps')
                top_healer = max(players, key=lambda x: float(x.get('healing_score', x.get('healing', 0))))
                # Só registra healer se houver cura > 0
                if float(top_healer.get('healing', 0)) > 0:
                    await add_battle_mvp(event_id, top_healer['name'], 'healer')
        except Exception as e:
            print(f"✗ Erro registrando MVPs: {e}")

        # Guarda o link albionbb (combinado) no evento p/ mostrar na embed.
        link = albionbb_link(ok_ids, host)
        try:
            await update_event_meta(event_id, battleboard_url=link)
        except Exception as e:
            print(f"✗ /bb salvar link: {e}")

        # Envia link simples (THREAD + albionbb) no canal de battleboards, sem embed.
        try:
            cfg = await load_economy_config()
            bb_chan = self.bot.get_channel(cfg.get('channel_battleboard'))
            if bb_chan is None:
                try:
                    bb_chan = await self.bot.fetch_channel(cfg.get('channel_battleboard'))
                except (discord.HTTPException, TypeError):
                    bb_chan = None
            # Recupera o evento atualizado (pra pegar event_thread_id / guild_id)
            ev = await get_event_by_id(event_id)
            if bb_chan and ev and link:
                guild_id = ev.get('guild_id') or ctx.guild.id if ctx and ctx.guild else None
                thread_id = ev.get('event_thread_id')
                # Monta o link da thread (canal). Se não tiver thread, não inclui.
                parts = []
                if guild_id and thread_id:
                    parts.append(f"https://discordapp.com/channels/{guild_id}/{thread_id}")
                # AlbionBB link
                parts.append(link)
                if parts:
                    await bb_chan.send(" ".join(parts), allowed_mentions=discord.AllowedMentions.none())
        except Exception as e:
            print(f"✗ /bb postar no channel_battleboard: {e}")

        suggest_add, suggest_remove, unregistered = await self._crossref(event_id)
        await self._post_report(event_id, ok_ids, link, players,
                                suggest_add, suggest_remove, unregistered)
        await _refresh(self.bot, event_id)

        extra = f" (falharam: {', '.join(errs)})" if errs else ""
        await send_ok(ctx, f"{len(ok_ids)} batalha(s) lida(s){extra}: **{len(players)}** "
                           f"player(s) da sua guild.\n🔗 {link}\n"
                           f"Sugestões (➕{len(suggest_add)} / ➖{len(suggest_remove)}) "
                           f"enviadas pro late-attend.")
        print(f"✓ /bb {','.join(ok_ids)} → CTA #{event_id}: {len(players)} players, "
              f"+{len(suggest_add)}/-{len(suggest_remove)}")

    async def _post_report(self, event_id, battle_ids, link, players,
                           suggest_add, suggest_remove, unregistered):
        cfg = await load_economy_config()
        chan = self.bot.get_channel(cfg.get('channel_bombleaderchat'))  # canal de late-attend
        if chan is None:
            try:
                chan = await self.bot.fetch_channel(cfg.get('channel_bombleaderchat'))
            except (discord.HTTPException, TypeError):
                return

        ids_txt = ", ".join(str(b) for b in battle_ids)
        desc = f"Batalha(s) **{ids_txt}** · **{len(players)}** player(s) da sua guild na luta."
        if link:
            desc += f"\n🔗 [Ver no albionbb]({link})"
        embed = discord.Embed(
            title=f"⚔️ Battleboard — CTA #{event_id}",
            description=desc,
            color=EMBED_INFO,
            timestamp=discord.utils.utcnow(),
        )
        if suggest_add:
            lines = []
            for ign, uid, *_ in suggest_add[:20]:
                lines.append(f"➕ `{ign}` " + (f"<@{uid}>" if uid else "*(sem cadastro)*"))
            if len(suggest_add) > 20:
                lines.append(f"… +{len(suggest_add) - 20}")
            embed.add_field(name=f"Sugerir ADICIONAR ({len(suggest_add)}) — na batalha, fora da call",
                            value="\n".join(lines), inline=False)
        if suggest_remove:
            lines = [f"➖ <@{uid}>" for uid, _ in suggest_remove[:20]]
            if len(suggest_remove) > 20:
                lines.append(f"… +{len(suggest_remove) - 20}")
            embed.add_field(name=f"Sugerir REMOVER ({len(suggest_remove)}) — na call, fora da batalha, sem registro",
                            value="\n".join(lines), inline=False)
        if unregistered:
            embed.add_field(
                name=f"Na batalha mas SEM cadastro ({len(unregistered)}) — registre p/ poder adicionar",
                value=", ".join(f"`{n}`" for n in unregistered[:20])
                      + (f" … +{len(unregistered) - 20}" if len(unregistered) > 20 else ""),
                inline=False,
            )
        if not (suggest_add or suggest_remove or unregistered):
            embed.add_field(name="✅ Tudo certo",
                            value="A call do CTA bate com a batalha — sem divergências.", inline=False)

        view = BattleSuggestView(event_id, suggest_add, suggest_remove)
        try:
            await chan.send(embed=embed, view=view if (view.children) else None,
                            allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            print(f"✗ /bb erro postando relatório: {e}")

    # ==============================================================
    # Auto-descoberta de batalhas no callout (best-effort)
    # ==============================================================
    @staticmethod
    def _battle_names_guilds(data):
        """(nomes_lower, guildas_lower) dos players de uma batalha; (None, None) se a
        batalha não trouxe nem players nem guildas (pra forçar buscar a versão cheia)."""
        if not isinstance(data, dict):
            return None, None
        names, guilds = set(), set()
        graw = data.get('guilds')
        if isinstance(graw, dict):
            graw = list(graw.values())
        for g in (graw or []):
            if isinstance(g, dict):
                gn = g.get('Name') or g.get('name')
                if gn:
                    guilds.add(gn.strip().lower())
        praw = data.get('players')
        items = praw.values() if isinstance(praw, dict) else (praw or [])
        for p in items:
            if not isinstance(p, dict):
                continue
            n = p.get('Name') or p.get('name')
            if n:
                names.add(n.strip().lower())
            gn = p.get('GuildName') or p.get('guildName')
            if gn:
                guilds.add(gn.strip().lower())
        if praw is None and not guilds:
            return None, None    # nada útil nesta entrada da lista
        return names, guilds

    async def _battle_is_ours(self, b, bid, host, caller_low, guild_set):
        """True se o CALLER OU alguma das nossas guildas participou da batalha.
        Busca a batalha cheia só se a entrada da lista não trouxe players/guildas."""
        names, guilds = self._battle_names_guilds(b)
        if names is None:
            full, _ = await self._fetch_battle(bid, host)
            names, guilds = self._battle_names_guilds(full)
            names, guilds = (names or set()), (guilds or set())
        if caller_low and caller_low in names:
            return True
        return bool(guild_set and (guilds & guild_set))

    async def discover_event_battles(self, event: dict):
        """Best-effort: ids das batalhas na janela do CTA em que o CALLER OU alguma
        das nossas guildas participou. Pagina a lista de batalhas até passar da janela
        (a lista é decrescente no tempo), em vez de olhar só as 50 mais recentes —
        senão batalhas do meio do CTA, ou em que o caller não pegou crédito, escapavam."""
        host = DEFAULT_HOST
        caller_id = event.get('caller_id')
        reg = await get_registration(caller_id) if caller_id else None
        caller_ign = (reg.get('nick') if reg else None) or event.get('caller_name')
        caller_low = caller_ign.strip().lower() if caller_ign else None

        cfg = await load_economy_config()
        guild_set = {g.strip().lower()
                     for g in (cfg.get('guild_ingame_name') or '').split(';') if g.strip()}
        if not caller_low and not guild_set:
            return [], host

        start = _parse_ts(event.get('started_at'))
        end = _parse_ts(event.get('ended_at')) or datetime.now(timezone.utc)
        if start is None:
            return [], host
        lo, hi = start - timedelta(minutes=5), end + timedelta(minutes=20)

        PAGE = 50
        MAX_PAGES = 10          # teto de segurança (~500 batalhas do dia)
        found, seen = [], set()
        for page in range(MAX_PAGES):
            data, err = await self._fetch_json(
                f"https://{host}/api/gameinfo/battles"
                f"?range=day&limit={PAGE}&offset={page * PAGE}&sort=recent")
            if not isinstance(data, list) or not data:
                if page == 0:
                    print(f"✗ battleboard auto: lista de batalhas falhou ({err})")
                break
            page_oldest = None
            for b in data:
                if not isinstance(b, dict):
                    continue
                bid = b.get('id') or b.get('Id')
                st = _parse_ts(b.get('startTime') or b.get('StartTime') or b.get('endTime'))
                if st is not None:
                    page_oldest = st if page_oldest is None else min(page_oldest, st)
                if bid is None or st is None or not (lo <= st <= hi):
                    continue
                if str(bid) in seen:
                    continue
                if await self._battle_is_ours(b, bid, host, caller_low, guild_set):
                    seen.add(str(bid))
                    found.append(str(bid))
            # Lista decrescente: se a batalha mais antiga desta página já é anterior à
            # janela, as próximas páginas também são → para de paginar.
            if page_oldest is not None and page_oldest < lo:
                break
        return found, host

    async def run_discovery_for_event(self, event: dict):
        """Chamado em background no /callout: descobre batalhas e posta o link albionbb."""
        try:
            cfg = await load_economy_config()
            if not cfg.get('channel_battleboard'):
                return  # battleboard não configurado → não faz nada
            guild_set = {g.strip().lower()
                         for g in (cfg.get('guild_ingame_name') or '').split(';') if g.strip()}
            event_id = event['id']
            ids, host = await self.discover_event_battles(event)
            if not ids:
                print(f"✓ battleboard auto CTA #{event_id}: nenhuma batalha na janela.")
                return
            # Acumula os players das batalhas detectadas (alimenta o cross-ref do /bb).
            for bid in ids:
                data, _ = await self._fetch_battle(bid, host)
                for p in _extract_players(data, guild_set):
                    await add_battle_player(event_id, p['name'], p['guild'], p['kills'], p['deaths'], p.get('healing', 0))
            # Após acumular todos os players das batalhas detectadas, registra MVPs
            try:
                bplayers = await get_battle_players(event_id)
                if bplayers:
                    top_dps = max(bplayers, key=lambda x: int(x.get('kills', 0)))
                    await add_battle_mvp(event_id, top_dps['player'], 'dps')
                    # Healer MVP: prioriza healing_score se existir, senão usa healing
                    top_healer = max(bplayers, key=lambda x: float(x.get('healing_score', x.get('healing', 0))))
                    if float(top_healer.get('healing', 0)) > 0:
                        await add_battle_mvp(event_id, top_healer['player'], 'healer')
            except Exception as e:
                print(f"✗ Erro registrando MVPs auto: {e}")
            link = albionbb_link(ids, host)
            await update_event_meta(event_id, battleboard_url=link)
            await self._post_battleboard(event, ids, link)
            await _refresh(self.bot, event_id)
            print(f"✓ battleboard auto CTA #{event_id}: {len(ids)} batalha(s) → {link}")
        except Exception as e:
            print(f"✗ battleboard auto: {type(e).__name__}: {e}")

    async def _post_battleboard(self, event: dict, ids: list, link: str):
        cfg = await load_economy_config()
        chan = self.bot.get_channel(cfg.get('channel_battleboard'))
        try:
            await chan.send(f"{link}")
        except discord.HTTPException as e:
            print(f"✗ battleboard post: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(BattleboardCog(bot))
