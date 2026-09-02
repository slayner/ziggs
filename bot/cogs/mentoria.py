"""
Mentoria — um POST de fórum por membro/trial.

Fluxo:
  · /setmentoria define o CANAL DE FÓRUM da mentoria.
  · /register (e /trial) atribui membro OU trial (mutuamente exclusivos) e cria,
    para o usuário, um POST no fórum com o nome dele. Ganhar um cargo remove o
    outro (exclusividade garantida no on_member_update).
  · Se a pessoa perde membro E trial (ou sai do Discord), começa um prazo de 7
    dias. Voltar a ter membro/trial dentro do prazo cancela (e reabre o post se
    já tiver sido arquivado). Passados os 7 dias:
      - o POST é ARQUIVADO/FECHADO (mantém o histórico; reabre se a pessoa voltar);
      - o SALDO é confiscado pro guild bank SÓ se a pessoa estiver sem NENHUM
        cargo (quem mantém qualquer cargo — ex.: mentor — segue na guild).
    O cadastro (nick) é sempre mantido.
"""
import os
import asyncio
from typing import Literal
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv

import database
from database import (
    is_server_activated, load_economy_config, update_economy_config,
    get_registration, forfeit_balance_to_bank, get_activated_guild_ids,
    set_mentoria_channel, set_mentoria_delete_at, get_due_mentoria_deletions,
)
from cogs.economy import has_configured_role
from utils import format_silver, send_err, send_ok, send_warn, send_info
import utils

load_dotenv()
OWNER_ID = int(os.getenv('OWNER_ID', 0))

MENTORIA_GRACE_DAYS  = 7    # prazo antes de arquivar o post / confiscar o saldo
CLEANUP_INTERVAL_MIN = 30


