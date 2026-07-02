"""Comandos de economia: /balance /pay /addmoney /removemoney /leaderboard
/economystats — porta simplificada do sistema de economia do bot legado
(bot/cogs/economy.py: saldo por usuário, sem banco da guild nem os outros
rankings, que dependem de dados de CTA que não existem aqui).

Sem banco local: os saldos vivem no backend do site (mesma base de tudo o
resto), o bot só chama /bot/economy/* com o secret compartilhado — igual ao
/avatar, /banner e /register (ver cogs/general.py, cogs/registration.py)."""
import os
import re
from typing import Optional

import aiohttp
import discord
from discord import app_commands, Interaction
from discord.ext import commands

from cogs.general import (
    _MENTION_RE, _ROLE_MENTION_RE, check_command_access, check_command_access_ctx,
    dedupe_members, extract_mention_targets, guild_lang, guild_lang_for, resolve_user_or_guild,
)
from i18n import t
from localization import loc

SITE_URL   = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")

LEADERBOARD_PAGE_SIZE = 10
MAX_TARGETS_SHOWN = 15

# Palavra-chave que representa "todo o valor disponível" no lugar de um número.
_ALL_KEYWORDS = ("all", "tudo")


def _is_all_keyword(s) -> bool:
    return isinstance(s, str) and s.strip().lower() in _ALL_KEYWORDS


# ---- parse/format de prata (porta de bot/utils.py — mesmas regras aceitas) ----

def parse_silver(raw) -> Optional[int]:
    """'1500000' | '2,298,291' | '2.192.281' | '1.5m' | '150k' | '2b' -> int, ou None se inválido."""
    if raw is None:
        return None
    s = str(raw).strip().lower().replace(" ", "")
    if not s:
        return None
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)([kmb])", s)
    if m:
        mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[m.group(2)]
        try:
            value = float(m.group(1).replace(",", ".")) * mult
        except ValueError:
            return None
        return int(value) if value == int(value) else None
    if re.fullmatch(r"\d{1,3}(?:[,.]\d{3})+", s):
        return int(s.replace(",", "").replace(".", ""))
    if re.fullmatch(r"\d+", s):
        return int(s)
    return None


def format_silver(value) -> str:
    return f"{int(value or 0):,}"


def _format_target_list(members: list[discord.Member], limit: int = MAX_TARGETS_SHOWN) -> str:
    """Lista de menções pra mostrar em embed — corta em `limit` pra não estourar
    o texto quando um cargo mencionado tem muita gente (o valor ainda é aplicado
    a todo mundo, isso é só o texto de exibição)."""
    mentions = [m.mention for m in members[:limit]]
    if len(members) > limit:
        mentions.append(f"+{len(members) - limit}")
    return ", ".join(mentions)


def _extract_amount(text: str) -> Optional[int]:
    """Tira as menções do texto e tenta achar um valor de prata em algum dos
    tokens restantes — usado no parse do comando de prefixo, onde não tem
    campo separado pra quantia como no slash command."""
    stripped = _ROLE_MENTION_RE.sub("", _MENTION_RE.sub("", text))
    for token in stripped.split():
        amount = parse_silver(token)
        if amount:
            return amount
    return None


def _set_tx_footer(embed: discord.Embed, lang: str, tx_ids: list[int]) -> None:
    """ID(s) da transação no rodapé — é o que o /undo espera como argumento."""
    ids = [i for i in tx_ids if i is not None]
    if not ids:
        return
    if len(ids) == 1:
        embed.set_footer(text=t(lang, "tx_footer", id=ids[0]))
    else:
        embed.set_footer(text=t(lang, "tx_footer_multi", ids=", ".join(str(i) for i in ids)))


async def _get(path: str) -> Optional[dict]:
    if not SITE_URL or not API_SECRET:
        return None
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(f"{SITE_URL}{path}", headers={"Authorization": f"Bearer {API_SECRET}"},
                             timeout=aiohttp.ClientTimeout(total=5))
            if r.status == 200:
                return await r.json()
    except Exception:
        pass
    return None


async def _post(path: str, body: dict) -> Optional[dict]:
    if not SITE_URL or not API_SECRET:
        return None
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.post(f"{SITE_URL}{path}", json=body, headers={"Authorization": f"Bearer {API_SECRET}"},
                              timeout=aiohttp.ClientTimeout(total=5))
            if r.status == 200:
                return await r.json()
    except Exception:
        pass
    return None


