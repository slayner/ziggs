"""
Late Attend — adicionar ao split quem ficou FORA da call.

Mantém uma única embed no canal de late-attend (coluna `channel_bombleaderchat`)
listando os CTAs já ENCERRADOS (/callout) mas com split ainda NÃO finalizado.
Cada CTA tem um botão: council/logistic/caller/offcd abrem um seletor pra
ADICIONAR ao split quem participou mas não estava na call (e por isso não foi
detectado nos snapshots).

Quem é adicionado:
  · entra na attendance do CTA com enlisted=1 e percent = 100 (share cheia)
  · recebe silver normalmente na finalização do split
O enlisted=1 serve só pra continuar vendo QUEM foi adicionado após o evento.
"""
import os
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord import ui
from dotenv import load_dotenv

import database
from database import (
    load_economy_config, update_economy_config,
    get_unfinalized_splits, get_event_by_id,
    enlist_member, unenlist_member,
    get_enlisted_members, get_attendance_counts,
    get_activated_guild_ids,
)
from cogs.economy import has_configured_role
from utils import format_silver, EMBED_INFO, TIME_GAP_BREAK_S, send_err, send_ok
import utils

load_dotenv()
OWNER_ID = int(os.getenv('OWNER_ID', 0))

# Cargos que podem alistar membros
ENLIST_ROLE_KEYS = ('role_council', 'role_logistic', 'role_caller', 'role_offcd')


