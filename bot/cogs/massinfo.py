"""
Mass-info — tracker de CTAs.

Mantém uma única embed no canal `channel_massinfo` listando todos os CTAs
em andamento. Cada CTA tem um botão associado: ao clicar, o bot lê as ROLES
da comp do CTA (página de roles indicada no COMPS) e abre um DROPDOWN para o
usuário escolher até 3 funções. O bot então:
  · grava no DB local (tabela cta_function_logs)
  · escreve nome + roles nas colunas L/M/N/O da página do CTA (via Apps Script)

Se a planilha não tiver roles disponíveis (offline / comp sem página), cai num
modal de texto livre como fallback.

Gate de QUANTIDADE ("espelho da escalação"): as VAGAS vêm dos slots da área de
escalação (role em B, nome vazio em A). Uma função só aparece no dropdown se
tiver vaga numa party ABERTA. As 2 PRIMEIRAS parties já vêm abertas de início
(OPEN_PARTIES_AT_START); as demais abrem por dois caminhos: em cascata (a seguinte
quando a anterior quase enche) OU pela escada de INSCRITOS no bot (pt3=25, pt4=45,
… — _party_signup_threshold). Função EXTRA (sem slot
correspondente — ex.: battlemount/scout) abre no degrau após a última party ou
com a comp (quase) cheia. Opcional: uma linha "--- N ---" na página de roles
troca a regra do bloco abaixo por "N escalados no total" (não é necessária).
O gate vale pra todo mundo (staff incluso); /liberarfuncoes desliga o gate do
CTA; sem dados da planilha → libera tudo.
"""
import asyncio
import re
import time
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks
from discord import app_commands, ui

import database
from database import (
    load_economy_config, update_economy_config,
    get_active_ctas, get_event_by_id, update_event_meta,
    log_cta_function, mark_function_log_synced,
    get_user_function_log, delete_user_function_logs, get_user_recent_roles,
    get_registration, get_activated_guild_ids,
    get_role_gates, get_massinfo_ping_count, get_function_log_names,
)
import sheets
from cogs.economy import has_configured_role
from utils import EMBED_INFO, TIME_GAP_BREAK_S, bold_number, send_err, send_ok, send_info
import utils


# Cor padrão das embeds do mass-info (azul aço, igual ao tracker).
_MI_COLOR = EMBED_INFO

# Workers que drenam a fila de escrita na planilha (limita a concorrência contra
# o Apps Script; o usuário não espera por isso — só pelo DB local).
SHEET_SYNC_WORKERS = 3

# A lupa (e a zona de parties) aparece a partir de ~10 min antes do início e fica
# até o callout. Mesmo limiar do aviso de pré-início (PRESTART_WARN_SECONDS).
LUPA_LEAD_SECONDS = 600
# Ping de "massando agora": dura 20 min (ou até o callout limpar o canal).
START_PING_SECONDS = 1200


def _ev_started_dt(ev: dict):
    """Horário de início (UTC) do CTA, ou None se não houver/for inválido."""
    try:
        dt = datetime.fromisoformat(ev.get('started_at') or '')
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _lupa_active(ev: dict, now: datetime | None = None) -> bool:
    """True se o CTA está EM ANDAMENTO ou faltam <= LUPA_LEAD_SECONDS pra começar
    (e ainda não encerrou) — janela em que a lupa/zona de parties aparece."""
    if ev.get('ended_at'):
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    dt = _ev_started_dt(ev)
    if dt is None or dt <= now:
        return True   # sem horário ou já começou → em andamento
    return (dt - now).total_seconds() <= LUPA_LEAD_SECONDS


