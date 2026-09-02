import discord
from discord.ext import commands, tasks
from discord import ui, app_commands
from datetime import datetime, timezone, timedelta
import re
import os
from dotenv import load_dotenv
import database
from database import (
    save_node_calendar, load_node_calendar,
    add_node_event, get_node_events, delete_node_event,
    delete_node_calendar, delete_expired_node_events,
    log_node_event, find_duplicate_node, get_removable_nodes,
    is_server_activated, get_activated_guild_ids,
    add_node_map, remove_node_map, get_node_maps,
    exclude_node_map, unexclude_node_map, get_excluded_node_maps,
    get_node_defs, add_node_def, remove_node_def,
    load_economy_config,
    get_scout_paid_count, export_node_global, get_exported_nodes_for_display,
    get_current_guild,
)
from cogs.economy import has_configured_role
from blackzone_maps import BLACKZONE_MAPS
from utils import EMBED_INFO, TIME_GAP_BREAK_S, make_embed, send_err, send_ok, send_warn, send_info
import utils


async def _node_audit(interaction: discord.Interaction, desc: str, kind: str = 'info'):
    """Posta uma linha de auditoria de node no economy_logs — silencioso se não configurado."""
    guild = interaction.guild
    if not guild:
        return
    eco = interaction.client.cogs.get('Economy')
    if not eco:
        return
    ts = datetime.now(timezone.utc).strftime('%H:%M UTC')
    try:
        await eco._post_economy_log(
            guild,
            title="📋  𝐍𝐎𝐃𝐄𝐒",
            desc=desc,
            kind=kind,
            footer=f"por {interaction.user.display_name}  ·  {ts}",
        )
    except Exception as e:
        print(f"✗ node_audit: {e}")


async def _effective_node_maps() -> list:
    """Lista que aparece no seletor: Black Zone embutida (menos as escondidas) + extras custom."""
    custom   = await get_node_maps()
    excluded = {x.lower() for x in await get_excluded_node_maps()}
    base = [m for m in BLACKZONE_MAPS if m.lower() not in excluded]
    return sorted(set(base) | set(custom), key=str.lower)

load_dotenv()
OWNER_ID = int(os.getenv('OWNER_ID', 0))

SCOUT_RELIABILITY_MIN = 2   # mínimo de eventos pagos como scout nos últimos 30 dias

# ---------------------------------------------------------------------------
# Nodes do Albion Online — TIPOS POR SERVIDOR (nome · emoji · peso).
# Cada staff define os seus em /setup → Nodes (tabela node_defs). O `name` é o
# identificador guardado nos node_events (ex.: "Couro 8.4"); o emoji representa o
# tipo no calendário; o `weight` (0..1+) é o peso do scout no pagamento do split.
# As funções abaixo carregam/consultam isso por servidor (contexto de guild atual).
# ---------------------------------------------------------------------------

async def load_node_defs() -> list:
    """Tipos de node do servidor atual: [{name, emoji, weight}, …] já ordenados."""
    return await get_node_defs()


async def load_node_map() -> dict:
    """Mapa {name: {name, emoji, weight}} dos tipos de node do servidor (lookup rápido)."""
    return {d["name"]: d for d in await get_node_defs()}


def node_emoji_of(node_map: dict, label: str) -> str:
    """Emoji do node a partir de um node_map já carregado. Desconhecido → ❓."""
    info = node_map.get(label) if node_map else None
    return (info.get("emoji") if info else None) or "❓"


def node_scout_weight(node_map: dict, label: str) -> float:
    """Peso (0..1+) do node pro pagamento do scout, a partir de um node_map carregado.
    Usado quando há VÁRIOS scouts no evento. Node desconhecido/legado → 1.0."""
    info = node_map.get(label) if node_map else None
    if not info:
        return 1.0
    try:
        return float(info.get("weight", 1.0))
    except (TypeError, ValueError):
        return 1.0


def node_scout_share_pct(weight: float, scout_pct: float) -> float:
    """% do total do split que UM scout recebe por capturar SÓ este node (teto: o
    `node_scout_percent` quando o peso ≥ 1). É só uma referência — o valor real
    divide entre os nodes/scouts do CTA."""
    try:
        w = max(0.0, float(weight))
    except (TypeError, ValueError):
        w = 1.0
    return (scout_pct or 0) * min(w, 1.0)


def _fmt_pct(p: float) -> str:
    """Porcentagem enxuta: '5%' · '2.5%' · '0.75%' (sem zeros à toa)."""
    s = f"{p:.2f}".rstrip("0").rstrip(".")
    return f"{s or '0'}%"