def _iso_unix(iso_str):
    """ISO string -> unix int (assume UTC se sem tz). None se falhar."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


async def _can_enlist(member: discord.Member) -> bool:
    if member is None:
        return False
    if member.id == OWNER_ID:
        return True
    return await has_configured_role(member, *ENLIST_ROLE_KEYS)


# ==================================================================
# View efêmera de alistamento (aberta ao clicar no botão do CTA)
# ==================================================================
class EnlistMemberSelect(ui.UserSelect):
    def __init__(self, event_id: int):
        super().__init__(
            placeholder="Selecione quem estava com você (fora da zerg)…",
            min_values=1, max_values=25,
        )
        self.event_id = event_id

    async def callback(self, interaction: discord.Interaction):
        ev = await get_event_by_id(self.event_id)
        if not ev or ev.get('split_finalized'):
            await send_err(interaction, "Esse CTA não está mais aberto para adicionar.")
            return

        added, skipped = [], []
        for member in self.values:
            ok = await enlist_member(
                self.event_id, member.id, member.display_name, interaction.user.id,
            )
            (added if ok else skipped).append(member)

        parts = []
        if added:
            parts.append("✅ Adicionado(s) ao split: " + ", ".join(m.mention for m in added))
        if skipped:
            parts.append("↩️ Já estavam no split: " + ", ".join(m.mention for m in skipped))
        # Auto-edita a ephemeral do select (sem mandar mensagem nova).
        await interaction.response.edit_message(
            content="\n".join(parts) or "Nada para adicionar.", view=None,
        )

        cog = interaction.client.cogs.get('SplitsCog')
        if cog:
            try:
                await cog.refresh_splits()
            except Exception as e:
                print(f"✗ Erro refrescando splits: {e}")
        # Alistados saem da lista de "não registraram" no embed do evento
        cta_cog = interaction.client.cogs.get('CTACog')
        if cta_cog:
            try:
                await cta_cog._refresh_event_embed(self.event_id)
            except Exception as e:
                print(f"✗ Erro refrescando embed do evento: {e}")


class UnenlistMemberSelect(ui.UserSelect):
    """Remove do split quem foi ADICIONADO pelo late-attend (não afeta a zerg).
    UserSelect nativo → sem limite de 25, lida com muitos adicionados."""
    def __init__(self, event_id: int):
        super().__init__(
            placeholder="Remover quem foi adicionado por engano…",
            min_values=1, max_values=25,
        )
        self.event_id = event_id

    async def callback(self, interaction: discord.Interaction):
        removed = 0
        for member in self.values:
            try:
                # unenlist_member só remove quem foi ADICIONADO (enlisted=1);
                # escolher um membro da zerg é no-op (não conta).
                if await unenlist_member(self.event_id, member.id):
                    removed += 1
            except Exception as e:
                print(f"✗ splits: erro removendo {member.id}: {e}")
        await interaction.response.edit_message(
            content=f"🗑️ {removed} removido(s) do split.", view=None,
        )
        cog = interaction.client.cogs.get('SplitsCog')
        if cog:
            try:
                await cog.refresh_splits()
            except Exception as e:
                print(f"✗ Erro refrescando splits: {e}")
        cta_cog = interaction.client.cogs.get('CTACog')
        if cta_cog:
            try:
                await cta_cog._refresh_event_embed(self.event_id)
            except Exception as e:
                print(f"✗ Erro refrescando embed do evento: {e}")


class EnlistView(ui.View):
    def __init__(self, event_id: int, enlisted: list):
        super().__init__(timeout=300)
        self.add_item(EnlistMemberSelect(event_id))
        if enlisted:
            self.add_item(UnenlistMemberSelect(event_id))


# ==================================================================
# View persistente da embed: 1 botão por CTA não finalizado
# ==================================================================
class SplitsView(ui.View):
    """View NÃO persistente — recriada toda vez que a embed é atualizada."""

    def __init__(self, splits: list):
        super().__init__(timeout=None)
        for ev in splits[:25]:
            btn = ui.Button(
                label=f"📋 CTA #{ev['id']}"[:80],
                style=discord.ButtonStyle.secondary,
                custom_id=f"splits:enlist:{ev['id']}",
            )
            btn.callback = self._make_callback(ev['id'])
            self.add_item(btn)

    @staticmethod
    def _make_callback(event_id: int):
        async def callback(interaction: discord.Interaction):
            ev = await get_event_by_id(event_id)
            if not ev:
                await send_err(interaction, "Este CTA não existe mais.")
                return
            if ev.get('split_finalized'):
                await send_err(interaction, "O split desse CTA já foi finalizado.")
                return
            if not await _can_enlist(interaction.user):
                await send_err(interaction, "Apenas council, logistic, caller ou off-cd podem adicionar.")
                return

            enlisted = await get_enlisted_members(event_id)
            await interaction.response.send_message(
                f"Adicionar ao split do **CTA #{event_id}** quem ficou fora da call "
                f"(entram com share cheia):",
                view=EnlistView(event_id, enlisted),
                ephemeral=True,
            )
            try:
                await utils.schedule_ephemeral_from_ctx(interaction)
            except Exception:
                pass
        return callback


# ==================================================================
# Cog principal
# ==================================================================
class SplitsCog(commands.Cog, name="SplitsCog"):
    """Mantém a embed de splits não-finalizados no bombleaderchat."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        print("✓ Splits Cog carregada")
        self.bot.loop.create_task(self._refresh_when_ready())

    async def _refresh_when_ready(self):
        await self.bot.wait_until_ready()
        for gid in await get_activated_guild_ids():
            with database.using_guild(gid):
                try:
                    await self.refresh_splits()
                except Exception as e:
                    print(f"✗ Erro inicial do splits [{gid}]: {e}")

    def _find_channel(self, chan_id: int):
        if not chan_id:
            return None
        for g in self.bot.guilds:
            ch = g.get_channel(chan_id)
            if ch:
                return ch
        return None

    # ------------------------------------------------------------------
    # Refresh público — chamado por /end, finalize_split e /deleteevent
    # ------------------------------------------------------------------
    async def refresh_splits(self):
        cfg     = await load_economy_config()
        chan_id = cfg.get('channel_bombleaderchat')
        channel = self._find_channel(chan_id)
        if not channel:
            return

        splits = await get_unfinalized_splits()
        embed  = await self._build_splits_embed(splits)
        view   = SplitsView(splits) if splits else None

        msg_id = cfg.get('splits_message_id')
        msg = None
        if msg_id:
            try:
                msg = await channel.fetch_message(msg_id)
            except discord.NotFound:
                msg = None
            except Exception as e:
                print(f"✗ Erro buscando msg de splits: {e}")

        if msg:
            try:
                await msg.edit(embed=embed, view=view)
                return
            except Exception as e:
                print(f"✗ Erro editando splits; recriando: {e}")

        try:
            new_msg = await channel.send(embed=embed, view=view)
            await update_economy_config({'splits_message_id': new_msg.id})
        except Exception as e:
            print(f"✗ Erro criando msg de splits: {e}")

    # ------------------------------------------------------------------
    # Builder da embed
    # ------------------------------------------------------------------
    async def _build_splits_embed(self, splits: list) -> discord.Embed:
        now = datetime.now(timezone.utc)
        embed = discord.Embed(
            title="🕒  𝐋𝐀𝐓𝐄 𝐀𝐓𝐓𝐄𝐍𝐃",
            color=EMBED_INFO,
        )

        if not splits:
            embed.description = (
                "...\n"
            )
            return embed

        lines = []
        prev_ts = None
        for ev in splits[:25]:
            zerg, enl = await get_attendance_counts(ev['id'])
            start_ts = _iso_unix(ev.get('started_at'))
            when = f"<t:{start_ts}:t>" if start_ts else "?"
            # Gap de 2h+ entre CTAs vizinhos → linha "…" separando os blocos.
            if prev_ts and start_ts and abs(start_ts - prev_ts) >= TIME_GAP_BREAK_S:
                lines.append("**…**")
            prev_ts = start_ts or prev_ts
            split_txt = (f"💰 {format_silver(int(ev.get('repair_value') or 0))}"
                         if ev.get('split_defined') else "💰 *sem split definido*")
            lines.append(
                f"**CTA #{ev['id']}** · 🕒 {when} · {split_txt}\n"
                f"   🪖 na call: **{zerg}** · ➕ adicionados: **{enl}**"
            )

        embed.description = (f"**{len(splits)} CTA(s) aguardando split.**\n\n" + "\n".join(lines))
        return embed


async def setup(bot: commands.Bot):
    await bot.add_cog(SplitsCog(bot))
