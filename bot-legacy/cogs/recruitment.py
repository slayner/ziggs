"""
Recrutamento — ticket clássico em THREAD PRIVADA.

Fluxo:
  1. `/setup` → seção **Recrutamento**: escolhe o canal do painel + os cargos de
     recrutador e publica o painel (botão "📩 Abrir ticket").
  2. O candidato clica → o bot abre uma THREAD PRIVADA só dele, **adiciona toda a
     staff** (cargos de recrutador) e posta um **embed de instruções**. O candidato
     NÃO responde nada por modal — é uma conversa normal no ticket.
  3. Decisão (sem botões), detectada pelo bot:
       · APROVAR  = a staff dá cargo de membro/trial ao dono (on_member_update) →
         arquiva a thread e apaga após 4h.
       · RECUSAR  = a staff DELETA a thread (on_raw_thread_delete) → DM ao candidato.
     O status no banco distingue a deleção da staff (recusa) das deleções que o
     próprio bot faz (aprovação), evitando DM indevida.

Config (canal/cargos/painel) fica toda no `/setup` → Recrutamento.
"""
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands, tasks
from discord import ui

import database
from database import (
    is_server_activated, load_economy_config, update_economy_config,
    get_activated_guild_ids, create_recruitment_ticket,
    get_recruitment_ticket_by_thread, get_open_recruitment_ticket_for_user,
    get_due_recruitment_deletions, update_recruitment_ticket,
)
from utils import make_embed, send_err, send_info
import utils

APPROVED_DELETE_HOURS = 4   # aprovado: arquiva e apaga a thread depois disso
# Estados em que o ticket JÁ acabou — uma deleção nesses não é recusa manual.
TERMINAL_STATES = ('approved', 'rejected', 'cancelled', 'closed')


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _recruiter_role_ids(cfg: dict) -> set:
    """IDs dos cargos de recrutador (vários, CSV em recruiter_roles).
    Cai pro campo legado role_recruiter se a lista estiver vazia."""
    raw = cfg.get('recruiter_roles')
    ids = {int(x) for x in str(raw or '').split(',') if x.strip().isdigit()}
    if not ids and cfg.get('role_recruiter'):
        ids = {int(cfg['role_recruiter'])}
    return ids


# ==================================================================
# Embeds
# ==================================================================
def _panel_embed() -> discord.Embed:
    return make_embed(
        'info',
        title="📩 𝐑𝐄𝐂𝐑𝐔𝐓𝐀𝐌𝐄𝐍𝐓𝐎",
        desc=("Quer entrar na guilda? Clique em **Abrir ticket** abaixo.\n\n"
              "Vou abrir um canal **privado** só seu e chamar os recrutadores pra te atender."),
    )


def _instructions_embed() -> discord.Embed:
    return make_embed(
        'info',
        title="📩 𝐑𝐄𝐂𝐑𝐔𝐓𝐀𝐌𝐄𝐍𝐓𝐎",
        desc=("Este é o seu canal **privado** de recrutamento. Os recrutadores foram "
              "notificados e vão te atender por aqui.\n\n"
              "Pra agilizar, siga o exemplo abaixo com as informações que vamos precisar:\n"
              "\n~~-------------------------------------------------------------------------------------~~\n"
              "» Nick: slayner\n"
              "» Horários: 20-02 UTC\n"
              "» Roles: Martelo; Queda-Santa; Braçadeiras;...\n"
              "» Status: https://i.imgur.com/rG9hcUQ.png\n"
              "» Vídeos:\n"
              "https://streamable.com/2m33ho\n"
              "https://streamable.com/qqxk1b\n"
        ),

        image="https://i.imgur.com/rG9hcUQ.png"
    )


# ==================================================================
# View persistente do painel
# ==================================================================
class RecruitPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="📩 Abrir ticket", style=discord.ButtonStyle.success, custom_id="recruit:open")
    async def open(self, interaction: discord.Interaction, _b: ui.Button):
        cog = interaction.client.cogs.get('RecruitmentCog')
        if cog:
            await cog.open_ticket(interaction)


