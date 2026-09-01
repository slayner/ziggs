"""Ingestão e mensagens de pagamento de regear via API do bot."""
import asyncio
import os
import time

import aiohttp
import discord
from discord.ext import commands

import http_client
from cogs.general import guild_lang_for
from i18n import t

SITE_URL = os.getenv("BOT_SITE_URL", "").rstrip("/")
API_SECRET = os.getenv("BOT_API_SECRET", "")
PUBLIC_URL = os.getenv("BOT_PUBLIC_URL", "").rstrip("/") or SITE_URL
_IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
_channel_cache: dict[int, tuple[float, dict]] = {}
_CHANNEL_TTL = 120.0
_done_msgs: dict[int, float] = {}
_DONE_TTL = 3600.0


async def _regear_settings(guild_id: int) -> dict:
    now = time.monotonic()
    cached = _channel_cache.get(guild_id)
    if cached and cached[0] > now:
        return cached[1]
    data = await http_client.get_json(f"/bot/guilds/{guild_id}/regear/settings", tag="regears") or {}
    _channel_cache[guild_id] = (now + _CHANNEL_TTL, data)
    return data


def _prune_done() -> None:
    now = time.monotonic()
    for message_id, expires in list(_done_msgs.items()):
        if expires < now:
            _done_msgs.pop(message_id, None)


def _money(value: int | None) -> str:
    return f"{int(value or 0):,}".replace(",", ".")


def _event_link(guild_id: int, request: dict) -> str:
    event_id = request.get("event_id")
    if not event_id:
        return "—"
    title = request.get("event_title") or f"#{event_id}"
    return f"[{title}]({PUBLIC_URL}/events/{guild_id}/{event_id})"


def _payment_embed(guild_id: int, request: dict, lang: str) -> discord.Embed:
    total = request.get("final_total")
    if total is None:
        total = request.get("suggested_total")
    embed = discord.Embed(title=t(lang, "regear_payment_title", request_id=request["id"]), color=discord.Color.gold())
    requester = request.get("requester_name") or "—"
    if request.get("requester_user_id"):
        requester = f"<@{request['requester_user_id']}> ({requester})"
    embed.add_field(name=t(lang, "regear_payment_requester"), value=requester, inline=True)
    embed.add_field(name=t(lang, "regear_payment_event"), value=_event_link(guild_id, request), inline=True)
    roles = request.get("requester_role_ids_snapshot") or []
    participation = request.get("event_participation_snapshot") or {}
    role_name = participation.get("role_name")
    role_value = role_name or " ".join(f"<@&{role_id}>" for role_id in roles) or "—"
    embed.add_field(name=t(lang, "regear_payment_role"), value=role_value, inline=False)
    if request.get("event_id"):
        embed.add_field(name=t(lang, "regear_payment_attendance"), value=f"{participation.get('percent', 0)}%", inline=True)
    embed.add_field(name=t(lang, "regear_payment_parts"), value=str(len(request.get("detected_items") or [])), inline=True)
    embed.add_field(name=t(lang, "regear_payment_silver"), value=_money(total), inline=True)
    embed.add_field(name=t(lang, "regear_payment_identifier"), value=f"`{request['id']}`", inline=True)
    return embed


def _actor_payload(interaction: discord.Interaction) -> dict:
    member = interaction.user
    roles = getattr(member, "roles", [])
    permissions = getattr(member, "guild_permissions", None)
    return {
        "actor_user_id": interaction.user.id,
        "actor_role_ids": [role.id for role in roles if role != interaction.guild.default_role],
        "actor_is_admin": bool(permissions and permissions.administrator),
    }


async def _update_payment(interaction: discord.Interaction, view: "RegearPaymentView", payload: dict) -> dict | None:
    request = await http_client.request_json(
        "PATCH", f"/bot/guilds/{interaction.guild_id}/regear/{view.request['id']}",
        json={**payload, **_actor_payload(interaction)}, queue_on_failure=False,
    )
    if not request or "id" not in request:
        return None
    view.request = request
    message = interaction.message or view.message
    if message is None:
        return None
    view.message = message
    await message.edit(embed=_payment_embed(interaction.guild_id or 0, request, view.lang), view=view)
    return request


