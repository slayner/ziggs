"""
Mentoria — um POST de fórum por membro/trial.

Fluxo:
  · /setmentoria define o CANAL DE FÓRUM da mentoria.
  · A atribuição dos cargos de membro/trial NÃO é mais comando deste bot —
    acontece fora (manual ou pelo bot novo). O on_member_update reage:
    ganhou o cargo → cria/reabre o post; ganhou um dos dois removendo o
    outro → aplica a exclusividade mútua.
  · O cadastro (nick) vem do BACKEND (bot novo, /register de lá) via
    /bot/registration-lookup — este bot só guarda localmente o estado do
    POST (id, grace). Quem não é registrado no bot novo não ganha post.
  · Se a pessoa perde membro E trial (ou sai do Discord), começa um prazo de 7
    dias. Voltar a ter membro/trial dentro do prazo cancela (e reabre o post se
    já tiver sido arquivado). Passados os 7 dias o POST é ARQUIVADO/FECHADO
    (mantém o histórico; reabre se a pessoa voltar).
    (O confisco de saldo pro guild bank foi REMOVIDO — o economy interno não
    existe mais neste bot. Mudança de comportamento comunicada à guild.)
"""
import asyncio
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

import database
import http_client
from database import (
    is_server_activated, load_economy_config,
    get_registration, get_activated_guild_ids, upsert_registration_row,
    set_mentoria_channel, set_mentoria_delete_at, get_due_mentoria_deletions,
)
from utils import send_err

load_dotenv()

MENTORIA_GRACE_DAYS  = 7    # prazo antes de arquivar o post
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
        """Garante o post do membro no fórum: cria se não existe; reabre se arquivado.

        O nick e o gate "está cadastrado" vêm do BACKEND (bot novo). O estado
        do post (id/grace) continua no SQLite local — upsert_registration_row
        garante a row pros usuários que só existem no backend."""
        nick = await http_client.lookup_nick_by_user(member.guild.id, member.id)
        if not nick:
            return None  # não registrado no bot novo → sem post
        forum = await self._get_forum()
        if forum is None:
            return None  # /setmentoria não feito / fórum inacessível
        async with self._post_locks[member.id]:
            await upsert_registration_row(member.id, nick)
            reg = await get_registration(member.id)     # row local (post/grace)
            pid = (reg or {}).get('mentoria_channel_id')
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
        """Agenda o prazo de 7 dias (arquivar post). Só p/ quem tem row local —
        que só existe depois de um ensure_post, i.e. registrado no backend."""
        reg = await get_registration(user_id)
        if not reg:
            return
        if reg.get('mentoria_delete_at'):
            return  # já agendado
        when = (datetime.now(timezone.utc) + timedelta(days=MENTORIA_GRACE_DAYS)).isoformat()
        await set_mentoria_delete_at(user_id, when)
        print(f"✓ Mentoria: grace de {MENTORIA_GRACE_DAYS}d (arquivar post) de {user_id} agendado.")

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
    # Limpeza (grace de 7 dias): arquiva o post
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

            # (2) Confisco de saldo REMOVIDO — economy interno não existe mais
            # neste bot; o saldo vive na plataforma (regear/balance do bot novo).

            # 3) Encerra o agendamento. MANTÉM o post id (arquivado; reabre se voltar).
            if ok:
                await set_mentoria_delete_at(uid, None)

    @mentoria_cleanup_loop.before_loop
    async def _before_cleanup(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(MentoriaCog(bot))
