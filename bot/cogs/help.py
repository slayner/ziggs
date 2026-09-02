"""
/help (aliases: /ajuda, /comandos) — lista os comandos que O USUÁRIO pode usar,
com descrição e modo de uso, agrupados por categoria.

Nome, uso (assinatura) e descrição vêm DOS PRÓPRIOS comandos registrados (sempre
em sincronia). A única coisa curada aqui é a PERMISSÃO de cada comando — ela não
é declarativa no discord.py (cada comando checa cargo no corpo), então mapeamos
abaixo qual cargo libera cada comando, espelhando exatamente os checks reais.
"""
import os
import re

import discord
from discord.ext import commands
from discord import app_commands

from dotenv import load_dotenv

from database import load_economy_config
from utils import make_embed, send_err
import utils

load_dotenv()
OWNER_ID = int(os.getenv('OWNER_ID', 0))

# Acesso por comando:
#   'all'   → todo mundo
#   'owner' → só o OWNER
#   tupla   → QUALQUER um dos cargos (o OWNER e o cargo LEAD entram sempre,
#             igual ao has_configured_role). Comando não listado = 'all'.
_ACCESS = {
    # Owner
    'setup': 'owner',
    'stoputc': 'owner',   # setutc foi pro /setup → Relógio UTC
    'stopnode': 'owner',  # setnode foi pro /setup → Nodes
    # Council / Logistic
    'addmoney': ('role_council', 'role_logistic'),
    'multiaddmoney': ('role_council', 'role_logistic'),
    'removemoney': ('role_council', 'role_logistic'),
    'addguildmoney': ('role_council', 'role_logistic'),
    'removeguildmoney': ('role_council', 'role_logistic'),
    'guildbalance': ('role_council', 'role_logistic'),
    'economystats': ('role_council', 'role_logistic'),
    'deleteevent': ('role_council', 'role_logistic'),
    'reconciliarlog': ('role_council', 'role_logistic'),
    'bb': ('role_council', 'role_logistic'),
    'energywl': ('role_council', 'role_logistic'),
    # Council (lead/owner implícitos)
    'energylog': ('role_council',),
    'setenergy': ('role_council',),
    'lowattendance': ('role_council',),
    'addnodemap': ('role_council',),
    # Logistic (lead/owner implícitos)
    'removenodemap': ('role_logistic',),
    # Caller / Council / Logistic
    'cta': ('role_caller', 'role_council', 'role_logistic'),
    'participacaototal': ('role_caller', 'role_council', 'role_logistic'),
    'callout': ('role_caller', 'role_council', 'role_logistic'),
    'poke': ('role_caller', 'role_council', 'role_logistic'),
    'adiarcta': ('role_caller', 'role_council', 'role_logistic'),
    'openregear': ('role_caller', 'role_council', 'role_logistic'),
    'liberarfuncoes': ('role_caller', 'role_council', 'role_logistic'),
    'gateinfo': ('role_caller', 'role_council', 'role_logistic'),
    # Council / Logistic / Caller / Officer
    'register': ('role_council', 'role_logistic', 'role_caller', 'role_officer'),
    # Council / Logistic / Officer / Mentor
    'trial': ('role_council', 'role_logistic', 'role_officer', 'role_mentor'),
    # Recrutamento: sem comandos — tudo é via painel (/setup → Recrutamento)
    # (todo o resto = 'all': avatar, stats, balance, leaderboard, pay, energy,
    #  disarray, teamspeak, nodemaps, attendance, help)
}

# cog → categoria
_CAT = {
    'Misc': 'Geral', 'Avatar': 'Geral', 'PerfilCog': 'Geral', 'HelpCog': 'Geral',
    'CTACog': 'CTA', 'BattleboardCog': 'CTA', 'LootLogCog': 'CTA',
    'MassInfoCog': 'CTA',
    'Economy': 'Economia',
    'EnergiaCog': 'Energia',
    'NodeCalendar': 'Nodes',
    'RegistrationCog': 'Membros', 'MentoriaCog': 'Membros',
    'RecruitmentCog': 'Membros',
    'Management': 'Gestão',
    'Clock': 'Configuração',
}
# ordem + emoji das categorias na embed
_CAT_ORDER = [
    ('Geral', '🧭'), ('CTA', '⚔️'), ('Economia', '💰'), ('Energia', '⚡'),
    ('Nodes', '🌿'), ('Membros', '📝'), ('Gestão', '📊'), ('Configuração', '⚙️'),
]

_ALL_ROLE_KEYS = {
    'role_council', 'role_logistic', 'role_caller', 'role_officer', 'role_mentor',
}
_PERM_WORDS = re.compile(r'(owner|council|logistic|lead|caller|officer|mentor)', re.I)


