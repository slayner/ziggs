import os
import re
import discord
from discord.ext import commands
from discord import app_commands, ui
from dotenv import load_dotenv
import database

from database import (
    load_economy_config, update_economy_config,
    is_server_activated,
    get_user_balance, add_user_money, remove_user_money, transfer_money,
    get_user_energy, has_energy_history,
    get_guild_bank_balance, add_guild_bank, remove_guild_bank,
    get_economy_overview,
    get_leaderboard_page, get_leaderboard_count, get_user_leaderboard_position,
    get_attendance_leaderboard_page, get_attendance_leaderboard_count, get_attendance_rank_for_user,
    get_mvp_leaderboard_page, get_mvp_leaderboard_count, get_mvp_rank_for_player,
    get_kills_leaderboard_page, get_kills_leaderboard_count, get_kills_rank_for_player,
    get_deaths_leaderboard_page, get_deaths_leaderboard_count, get_deaths_rank_for_player,
    recompute_trial_discount, get_open_trial_event_ids,
    get_role_gates, add_role_gate, remove_role_gate,
    load_node_calendar, load_utc_clock,
    get_node_defs, add_node_def, remove_node_def,
)
from utils import (
    parse_silver, format_silver,
    make_embed, ok_embed, err_embed, warn_embed, info_embed,
    send_ok, send_err, send_warn, send_info,
)
import utils

LEADERBOARD_PAGE_SIZE = 10
MEDAL_BY_RANK = {1: "🥇", 2: "🥈", 3: "🥉"}

# Palavras-chave que representam "todo o valor disponível" no lugar de um número.
_ALL_KEYWORDS = ('all', 'tudo')


def _is_all_keyword(amount) -> bool:
    """True se `amount` for a palavra mágica `all`/`tudo` (= todo o saldo disponível)."""
    return isinstance(amount, str) and amount.strip().lower() in _ALL_KEYWORDS

load_dotenv()
OWNER_ID = int(os.getenv('OWNER_ID', 0))


async def has_configured_role(member: discord.Member, *role_keys: str) -> bool:
    """
    Verifica se `member` possui pelo menos um dos cargos cujas chaves são passadas.
    Chaves válidas: role_council, role_caller, role_member, role_content_creator,
    role_logistic, role_officer.
    """
    if member is None or not hasattr(member, 'roles'):
        return False
    # OWNER sempre pode. 'lead' tem acesso livre a TODOS os comandos com permissão.
    if member.id == OWNER_ID:
        return True
    cfg = await load_economy_config()
    keys = set(role_keys) | {'role_lead'}
    target_ids = {cfg.get(k) for k in keys if cfg.get(k)}
    if not target_ids:
        return False
    return any(r.id in target_ids for r in member.roles)


# Mapeamento campo → label legível para exibição
ROLE_FIELDS = [
    ('role_council',         'Council'),
    ('role_caller',          'Caller'),
    ('role_member',          'Membro'),
    ('role_content_creator', 'Content Creator'),
    ('role_logistic',        'Logistic'),
    ('role_trial',           'Trial'),
    ('role_mentor',          'Mentor'),
    ('role_officer',         'Officer'),
    ('role_lead',            'Lead'),
    ('role_offcd',           'Off-CD'),
    ('role_bomb',            'Bomb'),
]

CHANNEL_FIELDS = [
    ('voice_cta',              'Voice — Call CTA'),
    ('voice_waitingroom',      'Voice — Waiting Room'),
    ('voice_temp_mother',      'Voice — Sala Mãe (temporárias)'),
    ('channel_events',         'Canal de Eventos'),
    ('channel_logger',         'Canal de Logger'),
    ('channel_economylogs',    'Canal de Economy Logs'),
    ('channel_zergregear',     'Canal de Zerg Regear'),
    ('channel_bombregear',     'Canal de Bomb Regear'),
    ('channel_massinfo',       'Canal de Mass-Info'),
    ('channel_bombleaderchat', 'Canal de Late Attend'),
    ('channel_tabsell',        'Canal de Tab Sell'),
    ('channel_battleboard',    'Canal de Battleboard'),
]


# ======================================================================
# Painel /setup interativo (owner-only). Substitui o /setup de ~22 args e
# ABSORVE todos os setters de config: cargos, canais, percentuais, guilda,
# planilha, mentoria, energia, teamspeak e gates de função (request 1+2).
# ======================================================================
_CT_TEXT  = [discord.ChannelType.text, discord.ChannelType.news]
_CT_VOICE = [discord.ChannelType.voice]
_CT_FORUM = [discord.ChannelType.forum]

_PANEL_ROLES = ROLE_FIELDS                      # [(key, label)]
_PANEL_CHANNELS = [                             # (key, label, channel_types)
    ('voice_cta',              'Voice — Call CTA',     _CT_VOICE),
    ('voice_waitingroom',      'Voice — Waiting Room', _CT_VOICE),
    ('voice_temp_mother',      'Voice — Sala Mãe',     _CT_VOICE),
    ('channel_events',         'Eventos',              _CT_TEXT),
    ('channel_logger',         'Logger',               _CT_TEXT),
    ('channel_economylogs',    'Economy Logs',         _CT_TEXT),
    ('channel_zergregear',     'Zerg Regear',          _CT_TEXT),
    ('channel_bombregear',     'Bomb Regear',          _CT_TEXT),
    ('channel_massinfo',       'Mass-Info',            _CT_TEXT),
    ('channel_bombleaderchat', 'Late Attend',          _CT_TEXT),
    ('channel_tabsell',        'Tab Sell',             _CT_TEXT),
    ('channel_battleboard',    'Battleboard',          _CT_TEXT),
]
_PANEL_PERCENTS = [                             # (key, label)
    ('guild_tax_percent',  'Guild Tax'),
    ('node_scout_percent', 'Node Scout'),
    ('trial_percent',      'Desconto Trial'),
    ('logger_percent',     'Loggers'),
]
_SECTIONS = [                                   # (value, label, descrição)
    ('roles',     '👥 Cargos',          'Definir os cargos do servidor'),
    ('channels',  '📺 Canais',          'Definir os canais (texto/voz)'),
    ('percents',  '％ Percentuais',     'Guild tax, node scout, trial, loggers'),
    ('guild',     '🏷️ Guilda (jogo)',   'Nome(s) da guilda p/ o loot-log'),
    ('sheets',    '📄 Planilha',        'Conectar a planilha (Sheets)'),
    ('mentoria',  '🎓 Mentoria',        'Fórum onde cada membro ganha um post'),
    ('recrutamento', '📩 Recrutamento',  'Canal + cargos e publica o painel'),
    ('nodetypes', '🌿 Nodes — tipos & peso', 'Add/remove tipos de node e o peso do scout'),
    ('nodes',     '🌿 Calendário Nodes', 'Posta o calendário de nodes num canal'),
    ('utc',       '🕒 Relógio UTC',      'Categoria que mostra a hora UTC'),
    ('energy',    '⚡ Energia',          'Canal de alertas e limite'),
    ('teamspeak', '🎧 TeamSpeak',       'Endereço e senha do TS'),
    ('gates',     '🔒 Gates de função', 'Restringir funções da planilha por cargo'),
]

_SHEETS_TUTORIAL = (
    "📄 **Configurar a planilha deste servidor (Sheets)**\n"
    "Cada servidor usa a **própria** planilha — siga isto **uma vez**:\n\n"
    "**1.** Abra sua planilha no Google Sheets (com as páginas `COMPS`, os modelos "
    "de cada comp e as páginas de roles).\n"
    "**2.** Na planilha: **Extensões → Apps Script**.\n"
    "**3.** Apague o conteúdo e **cole o arquivo `sheets_appscript.gs`** (anexo nesta "
    "mensagem). No topo dele dá pra ajustar `COMPS_HEADER_ROWS` e, se quiser senha, "
    "`var SECRET = 'suaSenha';`.\n"
    "**4.** Salve (💾) → **Implantar → Nova implantação** → engrenagem → **App da Web**:\n"
    "   • *Executar como:* **Eu**\n"
    "   • *Quem tem acesso:* **Qualquer pessoa**\n"
    "**5.** Autorize e **copie a URL que termina em `/exec`**.\n"
    "**6.** Volte ao painel `/setup` → Planilha → **Definir URL/secret**.\n\n"
    "ℹ️ O `secret` é **opcional** — só preencha se você setou `var SECRET` no script.\n"
    "🔁 Se editar o `.gs` depois, **republique** (Implantar → Gerenciar implantações → "
    "editar → Nova versão)."
)


