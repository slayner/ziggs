"""Painel /setup + permissões do bot LEGADO (Hideout).

Isto ERA o cogs/economy.py do bot antigo (71KB): economia completa (saldos,
banco, splits, planilha, gates, nodes). Em ago/2026 o bot foi reduzido às
features que o bot-v2 não tem (recrutamento, relógio UTC, energia, temp voice,
mentoria) e a ECONOMIA saiu junto — o que restou do economy.py é a
infraestrutura que os cogs mantidos precisam:

  · has_configured_role() — checagem de permissão central (OWNER + role_lead
    sempre passam), usada por recruitment/energia/mentoria/misc
  · /setup — painel de configuração de cargos, canais, recrutamento (publica o
    painel de tickets), fórum da mentoria, relógio UTC, energia e TeamSpeak
  · _post_economy_log — audit log no canal configurado

Os comandos de saldo (balance/pay/addmoney/guildbank/leaderboard…) foram
REMOVIDOS — a economia agora é do bot-v2 (backend Postgres).
"""
import os
import discord
from discord.ext import commands
from discord import app_commands, ui
from dotenv import load_dotenv
import database

from database import (
    load_economy_config, update_economy_config,
    is_server_activated,
    load_utc_clock,
)
from utils import (
    make_embed,
    send_err, send_info,
    info_embed,
)
import utils

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

# Só os canais das features que FICARAM no bot legado. (A lista antiga tinha
# voice_cta/logger/zergregear/massinfo/tabsell etc. — cogs removidos.)
CHANNEL_FIELDS = [
    ('voice_temp_mother',   'Voice — Sala Mãe (temporárias)'),
    ('channel_economylogs', 'Canal de Logs'),
]


# ======================================================================
# Painel /setup interativo (owner-only).
# ======================================================================
_CT_TEXT  = [discord.ChannelType.text, discord.ChannelType.news]
_CT_VOICE = [discord.ChannelType.voice]
_CT_FORUM = [discord.ChannelType.forum]

_PANEL_ROLES = ROLE_FIELDS                      # [(key, label)]
_PANEL_CHANNELS = [                             # (key, label, channel_types)
    ('voice_temp_mother',   'Voice — Sala Mãe (temporárias)', _CT_VOICE),
    ('channel_economylogs', 'Logs',                           _CT_TEXT),
]
_SECTIONS = [                                   # (value, label, descrição)
    ('roles',     '👥 Cargos',          'Definir os cargos do servidor'),
    ('channels',  '📺 Canais',          'Definir os canais (texto/voz)'),
    ('mentoria',  '🎓 Mentoria',        'Fórum onde cada membro ganha um post'),
    ('recrutamento', '📩 Recrutamento',  'Canal + cargos e publica o painel'),
    ('utc',       '🕒 Relógio UTC',      'Categoria que mostra a hora UTC'),
    ('energy',    '⚡ Energia',          'Canal de alertas e limite'),
    ('teamspeak', '🎧 TeamSpeak',       'Endereço e senha do TS'),
]


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

    # Recrutamento e relógio UTC vivem em colunas próprias — entram no resumo.
    rec_cid   = cfg.get('channel_recruitment')
    rec_roles = [x for x in str(cfg.get('recruiter_roles') or '').split(',') if x.strip().isdigit()]
    rec_ch    = guild.get_channel(rec_cid) if (guild and rec_cid) else None
    rec_line  = (f"✅ {rec_ch.mention} · {len(rec_roles)} cargo(s)" if rec_ch
                 else ('⚠️ `canal sumiu`' if rec_cid else '❌'))
    utc_cid, utc_base = await load_utc_clock()
    utc_cat   = guild.get_channel(utc_cid) if (guild and utc_cid) else None
    utc_line  = (f"✅ {utc_cat.name}" if utc_cat
                 else ('⚠️ `categoria sumiu`' if utc_cid else '❌'))
    embed.add_field(
        name='Recrutamento · Relógio',
        value=(f"Recrutamento: {rec_line}\n"
               f"Relógio UTC: {utc_line}"),
        inline=False)
    return embed


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
        await update_economy_config({self.key: self.values[0].id})
        await self.view.go_home(interaction)


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


class SetupView(ui.View):
    def __init__(self, guild, author_id):
        super().__init__(timeout=300)
        self.guild = guild
        self.author_id = author_id
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

    async def open_section(self, interaction, section):
        if section == 'roles':
            await self.show_items(interaction, [_RoleFieldSelect()], 'Escolha o cargo a definir:')
        elif section == 'channels':
            await self.show_items(interaction, [_ChannelFieldSelect()], 'Escolha o canal a definir:')
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
        elif section == 'utc':
            await self.show_items(interaction, [_UtcCategorySelect()],
                                  'Escolha a categoria do relógio UTC:')
        elif section == 'teamspeak':
            await interaction.response.send_modal(_TeamspeakModal(self))


class Setup(commands.Cog):
    """/setup + permissões + audit log (o que restou do economy.py)."""

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
        """Posta um embed no canal de logs (silencioso se não configurado).
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
            print("✗ Sem permissão para postar no canal de logs")

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


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))