async def nodemap_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete dos mapas do seletor (BZ embutida + extras) — usado no /removenodemap."""
    cur = (current or '').strip().lower()
    maps = await _effective_node_maps()
    out = [m for m in maps if cur in m.lower()] if cur else maps
    return [app_commands.Choice(name=m[:100], value=m[:100]) for m in out[:25]]


# ---------------------------------------------------------------------------
# Parser de tempo inteligente
# ---------------------------------------------------------------------------
class TimeParser:
    """
    Interpreta dois formatos de entrada de tempo do Albion:

    · Tempo RELATIVO  → ex.: 3h20m | 1h | 45m | 3H20M
      (quanto tempo falta até o node abrir — como o jogo mostra)

    · Horário ABSOLUTO UTC → ex.: 21:30 | 06:25 | 21:30:00
      (horário exato em UTC)
    """

    @staticmethod
    def parse_time(raw: str) -> tuple[bool | None, datetime | None]:
        """
        Retorna (is_absolute, spawn_datetime) ou (None, None) se inválido.
        is_absolute=False → tempo relativo
        is_absolute=True  → horário absoluto UTC
        """
        s = raw.strip()

        # --- Tempo relativo: 3h20m · 3h · 20m (case insensitive) ---
        rel_match = re.fullmatch(r'(?:(\d+)[Hh])?(?:(\d+)[Mm])?', s)
        if rel_match and (rel_match.group(1) or rel_match.group(2)):
            hours   = int(rel_match.group(1) or 0)
            minutes = int(rel_match.group(2) or 0)
            if hours == 0 and minutes == 0:
                return None, None
            spawn = datetime.now(timezone.utc) + timedelta(hours=hours, minutes=minutes)
            return False, spawn

        # --- Horário absoluto: HH:MM ou HH:MM:SS ---
        abs_match = re.fullmatch(r'(\d{1,2}):(\d{2})(?::(\d{2}))?', s)
        if abs_match:
            h  = int(abs_match.group(1))
            m  = int(abs_match.group(2))
            sc = int(abs_match.group(3) or 0)
            if h > 23 or m > 59 or sc > 59:
                return None, None
            now   = datetime.now(timezone.utc)
            spawn = now.replace(hour=h, minute=m, second=sc, microsecond=0)
            if spawn <= now:              # já passou hoje → amanhã
                spawn += timedelta(days=1)
            return True, spawn

        return None, None


# ---------------------------------------------------------------------------
# Fluxo de adição numa ephemeral. As 3 listas (tipo · mapa · horário) ficam juntas;
# CADA UMA some assim que é definida (e o texto passa a mostrar o ✓ da escolha).
#   · Tipo   → 1 select (≤25).
#   · Horário→ faixas de 25min em UTC; a lista pagina (◀️/▶️) até 26h à frente.
#   · Mapa   → se a lista cabe em 25, dropdown direto; se for grande (BZ inteira),
#              um select de FAIXA alfabética (≤25 chunks) → select do mapa daquela
#              faixa. Sem modal: você navega a lista até achar o mapa.
# ---------------------------------------------------------------------------
def _safe_option(label: str, value: str, *, emoji=None, description=None) -> discord.SelectOption:
    """SelectOption tolerante a emoji inválido (custom digitado pela staff): se o emoji
    não for aceito, cai pra opção sem emoji em vez de quebrar a view inteira."""
    label, value = label[:100], value[:100]
    description = (description or None)
    try:
        return discord.SelectOption(label=label, value=value,
                                    emoji=(emoji or None), description=description)
    except Exception:
        return discord.SelectOption(label=label, value=value, description=description)


class _NodeTypeSelect(ui.Select):
    def __init__(self, node_defs: list, scout_pct: float = 0):
        opts = []
        for d in node_defs[:25]:
            # Descrição SUTIL: % do split que o scout recebe por este node (peso × scout%).
            share = node_scout_share_pct(d.get("weight", 1.0), scout_pct)
            opts.append(_safe_option(
                d["name"], d["name"], emoji=d.get("emoji"),
                description=f"scout ≈ {_fmt_pct(share)} do split"))
        if not opts:
            opts = [discord.SelectOption(
                label="(nenhum tipo de node definido)", value="__none__")]
        super().__init__(placeholder="🌿 Tipo de node…", min_values=1, max_values=1,
                         options=opts, row=0)

    async def callback(self, interaction: discord.Interaction):
        view = self.view                      # capturar ANTES de remover (remove_item zera self.view)
        if self.values[0] == "__none__":
            await interaction.response.edit_message(
                content="⚠️ Nenhum tipo de node definido neste servidor. A staff define em "
                        "`/setup` → **Nodes**.", view=None)
            return
        view.node_type = self.values[0]
        view.remove_item(self)
        view.add_map_stage()                  # etapa 2: agora aparece o mapa
        await view.step(interaction)


class _NodeMapLetterSelect(ui.Select):
    """Escolhe a LETRA. Todos os mapas da mesma letra ficam juntos (sem mapas soltos)."""
    def __init__(self, view: "AddNodeView"):
        opts = [discord.SelectOption(label=f"{L}  ·  {len(view.by_letter[L])} mapa(s)", value=L)
                for L in sorted(view.by_letter)][:25]
        super().__init__(placeholder="🗺️ Mapa — escolha a letra…",
                         min_values=1, max_values=1, options=opts, row=1)

    async def callback(self, interaction: discord.Interaction):
        view = self.view                      # capturar ANTES de remover
        letter = self.values[0]
        view.remove_item(self)
        view.add_item(_NodeMapPickSelect(view.by_letter[letter]))
        await view.step(interaction)


class _NodeMapPickSelect(ui.Select):
    """Mapas de uma letra (ou lista pequena). Se passar de 25, a última opção é
    '▶️ Próximos…', que pagina DENTRO da mesma letra (sem mapas soltos)."""
    _PER = 24

    def __init__(self, maps: list, page: int = 0):
        self._maps = maps
        self._page = page
        pages = max(1, (len(maps) + self._PER - 1) // self._PER)
        chunk = maps[page * self._PER:(page + 1) * self._PER]
        opts = [discord.SelectOption(label=m[:100], value=str(page * self._PER + i))
                for i, m in enumerate(chunk)]
        if len(maps) > self._PER:                # passou de 25 → opção de paginar
            opts.append(discord.SelectOption(
                label=f"▶️ Próximos… (pág {page + 1}/{pages})", value="__next__"))
        super().__init__(placeholder="🗺️ Mapa…", min_values=1, max_values=1, options=opts, row=1)

    async def callback(self, interaction: discord.Interaction):
        view = self.view                      # capturar ANTES de remover
        val = self.values[0]
        if val == "__next__":
            pages = max(1, (len(self._maps) + self._PER - 1) // self._PER)
            view.remove_item(self)
            view.add_item(_NodeMapPickSelect(self._maps, (self._page + 1) % pages))
            await view.step(interaction)
            return
        view.map_name = self._maps[int(val)]
        view.remove_item(self)
        view.add_time_stage()                 # etapa 3: agora aparece o horário
        await view.step(interaction)


# ----- Horário em "lupa" de 25 em 25: faixas de 25min → o minuto exato -----
# Aceita até 26h à frente. Como não cabem todas as faixas num único select, a própria
# lista pagina: a 1ª página tem "▶️ Próximos horários"; as seguintes têm "◀️ Anteriores"
# e "▶️ Próximos" (a última, só "◀️ Anteriores"). Reservamos 2 slots p/ a navegação.
_NODE_TIME_RANGE_MIN       = 25                       # minutos por faixa
_NODE_TIME_MAX_HORIZON_MIN = 26 * 60                  # 26h à frente (limite)
_NODE_TIME_TOTAL_FAIXAS    = _NODE_TIME_MAX_HORIZON_MIN // _NODE_TIME_RANGE_MIN + 1  # 63
_NODE_TIME_FAIXAS_PER_PAGE = 23                       # 25 - 2 slots de navegação
_NODE_TIME_PAGES           = -(-_NODE_TIME_TOTAL_FAIXAS // _NODE_TIME_FAIXAS_PER_PAGE)  # ceil → 3


def _fmt_eta(minutes: int) -> str:
    """Quanto falta, legível: '2 horas' · '2h25min' (h abrevia quando tem minuto)
    · '25 minutos' (só minutos vai por extenso) · 'agora'."""
    if minutes <= 0:
        return "agora"
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}h{m:02d}min"
    if h:
        return f"{h} hora{'s' if h > 1 else ''}"
    return f"{m} minuto{'s' if m > 1 else ''}"


class _NodeTimeRangeSelect(ui.Select):
    def __init__(self, base: datetime, page: int = 0):
        start_idx = page * _NODE_TIME_FAIXAS_PER_PAGE
        end_idx   = min(start_idx + _NODE_TIME_FAIXAS_PER_PAGE, _NODE_TIME_TOTAL_FAIXAS)
        now = datetime.now(timezone.utc)
        opts = []
        if page > 0:                                     # opção de VOLTAR (não na 1ª pág)
            opts.append(discord.SelectOption(
                label="◀️ Horários anteriores", value="__prev__"))
        for idx in range(start_idx, end_idx):
            s = base + timedelta(minutes=idx * _NODE_TIME_RANGE_MIN)
            e = s + timedelta(minutes=_NODE_TIME_RANGE_MIN)
            # quanto falta pra faixa (ceil: 1h59m30s mostra "2 horas", não "1h59min")
            eta_s = _fmt_eta(-(-int((s - now).total_seconds()) // 60))
            eta_e = _fmt_eta(-(-int((e - now).total_seconds()) // 60))
            pref = "" if eta_s == "agora" else "em "
            opts.append(discord.SelectOption(
                label=f"{s:%H:%M}–{e:%H:%M} UTC  ({pref}{eta_s} - {eta_e})",
                value=str(int(s.timestamp()))))
        if page < _NODE_TIME_PAGES - 1:                  # opção de AVANÇAR (não na última)
            opts.append(discord.SelectOption(
                label="▶️ Próximos horários", value="__next__"))
        super().__init__(
            placeholder=f"🕐 Horário — faixa de 25min (pág {page + 1}/{_NODE_TIME_PAGES})…",
            min_values=1, max_values=1, options=opts, row=2)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        val = self.values[0]
        if val == "__next__":
            view._time_page = min(view._time_page + 1, _NODE_TIME_PAGES - 1)
            view._render_time_stage()
            await view.step(interaction)
            return
        if val == "__prev__":
            view._time_page = max(view._time_page - 1, 0)
            view._render_time_stage()
            await view.step(interaction)
            return
        bs = datetime.fromtimestamp(int(val), tz=timezone.utc)
        view._remove_time_widgets()                      # tira a faixa
        view.add_item(_NodeTimeMinuteSelect(bs))         # aproxima: o minuto exato
        await view.step(interaction)


class _NodeTimeMinuteSelect(ui.Select):
    def __init__(self, block_start):
        now = datetime.now(timezone.utc)
        opts = []
        for k in range(_NODE_TIME_RANGE_MIN):            # os 25 minutos da faixa, um a um
            s = block_start + timedelta(minutes=k)
            eta = _fmt_eta(-(-int((s - now).total_seconds()) // 60))
            pref = "" if eta == "agora" else "em "
            opts.append(discord.SelectOption(label=f"{s:%H:%M} UTC  ({pref}{eta})",
                                             value=str(int(s.timestamp()))))
        super().__init__(placeholder="🕐 Horário — minuto exato…",
                         min_values=1, max_values=1, options=opts, row=2)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        view.spawn_ts = int(self.values[0])              # minuto exato definido
        view.remove_item(self)
        await view.step(interaction)


class AddNodeView(ui.View):
    """Tutorial em ephemeral (só listas, SEM botões): escolha tipo · mapa · horário.
    Cada lista some ao ser definida e, quando os 3 estiverem prontos, o node é
    adicionado SOZINHO."""
    def __init__(self, cog_ref: "NodeCalendar", calendar_channel_id: int, maps: list,
                 node_defs: list, scout_pct: float = 0, node_map: dict = None):
        super().__init__(timeout=300)
        self.cog_ref = cog_ref
        self.calendar_channel_id = calendar_channel_id
        self.node_type: str | None = None
        self.map_name: str | None = None
        self.spawn_ts: int | None = None
        # Tipos de node do servidor (p/ o seletor) + node_map (p/ o emoji no fim).
        self._node_defs = node_defs
        self._scout_pct = scout_pct
        self._node_map = node_map or {d["name"]: d for d in node_defs}
        # Paginação do horário (faixas de 25min até 26h via botão "Mais horários").
        self._time_base: datetime | None = None
        self._time_page: int = 0

        # Mapa agrupado por LETRA (mesma letra junta; se >25, pagina dentro da letra).
        self._all_maps = list(maps)
        self.by_letter: dict = {}
        for m in maps:
            self.by_letter.setdefault(m[0].upper(), []).append(m)

        # Em ETAPAS: começa só com o tipo. O mapa aparece após o tipo; o horário,
        # após tipo + mapa (ver add_map_stage / add_time_stage).
        self.add_item(_NodeTypeSelect(self._node_defs, self._scout_pct))

    def add_map_stage(self):
        """Etapa 2: mostra o mapa (lista direta se ≤25; senão letra → mapa paginado)."""
        if len(self._all_maps) <= 25:
            self.add_item(_NodeMapPickSelect(self._all_maps))
        else:
            self.add_item(_NodeMapLetterSelect(self))

    def add_time_stage(self):
        """Etapa 3: mostra o horário (lupa de 25 em 25 até o minuto exato). A própria
        lista pagina as faixas até 26h à frente (opções ◀️/▶️ dentro do dropdown)."""
        self._time_base = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        self._time_page = 0
        self._render_time_stage()

    def _remove_time_widgets(self):
        """Remove os widgets de horário (faixa e minuto) da view."""
        for it in list(self.children):
            if isinstance(it, (_NodeTimeRangeSelect, _NodeTimeMinuteSelect)):
                self.remove_item(it)

    def _render_time_stage(self):
        """(Re)desenha a faixa de horário da página atual (com a navegação embutida)."""
        self._remove_time_widgets()
        self.add_item(_NodeTimeRangeSelect(self._time_base, self._time_page))

    def _content(self) -> str:
        t  = self.node_type or "~~——~~"
        m  = self.map_name or "~~——~~"
        hm = f"<t:{self.spawn_ts}:t> (<t:{self.spawn_ts}:R>)" if self.spawn_ts else "~~——~~"
        return ("🌿   **𝐀𝐃𝐈𝐂𝐈𝐎𝐍𝐀𝐑 𝐍𝐎𝐃𝐄**"
                "\n\n"
                f"» Tipo: {t}\n» Mapa: {m}\n» Horário: {hm}\n \n")

    async def step(self, interaction: discord.Interaction):
        """Após cada escolha: se os 3 estiverem prontos → finaliza; senão re-renderiza."""
        if self.node_type and self.map_name and self.spawn_ts:
            await self._finalize(interaction)
        else:
            await interaction.response.edit_message(content=self._content(), view=self)

    async def _finalize(self, interaction: discord.Interaction):
        unix_ts = int(self.spawn_ts)
        # Anti-duplicado: mesmo tipo + mesmo mapa com horário a < 2h = já scoutado.
        dup = await find_duplicate_node(self.calendar_channel_id, self.node_type,
                                        self.map_name, unix_ts)
        if dup:
            who, dts = dup
            await interaction.response.edit_message(
                content=(f"⚠️ Esse node já foi scoutado por **{who}** — **{self.node_type}** em "
                         f"🗺️ **{self.map_name}**, abre <t:{dts}:t> (<t:{dts}:R>). A menos de 2h "
                         f"do horário escolhido, então não registrei (duplicado)."),
                view=None)
            return
        await add_node_event(
            channel_id=self.calendar_channel_id, node_type=self.node_type,
            map_name=self.map_name, spawn_timestamp=unix_ts,
            added_by=interaction.user.display_name, added_by_id=interaction.user.id,
        )
        await log_node_event(
            node_type=self.node_type, map_name=self.map_name,
            added_by=interaction.user.display_name, added_by_id=interaction.user.id,
            spawn_timestamp=unix_ts,
        )
        emoji = node_emoji_of(self._node_map, self.node_type)

        # Exportação para guildas sem premium (silenciosa — nunca exibir ao usuário).
        cfg = await load_economy_config()
        if not cfg.get('is_premium'):
            try:
                paid = await get_scout_paid_count(interaction.user.id)
                if paid >= SCOUT_RELIABILITY_MIN:
                    gid = interaction.guild_id or get_current_guild()
                    if gid:
                        await export_node_global(
                            source_guild_id=gid,
                            node_type=self.node_type,
                            map_name=self.map_name,
                            spawn_timestamp=unix_ts,
                            scout_name=interaction.user.display_name,
                            scout_id=interaction.user.id,
                        )
            except Exception as _e:
                print(f"✗ export_node_global: {_e}")

        await _node_audit(interaction,
                          f"➕ {emoji} **{self.node_type}** · 🗺️ {self.map_name} · abre <t:{unix_ts}:t>",
                          kind='ok')
        await interaction.response.edit_message(
            content=(f"✅ Node adicionado: {emoji} **{self.node_type}** · 🗺️ {self.map_name} · "
                     f"abre <t:{unix_ts}:t> (<t:{unix_ts}:R>)"),
            view=None)
        await self.cog_ref._update_calendar_now()


# ---------------------------------------------------------------------------
# Select de remoção de evento
# ---------------------------------------------------------------------------
class NodeRemoveSelect(ui.Select):
    def __init__(self, cog_ref: "NodeCalendar", events: list, node_map: dict = None):
        self.cog_ref = cog_ref
        node_map = node_map or {}
        self._event_meta: dict = {}   # str(event_id) → (node_type, map_name, emoji)

        options = []
        for event_id, node_type, map_name, spawn_ts, added_by in events[:25]:
            emoji    = node_emoji_of(node_map, node_type)
            spawn_dt = datetime.fromtimestamp(spawn_ts, tz=timezone.utc)
            time_str = spawn_dt.strftime('%H:%M UTC') if spawn_ts else "?? UTC"
            label    = f"{node_type} — {time_str}"[:100]
            desc     = f"{map_name}  (por {added_by})"[:100]
            self._event_meta[str(event_id)] = (node_type, map_name, emoji)
            options.append(
                discord.SelectOption(
                    label=label,
                    description=desc,
                    value=str(event_id),
                    emoji=emoji,
                )
            )

        super().__init__(
            placeholder="Selecione o evento para remover…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        sel_id = self.values[0]
        node_type, map_name, emoji = self._event_meta.get(sel_id, ("?", "?", ""))
        await delete_node_event(int(sel_id))
        await _node_audit(interaction,
                          f"➖ {emoji} **{node_type}** · 🗺️ {map_name}",
                          kind='warn')
        # Auto-edita a ephemeral do select (sem mandar msg nova).
        await interaction.response.edit_message(
            content=None,
            embed=make_embed('ok', desc="Node removido."),
            view=None,
        )
        await self.cog_ref._update_calendar_now()


class NodeRemoveView(ui.View):
    def __init__(self, cog_ref: "NodeCalendar", events: list, node_map: dict = None):
        super().__init__(timeout=120)
        self.add_item(NodeRemoveSelect(cog_ref, events, node_map))


# ---------------------------------------------------------------------------
# View persistente do calendário (sobrevive a restart do bot)
# ---------------------------------------------------------------------------
class CalendarView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)   # timeout=None → persistente

    @ui.button(
        label="⚔️ Adicionar Node",
        style=discord.ButtonStyle.green,
        custom_id="nodes:add_v2",
    )
    async def add_node_button(self, interaction: discord.Interaction, button: ui.Button):
        cog: NodeCalendar | None = interaction.client.cogs.get("NodeCalendar")  # type: ignore
        if not cog:
            await send_err(interaction, "Sistema indisponível.")
            return
        await cog._on_add_node_click(interaction)

    @ui.button(
        label="🗑️ Remover",
        style=discord.ButtonStyle.red,
        custom_id="nodes:remove_v2",
    )
    async def remove_node_button(self, interaction: discord.Interaction, button: ui.Button):
        cog: NodeCalendar | None = interaction.client.cogs.get("NodeCalendar")  # type: ignore
        if not cog:
            await send_err(interaction, "Sistema indisponível.")
            return
        await cog._on_remove_node_click(interaction)


# ---------------------------------------------------------------------------
# Cog principal
# ---------------------------------------------------------------------------
class NodeCalendar(commands.Cog):
    """Calendário de nodes do Albion Online"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.update_calendar.start()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.command and ctx.command.name in ("ativar",):
            return True
        if ctx.guild is None:
            return True
        if not await is_server_activated(ctx.guild.id):
            await send_err(ctx, "Este servidor não está ativado!")
            return False
        return True

    async def cog_load(self):
        # Registrar a view persistente para que os botões funcionem após restart.
        # (A config do calendário é por servidor — carregada sob demanda no DB.)
        self.bot.add_view(CalendarView())

    def cog_unload(self):
        self.update_calendar.cancel()

    # ------------------------------------------------------------------
    # Task de atualização periódica
    # ------------------------------------------------------------------

    @tasks.loop(minutes=5)
    async def update_calendar(self):
        for gid in await get_activated_guild_ids():
            with database.using_guild(gid):
                try:
                    await self._update_calendar_now()
                except Exception as e:
                    print(f"✗ update_calendar [{gid}]: {e}")

    @update_calendar.before_loop
    async def before_update_calendar(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Atualização do embed
    # ------------------------------------------------------------------

    async def _update_calendar_now(self):
        """Limpa eventos expirados e edita o embed do calendário (servidor atual)."""
        channel_id, message_id = await load_node_calendar()
        if not channel_id or not message_id:
            return

        try:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                return

            # Remover eventos que já passaram há mais de 1 hora
            cutoff = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
            await delete_expired_node_events(channel_id, cutoff)

            message = await channel.fetch_message(message_id)
            embed   = await self._create_calendar_embed()
            await message.edit(embed=embed, view=CalendarView())
            # (silencioso: atualização periódica do calendário)

        except discord.NotFound:
            print("✗ Mensagem do calendário não encontrada (pode ter sido deletada)")
        except Exception as e:
            print(f"✗ Erro ao atualizar calendário: {e}")

    async def _create_calendar_embed(self) -> discord.Embed:
        """Monta o embed do calendário com os nodes em ordem cronológica."""
        now = datetime.now(timezone.utc)
        embed = discord.Embed(
            title="🌿  𝐍𝐎𝐃𝐄𝐒",
            color=EMBED_INFO,
        )

        channel_id, _ = await load_node_calendar()
        if not channel_id:
            embed.description = "*Nenhum calendário configurado.*"
            return embed

        events = await get_node_events(channel_id)
        valid  = [e for e in events if e[3] and e[3] > 0]

        # Mescla nodes exportados de guildas não-premium (visíveis só quando faltam ≤4h).
        try:
            gid = get_current_guild()
            if gid:
                exported = await get_exported_nodes_for_display(gid)
                for exp in exported:
                    # Omite quem enviou; aparece como node local para não revelar a origem.
                    valid.append((-exp['id'], exp['node_type'], exp['map_name'],
                                  exp['spawn_timestamp'], ''))
        except Exception as _e:
            print(f"✗ get_exported_nodes_for_display: {_e}")

        valid = sorted(valid, key=lambda e: e[3])

        if not valid:
            embed.description = ("...\n")
            return embed

        # 3 COLUNAS (fields inline lado a lado), uma linha por node:
        #   1) Node + tier (emoji + texto minimalista)   2) Horário UTC
        #   3) Mapa + tempo restante
        # Corta quando a coluna do mapa (a maior) chegaria perto do limite de 1024.
        node_map = await load_node_map()
        col_node, col_utc, col_map = [], [], []
        used, shown = 0, 0
        prev_ts = None
        for _event_id, node_type, map_name, spawn_ts, _added_by in valid:
            line_map = f"🌎 __{map_name}__ ・ <t:{spawn_ts}:R>"
            #  · <t:{spawn_ts}:R>
            if used + len(line_map) + 1 > 1000:
                break
            # Gap de 2h+ entre nodes vizinhos → UMA linha "…" (na coluna do horário);
            # as outras colunas levam um filler invisível pra manter o alinhamento.
            if prev_ts is not None and spawn_ts - prev_ts >= TIME_GAP_BREAK_S:
                col_utc.append("**…**")
                col_node.append("ㅤ")
                col_map.append("ㅤ")
                used += 2
            prev_ts = spawn_ts
            emoji    = node_emoji_of(node_map, node_type)
            utc_time = datetime.fromtimestamp(spawn_ts, tz=timezone.utc).strftime("%H:%M")
            col_node.append(f"**{emoji}  {node_type}**")
            col_utc.append(f"**{utc_time}**")
            col_map.append(line_map)
            used += len(line_map) + 1
            shown += 1

        embed.add_field(name="",        value="\n".join(col_utc), inline=True)
        embed.add_field(name="",         value="\n".join(col_node),  inline=True)
        embed.add_field(name="",   value="\n".join(col_map),  inline=True)
        remaining = len(valid) - shown
        if remaining > 0:
            embed.description = f"*… e mais **{remaining}** node(s) além dos listados.*"
        return embed

    # ------------------------------------------------------------------
    # Callbacks dos botões da CalendarView
    # ------------------------------------------------------------------

    async def _on_add_node_click(self, interaction: discord.Interaction):
        channel_id, _ = await load_node_calendar()
        if not channel_id:
            await send_err(interaction, "Calendário não configurado neste servidor "
                                        "(`/setup` → Nodes).")
            return
        # Lista do seletor: Black Zone embutida (menos as removidas) + extras custom.
        maps = await _effective_node_maps()
        node_defs = await load_node_defs()
        if not node_defs:
            await send_err(interaction, "Nenhum tipo de node definido neste servidor. "
                                        "A staff define em `/setup` → **Nodes**.")
            return
        cfg = await load_economy_config()
        scout_pct = int(cfg.get('node_scout_percent') or 0)
        view = AddNodeView(cog_ref=self, calendar_channel_id=channel_id, maps=maps,
                           node_defs=node_defs, scout_pct=scout_pct)
        await interaction.response.send_message(view._content(), view=view, ephemeral=True)
        try:
            await utils.schedule_ephemeral_from_ctx(interaction)
        except Exception:
            pass

    async def _on_remove_node_click(self, interaction: discord.Interaction):
        channel_id, _ = await load_node_calendar()
        if not channel_id:
            await send_err(interaction, "Nenhum calendário ativo.")
            return

        # Staff (lead/council/logistic/owner) vê TODOS; os demais, só os que scoutaram.
        is_staff = await has_configured_role(interaction.user, 'role_council', 'role_logistic')
        if is_staff:
            valid = await get_removable_nodes(channel_id)
        else:
            valid = await get_removable_nodes(channel_id, only_user_id=interaction.user.id,
                                              only_user_name=interaction.user.display_name)

        if not valid:
            msg = ("📭 Nenhum node pra remover." if is_staff
                   else "📭 Você não tem nodes pra remover (a staff pode remover os de todos).")
            await interaction.response.send_message(msg, ephemeral=True)
            try:
                await utils.schedule_ephemeral_from_ctx(interaction)
            except Exception:
                pass
            return

        node_map = await load_node_map()
        view = NodeRemoveView(cog_ref=self, events=valid, node_map=node_map)
        escopo = "todos os nodes" if is_staff else "seus nodes"
        prompt = f"Selecione o node para remover ({escopo}):"
        if len(valid) > 25:
            prompt += f"  *(mostrando os 25 mais próximos, de {len(valid)})*"
        await interaction.response.send_message(prompt, view=view, ephemeral=True)
        try:
            await utils.schedule_ephemeral_from_ctx(interaction)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------

    async def publish_calendar(self, channel) -> bool:
        """Posta o calendário de nodes no canal e salva. Chamado pelo /setup → Nodes.
        Retorna True se publicou."""
        try:
            embed = await self._create_calendar_embed()
            message = await channel.send(embed=embed, view=CalendarView())
        except discord.HTTPException:
            return False
        await save_node_calendar(channel.id, message.id)
        print(f"✓ Calendário de nodes publicado em: #{getattr(channel, 'name', channel.id)}")
        return True

    @commands.hybrid_command(
        name="stopnode",
        description="Para o calendário de nodes e apaga os dados (apenas owner)",
    )
    async def stopnode(self, ctx: commands.Context):
        if ctx.author.id != OWNER_ID:
            await send_err(ctx, "Apenas o owner pode usar este comando.")
            return

        channel_id, _ = await load_node_calendar()
        if not channel_id:
            await send_err(ctx, "Nenhum calendário está ativo.")
            return

        await delete_node_calendar()

        await send_ok(ctx, "Calendário de nodes parado e dados removidos.")
        print("✓ Calendário de nodes parado")

    # ------------------------------------------------------------------
    # Mapas EXTRAS do seletor de node (a Black Zone do jogo já vem embutida;
    # estes comandos só adicionam/removem nomes a mais, por servidor).
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="addnodemap",
        description="Adiciona um mapa EXTRA ao seletor de node (a Black Zone já vem embutida) — council/lead/owner",
    )
    @app_commands.guild_only()
    @app_commands.describe(nome="Nome do mapa (use só se não estiver já na Black Zone embutida)")
    async def addnodemap(self, ctx: commands.Context, *, nome: str):
        if not await has_configured_role(ctx.author, 'role_council'):
            await send_err(ctx, "Apenas council, lead ou owner.")
            return
        nome = (nome or '').strip()
        if not nome:
            await send_err(ctx, "Informe o nome do mapa.")
            return
        if len(nome) > 100:
            await send_err(ctx, "Nome muito longo (máx. 100 caracteres).")
            return
        if nome.lower() in {m.lower() for m in BLACKZONE_MAPS}:
            # Se estava escondido por /removenodemap, volta pro seletor; senão já existe.
            if await unexclude_node_map(nome):
                await send_ok(ctx, f"**{nome}** voltou pro seletor (estava removido).")
            else:
                await send_info(ctx, f"**{nome}** já faz parte da Black Zone embutida — "
                                     f"é só buscar no botão de node.")
            return
        novo = await add_node_map(nome, ctx.author.id)
        if not novo:
            await send_info(ctx, f"**{nome}** já estava nos mapas extras.")
            return
        await send_ok(ctx, f"Mapa extra **{nome}** adicionado ao seletor de node "
                           f"(além da Black Zone embutida).")

    @commands.hybrid_command(
        name="removenodemap",
        description="Remove um mapa do seletor de node (embutido ou extra) — logistic/lead/owner",
    )
    @app_commands.guild_only()
    @app_commands.describe(nome="Mapa a remover (busque na lista)")
    @app_commands.autocomplete(nome=nodemap_autocomplete)
    async def removenodemap(self, ctx: commands.Context, *, nome: str):
        # has_configured_role já libera OWNER + lead; aqui pedimos logistic.
        if not await has_configured_role(ctx.author, 'role_logistic'):
            await send_err(ctx, "Apenas logistic, lead ou owner.")
            return
        nome = (nome or '').strip()
        # 1) é um EXTRA custom? apaga direto.
        if await remove_node_map(nome):
            await send_ok(ctx, f"Mapa extra **{nome}** removido do seletor.")
            return
        # 2) é um mapa EMBUTIDO (Black Zone)? esconde via exclusão.
        if nome.lower() in {m.lower() for m in BLACKZONE_MAPS}:
            if await exclude_node_map(nome, ctx.author.id):
                await send_ok(ctx, f"**{nome}** removido do seletor (mapa embutido escondido). "
                                   f"Pra reverter: `/addnodemap {nome}`.")
            else:
                await send_info(ctx, f"**{nome}** já estava removido.")
            return
        # 3) não existe em lugar nenhum
        await send_err(ctx, f"**{nome}** não está no seletor. Veja com `/nodemaps`.")

    @commands.hybrid_command(
        name="nodemaps",
        description="Mostra os mapas do seletor de node (Black Zone embutida + extras)",
    )
    @app_commands.guild_only()
    async def nodemaps(self, ctx: commands.Context):
        custom   = await get_node_maps()
        excluded = await get_excluded_node_maps()
        total    = len(await _effective_node_maps())
        head = (f"🗺️ **Mapas de node** — **{total}** no seletor\n"
                f"• Black Zone embutida: **{len(BLACKZONE_MAPS) - len(excluded)}** "
                f"(de {len(BLACKZONE_MAPS)}; **{len(excluded)}** removido(s))\n"
                f"• Extras desta guild: **{len(custom)}**")
        body = ""
        if custom:
            body += "\n\n**Extras:**\n" + "\n".join(f"• {m}" for m in custom[:40])
            if len(custom) > 40:
                body += f"\n… +{len(custom) - 40}"
        if excluded:
            body += "\n\n**Removidos (embutidos escondidos):**\n" + "\n".join(f"• {m}" for m in excluded[:40])
            if len(excluded) > 40:
                body += f"\n… +{len(excluded) - 40}"
        await ctx.send(head + body, ephemeral=True)
        try:
            await utils.schedule_ephemeral_from_ctx(ctx)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(NodeCalendar(bot))