async def _build_setup_embed(guild) -> discord.Embed:
    """Embed-resumo da configuração do servidor (também é a 'home' do painel)."""
    cfg = await load_economy_config()

    def _role_line(key, label):
        rid = cfg.get(key)
        if not rid:
            return f"❌ {label}"
        role = guild.get_role(rid) if guild else None
        return f"✅ {label} — {role.mention}" if role else f"⚠️ {label} — `id {rid}`"

    def _chan_line(key, label):
        cid = cfg.get(key)
        if not cid:
            return f"❌ {label}"
        ch = guild.get_channel(cid) if guild else None
        return f"✅ {label} — {ch.mention}" if ch else f"⚠️ {label} — `id {cid}`"

    embed = make_embed('info', title='Configuração do servidor')
    embed.add_field(name='Cargos',
                    value='\n'.join(_role_line(k, l) for k, l in _PANEL_ROLES), inline=False)
    embed.add_field(name='Canais',
                    value='\n'.join(_chan_line(k, l) for k, l, _ in _PANEL_CHANNELS), inline=False)
    tax    = cfg.get('guild_tax_percent')  or 0
    scout  = cfg.get('node_scout_percent') or 0
    trial  = cfg.get('trial_percent')      or 0
    logger = cfg.get('logger_percent')     or 0
    embed.add_field(name='Percentuais',
                    value=f"Guild Tax {tax}% · Node Scout {scout}% · Trial {trial}% · Loggers {logger}%",
                    inline=False)
    embed.add_field(name='Guilda (jogo)',
                    value=(cfg.get('guild_ingame_name') or '❌ não definida'), inline=False)
    wh  = (cfg.get('sheets_webhook_url') or '').strip()
    sec = (cfg.get('sheets_secret') or '').strip()
    embed.add_field(name='Planilha',
                    value=(f"✅ conectada (…{wh[-10:]}) · secret: {'sim' if sec else 'não'}"
                           if wh else '❌ não definida'),
                    inline=False)
    forum_id = cfg.get('mentoria_forum_id')
    ealert   = cfg.get('channel_energyalerts')
    ethr     = cfg.get('energy_alert_threshold')
    ts       = (cfg.get('teamspeak_address') or '').strip()
    embed.add_field(
        name='Mentoria · Energia · TeamSpeak',
        value=(f"Mentoria (fórum): {'✅' if forum_id else '❌'}\n"
               f"Energia: alertas {'✅' if ealert else '❌'} · limite "
               f"{ethr if ethr is not None else '—'}\n"
               f"TeamSpeak: {'✅' if ts else '❌'}"),
        inline=False)

    # Recrutamento (canal + cargos), calendário de nodes e relógio UTC vivem em
    # tabelas/colunas próprias — também entram no resumo.
    rec_cid   = cfg.get('channel_recruitment')
    rec_roles = [x for x in str(cfg.get('recruiter_roles') or '').split(',') if x.strip().isdigit()]
    rec_ch    = guild.get_channel(rec_cid) if (guild and rec_cid) else None
    rec_line  = (f"✅ {rec_ch.mention} · {len(rec_roles)} cargo(s)" if rec_ch
                 else ('⚠️ `canal sumiu`' if rec_cid else '❌'))
    node_cid, _ = await load_node_calendar()
    node_ch   = guild.get_channel(node_cid) if (guild and node_cid) else None
    n_types   = len(await get_node_defs())
    node_line = ((f"✅ {node_ch.mention}" if node_ch
                  else ('⚠️ `canal sumiu`' if node_cid else '❌'))
                 + f" · {n_types} tipo(s)")
    utc_cid, utc_base = await load_utc_clock()
    utc_cat   = guild.get_channel(utc_cid) if (guild and utc_cid) else None
    utc_line  = (f"✅ {utc_cat.name}" if utc_cat
                 else ('⚠️ `categoria sumiu`' if utc_cid else '❌'))
    embed.add_field(
        name='Recrutamento · Nodes · Relógio',
        value=(f"Recrutamento: {rec_line}\n"
               f"Calendário de nodes: {node_line}\n"
               f"Relógio UTC: {utc_line}"),
        inline=False)
    return embed


def _gates_embed(guild, gates: dict) -> discord.Embed:
    if not gates:
        return info_embed(
            'Nenhuma função restrita — toda função fica aberta a todos.\n'
            'Escolha uma **comp** para criar restrições.',
            title='Gates de função')
    lines = []
    for name, ids in sorted(gates.items()):
        mentions = ', '.join(
            (guild.get_role(i).mention if (guild and guild.get_role(i)) else f'`{i}`')
            for i in ids)
        lines.append(f"🔒 **{name}** → {mentions}")
    lines = lines[:30]
    # Poucas: 1 coluna (descrição). Muitas: 2 colunas inline pra economizar altura.
    if len(lines) <= 6:
        return info_embed('\n'.join(lines), title='Gates de função')
    embed = info_embed(title='Gates de função')
    per = (len(lines) + 1) // 2          # 2 colunas, arredonda pra cima
    for i in range(0, len(lines), per):
        embed.add_field(name='​', value='\n'.join(lines[i:i + per]), inline=True)
    return embed


async def _recompute_trials(interaction, percent):
    """Side-effect do trial_percent (igual ao antigo /settrialperc): recalcula os
    CTAs abertos com trial e reedita as embeds."""
    try:
        open_ids = await get_open_trial_event_ids()
        for eid in open_ids:
            try:
                await recompute_trial_discount(eid, percent)
            except Exception as e:
                print(f"✗ trial recompute #{eid}: {e}")
        cta_cog = interaction.client.cogs.get('CTACog')
        if cta_cog and open_ids:
            for eid in open_ids:
                try:
                    await cta_cog._refresh_event_embed(eid)
                except Exception as e:
                    print(f"✗ refresh embed #{eid} pós-trial: {e}")
    except Exception as e:
        print(f"✗ _recompute_trials: {e}")


async def _maybe_refresh(interaction, key):
    """Trocar o canal do mass-info / late-attend recria a mensagem fixada."""
    try:
        if key == 'channel_massinfo':
            cog = interaction.client.cogs.get('MassInfoCog')
            if cog:
                await cog.refresh_massinfo()
        elif key == 'channel_bombleaderchat':
            cog = interaction.client.cogs.get('SplitsCog')
            if cog:
                await cog.refresh_splits()
    except Exception as e:
        print(f"✗ refresh pós-setup ({key}): {e}")


async def _show_gates_root(interaction, view):
    """Raiz da seção de gates: select de comps + lista dos gates atuais."""
    if not interaction.response.is_done():
        await interaction.response.defer()
    try:
        import sheets as _sheets
        comps = await _sheets.list_comps()
    except Exception:
        comps = []
    gates = await get_role_gates()
    view.clear_items()
    if comps:
        view.add_item(_GateCompSelect(comps[:25]))
    view.add_item(_BackButton())
    await interaction.edit_original_response(embed=_gates_embed(view.guild, gates), view=view)


class _BackButton(ui.Button):
    def __init__(self):
        super().__init__(label='‹ Voltar', style=discord.ButtonStyle.secondary, row=4)

    async def callback(self, interaction):
        await self.view.go_home(interaction)


class _SectionSelect(ui.Select):
    def __init__(self):
        opts = [discord.SelectOption(label=lbl[:100], value=val, description=desc[:100])
                for val, lbl, desc in _SECTIONS]
        super().__init__(placeholder='Escolha o que configurar…', options=opts)

    async def callback(self, interaction):
        await self.view.open_section(interaction, self.values[0])


class _RoleFieldSelect(ui.Select):
    def __init__(self):
        opts = [discord.SelectOption(label=lbl[:100], value=key) for key, lbl in _PANEL_ROLES]
        super().__init__(placeholder='Cargo a definir…', options=opts)

    async def callback(self, interaction):
        key = self.values[0]
        label = dict(_PANEL_ROLES)[key]
        await self.view.show_items(
            interaction, [_RoleValueSelect(key, label)], f'Selecione o cargo para **{label}**:')


class _RoleValueSelect(ui.RoleSelect):
    def __init__(self, key, label):
        super().__init__(placeholder=f'Cargo para {label}…'[:100], min_values=1, max_values=1)
        self.key = key

    async def callback(self, interaction):
        await update_economy_config({self.key: self.values[0].id})
        await self.view.go_home(interaction)


class _ChannelFieldSelect(ui.Select):
    def __init__(self):
        opts = [discord.SelectOption(label=lbl[:100], value=key) for key, lbl, _ in _PANEL_CHANNELS]
        super().__init__(placeholder='Canal a definir…', options=opts)

    async def callback(self, interaction):
        key = self.values[0]
        label, types = next((l, t) for k, l, t in _PANEL_CHANNELS if k == key)
        await self.view.show_items(
            interaction, [_ChannelValueSelect(key, label, types)],
            f'Selecione o canal para **{label}**:')


class _ChannelValueSelect(ui.ChannelSelect):
    def __init__(self, key, label, types):
        super().__init__(placeholder=f'Canal para {label}…'[:100],
                         channel_types=types, min_values=1, max_values=1)
        self.key = key

    async def callback(self, interaction):
        updates = {self.key: self.values[0].id}
        if self.key == 'channel_massinfo':
            updates['massinfo_message_id'] = None
        if self.key == 'channel_bombleaderchat':
            updates['splits_message_id'] = None
        await update_economy_config(updates)
        await self.view.go_home(interaction)
        await _maybe_refresh(interaction, self.key)


class _PercentFieldSelect(ui.Select):
    def __init__(self):
        opts = [discord.SelectOption(label=lbl[:100], value=key) for key, lbl in _PANEL_PERCENTS]
        super().__init__(placeholder='Percentual a definir…', options=opts)

    async def callback(self, interaction):
        key = self.values[0]
        label = dict(_PANEL_PERCENTS)[key]
        await interaction.response.send_modal(_NumberModal(self.view, key, label, 0, 100, True))


