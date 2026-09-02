"""
Sistema de CTA do Albion Online.

Fluxo:
1. /cta <time>       (council/logistic/caller) cria um evento. Pode haver vários,
                     desde que em horários diferentes. Se o horário ainda não chegou,
                     a CTA fica AGENDADA; quando o timer inicia, vira EM ANDAMENTO.
2. Loop de snapshot a cada 30s grava presenças no canal de voz CTA, mas SÓ para
   CTAs em andamento (agendadas não contam presença até iniciar).
3. /callout          (caller original ou council/logistic) — menu de seleção:
                     · CTA em andamento → pergunta se teve regear (finaliza).
                     · CTA agendada     → cancela sem gerar evento.
   Aliases (prefix): cancelcta, end.
4. Ao confirmar, calcula %, cria threads e posta embed final com botões.
5. Embed se auto-atualiza a cada 5min para manter os botões interativos vivos.

Botões de split/finalizar ficam como stubs — implementados na Fase 5.
"""
import os
import io
import re
import asyncio
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands, tasks
from discord import app_commands, ui
from dotenv import load_dotenv

import database
from database import (
    is_server_activated, get_activated_guild_ids,
    load_economy_config,
    create_cta_event, get_active_event, get_active_ctas, get_event_by_id,
    get_non_finalized_events,
    add_snapshot, end_cta_event,
    get_event_attendances, get_enlisted_user_ids,
    update_attendance_percent, delete_attendance,
    update_event_meta, get_event_by_message_id,
    get_nodes_near,
    set_event_split, set_event_tab_image, mark_event_split_finalized, set_attendance_silver,
    add_user_money, add_guild_bank,
    record_event_payouts, get_event_payouts, revert_user_money, remove_guild_bank,
    set_event_captured_nodes, get_event_captured_nodes,
    delete_event_completely,
    get_non_pingers,
    get_due_sheet_deletions,
    mark_event_trials, recompute_trial_discount,
    get_log_submissions, get_battle_players,
    get_function_log_users,
)
from cogs.economy import has_configured_role
from cogs.nodes import node_scout_weight, load_node_map, node_emoji_of
from utils import (parse_silver, format_silver, make_embed, bold_number, EMBED_INFO, EMBED_OK,
                   send_ok, send_err, send_warn, send_info,
                   guild_filesize_limit, human_size)
import utils
import sheets

load_dotenv()
OWNER_ID = int(os.getenv('OWNER_ID', 0))

# ------------------------------------------------------------------
# Constantes
# ------------------------------------------------------------------
SNAPSHOT_INTERVAL_SECONDS = 30      # cada quanto tira snapshot de quem está no voice
# Só quem tem um destes cargos (configurados no /setup) conta presença no voice CTA.
SNAPSHOT_ELIGIBLE_ROLE_KEYS = (
    'role_lead', 'role_council', 'role_caller', 'role_member',
    'role_trial', 'role_mentor', 'role_officer', 'role_content_creator',
)
EMBED_UPDATE_INTERVAL_SECONDS = 300  # 5min — reedita o embed pra manter interactions vivos
NODES_NEAR_THRESHOLD_SECONDS = 1800  # ±30min para procurar nodes próximos do end
SHEET_DELETE_DELAY_HOURS = 2         # CTA em andamento: apaga a planilha 2h após /callout
SHEET_CLEANUP_INTERVAL_SECONDS = 300  # 5min — verifica planilhas com exclusão vencida
PRE_START_MOVE_SECONDS = 600         # 10min antes de um CTA agendado começar: move voice
VOICE_MOVE_BATCH = 5                 # move membros de voice de 5 em 5 (limite do Discord)
EMBED_FIELD_LIMIT = 1024             # limite de chars no value de um field (regra do Discord)
REGEAR_PING_DELETE_SECONDS = 90      # quanto tempo a mensagem temporária de ping de regear fica

# Emojis dos nodes: os TIPOS são por servidor (tabela node_defs). Carregamos o
# node_map (nome → {emoji, weight}) sob demanda via load_node_map() e resolvemos o
# emoji com node_emoji_of() — fonte única compartilhada com a cog de nodes.


# ------------------------------------------------------------------
# Helpers de tempo
# ------------------------------------------------------------------
def parse_utc_time(raw: str):
    """Aceita 'HH:MM' ou 'HH:MM:SS'. Retorna datetime UTC do dia atual (próximo dia se já passou)."""
    s = raw.strip()
    m = re.fullmatch(r'(\d{1,2}):(\d{2})(?::(\d{2}))?', s)
    if not m:
        return None
    h, mn = int(m.group(1)), int(m.group(2))
    sc = int(m.group(3) or 0)
    if h > 23 or mn > 59 or sc > 59:
        return None
    now = datetime.now(timezone.utc)
    dt  = now.replace(hour=h, minute=mn, second=sc, microsecond=0)
    # Se o horário escolhido for mais de 15min no passado, joga pra amanhã
    if dt < (now - timedelta(minutes=15)):
        dt += timedelta(days=1)
    return dt


