"""Comandos de economia: /balance /pay /addmoney /removemoney /leaderboard
/economystats — porta simplificada do sistema de economia do bot legado
(bot/cogs/economy.py: saldo por usuário, sem banco da guild nem os outros
rankings, que dependem de dados de CTA que não existem aqui).

Sem banco local: os saldos vivem no backend do site (mesma base de tudo o
resto), o bot só chama /bot/economy/* com o secret compartilhado — igual ao
/avatar, /banner e /register (ver cogs/general.py, cogs/registration.py)."""
import os
import re
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands, Interaction
from discord.ext import commands

import http_client
from cogs.general import (
    _access_status, extract_mention_targets, guild_lang, resolve_user_or_guild,
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


async def _get(path: str) -> Optional[dict]:
    return await http_client.get_json(path, tag="economy")


async def _post(path: str, body: dict) -> Optional[dict]:
    return await http_client.post_json(
        path, body, tag="economy", attempts=2, queue_on_failure=False,
    )


async def _reply(interaction: Interaction, content: Optional[str] = None, **kwargs) -> None:
    """Every economy command acknowledges first, so its response cannot expire."""
    await interaction.edit_original_response(content=content, **kwargs)


async def _check_access(interaction: Interaction, name: str) -> bool:
    if not interaction.guild_id:
        return True
    status, lang = await _access_status(interaction.guild_id, interaction.user, name)
    if status == "ok":
        return True
    key = "cmd_disabled" if status == "disabled" else "no_permission"
    await _reply(interaction, t(lang, key))
    return False


async def _resolve_target(interaction: Interaction, raw: Optional[str], lang: str):
    """resolve_user_or_guild só devolve um Guild com allow_guild=True — aqui é
    sempre False, então o retorno é Member/User/None; guarda mesmo assim."""
    target = await resolve_user_or_guild(interaction, raw, allow_guild=False)
    if target is None or isinstance(target, discord.Guild):
        await _reply(interaction, t(lang, "not_found_target", alvo=raw))
        return None
    return target


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="balance", description=loc("Shows a user's balance (yours, if none given)", "cmd_desc_balance"))
    @app_commands.guild_only()
    @app_commands.describe(alvo=loc("@mention, ID, or name (default: yourself)", "opt_desc_balance_alvo"))
    @app_commands.rename(alvo=loc("user", "opt_name_alvo"))
    async def balance(self, interaction: Interaction, alvo: discord.Member | None = None) -> None:
        await interaction.response.defer()
        if not await _check_access(interaction, "balance"):
            return
        lang = await guild_lang(interaction)
        target = alvo or interaction.user
        data = await _get(f"/bot/economy/balance/{interaction.guild_id}/{target.id}")
        if data is None:
            await _reply(interaction, t(lang, "balance_fetch_fail"))
            return
        embed = discord.Embed(color=discord.Color.blurple(), title=target.display_name,
                               description=t(lang, "balance_display", balance=format_silver(data["balance"])))
        await _reply(interaction, embed=embed)

    @app_commands.command(name="pay", description=loc("Transfers silver from your balance to another user", "cmd_desc_pay"))
    @app_commands.guild_only()
    @app_commands.describe(alvo=loc("Who will receive it", "opt_desc_pay_alvo"),
                            quantia=loc("How much to send (e.g.: 100k, 1.5m, 2,500,000) or `all`/`tudo`", "opt_desc_pay_quantia"))
    @app_commands.rename(alvo=loc("user", "opt_name_alvo"), quantia=loc("amount", "opt_name_quantia"))
    async def pay(self, interaction: Interaction, alvo: discord.Member, quantia: str) -> None:
        await interaction.response.defer()
        if not await _check_access(interaction, "pay"):
            return
        lang = await guild_lang(interaction)
        target = alvo
        if target.id == interaction.user.id:
            await _reply(interaction, t(lang, "pay_self"))
            return
        if target.bot:
            await _reply(interaction, t(lang, "pay_bot"))
            return

        if _is_all_keyword(quantia):
            own = await _get(f"/bot/economy/balance/{interaction.guild_id}/{interaction.user.id}")
            if own is None:
                await _reply(interaction, t(lang, "balance_fetch_fail"))
                return
            if own["balance"] <= 0:
                await _reply(interaction, t(lang, "pay_no_balance", balance=format_silver(own["balance"])))
                return
            amount = own["balance"]
        else:
            amount = parse_silver(quantia)
            if amount is None or amount <= 0:
                await _reply(interaction, t(lang, "invalid_amount_full"))
                return

        result = await _post(f"/bot/economy/pay/{interaction.guild_id}", {
            "from_user_id": interaction.user.id, "to_user_id": target.id, "amount": amount,
            "request_id": str(interaction.id),
        })
        if result is None:
            await _reply(interaction, t(lang, "pay_process_fail"))
            return
        if not result["ok"]:
            await _reply(interaction, t(lang, "pay_insufficient", balance=format_silver(result["from_balance"])))
            return
        embed = discord.Embed(
            color=discord.Color.green(),
            description=t(lang, "pay_success", sender=interaction.user.mention,
                          amount=format_silver(amount), target=target.mention))
        await _reply(interaction, embed=embed)

    @app_commands.command(name="addmoney", description=loc("Adds silver to a user's balance", "cmd_desc_addmoney"))
    @app_commands.guild_only()
    @app_commands.describe(alvo=loc("Target user", "opt_desc_addmoney_alvo"),
                            quantia=loc("How much to add (e.g.: 100k, 1.5m)", "opt_desc_addmoney_quantia"))
    @app_commands.rename(alvo=loc("user", "opt_name_alvo"), quantia=loc("amount", "opt_name_quantia"))
    async def addmoney(self, interaction: Interaction, alvo: str, quantia: str) -> None:
        await interaction.response.defer()
        if not await _check_access(interaction, "addmoney"):
            return
        lang = await guild_lang(interaction)
        amount = parse_silver(quantia)
        if amount is None or amount <= 0:
            await _reply(interaction, t(lang, "invalid_amount"))
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

        ok_targets = []
        for target in targets:
            result = await _post(f"/bot/economy/add/{interaction.guild_id}",
                                  {"discord_user_id": target.id, "amount": amount,
                                   "actor_discord_id": interaction.user.id,
                                   "request_id": f"{interaction.id}:{target.id}"})
            if result is not None:
                ok_targets.append(target)

        if not ok_targets:
            await _reply(interaction, t(lang, "add_fail"))
            return

        if len(ok_targets) == 1:
            description = t(lang, "add_success", actor=interaction.user.mention,
                             target=ok_targets[0].mention, amount=format_silver(amount))
        else:
            description = t(lang, "add_success_multi", actor=interaction.user.mention,
                             amount=format_silver(amount), count=len(ok_targets),
                             targets=_format_target_list(ok_targets))
        embed = discord.Embed(color=discord.Color.green(), description=description)
        await _reply(interaction, embed=embed)

    @app_commands.command(name="removemoney", description=loc("Removes silver from a user's balance (no value = removes everything)", "cmd_desc_removemoney"))
    @app_commands.guild_only()
    @app_commands.describe(alvo=loc("Target user", "opt_desc_removemoney_alvo"),
                            quantia=loc("How much to remove (blank or `all`/`tudo` = removes everything)", "opt_desc_removemoney_quantia"))
    @app_commands.rename(alvo=loc("user", "opt_name_alvo"), quantia=loc("amount", "opt_name_quantia"))
    async def removemoney(self, interaction: Interaction, alvo: discord.Member, quantia: Optional[str] = None) -> None:
        await interaction.response.defer()
        if not await _check_access(interaction, "removemoney"):
            return
        lang = await guild_lang(interaction)
        target = alvo

        if quantia is None or _is_all_keyword(quantia):
            current = await _get(f"/bot/economy/balance/{interaction.guild_id}/{target.id}")
            if current is None:
                await _reply(interaction, t(lang, "balance_fetch_fail"))
                return
            if current["balance"] <= 0:
                await _reply(interaction, t(lang, "remove_no_balance", target=target.mention,
                                            balance=format_silver(current["balance"])))
                return
            amount, allow_negative = current["balance"], False
        else:
            amount = parse_silver(quantia)
            if amount is None or amount <= 0:
                await _reply(interaction, t(lang, "invalid_amount"))
                return
            # Valor EXPLÍCITO pode deixar o saldo negativo (punição/empréstimo)
            # — só o "remove tudo" acima é clampeado ao saldo disponível.
            allow_negative = True

        result = await _post(f"/bot/economy/remove/{interaction.guild_id}", {
            "discord_user_id": target.id, "amount": amount, "allow_negative": allow_negative,
            "actor_discord_id": interaction.user.id,
            "request_id": str(interaction.id),
        })
        if result is None:
            await _reply(interaction, t(lang, "remove_fail"))
            return
        desc = t(lang, "remove_success", actor=interaction.user.mention, target=target.mention,
                 amount=format_silver(result["removed"]))
        if result["balance"] < 0:
            desc += t(lang, "remove_negative_warn")
        embed = discord.Embed(color=discord.Color.red(), description=desc)
        await _reply(interaction, embed=embed)

    @app_commands.command(name="undo", description=loc("Reverts an economy transaction by its ID", "cmd_desc_undo"))
    @app_commands.guild_only()
    @app_commands.describe(id=loc("Transaction ID to revert (see the original embed's footer)", "opt_desc_undo_id"))
    async def undo(self, interaction: Interaction, id: int) -> None:
        await interaction.response.defer()
        if not await _check_access(interaction, "undo"):
            return
        lang = await guild_lang(interaction)
        result = await _post(
            f"/bot/economy/undo/{interaction.guild_id}/{id}",
            {"request_id": str(interaction.id)},
        )
        if result is None:
            await _reply(interaction, t(lang, "undo_fail"))
            return
        if not result.get("ok"):
            key = "undo_not_found" if result.get("reason") == "not_found" else "undo_already_undone"
            await _reply(interaction, t(lang, key, id=id))
            return
        await _reply(interaction, embed=discord.Embed(
            color=discord.Color.orange(),
            description=t(lang, "undo_success", id=id, amount=format_silver(result["amount"]))))

    @app_commands.command(name="economystats", description=loc("Shows a snapshot of the server's economy", "cmd_desc_economystats"))
    @app_commands.guild_only()
    async def economystats(self, interaction: Interaction) -> None:
        await interaction.response.defer()
        if not await _check_access(interaction, "economystats"):
            return
        lang = await guild_lang(interaction)
        stats = await _get(f"/bot/economy/stats/{interaction.guild_id}")
        if stats is None:
            await _reply(interaction, t(lang, "stats_fail"))
            return
        embed = discord.Embed(color=discord.Color.blurple(), title=t(lang, "stats_title"))
        embed.add_field(name=t(lang, "stats_users_field"), value=f"`{stats['user_count']}`", inline=False)
        embed.add_field(name=t(lang, "stats_total_field"), value=f"`{format_silver(stats['balances_sum'])}`", inline=False)
        await _reply(interaction, embed=embed)

    @app_commands.command(name="guildbank", description=loc("Shows the guild bank balance", "cmd_desc_guildbank"))
    @app_commands.guild_only()
    async def guildbank(self, interaction: Interaction) -> None:
        await self._show_guild_bank(interaction)

    @app_commands.command(name="gb", description=loc("Shows the guild bank balance", "cmd_desc_guildbank"))
    @app_commands.guild_only()
    async def gb(self, interaction: Interaction) -> None:
        await self._show_guild_bank(interaction)

    @app_commands.command(name="gbank", description=loc("Shows the guild bank balance", "cmd_desc_guildbank"))
    @app_commands.guild_only()
    async def gbank(self, interaction: Interaction) -> None:
        await self._show_guild_bank(interaction)

    async def _show_guild_bank(self, interaction: Interaction) -> None:
        await interaction.response.defer()
        if not await _check_access(interaction, "guildbank"):
            return
        lang = await guild_lang(interaction)
        data = await _get(f"/bot/guilds/{interaction.guild_id}/bank")
        if data is None:
            await _reply(interaction, t(lang, "bank_fetch_fail"))
            return
        embed = discord.Embed(
            color=discord.Color.blurple(), title=t(lang, "bank_title"),
            description=t(lang, "bank_balance_display", balance=format_silver(data["balance"])),
        )
        await _reply(interaction, embed=embed)

    @app_commands.command(name="addguildmoney", description=loc("Adds silver to the guild bank", "cmd_desc_addguildmoney"))
    @app_commands.guild_only()
    @app_commands.describe(
        quantia=loc("How much to add (e.g.: 100k, 1.5m)", "opt_desc_addguildmoney_quantia"),
        motivo=loc("Short reason for the adjustment (optional)", "opt_desc_bank_motivo"),
    )
    @app_commands.rename(quantia=loc("amount", "opt_name_quantia"), motivo=loc("reason", "opt_name_motivo"))
    async def addguildmoney(self, interaction: Interaction, quantia: str, motivo: Optional[str] = None) -> None:
        await interaction.response.defer()
        if not await _check_access(interaction, "addguildmoney"):
            return
        lang = await guild_lang(interaction)
        amount = parse_silver(quantia)
        if amount is None or amount <= 0:
            await _reply(interaction, t(lang, "invalid_amount"))
            return
        result = await _post(f"/bot/guilds/{interaction.guild_id}/bank/adjust", {
            "amount": amount, "reason": (motivo or None),
            "actor_discord_id": interaction.user.id,
            "request_id": str(interaction.id),
        })
        if result is None:
            await _reply(interaction, t(lang, "bank_fail"))
            return
        embed = discord.Embed(
            color=discord.Color.green(),
            description=t(lang, "bank_add_success", actor=interaction.user.mention,
                          amount=format_silver(amount), balance=format_silver(result["balance"])),
        )
        await _reply(interaction, embed=embed)

    @app_commands.command(name="removeguildmoney", description=loc("Removes silver from the guild bank", "cmd_desc_removeguildmoney"))
    @app_commands.guild_only()
    @app_commands.describe(
        quantia=loc("How much to remove (e.g.: 100k, 1.5m)", "opt_desc_removeguildmoney_quantia"),
        motivo=loc("Short reason for the adjustment (optional)", "opt_desc_bank_motivo"),
    )
    @app_commands.rename(quantia=loc("amount", "opt_name_quantia"), motivo=loc("reason", "opt_name_motivo"))
    async def removeguildmoney(self, interaction: Interaction, quantia: str, motivo: Optional[str] = None) -> None:
        await interaction.response.defer()
        if not await _check_access(interaction, "removeguildmoney"):
            return
        lang = await guild_lang(interaction)
        amount = parse_silver(quantia)
        if amount is None or amount <= 0:
            await _reply(interaction, t(lang, "invalid_amount"))
            return
        result = await _post(f"/bot/guilds/{interaction.guild_id}/bank/adjust", {
            "amount": -amount, "reason": (motivo or None),
            "actor_discord_id": interaction.user.id,
            "request_id": str(interaction.id),
        })
        if result is None:
            await _reply(interaction, t(lang, "bank_fail"))
            return
        embed = discord.Embed(
            color=discord.Color.red(),
            description=t(lang, "bank_remove_success", actor=interaction.user.mention,
                          amount=format_silver(amount), balance=format_silver(result["balance"])),
        )
        await _reply(interaction, embed=embed)

    @app_commands.command(name="leaderboard", description=loc("Ranking of users by current silver balance", "cmd_desc_leaderboard"))
    @app_commands.guild_only()
    async def leaderboard(self, interaction: Interaction) -> None:
        await interaction.response.defer()
        if not await _check_access(interaction, "leaderboard"):
            return
        lang = await guild_lang(interaction)
        data = await _get(f"/bot/economy/leaderboard/{interaction.guild_id}?limit={LEADERBOARD_PAGE_SIZE}&offset=0")
        if data is None:
            await _reply(interaction, t(lang, "leaderboard_fail"))
            return
        if data["total"] == 0:
            await _reply(interaction, t(lang, "leaderboard_empty"))
            return
        view = LeaderboardView(guild_id=interaction.guild_id, author_id=interaction.user.id, total=data["total"], lang=lang)
        view.update_buttons()
        await _reply(interaction, embed=view.build_embed(data["rows"], offset=0), view=view)

    @app_commands.command(name="transactions", description=loc("Shows your transaction history with pagination", "cmd_desc_transactions"))
    @app_commands.guild_only()
    @app_commands.describe(alvo=loc("User to check (default: yourself)", "opt_desc_transactions_alvo"))
    @app_commands.rename(alvo=loc("user", "opt_name_alvo"))
    async def transactions(self, interaction: Interaction, alvo: discord.Member | None = None) -> None:
        await interaction.response.defer()
        if not await _check_access(interaction, "transactions"):
            return
        lang = await guild_lang(interaction)
        target = alvo or interaction.user
        data = await _get(
            f"/bot/economy/transactions/{interaction.guild_id}/{target.id}"
            f"?limit={TX_PAGE_SIZE}&offset=0"
        )
        if data is None:
            await _reply(interaction, t(lang, "tx_fetch_fail"))
            return
        if data["total"] == 0:
            embed = discord.Embed(
                color=discord.Color.blurple(),
                title=t(lang, "tx_title", user=target.display_name),
                description=t(lang, "tx_empty"),
            )
            await _reply(interaction, embed=embed)
            return
        view = TransactionsView(
            guild_id=interaction.guild_id, target_id=target.id,
            target_name=target.display_name,
            author_id=interaction.user.id, total=data["total"], lang=lang,
        )
        view.update_buttons()
        await _reply(interaction, embed=view.build_embed(data, offset=0), view=view)


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
        await interaction.response.defer()
        self.page = max(0, min(page, self.max_page))
        offset = self.page * LEADERBOARD_PAGE_SIZE
        data = await _get(f"/bot/economy/leaderboard/{self.guild_id}?limit={LEADERBOARD_PAGE_SIZE}&offset={offset}")
        rows = data["rows"] if data else []
        self.update_buttons()
        await interaction.edit_original_response(embed=self.build_embed(rows, offset), view=self)

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