async def _resolve_target(interaction: Interaction, raw: Optional[str], lang: str):
    """resolve_user_or_guild só devolve um Guild com allow_guild=True — aqui é
    sempre False, então o retorno é Member/User/None; guarda mesmo assim."""
    target = await resolve_user_or_guild(interaction, raw, allow_guild=False)
    if target is None or isinstance(target, discord.Guild):
        await interaction.response.send_message(t(lang, "not_found_target", alvo=raw), ephemeral=True)
        return None
    return target


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="balance", description=loc("Mostra o saldo de um usuário (o seu, se ninguém for informado)", "cmd_desc_balance"))
    @app_commands.guild_only()
    @app_commands.describe(alvo=loc("@menção, ID ou nome (padrão: você mesmo)", "opt_desc_balance_alvo"))
    @app_commands.rename(alvo=loc("alvo", "opt_name_alvo"))
    async def balance(self, interaction: Interaction, alvo: Optional[str] = None) -> None:
        if not await check_command_access(interaction, "balance"):
            return
        lang = await guild_lang(interaction)
        target = await _resolve_target(interaction, alvo, lang)
        if target is None:
            return
        data = await _get(f"/bot/economy/balance/{interaction.guild_id}/{target.id}")
        if data is None:
            await interaction.response.send_message(t(lang, "balance_fetch_fail"), ephemeral=True)
            return
        embed = discord.Embed(color=discord.Color.blurple(), title=target.display_name,
                               description=t(lang, "balance_display", balance=format_silver(data["balance"])))
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="pay", description=loc("Transfere prata do seu saldo para outro usuário", "cmd_desc_pay"))
    @app_commands.guild_only()
    @app_commands.describe(alvo=loc("Quem vai receber", "opt_desc_pay_alvo"),
                            quantia=loc("Quanto enviar (ex: 100k, 1.5m, 2,500,000) ou `all`/`tudo`", "opt_desc_pay_quantia"))
    @app_commands.rename(alvo=loc("alvo", "opt_name_alvo"), quantia=loc("quantia", "opt_name_quantia"))
    async def pay(self, interaction: Interaction, alvo: str, quantia: str) -> None:
        if not await check_command_access(interaction, "pay"):
            return
        lang = await guild_lang(interaction)
        target = await _resolve_target(interaction, alvo, lang)
        if target is None:
            return
        if target.id == interaction.user.id:
            await interaction.response.send_message(t(lang, "pay_self"), ephemeral=True)
            return
        if target.bot:
            await interaction.response.send_message(t(lang, "pay_bot"), ephemeral=True)
            return

        if _is_all_keyword(quantia):
            own = await _get(f"/bot/economy/balance/{interaction.guild_id}/{interaction.user.id}")
            if own is None:
                await interaction.response.send_message(t(lang, "balance_fetch_fail"), ephemeral=True)
                return
            if own["balance"] <= 0:
                await interaction.response.send_message(
                    t(lang, "pay_no_balance", balance=format_silver(own["balance"])), ephemeral=True)
                return
            amount = own["balance"]
        else:
            amount = parse_silver(quantia)
            if amount is None or amount <= 0:
                await interaction.response.send_message(t(lang, "invalid_amount_full"), ephemeral=True)
                return

        result = await _post(f"/bot/economy/pay/{interaction.guild_id}", {
            "from_user_id": interaction.user.id, "to_user_id": target.id, "amount": amount,
        })
        if result is None:
            await interaction.response.send_message(t(lang, "pay_process_fail"), ephemeral=True)
            return
        if not result["ok"]:
            await interaction.response.send_message(
                t(lang, "pay_insufficient", balance=format_silver(result["from_balance"])), ephemeral=True)
            return
        embed = discord.Embed(
            color=discord.Color.green(),
            description=t(lang, "pay_success", sender=interaction.user.mention,
                          amount=format_silver(amount), target=target.mention))
        _set_tx_footer(embed, lang, [result.get("transaction_id")])
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="addmoney", description=loc("Adiciona prata ao saldo de um usuário", "cmd_desc_addmoney"))
    @app_commands.guild_only()
    @app_commands.describe(alvo=loc("Usuário alvo", "opt_desc_addmoney_alvo"),
                            quantia=loc("Quanto adicionar (ex: 100k, 1.5m)", "opt_desc_addmoney_quantia"))
    @app_commands.rename(alvo=loc("alvo", "opt_name_alvo"), quantia=loc("quantia", "opt_name_quantia"))
    async def addmoney(self, interaction: Interaction, alvo: str, quantia: str) -> None:
        if not await check_command_access(interaction, "addmoney"):
            return
        lang = await guild_lang(interaction)
        amount = parse_silver(quantia)
        if amount is None or amount <= 0:
            await interaction.response.send_message(t(lang, "invalid_amount"), ephemeral=True)
            return

        # "alvo" aceita várias @menções e/ou @cargos (expandidos pra todos os
        # membros) numa run só — só cai no resolve de alvo único (por nome/ID/
        # apelido) se não achar nenhuma menção no texto.
        targets = extract_mention_targets(interaction.guild, alvo)
        if not targets:
            single = await _resolve_target(interaction, alvo, lang)
            if single is None:
                return
            targets = [single]

        await interaction.response.defer()
        ok_targets, tx_ids = [], []
        for target in targets:
            result = await _post(f"/bot/economy/add/{interaction.guild_id}",
                                  {"discord_user_id": target.id, "amount": amount,
                                   "actor_discord_id": interaction.user.id})
            if result is not None:
                ok_targets.append(target)
                tx_ids.append(result.get("transaction_id"))

        if not ok_targets:
            await interaction.followup.send(t(lang, "add_fail"), ephemeral=True)
            return

        if len(ok_targets) == 1:
            description = t(lang, "add_success", actor=interaction.user.mention,
                             target=ok_targets[0].mention, amount=format_silver(amount))
        else:
            description = t(lang, "add_success_multi", actor=interaction.user.mention,
                             amount=format_silver(amount), count=len(ok_targets),
                             targets=_format_target_list(ok_targets))
        embed = discord.Embed(color=discord.Color.green(), description=description)
        _set_tx_footer(embed, lang, tx_ids)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="removemoney", description=loc("Remove prata do saldo de um usuário (sem valor = remove tudo)", "cmd_desc_removemoney"))
    @app_commands.guild_only()
    @app_commands.describe(alvo=loc("Usuário alvo", "opt_desc_removemoney_alvo"),
                            quantia=loc("Quanto remover (em branco ou `all`/`tudo` = remove tudo)", "opt_desc_removemoney_quantia"))
    @app_commands.rename(alvo=loc("alvo", "opt_name_alvo"), quantia=loc("quantia", "opt_name_quantia"))
    async def removemoney(self, interaction: Interaction, alvo: str, quantia: Optional[str] = None) -> None:
        if not await check_command_access(interaction, "removemoney"):
            return
        lang = await guild_lang(interaction)

        # @cargo entre os alvos pode afetar muita gente de uma vez — pede
        # confirmação em vez de já executar (@menções soltas de usuário, sem
        # cargo, seguem o fluxo de alvo único de sempre, sem confirmação).
        if _ROLE_MENTION_RE.search(alvo):
            targets = extract_mention_targets(interaction.guild, alvo)
            if not targets:
                await interaction.response.send_message(t(lang, "prefix_no_targets"), ephemeral=True)
                return
            if quantia is None or _is_all_keyword(quantia):
                desc = t(lang, "confirm_removemoney_desc_all", count=len(targets), targets=_format_target_list(targets))
            else:
                amt = parse_silver(quantia)
                if amt is None or amt <= 0:
                    await interaction.response.send_message(t(lang, "invalid_amount"), ephemeral=True)
                    return
                desc = t(lang, "confirm_removemoney_desc", amount=format_silver(amt),
                         count=len(targets), targets=_format_target_list(targets))
            embed = discord.Embed(color=discord.Color.gold(), title=t(lang, "confirm_removemoney_title"), description=desc)
            view = ConfirmRemoveMoneyView(author_id=interaction.user.id, guild_id=interaction.guild_id,
                                           targets=targets, quantia=quantia, lang=lang)
            await interaction.response.send_message(embed=embed, view=view)
            return

        target = await _resolve_target(interaction, alvo, lang)
        if target is None:
            return

        if quantia is None or _is_all_keyword(quantia):
            current = await _get(f"/bot/economy/balance/{interaction.guild_id}/{target.id}")
            if current is None:
                await interaction.response.send_message(t(lang, "balance_fetch_fail"), ephemeral=True)
                return
            if current["balance"] <= 0:
                await interaction.response.send_message(
                    t(lang, "remove_no_balance", target=target.mention,
                      balance=format_silver(current["balance"])), ephemeral=True)
                return
            amount, allow_negative = current["balance"], False
        else:
            amount = parse_silver(quantia)
            if amount is None or amount <= 0:
                await interaction.response.send_message(t(lang, "invalid_amount"), ephemeral=True)
                return
            # Valor EXPLÍCITO pode deixar o saldo negativo (punição/empréstimo)
            # — só o "remove tudo" acima é clampeado ao saldo disponível.
            allow_negative = True

        result = await _post(f"/bot/economy/remove/{interaction.guild_id}", {
            "discord_user_id": target.id, "amount": amount, "allow_negative": allow_negative,
            "actor_discord_id": interaction.user.id,
        })
        if result is None:
            await interaction.response.send_message(t(lang, "remove_fail"), ephemeral=True)
            return
        desc = t(lang, "remove_success", actor=interaction.user.mention, target=target.mention,
                 amount=format_silver(result["removed"]))
        if result["balance"] < 0:
            desc += t(lang, "remove_negative_warn")
        embed = discord.Embed(color=discord.Color.red(), description=desc)
        _set_tx_footer(embed, lang, [result.get("transaction_id")])
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="undo", description=loc("Reverte uma transação de economia pelo ID", "cmd_desc_undo"))
    @app_commands.guild_only()
    @app_commands.describe(id=loc("ID da transação a reverter (veja o rodapé do embed original)", "opt_desc_undo_id"))
    async def undo(self, interaction: Interaction, id: int) -> None:
        if not await check_command_access(interaction, "undo"):
            return
        lang = await guild_lang(interaction)
        result = await _post(f"/bot/economy/undo/{interaction.guild_id}/{id}", {})
        if result is None:
            await interaction.response.send_message(t(lang, "undo_fail"), ephemeral=True)
            return
        if not result.get("ok"):
            key = "undo_not_found" if result.get("reason") == "not_found" else "undo_already_undone"
            await interaction.response.send_message(t(lang, key, id=id), ephemeral=True)
            return
        await interaction.response.send_message(embed=discord.Embed(
            color=discord.Color.orange(),
            description=t(lang, "undo_success", id=id, amount=format_silver(result["amount"]))))

    @app_commands.command(name="economystats", description=loc("Mostra um snapshot da economia do servidor", "cmd_desc_economystats"))
    @app_commands.guild_only()
    async def economystats(self, interaction: Interaction) -> None:
        if not await check_command_access(interaction, "economystats"):
            return
        lang = await guild_lang(interaction)
        stats = await _get(f"/bot/economy/stats/{interaction.guild_id}")
        if stats is None:
            await interaction.response.send_message(t(lang, "stats_fail"), ephemeral=True)
            return
        embed = discord.Embed(color=discord.Color.blurple(), title=t(lang, "stats_title"))
        embed.add_field(name=t(lang, "stats_users_field"), value=f"`{stats['user_count']}`", inline=False)
        embed.add_field(name=t(lang, "stats_total_field"), value=f"`{format_silver(stats['balances_sum'])}`", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description=loc("Ranking dos usuários pelo saldo atual de prata", "cmd_desc_leaderboard"))
    @app_commands.guild_only()
    async def leaderboard(self, interaction: Interaction) -> None:
        if not await check_command_access(interaction, "leaderboard"):
            return
        lang = await guild_lang(interaction)
        data = await _get(f"/bot/economy/leaderboard/{interaction.guild_id}?limit={LEADERBOARD_PAGE_SIZE}&offset=0")
        if data is None:
            await interaction.response.send_message(t(lang, "leaderboard_fail"), ephemeral=True)
            return
        if data["total"] == 0:
            await interaction.response.send_message(t(lang, "leaderboard_empty"), ephemeral=True)
            return
        view = LeaderboardView(guild_id=interaction.guild_id, author_id=interaction.user.id, total=data["total"], lang=lang)
        view.update_buttons()
        await interaction.response.send_message(embed=view.build_embed(data["rows"], offset=0), view=view)

    # ------------------------------------------------------------------
    # !addmoney — versão de PREFIXO: sem campos separados de alvo/quantia,
    # então em vez de tentar adivinhar e já executar, monta a interpretação
    # e pede confirmação (botões) antes de tocar em qualquer saldo.
    # ------------------------------------------------------------------
    @commands.command(name="addmoney")
    @commands.guild_only()
    async def addmoney_prefix(self, ctx: commands.Context, *, args: str = "") -> None:
        if not await check_command_access_ctx(ctx, "addmoney"):
            return
        lang = await guild_lang_for(ctx.guild.id)

        targets = dedupe_members(ctx.message.mentions, ctx.message.role_mentions)
        if not targets:
            await ctx.send(t(lang, "prefix_no_targets"))
            return

        amount = _extract_amount(args)
        if amount is None:
            await ctx.send(t(lang, "prefix_no_amount"))
            return

        embed = discord.Embed(
            color=discord.Color.gold(),
            title=t(lang, "confirm_addmoney_title"),
            description=t(lang, "confirm_addmoney_desc", amount=format_silver(amount),
                          count=len(targets), targets=_format_target_list(targets)),
        )
        view = ConfirmAddMoneyView(author_id=ctx.author.id, guild_id=ctx.guild.id,
                                    targets=targets, amount=amount, lang=lang)
        await ctx.send(embed=embed, view=view)


