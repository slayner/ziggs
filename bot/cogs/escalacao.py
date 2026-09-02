"""
Parte 4 — PM do PARTY LEADER.

Depois que a planilha auto-preenche a escalação (botão A2), este cog manda PM
APENAS aos PARTY LEADERS de cada party, avisando que são líderes e dando instruções
do que fazer. Os demais jogadores veem sua função/equipamento pelo botão "Minha PT"
do painel de massa (cog massinfo) — não recebem mais PM. Líderes sem cadastro
(/register) ou com PM fechada entram num resumo no canal economylogs.

Como o bot não recebe avisos instantâneos do Sheets, ele CONSULTA a escalação de
tempos em tempos, com DEDUPE: cada líder recebe a PM uma única vez por CTA — e só
faltando ~2 min pro início (não antes). O PM de líder é OBRIGATÓRIO: o opt-out
("não receber mais PMs") NÃO vale pra ele.
"""
import asyncio
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks
from discord import ui

from utils import EMBED_INFO, EMBED_WARN, send_err
import utils

import database
from database import (
    get_activated_guild_ids,
    get_active_ctas,
    load_economy_config,
    get_registration_by_nick,
    get_party_leader, set_party_leader,
    add_pm_optout,
)
import sheets

# A cada quanto verificar a escalação na planilha.
NOTIFY_INTERVAL_SECONDS = 120

# PM do líder só sai faltando <= isto pro início (evita avisar bem antes, enquanto
# as parties ainda estão se formando e a liderança troca toda hora).
LEADER_PM_LEAD_SECONDS = 120   # 2 min

# Tipo de PM (pro opt-out). O botão liga este tipo na pm_optouts.
PM_TYPE_ESCALACAO = "escalacao"


class EscalacaoPMView(ui.View):
    """View persistente: botão pra não receber mais PMs de escalação."""

    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(
        label="🔕 Não receber mais PMs de escalação",
        style=discord.ButtonStyle.secondary,
        custom_id="pm_optout:escalacao",
    )
    async def optout(self, interaction: discord.Interaction, _btn: ui.Button):
        await add_pm_optout(interaction.user.id, PM_TYPE_ESCALACAO)
        await interaction.response.send_message(
            "🔕 Pronto! Você **não receberá mais** PMs de escalação. "
            "Fale com um council se quiser voltar a receber.",
            ephemeral=True,
        )
        try:
            await utils.schedule_ephemeral_from_ctx(interaction)
        except Exception:
            pass

# (chave no dict da planilha, rótulo no PM, mostrar mesmo se vazio?)
# Cada peça já vem como "tier nome" (ex.: "8.4 Bruxa") do getAssignments.
_EQUIP_FIELDS = [
    ('weapon',    'Arma',           True),
    ('offhand',   'Offhand',        False),   # nem toda arma tem offhand → some se vazia
    ('helmet',    'Capacete',       True),
    ('armor',     'Armadura',       True),
    ('boots',     'Bota',           True),
    ('cape',      'Capa',           True),
    ('food',      'Comida',         True),
    ('abilities', 'Habilidades',    False),
    ('style',     'Estilo de luta', False),
    ('obs',       'Observações',    False),
]

_COLOR = EMBED_INFO