class MentoriaCog(commands.Cog, name="MentoriaCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._post_locks = defaultdict(asyncio.Lock)

    async def cog_load(self):
        self.mentoria_cleanup_loop.start()
        print("✓ Mentoria Cog carregada")

    def cog_unload(self):
        self.mentoria_cleanup_loop.cancel()

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return True
        if not await is_server_activated(ctx.guild.id):
            await send_err(ctx, "Este servidor não está ativado!")
            return False
        return True

    # ==================================================================
    # /trial — troca trial ↔ membro (nunca coexistem)
    # ==================================================================
    @commands.hybrid_command(
        name="trial",
        description="Troca trial↔membro de um usuário — logistic/council/officer/mentor",
    )
    @app_commands.guild_only()
    @app_commands.describe(acao="adicionar (vira trial) ou remover (vira membro)", user="Usuário alvo")
    async def trial(self, ctx: commands.Context,
                    acao: Literal['adicionar', 'remover'], user: discord.Member):
        if ctx.author.id != OWNER_ID and not await has_configured_role(
                ctx.author, 'role_council', 'role_logistic', 'role_officer', 'role_mentor'):
            await send_err(ctx, "Apenas council, logistic, officer ou mentor podem usar "
                                "este comando.")
            return

        cfg = await load_economy_config()
        tid = cfg.get('role_trial')
        if not tid:
            await send_err(ctx, "Cargo de trial não configurado (rode `/setup`).")
            return
        trial_role = ctx.guild.get_role(tid)
        if trial_role is None:
            await send_err(ctx, "Cargo de trial não encontrado no servidor.")
            return

        await ctx.defer(ephemeral=True)

        # adicionar → vira TRIAL (remove membro) | remover → vira MEMBRO (remove trial)
        if acao == 'remover' and trial_role not in user.roles:
            await send_info(ctx, f"{user.mention} não tem o cargo de **trial**.")
            return

        cargo = 'trial' if acao == 'adicionar' else 'membro'
        role_note, post_note = await self.assign_and_post(user, cargo)
        if role_note:
            await send_warn(ctx, f"{role_note}")
            return
        if acao == 'adicionar':
            msg = f"✅ {user.mention} agora é **trial** (cargo de membro removido, se tinha)."
        else:
            msg = f"✅ {user.mention} **saiu do trial** e agora é **membro**."
        msg += f"\n{post_note}"
        await ctx.send(msg, ephemeral=True)
        try:
            await utils.schedule_ephemeral_from_ctx(ctx)
        except Exception:
            pass

    # ==================================================================
    # Atribuição de cargo (exclusivo) + post — usado pelo /register e /trial
    # ==================================================================
    async def assign_exclusive_role(self, member: discord.Member, cargo: str):
        """Atribui membro/trial removendo o outro. Retorna nota (str) ou None se ok."""
        cfg = await load_economy_config()
        target_key = 'role_member' if cargo == 'membro' else 'role_trial'
        other_key  = 'role_trial' if cargo == 'membro' else 'role_member'
        target_id, other_id = cfg.get(target_key), cfg.get(other_key)
        if not target_id:
            return f"⚠️ Cargo de **{cargo}** não configurado (rode `/setup`)."
        guild = member.guild
        target_role = guild.get_role(target_id)
        other_role  = guild.get_role(other_id) if other_id else None
        if target_role is None:
            return f"⚠️ Cargo de **{cargo}** (id {target_id}) não encontrado no servidor."
        try:
            if target_role not in member.roles:
                await member.add_roles(target_role, reason=f"/register: {cargo}")
            if other_role and other_role in member.roles:
                await member.remove_roles(other_role,
                                          reason=f"/register: exclusividade ({cargo})")
            return None
        except discord.Forbidden:
            return ("⚠️ Sem permissão para gerenciar os cargos "
                    "(o cargo do bot precisa estar ACIMA de membro/trial).")
        except discord.HTTPException as e:
            return f"⚠️ Falha ao ajustar cargos: {e}"

    async def assign_and_post(self, member: discord.Member, cargo: str):
        """Fluxo do /register: cargo exclusivo + cancela grace + cria/garante o post."""
        role_note = await self.assign_exclusive_role(member, cargo)
        await set_mentoria_delete_at(member.id, None)   # está ativo de novo
        thread = await self.ensure_post(member)
        if thread is not None:
            post_note = f"🧵 Post de mentoria: {thread.mention}"
        else:
            post_note = ("⚠️ Não criei o post de mentoria — configure em `/setup` → Mentoria e "
                         "confira se o bot pode criar posts no fórum.")
        return role_note, post_note

    # ==================================================================
    # Fórum + post (criar / reabrir / arquivar)
    # ==================================================================
    async def _get_forum(self):
        cfg = await load_economy_config()
        fid = cfg.get('mentoria_forum_id')
        if not fid:
            return None
        ch = self.bot.get_channel(fid)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(fid)
            except discord.HTTPException:
                ch = None
        return ch if isinstance(ch, discord.ForumChannel) else None

    async def _resolve_thread(self, pid: int):
        """Resolve um post do fórum por id (busca também threads ARQUIVADAS)."""
        if not pid:
            return None
        ch = self.bot.get_channel(pid)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(pid)
            except discord.HTTPException:
                ch = None
        return ch

    @staticmethod
    def _has_any_guild_role(member) -> bool:
        """True se o membro tem QUALQUER cargo (além do @everyone). None → False."""
        return member is not None and any(not r.is_default() for r in member.roles)

    async def ensure_post(self, member: discord.Member):
        """Garante o post do membro no fórum: cria se não existe; reabre se arquivado."""
        reg = await get_registration(member.id)
        if not reg:
            return None  # precisa estar cadastrado (nick)
        forum = await self._get_forum()
        if forum is None:
            return None  # /setmentoria não feito / fórum inacessível
        async with self._post_locks[member.id]:
            reg = await get_registration(member.id)   # re-fetch dentro do lock
            pid = reg.get('mentoria_channel_id')      # coluna reaproveitada p/ o post
            if pid:
                thread = await self._resolve_thread(pid)
                if thread is not None:
                    if getattr(thread, 'archived', False) or getattr(thread, 'locked', False):
                        try:
                            await thread.edit(archived=False, locked=False,
                                              reason="Mentoria: voltou — reabrindo post")
                            print(f"✓ Mentoria: post {pid} reaberto.")
                        except discord.HTTPException as e:
                            print(f"✗ Mentoria: erro reabrindo post {pid}: {e}")
                    return thread
            # Cria o post no fórum.
            nick = (reg.get('nick') or member.display_name)
            try:
                created = await forum.create_thread(
                    name=nick[:100],
                    content=f"👋 Post de mentoria de {member.mention} (`{nick}`).",
                    reason=f"Mentoria de {nick}",
                )
                thread = getattr(created, 'thread', created)
            except discord.HTTPException as e:
                print(f"✗ Mentoria: erro criando post de {nick}: {e}")
                return None
            await set_mentoria_channel(member.id, thread.id)
            await set_mentoria_delete_at(member.id, None)
            print(f"✓ Mentoria: post criado para {nick} ({thread.id}).")
            return thread

    async def _schedule_grace(self, user_id: int):
        """Agenda o prazo de 7 dias (arquivar post / confiscar saldo). Só p/ cadastrados."""
        reg = await get_registration(user_id)
        if not reg:
            return
        if reg.get('mentoria_delete_at'):
            return  # já agendado
        when = (datetime.now(timezone.utc) + timedelta(days=MENTORIA_GRACE_DAYS)).isoformat()
        await set_mentoria_delete_at(user_id, when)
        print(f"✓ Mentoria: grace de {MENTORIA_GRACE_DAYS}d (post+saldo) de {user_id} agendado.")

    # ==================================================================
    # Listeners: exclusividade + ciclo de vida
    # ==================================================================
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if after.guild is None:
            return
        database.set_current_guild(after.guild.id)   # multi-tenant: banco do servidor
        before_ids = {r.id for r in before.roles}
        after_ids  = {r.id for r in after.roles}
        added      = after_ids - before_ids
        removed    = before_ids - after_ids
        if not added and not removed:
            return  # update de nick/status (não de cargo)

        cfg = await load_economy_config()
        mid, tid = cfg.get('role_member'), cfg.get('role_trial')
        if not mid and not tid:
            return
        mt_changed     = bool({mid, tid} & (added | removed))
        had_any_before = any(not r.is_default() for r in before.roles)
        has_any_after  = self._has_any_guild_role(after)
        # Reage se membro/trial mudou OU o status de "tem algum cargo" virou.
        if not mt_changed and had_any_before == has_any_after:
            return

        # Exclusividade mútua: o cargo recém-adicionado remove o outro.
        try:
            if tid and tid in added and mid and mid in after_ids:
                role = after.guild.get_role(mid)
                if role:
                    await after.remove_roles(role, reason="Mentoria: trial remove membro")
                after_ids.discard(mid)
            elif mid and mid in added and tid and tid in after_ids:
                role = after.guild.get_role(tid)
                if role:
                    await after.remove_roles(role, reason="Mentoria: membro remove trial")
                after_ids.discard(tid)
        except discord.HTTPException as e:
            print(f"✗ Mentoria: erro aplicando exclusividade: {e}")

        has_now = (mid in after_ids) or (tid in after_ids)

        if has_now:
            # Mentee ativo → cancela grace + cria/reabre o post.
            await set_mentoria_delete_at(after.id, None)
            await self.ensure_post(after)
        else:
            # Não é mentee → agenda grace se tiver post (p/ arquivar) OU sem nenhum cargo.
            reg = await get_registration(after.id)
            has_post = bool(reg and reg.get('mentoria_channel_id'))
            if has_post or not has_any_after:
                await self._schedule_grace(after.id)
            else:
                await set_mentoria_delete_at(after.id, None)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.guild is None:
            return
        database.set_current_guild(member.guild.id)   # multi-tenant: banco do servidor
        # Saiu do Discord → agenda grace (mantém o cadastro/nick).
        await self._schedule_grace(member.id)

    # ==================================================================
    # Limpeza (grace de 7 dias): arquiva o post + confisca saldo se sem cargo
    # ==================================================================
    @tasks.loop(minutes=CLEANUP_INTERVAL_MIN)
    async def mentoria_cleanup_loop(self):
        for gid in await get_activated_guild_ids():
            with database.using_guild(gid):
                try:
                    await self._mentoria_cleanup_once(gid)
                except Exception as e:
                    print(f"✗ Mentoria cleanup [{gid}]: {e}")

    async def _mentoria_cleanup_once(self, gid):
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            due = await get_due_mentoria_deletions(now_iso)
        except Exception as e:
            print(f"✗ Mentoria: erro consultando prazos: {e}")
            return
        cfg = await load_economy_config() if due else {}
        mid, tid = cfg.get('role_member'), cfg.get('role_trial')
        guild = self.bot.get_guild(gid)

        for reg in due:
            uid = reg['user_id']
            pid = reg.get('mentoria_channel_id')

            # Resolve o membro neste servidor (None = saiu do Discord).
            member = guild.get_member(uid) if guild else None
            role_ids = {r.id for r in member.roles} if member else set()
            is_mentee = (mid in role_ids) or (tid in role_ids)
            has_any   = self._has_any_guild_role(member)

            if is_mentee:
                # Voltou a ser mentee antes do cleanup rodar → cancela.
                await set_mentoria_delete_at(uid, None)
                continue

            ok = True
            # 1) Arquiva/fecha o post (NÃO apaga — mantém histórico e o id, p/ reabrir).
            if pid:
                thread = await self._resolve_thread(pid)
                if thread is not None and not getattr(thread, 'archived', False):
                    try:
                        await thread.edit(archived=True, locked=True,
                                          reason="Mentoria: 7 dias sem ser membro/trial")
                        print(f"✓ Mentoria: post {pid} arquivado (grace {MENTORIA_GRACE_DAYS}d).")
                    except discord.HTTPException as e:
                        print(f"✗ Mentoria: erro arquivando post {pid}: {e}")
                        ok = False

            # 2) Confisca o saldo SÓ se estiver sem NENHUM cargo (ou saiu do server).
            if not has_any:
                try:
                    moved = await forfeit_balance_to_bank(uid)
                except Exception as e:
                    print(f"✗ Mentoria: erro transferindo saldo de {uid}: {e}")
                    moved = 0
                if moved > 0:
                    await self._log_forfeit(cfg, uid, reg.get('nick'), moved)
                    print(f"✓ Mentoria: saldo de {uid} ({moved}) → guild bank (sem cargo 7d).")

            # 3) Encerra o agendamento. MANTÉM o post id (arquivado; reabre se voltar).
            if ok:
                await set_mentoria_delete_at(uid, None)

    async def _log_forfeit(self, cfg: dict, uid: int, nick, amount: int):
        chan_id = cfg.get('channel_economylogs')
        if not chan_id:
            return
        ch = self.bot.get_channel(chan_id)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(chan_id)
            except discord.HTTPException:
                return
        try:
            await ch.send(
                f"🏦 Saldo de **{nick or uid}** (<@{uid}>) — **{format_silver(amount)}** — "
                f"transferido para o **guild bank** (7 dias fora da guilda).",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass

    @mentoria_cleanup_loop.before_loop
    async def _before_cleanup(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(MentoriaCog(bot))
