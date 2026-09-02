import os
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

from utils import make_embed, send_err, send_warn, send_info

load_dotenv()
OWNER_ID = int(os.getenv('OWNER_ID', 0))

import database
from database import (
    is_server_activated,
    log_role_change,
    get_configured_role_ids,
    load_economy_config,
    get_total_events_count,
    get_user_attendance_count,
    get_user_attendance_rank,
    get_user_top_caller,
    get_user_last_event_attended,
    get_user_oldest_role_acquired,
    get_membership_since,
)
from cogs.economy import has_configured_role

LOWATTENDANCE_MAX_ROWS = 15  # quantos membros mostrar na lista de baixa participação


class Management(commands.Cog):
    """
    Gestão da guild: attendance, lowattendance, listeners administrativos.
    Por enquanto contém apenas o listener de mudanças de cargo.
    Os comandos de attendance virão na Fase 3.
    """

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
    # Listener: registra mudanças de cargos CONFIGURADOS
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if after.guild is None:
            return
        database.set_current_guild(after.guild.id)   # multi-tenant: banco do servidor
        # Detectar diff de cargos
        before_ids = {r.id for r in before.roles}
        after_ids  = {r.id for r in after.roles}
        added      = after_ids - before_ids
        removed    = before_ids - after_ids

        if not added and not removed:
            return  # update de outra coisa (nick, status, etc.)

        # Filtrar para considerar APENAS cargos configurados no setup
        configured = await get_configured_role_ids()
        if not configured:
            return  # setup ainda não foi feito → não loga nada

        added_relevant   = added   & configured
        removed_relevant = removed & configured

        if not added_relevant and not removed_relevant:
            return

        user_id   = after.id
        user_name = after.display_name
        guild     = after.guild

        for rid in added_relevant:
            role = guild.get_role(rid)
            role_name = role.name if role else f"Role {rid}"
            await log_role_change(user_id, user_name, rid, role_name, 'added')
            print(f"✓ Role log: {user_name} recebeu {role_name}")

        for rid in removed_relevant:
            role = guild.get_role(rid)
            role_name = role.name if role else f"Role {rid}"
            await log_role_change(user_id, user_name, rid, role_name, 'removed')
            print(f"✓ Role log: {user_name} perdeu {role_name}")

    # ==================================================================
    # ==================  ATTENDANCE COMMANDS  =========================
    # ==================================================================

    @commands.hybrid_command(
        name="attendance",
        description="Mostra estatísticas de participação em eventos CTA",
    )
    @app_commands.guild_only()
    @app_commands.describe(target="Usuário (em branco = você)")
    async def attendance(
        self,
        ctx: commands.Context,
        target: discord.Member = None,
    ):
        target = target or ctx.author

        # Stats lifetime
        total_all          = await get_total_events_count()
        user_all           = await get_user_attendance_count(target.id)
        # Stats últimos 7 dias
        total_7d           = await get_total_events_count(within_days=7)
        user_7d            = await get_user_attendance_count(target.id, within_days=7)
        # Rank + top caller
        rank, _rank_count  = await get_user_attendance_rank(target.id)
        top_caller         = await get_user_top_caller(target.id)

        # Calcular porcentagens (proteção contra divisão por zero)
        pct_all = (user_all / total_all * 100) if total_all > 0 else 0
        pct_7d  = (user_7d  / total_7d  * 100) if total_7d  > 0 else 0

        embed = make_embed('info', title=f"Attendance — {target.display_name}")

        # Lifetime
        if total_all == 0:
            embed.add_field(
                name="📅  Histórico Total",
                value="*Nenhum evento registrado ainda.*",
                inline=False,
            )
        else:
            embed.add_field(
                name="📅  Histórico Total",
                value=(
                    f"Eventos da guild: **{total_all}**\n"
                    f"Você participou: **{user_all}**  ({pct_all:.1f}%)"
                ),
                inline=False,
            )

        # 7 dias
        if total_7d == 0:
            embed.add_field(
                name="🗓️  Últimos 7 dias",
                value="*Nenhum evento neste período.*",
                inline=False,
            )
        else:
            embed.add_field(
                name="🗓️  Últimos 7 dias",
                value=(
                    f"Total: **{total_7d}**\n"
                    f"Você participou: **{user_7d}**  ({pct_7d:.1f}%)"
                ),
                inline=False,
            )

        # Ranking
        if rank is not None:
            embed.add_field(
                name="🏆  Ranking de attendance",
                value=f"`#{rank}`",
                inline=True,
            )
        else:
            embed.add_field(
                name="🏆  Ranking de attendance",
                value="*Sem dados*",
                inline=True,
            )

        # Top caller
        if top_caller:
            caller_id, caller_name, cnt = top_caller
            embed.add_field(
                name="🎙️  Caller mais participado",
                value=f"<@{caller_id}>  ·  **{cnt}** evento(s)",
                inline=True,
            )
        else:
            embed.add_field(
                name="🎙️  Caller mais participado",
                value="*Sem dados*",
                inline=True,
            )

        embed.set_footer(text="Considera ≥ 50% de presença na call como evento participado")
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # /lowattendance
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="lowattendance",
        description="Lista membros com menores participações (últimos 7 dias) — council",
    )
    @app_commands.guild_only()
    async def lowattendance(self, ctx: commands.Context):
        # Permissão (owner bypassa)
        if ctx.author.id != OWNER_ID:
            if not await has_configured_role(ctx.author, 'role_council'):
                await send_err(ctx, "Apenas o council pode usar este comando.")
                return

        cfg = await load_economy_config()
        role_keys = [
            'role_council', 'role_caller', 'role_member',
            'role_content_creator', 'role_logistic',
        ]
        configured_role_ids = {cfg.get(k) for k in role_keys if cfg.get(k)}

        if not configured_role_ids:
            await send_err(ctx, "Nenhum cargo configurado. Rode `/setup` antes de usar "
                                "este comando.")
            return

        guild = ctx.guild
        # Garantir que todos os membros estejam em cache
        if not guild.chunked:
            try:
                await guild.chunk()
            except Exception as e:
                print(f"✗ lowattendance: falha ao carregar membros (chunk): {e}")

        # Candidatos: membros com pelo menos um cargo configurado
        candidates = [
            m for m in guild.members
            if any(r.id in configured_role_ids for r in m.roles)
        ]

        if not candidates:
            await ctx.send("Nenhum membro com cargos configurados foi encontrado.", ephemeral=True)
            try:
                import utils
                await utils.schedule_ephemeral_from_ctx(ctx)
            except Exception:
                pass
            return

        # Avisar que pode demorar (para guilds grandes)
        await ctx.defer()

        threshold_dt = datetime.now(timezone.utc) - timedelta(days=7)
        results = []   # (member, count_7d, last_event_iso)
        filtered_recent = 0

        # Membro/trial são tratados como UM tempo de casa contínuo: a troca entre
        # eles (feita pelo bot) não reseta o relógio.
        mt_ids = [rid for rid in (cfg.get('role_member'), cfg.get('role_trial')) if rid]

        for m in candidates:
            member_role_ids = {r.id for r in m.roles}
            isos = []
            # membro/trial: início do streak contínuo (ignora a troca entre eles)
            if mt_ids and any(rid in member_role_ids for rid in mt_ids):
                ms = await get_membership_since(m.id, mt_ids)
                if ms:
                    isos.append(ms)
            # demais cargos configurados: 'added' mais antigo
            other_ids = [rid for rid in member_role_ids
                         if rid in configured_role_ids and rid not in mt_ids]
            if other_ids:
                oo = await get_user_oldest_role_acquired(m.id, other_ids)
                if oo:
                    isos.append(oo)
            oldest_iso = min(isos) if isos else None

            if oldest_iso:
                # Desqualifica quem ganhou cargo há < 7 dias
                try:
                    oldest_dt = datetime.fromisoformat(oldest_iso.replace(' ', 'T'))
                    if oldest_dt.tzinfo is None:
                        oldest_dt = oldest_dt.replace(tzinfo=timezone.utc)
                    if oldest_dt > threshold_dt:
                        filtered_recent += 1
                        continue
                except (ValueError, TypeError):
                    pass  # data inválida → não filtra (esperado)

            count_7d = await get_user_attendance_count(m.id, within_days=7)
            last_evt = await get_user_last_event_attended(m.id)
            results.append((m, count_7d, last_evt))

        # Ordenar ascendente por count, depois pelo último evento (mais antigo primeiro)
        def sort_key(item):
            _, c, last = item
            # Sem evento → trata como timestamp 0 (vem antes)
            last_ts = 0
            if last:
                try:
                    dt = datetime.fromisoformat(last.replace(' ', 'T'))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    last_ts = dt.timestamp()
                except (ValueError, TypeError):
                    pass  # data inválida → mantém last_ts = 0
            return (c, last_ts)

        results.sort(key=sort_key)
        top_low = results[:LOWATTENDANCE_MAX_ROWS]

        total_7d = await get_total_events_count(within_days=7)

        embed = make_embed('warn', title="Low Attendance — últimos 7 dias")
        embed.description = (
            f"Total de eventos no período: **{total_7d}**\n"
            f"Membros analisados: **{len(results)}**  ·  "
            f"Filtrados (cargo < 7 dias): **{filtered_recent}**"
        )

        if not top_low:
            embed.add_field(
                name="Resultado",
                value="*Nenhum membro elegível para análise.*",
                inline=False,
            )
        else:
            lines = []
            for i, (member, count, last_iso) in enumerate(top_low, start=1):
                if last_iso:
                    try:
                        dt = datetime.fromisoformat(last_iso.replace(' ', 'T'))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        last_str = f"<t:{int(dt.timestamp())}:R>"
                    except Exception:
                        last_str = "*data inválida*"
                else:
                    last_str = "*nunca*"

                word = "evento" if count == 1 else "eventos"
                lines.append(
                    f"`#{i:>2}`  {member.mention}  ·  **{count}** {word}  ·  último: {last_str}"
                )

            # Quebra em múltiplos fields se o conteúdo passar de 1024 chars.
            chunk, chunk_len, first = [], 0, True
            def _flush(name):
                embed.add_field(name=name, value="\n".join(chunk), inline=False)
            for line in lines:
                needed = len(line) + (1 if chunk else 0)
                if chunk_len + needed > 1024:
                    _flush("Ranking" if first else "​")
                    first = False
                    chunk, chunk_len = [], 0
                chunk.append(line)
                chunk_len += needed
            if chunk:
                _flush("Ranking" if first else "​")

        embed.set_footer(text="Considera ≥ 50% de presença na call como evento participado")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Management(bot))