class EscalacaoCog(commands.Cog):
    """Manda PM de função + equipamento aos jogadores escalados."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # (guild_id, event_id) -> frozenset dos nomes já resumidos (evita repostar)
        self._last_missing: dict = {}

    async def cog_load(self):
        self.bot.add_view(EscalacaoPMView())   # botão de opt-out sobrevive a restart
        self.notify_loop.start()
        print("✓ Escalação Cog carregada")

    def cog_unload(self):
        self.notify_loop.cancel()

    # ------------------------------------------------------------------
    def _resolve_guild(self, event: dict) -> discord.Guild | None:
        gid = event.get('guild_id')
        if gid:
            g = self.bot.get_guild(gid)
            if g:
                return g
        return self.bot.guilds[0] if self.bot.guilds else None

    def _started_dt(self, event: dict):
        try:
            dt = datetime.fromisoformat(event.get('started_at') or '')
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    def _build_leader_dm_embed(self, event: dict, a: dict) -> discord.Embed:
        eid = event.get('id')
        party = a.get('party')
        nick = (a.get('name') or '').strip() or "seu_nick"
        embed = discord.Embed(
            title=f"👑 Você é LÍDER de party — CTA #{eid}",
            description=(
                f"Você é o **líder da Party {party}** deste CTA.\n\n"
                "**O que fazer:**\n"
                "• Fique online e atento ao jogo.\n"
                f"• Seus membros entram com **`/join {nick}`** então esteja pronto para aceita-los.\n"
            ),
            color=_COLOR,
        )
        sheet_url = event.get('sheet_url')
        if sheet_url:
            embed.url = sheet_url  # deixa o título clicável (abre a planilha)
        return embed

    def _build_lost_leader_dm(self, event: dict, party, new_name: str) -> discord.Embed:
        eid = event.get('id')
        return discord.Embed(
            title=f"⚠️ Você não é mais líder — Party {party} · CTA #{eid}",
            description=(
                f"Uma função de maior prioridade assumiu a **liderança da Party {party}**.\n\n"
                f"👉 **Passe a party para `{new_name}`**"),
            color=EMBED_WARN,
        )

    # ------------------------------------------------------------------
    @tasks.loop(seconds=NOTIFY_INTERVAL_SECONDS)
    async def notify_loop(self):
        for gid in await get_activated_guild_ids():
            with database.using_guild(gid):
                try:
                    await self._notify_once()
                except Exception as e:
                    print(f"✗ notify_loop [{gid}]: {e}")

    async def _notify_once(self):
        if not await sheets.is_configured():
            return
        try:
            events = await get_active_ctas()
        except Exception as e:
            print(f"✗ Escalação: erro lendo CTAs ativos: {e}")
            return
        if not events:
            return

        cfg = await load_economy_config()
        elogs_id = cfg.get('channel_economylogs')

        now = datetime.now(timezone.utc)
        for event in events:
            page = event.get('sheet_page')
            if not page:
                continue
            # PM de líder SÓ faltando <=2 min pro início (ou já em andamento). Antes
            # disso, pula — não avisa líder enquanto as parties ainda estão se formando.
            dt = self._started_dt(event)
            if dt and (dt - now).total_seconds() > LEADER_PM_LEAD_SECONDS:
                continue
            await self._process_event(event, elogs_id)

    async def _process_event(self, event: dict, elogs_id):
        event_id = event.get('id')
        page = event.get('sheet_page')
        try:
            assignments = await sheets.get_assignments(page)
        except Exception as e:
            print(f"✗ Escalação CTA #{event_id}: erro lendo planilha: {e}")
            return
        if not assignments:
            return

        guild = self._resolve_guild(event)
        missing: list[str] = []

        # Líder ATUAL de cada party (a coroa do assignLeaders, que pode mudar quando
        # entra uma role de maior prioridade).
        leaders = {}
        for a in assignments:
            if a.get('leader') and a.get('party'):
                leaders[a['party']] = a

        for party in sorted(leaders):
            a = leaders[party]
            name = (a.get('name') or '').strip()
            if not name:
                continue
            name_norm = name.lower()

            stored = await get_party_leader(event_id, party)
            if stored and (stored.get('name_norm') or '') == name_norm:
                continue   # mesmo líder → nada a fazer

            # Houve TROCA (ou 1ª definição) da liderança desta party.
            reg = await get_registration_by_nick(name)

            # 1) Avisa o líder ANTIGO (registrado e diferente) que perdeu o posto.
            if stored and stored.get('user_id') and (stored.get('name_norm') or '') != name_norm:
                old_user = await self._resolve_user(stored['user_id'], guild)
                if old_user is not None:
                    try:
                        await old_user.send(embed=self._build_lost_leader_dm(event, party, name))
                    except discord.HTTPException:
                        pass
                    await asyncio.sleep(0.4)

            # 2) Avisa o NOVO líder (OBRIGATÓRIO — o opt-out NÃO vale pro PM de líder,
            #    e por isso o DM também não leva mais o botão de opt-out).
            if not reg:
                missing.append(name)
            else:
                new_user = await self._resolve_user(reg['user_id'], guild)
                if new_user is not None:
                    try:
                        await new_user.send(embed=self._build_leader_dm_embed(event, a))
                    except discord.Forbidden:
                        missing.append(f"{name} (PM fechada)")
                    except Exception as e:
                        print(f"✗ Escalação CTA #{event_id}: erro mandando PM pro líder {name}: {e}")
                    await asyncio.sleep(0.4)  # gentileza com o rate limit de DM

            # Registra o novo líder da party (mesmo sem cadastro — pra detectar a próxima troca).
            await set_party_leader(event_id, party, name_norm, reg['user_id'] if reg else None)

        await self._post_missing_summary(event, guild, elogs_id, missing)

    async def _resolve_user(self, user_id, guild):
        """Resolve um usuário (membro do guild ou fetch global). None se não achar."""
        u = guild.get_member(user_id) if guild else None
        if u is None:
            try:
                u = await self.bot.fetch_user(user_id)
            except Exception:
                u = None
        return u

    async def _post_missing_summary(self, event, guild, elogs_id, missing):
        event_id = event.get('id')
        key = (database.get_current_guild(), event_id)   # event_id é ambíguo entre servidores
        sig = frozenset(m.lower() for m in missing)
        if sig == self._last_missing.get(key):
            return  # nada mudou desde o último resumo
        self._last_missing[key] = sig

        if not missing or not elogs_id or guild is None:
            return
        channel = guild.get_channel(elogs_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return

        names = sorted(set(missing), key=str.lower)
        shown = names[:50]
        lines = [f"• `{n}`" for n in shown]
        if len(names) > len(shown):
            lines.append(f"… e mais **{len(names) - len(shown)}**.")

        embed = discord.Embed(
            title=f"📋 Escalados sem PM — CTA #{event_id}",
            description=("Estes escalados **não receberam PM** (sem cadastro no "
                         "`/register` ou com a PM fechada):\n\n" + "\n".join(lines)),
            color=EMBED_WARN,
            timestamp=discord.utils.utcnow(),
        )
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            print("✗ Escalação: sem permissão pra postar resumo no economylogs")

    @notify_loop.before_loop
    async def before_notify_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(EscalacaoCog(bot))