class ConfirmAddMoneyView(discord.ui.View):
    """Botões Confirmar/Cancelar pro !addmoney de prefixo — só quem rodou o
    comando pode decidir; nada é aplicado até o Confirmar."""

    def __init__(self, *, author_id: int, guild_id: int, targets: list[discord.Member], amount: int, lang: str):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.guild_id = guild_id
        self.targets = targets
        self.amount = amount
        self.lang = lang

        confirm_btn = discord.ui.Button(label=t(lang, "confirm_btn"), style=discord.ButtonStyle.success)
        confirm_btn.callback = self._on_confirm
        cancel_btn = discord.ui.Button(label=t(lang, "cancel_btn"), style=discord.ButtonStyle.secondary)
        cancel_btn.callback = self._on_cancel
        self.add_item(confirm_btn)
        self.add_item(cancel_btn)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(t(self.lang, "confirm_only_author"), ephemeral=True)
            return False
        return True

    def _disable_all(self) -> None:
        for item in self.children:
            item.disabled = True

    async def _on_confirm(self, interaction: Interaction) -> None:
        await interaction.response.defer()
        self._disable_all()

        ok_targets, tx_ids = [], []
        for target in self.targets:
            result = await _post(f"/bot/economy/add/{self.guild_id}",
                                  {"discord_user_id": target.id, "amount": self.amount,
                                   "actor_discord_id": self.author_id})
            if result is not None:
                ok_targets.append(target)
                tx_ids.append(result.get("transaction_id"))

        if not ok_targets:
            await interaction.edit_original_response(content=t(self.lang, "add_fail"), embed=None, view=self)
            return

        actor = f"<@{self.author_id}>"
        if len(ok_targets) == 1:
            description = t(self.lang, "add_success", actor=actor, target=ok_targets[0].mention,
                             amount=format_silver(self.amount))
        else:
            description = t(self.lang, "add_success_multi", actor=actor, amount=format_silver(self.amount),
                             count=len(ok_targets), targets=_format_target_list(ok_targets))
        embed = discord.Embed(color=discord.Color.green(), description=description)
        _set_tx_footer(embed, self.lang, tx_ids)
        await interaction.edit_original_response(content=None, embed=embed, view=self)

    async def _on_cancel(self, interaction: Interaction) -> None:
        self._disable_all()
        await interaction.response.edit_message(content=t(self.lang, "prefix_cancelled"), embed=None, view=self)