class _NumberModal(ui.Modal):
    def __init__(self, view, key, label, lo, hi, is_percent):
        super().__init__(title=f'Definir {label}'[:45])
        self.view_ref, self.key, self.lo, self.hi, self.is_percent = view, key, lo, hi, is_percent
        self.valor = ui.TextInput(
            label=(f'{label} (0–100)' if is_percent else label)[:45],
            required=True, max_length=12)
        self.add_item(self.valor)

    async def on_submit(self, interaction):
        try:
            n = int((self.valor.value or '').strip())
        except ValueError:
            await send_err(interaction, "Informe um número inteiro.")
            return
        if self.is_percent and not (self.lo <= n <= self.hi):
            await send_err(interaction, f"Use um valor entre {self.lo} e {self.hi}.")
            return
        await update_economy_config({self.key: n})
        if self.key == 'trial_percent':
            await _recompute_trials(interaction, n)
        await self.view_ref.go_home(interaction)


class _GuildModal(ui.Modal, title='Guilda(s) do jogo'):
    def __init__(self, view):
        super().__init__()
        self.view_ref = view
        self.nomes = ui.TextInput(
            label='Nome(s) — separe várias com ;', required=True, max_length=200,
            placeholder='MOTORHOME;OUTRA')
        self.add_item(self.nomes)

    async def on_submit(self, interaction):
        seen, names = set(), []
        for n in (self.nomes.value or '').split(';'):
            n = n.strip()
            if n and n.lower() not in seen:
                seen.add(n.lower())
                names.append(n)
        if not names:
            await send_err(interaction, "Informe ao menos uma guilda.")
            return
        await update_economy_config({'guild_ingame_name': ';'.join(names)})
        await self.view_ref.go_home(interaction)


class _TeamspeakModal(ui.Modal, title='TeamSpeak'):
    def __init__(self, view):
        super().__init__()
        self.view_ref = view
        self.addr = ui.TextInput(label='Endereço', required=True, max_length=120)
        self.pwd = ui.TextInput(label='Senha (opcional)', required=False, max_length=120)
        self.add_item(self.addr)
        self.add_item(self.pwd)

    async def on_submit(self, interaction):
        await update_economy_config({
            'teamspeak_address': (self.addr.value or '').strip(),
            'teamspeak_password': (self.pwd.value or '').strip(),
        })
        await self.view_ref.go_home(interaction)


class _EnergyFieldSelect(ui.Select):
    def __init__(self):
        opts = [discord.SelectOption(label='Canal de Alertas', value='channel_energyalerts'),
                discord.SelectOption(label='Limite (threshold)', value='energy_alert_threshold')]
        super().__init__(placeholder='Energia — definir…', options=opts)

    async def callback(self, interaction):
        if self.values[0] == 'channel_energyalerts':
            await self.view.show_items(
                interaction,
                [_ChannelValueSelect('channel_energyalerts', 'Alertas de Energia', _CT_TEXT)],
                'Canal dos alertas de energia:')
        else:
            await interaction.response.send_modal(
                _NumberModal(self.view, 'energy_alert_threshold', 'Limite de energia', 0, 0, False))


class _MentoriaSelect(ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder='Fórum da mentoria…',
                         channel_types=_CT_FORUM, min_values=1, max_values=1)

    async def callback(self, interaction):
        await update_economy_config({'mentoria_forum_id': self.values[0].id})
        await self.view.go_home(interaction)


# ---- Recrutamento: canal + cargos + publica o painel (tudo num passo) ----
class _RecruitChannelSelect(ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder='Canal do painel de recrutamento…',
                         channel_types=_CT_TEXT, min_values=1, max_values=1)

    async def callback(self, interaction):
        self.view._recruit_channel_id = self.values[0].id
        await self.view.show_recruit(interaction)


class _RecruitRolesSelect(ui.RoleSelect):
    def __init__(self):
        super().__init__(placeholder='Cargos de recrutador (pode escolher vários)…',
                         min_values=1, max_values=25)

    async def callback(self, interaction):
        self.view._recruit_role_ids = [r.id for r in self.values]
        await self.view.show_recruit(interaction)


class _RecruitTempRoleSelect(ui.RoleSelect):
    """Cargo TEMPORÁRIO dado ao candidato ao abrir o ticket (opcional)."""
    def __init__(self):
        super().__init__(placeholder='Cargo temporário do candidato (opcional)…',
                         min_values=0, max_values=1)

    async def callback(self, interaction):
        self.view._recruit_temprole_id = self.values[0].id if self.values else None
        await self.view.show_recruit(interaction)


class _RecruitPublishButton(ui.Button):
    def __init__(self):
        super().__init__(label='📩 Salvar e publicar painel', style=discord.ButtonStyle.success)

    async def callback(self, interaction):
        v = self.view
        if not v._recruit_channel_id or not v._recruit_role_ids:
            await send_err(interaction, "Escolha o **canal** e ao menos **um cargo** "
                                        "antes de publicar.")
            return
        await interaction.response.defer()
        await update_economy_config({
            'channel_recruitment': v._recruit_channel_id,
            'recruiter_roles': ",".join(str(i) for i in v._recruit_role_ids),
            'role_recruitment': v._recruit_temprole_id,
        })
        channel = v.guild.get_channel(v._recruit_channel_id)
        cog = interaction.client.cogs.get('RecruitmentCog')
        ok = bool(cog and channel and await cog.publish_panel(channel))
        await v.go_home(interaction)
        note = ("✅ Recrutamento configurado e painel publicado." if ok else
                "⚠️ Config salva, mas não consegui publicar o painel (permissões do bot no canal?).")
        try:
            await interaction.followup.send(note, ephemeral=True)
            try:
                await utils.schedule_ephemeral_from_ctx(interaction)
            except Exception:
                pass
        except discord.HTTPException:
            pass


# ---- Nodes: posta o calendário num canal escolhido ----
class _NodeChannelSelect(ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder='Canal do calendário de nodes…',
                         channel_types=_CT_TEXT, min_values=1, max_values=1)

    async def callback(self, interaction):
        await interaction.response.defer()
        channel = self.view.guild.get_channel(self.values[0].id)
        cog = interaction.client.cogs.get('NodeCalendar')
        ok = bool(cog and channel and await cog.publish_calendar(channel))
        await self.view.go_home(interaction)
        try:
            await interaction.followup.send(
                "✅ Calendário de nodes publicado." if ok else
                "⚠️ Não consegui publicar o calendário (permissões do bot no canal?).",
                ephemeral=True)
            try:
                await utils.schedule_ephemeral_from_ctx(interaction)
            except Exception:
                pass
        except discord.HTTPException:
            pass


# ---- Nodes (tipos): add/remove com nome · emoji · peso (peso = % do scout) ----
def _node_scout_pct_text(weight, scout_pct) -> str:
    """% sutil do scout p/ um node (peso × node_scout%). Lazy-import p/ evitar
    import circular com cogs.nodes (que importa desta cog)."""
    from cogs.nodes import node_scout_share_pct, _fmt_pct
    return _fmt_pct(node_scout_share_pct(weight, scout_pct))


# Emoji custom do Discord (<:nome:id> / <a:nome:id>) ou faixas Unicode de emoji.
_CUSTOM_EMOJI_RE = re.compile(r'^<a?:[A-Za-z0-9_]+:\d+>$')
_EMOJI_CHAR_RE = re.compile(
    "[\U0001F000-\U0001FAFF"   # pictográficos, emoji, símbolos suplementares
    "\U00002600-\U000027BF"    # símbolos diversos + dingbats (⛏ ✅ ▶ …)
    "\U00002B00-\U00002BFF"    # setas/símbolos diversos (⬛ ⭐ …)
    "\U0001F1E6-\U0001F1FF"    # indicadores regionais (bandeiras)
    "\U00002190-\U000021FF"    # setas
    "\U00002300-\U000023FF"    # técnicos diversos (⌚ ⏏ …)
    "\U000025A0-\U000025FF"    # formas geométricas (▪ ◼ …)
    "\U0000FE00-\U0000FE0F"    # seletores de variação
    "\U00002000-\U0000206F]"   # pontuação geral (‼ ⁉ + ZWJ de sequências)
)


def _is_emoji_input(s: str) -> bool:
    """True se `s` é UM emoji válido (Unicode ou custom do Discord). Rejeita texto
    comum (qualquer letra/dígito ASCII) — assim 'xx'/'couro' não vira emoji."""
    s = (s or '').strip()
    if not s:
        return True                       # vazio = opcional, ok
    if _CUSTOM_EMOJI_RE.fullmatch(s):
        return True
    if re.search(r'[A-Za-z0-9]', s):      # tem texto ASCII → não é emoji
        return False
    return bool(_EMOJI_CHAR_RE.search(s))


class _NodeAddButton(ui.Button):
    def __init__(self):
        super().__init__(label='➕ Adicionar / editar node',
                         style=discord.ButtonStyle.success, row=0)

    async def callback(self, interaction):
        await interaction.response.send_modal(_NodeDefModal(self.view))