def _round_down_to_15(dt: datetime) -> datetime:
    """Arredonda pra baixo no múltiplo de 15min mais próximo."""
    minute = (dt.minute // 15) * 15
    return dt.replace(minute=minute, second=0, microsecond=0)


def _event_started_dt(event) -> datetime | None:
    """datetime UTC de início do evento, ou None se não der pra parsear."""
    raw = event.get('started_at')
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_scheduled(event, now: datetime | None = None) -> bool:
    """
    True se o CTA está AGENDADO (started_at no futuro e ainda não encerrado).
    Se started_at não parsear, trata como 'em andamento' (conservador).
    """
    if event.get('ended_at'):
        return False
    dt = _event_started_dt(event)
    if dt is None:
        return False
    now = now or datetime.now(timezone.utc)
    return dt > now


def event_status_label(event, now: datetime | None = None) -> str:
    """Rótulo curto do estado do CTA para embeds/menus."""
    if event.get('ended_at'):
        return "encerrado"
    return "agendado" if is_scheduled(event, now) else "em andamento"


def _parse_poke_file(text: str) -> list[tuple[str, str]]:
    """Parse do menu da guilda copiado do jogo (colunas 'Character Name',
    'Last Seen', 'Roles'). Aceita separador TAB/;/, e valores entre aspas.
    Retorna [(character_name, last_seen)]. Se não houver cabeçalho reconhecível,
    assume coluna 0 = nome e coluna 1 = last seen."""
    lines = [l for l in (text or '').splitlines() if l.strip()]
    if not lines:
        return []
    header = lines[0]
    sep = '\t' if '\t' in header else (';' if ';' in header else ',')

    def cells(line: str) -> list[str]:
        return [c.strip().strip('"').strip() for c in line.split(sep)]

    head = [c.lower() for c in cells(header)]
    name_i, seen_i, has_header = 0, 1, False
    for i, c in enumerate(head):
        if 'character' in c or c == 'name':
            name_i, has_header = i, True
        elif 'seen' in c:
            seen_i, has_header = i, True

    out = []
    for line in (lines[1:] if has_header else lines):
        c = cells(line)
        if len(c) <= max(name_i, seen_i):
            continue
        name = c[name_i]
        if name:
            out.append((name, c[seen_i]))
    return out


def _event_base_name(event) -> str:
    """Nome-base das threads do CTA (events/logger/regear)."""
    dt = _event_started_dt(event) or datetime.now(timezone.utc)
    return f"CTA #{event['id']} — {dt.strftime('%d/%m %H:%M UTC')}"


async def time_autocomplete(interaction: discord.Interaction, current: str):
    """
    Gera slots de 15 em 15min, do slot 15min antes de agora até ~3h depois.
    Total: 13 slots (cabe dentro do limite de 25 do Discord).
    """
    now      = datetime.now(timezone.utc)
    earliest = now - timedelta(minutes=15)
    base     = _round_down_to_15(earliest)

    # Enche o limite de 25 do Discord: 26 slots × 15min ≈ 6h15 de opções futuras
    # (gera 26 e o filtro/[:25] garante 25 após remover o slot anterior a agora).
    slots = [base + timedelta(minutes=15 * i) for i in range(26)]
    slots = [s for s in slots if s >= earliest]

    if current:
        cur = current.strip()
        slots = [s for s in slots if s.strftime("%H:%M").startswith(cur)]

    return [
        app_commands.Choice(name=s.strftime("%H:%M UTC"), value=s.strftime("%H:%M"))
        for s in slots[:25]
    ]


async def comp_autocomplete(interaction: discord.Interaction, current: str):
    """Sugere comps lidas da página COMPS (coluna A), via cache do CTACog."""
    cog = interaction.client.cogs.get('CTACog')
    comps = await cog.get_comps_cached() if cog else []
    cur = (current or "").strip().lower()
    if cur:
        comps = [c for c in comps if cur in c.lower()]
    return [app_commands.Choice(name=c[:100], value=c[:100]) for c in comps[:25]]


def _event_choices(events: list, current: str):
    """Monta Choices (#id · status · comp) p/ autocompletes de evento (valor = id)."""
    cur = (current or "").strip().lower()
    out = []
    for ev in events:
        comp = ev.get('comp')
        label = f"#{ev['id']} · {event_status_label(ev)}" + (f" · {comp}" if comp else "")
        if cur and cur not in str(ev['id']) and cur not in label.lower():
            continue
        out.append(app_commands.Choice(name=label[:100], value=ev['id']))
        if len(out) >= 25:
            break
    return out


async def openregear_autocomplete(interaction: discord.Interaction, current: str):
    """CTAs não finalizados (em andamento/encerrados-sem-split), id decrescente."""
    return _event_choices(await get_non_finalized_events(limit=25), current)


async def adiarcta_autocomplete(interaction: discord.Interaction, current: str):
    """CTAs ativos (em andamento + agendados), id decrescente."""
    events = sorted(await get_active_ctas(), key=lambda e: e['id'], reverse=True)
    return _event_choices(events, current)


def _fit_field(lines: list[str], limit: int = EMBED_FIELD_LIMIT) -> str:
    """
    Junta `lines` com \\n sem ultrapassar `limit` chars (regra do Discord: value de
    field <= 1024). Se não couber tudo, corta e acrescenta '… +N'. Nunca vazio.
    """
    out, total = [], 0
    for i, ln in enumerate(lines):
        tail = f"… +{len(lines) - i}"
        # reserva espaço pro aviso de corte
        if total + len(ln) + 1 > limit - (len(tail) + 1):
            out.append(tail)
            break
        out.append(ln)
        total += len(ln) + 1
    return "\n".join(out) if out else "​"


def _column_fields(lines: list[str], ncols: int = 3, limit: int = EMBED_FIELD_LIMIT) -> list[str]:
    """
    Distribui `lines` em até `ncols` colunas (valores de fields inline), arredondando
    pra cima por coluna e respeitando o limite de chars do Discord por field. Útil pra
    listas longas de itens CURTOS (participantes, ofensores) — economiza altura no embed.
    """
    if not lines:
        return []
    ncols = max(1, ncols)
    per = (len(lines) + ncols - 1) // ncols
    return [_fit_field(lines[i:i + per], limit) for i in range(0, len(lines), per)]


# (A pergunta de regear no /callout foi removida — o regear agora é aberto pelo
#  comando /openregear, separadamente. O /callout só finaliza.)


# ------------------------------------------------------------------
# Audit log: registra ações sensíveis no canal economy_logs
# ------------------------------------------------------------------
async def _cta_audit(src, event_id: int, desc: str, kind: str = 'info'):
    """src = discord.Interaction ou commands.Context.
    Posta uma linha de auditoria no economy_logs — silencioso se não configurado."""
    guild  = getattr(src, 'guild', None)
    actor  = getattr(src, 'user', None) or getattr(src, 'author', None)
    client = getattr(src, 'client', None) or getattr(src, 'bot', None)
    if not guild or not actor or not client:
        return
    eco = client.cogs.get('Economy')
    if not eco:
        return
    ts = datetime.now(timezone.utc).strftime('%H:%M UTC')
    try:
        await eco._post_economy_log(
            guild,
            title=f"📋  𝐄𝐕𝐄𝐍𝐓𝐎 {bold_number(event_id)}",
            desc=desc,
            kind=kind,
            footer=f"por {actor.display_name}  ·  {ts}",
        )
    except Exception as e:
        print(f"✗ cta_audit #{event_id}: {e}")


# ==================================================================
# View do /callout — menu de seleção de CTAs ativos/agendados
# ==================================================================
class CalloutSelect(ui.Select):
    def __init__(self, cog: "CTACog", events: list[dict], invoker_id: int):
        self.cog        = cog
        self.invoker_id = invoker_id
        now = datetime.now(timezone.utc)
        options = []
        for ev in events[:25]:
            status = event_status_label(ev, now)
            emoji  = "🗓️" if status == "agendado" else "🟢"
            dt = _event_started_dt(ev)
            when = dt.strftime('%d/%m %H:%M UTC') if dt else "?"
            caller = ev.get('caller_name') or str(ev.get('caller_id') or '')
            options.append(discord.SelectOption(
                label=f"CTA #{ev['id']} — {when}"[:100],
                value=str(ev['id']),
                description=f"{status} · caller {caller}"[:100],
                emoji=emoji,
            ))
        super().__init__(
            placeholder="Selecione o CTA…",
            min_values=1, max_values=1, options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        event_id = int(self.values[0])
        await self.cog._handle_callout_choice(interaction, event_id, self.invoker_id)


class CalloutSelectView(ui.View):
    def __init__(self, cog: "CTACog", events: list[dict], invoker_id: int):
        super().__init__(timeout=120)
        self.invoker_id = invoker_id
        self.add_item(CalloutSelect(cog, events, invoker_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await send_err(interaction, "Apenas quem usou `/callout` pode escolher.")
            return False
        return True


# ==================================================================
# Modal para enviar o link da batalha
# ==================================================================
class BattleboardLinkModal(ui.Modal, title="Enviar Link da Batalha"):
    link_input = ui.TextInput(
        label="Link da batalha ou do albionbb",
        placeholder="Ex: https://albionbb.com/battles/1234567890",
        max_length=500,
    )

    def __init__(self, event_id: int):
        super().__init__()
        self.event_id = event_id

    async def on_submit(self, interaction: discord.Interaction):
        link = self.link_input.value.strip()
        if not link:
            await send_err(interaction, "Link vazio.")
            return
        if not ('http://' in link or 'https://' in link or link.isdigit()):
            await send_err(interaction, "Formato inválido. Envie um link ou número da batalha.")
            return
        
        try:
            await update_event_meta(self.event_id, battleboard_url=link)
        except Exception as e:
            await send_err(interaction, f"Erro salvando link: {e}")
            return

        cog = interaction.client.cogs.get('CTACog')
        if cog:
            try:
                await cog._refresh_event_embed(self.event_id)
            except Exception as e:
                print(f"✗ Erro refrescando embed após envio de link: {e}")

        await _cta_audit(interaction, self.event_id, f"⚔️ Link da batalha: {link}")
        await send_ok(interaction, f"✅ Link da batalha salvo:\n{link}")


# ==================================================================
# View persistente do embed do evento
# ==================================================================
class EventEmbedView(ui.View):
    """
    Botões do embed pós-evento:
      ✏️ Alterar Participação · 🫷 Remover Participação · 💰 Definir Split · ✅ Finalizar Evento
    """
    def __init__(self):
        super().__init__(timeout=None)

    async def _get_cog(self, interaction):
        cog = interaction.client.cogs.get('CTACog') or interaction.client.cogs.get('CTA')
        if not cog:
            await send_err(interaction, "Sistema indisponível.")
            return None
        return cog

    @ui.button(emoji="✏️", style=discord.ButtonStyle.primary, custom_id="cta:alter_v1")
    async def alter_btn(self, interaction, _b):
        if not await self._get_cog(interaction):
            return
        event = await get_event_by_message_id(interaction.message.id)
        if not event:
            await send_err(interaction, "Evento não encontrado.")
            return
        await interaction.response.send_message(
            "Escolha o participante para **alterar a %**:",
            view=ParticipantUserSelectView(event['id'], "alter"),
            ephemeral=True,
        )
        try:
            await utils.schedule_ephemeral_from_ctx(interaction)
        except Exception:
            pass

    @ui.button(emoji="🫷", style=discord.ButtonStyle.danger, custom_id="cta:remove_v1")
    async def remove_btn(self, interaction, _b):
        if not await self._get_cog(interaction):
            return
        event = await get_event_by_message_id(interaction.message.id)
        if not event:
            await send_err(interaction, "Evento não encontrado.")
            return
        await interaction.response.send_message(
            "Escolha o participante para **remover** do evento:",
            view=ParticipantUserSelectView(event['id'], "remove"),
            ephemeral=True,
        )
        try:
            await utils.schedule_ephemeral_from_ctx(interaction)
        except Exception:
            pass

    @ui.button(emoji="💰", style=discord.ButtonStyle.primary, custom_id="cta:split_define_v1")
    async def split_define_btn(self, interaction, _b):
        # Permissão: logistic (ou owner)
        if interaction.user.id != OWNER_ID and not await has_configured_role(
            interaction.user, 'role_logistic'
        ):
            await send_err(interaction, "Apenas logistic pode definir split.")
            return

        event = await get_event_by_message_id(interaction.message.id)
        if not event:
            await send_err(interaction, "Evento não encontrado.")
            return
        if event.get('split_finalized'):
            await send_err(interaction, "Esse evento já teve o split finalizado.")
            return

        await interaction.response.send_modal(
            DefineSplitModal(event_id=event['id'])
        )

    @ui.button(emoji="🔗", style=discord.ButtonStyle.primary, custom_id="cta:battle_link_v1")
    async def battle_link_btn(self, interaction, _b):
        # Permissão: logistic (ou owner)
        if interaction.user.id != OWNER_ID and not await has_configured_role(
            interaction.user, 'role_logistic'
        ):
            await send_err(interaction, "Apenas logistic pode enviar o link da batalha.")
            return

        event = await get_event_by_message_id(interaction.message.id)
        if not event:
            await send_err(interaction, "Evento não encontrado.")
            return

        await interaction.response.send_modal(
            BattleboardLinkModal(event_id=event['id'])
        )

    @ui.button(emoji="✅", style=discord.ButtonStyle.success, custom_id="cta:split_finalize_v1")
    async def split_finalize_btn(self, interaction, _b):
        # Permissão: logistic (ou owner)
        if interaction.user.id != OWNER_ID and not await has_configured_role(
            interaction.user, 'role_logistic'
        ):
            await send_err(interaction, "Apenas logistic pode finalizar split.")
            return

        event = await get_event_by_message_id(interaction.message.id)
        if not event:
            await send_err(interaction, "Evento não encontrado.")
            return
        if not event.get('split_defined'):
            await send_err(interaction, "Você precisa **Definir split** antes de finalizar.")
            return
        if event.get('split_finalized'):
            await send_err(interaction, "Split já foi finalizado.")
            return

        cog = interaction.client.cogs.get('CTACog')
        if not cog:
            await send_err(interaction, "Sistema indisponível.")
            return

        await interaction.response.defer(ephemeral=True)
        await cog._finalize_split(interaction, event['id'])


# ==================================================================
# Modal de porcentagem (usado em Alterar e Adicionar)
# ==================================================================
class PercentModal(ui.Modal, title="Porcentagem"):
    percent_input = ui.TextInput(
        label="Porcentagem (0 a 100)",
        placeholder="Ex: 75",
        max_length=4,
    )

    def __init__(self, event_id: int, user_id: int, user_name: str):
        super().__init__()
        self.event_id  = event_id
        self.user_id   = user_id
        self.user_name = user_name

    async def on_submit(self, interaction: discord.Interaction):
        try:
            pct = int(self.percent_input.value.strip())
        except ValueError:
            await send_err(interaction, "Porcentagem deve ser um inteiro.")
            return
        if not (0 <= pct <= 100):
            await send_err(interaction, "Porcentagem deve estar entre 0 e 100.")
            return

        await update_attendance_percent(self.event_id, self.user_id, self.user_name, pct)
        await _cta_audit(interaction, self.event_id,
                         f"✏️ participação de <@{self.user_id}> ({self.user_name}) → **{pct}%**")

        cog = interaction.client.cogs.get('CTACog')
        if cog:
            await cog._refresh_event_embed(self.event_id)

        # Auto-edita a ephemeral do select (modal veio dela) — sem mandar msg nova.
        _msg = make_embed('ok', desc=f"Participação de <@{self.user_id}> definida em **{pct}%**.")
        try:
            await interaction.response.edit_message(content=None, embed=_msg, view=None)
        except discord.HTTPException:
            await interaction.response.send_message(embed=_msg, ephemeral=True)
            try:
                await utils.schedule_ephemeral_from_ctx(interaction)
            except Exception:
                pass


# ==================================================================
# UserSelect para escolher um participante (Alterar % / Remover)
# Usa o seletor NATIVO do Discord → sem o limite de 25 opções, lida com
# CTAs grandes (60-200). Valida que a pessoa está mesmo na lista do evento.
# ==================================================================
class ParticipantUserSelect(ui.UserSelect):
    def __init__(self, event_id: int, mode: str):
        """mode = 'alter' ou 'remove'."""
        self.event_id = event_id
        self.mode     = mode
        super().__init__(
            placeholder=("Escolha quem alterar a %…" if mode == "alter"
                         else "Escolha quem remover do evento…"),
            min_values=1, max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        member  = self.values[0]
        att_ids = {uid for uid, _n, _p, _s in await get_event_attendances(self.event_id)}
        if member.id not in att_ids:
            await interaction.response.edit_message(
                content=(f"❌ <@{member.id}> não está na lista deste evento. "
                         f"Para adicionar quem faltou na call, use o **late-attend**."),
                view=None,
            )
            return

        if self.mode == "alter":
            await interaction.response.send_modal(
                PercentModal(event_id=self.event_id, user_id=member.id,
                             user_name=getattr(member, 'display_name', member.name))
            )
        else:  # remove
            await delete_attendance(self.event_id, member.id)
            await _cta_audit(interaction, self.event_id,
                             f"🫷 <@{member.id}> ({member.display_name}) removido(a) da participação",
                             kind='warn')
            cog = interaction.client.cogs.get('CTACog')
            if cog:
                await cog._refresh_event_embed(self.event_id)
            await interaction.response.edit_message(
                content=None,
                embed=make_embed('ok', desc=f"<@{member.id}> removido(a) da participação."),
                view=None,
            )


class ParticipantUserSelectView(ui.View):
    def __init__(self, event_id: int, mode: str):
        super().__init__(timeout=180)
        self.add_item(ParticipantUserSelect(event_id, mode))


# ==================================================================
# Select multi de nodes CAPTURADOS (após o print da tab)
# ==================================================================
class CapturedNodesSelect(ui.Select):
    def __init__(self, nodes: list, node_map: dict = None):
        """nodes: lista de tuplas (id, node_type, map_name, scout_name, scout_id, ts)."""
        node_map = node_map or {}
        options = []
        for node_log_id, node_type, map_name, scout_name, _scout_id, _ts in nodes[:25]:
            emoji = node_emoji_of(node_map, node_type)
            options.append(discord.SelectOption(
                label=f"{node_type}  —  {map_name}"[:100],
                value=str(node_log_id),
                description=f"Scout: {scout_name}"[:100],
                emoji=emoji,
            ))
        super().__init__(
            placeholder="Marque os nodes CAPTURADOS (pode escolher vários)",
            min_values=0,
            max_values=len(options) if options else 1,
            options=options or [
                discord.SelectOption(label="(sem opções)", value="-1")
            ],
        )
        self.selected_ids: list[int] = []

    async def callback(self, interaction: discord.Interaction):
        # Atualiza a lista de IDs selecionados
        selected_values = set(v for v in self.values if v != "-1")
        self.selected_ids = [int(v) for v in selected_values]

        # ⚠️ IMPORTANTE: marcar default=True nas opções selecionadas pra
        # preservar visualmente o estado quando o user reabrir o dropdown.
        # Sem isso, o Discord "esquece" as escolhas após edit_message.
        for opt in self.options:
            opt.default = (opt.value in selected_values)

        n = len(self.selected_ids)

        # Mostrar o que foi marcado pra o usuário não ficar no escuro
        if n > 0:
            names = []
            for opt in self.options:
                if opt.default:
                    emoji_str = str(opt.emoji) if opt.emoji else ""
                    names.append(f"• {emoji_str} {opt.label}")
            listing = "\n".join(names)
            content = (
                f"📋 **{n} node(s) marcado(s) como capturado(s):**\n"
                f"{listing}\n\n"
                f"Reabra o menu pra ajustar ou clique em **✅ Confirmar split**."
            )
        else:
            content = (
                "Nenhum node marcado. Clique no menu pra selecionar ou em "
                "**⏭️ Nenhum capturado**."
            )

        # Re-renderiza a view inteira com os defaults atualizados
        await interaction.response.edit_message(
            content=content,
            view=self.view,
        )


class CapturedNodesView(ui.View):
    """View pós-print da tab: marca quais nodes foram capturados."""

    def __init__(self, event_id: int, nodes: list, node_map: dict = None):
        super().__init__(timeout=300)
        self.event_id = event_id
        self.nodes    = nodes
        self.select   = CapturedNodesSelect(nodes, node_map)
        self.add_item(self.select)

        confirm = ui.Button(
            label="✅ Confirmar split",
            style=discord.ButtonStyle.success,
        )
        confirm.callback = self._on_confirm
        self.add_item(confirm)

        skip = ui.Button(
            label="⏭️ Nenhum capturado",
            style=discord.ButtonStyle.secondary,
        )
        skip.callback = self._on_skip
        self.add_item(skip)

    async def _on_confirm(self, interaction):
        captured_ids = self.select.selected_ids
        await set_event_captured_nodes(self.event_id, captured_ids)
        await _cta_audit(interaction, self.event_id,
                         f"✅ Nodes capturados: **{len(captured_ids)}** de {len(self.nodes)}")
        cog = interaction.client.cogs.get('CTACog')
        if cog:
            await cog._refresh_event_embed(self.event_id)
        await interaction.response.edit_message(
            content=(
                f"✅ **Split definido!**\n"
                f"{len(captured_ids)} de {len(self.nodes)} nodes capturados.\n"
                f"Use **✅ Finalizar split** quando estiver pronto."
            ),
            view=None,
        )
        self.stop()

    async def _on_skip(self, interaction):
        await set_event_captured_nodes(self.event_id, [])
        await _cta_audit(interaction, self.event_id, "⏭️ Nenhum node capturado")
        cog = interaction.client.cogs.get('CTACog')
        if cog:
            await cog._refresh_event_embed(self.event_id)
        await interaction.response.edit_message(
            content=(
                f"✅ **Split definido!**\n"
                f"Nenhum node capturado neste CTA — nenhum scout será pago.\n"
                f"Use **✅ Finalizar split** quando estiver pronto."
            ),
            view=None,
        )
        self.stop()


# ==================================================================
# Definir split: modal (valor + local) → print da tab → nodes capturados
# ==================================================================
class DefineSplitModal(ui.Modal, title="Definir Split — passo 1/2"):
    """Pede valor da tab e localização. Em seguida pede o PRINT da tab no canal."""
    value_input = ui.TextInput(
        label="Valor da tab (prata)",
        placeholder="Ex: 50,000,000  ou  50m  ou  1.500.000",
        max_length=30,
    )
    location_input = ui.TextInput(
        label="Onde está a tab (HO / ilha)",
        placeholder="Ex: HO  ou  Ilha das Hyena",
        max_length=80,
    )

    def __init__(self, event_id: int):
        super().__init__()
        self.event_id = event_id

    async def on_submit(self, interaction: discord.Interaction):
        parsed = parse_silver(self.value_input.value)
        if parsed is None or parsed < 0:
            await send_err(interaction, "Valor inválido. Aceito: `0`, `1500000`, `1.5m`, "
                                        "`2,500,000`, `1.000.000`.")
            return

        location = self.location_input.value.strip()

        # Split 0: não há loot/tab — pula o passo 2/2 (print) e finaliza direto,
        # pra o evento ainda contar (attendance) mesmo sem economia.
        if parsed == 0:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Definir Split",
                    description=(f"**Valor:** `{format_silver(0)}`\n"
                                 f"**Tab:** {location}\n\n"
                                 "Split **zero** — sem print de tab."),
                    color=EMBED_INFO,
                ),
                ephemeral=True,
            )
            await _finish_define_split(interaction, self.event_id, 0, location, None)
            return

        # Passo 2/2: em vez de perguntar do lootlogger, pedimos o PRINT da tab.
        # (Modais não aceitam anexo → pedimos a imagem no canal e capturamos a próxima
        #  mensagem do autor com imagem.)
        prompt = discord.Embed(
            title="Definir Split — passo 2/2",
            description=(
                f"**Valor:** `{format_silver(parsed)}`\n"
                f"**Tab:** {location}\n\n"
                "📸 Envie agora **uma imagem (print) da tab** aqui no canal — você tem 5 min."
            ),
            color=EMBED_INFO,
        )
        await interaction.response.send_message(embed=prompt, ephemeral=True)
        try:
            await utils.schedule_ephemeral_from_ctx(interaction)
        except Exception:
            pass

        def _check(m: discord.Message) -> bool:
            return (m.author.id == interaction.user.id
                    and m.channel.id == interaction.channel_id
                    and bool(m.attachments)
                    and _is_image_attachment(m.attachments[0]))

        try:
            up = await interaction.client.wait_for('message', timeout=300, check=_check)
        except asyncio.TimeoutError:
            try:
                await interaction.edit_original_response(
                    content="⏱️ Tempo esgotado sem receber o print. Use **Definir Split** de novo.",
                    embed=None, view=None)
            except discord.HTTPException:
                pass
            return

        # Guarda os BYTES do print (vão ANEXADOS no invoice, na finalização) e apaga
        # o upload do user — SEM reenviar a imagem no chat.
        att = up.attachments[0]
        tab_url = None
        limit = guild_filesize_limit(interaction.guild)
        if att.size and att.size > limit:
            # Grande demais pro teto de upload → não dá pra guardar/anexar. Mantém o
            # upload do user e segue com a URL original (best-effort, pode expirar).
            tab_url = att.url
            print(f"⚠️ Print da tab do CTA #{self.event_id} ({human_size(att.size)}) "
                  f"passa do limite do servidor ({human_size(limit)}); não guardado.")
            try:
                await interaction.followup.send(
                    f"⚠️ O print ({human_size(att.size)}) passa do limite de upload do "
                    f"servidor ({human_size(limit)}), então não guardei cópia — o link pode "
                    f"expirar. O split foi definido mesmo assim.", ephemeral=True)
            except discord.HTTPException:
                pass
        else:
            try:
                data = await att.read()
                await set_event_tab_image(self.event_id, data)   # bytes → vão no invoice
                try:
                    await up.delete()                            # tira o upload do user
                except discord.HTTPException:
                    pass
            except Exception as e:
                print(f"✗ Erro guardando print da tab do CTA #{self.event_id}: {e}")
                tab_url = att.url   # fallback: usa a URL do user se o blob falhar

        await _finish_define_split(interaction, self.event_id, parsed, location, tab_url)


def _is_image_attachment(att: discord.Attachment) -> bool:
    """True se o anexo é uma imagem (por content-type ou extensão)."""
    if (att.content_type or '').lower().startswith('image/'):
        return True
    return (att.filename or '').lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif'))


async def _finish_define_split(interaction, event_id, repair_value, tab_location, tab_image_url):
    """Salva o split (com a URL do print da tab) e segue pro passo de nodes capturados.
    Edita a resposta EPHEMERAL original já enviada no on_submit."""
    await set_event_split(event_id, repair_value, tab_location, tab_image_url)
    await _cta_audit(interaction, event_id,
                     f"💰 Split definido: **{format_silver(repair_value)}** · 🗺️ {tab_location}")

    # Buscar nodes próximos do END pra perguntar quais foram capturados.
    event = await get_event_by_id(event_id)
    nodes = []
    if event and event.get('ended_at'):
        ended_dt = datetime.fromisoformat(event['ended_at'])
        if ended_dt.tzinfo is None:
            ended_dt = ended_dt.replace(tzinfo=timezone.utc)
        nodes = await get_nodes_near(int(ended_dt.timestamp()), NODES_NEAR_THRESHOLD_SECONDS)

    if repair_value == 0 and not tab_image_url:
        intro = (
            f"💰 Split definido como `0` (sem tab).\n"
            f"🗺️ Tab: {tab_location}\n\n"
        )
    else:
        intro = (
            f"🧾 Print da tab salvo.\n"
            f"💰 Valor: `{format_silver(repair_value)}`\n"
            f"🗺️ Tab: {tab_location}\n\n"
        )

    if not nodes:
        # Sem nodes próximos — salvar vazio e mostrar resumo direto.
        await set_event_captured_nodes(event_id, [])
        cog = interaction.client.cogs.get('CTACog')
        if cog:
            await cog._refresh_event_embed(event_id)
        await interaction.edit_original_response(
            content=(intro +
                     "Nenhum node próximo registrado.\n"
                     "✅ Split definido — use **Finalizar split** quando pronto."),
            embed=None, view=None)
        return

    # Há nodes próximos: pergunta quais foram capturados.
    node_map = await load_node_map()
    view = CapturedNodesView(event_id, nodes, node_map)
    await interaction.edit_original_response(
        content=(intro +
                 f"📋 **{len(nodes)} node(s) registrados próximos do CTA.**\n"
                 f"Marque os que **vocês conseguiram capturar** (só esses scouts serão pagos):"),
        embed=None, view=view)


# ==================================================================
# Cog principal
# ==================================================================
class CTACog(commands.Cog, name="CTACog"):
    """Sistema de CTA com snapshots de voz e embed pós-evento."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Cache de comps POR SERVIDOR (cada guild tem sua planilha): gid -> (comps, ts)
        self._comps_cache: dict = {}
        # Eventos com embed "suja" — refrescados de forma COALESCIDA (1× a cada
        # poucos segundos) em vez de a cada ping (evita rate limit quando muita
        # gente registra funções junto).
        self._dirty_events: set = set()   # tuplas (guild_id, event_id) — multi-tenant
        # Anti-duplicação (1 processo): callouts concorrentes no MESMO CTA geram
        # eventos/threads duplicados; este conjunto serializa o encerramento/cancelamento.
        self._finalizing: set = set()     # (guild_id, event_id) em finalização/cancelamento
        # Serializa a criação de CTA por servidor — a checagem de "mesmo horário" +
        # create_cta_event tem que ser atômica (senão 2 /cta simultâneos passam ambos).
        self._cta_create_locks: dict = {} # guild_id -> asyncio.Lock

    def _cta_create_lock(self, gid) -> asyncio.Lock:
        lock = self._cta_create_locks.get(gid)
        if lock is None:
            lock = asyncio.Lock()
            self._cta_create_locks[gid] = lock
        return lock

    def mark_event_dirty(self, event_id: int):
        """Pede um refresh do embed do evento (coalescido pelo dirty_embed_loop)."""
        gid = database.get_current_guild()
        if gid is not None:
            self._dirty_events.add((gid, event_id))

    async def get_comps_cached(self, ttl: float = 3600.0) -> list[str]:
        """
        Lista de comps da planilha, com cache. NUNCA bloqueia na rede (o autocomplete
        tem ~3s e o Sheets pode levar até 60s): se o cache estiver vencido OU vazio,
        dispara a atualização em BACKGROUND e devolve o que tem na hora (mesmo vazio).
        O warmup de 30min mantém o cache quente, então isso quase nunca devolve vazio.
        """
        import time
        gid = database.get_current_guild()
        comps, ts = self._comps_cache.get(gid, ([], 0.0))
        now = time.monotonic()
        if comps and (now - ts) < ttl:
            return comps
        # Vencido ou vazio: atualiza em background e serve o atual (pode ser []).
        asyncio.create_task(self._refresh_comps_cache(gid))
        return comps

    async def _refresh_comps_cache(self, gid=None):
        import time
        if gid is None:
            gid = database.get_current_guild()
        with database.using_guild(gid):
            comps = await sheets.list_comps()
        if comps:
            self._comps_cache[gid] = (comps, time.monotonic())

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return True
        if not await is_server_activated(ctx.guild.id):
            await send_err(ctx, "Servidor não ativado.")
            return False
        return True

    async def cog_load(self):
        # Registra a view persistente
        self.bot.add_view(EventEmbedView())

        # Religa as tasks se houver evento ativo
        self.snapshot_loop.start()
        self.embed_refresh_loop.start()
        self.sheet_cleanup_loop.start()
        self.comps_warmup_loop.start()
        self.dirty_embed_loop.start()
        print("✓ CTA Cog carregada")

    def cog_unload(self):
        self.snapshot_loop.cancel()
        self.embed_refresh_loop.cancel()
        self.sheet_cleanup_loop.cancel()
        self.comps_warmup_loop.cancel()
        self.dirty_embed_loop.cancel()

    # ------------------------------------------------------------------
    # Refresh COALESCIDO do embed do evento (junta vários pings num só refresh)
    # ------------------------------------------------------------------
    @tasks.loop(seconds=4)
    async def dirty_embed_loop(self):
        if not self._dirty_events:
            return
        items = list(self._dirty_events)[:10]   # teto por ciclo — tuplas (gid, event_id)
        self._dirty_events.difference_update(items)
        for gid, event_id in items:
            with database.using_guild(gid):
                try:
                    await self._refresh_event_embed(event_id)
                except Exception as e:
                    print(f"✗ Erro no refresh coalescido do embed (CTA #{event_id}): {e}")

    @dirty_embed_loop.before_loop
    async def before_dirty_embed_loop(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Loggers: pool logger_percent% dividido por PESO em _finalize_split
    # (corroboração com 2+ loggers; logger único leva o pool inteiro).
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Permissão
    # ------------------------------------------------------------------
    async def _check_caller_or_staff(self, ctx_or_interaction, event=None) -> bool:
        """Permite owner, caller original do evento, ou council/logistic."""
        user = (ctx_or_interaction.author if hasattr(ctx_or_interaction, 'author')
                else ctx_or_interaction.user)

        if user.id == OWNER_ID:
            return True
        if event and event.get('caller_id') == user.id:
            return True
        return await has_configured_role(user, 'role_council', 'role_logistic')

    # ==================================================================
    # /cta
    # ==================================================================
    @commands.hybrid_command(
        name="cta",
        description="Cria uma CTA (várias permitidas, desde que em horários diferentes).",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        time="Horário UTC do evento (ex: 21:30). Lista mostra slots de 30min.",
        comp="Composição da CTA (lida da planilha COMPS).",
        mensagem="Mensagem da CTA (exibida no mass-info, ex: 'tragam montaria de batalha').",
    )
    @app_commands.autocomplete(time=time_autocomplete, comp=comp_autocomplete)
    async def cta(self, ctx: commands.Context, time: str, comp: str, mensagem: str):
        # Permissão: council/logistic/caller
        if ctx.author.id != OWNER_ID:
            allowed = await has_configured_role(ctx.author, 'role_council', 'role_logistic', 'role_caller')
            if not allowed:
                await send_err(ctx, "Apenas council, logistic ou caller podem usar este comando.")
                return

        # Parse do horário
        started_dt = parse_utc_time(time)
        if not started_dt:
            await send_err(ctx, "Horário inválido. Use formato `HH:MM` (UTC).")
            return

        # Checa setup do voice CTA
        cfg = await load_economy_config()
        voice_id = cfg.get('voice_cta')
        if not voice_id:
            await send_err(ctx, "Canal de voz da CTA não configurado. "
                                "Configure em `/setup` → Canais → Voice — Call CTA.")
            return

        # A criação da página na planilha pode passar de 3s; acka já para não
        # estourar o token da interação (o ctx.send vira followup).
        await ctx.defer(ephemeral=True)

        comp = (comp or "").strip()
        mensagem = (mensagem or "").strip()

        # Criação SERIALIZADA por servidor: a checagem de "mesmo horário" + a criação
        # precisam ser ATÔMICAS — senão 2 /cta simultâneos no mesmo horário passam
        # ambos pela checagem e geram CTAs duplicadas.
        async with self._cta_create_lock(ctx.guild.id):
            # Várias CTAs são permitidas, MAS não no mesmo horário (timer).
            for ev in await get_active_ctas():
                ev_dt = _event_started_dt(ev)
                if ev_dt and ev_dt == started_dt:
                    status = event_status_label(ev)
                    await send_err(ctx, f"Já existe uma CTA (#{ev['id']}, {status}) marcada para esse "
                                        f"mesmo horário <t:{int(started_dt.timestamp())}:t>.\n"
                                        f"Escolha outro horário ou use `/callout` para cancelá-la.")
                    return

            # Cria o evento no DB
            event_id = await create_cta_event(
                caller_id=ctx.author.id,
                caller_name=ctx.author.display_name,
                started_at_iso=started_dt.isoformat(),
                guild_id=ctx.guild.id,
                announcement_channel_id=ctx.channel.id,
                comp=comp,
                message=mensagem,
            )

        # Cria a página do CTA na planilha (copia o modelo da comp e renomeia
        # para o horário UTC). Best-effort: se a planilha falhar, o CTA segue.
        sheet_status = ""
        if await sheets.is_configured() and comp:
            page_name = started_dt.strftime('%d/%m %H:%M')
            created = await sheets.create_cta_page(comp, page_name, event_id)
            if created:
                final_name = created.get('page_name')
                sheet_url  = created.get('sheet_url')
                meta = {'sheet_page': final_name}
                if sheet_url:
                    meta['sheet_url'] = sheet_url
                await update_event_meta(event_id, **meta)
                link = f"\n🔗 {sheet_url}" if sheet_url else ""
                sheet_status = f"\n📊 Planilha: página **{final_name}** criada (comp **{comp}**).{link}"
            else:
                sheet_status = (
                    f"\n⚠️ Planilha: não consegui criar a página da comp **{comp}** "
                    f"(confira a comp/o modelo no COMPS)."
                )

        # Atualiza a embed do mass-info (nova fonte de verdade para CTAs pendentes)
        massinfo_cog = self.bot.cogs.get('MassInfoCog')
        massinfo_chan_mention = ""
        if massinfo_cog:
            try:
                await massinfo_cog.refresh_massinfo(recreate=True)
                cfg2 = await load_economy_config()
                mid = cfg2.get('channel_massinfo')
                if mid:
                    massinfo_chan_mention = f" — veja <#{mid}>"
            except Exception as e:
                print(f"✗ Erro atualizando mass-info: {e}")
            # Ping de criação: CTA agendado dá um @everyone que se APAGA logo; CTA
            # "em cima da hora" (flash mass) dá o ping de INÍCIO (persistente).
            try:
                await massinfo_cog.post_cta_created_ping(event_id)
            except Exception as e:
                print(f"✗ Erro no ping de criação do CTA #{event_id}: {e}")


        unix_ts = int(started_dt.timestamp())
        scheduled = started_dt > datetime.now(timezone.utc)
        verb = "agendada" if scheduled else "iniciada"
        note = ("\n🗓️ *Status:* **agendada** — vira **em andamento** quando o horário "
                "chegar. Até lá pode ser cancelada com `/callout` (sem gerar evento)."
                if scheduled else "")
        await send_ok(ctx, f"**CTA #{event_id} {verb}** por {ctx.author.mention}\n"
                           f"⏰ Horário: <t:{unix_ts}:t> UTC  ·  <t:{unix_ts}:R>\n"
                           f"🎙️ Voice: <#{voice_id}>{massinfo_chan_mention}{sheet_status}{note}")
        print(f"✓ CTA #{event_id} {verb} por {ctx.author.display_name}")

    # ==================================================================
    # /deleteevent
    # ==================================================================
    @commands.hybrid_command(
        name="deleteevent",
        description="Apaga COMPLETAMENTE um CTA: dados do banco + threads/mensagens do bot.",
    )
    @app_commands.guild_only()
    @app_commands.describe(event_id="ID do CTA a apagar (ex: 12)")
    async def deleteevent(self, ctx: commands.Context, event_id: int):
        # Permissão: SOMENTE council/logistic (ação destrutiva)
        if not await has_configured_role(ctx.author, 'role_council', 'role_logistic'):
            await send_err(ctx, "Apenas council ou logistic podem apagar um CTA.")
            return

        event = await get_event_by_id(event_id)
        if not event:
            await send_err(ctx, f"CTA #{event_id} não existe.")
            return

        await ctx.defer(ephemeral=True)

        # 1) Limpeza no Discord (feita ANTES de apagar o DB, usando o dict atual).
        deleted_threads, failed_threads = await self._delete_event_artifacts(event)

        # 2) ESTORNO do split (se finalizado): reverte os créditos EXATOS do ledger —
        #    participantes, scouts e loggers (balance + total_earned) e o imposto do
        #    banco. TEM que vir ANTES de apagar o banco (delete_event_completely some
        #    com o ledger). Sem ledger (evento não finalizado) = nada a estornar.
        refunded_users = set()
        refunded_total = 0
        refunded_bank  = 0
        for kind, uid, amount in await get_event_payouts(event_id):
            if not amount or amount <= 0:
                continue
            if kind == 'guild_bank':
                await remove_guild_bank(amount, allow_negative=True)
                refunded_bank += amount
            elif uid is not None:
                await revert_user_money(uid, amount)
                refunded_users.add(uid)
                refunded_total += amount

        # 3) Apaga tudo do banco
        counts = await delete_event_completely(event_id)

        # 4) Atualiza as embeds de mass-info e splits (CTA sai das listas)
        await self._refresh_cta_embeds()

        # 5) Audit no economy_logs (apagação + estorno se houver)
        audit_lines = ["🗑️ Evento apagado por completo"]
        if refunded_total:
            audit_lines.append(f"💸 Estornados **{format_silver(refunded_total)}** de "
                               f"**{len(refunded_users)}** jogador(es)")
        if refunded_bank:
            audit_lines.append(f"🏦 Revertidos **{format_silver(refunded_bank)}** do banco")
        await _cta_audit(ctx, event_id, "\n".join(audit_lines), kind='warn')

        # 6) Feedback
        refund_line = ""
        if refunded_total or refunded_bank:
            parts = []
            if refunded_total:
                parts.append(f"**{format_silver(refunded_total)}** de "
                             f"**{len(refunded_users)}** jogador(es)")
            if refunded_bank:
                parts.append(f"**{format_silver(refunded_bank)}** do banco")
            refund_line = f"• 💸 Split estornado: {' + '.join(parts)}\n"
        await ctx.send(
            f"🗑️ **CTA #{event_id} apagado por completo.**\n"
            + refund_line +
            f"• Threads removidas: **{deleted_threads}**"
            + (f" (falha em {failed_threads})" if failed_threads else "") + "\n"
            f"• Pings (mass-info) apagados: **{counts.get('function_logs', 0)}**\n"
            f"• Presenças (zerg) apagadas: **{counts.get('attendance', 0)}**\n"
            f"• Nodes do evento apagados: **{counts.get('event_nodes', 0)}**\n"
            f"• Regears apagados: **{counts.get('regears', 0)}**",
            ephemeral=True,
        )
        print(f"✓ CTA #{event_id} apagado por {ctx.author.display_name}: {counts} "
              f"(estorno: {format_silver(refunded_total)} p/ {len(refunded_users)} + "
              f"{format_silver(refunded_bank)} banco)")

    # ==================================================================
    # /participacaototal — 100% a todos os participantes do CTA
    # ==================================================================
    @commands.hybrid_command(
        name="participacaototal",
        aliases=["all100", "full100"],
        description="Dá 100% de participação a TODOS do CTA — caller/council/logistic",
    )
    @app_commands.guild_only()
    @app_commands.describe(evento="CTA (em andamento / não finalizado)")
    @app_commands.autocomplete(evento=openregear_autocomplete)
    async def participacaototal(self, ctx: commands.Context, evento: int):
        if not await has_configured_role(ctx.author, 'role_caller', 'role_council', 'role_logistic'):
            await send_err(ctx, "Apenas caller, council ou logistic podem usar este comando.")
            return
        ev = await get_event_by_id(evento)
        if not ev:
            await send_err(ctx, f"CTA #{evento} não existe.")
            return
        if ev.get('split_finalized'):
            await send_err(ctx, "O split deste CTA já foi finalizado.")
            return
        atts = await get_event_attendances(evento)
        if not atts:
            await send_info(ctx, "Este CTA não tem participantes registrados.")
            return
        for uid, uname, _pct, _silver in atts:
            await update_attendance_percent(evento, uid, uname, 100)
        await _cta_audit(ctx, evento,
                         f"📊 Participação 100% aplicada a **{len(atts)}** participante(s)")
        try:
            await self._refresh_event_embed(evento)
        except Exception as e:
            print(f"✗ Erro refrescando embed pós participacaototal #{evento}: {e}")
        await send_ok(ctx, f"✅ **{len(atts)}** participante(s) do **CTA #{evento}** agora com **100%**.")

    # ------------------------------------------------------------------
    # Limpeza reutilizável (deleteevent + cancelamento de CTA agendada)
    # ------------------------------------------------------------------
    async def _delete_event_artifacts(self, event: dict) -> tuple[int, int]:
        """
        Apaga threads (events/logger/regear), o aviso 'started a thread' no canal-pai,
        e a mensagem de anúncio. NÃO mexe no banco. Retorna (apagadas, falhas).
        """
        deleted_threads = 0
        failed_threads  = 0

        async def _del_thread_system_msg(parent, thread_name):
            if parent is None or not thread_name:
                return
            try:
                async for m in parent.history(limit=300):
                    if (m.type == discord.MessageType.thread_created
                            and (m.content or '') == thread_name):
                        await m.delete()
                        break
            except Exception as e:
                print(f"✗ Erro apagando aviso de thread em {getattr(parent, 'id', '?')}: {e}")

        async def _del_thread(tid):
            nonlocal deleted_threads, failed_threads
            if not tid:
                return
            ch = self.bot.get_channel(tid)
            if ch is None:
                try:
                    ch = await self.bot.fetch_channel(tid)
                except Exception:
                    ch = None
            if ch is None:
                return  # já não existe — nada a fazer
            parent      = getattr(ch, 'parent', None)
            thread_name = getattr(ch, 'name', None)
            try:
                await ch.delete()
                deleted_threads += 1
            except Exception as e:
                failed_threads += 1
                print(f"✗ Erro apagando thread {tid}: {e}")
            await _del_thread_system_msg(parent, thread_name)

        await _del_thread(event.get('event_thread_id'))
        await _del_thread(event.get('logger_thread_id'))
        await _del_thread(event.get('regear_thread_id'))
        await _del_thread(event.get('logreview_thread_id'))

        # Mensagem de anúncio (postada no canal onde o /cta foi rodado)
        ann_chan_id = event.get('announcement_channel_id')
        ann_msg_id  = event.get('announcement_message_id')
        if ann_chan_id and ann_msg_id:
            ch = self.bot.get_channel(ann_chan_id)
            if ch is None:
                try:
                    ch = await self.bot.fetch_channel(ann_chan_id)
                except Exception:
                    ch = None
            if ch is not None:
                try:
                    msg = await ch.fetch_message(ann_msg_id)
                    await msg.delete()
                except discord.HTTPException:
                    pass  # msg já apagada / sem permissão (esperado)

        # Aviso de pré-início no mass-info: apaga o ping de '10 min' / flash mass que
        # sobraria órfão (o flash nasce já 'em andamento' e nunca passa pela transição
        # agendado→andamento que normalmente o apaga).
        massinfo_cog = self.bot.cogs.get('MassInfoCog')
        if massinfo_cog and (event.get('prestart_msg_id') or event.get('startboard_msg_id')):
            try:
                await massinfo_cog._delete_prestart_warning(event)
            except Exception as e:
                print(f"✗ Erro apagando aviso de pré-início do CTA #{event.get('id')}: {e}")

        return deleted_threads, failed_threads

    async def _refresh_cta_embeds(self):
        """Reedita as embeds de mass-info e splits (CTA mudou de estado)."""
        for cog_name, refresh in (
            ('MassInfoCog', 'refresh_massinfo'),
            ('SplitsCog',   'refresh_splits'),
        ):
            cog = self.bot.cogs.get(cog_name)
            if cog:
                try:
                    if cog_name == 'MassInfoCog':
                        await cog.refresh_massinfo(recreate=True)
                    else:
                        await getattr(cog, refresh)()
                except Exception as e:
                    print(f"✗ Erro atualizando {cog_name}: {e}")

    # ==================================================================
    # /callout  (antigo /end) — finaliza CTA em andamento ou cancela agendada
    # ==================================================================
    @commands.hybrid_command(
        name="callout",
        aliases=["cancelcta", "end"],
        description="Finaliza uma CTA em andamento, ou cancela uma agendada (sem gerar evento).",
    )
    @app_commands.guild_only()
    async def callout(self, ctx: commands.Context):
        events = await get_active_ctas()
        if not events:
            await send_err(ctx, "...")
            return

        # Atalho: se só existe 1, vai direto pro fluxo dela.
        if len(events) == 1:
            ev = events[0]
            if not await self._check_caller_or_staff(ctx, ev):
                await send_err(ctx, "Apenas o caller, council ou logistic pode finalizar/cancelar "
                                    "este CTA.")
                return
            if is_scheduled(ev):
                await self._cancel_scheduled(ctx, ev['id'])
            else:
                await self._finalize_event(ctx, ev['id'])
            return

        # Vários: mostra menu de seleção.
        await ctx.send(
            "Selecione a CTA para **finalizar** (em andamento) ou **cancelar** (agendada):",
            view=CalloutSelectView(self, events, ctx.author.id),
            ephemeral=True,
        )

    async def _handle_callout_choice(self, interaction: discord.Interaction,
                                     event_id: int, invoker_id: int):
        """Decide o fluxo após escolher um CTA no menu do /callout."""
        event = await get_event_by_id(event_id)
        if not event or event.get('ended_at'):
            await interaction.response.edit_message(
                content=None,
                embed=make_embed('err', desc="Essa CTA não está mais ativa."),
                view=None,
            )
            return
        if not await self._check_caller_or_staff(interaction, event):
            await interaction.response.edit_message(
                content=None,
                embed=make_embed('err', desc="Apenas o caller, council ou logistic pode "
                                             "finalizar/cancelar este CTA."),
                view=None,
            )
            return

        if is_scheduled(event):
            await self._cancel_scheduled(interaction, event_id)
        else:
            # Em andamento → finaliza direto (regear é tratado pelo /openregear).
            await self._finalize_event(interaction, event_id)

    async def _cancel_scheduled(self, ctx_or_interaction, event_id: int):
        """Anti-duplicação: serializa o cancelamento do MESMO CTA agendado (mesmo
        motivo do _finalize_event — 2 callouts simultâneos limpariam tudo em dobro)."""
        gid = database.get_current_guild()
        key = (gid, event_id)
        if key in self._finalizing:
            await send_err(ctx_or_interaction,
                           "⏳ Esse CTA já está sendo cancelado — aguarde.")
            return
        self._finalizing.add(key)
        try:
            ev_now = await get_event_by_id(event_id)
            if not ev_now:
                await send_err(ctx_or_interaction, "Essa CTA não existe mais.")
                return
            await self._cancel_scheduled_inner(ctx_or_interaction, event_id)
        finally:
            self._finalizing.discard(key)

    async def _cancel_scheduled_inner(self, ctx_or_interaction, event_id: int):
        """
        Cancela uma CTA AGENDADA: apaga tudo (threads/mensagens + DB) sem gerar
        evento, já que ela ainda não tinha iniciado. Reaproveita a limpeza do
        /deleteevent. Aceita tanto Context (prefix/slash) quanto Interaction.
        """
        is_interaction = isinstance(ctx_or_interaction, discord.Interaction)

        # Ack cedo: a limpeza (threads/mensagens/DB) pode passar dos 3s da interação.
        if is_interaction:
            if not ctx_or_interaction.response.is_done():
                # defer de update: mantém a mensagem do menu enquanto trabalha.
                await ctx_or_interaction.response.defer()
        else:
            try:
                await ctx_or_interaction.defer(ephemeral=True)
            except discord.HTTPException:
                pass  # já respondido / expirado (esperado)

        async def _respond(text: str):
            if is_interaction:
                await ctx_or_interaction.edit_original_response(content=text, view=None)
            else:
                await ctx_or_interaction.send(text, ephemeral=True)
                try:
                    await utils.schedule_ephemeral_from_ctx(ctx_or_interaction)
                except Exception:
                    pass

        event = await get_event_by_id(event_id)
        if not event:
            await _respond(f"❌ CTA #{event_id} não existe.")
            return

        # Planilha: CTA agendada cancelada → apaga a página IMEDIATAMENTE.
        sheet_page = event.get('sheet_page')
        if sheet_page and await sheets.is_configured():
            try:
                await sheets.delete_cta_page(sheet_page)
            except Exception as e:
                print(f"✗ Erro apagando planilha da CTA agendada #{event_id}: {e}")

        await self._delete_event_artifacts(event)
        await delete_event_completely(event_id)
        await self._refresh_cta_embeds()

        await _respond(
            f"🗓️❌ **CTA agendada #{event_id} cancelada.**\n"
            f"Como ela ainda não tinha iniciado, **nenhum evento foi gerado**"
            + (" e a **planilha** dela foi apagada." if sheet_page else ".")
        )
        actor = (ctx_or_interaction.user if is_interaction else ctx_or_interaction.author)
        print(f"✓ CTA agendada #{event_id} cancelada por {actor.display_name}")

    async def _finalize_no_participation(self, event: dict, respond):
        """CTA em andamento recebeu /callout SEM nenhuma participação → não gera evento.
        Apaga artefatos (threads/mensagens), a planilha e o registro no DB, e refresca
        as embeds — mesmo desfecho de uma agendada cancelada. `respond` é o callback de
        resposta do `_finalize_event`."""
        event_id = event['id']

        # Planilha: apaga a página IMEDIATAMENTE (não houve nada pra registrar).
        sheet_page = event.get('sheet_page')
        if sheet_page and await sheets.is_configured():
            try:
                await sheets.delete_cta_page(sheet_page)
            except Exception as e:
                print(f"✗ Erro apagando planilha da CTA sem participação #{event_id}: {e}")

        await self._delete_event_artifacts(event)
        await delete_event_completely(event_id)
        await self._refresh_cta_embeds()

        await respond(
            f"🚫 **CTA #{event_id} encerrada sem participação.**\n"
            f"Como ninguém participou, **nenhum evento foi gerado**"
            + (" e a **planilha** dela foi apagada." if sheet_page else ".")
        )
        print(f"✓ CTA #{event_id} encerrada sem participação (nenhum evento gerado).")

    # ==================================================================
    # /poke — cobra presença (DM) dos registrados que estão offline
    # ==================================================================
    async def _pick_poke_event(self) -> dict | None:
        """CTA alvo do /poke: o EM ANDAMENTO (o mais recente, se houver vários) ou,
        se nenhum estiver rolando, o AGENDADO mais próximo de começar."""
        active = await get_active_ctas()
        if not active:
            return None
        now = datetime.now(timezone.utc)
        floor = datetime.min.replace(tzinfo=timezone.utc)
        ceil  = datetime.max.replace(tzinfo=timezone.utc)
        running = [ev for ev in active if not is_scheduled(ev, now)]
        if running:
            return max(running, key=lambda e: _event_started_dt(e) or floor)
        return min(active, key=lambda e: _event_started_dt(e) or ceil)

    def _poke_embed(self, ev: dict, voice_id: int | None = None) -> discord.Embed:
        dt   = _event_started_dt(ev)
        comp = (ev.get('comp') or '').strip()
        comp_txt = f" · **{comp}**" if comp else ""
        when = (f"⏰ Início: <t:{int(dt.timestamp())}:t> (<t:{int(dt.timestamp())}:R>)\n"
                if dt else "")
        voice_line = f"\n📞 <#{voice_id}>" if voice_id else ""
        return make_embed(
            'warn', title="🔔 Presença no CTA",
            desc=(f"Você está **registrado** no CTA{comp_txt}, mas ainda aparece "
                  f"**offline** no jogo.\n{when}\n"
                  f"🎮 **Entra no jogo e na call** — o time tá contando com você!{voice_line}"),
        )

    @commands.hybrid_command(
        name="poke",
        description="DM cobrando presença dos registrados do CTA que ainda estão offline (menu da guilda).",
    )
    @app_commands.guild_only()
    @app_commands.describe(arquivo="Menu da guilda (.txt/.csv) com Character Name, Last Seen, Roles")
    async def poke(self, ctx: commands.Context, arquivo: discord.Attachment):
        if ctx.author.id != OWNER_ID and not await has_configured_role(
                ctx.author, 'role_caller', 'role_council', 'role_logistic'):
            await send_err(ctx, "Apenas caller, council ou logistic podem usar `/poke`.")
            return

        fname = (arquivo.filename or '').lower()
        if not fname.endswith(('.txt', '.csv')):
            await send_err(ctx, "Envie um arquivo **.txt** ou **.csv** "
                                "(o menu da guilda copiado do jogo).")
            return

        await ctx.defer(ephemeral=True)

        target = await self._pick_poke_event()
        if not target:
            await send_err(ctx, "Não há CTA em andamento nem agendado pra cobrar presença.")
            return

        try:
            data = await arquivo.read()
            text = data.decode('utf-8-sig')
        except Exception as e:
            await send_err(ctx, f"Não consegui ler o arquivo: {e}")
            return

        rows = _parse_poke_file(text)
        if not rows:
            await send_err(ctx, "Não achei linhas válidas no arquivo "
                                "(esperado colunas **Character Name** / **Last Seen**).")
            return

        # Quem está OFFLINE na lista (Last Seen != 'Online'), por nome (IGN) minúsculo.
        offline = {name.strip().lower() for name, seen in rows
                   if (seen or '').strip().lower() != 'online'}

        # Registrados no CTA alvo → cruza o nick com os offline da lista.
        registered = await get_function_log_users(target['id'])
        targets = [(uid, ign) for uid, ign in registered
                   if (ign or '').strip() and (ign or '').strip().lower() in offline]

        if not targets:
            await send_ok(ctx, f"Ninguém pra cobrar no **CTA #{target['id']}** — todos os "
                               f"registrados estão **Online** (ou não estão na lista).")
            return

        poke_cfg  = await load_economy_config()
        voice_id  = poke_cfg.get('voice_cta')
        embed = self._poke_embed(target, voice_id=voice_id)
        sent = failed = left = 0
        for uid, _ign in targets:
            member = ctx.guild.get_member(uid) if ctx.guild else None
            if member is None:
                left += 1
                continue
            try:
                await member.send(embed=embed)
                sent += 1
            except discord.HTTPException:
                failed += 1   # DM fechada / bloqueada

        await send_ok(
            ctx,
            f"🔔 **Cobrança de presença — CTA #{target['id']}**\n"
            f"Registrados offline: **{len(targets)}**\n"
            f"✅ Enviadas: **{sent}**  ·  ❌ DM fechada: **{failed}**"
            + (f"  ·  🚪 Fora do servidor: **{left}**" if left else ""),
        )
        print(f"✓ /poke CTA #{target['id']}: {sent} DMs enviadas "
              f"({failed} falharam, {left} fora do servidor) por {ctx.author.display_name}")

    # ==================================================================
    # /openregear  — abre o ticket de regear + marca os participantes
    # ==================================================================
    @commands.hybrid_command(
        name="openregear",
        description="Abre o ticket de regear de um CTA e marca os participantes.",
    )
    @app_commands.guild_only()
    @app_commands.describe(evento="CTA (em andamento / não finalizado)")
    @app_commands.autocomplete(evento=openregear_autocomplete)
    async def openregear(self, ctx: commands.Context, evento: int):
        event = await get_event_by_id(evento)
        if not event:
            await send_err(ctx, f"CTA #{evento} não existe.")
            return
        if event.get('split_finalized'):
            await send_err(ctx, f"CTA #{evento} já foi finalizado.")
            return
        if not await self._check_caller_or_staff(ctx, event):
            await send_err(ctx, "Apenas o caller, council ou logistic.")
            return

        await ctx.defer(ephemeral=True)

        # Já aberto? reaproveita a thread existente.
        thread = None
        existing = event.get('regear_thread_id')
        if existing:
            thread = self.bot.get_channel(existing)
            if thread is None:
                try:
                    thread = await self.bot.fetch_channel(existing)
                except discord.HTTPException:
                    thread = None
        novo = thread is None
        if novo:
            thread = await self._open_regear_thread(ctx.guild, event)
        if thread is None:
            await send_warn(ctx, "Não consegui abrir o regear — confira o **canal de zergregear** "
                                 "no `/setup`.")
            return

        n = await self._ping_regear_participants(thread, evento)
        verb = "aberto" if novo else "já estava aberto"
        await send_ok(ctx, f"Regear da **CTA #{evento}** {verb}: {thread.mention}.\n"
                           f"🔔 {n} participante(s) marcado(s) (mensagem temporária).")
        print(f"✓ Regear da CTA #{evento} aberto por {ctx.author.display_name} ({n} pings)")

    async def _open_regear_thread(self, guild, event):
        """Cria a thread de regear no canal de zergregear. Retorna a thread ou None."""
        cfg = await load_economy_config()
        zerg_id = cfg.get('channel_zergregear')
        if not zerg_id or not guild:
            return None
        ch = guild.get_channel(zerg_id)
        if not isinstance(ch, discord.TextChannel):
            return None
        try:
            thread = await ch.create_thread(
                name=_event_base_name(event), type=discord.ChannelType.public_thread,
            )
        except Exception as e:
            print(f"✗ Erro criando thread de regear (CTA #{event['id']}): {e}")
            return None
        await update_event_meta(event['id'], regear_thread_id=thread.id)
        try:
            await thread.send(f"🛡️ **Regear da {_event_base_name(event)}** — postem os regears aqui.")
        except discord.HTTPException:
            pass
        return thread

    async def _ping_regear_participants(self, thread, event_id: int) -> int:
        """Marca todos os participantes do CTA em mensagens TEMPORÁRIAS (auto-apagam)."""
        atts = await get_event_attendances(event_id)
        uids = [uid for uid, *_ in atts]
        if not uids:
            return 0
        CHUNK = 80   # ~80 menções por mensagem (limite de 2000 chars)
        temp_msgs = []
        for i in range(0, len(uids), CHUNK):
            mentions = " ".join(f"<@{u}>" for u in uids[i:i + CHUNK])
            try:
                m = await thread.send(
                    f"🛡️ **Regear aberto!** Postem seus prints/regears aqui 👇\n{mentions}",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
                temp_msgs.append(m)
            except discord.HTTPException as e:
                print(f"✗ Erro marcando participantes do regear (CTA #{event_id}): {e}")
        if temp_msgs:
            asyncio.create_task(self._delete_msgs_later(temp_msgs, REGEAR_PING_DELETE_SECONDS))
        return len(uids)

    async def _delete_msgs_later(self, msgs, seconds: int):
        await asyncio.sleep(seconds)
        for m in msgs:
            try:
                await m.delete()
            except discord.HTTPException:
                pass

    # ==================================================================
    # /adiarcta  — adia um CTA (em andamento ou agendado) p/ novo horário
    # ==================================================================
    @commands.hybrid_command(
        name="adiarcta",
        description="Adia um CTA (em andamento ou agendado) para um novo horário UTC.",
    )
    @app_commands.guild_only()
    @app_commands.describe(evento="CTA ativo a adiar", time="Novo horário UTC (ex: 21:30)")
    @app_commands.autocomplete(evento=adiarcta_autocomplete, time=time_autocomplete)
    async def adiarcta(self, ctx: commands.Context, evento: int, time: str):
        event = await get_event_by_id(evento)
        if not event:
            await send_err(ctx, f"CTA #{evento} não existe.")
            return
        if event.get('ended_at'):
            await send_err(ctx, f"CTA #{evento} já foi encerrado — não dá pra adiar.")
            return
        if not await self._check_caller_or_staff(ctx, event):
            await send_err(ctx, "Apenas o caller, council ou logistic.")
            return

        new_dt = parse_utc_time(time)
        if not new_dt:
            await send_err(ctx, "Horário inválido. Use `HH:MM` (UTC).")
            return
        # Colisão: outro CTA ativo no mesmo horário.
        for ev in await get_active_ctas():
            if ev['id'] == evento:
                continue
            d = _event_started_dt(ev)
            if d and d == new_dt:
                await send_err(ctx, f"Já existe outra CTA (#{ev['id']}) marcada para esse horário.")
                return

        await ctx.defer(ephemeral=True)

        # Apaga o aviso de pré-início antigo (se houver) e reseta o move/aviso, pra
        # re-disparar no novo horário.
        massinfo_cog = self.bot.cogs.get('MassInfoCog')
        if massinfo_cog and (event.get('prestart_msg_id') or event.get('startboard_msg_id')):
            try:
                await massinfo_cog._delete_prestart_warning(event)
            except Exception as e:
                print(f"✗ Erro apagando aviso de pré-início ao adiar CTA #{evento}: {e}")

        old_dt = datetime.fromisoformat(event['started_at'])
        if old_dt.tzinfo is None:
            old_dt = old_dt.replace(tzinfo=timezone.utc)
        old_unix = int(old_dt.timestamp())

        await update_event_meta(
            evento, started_at=new_dt.isoformat(), pre_start_moved=0, prestart_msg_id=None,
        )

        new_unix = int(new_dt.timestamp())
        await _cta_audit(ctx, evento,
                         f"⏰ CTA adiada: <t:{old_unix}:t> → <t:{new_unix}:t>")

        if massinfo_cog:
            try:
                await massinfo_cog.refresh_massinfo(recreate=True)
            except Exception as e:
                print(f"✗ Erro refrescando mass-info ao adiar CTA #{evento}: {e}")

        sched = new_dt > datetime.now(timezone.utc)
        await send_ok(ctx, f"**CTA #{evento} adiada** para <t:{new_unix}:t> UTC · <t:{new_unix}:R> "
                           f"({'agendada' if sched else 'em andamento'}).")
        print(f"✓ CTA #{evento} adiada para {new_dt.isoformat()} por {ctx.author.display_name}")

    # ==================================================================
    # Voice: mover Call to Arms -> Waiting Room
    # ==================================================================
    def _resolve_guild(self, event: dict):
        gid = event.get('guild_id')
        if gid:
            g = self.bot.get_guild(gid)
            if g:
                return g
        return self.bot.guilds[0] if self.bot.guilds else None

    async def _move_arms_to_waiting(self, guild) -> int:
        """
        Move pro waitingroom quem estava no calltoarms (voice_cta), em lotes de 5.

        IMPORTANTE: a lista é "fotografada" UMA VEZ no começo (members abaixo) e o
        loop percorre só essa lista fixa. Então:
          · quem ENTRA no canal durante o move NÃO é movido (não está na foto);
          · cada pessoa é movida UMA vez — se voltar pro canal durante o move, não
            é re-iterada (o loop não relê arms.members), então não é movida de novo.
        """
        if guild is None:
            return 0
        cfg     = await load_economy_config()
        arms_id = cfg.get('voice_cta')
        wait_id = cfg.get('voice_waitingroom')
        if not arms_id or not wait_id:
            return 0
        arms    = guild.get_channel(arms_id)
        waiting = guild.get_channel(wait_id)
        if not isinstance(arms, discord.VoiceChannel) or not isinstance(waiting, discord.VoiceChannel):
            return 0

        snapshot = [m for m in arms.members if not m.bot]   # foto única no início
        moved = 0
        for i in range(0, len(snapshot), VOICE_MOVE_BATCH):
            for m in snapshot[i:i + VOICE_MOVE_BATCH]:
                # confirma que a pessoa ainda está no calltoarms (pode ter saído)
                if m.voice is None or m.voice.channel is None or m.voice.channel.id != arms_id:
                    continue
                try:
                    await m.move_to(waiting, reason="CTA: Call to Arms → Waiting Room")
                    moved += 1
                except (discord.Forbidden, discord.HTTPException):
                    pass
            if i + VOICE_MOVE_BATCH < len(snapshot):
                await asyncio.sleep(1)   # respeita rate limit entre lotes
        if moved:
            print(f"✓ Voice: {moved} movido(s) de Call to Arms → Waiting Room")
        return moved

    async def _safe_move_arms(self, guild, event_id):
        """Wrapper p/ rodar o move em background sem estourar exceção solta."""
        try:
            await self._move_arms_to_waiting(guild)
        except Exception as e:
            print(f"✗ Erro movendo voice (CTA #{event_id}): {e}")

    async def _apply_trial_discount(self, event_id: int, guild, cfg: dict):
        """Marca quem é trial entre os participantes e recalcula a participação."""
        perc = cfg.get('trial_percent')
        if perc is None:
            perc = 20
        trial_role_id = cfg.get('role_trial')
        trial_ids = []
        if trial_role_id and guild is not None:
            for uid, *_ in await get_event_attendances(event_id):
                m = guild.get_member(uid)
                if m and any(r.id == trial_role_id for r in m.roles):
                    trial_ids.append(uid)
        await mark_event_trials(event_id, trial_ids)
        await recompute_trial_discount(event_id, perc)

    # ==================================================================
    # Snapshot loop
    # ==================================================================
    @tasks.loop(seconds=SNAPSHOT_INTERVAL_SECONDS)
    async def snapshot_loop(self):
        for gid in await get_activated_guild_ids():
            with database.using_guild(gid):
                try:
                    await self._snapshot_once()
                except Exception as e:
                    print(f"✗ snapshot_loop [{gid}]: {e}")

    async def _snapshot_once(self):
        events = await get_active_ctas()
        if not events:
            return

        now = datetime.now(timezone.utc)

        # Pré-início: CTAs AGENDADOS perto de começar -> move Call to Arms ->
        # Waiting Room (uma única vez por evento, marcado em pre_start_moved).
        # MAS se já houver um CTA EM ANDAMENTO, NÃO mexe na call — senão arrancaria
        # o pessoal do CTA ativo. Adia (NÃO marca o flag) até não ter CTA rodando.
        any_running = any(not is_scheduled(ev, now) for ev in events)
        for ev in events:
            if ev.get('pre_start_moved') or not is_scheduled(ev, now):
                continue
            ev_dt = _event_started_dt(ev)
            if ev_dt and (ev_dt - now).total_seconds() <= PRE_START_MOVE_SECONDS:
                if any_running:
                    continue   # tem CTA em andamento → não mexe na call agora
                # Marca ANTES (evita repetir) e move em background (não trava o loop).
                await update_event_meta(ev['id'], pre_start_moved=1)
                asyncio.create_task(self._safe_move_arms(self._resolve_guild(ev), ev['id']))

        # Só CTAs EM ANDAMENTO (horário já chegou) viram snapshot.
        # As AGENDADAS aparecem no mass-info e aceitam registro, mas não contam
        # presença até o timer iniciar.
        running = [ev for ev in events if not is_scheduled(ev, now)]
        if not running:
            return

        cfg      = await load_economy_config()
        voice_id = cfg.get('voice_cta')
        if not voice_id:
            return

        # Só conta presença de quem tem um dos cargos elegíveis (configurados no
        # /setup). Sem nenhum cargo elegível configurado → não filtra (conta todos),
        # pra não zerar a presença num servidor que ainda não configurou os cargos.
        allowed_role_ids = {
            cfg[k] for k in SNAPSHOT_ELIGIBLE_ROLE_KEYS if cfg.get(k)
        }

        for event in running:
            # Resolve guild
            guild_id = event.get('guild_id')
            guild = None
            if guild_id:
                guild = self.bot.get_guild(guild_id)
            if not guild:
                for g in self.bot.guilds:
                    guild = g
                    break
            if not guild:
                continue

            channel = guild.get_channel(voice_id)
            if not isinstance(channel, discord.VoiceChannel):
                continue

            present = [
                (m.id, m.display_name) for m in channel.members
                if not m.bot and (
                    not allowed_role_ids
                    or any(r.id in allowed_role_ids for r in m.roles)
                )
            ]
            await add_snapshot(event['id'], present)

    @snapshot_loop.before_loop
    async def before_snapshot_loop(self):
        await self.bot.wait_until_ready()

    # ==================================================================
    # Limpeza de planilhas (CTA em andamento: 2h após o /callout)
    # ==================================================================
    @tasks.loop(seconds=SHEET_CLEANUP_INTERVAL_SECONDS)
    async def sheet_cleanup_loop(self):
        for gid in await get_activated_guild_ids():
            with database.using_guild(gid):
                try:
                    await self._sheet_cleanup_once()
                except Exception as e:
                    print(f"✗ sheet_cleanup_loop [{gid}]: {e}")

    async def _sheet_cleanup_once(self):
        """Apaga planilhas de CTAs finalizados quando o prazo de 2h (sheet_delete_at) vence."""
        if not await sheets.is_configured():
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            due = await get_due_sheet_deletions(now_iso)
        except Exception as e:
            print(f"✗ Erro consultando planilhas a apagar: {e}")
            return

        for event in due:
            event_id = event.get('id')
            page = event.get('sheet_page')
            if not page:
                # Nada a apagar — só limpa o agendamento.
                await update_event_meta(event_id, sheet_delete_at=None)
                continue
            try:
                ok = await sheets.delete_cta_page(page)
            except Exception as e:
                print(f"✗ Erro apagando planilha do CTA #{event_id}: {e}")
                continue  # tenta de novo no próximo ciclo
            if ok:
                # Sucesso → zera o agendamento e os links (evita re-deleção/links mortos).
                await update_event_meta(
                    event_id, sheet_delete_at=None, sheet_page=None, sheet_url=None,
                )
                print(f"✓ Planilha do CTA #{event_id} apagada (2h após o callout).")
            # se não confirmou (ok=False), deixa pra próxima tentativa.

    @sheet_cleanup_loop.before_loop
    async def before_sheet_cleanup_loop(self):
        await self.bot.wait_until_ready()

    # ==================================================================
    # Aquecimento do cache de comps (a cada 5min)
    # ==================================================================
    @tasks.loop(minutes=30)
    async def comps_warmup_loop(self):
        """
        Mantém o cache de comps quente em background, POR SERVIDOR (cada guild tem
        sua planilha). Assim o autocomplete do /cta nunca aguarda a rede (~3s).
        """
        for gid in await get_activated_guild_ids():
            with database.using_guild(gid):
                try:
                    if await sheets.is_configured():
                        await self._refresh_comps_cache(gid)
                except Exception as e:
                    print(f"✗ comps_warmup_loop [{gid}]: {e}")

    @comps_warmup_loop.before_loop
    async def before_comps_warmup_loop(self):
        await self.bot.wait_until_ready()

    # ==================================================================
    # Auto-refresh do embed (5min)
    # ==================================================================
    @tasks.loop(seconds=EMBED_UPDATE_INTERVAL_SECONDS)
    async def embed_refresh_loop(self):
        for gid in await get_activated_guild_ids():
            with database.using_guild(gid):
                try:
                    await self._embed_refresh_once()
                except Exception as e:
                    print(f"✗ embed_refresh_loop [{gid}]: {e}")

    async def _embed_refresh_once(self):
        """Reedita embeds de eventos não finalizados pra manter interactions vivos."""
        from database import get_recent_unfinalized_event_ids
        event_ids = await get_recent_unfinalized_event_ids(limit=5)

        for event_id in event_ids:
            try:
                await self._refresh_event_embed(event_id)
            except Exception as e:
                print(f"✗ Erro ao atualizar embed do evento {event_id}: {e}")

    @embed_refresh_loop.before_loop
    async def before_embed_refresh(self):
        await self.bot.wait_until_ready()

    # ==================================================================
    # Finalização do evento (chamado pelo /callout — sem perguntar regear)
    # ==================================================================
    async def _finalize_event(self, ctx_or_interaction, event_id: int):
        """Anti-duplicação: serializa o encerramento do MESMO CTA. Callouts
        concorrentes (2 pessoas ao mesmo tempo) duplicavam o evento/threads — só o
        PRIMEIRO encerra; os demais recebem aviso e saem."""
        gid = database.get_current_guild()
        key = (gid, event_id)
        if key in self._finalizing:                    # (check)
            await send_err(ctx_or_interaction,
                           "⏳ Esse CTA já está sendo finalizado — aguarde.")
            return
        self._finalizing.add(key)                      # (claim) — colado ao check: atômico
        try:
            ev_now = await get_event_by_id(event_id)
            if not ev_now or ev_now.get('ended_at'):
                await send_err(ctx_or_interaction, "Essa CTA não está mais ativa.")
                return
            await self._finalize_event_inner(ctx_or_interaction, event_id)
        finally:
            self._finalizing.discard(key)

    async def _finalize_event_inner(self, ctx_or_interaction, event_id: int):
        # Aceita Context (/callout direto) ou Interaction (menu de seleção).
        is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
        if is_interaction:
            if not ctx_or_interaction.response.is_done():
                await ctx_or_interaction.response.defer(ephemeral=True)
        else:
            try:
                await ctx_or_interaction.defer(ephemeral=True)
            except discord.HTTPException:
                pass
        guild = ctx_or_interaction.guild

        async def _respond(text: str):
            if is_interaction:
                await ctx_or_interaction.followup.send(text, ephemeral=True)
                try:
                    await utils.schedule_ephemeral_from_ctx(ctx_or_interaction)
                except Exception:
                    pass
            else:
                await ctx_or_interaction.send(text, ephemeral=True)

        # had_regear agora é DERIVADO: teve regear se um ticket foi aberto (/openregear).
        ev0 = await get_event_by_id(event_id)
        had_regear = bool(ev0 and ev0.get('regear_thread_id'))

        # Sem NENHUMA participação registrada (ninguém apareceu nos snapshots / nenhum
        # /attendance) → NÃO gera evento. Limpa tudo, igual a uma agendada cancelada.
        if ev0 and not await get_event_attendances(event_id):
            await self._finalize_no_participation(ev0, _respond)
            return

        # 1) Atualiza DB (ended_at + percentuais)
        await end_cta_event(event_id, had_regear)

        # 1b) Desconto de participação dos TRIALS: marca quem é trial entre os
        # participantes e recalcula o percent. Feito ANTES dos refreshes/embeds
        # pra tudo já refletir o valor descontado.
        try:
            await self._apply_trial_discount(event_id, guild,
                                              await load_economy_config())
        except Exception as e:
            print(f"✗ Erro aplicando desconto de trial no CTA #{event_id}: {e}")

        # Refresca mass-info (CTA encerrou — sai da lista)
        massinfo_cog = self.bot.cogs.get('MassInfoCog')
        if massinfo_cog:
            try:
                await massinfo_cog.refresh_massinfo(recreate=True)
            except Exception as e:
                print(f"✗ Erro refrescando mass-info: {e}")
            # Apaga o aviso de início — o ping de '10 min'/flash E o painel "MASSANDO
            # AGORA" (startboard) — pra não sobrar órfão ao dar /callout.
            if ev0 and (ev0.get('prestart_msg_id') or ev0.get('startboard_msg_id')):
                try:
                    await massinfo_cog._delete_prestart_warning(ev0)
                except Exception as e:
                    print(f"✗ Erro apagando aviso de pré-início do CTA #{event_id}: {e}")
            # Limpa o canal de mass-info por completo, deixando só a embed canônica.
            try:
                await massinfo_cog.clear_massinfo_channel()
            except Exception as e:
                print(f"✗ Erro limpando canal de mass-info do CTA #{event_id}: {e}")
        # CTA encerrado mas split não finalizado → entra na embed de splits
        splits_cog = self.bot.cogs.get('SplitsCog')
        if splits_cog:
            try:
                await splits_cog.refresh_splits()
            except Exception as e:
                print(f"✗ Erro refrescando splits pós /end: {e}")

        # 2) Pega o evento atualizado e dados necessários
        event = await get_event_by_id(event_id)
        cfg   = await load_economy_config()

        # Obs: NÃO movemos a call no callout — a movimentação Call to Arms → Waiting
        # Room acontece só faltando ~10 min pro próximo CTA começar (snapshot_loop).

        # Planilha: CTA em andamento finalizado → agenda exclusão da página em 2h.
        # O loop sheet_cleanup_loop apaga quando o horário chegar (sobrevive a restart).
        if event and event.get('sheet_page'):
            delete_at = (datetime.now(timezone.utc)
                         + timedelta(hours=SHEET_DELETE_DELAY_HOURS)).isoformat()
            try:
                await update_event_meta(event_id, sheet_delete_at=delete_at)
            except Exception as e:
                print(f"✗ Erro agendando exclusão da planilha do CTA #{event_id}: {e}")

        # 3) Cria threads e embed
        result = await self._create_post_event(guild, event, cfg)

        # 4) Feedback útil pro usuário
        if result and result.get('main_thread'):
            await _respond(
                f"✅ CTA #{event_id} finalizada.\n"
                f"Embed em <#{result['main_thread'].id}>."
            )
        else:
            issues = result.get('issues', []) if result else ['_create_post_event não retornou nada']
            await _respond(
                f"⚠️ CTA #{event_id} finalizada no DB, mas o embed não foi postado.\n"
                f"**Problemas:**\n" + "\n".join(f"• {i}" for i in issues)
            )
        print(f"✓ CTA #{event_id} finalizada (regear={had_regear})")

    async def _create_post_event(self, guild, event, cfg) -> dict:
        """
        Cria threads no events/logger/zergregear e posta embed final na thread de eventos.
        Retorna {'main_thread': Thread | None, 'issues': [str]} para feedback ao usuário.
        """
        events_chan_id = cfg.get('channel_events')
        logger_chan_id = cfg.get('channel_logger')
        zerg_chan_id   = cfg.get('channel_zergregear')
        issues: list[str] = []

        # Nome base das threads
        started_dt = datetime.fromisoformat(event['started_at'])
        if started_dt.tzinfo is None:
            started_dt = started_dt.replace(tzinfo=timezone.utc)
        base_name = f"CTA #{event['id']} — {started_dt.strftime('%d/%m %H:%M UTC')}"

        # Guardamos referências DIRETAS das threads recém-criadas
        # (NÃO usar guild.get_channel(id) — cache pode não ter atualizado ainda)
        main_thread   = None
        logger_thread = None

        # Thread principal no canal de eventos
        if not events_chan_id:
            issues.append("`channel_events` não configurado. Rode `/setup channel_events:#canal`.")
            print("✗ channel_events não configurado")
        else:
            ch = guild.get_channel(events_chan_id) if guild else None
            if ch is None:
                issues.append(f"channel_events (id `{events_chan_id}`) não encontrado pelo bot.")
            elif not isinstance(ch, discord.TextChannel):
                issues.append(f"channel_events não é um canal de texto (é {type(ch).__name__}).")
            else:
                try:
                    main_thread = await ch.create_thread(
                        name=base_name,
                        type=discord.ChannelType.public_thread,
                    )
                    print(f"✓ Thread de eventos criada: {main_thread.id}")
                except discord.Forbidden:
                    issues.append(f"Bot sem permissão de criar threads em {ch.mention}.")
                except Exception as e:
                    issues.append(f"Erro criando thread no canal de eventos: {e}")

        # Thread no logger
        if logger_chan_id:
            ch = guild.get_channel(logger_chan_id) if guild else None
            if isinstance(ch, discord.TextChannel):
                try:
                    logger_thread = await ch.create_thread(
                        name=base_name,
                        type=discord.ChannelType.public_thread,
                    )
                except Exception as e:
                    print(f"✗ Erro criando thread no canal de logger: {e}")

        # Instrução de envio: thread PÚBLICA de logger (fecha em 30 min). Aberto a
        # TODO MUNDO (sem cargo); o envio em si é privado (via /enviarlog ephemeral).
        if logger_thread is not None:
            try:
                from cogs.lootlog import EnviarLogView   # import local: evita ciclo na carga
                tip = discord.Embed(
                    title="📥  Envio de logs deste CTA",
                    description=(
                        "**Qualquer um** que rodou o lootlogger pode enviar: clique em "
                        "**📤 Enviar log** abaixo e anexe o **`.csv`** no modal que abrir — "
                        "quem enviar entra na fatia de loggers do split.\n\n"
                        "🔒 O envio é **privado** — ninguém vê o conteúdo.\n"
                        "🔁 Pode reenviar; o último vale.\n"
                        "⏳ Esta thread **fecha em 30 minutos**."
                    ),
                    color=EMBED_INFO,
                )
                await logger_thread.send(
                    embed=tip,
                    view=EnviarLogView(),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception as e:
                print(f"✗ Erro postando instrução de log no CTA #{event['id']}: {e}")

        # Obs: a thread de regear NÃO é criada aqui — ela é aberta pelo /openregear
        # (durante/depois do CTA) e o regear_thread_id é preservado.

        # Persistir IDs no DB. A thread PÚBLICA de logger é apagada 30 min depois
        # (loop em LootLogCog); guardamos o prazo aqui. NÃO mexemos no regear_thread_id.
        public_delete_at = (
            (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
            if logger_thread else None
        )
        await update_event_meta(
            event['id'],
            event_thread_id  = main_thread.id   if main_thread   else None,
            logger_thread_id = logger_thread.id if logger_thread else None,
            logger_thread_delete_at = public_delete_at,
        )

        # Postar embed na thread principal USANDO A REFERÊNCIA DIRETA
        if main_thread:
            try:
                embed = await self._build_event_embed(event['id'])
                view  = EventEmbedView()
                msg   = await main_thread.send(embed=embed, view=view)
                await update_event_meta(event['id'], event_message_id=msg.id)
                print(f"✓ Embed do evento postado na thread {main_thread.id} (msg {msg.id})")
            except discord.Forbidden:
                issues.append(f"Bot sem permissão de postar na thread <#{main_thread.id}>.")
                main_thread = None  # invalida pra feedback ficar correto
            except Exception as e:
                issues.append(f"Erro postando embed do evento: {e}")
                main_thread = None
                print(f"✗ Erro postando embed do evento: {e}")

        return {'main_thread': main_thread, 'issues': issues}

    # ==================================================================
    # Embed builder
    # ==================================================================
    async def _build_event_embed(self, event_id: int) -> discord.Embed:
        event        = await get_event_by_id(event_id)
        attendances  = await get_event_attendances(event_id)
        enlisted_ids = await get_enlisted_user_ids(event_id)   # 🕒 adicionados após o evento

        started_dt = datetime.fromisoformat(event['started_at'])
        if started_dt.tzinfo is None: started_dt = started_dt.replace(tzinfo=timezone.utc)
        ended_dt   = datetime.fromisoformat(event['ended_at']) if event.get('ended_at') else None
        if ended_dt and ended_dt.tzinfo is None: ended_dt = ended_dt.replace(tzinfo=timezone.utc)

        embed = discord.Embed(
            title=f"📑  𝐄𝐕𝐄𝐍𝐓𝐎 {bold_number(event_id)}",
            color=EMBED_INFO,
        )

        # ----- Cabeçalho limpo: só o que importa (Horário · Foram · Split) -----
        split_txt = (f"`{format_silver(int(event.get('repair_value') or 0))}`"
                     if event.get('split_defined') else "0")
        bb_link = event.get('battleboard_url')
        if bb_link:
            embed.url = bb_link   # título do embed vira link clicável pra batalha
        embed.add_field(name=f"🕒  <t:{int(started_dt.timestamp())}:f>", value=f"", inline=True)
        embed.add_field(name=f"👥  {len(attendances)}",   value=f"",                inline=True)
        embed.add_field(name=f"💰  {split_txt}",   value=f"",                       inline=True)

        # ----- Participantes em 3 colunas -----
        if attendances:
            n = len(attendances)
            per_col = (n + 2) // 3   # arredonda pra cima dividido por 3
            cols = [attendances[i*per_col:(i+1)*per_col] for i in range(3)]

            for i, col in enumerate(cols):
                if not col:
                    embed.add_field(name="​", value="​", inline=True)
                    continue
                lines = []
                for uid, _uname, pct, _silver in col:
                    if pct < 50:
                        badge = f"`⚠️{pct}%`"
                    else:
                        badge = f"`{pct}%`"
                    # 🕒 = adicionado via late-attend (estava fora da call)
                    suffix = " 🕒" if uid in enlisted_ids else ""
                    lines.append(f"{badge} <@{uid}>{suffix}")
                title = f"#{i*per_col+1}–{i*per_col+len(col)}"
                embed.add_field(
                    name=title,
                    value=_fit_field(lines),   # cap em 1024 chars (evita HTTP 400)
                    inline=True,
                )
        else:
            embed.add_field(
                name="",
                value="*Ninguém foi detectado na call.*",
                inline=False,
            )

        # ----- No split mas NÃO registrou na planilha (mass-info) -----
        non_pingers = await get_non_pingers(event_id)
        if non_pingers:
            shown = non_pingers[:30]
            np_lines = [f"❗ <@{p['user_id']}> `{p['percent']}%`" for p in shown]
            extra = len(non_pingers) - len(shown)
            if extra > 0:
                np_lines.append(f"… e mais **{extra}**")
            # Em 3 colunas (itens curtos, lista potencialmente longa) — igual aos participantes.
            cols = _column_fields(np_lines, ncols=3)
            for i, col in enumerate(cols):
                embed.add_field(
                    name=(f"🚫  No split mas não registraram ({len(non_pingers)})"
                          if i == 0 else "​"),
                    value=col,
                    inline=True,
                )
            embed.add_field(
                name="​",
                value="*(adicionar pelo late-attend remove daqui)*",
                inline=False,
            )

        # ----- Loggers (quem enviou .csv via /enviarlog) -----
        log_subs = await get_log_submissions(event_id)
        if log_subs:
            embed.add_field(
                name=f"🪵  Loggers ({len(log_subs)})",
                value=_fit_field([f"<@{s['submitter_id']}>" for s in log_subs]),
                inline=False,
            )

        # ----- Nodes próximos (±30min do end) -----
        if ended_dt:
            nearby = await get_nodes_near(int(ended_dt.timestamp()), NODES_NEAR_THRESHOLD_SECONDS)
            if nearby:
                # Pegar IDs capturados pra marcar visualmente
                captured = await get_event_captured_nodes(event_id)
                captured_ids = {n[0] for n in captured}

                node_map = await load_node_map()
                node_lines = []
                for node_log_id, node_type, map_name, scout, _scout_id, spawn_ts in nearby[:20]:
                    emoji = node_emoji_of(node_map, node_type)
                    # Marca se foi capturado (✅) ou não decidido / não capturado (▫️)
                    if event.get('split_defined'):
                        # Split já foi definido — mostrar status real
                        mark = "✅" if node_log_id in captured_ids else "❌"
                    else:
                        mark = "▫️"
                    node_lines.append(
                        f"{mark} {emoji} <t:{spawn_ts}:t> · **{node_type}** · 🗺️ {map_name} · 🔎 {scout}"
                    )
                embed.add_field(
                    name=f"🌿  Nodes próximos (±30min)  ·  {len(nearby)} total",
                    value="\n".join(node_lines),
                    inline=False,
                )
        
        #Set footer
        embed.set_footer(text="✏️ Alterar Participação · 🫷 Remover Participação · 💰 Definir Split · ✅ Finalizar Evento")

        return embed

    async def _refresh_event_embed(self, event_id: int):
        """Reedita a mensagem do embed do evento."""
        event = await get_event_by_id(event_id)
        if not event or not event.get('event_thread_id') or not event.get('event_message_id'):
            return

        # Resolve guild
        guild = self.bot.get_guild(event.get('guild_id') or 0)
        if not guild:
            for g in self.bot.guilds:
                guild = g; break
        if not guild:
            return

        thread = guild.get_channel(event['event_thread_id'])
        if thread is None:
            try:
                thread = await self.bot.fetch_channel(event['event_thread_id'])
            except Exception:
                return

        try:
            msg = await thread.fetch_message(event['event_message_id'])
            embed = await self._build_event_embed(event_id)
            await msg.edit(embed=embed, view=EventEmbedView())
        except discord.NotFound:
            pass
        except Exception as e:
            print(f"✗ Erro reeditando embed: {e}")

    # ==================================================================
    # Finalização do split (Fase 5)
    # ==================================================================
    async def _finalize_split(self, interaction: discord.Interaction, event_id: int):
        """
        Calcula payouts, distribui prata, posta invoice nas threads
        de events + logger, posta resumo no economylogs e bloqueia as threads.
        """
        event = await get_event_by_id(event_id)
        if not event:
            await send_err(interaction, "Evento não encontrado.")
            return
        if event.get('split_finalized'):
            await send_err(interaction, "Split já finalizado.")
            return

        # Split pode ser 0 (finaliza o evento sem distribuir prata). Só barra negativo
        # e o caso de nunca ter sido definido.
        if not event.get('split_defined'):
            await send_err(interaction, "Você precisa **Definir split** antes de finalizar.")
            return
        total = int(event.get('repair_value') or 0)
        if total < 0:
            await send_err(interaction, "Valor do split inválido.")
            return

        cfg       = await load_economy_config()
        tax_pct   = int(cfg.get('guild_tax_percent') or 0)
        scout_pct = int(cfg.get('node_scout_percent') or 0)
        guild     = interaction.guild

        # Ledger de pagamentos (kind, user_id, amount) — persistido no fim p/ permitir
        # estorno EXATO no /deleteevent (sem recalcular, imune a mudança de config).
        payout_records = []

        # ---------- 1) Guild tax ----------
        tax_amount = (total * tax_pct) // 100
        if tax_amount > 0:
            await add_guild_bank(tax_amount)
            payout_records.append(('guild_bank', None, tax_amount))

        # ---------- 2) Scouts dos nodes CAPTURADOS ----------
        # Só paga scouts cujo node foi marcado como capturado na etapa de
        # "Definir split" (CapturedNodesView).
        captured = await get_event_captured_nodes(event_id)

        # PESO (coluna `weight` dos node_defs do servidor): node_scout_percent é o TETO
        # da fatia dos scouts, atingido quando o peso TOTAL dos nodes >= 1. Abaixo disso,
        # escala linear pelo peso total (ex.: 2 nodes de 0.1 → 0.2 do teto). O pool é
        # dividido entre os scouts proporcional ao peso que CADA um capturou.
        # Estrutura: {scout_id: {"name", "total": int, "weight": float, "nodes": [...]}}
        node_map = await load_node_map()
        scout_pool = {}
        total_weight = 0.0
        for _node_log_id, node_type, map_name, scout_name, scout_id, _ts in captured:
            if scout_id is None:
                continue
            w = node_scout_weight(node_map, node_type)
            total_weight += w
            slot = scout_pool.setdefault(
                scout_id, {"name": scout_name, "total": 0, "weight": 0.0, "nodes": []}
            )
            slot["weight"] += w
            slot["nodes"].append((node_type, map_name))

        scout_max        = (total * scout_pct) // 100          # teto (peso total >= 1)
        scout_pool_value = int(scout_max * min(total_weight, 1.0))
        for sid, slot in scout_pool.items():
            slot["total"] = (int(scout_pool_value * slot["weight"] / total_weight)
                             if total_weight > 0 else 0)
            if slot["total"] > 0:
                await add_user_money(sid, slot["total"])
                payout_records.append(('scout', sid, slot["total"]))

        scout_total = sum(s["total"] for s in scout_pool.values())

        # ---------- 2b) Loggers — pool dividido por PESO ----------
        # 2+ loggers: proporcional a coletas corroboradas (vistas por ambos).
        # 1 logger só: 100% do pool (peso = coletas únicas dele). Cópias zeradas.
        logger_pct  = int(cfg.get('logger_percent') or 0)
        logger_pool = (total * logger_pct) // 100
        logger_payouts = []   # (uid, amount)
        logger_total = 0
        if logger_pool > 0:
            weights = {}
            lootlog_cog = self.bot.cogs.get('LootLogCog')
            if lootlog_cog:
                try:
                    weights, _notes = await lootlog_cog.compute_logger_weights(event_id)
                except Exception as e:
                    print(f"✗ Erro calculando pesos de logger (CTA #{event_id}): {e}")
            total_w = sum(weights.values())
            if total_w > 0:
                for uid, w in weights.items():
                    amount = (logger_pool * w) // total_w
                    if amount > 0:
                        await add_user_money(uid, amount)
                        payout_records.append(('logger', uid, amount))
                    logger_payouts.append((uid, amount))
                logger_total = sum(a for _, a in logger_payouts)

        # ---------- 3) Pool dos participantes ----------
        participant_pool = max(0, total - tax_amount - scout_total - logger_total)
        attendances = await get_event_attendances(event_id)
        sum_pct = sum(pct for _, _, pct, _ in attendances)

        payouts = []   # lista de (uid, name, pct, amount)
        if sum_pct > 0 and participant_pool > 0:
            for uid, uname, pct, _silver in attendances:
                if pct <= 0:
                    continue
                amount = (participant_pool * pct) // sum_pct
                if amount > 0:
                    await add_user_money(uid, amount)
                    await set_attendance_silver(event_id, uid, amount)
                    payout_records.append(('participant', uid, amount))
                payouts.append((uid, uname, pct, amount))

        # ---------- 4) Marcar como finalizado ----------
        await mark_event_split_finalized(event_id)
        # Ledger p/ estorno exato no /deleteevent (participantes, scouts, loggers, banco).
        await record_event_payouts(event_id, payout_records)

        # Refresca mass-info (CTA não é mais "pendente" — sai da lista)
        massinfo_cog = self.bot.cogs.get('MassInfoCog')
        if massinfo_cog:
            try:
                await massinfo_cog.refresh_massinfo(recreate=True)
            except Exception as e:
                print(f"✗ Erro refrescando mass-info pós split: {e}")
        # Split finalizado → sai da embed de splits do bombleaderchat
        splits_cog = self.bot.cogs.get('SplitsCog')
        if splits_cog:
            try:
                await splits_cog.refresh_splits()
            except Exception as e:
                print(f"✗ Erro refrescando splits pós finalização: {e}")

        # ---------- 4b) Resumo no economylogs + hospeda o print da tab ----------
        # O print da tab vai ANEXADO a este resumo (canal importante e persistente);
        # a URL hospedada vira o link discreto (🧾) no título do invoice, em vez de
        # anexar o print como spoiler na thread.
        tab_url  = event.get('tab_image_url') or None
        tab_blob = event.get('tab_image_blob')
        elogs_id = cfg.get('channel_economylogs')
        if elogs_id and guild:
            ch = guild.get_channel(elogs_id)
            if ch:
                try:
                    summary = discord.Embed(
                        title=f"📦  𝐒𝐏𝐋𝐈𝐓 {bold_number(event_id)}",
                        description=(
                            f"💰  total **{format_silver(total)}**\n\n"
                            f"🏛️ tax `{format_silver(tax_amount)}` ({tax_pct}%)  ·  "
                            f"🔎 scouts `{format_silver(scout_total)}` ({len(scout_pool)})  ·  "
                            f"👥 split `{format_silver(participant_pool)}` ({len(payouts)})"
                        ),
                        color=EMBED_OK,
                    )
                    summary.set_footer(text=f"por {interaction.user.display_name}")
                    if tab_blob:
                        f = discord.File(io.BytesIO(tab_blob), filename=f"tab_{event_id}.png")
                        smsg = await ch.send(embed=summary, file=f)
                        if smsg.attachments:
                            tab_url = smsg.attachments[0].url
                    else:
                        await ch.send(embed=summary)
                except Exception as e:
                    print(f"✗ Erro postando resumo no economylogs: {e}")

        # Obs: o sorteio de looters (Ponto 2) é feito DURANTE o evento (botão
        # "Sortear" no looterchat), não na finalização do split. O CTA já saiu
        # do looterchat ao ser encerrado (/end).

        # ---------- 5) Construir invoice (1+ embeds; NUNCA vira arquivo) ----------
        # Eventos grandes (60-200) são PAGINADOS em vários embeds/mensagens em vez de
        # virar um .txt — tudo continua no Discord.
        invoices = self._build_invoice_embeds(
            event_id      = event_id,
            event         = event,
            total         = total,
            tax_pct       = tax_pct,
            tax_amount    = tax_amount,
            scout_pct     = scout_pct,
            scout_pool    = scout_pool,
            scout_total   = scout_total,
            logger_pct    = logger_pct,
            logger_payouts = logger_payouts,
            logger_total  = logger_total,
            participants_pool = participant_pool,
            payouts       = payouts,
            sum_pct       = sum_pct,
            tab_url       = tab_url,
        )

        # ---------- 6) Postar invoice (SÓ na thread de eventos) ----------
        # O print da tab agora vive no economylogs (passo 4b) e é linkado pelo 🧾
        # no título do invoice — sem anexar a imagem aqui.
        tid = event.get('event_thread_id')
        ch = guild.get_channel(tid) if (tid and guild) else None
        if tid and ch is None:
            try:
                ch = await self.bot.fetch_channel(tid)
            except discord.HTTPException:
                ch = None
        if ch is not None:
            for emb in invoices:
                try:
                    await ch.send(embed=emb)
                except discord.HTTPException as e:
                    print(f"✗ Erro postando invoice na thread {tid}: {e}")

        # ---------- 7) Apagar o embed do evento (tinha os botões) ----------
        await self._delete_event_message(event)

        # ---------- 8) Bloquear threads ----------
        async def _lock(thread_id):
            if not thread_id or not guild:
                return
            ch = guild.get_channel(thread_id)
            if ch is None:
                try:
                    ch = await self.bot.fetch_channel(thread_id)
                except Exception:
                    return
            try:
                await ch.edit(locked=True, archived=True)
            except Exception as e:
                print(f"✗ Erro bloqueando thread {thread_id}: {e}")

        await _lock(event.get('event_thread_id'))
        await _lock(event.get('logger_thread_id'))      # já apagada (30min), no-op
        await _lock(event.get('regear_thread_id'))
        await _lock(event.get('logreview_thread_id'))   # thread privada da logística

        await send_ok(interaction, f"Split da CTA #{event_id} finalizado.\n"
                                   f"Pago a {len(payouts)} participante(s) e "
                                   f"{len(scout_pool)} scout(s).\nThreads bloqueadas.")
        print(f"✓ Split CTA #{event_id} finalizado")

    def _build_invoice_embeds(
        self, *, event_id, event, total, tax_pct, tax_amount,
        scout_pct, scout_pool, scout_total,
        participants_pool, payouts, sum_pct,
        logger_pct=0, logger_payouts=None, logger_total=0,
        tab_url=None,
    ) -> list[discord.Embed]:
        """Monta o invoice como 1+ embeds. Eventos grandes (60-200) são PAGINADOS em
        vários embeds/mensagens — NUNCA vira arquivo: tudo continua no Discord.
        O cabeçalho fica no 1º embed; os pagamentos quebram em páginas de 3 colunas;
        scouts/loggers/rodapé entram no ÚLTIMO embed."""
        PER_PAGE = 60   # pagamentos por embed (20/coluna × 3) — folga sob os limites do Discord

        pages = [payouts[i:i + PER_PAGE] for i in range(0, len(payouts), PER_PAGE)] or [[]]
        total_pages = len(pages)
        embeds: list[discord.Embed] = []
        battle_url = event.get('battleboard_url')

        for pidx, page in enumerate(pages):
            first = (pidx == 0)
            last  = (pidx == total_pages - 1)
            suffix = "" if total_pages == 1 else f"  ({pidx + 1}/{total_pages})"
            # O print da tab vira um 🧾 DISCRETO no título do 1º embed, clicável
            # (link da imagem hospedada no economylogs) — sem field nem anexo.
            title = f"𝐈𝐍𝐕𝐎𝐈𝐂𝐄 {bold_number(event_id)}{suffix}"
            if first and tab_url:
                title += " 🧾"
            embed = make_embed('info', title=title)
            if first and tab_url:
                embed.url = tab_url
            # ⚔️ como link clicável pra batalha na descrição do 1º embed
            # (embed.url já está ocupado pelo link da tab — não dá pra reusar)
            if first and battle_url:
                embed.description = f"[⚔️]({battle_url})"

            # ----- Cabeçalho só no 1º embed (Split · quantos receberam · Tab) -----
            if first:
                embed.add_field(name=f"💰  {format_silver(total)}",     value=f"", inline=True)
                embed.add_field(name=f"👥  {len(payouts)}", value=f"",         inline=True)
                # Localização da tab (o print é o 🧾 do título).
                loc = event.get('tab_location')
                if loc:
                    embed.add_field(name=f"🗺️  {loc}", value="​", inline=True)

            # ----- Pagamentos desta página em 3 colunas -----
            if page:
                base = pidx * PER_PAGE
                per_col = (len(page) + 2) // 3
                for i in range(3):
                    col = page[i * per_col:(i + 1) * per_col]
                    if not col:
                        embed.add_field(name="​", value="​", inline=True)
                        continue
                    lines = [f"<@{uid}> {format_silver(amount)}" for uid, _n, _p, amount in col]
                    lo = base + i * per_col + 1
                    hi = base + i * per_col + len(col)
                    embed.add_field(name=f" #{lo}–{hi}", value=_fit_field(lines), inline=True)
            elif first:
                embed.add_field(name="💸 Pagamentos", value="*Ninguém com prata a receber.*", inline=False)

            # ----- Scouts/loggers/rodapé só no ÚLTIMO embed -----
            if last:
                if scout_pool:
                    lines = [f"<@{sid}> {format_silver(d['total'])}" for sid, d in scout_pool.items()]
                    embed.add_field(name="🔎 Scouts", value=_fit_field(lines), inline=False)
                if logger_payouts:
                    lines = [f"<@{uid}> {format_silver(amount)}" for uid, amount in logger_payouts]
                    embed.add_field(name="🪵 Loggers", value=_fit_field(lines), inline=False)
                embed.set_footer(
                    text=(f"guildtax {tax_pct}%: {format_silver(tax_amount)}   ·   "
                          f"scouts: {format_silver(scout_total)}   ·   "
                          f"loggers: {format_silver(logger_total)}   ·   "
                          f"split: {format_silver(participants_pool)}")
                )

            embeds.append(embed)

        return embeds

    async def _delete_event_message(self, event):
        """Apaga a mensagem do embed do evento (a que tinha os botões), deixando
        só o invoice na thread."""
        tid = event.get('event_thread_id')
        mid = event.get('event_message_id')
        if not tid or not mid:
            return
        ch = self.bot.get_channel(tid)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(tid)
            except discord.HTTPException:
                return
        try:
            msg = await ch.fetch_message(mid)
            await msg.delete()
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(CTACog(bot))