class ConfirmRemoveMoneyView(discord.ui.View):
    """Botões Confirmar/Cancelar pro /removemoney com @cargo entre os alvos —
    `quantia` já validada (se explícita) antes de a view ser criada; "all"/tudo
    fica sem validar aqui porque cada alvo usa o PRÓPRIO saldo, resolvido só na
    hora de confirmar (não dá pra pré-calcular sem uma chamada por alvo)."""

    def __init__(self, *, author_id: int, guild_id: int, targets: list[discord.Member],
                 quantia: Optional[str], lang: str):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.guild_id = guild_id
        self.targets = targets
        self.quantia = quantia
        self.lang = lang

        confirm_btn = discord.ui.Button(label=t(lang, "confirm_btn"), style=discord.ButtonStyle.danger)
        confirm_btn.callback = self._on_confirm
        cancel_btn = discord.ui.Button(label=t(lang, "cancel_btn"), style=discord.ButtonStyle.secondary)
        cancel_btn.callback = self._on_cancel
        self.add_item(confirm_btn)
        self.add_item(cancel_btn)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(t(self.lang, "confirm_only_author"), ephemeral=True)
            return False
        return True

    def _disable_all(self) -> None:
        for item in self.children:
            item.disabled = True

    async def _on_confirm(self, interaction: Interaction) -> None:
        await interaction.response.defer()
        self._disable_all()

        is_all = self.quantia is None or _is_all_keyword(self.quantia)
        explicit_amount = None if is_all else parse_silver(self.quantia)

        # "all"/tudo só tem o que remover se o saldo ATUAL for positivo — saldo
        # negativo é um estado normal (empréstimo/punição anterior), não um
        # erro, então esses membros são listados como pulados em vez de
        # somem sem explicação do resultado.
        ok_targets, tx_ids, skipped_targets, total_removed = [], [], [], 0
        for member in self.targets:
            if is_all:
                current = await _get(f"/bot/economy/balance/{self.guild_id}/{member.id}")
                balance = current["balance"] if current else 0
                if balance <= 0:
                    skipped_targets.append(member)
                    continue
                amount, allow_negative = balance, False
            else:
                amount, allow_negative = explicit_amount, True

            result = await _post(f"/bot/economy/remove/{self.guild_id}", {
                "discord_user_id": member.id, "amount": amount, "allow_negative": allow_negative,
                "actor_discord_id": self.author_id,
            })
            if result is not None and result.get("transaction_id") is not None:
                ok_targets.append(member)
                tx_ids.append(result["transaction_id"])
                total_removed += result["removed"]

        if not ok_targets:
            content = (t(self.lang, "remove_multi_all_skipped") if skipped_targets
                       else t(self.lang, "remove_fail"))
            await interaction.edit_original_response(content=content, embed=None, view=self)
            return

        description = t(self.lang, "remove_success_multi", actor=f"<@{self.author_id}>",
                         amount=format_silver(total_removed), count=len(ok_targets),
                         targets=_format_target_list(ok_targets))
        if skipped_targets:
            description += t(self.lang, "remove_multi_skipped", targets=_format_target_list(skipped_targets))
        embed = discord.Embed(color=discord.Color.red(), description=description)
        _set_tx_footer(embed, self.lang, tx_ids)
        await interaction.edit_original_response(content=None, embed=embed, view=self)

    async def _on_cancel(self, interaction: Interaction) -> None:
        self._disable_all()
        await interaction.response.edit_message(content=t(self.lang, "prefix_cancelled"), embed=None, view=self)