class RegearPaymentModal(discord.ui.Modal):
    def __init__(self, view: "RegearPaymentView", action: str):
        super().__init__(title=t(view.lang, f"regear_modal_{action}_title"))
        self.view = view
        self.action = action
        self.value = discord.ui.TextInput(label=t(view.lang, f"regear_modal_{action}_label"), required=True, max_length=100)
        self.add_item(self.value)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            if self.action == "price":
                index_text, value_text = self.value.value.split(",", 1)
                index = int(index_text.strip())
                price = int(value_text.strip().replace(".", "").replace(",", ""))
                items = [dict(item) for item in self.view.request.get("detected_items") or []]
                if index < 1 or index > len(items) or price < 0:
                    raise ValueError
                item = items[index - 1]
                item["unit_price"] = price
                item["total_price"] = price
                payload = {"detected_items": items}
            elif self.action == "attendance":
                payload = {"event_participation_pct": int(self.value.value.strip())}
            else:
                role_name = self.value.value.strip()
                if not role_name:
                    raise ValueError
                payload = {"event_role_name": role_name}
        except ValueError:
            await interaction.response.send_message(t(self.view.lang, "regear_price_format_invalid"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if await _update_payment(interaction, self.view, payload):
            await interaction.followup.send(t(self.view.lang, "regear_payment_updated"), ephemeral=True)
        else:
            await interaction.followup.send(t(self.view.lang, "regear_payment_update_failed"), ephemeral=True)


class RegearPaymentSelect(discord.ui.Select):
    def __init__(self, view: "RegearPaymentView"):
        self.payment_view = view
        lang = view.lang
        super().__init__(
            custom_id="regear-payment-actions",
            placeholder=t(lang, "regear_payment_select_placeholder"),
            options=[
                discord.SelectOption(label=t(lang, "regear_payment_action_price"), value="price"),
                discord.SelectOption(label=t(lang, "regear_payment_action_role"), value="role"),
                discord.SelectOption(label=t(lang, "regear_payment_action_attendance"), value="attendance"),
                discord.SelectOption(label=t(lang, "regear_payment_action_confirm"), value="confirm"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        action = self.values[0]
        if action in {"price", "role", "attendance"}:
            if action in {"role", "attendance"} and not self.payment_view.request.get("event_id"):
                await interaction.response.send_message(t(self.payment_view.lang, "regear_event_action_unavailable"), ephemeral=True)
                return
            await interaction.response.send_modal(RegearPaymentModal(self.payment_view, action))
            return
        await interaction.response.defer(ephemeral=True)
        request = await _update_payment(interaction, self.payment_view, {"status": "paid"})
        await interaction.followup.send(
            t(self.payment_view.lang, "regear_payment_confirmed") if request and request.get("status") == "paid" else t(self.payment_view.lang, "regear_payment_update_failed"),
            ephemeral=True,
        )


class RegearPaymentView(discord.ui.View):
    def __init__(self, request: dict, lang: str, message: discord.Message | None = None):
        super().__init__(timeout=None)
        self.request = request
        self.lang = lang
        self.message = message
        self.add_item(RegearPaymentSelect(self))


class Regears(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            await self._restore_payment_views(guild)

    async def _restore_payment_views(self, guild: discord.Guild) -> None:
        data = await http_client.get_json(f"/bot/guilds/{guild.id}/regear/requests?status=pending", tag="regears") or {}
        for request in data.get("requests") or []:
            message_id = request.get("payment_message_id")
            if not message_id:
                continue
            lang = await guild_lang_for(guild.id)
            self.bot.add_view(RegearPaymentView(request, lang), message_id=int(message_id))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or not message.attachments:
            return
        settings = await _regear_settings(message.guild.id)
        if not settings.get("enabled"):
            return
        channel = message.channel
        extra_channels = {str(item.get("channel_id")) for item in settings.get("extra_channels") or []}
        is_event_thread = isinstance(channel, discord.Thread) and str(channel.parent_id) == str(settings.get("event_thread_parent_channel_id"))
        if not is_event_thread and str(channel.id) not in extra_channels:
            return
        _prune_done()
        if message.id in _done_msgs:
            return
        attachments = [(index, attachment) for index, attachment in enumerate(message.attachments) if attachment.filename.lower().endswith(_IMG_EXT)]
        if not attachments:
            return
        payment_channel_id = settings.get("payment_channel_id")
        if not payment_channel_id:
            print(f"[regears] guilda {message.guild.id}: payment_channel_id não configurado")
            return
        try:
            payment_channel = message.guild.get_channel(int(payment_channel_id)) or await message.guild.fetch_channel(int(payment_channel_id))
        except (ValueError, discord.DiscordException):
            print(f"[regears] guilda {message.guild.id}: payment_channel_id inacessível: {payment_channel_id}")
            return
        if not isinstance(payment_channel, (discord.TextChannel, discord.Thread)):
            return
        await message.add_reaction("⌛")
        lang = await guild_lang_for(message.guild.id)
        role_ids = [role.id for role in getattr(message.author, "roles", []) if role != message.guild.default_role]
        processed = 0
        for index, attachment in attachments:
            try:
                image = await attachment.read()
            except Exception:
                continue
            form = aiohttp.FormData()
            form.add_field("file", image, filename=attachment.filename, content_type=attachment.content_type or "image/png")
            form.add_field("msg_id", str(message.id))
            form.add_field("requester_name", message.author.display_name)
            form.add_field("requester_user_id", str(message.author.id))
            form.add_field("channel_id", str(channel.id))
            form.add_field("parent_channel_id", str(channel.parent_id) if is_event_thread else "")
            form.add_field("attachment_id", str(attachment.id))
            form.add_field("attachment_index", str(index))
            for role_id in role_ids:
                form.add_field("requester_role_ids", str(role_id))
            request = await http_client.post_form(f"/guilds/{message.guild.id}/regear/ingest", form, timeout=20, tag="regears")
            if request is None:
                continue
            try:
                payment_view = RegearPaymentView(request, lang)
                payment_message = await payment_channel.send(embed=_payment_embed(message.guild.id, request, lang), view=payment_view)
                payment_view.message = payment_message
            except discord.DiscordException as error:
                print(f"[regears] falhou postar pagamento {request.get('id')}: {type(error).__name__}: {error}")
                continue
            mapped = await http_client.request_json(
                "PUT", f"/bot/guilds/{message.guild.id}/regear/{request['id']}/payment-message",
                json={"payment_message_id": str(payment_message.id), "payment_message_channel_id": str(payment_channel.id)},
                queue_on_failure=False,
            )
            if mapped is None:
                await payment_message.delete()
                print(f"[regears] falhou salvar mapping do pagamento {request['id']}")
                continue
            processed += 1
        _done_msgs[message.id] = time.monotonic() + _DONE_TTL
        try:
            await message.remove_reaction("⌛", self.bot.user)
        except discord.DiscordException:
            pass
        if processed:
            await message.add_reaction("✅")

    async def _remove_request(self, guild_id: int, request: dict, delete_payment: bool) -> None:
        if delete_payment and request.get("payment_message_id") and request.get("payment_message_channel_id"):
            try:
                payment_channel = self.bot.get_channel(int(request["payment_message_channel_id"])) or await self.bot.fetch_channel(int(request["payment_message_channel_id"]))
                payment_message = await payment_channel.fetch_message(int(request["payment_message_id"]))
                await payment_message.delete()
            except (ValueError, discord.DiscordException):
                pass
        removed = await http_client.request_json(
            "DELETE", f"/bot/guilds/{guild_id}/regear/{request['id']}?actor_user_id={self.bot.user.id}",
            queue_on_failure=False,
        )
        if removed is None:
            print(f"[regears] falhou remover pedido {request['id']}")

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.guild_id is None:
            return
        requests = await http_client.get_json(f"/bot/guilds/{payload.guild_id}/regear/requests?status=pending", tag="regears")
        for request in (requests or {}).get("requests") or []:
            source_deleted = str(request.get("source_message_id")) == str(payload.message_id)
            payment_deleted = str(request.get("payment_message_id")) == str(payload.message_id)
            if source_deleted or payment_deleted:
                await self._remove_request(payload.guild_id, request, delete_payment=source_deleted)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        if payload.guild_id is None or "attachments" not in payload.data:
            return
        attachment_ids = {str(item.get("id")) for item in payload.data.get("attachments") or []}
        requests = await http_client.get_json(f"/bot/guilds/{payload.guild_id}/regear/requests?status=pending", tag="regears")
        for request in (requests or {}).get("requests") or []:
            if str(request.get("source_message_id")) != str(payload.message_id):
                continue
            attachment_id = request.get("source_attachment_id")
            if attachment_id and str(attachment_id) not in attachment_ids:
                await self._remove_request(payload.guild_id, request, delete_payment=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Regears(bot))