# ==================================================================
# Cog
# ==================================================================
class RecruitmentCog(commands.Cog, name="RecruitmentCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(RecruitPanelView())
        self.cleanup_loop.start()
        print("✓ Recruitment Cog carregada")

    def cog_unload(self):
        self.cleanup_loop.cancel()

    # ------------------------------------------------------------------
    # Helpers de thread
    # ------------------------------------------------------------------
    async def _archive(self, thread: discord.Thread):
        try:
            await thread.edit(archived=True, reason="Recrutamento: aprovado (apaga em 4h)")
        except discord.HTTPException:
            pass

    async def _delete_thread(self, thread):
        if not isinstance(thread, discord.Thread):
            return
        try:
            await thread.delete(reason="Recrutamento encerrado")
        except discord.HTTPException:
            pass

    async def _resolve_thread(self, thread_id):
        """Resolve a thread por id (cache → fetch). Pega também arquivadas."""
        if not thread_id:
            return None
        thread = self.bot.get_channel(thread_id)
        if thread is None:
            try:
                thread = await self.bot.fetch_channel(thread_id)
            except discord.HTTPException:
                thread = None
        return thread

    async def _grant_temprole(self, member: discord.Member, cfg: dict):
        """Dá o cargo TEMPORÁRIO de recrutamento ao candidato — SÓ se ele não tiver
        nenhum cargo (além do @everyone). Esse cargo libera as salas de recrutamento e
        é removido quando o ticket é aprovado/recusado."""
        rid = cfg.get('role_recruitment')
        if not rid:
            return
        role = member.guild.get_role(rid)
        if role is None:
            return
        # "nenhum cargo" = só possui o @everyone (is_default()) e/ou o cargo de
        # booster do Discord (is_premium_subscriber()) — esses não contam.
        if any(not r.is_default() and not r.is_premium_subscriber() for r in member.roles):
            return
        try:
            await member.add_roles(role, reason="Recrutamento: cargo temporário (ticket aberto)")
        except discord.HTTPException:
            pass

    async def _revoke_temprole(self, guild: discord.Guild, member_id: int, cfg: dict):
        """Remove o cargo temporário de recrutamento do candidato (ticket encerrado)."""
        if guild is None:
            return
        rid = cfg.get('role_recruitment')
        if not rid:
            return
        role = guild.get_role(rid)
        if role is None:
            return
        member = guild.get_member(member_id)
        if member is None or role not in member.roles:
            return
        try:
            await member.remove_roles(
                role, reason="Recrutamento: cargo temporário removido (ticket encerrado)")
        except discord.HTTPException:
            pass

    async def _pull_recruiters(self, thread: discord.Thread, cfg: dict):
        """Adiciona TODA a staff (cargos de recrutador) à thread. Retorna os mentions."""
        mentions = []
        seen = set()
        for rid in _recruiter_role_ids(cfg):
            role = thread.guild.get_role(rid)
            if role is None:
                continue
            mentions.append(role.mention)
            for m in role.members:
                if m.id in seen:
                    continue
                seen.add(m.id)
                try:
                    await thread.add_user(m)
                except discord.HTTPException:
                    pass
        return " ".join(mentions) if mentions else None

    # ==================================================================
    # Abrir ticket
    # ==================================================================
    async def open_ticket(self, interaction: discord.Interaction):
        if interaction.guild is None or not await is_server_activated(interaction.guild.id):
            await send_err(interaction, "Este servidor não está ativado.")
            return

        cfg = await load_economy_config()
        parent_id = cfg.get('channel_recruitment')
        if not parent_id or not _recruiter_role_ids(cfg):
            await send_err(interaction, "O recrutamento ainda não foi configurado. "
                                        "Peça a um admin para configurar em `/setup` → Recrutamento.")
            return

        member = interaction.user
        existing = await get_open_recruitment_ticket_for_user(member.id)
        if existing:
            tid = existing.get('thread_id')
            ref = f"<#{tid}>" if tid else "seu ticket em aberto"
            await send_info(interaction, f"Você já tem um ticket aberto: {ref}.")
            return

        parent = interaction.guild.get_channel(parent_id)
        if not isinstance(parent, discord.TextChannel):
            await send_err(interaction, "O canal de recrutamento configurado é inválido.")
            return

        await interaction.response.defer(ephemeral=True)

        try:
            thread = await parent.create_thread(
                name=f"📩 {member.display_name}"[:100],
                type=discord.ChannelType.private_thread,
                invitable=False,
                reason=f"Recrutamento de {member}",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Não consegui criar a thread (faltam permissões de **Criar Tópicos "
                "Privados** no canal de recrutamento).", ephemeral=True)
            try:
                await utils.schedule_ephemeral_from_ctx(interaction)
            except Exception:
                pass
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ Falha ao criar a thread: {e}", ephemeral=True)
            try:
                await utils.schedule_ephemeral_from_ctx(interaction)
            except Exception:
                pass
            return

        try:
            await thread.add_user(member)
        except discord.HTTPException:
            pass

        # Cargo temporário (libera as salas de recrutamento) — só pra quem não tem cargo.
        await self._grant_temprole(member, cfg)

        await create_recruitment_ticket(interaction.guild.id, member.id, thread.id)

        # Adiciona toda a staff à thread (ser adicionado já notifica cada um) e posta
        # só o embed de instruções — SEM repingar candidato nem cargos de recrutador.
        await self._pull_recruiters(thread, cfg)
        await thread.send(
            embed=_instructions_embed(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await interaction.followup.send(
            f"✅ Seu ticket foi aberto em {thread.mention}.", ephemeral=True)
        try:
            await utils.schedule_ephemeral_from_ctx(interaction)
        except Exception:
            pass

    # ==================================================================
    # Decisão SEM botões:
    #  · APROVAR  = a staff dá cargo de membro/trial ao dono → on_member_update
    #  · RECUSAR  = a staff DELETA a thread → on_raw_thread_delete (DM ao candidato)
    # ==================================================================
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if after.guild is None:
            return
        added = {r.id for r in after.roles} - {r.id for r in before.roles}
        if not added:
            return
        database.set_current_guild(after.guild.id)   # multi-tenant
        cfg = await load_economy_config()
        mid, tid = cfg.get('role_member'), cfg.get('role_trial')
        if not ((mid and mid in added) or (tid and tid in added)):
            return  # não ganhou membro/trial
        ticket = await get_open_recruitment_ticket_for_user(after.id)
        if not ticket:
            return
        await self._approve_ticket(after, ticket, cfg)

    async def _approve_ticket(self, member: discord.Member, ticket: dict, cfg: dict):
        """Dono ganhou cargo de membro/trial → aprovado. Arquiva e apaga em 4h."""
        # Remove o cargo temporário de recrutamento (ticket encerrado).
        await self._revoke_temprole(member.guild, member.id, cfg)
        delete_at = (datetime.now(timezone.utc) + timedelta(hours=APPROVED_DELETE_HOURS)).isoformat()
        await update_recruitment_ticket(ticket['id'], {
            'status': 'approved', 'decided_at': _now_iso(), 'delete_at': delete_at,
        })
        thread = await self._resolve_thread(ticket.get('thread_id'))
        if isinstance(thread, discord.Thread):
            try:
                await thread.send(
                    content=member.mention,
                    embed=make_embed('ok', title="✅ 𝐑𝐄𝐂𝐑𝐔𝐓𝐀𝐌𝐄𝐍𝐓𝐎",
                                     desc=("Você recebeu o cargo — **bem-vindo(a)!** 🎉\n"
                                           f"*Esta thread será apagada em {APPROVED_DELETE_HOURS}h.*")),
                    allowed_mentions=discord.AllowedMentions(users=True))
            except discord.HTTPException:
                pass
            await self._archive(thread)

    @commands.Cog.listener()
    async def on_raw_thread_delete(self, payload: discord.RawThreadDeleteEvent):
        if payload.guild_id is None:
            return
        database.set_current_guild(payload.guild_id)   # multi-tenant
        ticket = await get_recruitment_ticket_by_thread(payload.thread_id)
        if not ticket:
            return
        # Já encerrado (aprovado/recusado/…) = deleção esperada → ignora.
        if ticket['status'] in TERMINAL_STATES:
            return
        # Ticket ainda em aberto e a thread sumiu → recusa MANUAL da staff → DM.
        await update_recruitment_ticket(
            ticket['id'], {'status': 'rejected', 'decided_at': _now_iso(),
                           'closed_at': _now_iso()})
        guild = self.bot.get_guild(payload.guild_id)
        # Remove o cargo temporário de recrutamento (ticket encerrado).
        cfg = await load_economy_config()
        await self._revoke_temprole(guild, ticket['user_id'], cfg)
        member = guild.get_member(ticket['user_id']) if guild else None
        if member is not None:
            try:
                await member.send(embed=make_embed(
                    'err', title="Aplicação recusada",
                    desc=f"Sua aplicação para **{guild.name}** foi recusada."))
            except discord.HTTPException:
                pass  # DM fechada — nada a fazer

    # ==================================================================
    # Limpeza: apaga as threads APROVADAS depois do prazo (4h)
    # ==================================================================
    @tasks.loop(minutes=10)
    async def cleanup_loop(self):
        for gid in await get_activated_guild_ids():
            with database.using_guild(gid):
                try:
                    await self._cleanup_once(gid)
                except Exception as e:
                    print(f"✗ Recrutamento cleanup [{gid}]: {e}")

    async def _cleanup_once(self, gid):
        due = await get_due_recruitment_deletions(_now_iso())
        if not due:
            return
        guild = self.bot.get_guild(gid)
        for t in due:
            thread_id = t.get('thread_id')
            thread = guild.get_thread(thread_id) if (guild and thread_id) else None
            if thread is None and thread_id:
                try:
                    thread = await self.bot.fetch_channel(thread_id)
                except discord.HTTPException:
                    thread = None
            if isinstance(thread, discord.Thread):
                try:
                    await thread.delete(
                        reason=f"Recrutamento: {APPROVED_DELETE_HOURS}h após aprovação")
                except discord.HTTPException:
                    pass
            await update_recruitment_ticket(t['id'], {'delete_at': None, 'closed_at': _now_iso()})

    @cleanup_loop.before_loop
    async def _before_cleanup(self):
        await self.bot.wait_until_ready()

    # ==================================================================
    # Publicação do painel (chamada pelo /setup → seção Recrutamento)
    # ==================================================================
    async def publish_panel(self, channel: discord.TextChannel) -> bool:
        """Publica/republica o painel 'Abrir ticket' no canal. True se publicou."""
        try:
            msg = await channel.send(embed=_panel_embed(), view=RecruitPanelView())
        except discord.HTTPException:
            return False
        await update_economy_config({'recruitment_message_id': msg.id})
        return True


async def setup(bot: commands.Bot):
    await bot.add_cog(RecruitmentCog(bot))