TX_PAGE_SIZE = 5

_KIND_LABELS = {
    "pay": "pay",
    "add": "add",
    "remove": "remove",
    "forfeit": "forfeit",
    "event_payout": "event_payout",
    "event_deficit": "event_deficit",
    "bank_adjust": "bank_adjust",
}


def _format_tx_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y %H:%M")
    except (ValueError, TypeError):
        return "—"


class TransactionsView(discord.ui.View):
    """Paginacao do /transactions — lista todas as mudancas de saldo."""

    def __init__(self, *, guild_id: int, target_id: int, target_name: str,
                 author_id: int, total: int, lang: str = "pt"):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.target_id = target_id
        self.target_name = target_name
        self.author_id = author_id
        self.total = total
        self.lang = lang
        self.page = 0
        self.max_page = max(0, (total - 1) // TX_PAGE_SIZE)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                t(self.lang, "tx_only_author"), ephemeral=True)
            return False
        return True

    def update_buttons(self):
        self.first_btn.disabled = self.prev_btn.disabled = (self.page == 0)
        self.next_btn.disabled = self.last_btn.disabled = (self.page >= self.max_page)

    def build_embed(self, data: dict, offset: int) -> discord.Embed:
        txs = data.get("transactions", [])
        balance = data.get("balance", 0)
        total_earned = data.get("total_earned", 0)
        embed = discord.Embed(
            color=discord.Color.blurple(),
            title=t(self.lang, "tx_title", user=self.target_name),
        )
        if not txs:
            embed.description = t(self.lang, "tx_empty_page")
            return embed

        for tx in txs:
            kind = tx["kind"]
            direction = tx["direction"]
            amount = tx["amount"]
            kind_label = t(self.lang, f"tx_kind_{_KIND_LABELS.get(kind, kind)}")
            sign = "+" if direction == "in" else ("-" if direction == "out" else "")

            field_title = f"#{tx['id']} — {kind_label}"
            parts: list[str] = []
            parts.append(f"**{sign}{format_silver(amount)}**")

            cp = tx.get("counterparty_albion_name") or tx.get("counterparty_name")
            if cp:
                cp_label = t(self.lang, "tx_counterparty")
                parts.append(f"{cp_label}: {cp}")

            actor = tx.get("actor_name")
            if actor:
                actor_label = t(self.lang, "tx_actor")
                parts.append(f"{actor_label}: {actor}")

            if tx.get("event_id"):
                ev_title = tx.get("event_title") or f"#{tx['event_id']}"
                ev_ch = tx.get("event_channel_id")
                ev_msg = tx.get("event_message_id")
                if ev_ch and ev_msg:
                    jump = f"https://discord.com/channels/{self.guild_id}/{ev_ch}/{ev_msg}"
                    parts.append(f"{t(self.lang, 'tx_event')}: [{ev_title}]({jump})")
                else:
                    parts.append(f"{t(self.lang, 'tx_event')}: {ev_title}")

            if tx.get("undone"):
                parts.append(f"*{t(self.lang, 'tx_undone')}*")

            parts.append(f"`{_format_tx_date(tx.get('created_at'))}`")

            embed.add_field(
                name=field_title,
                value="\n".join(parts),
                inline=False,
            )

        embed.add_field(
            name=t(self.lang, "tx_balance_field"),
            value=f"`{format_silver(balance)}`",
            inline=True,
        )
        embed.add_field(
            name=t(self.lang, "tx_total_earned_field"),
            value=f"`{format_silver(total_earned)}`",
            inline=True,
        )
        embed.set_footer(text=t(self.lang, "tx_page_footer",
                                 page=self.page + 1, max_page=self.max_page + 1,
                                 total=self.total))
        return embed

    async def _goto(self, interaction: Interaction, page: int):
        await interaction.response.defer()
        self.page = max(0, min(page, self.max_page))
        offset = self.page * TX_PAGE_SIZE
        data = await _get(
            f"/bot/economy/transactions/{self.guild_id}/{self.target_id}"
            f"?limit={TX_PAGE_SIZE}&offset={offset}"
        )
        if data is None:
            data = {"transactions": [], "balance": 0, "total_earned": 0}
        self.update_buttons()
        await interaction.edit_original_response(
            embed=self.build_embed(data, offset), view=self)

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
