"""
Utilitários compartilhados entre as cogs.
"""
import re
from typing import Optional

import discord
import asyncio
from typing import Dict, Any
import time


_BOLD_DIGIT_BASE = 0x1D7CE   # 𝟎 (dígitos em negrito matemático: 𝟎-𝟗)


def bold_number(n) -> str:
    """Converte um número (ou string) para dígitos em NEGRITO matemático (𝟎-𝟗).
    Ex.: bold_number(6) → '𝟔'. Não-dígitos passam intactos."""
    return ''.join(chr(_BOLD_DIGIT_BASE + int(c)) if c.isdigit() else c for c in str(n))


def parse_silver(raw) -> Optional[int]:
    """
    Converte uma string de prata em inteiro.

    Aceita:
        Inteiro puro:         "1500000"           → 1500000
        USD (vírgula):        "2,298,291"         → 2298291
        BR/EU (ponto):        "2.192.281"         → 2192281
        Sufixos k/m/b:        "1.5m" "150k" "2b"  → 1500000, 150000, 2000000000
        Sufixos com vírgula:  "1,5m"              → 1500000

    Regras:
        - O resultado FINAL precisa ser inteiro. "1.5" sozinho é rejeitado.
        - Para separador de milhar, os grupos depois do separador devem ter 3 dígitos.
        - Espaços são ignorados.

    Retorna None se for inválido.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower().replace(' ', '')
    if not s:
        return None

    # Suffix form: 1.5m, 150k, 2b, 1,5m
    m = re.fullmatch(r'(\d+(?:[.,]\d+)?)([kmb])', s)
    if m:
        num_str = m.group(1).replace(',', '.')
        suffix = m.group(2)
        multipliers = {'k': 1_000, 'm': 1_000_000, 'b': 1_000_000_000}
        try:
            value = float(num_str) * multipliers[suffix]
        except ValueError:
            return None
        if value != int(value):     # precisa ser inteiro
            return None
        return int(value)

    # Separador de milhar (USD ou BR/EU): 2,298,291 | 2.192.281
    # Exige pelo menos 1 separador, todos os grupos depois com 3 dígitos.
    if re.fullmatch(r'\d{1,3}(?:[,.]\d{3})+', s):
        return int(s.replace(',', '').replace('.', ''))

    # Inteiro puro
    if re.fullmatch(r'\d+', s):
        return int(s)

    return None


def format_silver(value) -> str:
    """Formata um inteiro com separador de milhar (vírgula). Ex: 1500000 → '1,500,000'."""
    if value is None:
        return "0"
    return f"{int(value):,}"


# ======================================================================
# Upload de arquivos — o limite varia pelo nível de boost do servidor
# (25MB sem boost / nível 1 · 50MB nível 2 · 100MB nível 3). O bot não tem
# Nitro, então nunca passa do teto do servidor.
# ======================================================================
_DEFAULT_FILESIZE_LIMIT = 25 * 1024 * 1024   # fallback se não houver guild no contexto


def guild_filesize_limit(guild) -> int:
    """Limite de upload (em bytes) do servidor — já reflete o nível de boost.
    `guild.filesize_limit` existe em qualquer Guild do discord.py; cai pro default
    de 25MB se não houver guild (ex.: DM)."""
    return getattr(guild, 'filesize_limit', None) or _DEFAULT_FILESIZE_LIMIT


def human_size(num_bytes) -> str:
    """Formata bytes em MB legível. Ex: 31457280 → '30.0 MB'."""
    return f"{(num_bytes or 0) / (1024 * 1024):.1f} MB"


# ======================================================================
# Tema de embeds — UMA cara para o bot inteiro.
# Minimalista (decisão do projeto): a COR carrega o significado; títulos
# secos; SEM footer, SEM thumbnail, SEM author. Emojis só quando são DADO
# (ícones de status numa lista), nunca decoração de título.
# ======================================================================
EMBED_OK   = discord.Color.from_rgb(149, 255, 156)    # verde    — sucesso
EMBED_ERR  = discord.Color.from_rgb(255, 114, 114)    # vermelho — erro
EMBED_WARN = discord.Color.from_rgb(255, 251, 114)    # âmbar    — aviso
EMBED_INFO = discord.Color.from_rgb(13, 13, 13)    # neutro   — informação

_EMBED_COLORS = {
    'ok': EMBED_OK, 'success': EMBED_OK,
    'err': EMBED_ERR, 'error': EMBED_ERR,
    'warn': EMBED_WARN, 'warning': EMBED_WARN,
    'info': EMBED_INFO,
}

# Nas listas cronológicas (mass-info, late-attend, calendário de nodes):
# eventos separados por este gap ou mais ganham uma linha "…" entre eles.
TIME_GAP_BREAK_S = 2 * 3600


def make_embed(kind: str = 'info', *, title: str = None, desc: str = None,
               fields: list = None, image: str = None) -> discord.Embed:
    """Embed padrão do bot. `kind` ∈ {ok, err, warn, info} define só a cor.
    `fields` aceita itens (name, value) ou (name, value, inline).
    `image` (URL) vira a imagem grande do embed (set_image)."""
    embed = discord.Embed(color=_EMBED_COLORS.get(kind, EMBED_INFO))
    if title:
        embed.title = title
    if desc:
        embed.description = desc
    for f in (fields or []):
        name, value = f[0], f[1]
        inline = f[2] if len(f) > 2 else False
        embed.add_field(name=name, value=value, inline=inline)
    if image:
        embed.set_image(url=image)
    return embed


def ok_embed(desc: str = None, *, title: str = None, fields: list = None,
             image: str = None) -> discord.Embed:
    return make_embed('ok', title=title, desc=desc, fields=fields, image=image)


def err_embed(desc: str = None, *, title: str = None, fields: list = None,
              image: str = None) -> discord.Embed:
    return make_embed('err', title=title, desc=desc, fields=fields, image=image)


def warn_embed(desc: str = None, *, title: str = None, fields: list = None,
               image: str = None) -> discord.Embed:
    return make_embed('warn', title=title, desc=desc, fields=fields, image=image)


def info_embed(desc: str = None, *, title: str = None, fields: list = None,
               image: str = None) -> discord.Embed:
    return make_embed('info', title=title, desc=desc, fields=fields, image=image)


async def _send_embed(ctx, embed: discord.Embed, *, ephemeral: bool = True):
    """Envia o embed funcionando tanto para Context (hybrid) quanto Interaction.
    Em prefix-command o `ephemeral` é ignorado pelo discord.py (comportamento normal)."""
    # Envia a mensagem e, se for ephemeral em Interaction, agenda deleção.
    msg = None
    if isinstance(ctx, discord.Interaction):
        if ctx.response.is_done():
            msg = await ctx.followup.send(embed=embed, ephemeral=ephemeral)
        else:
            await ctx.response.send_message(embed=embed, ephemeral=ephemeral)
            try:
                msg = await ctx.original_response()
            except Exception:
                msg = None
    else:
        msg = await ctx.send(embed=embed, ephemeral=ephemeral)

    # Agendar deleção de ephemerals (por padrão 60s) — pode ser cancelado por
    # `mark_ephemeral_interacted(interaction)` quando houver interação na mensagem.
    if isinstance(ctx, discord.Interaction) and ephemeral and msg is not None:
        try:
            _schedule_ephemeral_deletion(msg, ctx, timeout=60)
        except Exception:
            pass

    return msg


async def send_ok(ctx, desc: str = None, *, title: str = None,
                  ephemeral: bool = True, fields: list = None, image: str = None):
    return await _send_embed(ctx, ok_embed(desc, title=title, fields=fields, image=image),
                             ephemeral=ephemeral)


async def send_err(ctx, desc: str = None, *, title: str = None,
                   ephemeral: bool = True, fields: list = None, image: str = None):
    return await _send_embed(ctx, err_embed(desc, title=title, fields=fields, image=image),
                             ephemeral=ephemeral)


async def send_warn(ctx, desc: str = None, *, title: str = None,
                    ephemeral: bool = True, fields: list = None, image: str = None):
    return await _send_embed(ctx, warn_embed(desc, title=title, fields=fields, image=image),
                             ephemeral=ephemeral)


async def send_info(ctx, desc: str = None, *, title: str = None,
                    ephemeral: bool = True, fields: list = None, image: str = None):
    return await _send_embed(ctx, info_embed(desc, title=title, fields=fields, image=image),
                              ephemeral=ephemeral)


# ---------------- Ephemeral helpers ----------------
# Map key -> asyncio.Task. Key is str(message.id) when available, else 'u:<user_id>:<ts>'
_EPHEMERAL_DELETE_TASKS: Dict[str, asyncio.Task] = {}
# Map key -> owner user id (int)
_EPHEMERAL_OWNERS: Dict[str, int] = {}


def _is_ephemeral_message(msg: discord.Message) -> bool:
    flags = getattr(msg, 'flags', None)
    return bool(getattr(flags, 'ephemeral', False))


def _schedule_ephemeral_deletion(msg: discord.Message, interaction: discord.Interaction, *, timeout: int = 60):
    """Agenda a deleção do ephemeral `msg` após `timeout` segundos.
    Para cancelar (quando houver interação na componente/modal), chame
    `mark_ephemeral_interacted(interaction)` a partir do callback da interação.
    """
    if not _is_ephemeral_message(msg):
        return

    mid = getattr(msg, 'id', None)
    owner = getattr(interaction, 'user', None)
    owner_id = owner.id if owner else None
    if mid is None and owner_id is None:
        return

    gen_key = None

    async def _task():
        try:
            await asyncio.sleep(timeout)
            # Tenta deletar a resposta original da interação (ephemeral)
            try:
                await interaction.delete_original_response()
            except Exception:
                # fallback: tenta apagar a mensagem diretamente
                try:
                    await msg.delete()
                except Exception:
                    pass
        finally:
            # Remove pela chave usada
            key = str(mid) if mid is not None else gen_key
            _EPHEMERAL_DELETE_TASKS.pop(key, None)
            _EPHEMERAL_OWNERS.pop(key, None)

    # Escolhe chave: message id quando disponível, senão por user+ts
    if mid is not None:
        key = str(mid)
    else:
        gen_key = f"u:{owner_id}:{int(time.time() * 1000)}"
        key = gen_key

    # Cancela e substitui se já existe
    prev = _EPHEMERAL_DELETE_TASKS.get(key)
    if prev and not prev.done():
        prev.cancel()
    task = asyncio.create_task(_task())
    _EPHEMERAL_DELETE_TASKS[key] = task
    if owner_id is not None:
        _EPHEMERAL_OWNERS[key] = owner_id


def mark_ephemeral_interacted(interaction: discord.Interaction):
    """Marca que o ephemeral foi interagido — cancela a deleção agendada.
    Deve ser chamado no início do callback das Views / componentes que respondem
    àquela mensagem (por exemplo, em button/select callbacks ou modals).
    """
    try:
        msg = getattr(interaction, 'message', None)
        mid = getattr(msg, 'id', None) if msg is not None else None
        # If message id present, cancel that specific task
        if mid is not None:
            key = str(mid)
            task = _EPHEMERAL_DELETE_TASKS.pop(key, None)
            _EPHEMERAL_OWNERS.pop(key, None)
            if task and not task.done():
                task.cancel()
                return

        # Otherwise (or additionally), cancel tasks owned by this user
        owner = getattr(interaction, 'user', None)
        owner_id = owner.id if owner else None
        if owner_id is None:
            return
        # Find all keys owned by this user
        keys = [k for k, uid in _EPHEMERAL_OWNERS.items() if uid == owner_id]
        for k in keys:
            task = _EPHEMERAL_DELETE_TASKS.pop(k, None)
            _EPHEMERAL_OWNERS.pop(k, None)
            if task and not task.done():
                task.cancel()
    except Exception:
        pass


async def schedule_ephemeral_from_ctx(ctx, *, timeout: int = 60):
    """Utility to be called after an ephemeral send to locate the message and
    schedule its deletion. Use this from cogs after sending ephemeral responses
    when not using the bot's `send_*` helpers.
    """
    try:
        if not isinstance(ctx, discord.Interaction):
            return
        # Prefer original_response(); fallback to followup messages.
        try:
            msg = await ctx.original_response()
        except Exception:
            # Some code calls followup.send which returns the message directly.
            msg = None
        if msg is None:
            return
        _schedule_ephemeral_deletion(msg, ctx, timeout=timeout)
    except Exception:
        pass