class _NodeDefModal(ui.Modal, title='Node — nome · emoji · peso'):
    def __init__(self, view):
        super().__init__()
        self.view_ref = view
        self.nome = ui.TextInput(label='Nome do node', required=True, max_length=80,
                                 placeholder='ex.: Couro 8.4')
        self.emoji = ui.TextInput(label='Emoji (opcional)', required=False, max_length=40,
                                  placeholder='ex.: 🐂')
        self.peso = ui.TextInput(label='Peso do scout (0 a 1)', required=False, max_length=10,
                                 placeholder='1 = teto cheio · 0.2 = 1/5 do teto · vazio = 1')
        self.add_item(self.nome)
        self.add_item(self.emoji)
        self.add_item(self.peso)

    async def on_submit(self, interaction):
        name = (self.nome.value or '').strip()
        if not name:
            await send_err(interaction, "Informe o nome do node.")
            return
        raw = (self.peso.value or '').strip().replace(',', '.')
        try:
            weight = float(raw) if raw else 1.0
        except ValueError:
            await send_err(interaction, "Peso inválido — use um número (ex.: 1, 0.5, 0.2).")
            return
        weight = max(0.0, weight)
        emoji = (self.emoji.value or '').strip()
        if not _is_emoji_input(emoji):
            await send_err(interaction, "Emoji inválido — cole **um** emoji (ex.: 🐂) "
                                        "ou deixe o campo em branco.")
            return
        action = await add_node_def(name, emoji, weight)
        await self.view_ref.show_nodetypes(interaction)          # refresca a lista
        verb = "adicionado" if action == 'added' else "atualizado"
        try:
            await interaction.followup.send(f"✅ Node **{name}** {verb}.", ephemeral=True)
            try:
                await utils.schedule_ephemeral_from_ctx(interaction)
            except Exception:
                pass
        except discord.HTTPException:
            pass


class _NodeDefRemoveSelect(ui.Select):
    def __init__(self, node_defs):
        opts = []
        for d in node_defs[:25]:
            try:
                opts.append(discord.SelectOption(
                    label=d['name'][:100], value=d['name'][:100], emoji=(d.get('emoji') or None)))
            except Exception:                                    # emoji custom inválido
                opts.append(discord.SelectOption(label=d['name'][:100], value=d['name'][:100]))
        super().__init__(placeholder='🗑️ Remover um node…',
                         min_values=1, max_values=1, options=opts, row=1)

    async def callback(self, interaction):
        name = self.values[0]
        await remove_node_def(name)
        await self.view.show_nodetypes(interaction)
        try:
            await interaction.followup.send(f"🗑️ Node **{name}** removido.", ephemeral=True)
            try:
                await utils.schedule_ephemeral_from_ctx(interaction)
            except Exception:
                pass
        except discord.HTTPException:
            pass


# ---- Relógio UTC: escolhe a CATEGORIA cujo nome mostra a hora ----
class _UtcCategorySelect(ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder='Categoria do relógio UTC…',
                         channel_types=[discord.ChannelType.category],
                         min_values=1, max_values=1)

    async def callback(self, interaction):
        await interaction.response.defer()
        category = self.view.guild.get_channel(self.values[0].id)
        cog = interaction.client.cogs.get('Clock')
        ok = bool(cog and category and await cog.publish_utc(category))
        await self.view.go_home(interaction)
        try:
            await interaction.followup.send(
                "✅ Relógio UTC configurado — a categoria atualiza a cada 10 min." if ok else
                "⚠️ Não consegui configurar (permissões do bot p/ renomear a categoria?).",
                ephemeral=True)
        except discord.HTTPException:
            pass


class _SheetsTutorialButton(ui.Button):
    def __init__(self):
        super().__init__(label='📄 Tutorial + baixar .gs', style=discord.ButtonStyle.secondary)

    async def callback(self, interaction):
        gs_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'sheets_appscript.gs')
        if os.path.isfile(gs_path):
            await interaction.response.send_message(
                _SHEETS_TUTORIAL, file=discord.File(gs_path, filename='sheets_appscript.gs'),
                ephemeral=True)
            try:
                await utils.schedule_ephemeral_from_ctx(interaction)
            except Exception:
                pass
        else:
            await interaction.response.send_message(
                _SHEETS_TUTORIAL + "\n\n⚠️ (`.gs` não encontrado no host do bot.)", ephemeral=True)
            try:
                await utils.schedule_ephemeral_from_ctx(interaction)
            except Exception:
                pass


class _SheetsUrlButton(ui.Button):
    def __init__(self):
        super().__init__(label='🔗 Definir URL/secret', style=discord.ButtonStyle.primary)

    async def callback(self, interaction):
        await interaction.response.send_modal(_SheetsUrlModal(self.view))


class _SheetsUrlModal(ui.Modal, title='Planilha — URL /exec'):
    def __init__(self, view):
        super().__init__()
        self.view_ref = view
        self.url = ui.TextInput(label='URL /exec do Web App', required=True, max_length=300,
                                placeholder='https://script.google.com/…/exec')
        self.secret = ui.TextInput(label='Secret (opcional)', required=False, max_length=100)
        self.add_item(self.url)
        self.add_item(self.secret)

    async def on_submit(self, interaction):
        url = (self.url.value or '').strip()
        if not (url.startswith('https://') and url.rstrip('/').endswith('/exec')):
            await send_err(interaction, "URL inválida — precisa começar com `https://` "
                                        "e terminar em `/exec`.")
            return
        await update_economy_config({
            'sheets_webhook_url': url,
            'sheets_secret': (self.secret.value or '').strip(),
        })
        await self.view_ref.go_home(interaction)            # responde já (edita o painel)
        try:                                                # teste best-effort via followup
            import sheets as _sheets
            comps = await _sheets.list_comps()
            msg = (f"✅ Planilha configurada — teste OK: {len(comps)} comp(s)." if comps
                   else "⚠️ Salvei, mas a planilha não retornou comps (confira publicação/COMPS).")
        except Exception as e:
            msg = f"⚠️ Salvei, mas o teste falhou ({type(e).__name__})."
        try:
            await interaction.followup.send(msg, ephemeral=True)
            try:
                await utils.schedule_ephemeral_from_ctx(interaction)
            except Exception:
                pass
        except Exception:
            pass


class _GateCompSelect(ui.Select):
    def __init__(self, comps):
        opts = [discord.SelectOption(label=c[:100], value=c[:100]) for c in comps]
        super().__init__(placeholder='Comp…', options=opts)

    async def callback(self, interaction):
        comp = self.values[0]
        if not interaction.response.is_done():
            await interaction.response.defer()
        try:
            import sheets as _sheets
            roles = await _sheets.list_roles(comp)
        except Exception:
            roles = []
        view = self.view
        view._gate_comp = comp
        view._gate_funcs = roles
        view._gate_func_page = 0
        view._gate_func_gates = await get_role_gates()
        await _render_gate_functions(interaction, view)