def _cta_status_time(ev: dict, now: datetime | None = None) -> tuple[str, str]:
    """
    Retorna (emoji_status, horario_utc) de um CTA, usado tanto no embed quanto
    nos botões pra ficarem consistentes.
      🗓️ agendado · 🟢 em andamento · 💰 split definido · ⏳ aguardando split
    Horário no formato 'HH:MM UTC' (UTC explícito, igual ao input do /cta).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    dt = None
    try:
        dt = datetime.fromisoformat(ev.get('started_at') or '')
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        dt = None
    time_str = f"{dt.strftime('%H:%M')} UTC" if dt else "??:?? UTC"

    if not ev.get('ended_at'):
        emoji = "🗓️" if (dt is not None and dt > now) else "🟢"
    elif ev.get('split_defined'):   # 'definido' inclui split = 0
        emoji = "💰"
    else:
        emoji = "⏳"
    return emoji, time_str


def _massinfo_embed(event_id: int, sheet_url: str | None, body: str,
                    color: discord.Color | None = None) -> discord.Embed:
    """
    Embed padrão das respostas do mass-info (Ponto 7):
      · título clicável **CTA #N** que abre a página da planilha (fica azul);
      · corpo (body) com o conteúdo da resposta;
      · link da planilha repetido no final, como atalho explícito.
    Se não houver `sheet_url`, vira uma embed simples sem links.
    """
    embed = discord.Embed(
        title=f"{bold_number(event_id)}",
        description=body,
        color=color or _MI_COLOR,
    )
    if sheet_url:
        embed.url = sheet_url  # deixa o título (número do CTA) clicável (azul)
        embed.add_field(
            name="​",
            value=f"🔗 **[Link da Planilha]({sheet_url})**",
            inline=False,
        )
    return embed


# ==================================================================
# Helper compartilhado: grava no DB + planilha + feedback
# ==================================================================
async def _register_functions(interaction: discord.Interaction, event_id: int,
                              sheet_page: str, caller_name: str, roles: list[str]) -> str:
    """
    Grava as funções no DB NA HORA (fonte da verdade) e ENFILEIRA a escrita na
    planilha pra rodar em background — assim a resposta é instantânea mesmo com
    centenas de cliques em segundos (o Sheets tem timeout de 60s e não pode estar
    no caminho da resposta). NÃO responde à interação: retorna o texto de feedback.
    """
    user = interaction.user
    roles = [r for r in (roles or []) if r][:3]
    f1, f2, f3 = (roles + ["", "", ""])[:3]

    # Nome na planilha = NICK DE JOGO cadastrado (/register); fallback p/ display name.
    reg = await get_registration(user.id)
    sheet_name = (reg.get('nick') if reg else None) or user.display_name

    # Re-registro: captura o registro anterior (pra remover da planilha depois) e
    # apaga o log antigo no DB. A remoção na planilha vai junto no job da fila.
    prev = await get_user_function_log(event_id, user.id)
    prev_name = (prev.get('user_name') or sheet_name) if prev else None
    prev_row  = (prev.get('sheet_row') or 0) if prev else 0
    if prev:
        await delete_user_function_logs(event_id, user.id)

    log_id = await log_cta_function(
        event_id=event_id, user_id=user.id, user_name=sheet_name,
        function1=f1, function2=f2, function3=f3, synced_sheets=False,
    )

    # Planilha: vai pra fila (background). DB já tem tudo (synced_sheets=False).
    cog = interaction.client.cogs.get('MassInfoCog')
    if cog and sheet_page and await sheets.is_configured():
        cog.enqueue_sheet_write({
            'log_id': log_id, 'event_id': event_id, 'sheet_page': sheet_page,
            'sheet_name': sheet_name, 'roles': roles, 'caller_name': caller_name,
            'prev_name': prev_name, 'prev_row': prev_row,
        })
        suffix = "📊 sincronizando com a planilha…"
    else:
        suffix = "💾 salvo (este CTA não tem página na planilha)"

    chosen = "\n".join(f"• {r}" for r in roles) or "• (nenhuma)"

    # Marca o embed do evento como "sujo" — refresh coalescido (1× a cada poucos
    # segundos) em vez de refrescar a cada submissão (evita rate limit / trabalho
    # redundante quando muita gente pinga junto).
    cta_cog = interaction.client.cogs.get('CTACog')
    if cta_cog and hasattr(cta_cog, 'mark_event_dirty'):
        cta_cog.mark_event_dirty(event_id)
    # Pede o refresh coalescido do mass-info (atualiza a contagem de presentes).
    if cog and hasattr(cog, 'request_massinfo_refresh'):
        cog.request_massinfo_refresh()

    return f"✅ **Funções registradas!**\n{chosen}\n\n_{suffix}_"


# ==================================================================
# Gate de funções (Request 2): quais funções da planilha ESTE membro pode pegar
# ==================================================================
async def _allowed_roles(member, roles: list) -> list:
    """Filtra as funções da planilha que `member` pode escolher.
    · staff (owner/lead/council) vê tudo;
    · função SEM gate é aberta a todos;
    · senão, exige que o membro tenha QUALQUER UM dos cargos do gate.
    Em qualquer falha, libera tudo (não trava o registro do CTA)."""
    roles = list(roles or [])
    if not roles:
        return roles
    try:
        if await has_configured_role(member, 'role_council'):   # já cobre owner + lead
            return roles
        gates = await get_role_gates()
    except Exception as e:
        print(f"✗ gate de funções: falha ao avaliar ({type(e).__name__}: {e}) — liberando tudo")
        return roles
    if not gates:
        return roles
    member_role_ids = {r.id for r in getattr(member, 'roles', [])}
    out = []
    for r in roles:
        need = gates.get((r or '').strip().lower())
        if not need or (member_role_ids & need):
            out.append(r)
    return out


# ==================================================================
# Gate de QUANTIDADE ("espelho da escalação"): a área de escalação da página
# do CTA define as VAGAS (slots com role em B e nome vazio em A). As parties
# abrem em ordem; funções EXTRAS (sem slot) abrem quando todas as parties
# estiverem (quase) cheias — nenhuma config na planilha é necessária.
# ==================================================================
# A party N+1 só abre quando faltarem <= este nº de vagas na party N. A mesma
# tolerância decide quando a comp está "cheia" pra liberar as funções extras.
PARTY_UNLOCK_REMAINING = 2

# Escada por INSCRITOS no bot (independe da escalação): pt1 sempre aberta;
# pt2 abre com SIGNUPS_FIRST_STEP inscritos; cada party seguinte soma
# SIGNUPS_PER_PARTY — pt2=5, pt3=25, pt4=45, pt5=65… As funções extras ficam
# no degrau depois da última party. Evita forçar quem não sabe jogar as roles
# da pt1 a pegá-las só porque a pt1 ainda não encheu/escalonou.
SIGNUPS_FIRST_STEP = 5
SIGNUPS_PER_PARTY  = 20
# As N primeiras parties já vêm ABERTAS (sem exigir inscritos), pra todo mundo
# conseguir escolher role logo de início (reclamação recorrente do gate).
OPEN_PARTIES_AT_START = 2


def _party_signup_threshold(i: int) -> int:
    """Inscritos exigidos pra abrir a i-ésima party (0-based). As
    OPEN_PARTIES_AT_START primeiras já vêm abertas (0); as seguintes seguem a escala
    (pt3=25, pt4=45…). O índice len(parties) é o degrau das funções extras."""
    if i < OPEN_PARTIES_AT_START:
        return 0
    return SIGNUPS_FIRST_STEP + SIGNUPS_PER_PARTY * (i - 1)

# Linha-marcador OPCIONAL na página de roles: "--- 40 ---" → as funções ABAIXO
# dela só aparecem com >= 40 escalados (override da regra automática dos extras).
_MARKER_RE = re.compile(r'^\s*-{2,}\s*(\d+)\s*-{2,}\s*$')


def _parse_role_sections(roles: list[str]) -> list[tuple[int, str]]:
    """Converte a lista da página de roles em [(threshold, role)]: cada função
    carrega o mínimo de ESCALADOS exigido pelo último marcador acima dela
    (0 = sem marcador, o caso normal). As linhas-marcador são consumidas."""
    out: list[tuple[int, str]] = []
    threshold = 0
    for r in roles or []:
        m = _MARKER_RE.match(r or '')
        if m:
            threshold = int(m.group(1))
            continue
        out.append((threshold, r))
    return out


def _strip_markers(roles: list[str]) -> list[str]:
    """Só remove as linhas-marcador (usado quando o gate de quantidade não se aplica)."""
    return [r for _, r in _parse_role_sections(roles)]


def _slot_stats(slots: list[dict]) -> dict:
    """Estatísticas por party a partir dos slots da escalação (get_assignments):
    {party: {'total', 'filled', 'open': {roles c/ vaga}, 'roles': {todas as roles}}}.
    Roles em minúsculas (comparação case-insensitive com a página de roles)."""
    parties: dict = {}
    for s in slots or []:
        p    = s.get('party')
        role = (s.get('role') or '').strip().lower()
        if not p or not role:
            continue
        st = parties.setdefault(p, {'total': 0, 'filled': 0,
                                    'open': set(), 'roles': set()})
        st['total'] += 1
        st['roles'].add(role)
        if (s.get('name') or '').strip():
            st['filled'] += 1
        else:
            st['open'].add(role)
    return parties


def _quantity_gate(roles: list[str], slots: list[dict],
                   signups: int = 0) -> list[str]:
    """Filtra as funções da comp pelo estado da ESCALAÇÃO + nº de INSCRITOS:
      · função que existe como slot só aparece se houver VAGA numa party ABERTA.
        Uma party abre por DOIS caminhos (o que vier primeiro):
          - cascata: a 1ª sempre; a seguinte quando esta tiver
            <= PARTY_UNLOCK_REMAINING vagas;
          - inscritos: _party_signup_threshold — pt2 com 5, pt3 com 25,
            pt4 com 45, …;
      · função sem slot correspondente (extra — ex.: battlemount/scout) abre no
        degrau de inscritos DEPOIS da última party ou quando TODAS as parties
        estiverem (quase) cheias; um marcador "--- N ---" opcional troca a
        regra por "N escalados no total".
    FAIL-OPEN: sem NENHUM slot vago nos dados (planilha offline, .gs antigo que
    não devolve vagas, ou comp 100% cheia), devolve tudo — só tira os marcadores."""
    sections = _parse_role_sections(roles)
    stats = _slot_stats(slots)
    if not any(st['open'] for st in stats.values()):
        return [r for _, r in sections]

    total_filled = sum(st['filled'] for st in stats.values())
    parties = sorted(stats)
    slot_roles: set = set()    # roles que têm slot em ALGUMA party
    open_roles: set = set()    # roles com vaga em party ABERTA
    cascade = True             # a party 1 está sempre aberta
    for i, p in enumerate(parties):
        st = stats[p]
        slot_roles |= st['roles']
        if cascade or signups >= _party_signup_threshold(i):
            open_roles |= st['open']
        # Cascata: a party seguinte só herda a abertura se esta estiver (quase) cheia.
        cascade = cascade and (st['total'] - st['filled']) <= PARTY_UNLOCK_REMAINING
    all_full = cascade         # depois do loop: a ÚLTIMA party também (quase) encheu
    extras_open = all_full or signups >= _party_signup_threshold(len(parties))

    out = []
    for threshold, r in sections:
        rl = r.strip().lower()
        if rl in slot_roles:
            # Role de slot: precisa de vaga em party aberta (e de passar pelo
            # marcador, se alguém pôs um acima dela).
            if threshold > total_filled or rl not in open_roles:
                continue
        elif threshold > 0:
            # Extra com marcador explícito: vale o número do marcador (escalados).
            if threshold > total_filled:
                continue
        elif not extras_open:
            # Extra sem marcador (caso normal): abre no degrau após a última party.
            continue
        out.append(r)
    return out


async def _apply_gates(member, ev: dict, roles: list[str],
                       slots: list[dict]) -> tuple[list[str], str]:
    """Combina os gates da escolha de funções e devolve (roles, motivo_vazio):
      motivo ∈ '' (ok, ou já não havia roles) · 'vagas' (quantidade zerou) · 'cargo'.
    O gate de quantidade vale pra TODO MUNDO (staff incluso) — só o
    /liberarfuncoes o desliga. Em falha/sem dados, libera."""
    roles = list(roles or [])
    if not roles:
        return [], ''
    if ev.get('functions_released'):
        roles = _strip_markers(roles)
    else:
        try:
            signups = await get_massinfo_ping_count(ev.get('id') or 0)
        except Exception:
            signups = 0
        roles = _quantity_gate(roles, slots, signups)
        if not roles:
            return [], 'vagas'
    allowed = await _allowed_roles(member, roles)
    if not allowed:
        return [], 'cargo'
    return allowed, ''


_NO_SLOTS_MSG = ("⏳ As funções com **vaga aberta** já foram todas escolhidas/escaladas. "
                 "Aguarde a próxima party abrir (ou o caller liberar com `/liberarfuncoes`).")
_NO_ROLE_MSG  = "🔒 Você não tem cargo para escolher nenhuma função desta comp."


def _gate_report(roles: list[str], slots: list[dict], signups: int,
                 released: bool) -> str:
    """Texto de diagnóstico do gate (/gateinfo): o que o gate vê e o que um
    membro comum (antes do gate de cargo) teria no dropdown — e por quê."""
    sections = _parse_role_sections(roles)
    stats = _slot_stats(slots)
    lines: list[str] = []

    if released:
        lines.append("🔓 **/liberarfuncoes ATIVO** — gate de quantidade desligado neste CTA.")

    total  = sum(st['total'] for st in stats.values())
    filled = sum(st['filled'] for st in stats.values())
    if not stats:
        lines.append("⚠️ **Sem dados da escalação** (cache vazio / planilha offline) "
                     "→ FAIL-OPEN: tudo liberado.")
    elif not any(st['open'] for st in stats.values()):
        lines.append(f"⚠️ **{total} slots e NENHUMA vaga** nos dados — comp 100% cheia "
                     f"ou Apps Script desatualizado (republique o .gs) "
                     f"→ FAIL-OPEN: tudo liberado.")
    else:
        lines.append(f"Slots: **{total}** · escalados: **{filled}** · vagas: **{total - filled}**")
        cascade = True
        for i, p in enumerate(sorted(stats)):
            st  = stats[p]
            thr = _party_signup_threshold(i)
            opened = cascade or signups >= thr
            if opened:
                how = "cascata" if cascade else f"inscritos ≥ {thr}"
            else:
                how = f"precisa de {thr} inscritos (tem {signups}) ou da pt anterior cheia"
            lines.append(f"· pt{p}: {st['filled']}/{st['total']} — "
                         f"{'🟢 aberta' if opened else '🔒 trancada'} ({how})")
            cascade = cascade and (st['total'] - st['filled']) <= PARTY_UNLOCK_REMAINING
        extras_thr  = _party_signup_threshold(len(stats))
        extras_open = cascade or signups >= extras_thr
        lines.append("· extras: " + ("🟢 abertos" if extras_open
                     else f"🔒 trancados (degrau: {extras_thr} inscritos)"))
    lines.append(f"Inscritos no bot: **{signups}**")

    visible = _strip_markers(roles) if released else _quantity_gate(roles, slots, signups)
    vis_set = {v.strip().lower() for v in visible}
    hidden  = [r for _, r in sections if r.strip().lower() not in vis_set]

    def _fmt(lst: list[str]) -> str:
        shown = lst[:15]
        txt = ", ".join(shown) if shown else "—"
        if len(lst) > len(shown):
            txt += f" … +{len(lst) - len(shown)}"
        return txt

    lines.append(f"**Visíveis ({len(visible)}):** {_fmt(visible)}")
    lines.append(f"**Ocultas ({len(hidden)}):** {_fmt(hidden)}")
    return "\n".join(lines)


async def _liberar_evento_autocomplete(interaction: discord.Interaction, current: str):
    """CTAs ativos p/ o /liberarfuncoes — '#id 🔓/🔒 · comp' (valor = id)."""
    cur = (current or '').strip().lower()
    out = []
    for ev in sorted(await get_active_ctas(), key=lambda e: e['id'], reverse=True):
        comp  = ev.get('comp') or ''
        flag  = '🔓' if ev.get('functions_released') else '🔒'
        label = f"#{ev['id']} {flag}" + (f" · {comp}" if comp else "")
        if cur and cur not in str(ev['id']) and cur not in label.lower():
            continue
        out.append(app_commands.Choice(name=label[:100], value=ev['id']))
        if len(out) >= 25:
            break
    return out


# ==================================================================
# Dropdown de roles (caminho principal)
# ==================================================================
# Categoria da função pelo EMOJI inicial do nome (na planilha). Sem emoji
# reconhecido → 'OUTROS' (o fluxo segue funcionando mesmo sem categorizar nada).
_CAT_BY_CP = {
    '\U0001F6E1': 'TANK',     # 🛡 escudo
    '\U0001F54A': 'HEALER',   # 🕊 pomba
    '✨':     'SUPORTE',  # ✨ brilhos
    '⚔':     'DPS',      # ⚔ espadas
}
_CAT_EMOJI = {'TANK': '🛡️', 'HEALER': '🕊️', 'SUPORTE': '✨', 'DPS': '⚔️', 'OUTROS': '❓'}
_CAT_ORDER = ['TANK', 'HEALER', 'SUPORTE', 'DPS', 'OUTROS']
# Pseudo-categoria #7: as últimas roles do jogador (que existem na comp), no topo.
# (o emoji ⭐ vem do select; o label fica sem estrela pra não duplicar)
_RECENT_CAT = 'Recentes'


def _role_category(role: str) -> str:
    """Categoria pelo 1º caractere do nome — o emoji-base é sempre o 1º codepoint
    (o variation selector vem depois). Sem emoji reconhecido → 'OUTROS'."""
    s = (role or '').strip()
    return _CAT_BY_CP.get(s[:1], 'OUTROS') if s else 'OUTROS'


def _group_roles_by_category(roles: list[str]) -> dict:
    """Agrupa as funções por categoria, deduplicando e preservando a ordem da planilha."""
    groups: dict = {}
    seen = set()
    for r in roles or []:
        r = (r or '').strip()
        if not r or r.lower() in seen:
            continue
        seen.add(r.lower())
        groups.setdefault(_role_category(r), []).append(r)
    return groups


# ==================================================================
# Seletor de funções: categoria (🛡️/🕊️/✨/⚔️) → função, PAGINADO.
# Substitui o dropdown único que cortava em 25 — agora todas as funções são
# alcançáveis (até 3 escolhas). Fluxo em etapas, espelhando o AddNodeView de nodes.
# ==================================================================
class _CategorySelect(ui.Select):
    """Etapa 1 do slot: escolher a categoria (só as que ainda têm função disponível)."""
    def __init__(self, view: "FunctionPickView"):
        opts = []
        for cat in view._cat_order:
            funcs = view.available(cat)
            if funcs:
                opts.append(discord.SelectOption(
                    label=cat, value=cat, emoji=_CAT_EMOJI.get(cat, '⭐')))
        super().__init__(placeholder="Escolha a categoria…",
                         min_values=1, max_values=1, options=opts, row=0)

    async def callback(self, interaction: discord.Interaction):
        v: FunctionPickView = self.view
        v.current_cat = self.values[0]
        v.page = 0
        v.show_functions()
        await v.render(interaction)


class _FunctionSelect(ui.Select):
    """Etapa 2 do slot: escolher a função da categoria — paginado (◀️/▶️) + voltar."""
    _PER = 22   # reserva espaço pras opções de navegação (limite de 25 por select)

    def __init__(self, view: "FunctionPickView"):
        cat   = view.current_cat
        funcs = view.available(cat)
        pages = max(1, (len(funcs) + self._PER - 1) // self._PER)
        view.page %= pages
        start = view.page * self._PER
        chunk = funcs[start:start + self._PER]
        opts = [discord.SelectOption(label=f[:100], value=f"f:{start + i}")
                for i, f in enumerate(chunk)]
        if pages > 1:
            opts.append(discord.SelectOption(label="◀️ Anteriores", value="__prev__"))
            opts.append(discord.SelectOption(label="▶️ Próximos",   value="__next__"))
        opts.append(discord.SelectOption(label="↩️ Voltar às categorias", value="__back__"))
        ph = f"{_CAT_EMOJI.get(cat, '⭐')} {cat} — função…"
        if pages > 1:
            ph += f"  (pág {view.page + 1}/{pages})"
        super().__init__(placeholder=ph[:150], min_values=1, max_values=1, options=opts, row=0)

    async def callback(self, interaction: discord.Interaction):
        v: FunctionPickView = self.view
        val = self.values[0]
        if val == "__back__":
            v.show_categories(); await v.render(interaction); return
        if val == "__prev__":
            v.page -= 1; v.show_functions(); await v.render(interaction); return
        if val == "__next__":
            v.page += 1; v.show_functions(); await v.render(interaction); return
        idx   = int(val[2:])
        funcs = v.available(v.current_cat)
        if 0 <= idx < len(funcs):
            v.selected.append(funcs[idx])
        if len(v.selected) >= 3:
            await v.finalize(interaction); return
        v.show_categories()
        await v.render(interaction)


class FunctionPickView(ui.View):
    """Seleciona até 3 funções navegando categoria → função (com paginação).
    Mesmos args do antigo RoleSelectView (+ `header` opcional para contexto da comp)."""
    def __init__(self, event_id: int, sheet_page: str, caller_name: str,
                 roles: list[str], sheet_url: str | None = None, header: str = "",
                 recent_roles: list[str] | None = None):
        super().__init__(timeout=180)
        self.event_id    = event_id
        self.sheet_page  = sheet_page
        self.caller_name = caller_name
        self.sheet_url   = sheet_url
        self.header      = header
        self.groups      = _group_roles_by_category(roles)
        # #7: atalho com as últimas roles do jogador QUE EXISTEM nesta comp, no topo.
        self._cat_order = list(_CAT_ORDER)
        if recent_roles:
            in_comp = {r.strip().lower(): r.strip()
                       for rs in self.groups.values() for r in rs}
            recent, seen = [], set()
            for r in recent_roles:
                k = (r or '').strip().lower()
                if k in in_comp and k not in seen:
                    seen.add(k)
                    recent.append(in_comp[k])   # grafia da comp (emoji/acentos certos)
            if recent:
                self.groups[_RECENT_CAT] = recent
                self._cat_order = [_RECENT_CAT] + list(_CAT_ORDER)
        self.selected: list[str] = []
        self.current_cat: str | None = None
        self.page = 0
        self._select = None
        self.show_categories()
        self._done_btn = ui.Button(label="✅ Concluir", style=discord.ButtonStyle.success,
                                   row=1, disabled=True)
        self._done_btn.callback = self._on_done
        self.add_item(self._done_btn)
        cancel = ui.Button(label="✖ Cancelar", style=discord.ButtonStyle.secondary, row=1)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    def available(self, cat: str) -> list:
        """Funções da categoria que ainda NÃO foram escolhidas (dedup contra selected)."""
        chosen = {s.lower() for s in self.selected}
        return [f for f in self.groups.get(cat, []) if f.lower() not in chosen]

    def _clear_select(self):
        if self._select is not None:
            self.remove_item(self._select)
            self._select = None

    def show_categories(self):
        self._clear_select()
        self.current_cat = None
        # Só adiciona o select se ainda houver função disponível (Select vazio = erro).
        if any(self.available(c) for c in self._cat_order):
            self._select = _CategorySelect(self)
            self.add_item(self._select)

    def show_functions(self):
        self._clear_select()
        self._select = _FunctionSelect(self)
        self.add_item(self._select)

    def _content(self) -> str:
        chosen = "\n".join(f"• {r}" for r in self.selected) or "• (nenhuma ainda)"
        ctx = f" ({self.header})" if self.header else ""
        return f"Selecione suas funções{ctx} — **{len(self.selected)}/3**:\n{chosen}"

    async def render(self, interaction: discord.Interaction):
        self._done_btn.disabled = not self.selected
        await interaction.response.edit_message(content=self._content(), embed=None, view=self)

    async def _on_done(self, interaction: discord.Interaction):
        if not self.selected:
            await interaction.response.defer()   # nada escolhido — no-op
            return
        await self.finalize(interaction)

    async def _on_cancel(self, interaction: discord.Interaction):
        embed = _massinfo_embed(self.event_id, self.sheet_url, "Ok, nada foi registrado.")
        await interaction.response.edit_message(content=None, embed=embed, view=None)
        self.stop()

    async def finalize(self, interaction: discord.Interaction):
        # Acka em até 3s e remove os controles; depois mostra o feedback (embed com link).
        await interaction.response.edit_message(
            content="⏳ Registrando funções…", embed=None, view=None,
        )
        text = await _register_functions(
            interaction, self.event_id, self.sheet_page, self.caller_name, self.selected[:3],
        )
        embed = _massinfo_embed(self.event_id, self.sheet_url, text,
                                color=discord.Color.green())
        try:
            await interaction.edit_original_response(content=None, embed=embed, view=None)
        except Exception as e:
            print(f"✗ Erro exibindo feedback de funções: {e}")
        self.stop()


# ==================================================================
# Modal das 3 funções (FALLBACK — só se a planilha não trouxe roles)
# ==================================================================
class FunctionsModal(ui.Modal, title="Registrar funções no CTA"):
    function1 = ui.TextInput(label="Função 1", max_length=80, required=True)
    function2 = ui.TextInput(label="Função 2", max_length=80, required=False)
    function3 = ui.TextInput(label="Função 3", max_length=80, required=False)

    def __init__(self, event_id: int, sheet_page: str = "", caller_name: str = "",
                 sheet_url: str | None = None):
        super().__init__()
        self.event_id    = event_id
        self.sheet_page  = sheet_page
        self.caller_name = caller_name
        self.sheet_url   = sheet_url

    async def on_submit(self, interaction: discord.Interaction):
        roles = [self.function1.value, self.function2.value, self.function3.value]
        roles = [r.strip() for r in roles if r and r.strip()]
        await interaction.response.defer(ephemeral=True)
        text = await _register_functions(
            interaction, self.event_id, self.sheet_page, self.caller_name, roles,
        )
        embed = _massinfo_embed(self.event_id, self.sheet_url, text,
                                color=discord.Color.green())
        await interaction.followup.send(embed=embed, ephemeral=True)
        try:
            await utils.schedule_ephemeral_from_ctx(interaction)
        except Exception:
            pass


# ==================================================================
# Confirmação: já inscrito → quer alterar?
# ==================================================================
class AlreadyRegisteredView(ui.View):
    """Mostrada quando a pessoa clica no botão de um CTA em que já se inscreveu.
    `roles` vem pré-carregada e CRUA (sem gates) — os gates são reaplicados no
    clique de "Alterar", com o estado mais novo de vagas/escalação."""

    def __init__(self, event_id: int, sheet_page: str, caller_name: str,
                 comp: str, roles: list[str], sheet_url: str | None = None):
        super().__init__(timeout=180)
        self.event_id    = event_id
        self.sheet_page  = sheet_page
        self.caller_name = caller_name
        self.comp        = comp
        self.roles       = roles
        self.sheet_url   = sheet_url

    @ui.button(label="🔄 Alterar funções", style=discord.ButtonStyle.primary)
    async def alter_btn(self, interaction: discord.Interaction, _btn: ui.Button):
        # Usa as roles PRÉ-CARREGADAS (cache). Nada de rede aqui — o callback
        # precisa responder em até 3s, e list_roles leva ~2-3s.
        cog = interaction.client.cogs.get('MassInfoCog')
        roles = self.roles
        if not roles and self.comp and cog:
            roles = cog._roles_now(self.comp)
        # Reaplica TODOS os gates (vagas/parties/marcadores + cargo) com o estado atual.
        ev = await get_event_by_id(self.event_id) or {}
        slots = await cog.get_slots_cached(self.sheet_page) if (cog and self.sheet_page) else []
        roles, why = await _apply_gates(interaction.user, ev, roles, slots)
        if roles:
            header = f"NOVAS · comp **{self.comp}**" if self.comp else "NOVAS funções"
            view = FunctionPickView(
                self.event_id, self.sheet_page, self.caller_name,
                roles, self.sheet_url, header=header,
                recent_roles=await get_user_recent_roles(interaction.user.id))
            await interaction.response.edit_message(
                content=view._content(), embed=None, view=view,
            )
        elif why:
            msg = _NO_SLOTS_MSG if why == 'vagas' else _NO_ROLE_MSG
            embed = _massinfo_embed(
                self.event_id, self.sheet_url,
                f"{msg}\n\nSeu registro foi **mantido**.",
                color=discord.Color.orange(),
            )
            await interaction.response.edit_message(content=None, embed=embed, view=None)
        else:
            # Fallback: modal de texto livre (planilha offline / comp sem roles).
            await interaction.response.send_modal(
                FunctionsModal(event_id=self.event_id, sheet_page=self.sheet_page,
                               caller_name=self.caller_name, sheet_url=self.sheet_url)
            )

    @ui.button(label="🗑️ Retirar meu nome", style=discord.ButtonStyle.danger)
    async def remove_btn(self, interaction: discord.Interaction, _btn: ui.Button):
        # Apaga o registro local e enfileira a remoção do nome na planilha.
        prev = await get_user_function_log(self.event_id, interaction.user.id)
        if not prev:
            embed = _massinfo_embed(self.event_id, self.sheet_url,
                                    "Você não tem registro neste CTA.")
            await interaction.response.edit_message(content=None, embed=embed, view=None)
            return
        await delete_user_function_logs(self.event_id, interaction.user.id)
        cog = interaction.client.cogs.get('MassInfoCog')
        if cog and self.sheet_page and await sheets.is_configured():
            cog.enqueue_sheet_remove({
                'sheet_page': self.sheet_page,
                'name': prev.get('user_name') or '',
                'row': prev.get('sheet_row') or 0,
            })
        if cog:
            cog.request_massinfo_refresh()
        cta_cog = interaction.client.cogs.get('CTACog')
        if cta_cog and hasattr(cta_cog, 'mark_event_dirty'):
            cta_cog.mark_event_dirty(self.event_id)
        embed = _massinfo_embed(
            self.event_id, self.sheet_url,
            "🗑️ Seu nome foi **retirado** deste CTA (removido da planilha).",
            color=discord.Color.orange(),
        )
        await interaction.response.edit_message(content=None, embed=embed, view=None)

    @ui.button(label="✖ Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, _btn: ui.Button):
        embed = _massinfo_embed(
            self.event_id, self.sheet_url, "Ok, seu registro foi **mantido**.",
        )
        await interaction.response.edit_message(content=None, embed=embed, view=None)


async def _open_function_signup(interaction: discord.Interaction, ev: dict,
                                preloaded_roles: list | None = None,
                                caller_name: str = "") -> None:
    """Abre o fluxo de inscrição de funções de um CTA (o mesmo do botão do evento):
    já-inscrito → confirmar alteração; senão → seletor de roles (com gates) ou modal.
    PRECISA ser a 1ª resposta da interação (usa send_message/send_modal) — NÃO chamar
    depois de defer. Usado pelo botão do evento e pela lupa (jogador sem nick)."""
    if ev.get('split_finalized'):
        await send_err(interaction, "Esse CTA já teve o split finalizado — não aceita mais registros.")
        return
    event_id    = ev['id']
    comp        = ev.get('comp') or ""
    sheet_page  = ev.get('sheet_page') or ""
    sheet_url   = ev.get('sheet_url') or None
    cname       = ev.get('caller_name') or caller_name

    cog = interaction.client.cogs.get('MassInfoCog')
    raw_roles = list(preloaded_roles or [])
    if not raw_roles and comp and cog:
        raw_roles = cog._roles_now(comp)

    # Já se inscreveu? Mostra as funções atuais e pede confirmação de alteração.
    existing = await get_user_function_log(event_id, interaction.user.id)
    if existing:
        chosen = [existing.get('function1'), existing.get('function2'),
                  existing.get('function3')]
        chosen = [r for r in chosen if r]
        chosen_txt = "\n".join(f"• {r}" for r in chosen) or "• (nenhuma)"
        embed = _massinfo_embed(
            event_id, sheet_url,
            f"⚠️ Você **já se inscreveu** com:\n{chosen_txt}\n\n"
            f"Deseja **alterar**? Isso apaga o registro anterior (nome + roles "
            f"na planilha) e deixa você escolher de novo.",
        )
        await interaction.response.send_message(
            embed=embed,
            view=AlreadyRegisteredView(event_id, sheet_page, cname, comp, raw_roles, sheet_url),
            ephemeral=True,
        )
        try:
            await utils.schedule_ephemeral_from_ctx(interaction)
        except Exception:
            pass
        return

    # Gates: quantidade (vagas/parties/marcadores, via slots em cache) + cargo.
    slots = await cog.get_slots_cached(sheet_page) if (cog and sheet_page) else []
    roles, why = await _apply_gates(interaction.user, ev, raw_roles, slots)

    if roles:
        view = FunctionPickView(
            event_id, sheet_page, cname, roles, sheet_url,
            header=f"comp **{comp}**",
            recent_roles=await get_user_recent_roles(interaction.user.id))
        await interaction.response.send_message(
            content=view._content(), view=view, ephemeral=True)
        try:
            await utils.schedule_ephemeral_from_ctx(interaction)
        except Exception:
            pass
        return

    if why:
        embed = _massinfo_embed(
            event_id, sheet_url,
            _NO_SLOTS_MSG if why == 'vagas' else _NO_ROLE_MSG,
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        try:
            await utils.schedule_ephemeral_from_ctx(interaction)
        except Exception:
            pass
        return

    # Fallback: modal de texto livre (planilha offline / comp sem roles).
    await interaction.response.send_modal(
        FunctionsModal(event_id=event_id, sheet_page=sheet_page,
                       caller_name=cname, sheet_url=sheet_url)
    )


# ==================================================================
# View dinâmica: 1 botão por CTA pendente
# ==================================================================
class MassInfoView(ui.View):
    """View NÃO persistente — recriada toda vez que a embed é atualizada."""

    def __init__(self, pending_events: list, roles_by_comp: dict | None = None):
        super().__init__(timeout=None)
        roles_by_comp = roles_by_comp or {}
        now = datetime.now(timezone.utc)

        # Limite de botões em uma view: 25 (5 linhas × 5). A lupa (🔍) vem SEMPRE
        # PRIMEIRO — só o ícone, sem label — pros CTAs na janela da lupa (T-10min
        # até o callout); depois os botões de inscrição de cada CTA.
        pending = pending_events[:25]

        for ev in pending:
            if len(self.children) >= 25:
                break
            if _lupa_active(ev, now):      # em andamento OU faltando <= 10 min
                lupa = ui.Button(
                    emoji="🔍",            # só o ícone (sem label)
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"massinfo:lupa:{ev['id']}",
                )
                lupa.callback = self._make_lupa_callback(ev['id'])
                self.add_item(lupa)

        for ev in pending:
            if len(self.children) >= 25:
                break
            comp  = ev.get('comp') or ""
            roles = roles_by_comp.get(comp, [])
            emoji, time_str = _cta_status_time(ev, now)
            btn = ui.Button(
                label=time_str,            # só o horário UTC
                emoji=emoji,               # status (mesmo ícone do embed)
                style=discord.ButtonStyle.primary,
                # custom_id encoded com event_id pra sobrevivência a restart
                custom_id=f"massinfo:func:{ev['id']}",
            )
            btn.callback = self._make_callback(ev['id'], ev.get('caller_name', ''), roles)
            self.add_item(btn)

    @staticmethod
    def _make_callback(event_id: int, caller_name: str, preloaded_roles: list[str]):
        async def callback(interaction: discord.Interaction):
            ev = await get_event_by_id(event_id)
            if not ev:
                await send_err(interaction, "Este CTA não existe mais.")
                return
            await _open_function_signup(interaction, ev, preloaded_roles, caller_name)
        return callback

    @staticmethod
    def _make_lupa_callback(event_id: int):
        async def callback(interaction: discord.Interaction):
            ev = await get_event_by_id(event_id)
            if not ev:
                await send_err(interaction, "Este CTA não existe mais.")
                return
            await _open_lupa(interaction, ev)
        return callback


# ==================================================================
# Cog principal
# ==================================================================
# ==================================================================
# Massa AO VIVO: quando o CTA inicia, dá-se UM ping efêmero @everyone "MASSANDO
# AGORA" (apagado em seguida) e as parties (X/PARTY_SIZE com /join do líder)
# passam a aparecer numa ZONA no fim do próprio embed do mass-info, junto do
# botão de lupa (🔍 "Minha PT", ephemeral) do CTA em andamento.
# ==================================================================
PARTY_SIZE = 20   # tamanho de party (igual ao bloco do sheet)


def _parties_from_assignments(assignments: list) -> list:
    """Agrupa a escalação por party: [{'party','count','leader'}], ordenado por party."""
    by_party: dict = {}
    for a in assignments or []:
        p = a.get('party')
        # Slot VAGO (sem nome) é vaga da comp — não conta pro painel de massa.
        if not p or not (a.get('name') or '').strip():
            continue
        slot = by_party.setdefault(p, {'party': p, 'count': 0, 'leader': None})
        slot['count'] += 1
        if a.get('leader'):
            slot['leader'] = (a.get('name') or '').strip() or None
    return [by_party[k] for k in sorted(by_party)]


def _startboard_content(ev: dict) -> str:
    """Texto do ping efêmero de 'massando agora' (o @everyone notifica no envio)."""
    msg_txt = (ev.get('cta_message') or '').strip().upper() or (ev.get('comp') or 'A CTA').upper()
    return f"@everyone 🚨 MASSANDO AGORA PARA {msg_txt}"


def _zone_blocks(parties: list) -> list[str]:
    """Linhas da zona de parties (PARTY N ~ (x/20) + /join líder) pro embed do mass-info."""
    blocks = []
    for p in parties or []:
        leader = p['leader'] or '—'
        blocks.append(f"PARTY {p['party']} ~ ({p['count']}/{PARTY_SIZE})\n"
                      f"```/join {leader}```")
    return blocks


def _build_mypt_embed(ev: dict, a: dict) -> discord.Embed:
    """Embed ephemeral do botão 'Minha PT': party + função + equipamento de quem clicou."""
    from cogs.escalacao import _EQUIP_FIELDS   # reaproveita a formatação de equipamento
    role  = a.get('role') or '?'
    party = a.get('party')
    crown = ' 👑' if a.get('leader') else ''
    embed = discord.Embed(description=f"{role} ~ PT **{party}**", color=_MI_COLOR)
    eq_lines = []
    for key, label, always in _EQUIP_FIELDS:
        val = (a.get(key) or '').strip()
        if val:
            eq_lines.append(f"**{label}:** {val}")
        elif always:
            eq_lines.append(f"**{label}:** —")
    if eq_lines:
        embed.add_field(name="Build", value="\n".join(eq_lines), inline=False)
    return embed


async def _open_lupa(interaction: discord.Interaction, ev: dict) -> None:
    """Lupa (🔍) de um CTA em andamento. Tudo responde na 1ª interação (sem rede
    bloqueante — usa o cache de slots), então não precisa deferir:
      1) escalado (nick na escalação) → mostra a PT/build;
      2) não escalado mas com função registrada → mostra as funções escolhidas;
      3) sem função → abre o menu de funções (igual ao botão do CTA)."""
    event_id  = ev['id']
    sheet_url = ev.get('sheet_url') or None
    page      = ev.get('sheet_page') or ''
    cog = interaction.client.cogs.get('MassInfoCog')

    cfg      = await load_economy_config()
    voice_id = cfg.get('voice_cta')
    voice_line = f"\n\n📞 <#{voice_id}>" if voice_id else ""

    # 1) Escalado? Compara o nick com a escalação EM CACHE (sem chamar a planilha
    #    no caminho da resposta de 3s; o cache fica morno enquanto o CTA roda).
    reg  = await get_registration(interaction.user.id)
    nick = (reg.get('nick') if reg else None)
    mine = None
    if nick and page and cog:
        slots = await cog.get_slots_cached(page)
        nl = nick.strip().lower()
        mine = next((a for a in slots
                     if (a.get('name') or '').strip().lower() == nl), None)
    if mine:
        embed = _build_mypt_embed(ev, mine)
        if voice_id:
            embed.add_field(name="​", value=f"📞 <#{voice_id}>", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        try:
            await utils.schedule_ephemeral_from_ctx(interaction)
        except Exception:
            pass
        return

    # 2) Não escalado, mas já registrou função → mostra o que escolheu.
    existing = await get_user_function_log(event_id, interaction.user.id)
    if existing:
        chosen = [existing.get('function1'), existing.get('function2'),
                  existing.get('function3')]
        chosen = [r for r in chosen if r]
        chosen_txt = "\n".join(f"• {r}" for r in chosen) or "• (nenhuma)"
        embed = _massinfo_embed(
            event_id, sheet_url,
            f"📋 Você ainda **não está escalado** neste CTA.\n\n"
            f"Suas funções escolhidas:\n{chosen_txt}{voice_line}",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        try:
            await utils.schedule_ephemeral_from_ctx(interaction)
        except Exception:
            pass
        return

    # 3) Sem função registrada → abre o menu de funções (mesmo fluxo do botão do CTA).
    await _open_function_signup(interaction, ev)


class MassInfoCog(commands.Cog, name="MassInfoCog"):
    """Mantém a embed do canal mass-info atualizada com CTAs pendentes."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Cache de roles por (servidor, comp) — cada guild tem sua planilha.
        # Evita chamar a planilha dentro do callback do botão (que tem ~3s).
        self._roles_cache: dict = {}      # (gid, comp) -> [roles]
        self._roles_cache_ts: dict = {}   # (gid, comp) -> ts
        # Cache dos SLOTS da escalação por página (preenchidos + vagas) — alimenta
        # o gate de quantidade. Mesma regra do _roles_cache: nunca bloquear na rede.
        self._slots_cache: dict = {}      # (gid, page) -> [slots]
        self._slots_cache_ts: dict = {}   # (gid, page) -> ts
        self._slots_refreshing: set = set()   # (gid, page) com refresh em voo
        self._noslot_warned: set = set()      # (gid, page) já avisado: sem vagas nos dados
        # Último estado conhecido de cada CTA ('scheduled'/'running') — pra detectar
        # transições e forçar o refresh do embed.
        self._cta_status: dict = {}   # (guild_id, event_id) -> 'scheduled'|'running'
        # Último refresh do mass-info por servidor enquanto há CTA em andamento
        # (mantém a zona de parties / contagem atualizada, gate de ~60s).
        self._zone_refresh_ts: dict = {}   # guild_id -> monotonic
        # Servidores com refresh do mass-info pendente (coalescido pelo dynamics_loop):
        # registrar/retirar função marca aqui pra a contagem atualizar sem spammar edits.
        self._massinfo_dirty: set = set()   # guild_ids
        # Fila de escrita na planilha (background): o registro de funções grava no
        # DB na hora e a sincronização com o Sheets roda atrás, em workers limitados.
        # Assim 200 cliques em segundos NÃO esperam o Google (timeout de 60s).
        self._sheet_queue: asyncio.Queue = asyncio.Queue()
        self._sheet_workers: list[asyncio.Task] = []

    async def cog_load(self):
        print("✓ MassInfo Cog carregada")
        self.roles_warmup_loop.start()
        self.dynamics_loop.start()
        # Workers da fila de sincronização com a planilha.
        self._sheet_workers = [
            self.bot.loop.create_task(self._sheet_worker())
            for _ in range(SHEET_SYNC_WORKERS)
        ]
        # Refresh inicial após o bot conectar
        self.bot.loop.create_task(self._refresh_when_ready())

    def cog_unload(self):
        self.roles_warmup_loop.cancel()
        self.dynamics_loop.cancel()
        for t in self._sheet_workers:
            t.cancel()

    # ------------------------------------------------------------------
    # Fila de sincronização com a planilha (escrita em background)
    # ------------------------------------------------------------------
    def enqueue_sheet_write(self, job: dict):
        """Enfileira uma escrita de funções na planilha (não bloqueia o caller)."""
        job.setdefault('guild_id', database.get_current_guild())   # multi-tenant
        try:
            self._sheet_queue.put_nowait(job)
        except asyncio.QueueFull:
            print("✗ mass-info: fila de sync da planilha cheia — registro só no DB.")

    def enqueue_sheet_remove(self, job: dict):
        """Enfileira a REMOÇÃO do nome de um jogador na planilha (background)."""
        job['action'] = 'remove'
        job.setdefault('guild_id', database.get_current_guild())   # multi-tenant
        try:
            self._sheet_queue.put_nowait(job)
        except asyncio.QueueFull:
            print("✗ mass-info: fila de sync da planilha cheia — remoção só no DB.")

    def request_massinfo_refresh(self):
        """Marca o servidor atual pra um refresh COALESCIDO do mass-info (a contagem
        de presentes atualiza no próximo tick do dynamics_loop, sem spammar edits)."""
        self._massinfo_dirty.add(database.get_current_guild())

    async def _sheet_worker(self):
        await self.bot.wait_until_ready()
        while True:
            job = await self._sheet_queue.get()
            try:
                with database.using_guild(job.get('guild_id')):
                    await self._process_sheet_job(job)
            except Exception as e:
                print(f"✗ mass-info: erro sincronizando funções com a planilha: {e}")
            finally:
                self._sheet_queue.task_done()

    async def _process_sheet_job(self, job: dict):
        if not (job.get('sheet_page') and await sheets.is_configured()):
            return
        # Job de CANCELAMENTO (botão "retirar meu nome"): marca o nome como cancelado
        # na planilha (fica cinza no rodapé da lista, não some — modelo persistente).
        if job.get('action') == 'remove':
            if job.get('name'):
                try:
                    await sheets.cancel_functions(job['sheet_page'], job['name'])
                except Exception as e:
                    print(f"✗ mass-info: erro cancelando nome na planilha: {e}")
            return
        # Re-registro: NÃO remove o anterior — o submit faz upsert e trata o nick
        # antigo via prev_name (some o passo de remoção dupla do modelo de fila).
        try:
            row = await sheets.submit_functions(
                job['sheet_page'], job['sheet_name'], job['roles'],
                job['event_id'], job.get('caller_name', ''),
                prev_name=job.get('prev_name') or '',
            )
            if row is not None:
                await mark_function_log_synced(job['log_id'], row)
        except Exception as e:
            print(f"✗ mass-info: erro enviando funções pra planilha: {e}")

    # ------------------------------------------------------------------
    # Cache de roles (pré-carregado pra não estourar a janela de 3s)
    # ------------------------------------------------------------------
    async def get_roles_cached(self, comp: str, ttl: float = 3600.0) -> list[str]:
        """Roles da comp, com cache. NUNCA bloqueia na rede (o callback do botão tem
        ~3s e o Sheets pode levar até 60s): se vencido OU vazio, atualiza em background
        e devolve o que tem na hora (pode ser []). O warmup de 30min mantém quente."""
        if not comp:
            return []
        gid = database.get_current_guild()
        key = (gid, comp)
        now = time.monotonic()
        cached = self._roles_cache.get(key)
        fresh = cached and (now - self._roles_cache_ts.get(key, 0)) < ttl
        if fresh:
            return cached
        asyncio.create_task(self._refresh_roles_cache(comp, gid))
        return cached or []

    def _roles_now(self, comp: str) -> list:
        """Roles em cache p/ a comp no SERVIDOR ATUAL (sem rede). [] se não tiver."""
        return self._roles_cache.get((database.get_current_guild(), comp), [])

    # ------------------------------------------------------------------
    # Cache de SLOTS da escalação (gate de quantidade: vagas/parties)
    # ------------------------------------------------------------------
    async def get_slots_cached(self, page: str, ttl: float = 120.0) -> list:
        """Slots da escalação (preenchidos + vagas) da página, com cache. NUNCA
        bloqueia na rede (o callback do botão tem ~3s): se vencido, atualiza em
        background e devolve o que tem na hora ([] = o gate libera tudo)."""
        if not page:
            return []
        gid = database.get_current_guild()
        key = (gid, page)
        cached = self._slots_cache.get(key)
        fresh = (cached is not None and
                 (time.monotonic() - self._slots_cache_ts.get(key, 0)) < ttl)
        if fresh:
            return cached
        # 1 refresh em voo por página: uma rajada de cliques com o cache vencido
        # não pode virar uma rajada de chamadas ao Apps Script.
        if key not in self._slots_refreshing:
            asyncio.create_task(self._refresh_slots_cache(page, gid))
        return cached or []

    def _store_slots(self, page: str, slots: list, gid=None):
        if gid is None:
            gid = database.get_current_guild()
        key = (gid, page)
        self._slots_cache[key] = slots or []
        self._slots_cache_ts[key] = time.monotonic()
        # Diagnóstico (1x por página): dados SEM nenhum slot vago = comp 100%
        # cheia OU Apps Script desatualizado (não devolve vagas) → o gate de
        # quantidade fica em fail-open e libera tudo.
        if slots and key not in self._noslot_warned and \
                all((s.get('name') or '').strip() for s in slots):
            self._noslot_warned.add(key)
            print(f"⚠️ gate de funções: escalação de '{page}' veio sem NENHUM slot "
                  f"vago — comp cheia ou .gs desatualizado (gate liberando tudo).")

    async def _refresh_slots_cache(self, page: str, gid=None):
        if gid is None:
            gid = database.get_current_guild()
        key = (gid, page)
        if key in self._slots_refreshing:
            return
        self._slots_refreshing.add(key)
        try:
            with database.using_guild(gid):
                if not await sheets.is_configured():
                    return
                slots = await sheets.get_assignments(page)
            # Guarda mesmo vazio ([] também significa falha do get_assignments):
            # o gate FAZ fail-open com [], e o ts evita refresh em rajada.
            self._store_slots(page, slots, gid)
        except Exception as e:
            print(f"✗ Erro atualizando cache de slots ({page}) [{gid}]: {e}")
        finally:
            self._slots_refreshing.discard(key)

    async def _refresh_roles_cache(self, comp: str, gid=None):
        if gid is None:
            gid = database.get_current_guild()
        try:
            with database.using_guild(gid):
                roles = await sheets.list_roles(comp)
        except Exception as e:
            print(f"✗ Erro atualizando cache de roles ({comp}) [{gid}]: {e}")
            return
        if roles:
            self._roles_cache[(gid, comp)] = roles
            self._roles_cache_ts[(gid, comp)] = time.monotonic()

    @tasks.loop(minutes=30)
    async def roles_warmup_loop(self):
        """Mantém o cache de roles quente, POR SERVIDOR (cada guild tem sua planilha)."""
        for gid in await get_activated_guild_ids():
            with database.using_guild(gid):
                try:
                    if not await sheets.is_configured():
                        continue
                    events = await get_active_ctas()
                except Exception as e:
                    print(f"✗ roles_warmup_loop [{gid}]: {e}")
                    continue
                comps = {ev.get('comp') for ev in events if ev.get('comp')}
            for comp in comps:
                await self._refresh_roles_cache(comp, gid)

    @roles_warmup_loop.before_loop
    async def before_roles_warmup_loop(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Dinâmica: aviso de ~10min + refresh automático nas transições de estado
    # ------------------------------------------------------------------
    PRESTART_WARN_SECONDS = 600   # avisa quando faltam ~10 min p/ começar
    FLASH_WARN_SECONDS    = 120   # CTA criado a <=2min do início (ou já rolando) = flash mass

    def _find_channel(self, chan_id):
        if not chan_id:
            return None
        for g in self.bot.guilds:
            ch = g.get_channel(chan_id)
            if ch:
                return ch
        return None

    def _started_dt(self, ev: dict):
        try:
            dt = datetime.fromisoformat(ev.get('started_at') or '')
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    @tasks.loop(seconds=30)
    async def dynamics_loop(self):
        for gid in await get_activated_guild_ids():
            with database.using_guild(gid):
                try:
                    await self._dynamics_once(gid)
                except Exception as e:
                    print(f"✗ mass-info dynamics [{gid}]: {e}")

    async def _dynamics_once(self, gid):
        events = await get_active_ctas()
        now = datetime.now(timezone.utc)
        force_refresh = False
        force_recreate = False
        any_lupa = False

        for ev in events:
            key = (gid, ev['id'])   # event_id sozinho é ambíguo entre servidores
            dt  = self._started_dt(ev)
            scheduled = bool(dt and dt > now)
            cur  = 'scheduled' if scheduled else 'running'
            prev = self._cta_status.get(key)
            lupa = _lupa_active(ev, now)   # em andamento OU faltando <= 10 min

            # Mantém o cache de slots/escalação morno (alimenta o gate de vagas, a
            # zona de parties e a contagem de presentes). Na janela da lupa refresca
            # a cada ~60s (a zona ao vivo usa esse cache); fora dela, devagar.
            if ev.get('sheet_page'):
                await self.get_slots_cached(ev['sheet_page'], ttl=60.0 if lupa else 240.0)

            # Aviso "~10 min p/ começar" (agendado, ainda sem aviso postado).
            if scheduled and not ev.get('prestart_msg_id') and dt:
                if 0 < (dt - now).total_seconds() <= self.PRESTART_WARN_SECONDS:
                    await self._post_prestart_warning(ev, dt)
                    force_refresh = True   # mostra a lupa/zona já no T-10min

            # EM ANDAMENTO: ping de início "massando agora" (1×) + zona ao vivo.
            if cur == 'running':
                try:
                    await self._announce_running(ev)
                except Exception as e:
                    print(f"✗ ping de massa CTA #{ev['id']}: {e}")

            if lupa:
                any_lupa = True

            # Transição AGENDADO → EM ANDAMENTO: força o refresh do tracker.
            if prev == 'scheduled' and cur == 'running':
                force_refresh = True
                force_recreate = True

            self._cta_status[key] = cur

        # Limpa o estado de CTAs DESTE servidor que saíram da lista.
        active_keys = {(gid, ev['id']) for ev in events}
        for gone in [k for k in self._cta_status if k[0] == gid and k not in active_keys]:
            del self._cta_status[gone]
        if not any_lupa:
            self._zone_refresh_ts.pop(gid, None)

        # Refresh COALESCIDO pedido por registro/retirada de função (contagem).
        if gid in self._massinfo_dirty:
            self._massinfo_dirty.discard(gid)
            force_refresh = True

        # Enquanto houver CTA na janela da lupa, mantém a zona/contagem fresca (~60s).
        if any_lupa:
            last = self._zone_refresh_ts.get(gid, 0)
            if time.monotonic() - last >= 60:
                self._zone_refresh_ts[gid] = time.monotonic()
                force_refresh = True

        if force_refresh:
            try:
                await self.refresh_massinfo(recreate=force_recreate)
            except Exception as e:
                print(f"✗ mass-info dynamics: erro no refresh: {e}")

    @dynamics_loop.before_loop
    async def before_dynamics_loop(self):
        await self.bot.wait_until_ready()

    @staticmethod
    def _ping_text(ev: dict) -> str:
        """Mensagem do caller (definida no /cta) em CAPSLOCK; fallback p/ comp."""
        return (ev.get('cta_message') or '').strip().upper() or (ev.get('comp') or 'A CTA').upper()

    async def post_cta_created_ping(self, event_id: int):
        """Ping na CRIAÇÃO do /cta:
          · agendado com folga → @everyone que se APAGA logo (fire-and-delete);
          · "em cima da hora" (flash mass, <=FLASH_WARN_SECONDS) → ping de INÍCIO
            persistente (mesmo do começo do evento), pra não duplicar depois."""
        ev = await get_event_by_id(event_id)
        if not ev or ev.get('prestart_msg_id') or ev.get('startboard_msg_id'):
            return
        dt = self._started_dt(ev)
        if dt is None:
            return
        cfg     = await load_economy_config()
        channel = self._find_channel(cfg.get('channel_massinfo'))
        if not channel:
            return
        msg_txt = self._ping_text(ev)
        if (dt - datetime.now(timezone.utc)).total_seconds() <= self.FLASH_WARN_SECONDS:
            await self._post_start_ping(ev, f"@everyone FLASH MASS AGORA PARA {msg_txt}")
        else:
            await self._fire_ping(channel, f"@everyone NOVA CTA PARA {msg_txt}", event_id)

    async def _post_prestart_warning(self, ev: dict, dt):
        """Ping de ~10 min: PERSISTE no canal até o ping de início aparecer."""
        cfg     = await load_economy_config()
        channel = self._find_channel(cfg.get('channel_massinfo'))
        if not channel:
            return
        try:
            msg = await channel.send(
                f"@everyone MASSANDO EM 10 MINUTOS PARA {self._ping_text(ev)}",
                allowed_mentions=discord.AllowedMentions(everyone=True),
            )
            await update_event_meta(ev['id'], prestart_msg_id=msg.id)
        except discord.HTTPException as e:
            print(f"✗ Erro postando aviso de pré-início do CTA #{ev['id']}: {e}")

    async def _post_start_ping(self, ev: dict, text: str):
        """Ping de INÍCIO: apaga o ping de 10 min e posta um novo que dura
        START_PING_SECONDS (20 min) ou até o callout limpar o canal."""
        cfg     = await load_economy_config()
        channel = self._find_channel(cfg.get('channel_massinfo'))
        if not channel:
            return
        await self._delete_prestart_ping(ev)   # tira o ping de 10 min
        try:
            msg = await channel.send(
                text, allowed_mentions=discord.AllowedMentions(everyone=True),
                delete_after=START_PING_SECONDS,
            )
            await update_event_meta(ev['id'], startboard_msg_id=msg.id)
        except discord.HTTPException as e:
            print(f"✗ Erro postando ping de início do CTA #{ev['id']}: {e}")

    async def _fire_ping(self, channel, text: str, event_id: int, field: str | None = None):
        """Ping efêmero: envia (a notificação @everyone dispara no envio), opcionalmente
        grava o id no DB (dedupe) e apaga a mensagem em seguida — canal limpo, todos
        notificados."""
        try:
            msg = await channel.send(
                text, allowed_mentions=discord.AllowedMentions(everyone=True),
            )
        except discord.HTTPException as e:
            print(f"✗ Erro postando ping do CTA #{event_id}: {e}")
            return
        if field:
            await update_event_meta(event_id, **{field: msg.id})
        try:
            await msg.delete()
        except discord.HTTPException:
            pass

    async def _delete_prestart_warning(self, ev: dict):
        """Apaga o aviso de início — o ping de 10 min/flash E o painel de massa.
        Chamado no encerramento do CTA (cta.py) e ao adiar."""
        cfg     = await load_economy_config()
        channel = self._find_channel(cfg.get('channel_massinfo'))
        for field in ('prestart_msg_id', 'startboard_msg_id'):
            mid = ev.get(field)
            if mid and channel:
                try:
                    msg = await channel.fetch_message(mid)
                    await msg.delete()
                except discord.HTTPException:
                    pass
        await update_event_meta(ev['id'], prestart_msg_id=None, startboard_msg_id=None)

    async def clear_massinfo_channel(self):
        """Apaga TODAS as mensagens do canal de mass-info, deixando SÓ a embed canônica
        (massinfo_message_id). Chamado no /callout pra zerar o canal — pings, painéis de
        massa e qualquer outra mensagem (do bot ou de usuários). Best-effort."""
        cfg     = await load_economy_config()
        channel = self._find_channel(cfg.get('channel_massinfo'))
        if not channel:
            return
        keep_id = cfg.get('massinfo_message_id')
        try:
            async for m in channel.history(limit=200):
                if keep_id and m.id == keep_id:
                    continue   # preserva a embed canônica do mass-info
                try:
                    await m.delete()
                except discord.HTTPException:
                    pass  # já apagada / sem permissão (esperado)
        except discord.HTTPException as e:
            print(f"✗ Erro limpando o canal de mass-info: {e}")

    async def _delete_prestart_ping(self, ev: dict):
        """Apaga SÓ o ping de 10 min/flash (usado ao trocá-lo pelo painel de massa)."""
        mid = ev.get('prestart_msg_id')
        if not mid:
            return
        cfg     = await load_economy_config()
        channel = self._find_channel(cfg.get('channel_massinfo'))
        if channel:
            try:
                msg = await channel.fetch_message(mid)
                await msg.delete()
            except discord.HTTPException:
                pass
        await update_event_meta(ev['id'], prestart_msg_id=None)

    # ------------------------------------------------------------------
    # Massando agora: ping de início (dura 20 min) — as parties vivem no embed
    # ------------------------------------------------------------------
    async def _announce_running(self, ev: dict):
        """No 1º tick em andamento: apaga o ping de 10 min e posta o ping de início
        'massando agora' (dura 20 min/até callout). Dedupe via startboard_msg_id."""
        if ev.get('startboard_msg_id'):
            return   # já anunciado
        await self._post_start_ping(ev, _startboard_content(ev))

    async def _refresh_when_ready(self):
        await self.bot.wait_until_ready()
        for gid in await get_activated_guild_ids():
            with database.using_guild(gid):
                try:
                    await self.refresh_massinfo()
                except Exception as e:
                    print(f"✗ Erro inicial do mass-info [{gid}]: {e}")

    # ------------------------------------------------------------------
    # /liberarfuncoes — válvula manual do caller (gate de vagas/parties)
    # ------------------------------------------------------------------
    async def _resolve_cta_arg(self, ctx, evento: int | None) -> dict | None:
        """Resolve o CTA alvo de um comando: o id passado ou o ÚNICO ativo.
        None = erro já respondido ao usuário."""
        if evento is None:
            active = await get_active_ctas()
            if not active:
                await send_err(ctx, "Não há CTA ativo.")
                return None
            if len(active) > 1:
                await send_err(ctx, "Há mais de um CTA ativo — informe o `evento`.")
                return None
            return active[0]
        ev = await get_event_by_id(evento)
        if not ev or ev.get('ended_at'):
            await send_err(ctx, "CTA não encontrado (ou já encerrado).")
            return None
        return ev

    @commands.hybrid_command(
        name="liberarfuncoes",
        description="Liga/desliga a liberação TOTAL das funções de um CTA (ignora vagas/parties/marcadores).",
    )
    @app_commands.describe(evento="CTA alvo (padrão: o único CTA ativo)")
    @app_commands.autocomplete(evento=_liberar_evento_autocomplete)
    async def liberarfuncoes(self, ctx: commands.Context, evento: int | None = None):
        """Quando a escalação atrasa (ninguém arrasta nomes), os gates nunca abrem —
        este comando libera TODAS as funções da comp no registro do mass-info.
        Rodar de novo reativa os gates. O gate por CARGO continua valendo."""
        if not await has_configured_role(ctx.author, 'role_caller', 'role_council',
                                         'role_logistic'):
            await send_err(ctx, "Sem permissão — apenas **Caller**, **Council** ou **Logistic**.")
            return
        ev = await self._resolve_cta_arg(ctx, evento)
        if not ev:
            return
        novo = 0 if ev.get('functions_released') else 1
        await update_event_meta(ev['id'], functions_released=novo)
        if novo:
            await send_ok(ctx, f"Funções do CTA **#{ev['id']}** **liberadas** — o registro "
                               f"passa a ignorar vagas/parties/marcadores. Rode o comando "
                               f"de novo para reativar os gates.")
        else:
            await send_ok(ctx, f"Gates do CTA **#{ev['id']}** **reativados** — "
                               f"vagas/parties/marcadores voltam a valer no registro.")

    @commands.hybrid_command(
        name="gateinfo",
        description="Diagnóstico do gate de funções de um CTA: slots, vagas, inscritos e o que está visível.",
    )
    @app_commands.describe(evento="CTA alvo (padrão: o único CTA ativo)")
    @app_commands.autocomplete(evento=_liberar_evento_autocomplete)
    async def gateinfo(self, ctx: commands.Context, evento: int | None = None):
        """Mostra o que o gate de funções está vendo neste CTA e POR QUE cada
        bloco está aberto/trancado — inclusive os fail-opens (.gs antigo etc.)."""
        if not await has_configured_role(ctx.author, 'role_caller', 'role_council',
                                         'role_logistic'):
            await send_err(ctx, "Sem permissão — apenas **Caller**, **Council** ou **Logistic**.")
            return
        ev = await self._resolve_cta_arg(ctx, evento)
        if not ev:
            return
        comp = ev.get('comp') or ''
        page = ev.get('sheet_page') or ''
        roles = await self.get_roles_cached(comp) if comp else []
        slots = await self.get_slots_cached(page) if page else []
        signups = await get_massinfo_ping_count(ev['id'])

        gid = database.get_current_guild()
        ts  = self._slots_cache_ts.get((gid, page)) if page else None
        cache_line = (f"Cache da escalação: atualizado há ~{int(time.monotonic() - ts)}s"
                      if ts is not None else
                      "Cache da escalação: **vazio** (nunca carregado — aquecendo agora)")

        report = _gate_report(roles, slots, signups, bool(ev.get('functions_released')))
        if comp and not roles:
            report = ("⚠️ Roles da comp ainda não estão em cache (aquecendo em "
                      "background) — rode de novo em ~10s.\n\n") + report
        head = f"CTA **#{ev['id']}** · comp **{comp or '—'}** · página `{page or '—'}`"
        await send_info(ctx, f"{head}\n{cache_line}\n\n{report}", title="Gate de funções")

    # ------------------------------------------------------------------
    # Refresh público — usado pelo CTA cog
    # ------------------------------------------------------------------
    async def refresh_massinfo(self, *, recreate: bool = False):
        """Cria ou atualiza a embed do canal mass-info refletindo os CTAs pendentes.

        `recreate=True` apaga a mensagem anterior e posta uma nova (em vez de editar).
        Usado quando um CTA entra/sai ou muda de fase — invalida botões antigos e evita
        acumular respostas efêmeras presas a interações obsoletas."""
        cfg     = await load_economy_config()
        chan_id = cfg.get('channel_massinfo')
        if not chan_id:
            return  # não configurado ainda

        # Encontra o canal
        channel = None
        for g in self.bot.guilds:
            ch = g.get_channel(chan_id)
            if ch:
                channel = ch
                break
        if not channel:
            return

        # Monta a embed + view (apenas CTAs EM ANDAMENTO), em ordem de HORÁRIO UTC
        # CRESCENTE — não pelo número do evento. started_at é ISO, então a ordenação
        # lexicográfica já é cronológica; sem horário (raro) vai pro fim.
        pending = await get_active_ctas()
        pending.sort(key=lambda e: e.get('started_at') or '~')   # sem horário → fim

        # Pré-carrega as roles de cada comp AQUI (em background, fora da janela
        # de 3s), pra ligar à view. Assim o callback do botão nunca chama a rede.
        roles_by_comp: dict[str, list[str]] = {}
        for ev in pending:
            comp = ev.get('comp') or ""
            if comp and comp not in roles_by_comp:
                roles_by_comp[comp] = await self.get_roles_cached(comp)

        # Contagem de "presentes na planilha" (escalados ∪ quem registrou função) e,
        # pros CTAs na janela da lupa (T-10min até callout), as parties pra zona
        # inferior do embed. Tudo a partir do cache de slots (sem rede) + DB local.
        now = datetime.now(timezone.utc)
        counts_by_event: dict[int, int] = {}
        parties_by_event: dict[int, list] = {}
        for ev in pending:
            page  = ev.get('sheet_page') or ''
            slots = await self.get_slots_cached(page) if page else []
            names = {(s.get('name') or '').strip().lower()
                     for s in slots if (s.get('name') or '').strip()}
            for nm in await get_function_log_names(ev['id']):
                names.add(nm.strip().lower())
            counts_by_event[ev['id']] = len(names)
            if _lupa_active(ev, now):   # janela da lupa → tem zona de parties
                parties = _parties_from_assignments(slots)
                if parties:
                    parties_by_event[ev['id']] = parties

        embed   = self._build_massinfo_embed(pending, counts_by_event, parties_by_event)
        view    = MassInfoView(pending, roles_by_comp) if pending else None

        msg_id = cfg.get('massinfo_message_id')

        if recreate and msg_id:
            try:
                old = await channel.fetch_message(msg_id)
                await old.delete()
            except discord.NotFound:
                pass
            except Exception as e:
                print(f"✗ Erro apagando mass-info p/ recriar: {e}")
            await update_economy_config({'massinfo_message_id': None})
            msg_id = None

        # Tenta editar; se não existir mais (ou recreate), cria nova
        msg = None
        if msg_id:
            try:
                msg = await channel.fetch_message(msg_id)
            except discord.NotFound:
                msg = None
            except Exception as e:
                print(f"✗ Erro buscando msg do mass-info: {e}")

        if msg and not recreate:
            try:
                await msg.edit(embed=embed, view=view)
                return
            except Exception as e:
                print(f"✗ Erro editando mass-info; recriando: {e}")

        # Criar nova
        try:
            new_msg = await channel.send(embed=embed, view=view)
            await update_economy_config({'massinfo_message_id': new_msg.id})
        except Exception as e:
            print(f"✗ Erro criando msg do mass-info: {e}")

    # ------------------------------------------------------------------
    # Builder da embed
    # ------------------------------------------------------------------
    def _build_massinfo_embed(self, pending: list, counts_by_event: dict | None = None,
                              parties_by_event: dict | None = None) -> discord.Embed:
        now = datetime.now(timezone.utc)
        counts_by_event  = counts_by_event or {}
        parties_by_event = parties_by_event or {}
        embed = discord.Embed(
            title="⚔️ 𝐌𝐀𝐒𝐒 𝐈𝐍𝐅𝐎",
            color=EMBED_INFO,
        )

        if not pending:
            embed.description = "*...*"
            return embed

        lines = []
        scheduled_count = 0
        prev_dt = None
        for ev in pending[:25]:
            emoji, time_str = _cta_status_time(ev, now)
            if emoji == "🗓️":
                scheduled_count += 1

            # Gap de 2h+ entre CTAs vizinhos → linha "…" separando os blocos.
            dt = self._started_dt(ev)
            if prev_dt and dt and abs((prev_dt - dt).total_seconds()) >= TIME_GAP_BREAK_S:
                lines.append("**…**")
            prev_dt = dt or prev_dt

            comp = ev.get('comp') or "—"
            msg  = (ev.get('cta_message') or "").strip().upper()

            # O horário UTC vira LINK da planilha do CTA (se já houver planilha).
            sheet_url = ev.get('sheet_url') or None
            time_disp = f"[{time_str}]({sheet_url})" if sheet_url else time_str

            # 🗓️/🟢 horário(link) · Nº presentes · comp · MENSAGEM (em capslock)
            line = f"{emoji} **{time_disp}**"
            n = counts_by_event.get(ev['id'], 0)
            if n:
                line += f" · `{n}`"
            line += f" · {comp}"
            if msg:
                line += f" · **{msg}**"
            lines.append(line)

        running_count = len(pending) - scheduled_count
        parts = []
        if running_count:
            parts.append(f"**{running_count}** em andamento")
        if scheduled_count:
            parts.append(f"**{scheduled_count}** agendada(s)")
        resumo = " · ".join(parts) if parts else f"**{len(pending)}** CTA(s)"

        desc = f"{resumo}\n\n" + "\n".join(lines)

        # Zona inferior: parties do(s) CTA(s) na janela da lupa (T-10min até callout).
        # Some quando não há nenhum. Em andamento = "MASSANDO AGORA"; pré-início (até
        # 10 min) = "MASSANDO EM BREVE".
        zone: list[str] = []
        for ev in pending[:25]:
            parties = parties_by_event.get(ev['id'])
            if not parties:
                continue
            comp = ev.get('comp') or "—"
            emoji, _ = _cta_status_time(ev, now)
            header = "MASSANDO AGORA" if emoji == "🟢" else "MASSANDO EM BREVE"
            zone.append(f"**{emoji} {header} · {comp}**")
            zone.extend(_zone_blocks(parties))
        if zone:
            desc += "\n\n" + "\n".join(zone)
            embed.set_footer(text="🔍 não sabe sua pt? clique na lupa")

        embed.description = desc
        return embed


async def setup(bot: commands.Bot):
    await bot.add_cog(MassInfoCog(bot))
