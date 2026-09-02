"""Fila oficial de aprovação de avatar/banner dos perfis públicos."""
from __future__ import annotations

import io
import os
import asyncio

import discord
from discord.ext import commands, tasks

import http_client
from cogs._discord_timeout import SKIP_EXC, dtimeout


GUILD_ID = int(os.getenv("PROFILE_MODERATION_GUILD_ID", "0") or 0)
CHANNEL_ID = int(os.getenv("PROFILE_MODERATION_CHANNEL_ID", "0") or 0)


async def _reply(interaction: discord.Interaction, text: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


def _submission_id(message: discord.Message | None) -> int | None:
    if not message or not message.embeds or not message.embeds[0].footer.text:
        return None
    marker = message.embeds[0].footer.text
    if not marker.startswith("submission:"):
        return None
    try:
        return int(marker.split(":", 1)[1])
    except ValueError:
        return None


class ProfileModerationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @staticmethod
    async def _finalize(message: discord.Message, approved: bool, member: discord.Member) -> None:
        embed = message.embeds[0]
        embed.title = "Imagem aprovada" if approved else "Imagem recusada"
        embed.color = discord.Color.green() if approved else discord.Color.red()
        embed.add_field(
            name="Decisão",
            value=f"{'Aprovada' if approved else 'Recusada'} por {member.mention}",
            inline=False,
        )
        if not approved:
            embed.add_field(
                name="Consequência",
                value="Todas as imagens foram removidas e novos uploads ficaram bloqueados por 90 dias.",
                inline=False,
            )
        for attempt in range(3):
            try:
                await dtimeout(message.edit(embed=embed, view=None))
                return
            except discord.NotFound:
                return
            except SKIP_EXC as error:
                if attempt == 2:
                    print(f"[profile_moderation] não consegui finalizar mensagem {message.id}: {error}")
                    return
                await asyncio.sleep(1)

    async def _decide(self, interaction: discord.Interaction, decision: str) -> None:
        member = interaction.user
        if (
            interaction.guild is None
            or interaction.guild.id != GUILD_ID
            or not isinstance(member, discord.Member)
            or not member.guild_permissions.administrator
        ):
            await _reply(interaction, "Apenas administradores do servidor oficial podem revisar imagens.")
            return
        submission_id = _submission_id(interaction.message)
        if submission_id is None:
            await _reply(interaction, "Não consegui identificar este upload.")
            return
        await interaction.response.defer(ephemeral=True)
        result = await http_client.post_json(
            f"/bot/profile-moderation/{submission_id}/decision",
            {"decision": decision, "actor_id": member.id},
            tag="profile_moderation", attempts=1, queue_on_failure=False,
        )
        if result is None:
            await interaction.followup.send("Este upload já foi revisado ou o backend não respondeu.", ephemeral=True)
            return

        approved = decision == "approve"
        await self._finalize(interaction.message, approved, member)
        if not approved and interaction.channel:
            for message_id in result.get("discord_message_ids", []):
                if int(message_id) == interaction.message.id:
                    continue
                try:
                    sibling = await dtimeout(interaction.channel.fetch_message(int(message_id)))
                    await self._finalize(sibling, False, member)
                except SKIP_EXC:
                    pass
        await interaction.followup.send("Decisão aplicada.", ephemeral=True)

    @discord.ui.button(label="Aprovar", style=discord.ButtonStyle.success,
                       custom_id="profile_media:approve:v1")
    async def approve(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._decide(interaction, "approve")

    @discord.ui.button(label="Recusar e bloquear 90 dias", style=discord.ButtonStyle.danger,
                       custom_id="profile_media:reject:v1")
    async def reject(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._decide(interaction, "reject")


class ProfileModeration(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(ProfileModerationView())
        if GUILD_ID and CHANNEL_ID:
            self.poll.start()
        else:
            print("[profile_moderation] PROFILE_MODERATION_GUILD_ID/CHANNEL_ID não configurados")

    async def cog_unload(self) -> None:
        self.poll.cancel()

    async def _post(self, channel: discord.TextChannel, item: dict) -> None:
        # Reconcilia o pequeno canal oficial antes de postar: cobre crash entre
        # channel.send() e o bind do message_id sem criar mensagem duplicada.
        try:
            async for message in channel.history(limit=100):
                if _submission_id(message) == int(item["id"]):
                    await http_client.post_json(
                        f"/bot/profile-moderation/{item['id']}/message",
                        {"message_id": message.id}, tag="profile_moderation",
                        attempts=2, queue_on_failure=False,
                    )
                    return
        except (discord.HTTPException, asyncio.TimeoutError):
            return
        data = await http_client.get_bytes(
            f"/bot/profile-moderation/{item['id']}/image", tag="profile_moderation",
        )
        if data is None:
            return
        if len(data) > channel.guild.filesize_limit:
            print(f"[profile_moderation] upload #{item['id']} excede limite do Discord")
            return
        filename = item.get("image_name") or f"{item['kind']}.jpg"
        embed = discord.Embed(
            title="Nova imagem de perfil para revisão",
            description=f"Usuário: <@{item['user_id']}> (`{item['username']}`)\nTipo: **{item['kind']}**",
            color=discord.Color.gold(),
        )
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(text=f"submission:{item['id']}")
        message = await dtimeout(channel.send(
            embed=embed,
            file=discord.File(io.BytesIO(data), filename=filename),
            view=ProfileModerationView(),
        ))
        bound = await http_client.post_json(
            f"/bot/profile-moderation/{item['id']}/message",
            {"message_id": message.id}, tag="profile_moderation",
            attempts=2, queue_on_failure=False,
        )
        if bound is None:
            await dtimeout(message.delete())

    @tasks.loop(seconds=10)
    async def poll(self) -> None:
        guild = self.bot.get_guild(GUILD_ID)
        channel = guild.get_channel(CHANNEL_ID) if guild else None
        if not isinstance(channel, discord.TextChannel):
            return
        data = await http_client.get_json("/bot/profile-moderation/pending", tag="profile_moderation")
        if data is None:
            return
        for item in data.get("submissions", []):
            try:
                message_id = item.get("discord_message_id")
                if message_id:
                    await dtimeout(channel.fetch_message(int(message_id)))
                    continue
                await self._post(channel, item)
            except discord.NotFound:
                await http_client.post_json(
                    f"/bot/profile-moderation/{item['id']}/message",
                    {"message_id": None}, tag="profile_moderation",
                    queue_on_failure=False,
                )
            except SKIP_EXC as error:
                print(f"[profile_moderation] Discord: {type(error).__name__}: {error}")

    @poll.before_loop
    async def before_poll(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProfileModeration(bot))