class LeaderboardView(discord.ui.View):
    """Botões ⏮️ ◀️ ▶️ ⏭️ — só quem usou o comando pode paginar."""

    def __init__(self, *, guild_id: int, author_id: int, total: int, lang: str = "pt"):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.author_id = author_id
        self.total = total
        self.lang = lang
        self.page = 0
        self.max_page = max(0, (total - 1) // LEADERBOARD_PAGE_SIZE)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(t(self.lang, "leaderboard_only_author"), ephemeral=True)
            return False
        return True

    def update_buttons(self):
        self.first_btn.disabled = self.prev_btn.disabled = (self.page == 0)
        self.next_btn.disabled = self.last_btn.disabled = (self.page >= self.max_page)

    def build_embed(self, rows: list, offset: int) -> discord.Embed:
        embed = discord.Embed(color=discord.Color.blurple(), title="Leaderboard")
        if not rows:
            embed.description = t(self.lang, "leaderboard_empty_page")
            return embed
        lines = [f"__{offset + i + 1}.__ <@{r['discord_user_id']}>: {format_silver(r['balance'])}"
                  for i, r in enumerate(rows)]
        embed.description = "\n".join(lines)
        embed.set_footer(text=t(self.lang, "leaderboard_page_footer", page=self.page + 1, max_page=self.max_page + 1))
        return embed

    async def _goto(self, interaction: Interaction, page: int):
        self.page = max(0, min(page, self.max_page))
        offset = self.page * LEADERBOARD_PAGE_SIZE
        data = await _get(f"/bot/economy/leaderboard/{self.guild_id}?limit={LEADERBOARD_PAGE_SIZE}&offset={offset}")
        rows = data["rows"] if data else []
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(rows, offset), view=self)

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary)
    async def first_btn(self, interaction: Interaction, _button: discord.ui.Button):
        await self._goto(interaction, 0)

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: Interaction, _button: discord.ui.Button):
        await self._goto(interaction, self.page - 1)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: Interaction, _button: discord.ui.Button):
        await self._goto(interaction, self.page + 1)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def last_btn(self, interaction: Interaction, _button: discord.ui.Button):
        await self._goto(interaction, self.max_page)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Economy(bot))