# Funções da planilha — MENU PAGINADO (25 por página). A comp pode ter mais de 25
# funções e o select do Discord só aceita 25, então percorremos com ◀️ ▶️.
async def _render_gate_functions(interaction, view):
    funcs = view._gate_funcs or []
    gates = view._gate_func_gates or {}
    comp = view._gate_comp
    per = 25
    pages = max(1, (len(funcs) + per - 1) // per)
    view._gate_func_page = max(0, min(view._gate_func_page, pages - 1))
    start = view._gate_func_page * per
    page_funcs = funcs[start:start + per]

    view.clear_items()
    if page_funcs:
        view.add_item(_GateFuncSelect(page_funcs, gates, view._gate_func_page, pages))
    if pages > 1:
        view.add_item(_GateFuncPageButton('◀️', -1, view._gate_func_page == 0))
        view.add_item(_GateFuncPageButton('▶️', +1, view._gate_func_page >= pages - 1))
    view.add_item(_BackButton())

    lines = [f"• {f} — {'🔒' if (f.strip().lower() in gates) else 'aberta'}" for f in funcs]
    body = '\n'.join(lines) if lines else 'comp sem funções.'
    if len(body) > 3800:
        body = body[:3800].rsplit('\n', 1)[0] + '\n…'
    embed = info_embed(body, title=f"Gates — {comp}  ·  pág {view._gate_func_page + 1}/{pages}")
    if interaction.response.is_done():
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        await interaction.response.edit_message(embed=embed, view=view)


class _GateFuncSelect(ui.Select):
    """Uma página (≤25) das FUNÇÕES da planilha; escolher uma abre o editor de cargos."""
    def __init__(self, page_funcs, gates, page, pages):
        opts = [discord.SelectOption(
            label=f[:100], value=f[:100],
            description=('restrita' if (f.strip().lower() in gates) else 'aberta a todos'))
            for f in page_funcs]
        super().__init__(placeholder=f'Função a restringir… (pág {page + 1}/{pages})',
                         options=opts, row=0)

    async def callback(self, interaction):
        view = self.view
        role_name = self.values[0]
        gates = await get_role_gates()
        view._gate_name = role_name
        view._gate_sel = set(gates.get(role_name.strip().lower(), set()))
        view._gate_page = 0
        await _render_gate_roles(interaction, view)


class _GateFuncPageButton(ui.Button):
    def __init__(self, emoji, delta, disabled):
        super().__init__(emoji=emoji, style=discord.ButtonStyle.secondary,
                         disabled=disabled, row=1)
        self.delta = delta

    async def callback(self, interaction):
        self.view._gate_func_page += self.delta
        await _render_gate_functions(interaction, self.view)


# Cargos do servidor — MENU PAGINADO (mostra TODOS, 25 por página). Substitui o
# RoleSelect nativo (limitado a 25). Acumula a seleção entre páginas e só grava no 💾.
def _gate_all_roles(guild):
    """Todos os cargos do servidor (sem @everyone), do mais alto pro mais baixo."""
    if guild is None:
        return []
    return [r for r in reversed(guild.roles) if not r.is_default()]


async def _render_gate_roles(interaction, view):
    """Desenha o editor paginado de cargos para a função em view._gate_name."""
    roles = _gate_all_roles(view.guild)
    per = 25
    pages = max(1, (len(roles) + per - 1) // per)
    view._gate_page = max(0, min(view._gate_page, pages - 1))
    start = view._gate_page * per
    page_roles = roles[start:start + per]

    view.clear_items()
    view.add_item(_GateRolePageSelect(page_roles, view._gate_sel, view._gate_page, pages))
    if pages > 1:
        view.add_item(_GatePageButton('◀️', -1, view._gate_page == 0))
        view.add_item(_GatePageButton('▶️', +1, view._gate_page >= pages - 1))
    view.add_item(_GateSaveButton())
    view.add_item(_BackButton())

    chosen = [view.guild.get_role(i) for i in view._gate_sel]
    chosen = ', '.join(r.mention for r in chosen if r) or '*nenhum (função aberta a todos)*'
    embed = info_embed(
        f"Marque os cargos que podem pegar **{view._gate_name}** e clique em "
        f"**💾 Salvar**.\nUse ◀️ ▶️ para percorrer **todos** os cargos do servidor.\n\n"
        f"**Selecionados:** {chosen}",
        title=f"Gate — {view._gate_name}  ·  pág {view._gate_page + 1}/{pages}")
    if interaction.response.is_done():
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        await interaction.response.edit_message(embed=embed, view=view)


class _GateRolePageSelect(ui.Select):
    """Uma página (até 25) dos cargos do servidor; o que estiver marcado entra no gate."""
    def __init__(self, page_roles, selected, page, pages):
        self._page_ids = {r.id for r in page_roles}
        opts = [discord.SelectOption(label=r.name[:100], value=str(r.id),
                                     default=(r.id in selected)) for r in page_roles]
        if not opts:
            opts = [discord.SelectOption(label='(sem cargos)', value='_')]
        super().__init__(placeholder=f'Cargos (pág {page + 1}/{pages})…',
                         min_values=0, max_values=len(opts), options=opts, row=0)

    async def callback(self, interaction):
        view = self.view
        chosen = {int(v) for v in self.values if v != '_'}
        # substitui apenas a seleção DESTA página; mantém o resto acumulado.
        view._gate_sel = (view._gate_sel - self._page_ids) | chosen
        await _render_gate_roles(interaction, view)


class _GatePageButton(ui.Button):
    def __init__(self, emoji, delta, disabled):
        super().__init__(emoji=emoji, style=discord.ButtonStyle.secondary,
                         disabled=disabled, row=1)
        self.delta = delta

    async def callback(self, interaction):
        self.view._gate_page += self.delta
        await _render_gate_roles(interaction, self.view)


class _GateSaveButton(ui.Button):
    def __init__(self):
        super().__init__(label='💾 Salvar', style=discord.ButtonStyle.success, row=1)

    async def callback(self, interaction):
        view = self.view
        await remove_role_gate(view._gate_name)            # limpa e regrava do zero
        for rid in view._gate_sel:
            await add_role_gate(view._gate_name, rid, interaction.user.id)
        await _show_gates_root(interaction, view)


class SetupView(ui.View):
    def __init__(self, guild, author_id):
        super().__init__(timeout=300)
        self.guild = guild
        self.author_id = author_id
        # estado transitório do editor PAGINADO de cargos (seção de gates)
        self._gate_name = None
        self._gate_sel: set = set()
        self._gate_page = 0
        # estado da lista PAGINADA de funções da planilha (comp pode ter >25)
        self._gate_comp = None
        self._gate_funcs: list = []
        self._gate_func_page = 0
        self._gate_func_gates: dict = {}
        # estado transitório da seção de recrutamento (canal + cargos antes de publicar)
        self._recruit_channel_id = None
        self._recruit_role_ids: list = []
        self._recruit_temprole_id = None
        self._home()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await send_err(interaction, "Só o owner pode usar este painel.")
            return False
        return True

    def _home(self):
        self.clear_items()
        self.add_item(_SectionSelect())

    async def go_home(self, interaction):
        self._home()
        embed = await _build_setup_embed(self.guild)
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    async def show_items(self, interaction, items, instr):
        self.clear_items()
        for it in items:
            self.add_item(it)
        self.add_item(_BackButton())
        await interaction.response.edit_message(embed=info_embed(instr), view=self)

    async def show_recruit(self, interaction):
        """Seção de recrutamento: canal + cargos (estado transitório) + publicar."""
        self.clear_items()
        self.add_item(_RecruitChannelSelect())
        self.add_item(_RecruitRolesSelect())
        self.add_item(_RecruitTempRoleSelect())
        self.add_item(_RecruitPublishButton())
        self.add_item(_BackButton())
        ch = self.guild.get_channel(self._recruit_channel_id) if self._recruit_channel_id else None
        roles = [self.guild.get_role(r) for r in (self._recruit_role_ids or [])]
        roles_m = ", ".join(r.mention for r in roles if r) or '*nenhum*'
        temprole = self.guild.get_role(self._recruit_temprole_id) if self._recruit_temprole_id else None
        embed = info_embed(
            "**📩 Recrutamento** — escolha o **canal** do painel e os **cargos** de "
            "recrutador, depois **Salvar e publicar painel**.\n\n"
            f"• Canal: {ch.mention if ch else '*não escolhido*'}\n"
            f"• Cargos: {roles_m}\n"
            f"• Cargo temporário: {temprole.mention if temprole else '*nenhum*'}\n\n"
            "*Dê aos cargos de recrutador a permissão **Gerenciar Tópicos** no canal "
            "(pra verem as threads privadas).*\n"
            "*O **cargo temporário** (opcional) é dado ao candidato ao abrir o ticket — "
            "só se ele **não tiver nenhum cargo** — e removido quando o ticket é "
            "aprovado/recusado.*")
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    async def show_nodetypes(self, interaction):
        """Seção de tipos de node: lista atual (nome · emoji · peso → % do scout),
        botão de adicionar/editar (modal) e select de remover."""
        cfg = await load_economy_config()
        scout_pct = int(cfg.get('node_scout_percent') or 0)
        defs = await get_node_defs()
        self.clear_items()
        self.add_item(_NodeAddButton())
        if defs:
            self.add_item(_NodeDefRemoveSelect(defs))
        self.add_item(_BackButton())

        if defs:
            lines = []
            for d in defs:
                em = d.get('emoji') or '•'
                share = _node_scout_pct_text(d['weight'], scout_pct)
                lines.append(f"{em} **{d['name']}** — peso `{d['weight']:g}` · scout ≈ **{share}** do split")
            body = "\n".join(lines)
        else:
            body = "*Nenhum tipo de node definido. Use **➕ Adicionar / editar node**.*"
        embed = info_embed(
            f"{body}\n\n"
            f"*O **peso** é a importância do node no pagamento do scout — a % é o quanto um "
            f"scout recebe do split por capturar **só** aquele node (teto = Node Scout "
            f"**{scout_pct}%**, ajustável em Percentuais).*",
            title=f'Nodes — tipos & peso ({len(defs)})')
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    async def open_section(self, interaction, section):
        if section == 'roles':
            await self.show_items(interaction, [_RoleFieldSelect()], 'Escolha o cargo a definir:')
        elif section == 'channels':
            await self.show_items(interaction, [_ChannelFieldSelect()], 'Escolha o canal a definir:')
        elif section == 'percents':
            await self.show_items(interaction, [_PercentFieldSelect()], 'Escolha o percentual a definir:')
        elif section == 'energy':
            await self.show_items(interaction, [_EnergyFieldSelect()], 'Energia — escolha o que definir:')
        elif section == 'mentoria':
            await self.show_items(interaction, [_MentoriaSelect()], 'Escolha o fórum da mentoria:')
        elif section == 'recrutamento':
            cfg = await load_economy_config()
            self._recruit_channel_id = cfg.get('channel_recruitment')
            self._recruit_role_ids = [int(x) for x in
                                      str(cfg.get('recruiter_roles') or '').split(',')
                                      if x.strip().isdigit()]
            self._recruit_temprole_id = cfg.get('role_recruitment')
            await self.show_recruit(interaction)
        elif section == 'nodetypes':
            await self.show_nodetypes(interaction)
        elif section == 'nodes':
            await self.show_items(interaction, [_NodeChannelSelect()],
                                  'Escolha o canal do calendário de nodes:')
        elif section == 'utc':
            await self.show_items(interaction, [_UtcCategorySelect()],
                                  'Escolha a categoria do relógio UTC:')
        elif section == 'sheets':
            await self.show_items(interaction, [_SheetsTutorialButton(), _SheetsUrlButton()],
                                  'Planilha (Sheets) deste servidor:')
        elif section == 'guild':
            await interaction.response.send_modal(_GuildModal(self))
        elif section == 'teamspeak':
            await interaction.response.send_modal(_TeamspeakModal(self))
        elif section == 'gates':
            await _show_gates_root(interaction, self)


class Economy(commands.Cog):
    """Sistema de economia da guild (saldos, banco, taxas, splits…)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return True
        if not await is_server_activated(ctx.guild.id):
            await send_err(ctx, "Este servidor não está ativado!")
            return False
        return True

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------
    async def _check_role_or_reply(self, ctx: commands.Context, *role_keys: str) -> bool:
        """
        Retorna True se o autor é OWNER ou possui um dos cargos.
        Caso contrário, envia mensagem de erro e retorna False.
        """
        if ctx.author.id == OWNER_ID:
            return True
        if await has_configured_role(ctx.author, *role_keys):
            return True
        await send_err(ctx, "Você não tem permissão para usar este comando.")
        return False

    async def _post_economy_log(
        self,
        guild: discord.Guild,
        *,
        title: str,
        kind: str = 'info',          # ok/err/warn/info → cor do tema
        desc: str = None,
        footer: str = None,          # texto simples (sem avatar), ex.: "por Fulano"
    ):
        """Posta um embed no canal de economylogs (silencioso se não configurado).
        Segue o tema do bot: título negrito-unicode, a COR carrega o significado,
        descrição compacta — sem footer com avatar nem timestamp."""
        cfg = await load_economy_config()
        chan_id = cfg.get('channel_economylogs')
        if not chan_id or guild is None:
            return
        channel = guild.get_channel(chan_id)
        if not channel:
            return

        embed = make_embed(kind, title=title, desc=desc)
        if footer:
            embed.set_footer(text=footer)
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            print("✗ Sem permissão para postar no canal de economylogs")

    # ------------------------------------------------------------------
    # /setup  — painel interativo (apenas owner)
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="setup",
        description="Painel de configuração do servidor (apenas owner)",
    )
    @app_commands.guild_only()
    async def setup_cmd(self, ctx: commands.Context):
        if ctx.author.id != OWNER_ID:
            await send_err(ctx, "Apenas o owner pode usar este comando.")
            return
        if ctx.guild is None:
            await send_err(ctx, "Use este comando dentro do servidor.")
            return
        embed = await _build_setup_embed(ctx.guild)
        await ctx.send(embed=embed, view=SetupView(ctx.guild, ctx.author.id), ephemeral=True)
        try:
            await utils.schedule_ephemeral_from_ctx(ctx)
        except Exception:
            pass

    # ==================================================================
    # ==================  COMANDOS DE SALDO  ===========================
    # ==================================================================

    # ------------------------------------------------------------------
    # /balance
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="balance",
        aliases=["bal", "saldo"],
        description="Mostra o saldo de um usuário (o seu, se nenhum for mencionado)",
    )
    @app_commands.guild_only()
    @app_commands.describe(target="Usuário (em branco = você)")
    async def balance(self, ctx: commands.Context, target: discord.Member = None):
        target = target or ctx.author
        bal, earned = await get_user_balance(target.id)
        energy = await get_user_energy(target.id)

        embed = make_embed('info', title=f"")
        embed.set_author(name=f"{target.display_name}", icon_url=target.display_avatar.url)
        embed.add_field(name="", value=f"Saldo de Prata: {format_silver(bal)}", inline=False)
        # Energia só aparece p/ quem realmente usa: já apareceu numa log de energia
        # OU tem saldo diferente de zero (ex.: empréstimo/punição c/ saldo negativo).
        if energy != 0 or await has_energy_history(target.id):
            embed.add_field(name="", value=f"Saldo de Energia: {format_silver(energy)}", inline=False)
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # /guildbalance
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="guildbalance",
        aliases=["guildbal", "gb"],
        description="Mostra o saldo do banco da guild (council/logistic)",
    )
    @app_commands.guild_only()
    async def guildbalance(self, ctx: commands.Context):
        if not await self._check_role_or_reply(ctx, 'role_council', 'role_logistic'):
            return
        bal = await get_guild_bank_balance()
        embed = make_embed('info', title="Banco da Guild",
                           desc=f"💰 Saldo: **{format_silver(bal)}**")
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # /pay
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="pay",
        aliases=["givemoney", "darsaldo"],
        description="Transfere prata do seu saldo para outro usuário",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        target="Quem vai receber",
        amount="Quanto enviar (ex: 100k, 1.5m, 2,500,000, 1.000.000) ou `all`/`tudo`",
    )
    async def pay(self, ctx: commands.Context, target: discord.Member, amount: str):
        if target.id == ctx.author.id:
            await send_err(ctx, "Você não pode pagar a si mesmo.")
            return
        if target.bot:
            await send_err(ctx, "Você não pode pagar a um bot.")
            return

        if _is_all_keyword(amount):
            # `all`/`tudo` = envia todo o saldo positivo do autor.
            author_bal, _ = await get_user_balance(ctx.author.id)
            if author_bal <= 0:
                await send_err(ctx, f"Você não tem saldo positivo para enviar "
                                    f"(atual: `{format_silver(author_bal)}`).")
                return
            parsed = author_bal
        else:
            parsed = parse_silver(amount)
            if parsed is None or parsed <= 0:
                await send_err(ctx, "Valor inválido. Aceito: `1500000`, `2,500,000`, `1.5m`, `150k`, `1.000.000` ou `all`/`tudo`.")
                return

        ok = await transfer_money(ctx.author.id, target.id, parsed)
        if not ok:
            author_bal, _ = await get_user_balance(ctx.author.id)
            await send_err(ctx, f"Saldo insuficiente. Você tem apenas `{format_silver(author_bal)}`.")
            return

        author_bal, _ = await get_user_balance(ctx.author.id)
        target_bal, _ = await get_user_balance(target.id)

        await ctx.send(embed=ok_embed(
            f"{ctx.author.mention} enviou {format_silver(parsed)} para {target.mention}"))

        await self._post_economy_log(
            ctx.guild,
            title="💸  𝐏𝐀𝐆𝐀𝐌𝐄𝐍𝐓𝐎",
            kind='info',
            desc=(f"{ctx.author.mention}  →  {target.mention}\n"
                  f"💰  **{format_silver(parsed)}**\n\n"
                  f"{ctx.author.display_name} `{format_silver(author_bal)}`  ·  "
                  f"{target.display_name} `{format_silver(target_bal)}`"),
        )

    # ------------------------------------------------------------------
    # /addmoney
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="addmoney",
        aliases=["adicionarsaldo", "addbal"],
        description="Adiciona prata ao saldo de um usuário (council/logistic)",
    )
    @app_commands.guild_only()
    @app_commands.describe(target="Usuário alvo", amount="Quanto adicionar")
    async def addmoney(self, ctx: commands.Context, target: discord.Member, amount: str):
        if not await self._check_role_or_reply(ctx, 'role_council', 'role_logistic'):
            return

        parsed = parse_silver(amount)
        if parsed is None or parsed <= 0:
            await send_err(ctx, "Valor inválido.")
            return

        await add_user_money(target.id, parsed)
        new_bal, _ = await get_user_balance(target.id)

        await ctx.send(embed=ok_embed(f"{target.mention} recebeu **{format_silver(parsed)}**"))

        await self._post_economy_log(
            ctx.guild,
            title="➕  𝐒𝐀𝐋𝐃𝐎",
            kind='ok',
            desc=(f"{target.mention}  💰 **+{format_silver(parsed)}**\n"
                  f"saldo `{format_silver(new_bal)}`"),
            footer=f"por {ctx.author.display_name}",
        )

    # ------------------------------------------------------------------
    # /multiaddmoney — mesmo valor pra CADA participante (sem dividir)
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="multiaddmoney",
        aliases=["multiadd", "addmulti"],
        description="Adiciona um valor a CADA participante mencionado, sem dividir (council/logistic)",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        amount="Quanto dar a CADA um (ex: 100k, 1.5m)",
        participantes="Mencione os participantes (@a @b @c)",
    )
    async def multiaddmoney(self, ctx: commands.Context, amount: str, *, participantes: str):
        if not await self._check_role_or_reply(ctx, 'role_council', 'role_logistic'):
            return
        parsed = parse_silver(amount)
        if parsed is None or parsed <= 0:
            await send_err(ctx, "Valor inválido.")
            return

        # Resolve os participantes pelas menções (funciona por / e por !).
        seen, members = set(), []
        for raw in re.findall(r'<@!?(\d+)>', participantes or ''):
            uid = int(raw)
            if uid in seen:
                continue
            seen.add(uid)
            m = ctx.guild.get_member(uid) if ctx.guild else None
            if m and not m.bot:
                members.append(m)
        if not members:
            await send_err(ctx, "Mencione ao menos um participante (ex: `@a @b @c`).")
            return

        for m in members:
            await add_user_money(m.id, parsed)
        total = parsed * len(members)

        await ctx.send(embed=ok_embed(
            f"**{format_silver(parsed)}** para CADA um de **{len(members)}** participante(s) "
            f"(total `{format_silver(total)}`)."))

        mentions = ", ".join(m.mention for m in members[:30])
        if len(members) > 30:
            mentions += f" … +{len(members) - 30}"
        await self._post_economy_log(
            ctx.guild,
            title="➕  𝐌𝐔𝐋𝐓𝐈 𝐒𝐀𝐋𝐃𝐎",
            kind='ok',
            desc=(f"💰 **+{format_silver(parsed)}** para cada um de **{len(members)}** "
                  f"(total `{format_silver(total)}`)\n{mentions}"),
            footer=f"por {ctx.author.display_name}",
        )

    # ------------------------------------------------------------------
    # /removemoney
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="removemoney",
        aliases=["rmm", "rmmoney", "removersaldo"],
        description="Remove prata do saldo de um usuário (sem valor = remove tudo)",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        target="Usuário alvo",
        amount="Quanto remover (em branco ou `all`/`tudo` = remove tudo)",
    )
    async def removemoney(
        self,
        ctx: commands.Context,
        target: discord.Member,
        amount: str = None,
    ):
        if not await self._check_role_or_reply(ctx, 'role_council', 'role_logistic'):
            return

        current, _ = await get_user_balance(target.id)

        if amount is None or _is_all_keyword(amount):
            # "remove tudo" só zera saldo positivo; nunca cria saldo negativo.
            if current <= 0:
                await send_warn(ctx, f"{target.mention} não tem saldo positivo a remover "
                                     f"(atual: `{format_silver(current)}`).")
                return
            actual = await remove_user_money(target.id, current)
        else:
            requested = parse_silver(amount)
            if requested is None or requested <= 0:
                await send_err(ctx, "Valor inválido.")
                return
            # Com valor explícito o saldo PODE ficar negativo (punição/empréstimo).
            actual = await remove_user_money(target.id, requested, allow_negative=True)

        new_bal, _ = await get_user_balance(target.id)

        embed = make_embed('err',
            desc=f"{ctx.author.mention} removeu {format_silver(actual)} do saldo de {target.mention}")
        if new_bal < 0:
            embed.add_field(name="⚠️ Saldo negativo",
                            value=f"{format_silver(new_bal)} (empréstimo/punição)", inline=False)
        await ctx.send(embed=embed)

        await self._post_economy_log(
            ctx.guild,
            title="➖  𝐒𝐀𝐋𝐃𝐎",
            kind='err',
            desc=(f"{target.mention}  💰 **−{format_silver(actual)}**\n"
                  f"saldo `{format_silver(new_bal)}`"
                  + ("  ⚠️ negativo" if new_bal < 0 else "")),
            footer=f"por {ctx.author.display_name}",
        )

    # ------------------------------------------------------------------
    # /addguildmoney
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="addguildmoney",
        aliases=["addgb"],
        description="Adiciona prata ao banco da guild (council/logistic)",
    )
    @app_commands.guild_only()
    @app_commands.describe(amount="Quanto adicionar")
    async def addguildmoney(self, ctx: commands.Context, amount: str):
        if not await self._check_role_or_reply(ctx, 'role_council', 'role_logistic'):
            return

        parsed = parse_silver(amount)
        if parsed is None or parsed <= 0:
            await send_err(ctx, "Valor inválido.")
            return

        await add_guild_bank(parsed)
        new_bal = await get_guild_bank_balance()

        embed = make_embed('ok', title="Banco da Guild — depósito",
            desc=f"{ctx.author.mention} adicionou **{format_silver(parsed)}** ao banco da guild")
        embed.add_field(name="Novo saldo do banco", value=f"`{format_silver(new_bal)}`", inline=False)
        await ctx.send(embed=embed)

        await self._post_economy_log(
            ctx.guild,
            title="🏦  𝐁𝐀𝐍𝐂𝐎 𝐃𝐀 𝐆𝐔𝐈𝐋𝐃",
            kind='ok',
            desc=(f"💰 **+{format_silver(parsed)}**\n"
                  f"banco `{format_silver(new_bal)}`"),
            footer=f"por {ctx.author.display_name}",
        )

    # ------------------------------------------------------------------
    # /removeguildmoney
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="removeguildmoney",
        aliases=["removegb", "rmgb"],
        description="Remove prata do banco da guild (sem valor = remove tudo)",
    )
    @app_commands.guild_only()
    @app_commands.describe(amount="Quanto remover (em branco ou `all`/`tudo` = remove tudo)")
    async def removeguildmoney(self, ctx: commands.Context, amount: str = None):
        if not await self._check_role_or_reply(ctx, 'role_council', 'role_logistic'):
            return

        current = await get_guild_bank_balance()

        if amount is None or _is_all_keyword(amount):
            # "remove tudo" só zera saldo positivo; nunca cria banco negativo.
            if current <= 0:
                await send_warn(ctx, f"O banco da guild não tem saldo positivo a remover "
                                     f"(atual: `{format_silver(current)}`).")
                return
            actual = await remove_guild_bank(current)
        else:
            requested = parse_silver(amount)
            if requested is None or requested <= 0:
                await send_err(ctx, "Valor inválido.")
                return
            # Com valor explícito o banco PODE ficar negativo (decisão da staff).
            actual = await remove_guild_bank(requested, allow_negative=True)

        new_bal = await get_guild_bank_balance()

        embed = make_embed('err', title="Banco da Guild — saque",
            desc=f"{ctx.author.mention} removeu **{format_silver(actual)}** do banco da guild")
        embed.add_field(name="Novo saldo do banco", value=f"`{format_silver(new_bal)}`", inline=False)
        if new_bal < 0:
            embed.add_field(name="⚠️ Banco negativo", value=f"`{format_silver(new_bal)}`", inline=False)
        await ctx.send(embed=embed)

        await self._post_economy_log(
            ctx.guild,
            title="🏦  𝐁𝐀𝐍𝐂𝐎 𝐃𝐀 𝐆𝐔𝐈𝐋𝐃",
            kind='err',
            desc=(f"💰 **−{format_silver(actual)}**\n"
                  f"banco `{format_silver(new_bal)}`"
                  + ("  ⚠️ negativo" if new_bal < 0 else "")),
            footer=f"por {ctx.author.display_name}",
        )

    # ------------------------------------------------------------------
    # /economystats
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="economystats",
        aliases=["es"],   # 'stats' agora é o /stats (perfil do jogador), não mais alias daqui
        description="Mostra um snapshot da economia da guild (council/logistic)",
    )
    @app_commands.guild_only()
    async def economystats(self, ctx: commands.Context):
        if not await self._check_role_or_reply(ctx, 'role_council', 'role_logistic'):
            return

        ov = await get_economy_overview()

        embed = make_embed('info', title="Estatísticas econômicas")
        embed.add_field(
            name="🏦  Banco da Guild",
            value=f"`{format_silver(ov['guild_bank'])}`",
            inline=False,
        )
        embed.add_field(
            name=f"👥  Saldos de Usuários  ({ov['user_count']})",
            value=f"`{format_silver(ov['user_balances_sum'])}`",
            inline=False,
        )
        embed.add_field(
            name="📊  Total Circulante",
            value=f"**`{format_silver(ov['total'])}`**",
            inline=False,
        )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # /leaderboard  (paginação 4 botões, autor-only)
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="leaderboard",
        aliases=["lb", "ranking"],
        description="Ranking dos usuários pelo saldo atual de prata",
    )
    @app_commands.guild_only()
    async def leaderboard(self, ctx: commands.Context):
        total = await get_leaderboard_count()
        if total == 0:
            await send_info(ctx, "Ninguém tem saldo ainda — leaderboard vazio.")
            return

        view = LeaderboardView(
            author_id=ctx.author.id,
            total_count=total,
            page_size=LEADERBOARD_PAGE_SIZE,
        )
        embed = await view.build_embed()
        view.update_buttons()
        await ctx.send(embed=embed, view=view)


# ----------------------------------------------------------------------
# LeaderboardView — paginação interativa do /leaderboard
# ----------------------------------------------------------------------
class LeaderboardView(discord.ui.View):
    """
    Botões: ⏮️ início · ◀️ anterior · ▶️ próximo · ⏭️ fim
    Apenas o autor do comando pode usar.
    """

    def __init__(self, *, author_id: int, total_count: int, page_size: int = 10):
        super().__init__(timeout=300)   # 5 min
        self.author_id   = author_id
        self.total_count = total_count
        self.page_size   = page_size
        self.page        = 0
        self.max_page    = max(0, (total_count - 1) // page_size)
        self.ranking_type = 'balance'  # one of: balance, attendance, mvp, kills, deaths

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await send_err(interaction, "Apenas quem usou o comando pode controlar a paginação.")
            return False
        return True

    def update_buttons(self):
        """Ativa/desativa botões conforme página atual."""
        self.first_btn.disabled = (self.page == 0)
        self.prev_btn.disabled  = (self.page == 0)
        self.next_btn.disabled  = (self.page >= self.max_page)
        self.last_btn.disabled  = (self.page >= self.max_page)

    async def _set_ranking(self, ranking: str):
        """Switch ranking type and recompute totals/pages."""
        self.ranking_type = ranking
        # recalc total_count and max_page
        if ranking == 'balance':
            self.total_count = await get_leaderboard_count()
        elif ranking == 'attendance':
            self.total_count = await get_attendance_leaderboard_count()
        elif ranking == 'mvp':
            self.total_count = await get_mvp_leaderboard_count()
        elif ranking == 'kills':
            self.total_count = await get_kills_leaderboard_count()
        elif ranking == 'deaths':
            self.total_count = await get_deaths_leaderboard_count()
        else:
            self.total_count = 0
        self.max_page = max(0, (self.total_count - 1) // self.page_size)

    async def build_embed(self) -> discord.Embed:
        offset = self.page * self.page_size
        embed = make_embed('info', title=f"Leaderboard")

        # Fetch rows depending on ranking_type
        if self.ranking_type == 'balance':
            rows = await get_leaderboard_page(offset, self.page_size)
        elif self.ranking_type == 'attendance':
            rows = await get_attendance_leaderboard_page(offset, self.page_size)
        elif self.ranking_type == 'mvp':
            rows = await get_mvp_leaderboard_page(offset, self.page_size)
        elif self.ranking_type == 'kills':
            rows = await get_kills_leaderboard_page(offset, self.page_size)
        elif self.ranking_type == 'deaths':
            rows = await get_deaths_leaderboard_page(offset, self.page_size)
        else:
            rows = []

        if not rows:
            embed.description = "*Nada para mostrar nesta página.*"
            return embed

        lines = []
        for i, row in enumerate(rows):
            rank = offset + i + 1
            # row formats differ by ranking_type
            if self.ranking_type == 'balance':
                uid, bal, _total_earned, _attend = row
                lines.append(f"__{rank}.__ <@{uid}>: {format_silver(bal)}")
            elif self.ranking_type == 'attendance':
                uid, cnt = row
                lines.append(f"__{rank}.__ <@{uid}>: `{cnt}` 👋")
            elif self.ranking_type == 'mvp':
                owner, cnt = row
                if isinstance(owner, int):
                    lines.append(f"__{rank}.__ <@{owner}>: `{cnt}` ⭐")
                else:
                    # owner is player name
                    reg = await database.get_registration_by_nick(owner)
                    if reg and reg.get('user_id'):
                        lines.append(f"__{rank}.__ <@{reg['user_id']}>: `{cnt}` ⭐")
                    else:
                        lines.append(f"__{rank}.__ `{owner}`: `{cnt}` ⭐")
            elif self.ranking_type == 'kills':
                owner, cnt = row
                if isinstance(owner, int):
                    lines.append(f"__{rank}.__ <@{owner}>: `{cnt}` ⚔️")
                else:
                    reg = await database.get_registration_by_nick(owner)
                    if reg and reg.get('user_id'):
                        lines.append(f"__{rank}.__ <@{reg['user_id']}>: `{cnt}` ⚔️")
                    else:
                        lines.append(f"__{rank}.__ `{owner}`: `{cnt}` ⚔️")
            elif self.ranking_type == 'deaths':
                owner, cnt = row
                if isinstance(owner, int):
                    lines.append(f"__{rank}.__ <@{owner}>: `{cnt}` 💀")
                else:
                    reg = await database.get_registration_by_nick(owner)
                    if reg and reg.get('user_id'):
                        lines.append(f"__{rank}.__ <@{reg['user_id']}>: `{cnt}` 💀")
                    else:
                        lines.append(f"__{rank}.__ `{owner}`: `{cnt}` 💀")

        embed.description = "\n".join(lines)

        # Posição do autor (se não estiver na página atual)
        ids_on_page = {row[0] for row in rows}
        if self.ranking_type == 'balance':
            ids_on_page = {row[0] for row in rows}
            if self.author_id not in ids_on_page:
                pos = await get_user_leaderboard_position(self.author_id)
                if pos:
                    a_rank, a_bal, _a_earned, _a_attend = pos
                    embed.add_field(name="", value=(f"__{a_rank}.__ <@{self.author_id}>: {format_silver(a_bal)}"), inline=False)
        elif self.ranking_type == 'attendance':
            ids_on_page = {row[0] for row in rows}
            if self.author_id not in ids_on_page:
                rnk, cnt = await get_attendance_rank_for_user(self.author_id)
                if rnk:
                    embed.add_field(name="", value=(f"__{rnk}.__ <@{self.author_id}>: `{cnt}` 👋"), inline=False)
            else:
                # For name-based leaderboards (mvp/kills/deaths), try to show author if registered
                reg = await database.get_registration(self.author_id)
                if reg:
                    nick = reg.get('nick')
                if nick:
                    if self.ranking_type == 'mvp':
                        # Prefer rank by user_id (aggregated). Fallback to nick-based.
                        rnk, cnt = await get_mvp_rank_for_user(self.author_id)
                        if rnk:
                            embed.add_field(name="", value=(f"__{rnk}.__ <@{self.author_id}>: `{cnt}` ⭐"), inline=False)
                        else:
                            rnk, cnt = await get_mvp_rank_for_player(nick)
                            if rnk:
                                embed.add_field(name="", value=(f"__{rnk}.__ <@{self.author_id}>: `{cnt}` ⭐"), inline=False)
                    elif self.ranking_type == 'kills':
                        rnk, cnt = await get_kills_rank_for_user(self.author_id)
                        if rnk:
                            embed.add_field(name="", value=(f"__{rnk}.__ <@{self.author_id}>: `{cnt}` ⚔️"), inline=False)
                        else:
                            rnk, cnt = await get_kills_rank_for_player(nick)
                            if rnk:
                                embed.add_field(name="", value=(f"__{rnk}.__ <@{self.author_id}>: `{cnt}` ⚔️"), inline=False)
                    elif self.ranking_type == 'deaths':
                        rnk, cnt = await get_deaths_rank_for_user(self.author_id)
                        if rnk:
                            embed.add_field(name="", value=(f"__{rnk}.__ <@{self.author_id}>: `{cnt}` 💀"), inline=False)
                        else:
                            rnk, cnt = await get_deaths_rank_for_player(nick)
                            if rnk:
                                embed.add_field(name="", value=(f"__{rnk}.__ <@{self.author_id}>: `{cnt}` 💀"), inline=False)

        return embed

    async def _render_page(self, interaction: discord.Interaction):
        # NÃO chamar de "_refresh": discord.ui.View já tem um _refresh interno (síncrono)
        # que ele invoca sozinho — sobrescrever com coroutine gera "never awaited".
        embed = await self.build_embed()
        self.update_buttons()
        await interaction.response.edit_message(embed=embed, view=self)

    # ----- Botões -----
    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.primary)
    async def first_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = 0
        await self._render_page(interaction)

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.primary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        await self._render_page(interaction)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.primary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.max_page, self.page + 1)
        await self._render_page(interaction)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.primary)
    async def last_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = self.max_page
        await self._render_page(interaction)

    # ----- Ranking type buttons (row 1) -----
    @discord.ui.button(emoji="💰", style=discord.ButtonStyle.secondary, row=1)
    async def rank_money_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_ranking('balance')
        self.page = 0
        await self._render_page(interaction)

    @discord.ui.button(emoji="👋", style=discord.ButtonStyle.secondary, row=1)
    async def rank_attendance_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_ranking('attendance')
        self.page = 0
        await self._render_page(interaction)

    @discord.ui.button(emoji="⭐", style=discord.ButtonStyle.secondary, row=1)
    async def rank_mvp_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_ranking('mvp')
        self.page = 0
        await self._render_page(interaction)

    @discord.ui.button(emoji="⚔️", style=discord.ButtonStyle.secondary, row=1)
    async def rank_kills_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_ranking('kills')
        self.page = 0
        await self._render_page(interaction)

    @discord.ui.button(emoji="💀", style=discord.ButtonStyle.secondary, row=1)
    async def rank_deaths_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_ranking('deaths')
        self.page = 0
        await self._render_page(interaction)

    @discord.ui.button(emoji="👥", style=discord.ButtonStyle.primary)
    async def goto_author_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Jump to the page containing the author for the current ranking_type
        if self.ranking_type == 'balance':
            pos = await get_user_leaderboard_position(self.author_id)
            if not pos:
                await interaction.response.send_message("Você não aparece no leaderboard.", ephemeral=True)
                try:
                    await utils.schedule_ephemeral_from_ctx(interaction)
                except Exception:
                    pass
                return
            rank = pos[0]
        elif self.ranking_type == 'attendance':
            rnk, _ = await get_attendance_rank_for_user(self.author_id)
            if not rnk:
                await interaction.response.send_message("Você não aparece no ranking de attendance.", ephemeral=True)
                try:
                    await utils.schedule_ephemeral_from_ctx(interaction)
                except Exception:
                    pass
                return
            rank = rnk
        else:
            reg = await database.get_registration(self.author_id)
            if not reg or not reg.get('nick'):
                await interaction.response.send_message("Você não está registrado. Use /register para registrar seu nick.", ephemeral=True)
                try:
                    await utils.schedule_ephemeral_from_ctx(interaction)
                except Exception:
                    pass
                return
            nick = reg.get('nick')
            if self.ranking_type == 'mvp':
                rnk, _ = await get_mvp_rank_for_player(nick)
            elif self.ranking_type == 'kills':
                rnk, _ = await get_kills_rank_for_player(nick)
            elif self.ranking_type == 'deaths':
                rnk, _ = await get_deaths_rank_for_player(nick)
            else:
                rnk = None
            if not rnk:
                await interaction.response.send_message("Seu nick não aparece neste ranking.", ephemeral=True)
                try:
                    await utils.schedule_ephemeral_from_ctx(interaction)
                except Exception:
                    pass
                return
            rank = rnk

        # compute page and render
        self.page = max(0, (int(rank) - 1) // self.page_size)
        await self._render_page(interaction)


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