def _clean_desc(d: str) -> str:
    """Tira o sufixo de permissão da descrição (a embed já só mostra o que o user pode)."""
    d = (d or '').strip()
    # remove "(... council/logistic ...)" no fim
    m = re.search(r'\s*\(([^()]*)\)\s*$', d)
    if m and _PERM_WORDS.search(m.group(1)):
        d = d[:m.start()].strip()
    # remove "— ... council/logistic ..." no fim
    d = re.sub(r'\s*[—–-]\s*[^—–-]*?(owner|council|logistic|lead|caller|officer|mentor)'
               r'[^—–-]*$', '', d, flags=re.I).strip()
    if len(d) > 110:
        d = d[:107].rstrip() + '…'
    return d


def _app_usage(cmd: app_commands.Command) -> str:
    return ' '.join((f"<{p.display_name}>" if p.required else f"[{p.display_name}]")
                    for p in cmd.parameters)


class HelpCog(commands.Cog, name="HelpCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        print("✓ Help Cog carregada")

    def _iter_commands(self):
        """(nome, uso, descrição, cog) de cada comando — híbridos + slash-puros, sem duplicar."""
        seen = set()
        for cogname, cog in self.bot.cogs.items():
            for c in cog.get_commands():               # prefixo/híbridos
                if getattr(c, 'hidden', False) or c.name in seen:
                    continue
                seen.add(c.name)
                yield c.name, (c.signature or '').strip(), (c.description or c.short_doc or ''), cogname
            for ac in cog.get_app_commands():           # slash-puros (ex.: /reconciliarlog)
                if not isinstance(ac, app_commands.Command) or ac.name in seen:
                    continue
                seen.add(ac.name)
                yield ac.name, _app_usage(ac), (ac.description or ''), cogname

    @commands.hybrid_command(
        name="help", aliases=["ajuda", "comandos"],
        description="Mostra os comandos que VOCÊ pode usar, com descrição e uso.",
    )
    @app_commands.guild_only()
    async def help_cmd(self, ctx: commands.Context):
        member = ctx.author
        if ctx.guild is None or not hasattr(member, 'roles'):
            await send_err(ctx, "Use este comando dentro do servidor.")
            return

        is_owner = (member.id == OWNER_ID)
        try:
            cfg = await load_economy_config()
        except Exception:
            cfg = {}
        role_ids = {r.id for r in member.roles}
        has_lead = bool(cfg.get('role_lead') and cfg.get('role_lead') in role_ids)
        held = {k for k in _ALL_ROLE_KEYS if cfg.get(k) and cfg.get(k) in role_ids}

        def can(access) -> bool:
            if access == 'all':
                return True
            if access == 'owner':
                return is_owner
            if is_owner or has_lead:
                return True
            return any(k in held for k in access)

        # Agrupa por categoria os comandos que o usuário PODE usar.
        by_cat: dict[str, list[str]] = {}
        total = 0
        for name, usage, desc, cogname in self._iter_commands():
            if not can(_ACCESS.get(name, 'all')):
                continue
            cat = _CAT.get(cogname, 'Outros')
            usage_str = f" `{usage}`" if usage else ""
            line = f"**/{name}**{usage_str}"
            cd = _clean_desc(desc)
            if cd:
                line += f" — {cd}"
            by_cat.setdefault(cat, []).append(line)
            total += 1

        if total == 0:
            await send_err(ctx, "Você ainda não tem comandos disponíveis aqui.")
            return

        embed = make_embed(
            'info', title="Comandos disponíveis",
            desc=(f"Estes são os **{total}** comandos que você pode usar. "
                  f"Funcionam por barra (`/`) ou prefixo (`!`).\n"
                  f"`<obrigatório>` · `[opcional]`"),
        )

        ordered = _CAT_ORDER + [(c, '•') for c in sorted(by_cat) if c not in dict(_CAT_ORDER)]
        for cat, emoji in ordered:
            lines = by_cat.get(cat)
            if not lines:
                continue
            # respeita o limite de 1024 por field: quebra em vários se precisar.
            chunk, length, part = [], 0, 0
            for ln in lines:
                if length + len(ln) + 1 > 1000 and chunk:
                    part += 1
                    embed.add_field(
                        name=f"{emoji} {cat}" + (f" ({part})" if part > 1 else ""),
                        value="\n".join(chunk), inline=False)
                    chunk, length = [], 0
                chunk.append(ln)
                length += len(ln) + 1
            if chunk:
                part += 1
                embed.add_field(
                    name=f"{emoji} {cat}" + (f" ({part})" if part > 1 else ""),
                    value="\n".join(chunk), inline=False)

        await ctx.send(embed=embed, ephemeral=True)
        try:
            await utils.schedule_ephemeral_from_ctx(ctx)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
