"""
Loot-log — Fase 1: coleta cega de logs (.txt/.csv) por CTA.

Cada logger envia o arquivo do lootlogger (ao-loot-logger exporta loot-events-*.txt,
conteúdo CSV com ';') pelo BOTÃO "📤 Enviar log" (modal de upload),
dentro da thread de logger do CTA (a thread identifica o evento). O canal de logger
fica TRANCADO (sem "Enviar mensagens"): o único jeito de mandar o log é pelo botão —
o que impede vazar o log digitando no chat. O envio é privado (resposta ephemeral):
o logger não vê os envios dos outros. O bot valida o formato, registra a submissão
(1 por pessoa, reenvio sobrescreve) e arquiva o cru no canal SÓ-staff (`channel_logreview`).

Ainda NÃO faz reconciliação/peso/pagamento — isso é Fase 2/3. Aqui só garante
cegueira + separação staff/logger e centraliza os arquivos para a staff.
"""
import io
import os
import re
import asyncio
import hashlib
import traceback
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands, tasks
from discord import app_commands, ui
from dotenv import load_dotenv

from utils import EMBED_INFO, send_err, send_ok, send_warn
import utils

import database
from database import (
    get_activated_guild_ids,
    get_event_by_logger_thread, get_event_by_id, get_registration,
    load_economy_config, add_log_submission, update_event_meta,
    replace_log_events, get_log_events, get_log_submissions,
    get_due_logger_thread_deletions,
)
from cogs.economy import has_configured_role

load_dotenv()
OWNER_ID = int(os.getenv('OWNER_ID', 0))

# Colunas essenciais que o .csv do lootlogger precisa ter (tolera extras/ordem).
REQUIRED_COLS = {
    'timestamp_utc', 'looted_by__guild', 'looted_by__name',
    'item_id', 'quantity', 'looted_from__name',
}
MAX_FILE_BYTES = 15 * 1024 * 1024   # 15 MB

# Janela do CTA pra considerar coletas: começo - LEAD até fim + GRACE.
WINDOW_LEAD_MIN  = 5    # loot logo antes do timer
WINDOW_GRACE_MIN = 15   # loot continua após o fim da luta
DEDUP_WINDOW_S   = 60.0  # mesma coleta vista por loggers diferentes em até ~1 min
                         # (o lootlogger gera timestamps defasados entre máquinas;
                         #  janela curta marcava como 'fonte única' e não corroborava)

# Cabeçalho do ao-loot-logger (ordem fixa) — o canônico exportado segue isto.
CANONICAL_CSV_COLS = (
    'timestamp_utc', 'looted_by__alliance', 'looted_by__guild', 'looted_by__name',
    'item_id', 'item_name', 'quantity',
    'looted_from__alliance', 'looted_from__guild', 'looted_from__name',
)

_TS_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$'
)


def _parse_header_cols(text: str) -> set[str]:
    """Pega a 1ª linha não vazia e devolve o conjunto de colunas (delimitador ;)."""
    for line in text.splitlines():
        line = line.strip()
        if line:
            return {c.strip() for c in line.split(';')}
    return set()


def _count_rows(text: str) -> int:
    """Conta linhas de dados (ignora cabeçalho e linhas vazias)."""
    lines = [l for l in text.splitlines() if l.strip()]
    return max(0, len(lines) - 1)


def _parse_ts(s: str):
    """
    '2026-05-29T02:22:00.6940188Z' (UTC, frac até 7 dígitos) -> datetime aware.
    Trunca a fração a 6 dígitos (limite do datetime) e normaliza Z/offset.
    """
    if not s:
        return None
    s = s.strip()
    m = _TS_RE.match(s)
    if m:
        base, frac, tz = m.group(1), m.group(2), m.group(3)
        iso = base.replace(' ', 'T')
        if frac:
            iso += '.' + frac[:6]
        if not tz or tz == 'Z':
            iso += '+00:00'
        elif len(tz) == 5:           # +0000 -> +00:00
            iso += tz[:3] + ':' + tz[3:]
        else:
            iso += tz
    else:
        iso = s.replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(iso)
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _format_ts_lootlogger(dt: datetime) -> str:
    """Formata timestamp no estilo do ao-loot-logger (UTC com sufixo Z)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    base = dt.strftime('%Y-%m-%dT%H:%M:%S')
    if dt.microsecond:
        frac = f'.{dt.microsecond:06f}'.rstrip('0').rstrip('.')
        if frac != '.':
            base += frac
    return base + 'Z'


def _window(event: dict):
    """(início - LEAD, fim + GRACE) do CTA, como datetimes aware. (None,None) se falhar."""
    start = _parse_ts(event.get('started_at'))
    if start is None:
        return None, None
    end = _parse_ts(event.get('ended_at')) if event.get('ended_at') else None
    if end is None:
        end = datetime.now(timezone.utc)
    return (start - timedelta(minutes=WINDOW_LEAD_MIN),
            end + timedelta(minutes=WINDOW_GRACE_MIN))


def _parse_loot_rows(text: str, win_start, win_end) -> list:
    """
    Extrai as linhas de COLETA (ignora linhas de morte) dentro da janela do CTA.
    Retorna dicts {ts(iso), item_id, item_name, quantity, looted_by,
    looted_by_guild, looted_by_alliance, looted_from, looted_from_guild,
    looted_from_alliance}.
    """
    header = None
    idx = {}
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        cols = [c.strip() for c in line.split(';')]
        if header is None:
            header = cols
            idx = {name: i for i, name in enumerate(cols)}
            continue

        def get(name):
            i = idx.get(name)
            return cols[i] if (i is not None and i < len(cols)) else ''

        item_id   = get('item_id')
        looted_by = get('looted_by__name')
        if not item_id or not looted_by:
            continue  # linha de morte / sem coleta
        ts = _parse_ts(get('timestamp_utc'))
        if ts is None:
            continue
        if (win_start and ts < win_start) or (win_end and ts > win_end):
            continue
        try:
            qty = int(get('quantity') or 1)
        except ValueError:
            qty = 1
        rows.append({
            'ts': ts.isoformat(),
            'item_id': item_id,
            'item_name': get('item_name'),
            'quantity': qty,
            'looted_by': looted_by,
            'looted_by_guild': get('looted_by__guild'),
            'looted_by_alliance': get('looted_by__alliance'),
            'looted_from': get('looted_from__name'),
            'looted_from_guild': get('looted_from__guild'),
            'looted_from_alliance': get('looted_from__alliance'),
        })
    return rows


def _mk_canon(cluster: list) -> dict:
    """Cria um evento canônico a partir de um cluster de detecções (mesma coleta)."""
    witness_ids = {c[1].get('submitter_id') for c in cluster}
    dt0, e0 = cluster[0]
    return {
        'ts': dt0,
        'item_id': e0.get('item_id'),
        'item_name': e0.get('item_name'),
        'quantity': int(e0.get('quantity') or 1),
        'looted_by': e0.get('looted_by'),
        'looted_by_guild': e0.get('looted_by_guild'),
        'looted_by_alliance': e0.get('looted_by_alliance'),
        'looted_from': e0.get('looted_from'),
        'looted_from_guild': e0.get('looted_from_guild'),
        'looted_from_alliance': e0.get('looted_from_alliance'),
        'witness_ids': witness_ids,
        'witnesses': len(witness_ids),
    }


def _reconcile(events: list, window_s: float = DEDUP_WINDOW_S) -> list:
    """
    Funde detecções de vários submissores em eventos canônicos.
    Chave: (item_id, quantity, looted_by, looted_from) + timestamps em até `window_s`.
    `witnesses` = nº de submissores DISTINTOS que viram (1 = fonte única).
    """
    parsed = []
    for e in events:
        dt = _parse_ts(e.get('ts'))
        if dt:
            parsed.append((dt, e))

    groups = defaultdict(list)
    for dt, e in parsed:
        key = (e.get('item_id'), int(e.get('quantity') or 1),
               e.get('looted_by'), e.get('looted_from'))
        groups[key].append((dt, e))

    canonical = []
    for _key, lst in groups.items():
        lst.sort(key=lambda x: x[0])
        cluster = []
        for dt, e in lst:
            if cluster and (dt - cluster[-1][0]).total_seconds() > window_s:
                canonical.append(_mk_canon(cluster))
                cluster = []
            cluster.append((dt, e))
        if cluster:
            canonical.append(_mk_canon(cluster))
    return canonical


COPY_OVERLAP_THRESHOLD = 0.85   # >=85% de timestamps idênticos = cópia
COPY_MIN_EVENTS        = 5      # ignora logs minúsculos (pouca evidência)


def _detect_copies(events: list, subs: list):
    """
    Detecta submissões COPIADAS: capturas independentes nunca têm timestamps
    idênticos ao nanossegundo. Sinaliza por mesmo `file_hash` OU alta sobreposição
    de tuplas exatas (ts, item, looter, vítima). Mantém o submissor MAIS ANTIGO e
    exclui os posteriores. Retorna (excluded_ids:set, notes:list[(drop, keep, motivo)]).
    """
    # ordem de envio (mais antigo primeiro) decide quem fica
    order = {}
    for i, s in enumerate(sorted(subs, key=lambda x: (x.get('submitted_at') or ''))):
        order[s['submitter_id']] = i

    def keeper(a, b):
        return a if order.get(a, 1 << 30) <= order.get(b, 1 << 30) else b

    by_sub = defaultdict(set)
    for e in events:
        by_sub[e['submitter_id']].add(
            (e.get('ts'), e.get('item_id'), e.get('looted_by'), e.get('looted_from'))
        )
    hash_by_sub = {s['submitter_id']: s.get('file_hash') for s in subs}

    ids = list(by_sub.keys())
    excluded = set()
    notes = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            ta, tb = by_sub[a], by_sub[b]
            if not ta or not tb:
                continue
            ha, hb = hash_by_sub.get(a), hash_by_sub.get(b)
            same_hash = bool(ha) and ha == hb
            ratio = len(ta & tb) / min(len(ta), len(tb))
            if same_hash or (min(len(ta), len(tb)) >= COPY_MIN_EVENTS
                             and ratio >= COPY_OVERLAP_THRESHOLD):
                keep = keeper(a, b)
                drop = b if keep == a else a
                if drop not in excluded:
                    excluded.add(drop)
                    motivo = "arquivo idêntico" if same_hash else f"{ratio:.0%} de timestamps iguais"
                    notes.append((drop, keep, motivo))
    return excluded, notes


def _build_canonical_csv(canonical: list, event_id: int) -> discord.File:
    """Gera o .csv canônico (1 linha por coleta única) no formato do ao-loot-logger."""
    lines = [";".join(CANONICAL_CSV_COLS)]
    for c in sorted(canonical, key=lambda x: ((x['looted_by'] or '').lower(), x['ts'])):
        ts = c['ts'] if isinstance(c['ts'], datetime) else _parse_ts(c.get('ts'))
        lines.append(";".join([
            _format_ts_lootlogger(ts) if ts else '',
            c.get('looted_by_alliance') or '',
            c.get('looted_by_guild') or '',
            c.get('looted_by') or '',
            c.get('item_id') or '',
            c.get('item_name') or '',
            str(c.get('quantity') or 1),
            c.get('looted_from_alliance') or '',
            c.get('looted_from_guild') or '',
            c.get('looted_from') or '',
        ]))
    data = io.BytesIO("\n".join(lines).encode('utf-8'))
    return discord.File(data, filename=f"canonico_cta_{event_id}.csv")


# ==================================================================
# Envio de log via BOTÃO + modal de UPLOAD (canal de logger TRANCADO).
# O canal nega "Enviar mensagens", então ninguém digita; o ÚNICO jeito de mandar
# o .csv é por aqui (interação privada). Botão e modal NÃO precisam de permissão
# de enviar mensagem — funcionam mesmo com o chat trancado.
# ==================================================================
class EnviarLogModal(ui.Modal, title="Enviar log do CTA"):
    def __init__(self):
        super().__init__()
        self.arquivo = ui.FileUpload(custom_id="log_csv", min_values=1, max_values=1, required=True)
        # ATENÇÃO: o `text` do Label tem limite de 45 caracteres no Discord
        # (erro 50035 se passar) — mantenha curto; o detalhe vai na description.
        self.add_item(ui.Label(
            text="Arquivo do lootlogger (.txt ou .csv)",
            description="Anexe o arquivo exportado pelo lootlogger deste CTA (sem editar).",
            component=self.arquivo,
        ))

    async def on_submit(self, interaction: discord.Interaction):
        files = list(self.arquivo.values or [])
        if not files:
            await send_err(interaction, "Nenhum arquivo enviado.")
            return
        cog = interaction.client.cogs.get('LootLogCog')
        if not cog:
            await send_err(interaction, "Sistema de logs indisponível.")
            return
        await cog._process_log_submission(interaction, files[0])

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        """Sem isto, o discord.py só LOGA a exceção e o usuário não vê nada
        ('botão não funciona, nenhum erro aparece'). Aqui mostramos o erro pro
        usuário E imprimimos o traceback no console pra diagnosticar."""
        traceback.print_exc()
        print(f"✗ Loot-log: erro processando envio: {type(error).__name__}: {error}")
        msg = ("❌ Deu erro ao processar seu log. Tente reenviar; se persistir, "
               "avise a staff (o erro foi registrado no console do bot).")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
                try:
                    await utils.schedule_ephemeral_from_ctx(interaction)
                except Exception:
                    pass
            else:
                await interaction.response.send_message(msg, ephemeral=True)
                try:
                    await utils.schedule_ephemeral_from_ctx(interaction)
                except Exception:
                    pass
        except discord.HTTPException:
            pass


class EnviarLogView(ui.View):
    """View persistente (sobrevive a restart) com o botão de envio de log."""
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="📤 Enviar log", style=discord.ButtonStyle.primary,
               custom_id="lootlog:enviar_v1")
    async def enviar(self, interaction: discord.Interaction, _btn: ui.Button):
        event = await get_event_by_logger_thread(interaction.channel_id)
        if not event:
            await send_err(interaction, "Use este botão **dentro da thread de logger do CTA**.")
            return
        if event.get('split_finalized'):
            await send_err(interaction, f"O split do CTA #{event['id']} já foi finalizado — "
                                        f"envios encerrados.")
            return
        try:
            await interaction.response.send_modal(EnviarLogModal())
        except Exception as e:
            traceback.print_exc()
            print(f"✗ Loot-log: erro abrindo o modal de envio: {type(e).__name__}: {e}")
            await send_err(interaction, "Não consegui abrir o formulário de envio. "
                                        "Avise a staff (erro registrado no console).")


class LootLogCog(commands.Cog, name="LootLogCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        print("✓ LootLog Cog carregada")
        self.bot.add_view(EnviarLogView())      # botão de envio sobrevive a restart
        self.public_thread_cleanup_loop.start()

    def cog_unload(self):
        self.public_thread_cleanup_loop.cancel()

    # ------------------------------------------------------------------
    # Rede de segurança: nada além de interações nas threads de logger.
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Apaga na hora qualquer mensagem (não-bot) nas THREADS do canal de logger.
        O canal em si fica livre — é onde moram as instruções de uso fixadas pela
        staff. As threads ficam TRANCADAS por permissão (ninguém digita), então isto
        só age sobre quem fura o bloqueio (ex.: Administrador). Defesa em
        profundidade — a prevenção real é a permissão negada de 'Enviar mensagens'."""
        if message.author.bot or message.guild is None:
            return
        try:
            with database.using_guild(message.guild.id):
                cfg = await load_economy_config()
        except Exception:
            return
        logger_chan_id = cfg.get('channel_logger')
        if not logger_chan_id:
            return
        ch = message.channel
        if getattr(ch, 'parent_id', None) == logger_chan_id:
            try:
                await message.delete()
            except discord.HTTPException:
                pass

    # ------------------------------------------------------------------
    # Limpeza: apaga a thread PÚBLICA de logger 30 min após o evento.
    # ------------------------------------------------------------------
    @tasks.loop(seconds=60)
    async def public_thread_cleanup_loop(self):
        for gid in await get_activated_guild_ids():
            with database.using_guild(gid):
                try:
                    await self._public_thread_cleanup_once()
                except Exception as e:
                    print(f"✗ public_thread_cleanup_loop [{gid}]: {e}")

    async def _public_thread_cleanup_once(self):
        from datetime import datetime as _dt, timezone as _tz
        now_iso = _dt.now(_tz.utc).isoformat()
        try:
            due = await get_due_logger_thread_deletions(now_iso)
        except Exception as e:
            print(f"✗ Erro consultando threads públicas a apagar: {e}")
            return
        for ev in due:
            await self._delete_public_thread(ev.get('logger_thread_id'))
            await update_event_meta(ev['id'], logger_thread_id=None,
                                    logger_thread_delete_at=None)

    @public_thread_cleanup_loop.before_loop
    async def _before_cleanup(self):
        await self.bot.wait_until_ready()

    async def _delete_public_thread(self, tid):
        if not tid:
            return
        ch = self.bot.get_channel(tid)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(tid)
            except discord.HTTPException:
                return
        # Captura o canal-pai e o nome ANTES de apagar (depois somem).
        parent = getattr(ch, 'parent', None)
        thread_name = getattr(ch, 'name', None)
        try:
            await ch.delete()
            print(f"✓ Thread pública de logger {tid} apagada (30 min).")
        except discord.HTTPException as e:
            print(f"✗ Erro apagando thread pública {tid}: {e}")
            return
        # Apaga também a mensagem de sistema "iniciou uma thread" no canal de logger.
        await self._delete_thread_system_msg(parent, thread_name)

    async def _delete_thread_system_msg(self, parent, thread_name):
        """Remove o aviso de sistema 'fulano iniciou uma thread' no canal pai (o
        conteúdo dessa mensagem é o NOME da thread)."""
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

    async def _process_log_submission(self, interaction: discord.Interaction,
                                      arquivo: discord.Attachment):
        """Valida + registra um .csv de log. Chamado pelo modal do botão '📤 Enviar log'.
        Funciona com o canal de logger TRANCADO (é interação, não mensagem)."""
        # Ler o anexo pode passar de 3s → ack ephemeral já.
        await interaction.response.defer(ephemeral=True)

        # 1) Identifica o CTA pela thread de logger onde o comando foi rodado.
        event = await get_event_by_logger_thread(interaction.channel_id)
        if not event:
            await send_err(interaction, "Rode este comando **dentro da thread de logger do CTA** "
                                        "(a thread que o bot cria no canal de logger quando o CTA "
                                        "é encerrado).")
            return
        if event.get('split_finalized'):
            await send_err(interaction, f"O split do CTA #{event['id']} já foi finalizado — "
                                        f"envios encerrados.")
            return

        # 2) Valida o anexo (extensão + tamanho). O ao-loot-logger exporta
        # loot-events-*.txt (conteúdo CSV com ';'); aceita .txt e .csv.
        fname = arquivo.filename or "log.txt"
        if not fname.lower().endswith(('.csv', '.txt')):
            await send_err(interaction, "O arquivo precisa ser um **`.txt`** ou **`.csv`** "
                                        "(o que o lootlogger exporta).")
            return
        if arquivo.size and arquivo.size > MAX_FILE_BYTES:
            await send_err(interaction, "Arquivo grande demais (limite 15 MB). "
                                        "Confere se é o arquivo certo do lootlogger.")
            return

        # 3) Lê e decodifica.
        try:
            data = await arquivo.read()
        except Exception as e:
            await send_err(interaction, f"Não consegui ler o anexo: {e}")
            return
        try:
            text = data.decode('utf-8-sig')   # tira BOM se houver
        except UnicodeDecodeError:
            await send_err(interaction, "Não consegui decodificar o arquivo (esperado texto "
                                        "UTF-8). Reexporta o log pelo lootlogger e tenta de novo.")
            return

        # 4) Valida o cabeçalho (colunas essenciais presentes).
        cols = _parse_header_cols(text)
        faltando = REQUIRED_COLS - cols
        if faltando:
            await send_err(interaction, "Esse arquivo não parece um log válido do lootlogger.\n"
                                        f"Faltam as colunas: `{', '.join(sorted(faltando))}`.\n"
                                        "Envie o arquivo original exportado pelo programa, sem editar.")
            return

        rows = _count_rows(text)
        if rows <= 0:
            await send_err(interaction, "O log está vazio (só cabeçalho). "
                                        "Confere se capturou algo neste CTA.")
            return

        file_hash = hashlib.sha256(data).hexdigest()

        # 5) Nick cadastrado (Discord → IGN), se houver.
        reg = await get_registration(interaction.user.id)
        nick = reg.get('nick') if reg else None

        # 6) Registra a submissão (reenvio sobrescreve).
        was_update = await add_log_submission(
            event_id=event['id'],
            submitter_id=interaction.user.id,
            submitter_nick=nick,
            file_name=fname,
            file_hash=file_hash,
            row_count=rows,
        )

        # 6b) Normaliza as COLETAS dentro da janela do CTA p/ a reconciliação (Fase 2).
        loot_count = 0
        try:
            ws, we = _window(event)
            # Parse pesado (milhares de linhas) numa thread → não trava o event loop.
            loot_rows = await asyncio.to_thread(_parse_loot_rows, text, ws, we)
            loot_count = await replace_log_events(event['id'], interaction.user.id, loot_rows)
        except Exception as e:
            print(f"✗ Erro normalizando coletas do CTA #{event['id']}: {e}")

        # 7) Atualiza o canônico na thread PRIVADA (só-logística), criada no 1º envio.
        # O bot guarda os dados; a logística só vê a forma reconciliada, não os crus.
        published = None
        try:
            published = await self.refresh_canonical(event['id'])
        except Exception as e:
            print(f"✗ Erro atualizando canônico do CTA #{event['id']}: {e}")

        # 8) Confirma pro logger (ephemeral).
        msg = (f"✅ Log {'reenviado' if was_update else 'recebido'} para o "
               f"**CTA #{event['id']}** — `{rows}` linhas (`{loot_count}` coletas na "
               f"janela do CTA). Obrigado por rodar o logger!")
        if not nick:
            msg += ("\nℹ️ Você ainda não tem **cadastro** (nick do jogo). "
                    "Cadastre-se pra garantir sua recompensa de logger no futuro.")
        if published is None:
            msg += ("\n⚠️ Seus dados foram guardados, mas não consegui publicar pra "
                    "logística (o canal de logger está configurado no `/setup`?).")
        await interaction.followup.send(msg, ephemeral=True)
        print(f"✓ Loot-log: {interaction.user.display_name} enviou {rows} linhas "
              f"no CTA #{event['id']} (reenvio={was_update})")

    # ==================================================================
    # Reconciliação automática — mantém UMA mensagem canônica por CTA
    # ==================================================================
    @staticmethod
    def _analyze_sync(events: list, subs: list) -> dict:
        """Parte PESADA (CPU) da reconciliação — roda numa thread p/ não travar o loop."""
        excluded, copy_notes = _detect_copies(events, subs)
        clean = [e for e in events if e['submitter_id'] not in excluded]
        canonical = _reconcile(clean)
        valid_submitters = {s['submitter_id'] for s in subs if s['submitter_id'] not in excluded}
        single_logger = len(valid_submitters) == 1
        weights = defaultdict(int)
        for c in canonical:
            if single_logger:
                # Único logger: não dá corroborar — todas as coletas únicas contam.
                for sid in c['witness_ids']:
                    weights[sid] += 1
            elif len(c['witness_ids']) >= 2:
                for sid in c['witness_ids']:
                    weights[sid] += 1
        return {
            'subs': subs, 'canonical': canonical,
            'excluded': excluded, 'copy_notes': copy_notes, 'weights': dict(weights),
        }

    async def _analyze(self, event_id: int) -> dict:
        """
        Núcleo da reconciliação: detecta cópias, remove-as, reconcilia o resto e
        calcula o PESO de cada logger. A parte pesada vai pra uma thread (logs de
        milhares de linhas travariam o event loop e estouravam interações de 3s).
        """
        events = await get_log_events(event_id)
        subs   = await get_log_submissions(event_id)
        return await asyncio.to_thread(self._analyze_sync, events, subs)

    async def compute_logger_weights(self, event_id: int):
        """
        (weights, copy_notes) p/ o pagamento no _finalize_split.
        weights: {submitter_id: peso}; cópias já zeradas. Com 1 logger só, peso =
        nº de coletas únicas dele; com 2+, só coletas vistas por 2+ loggers.
        """
        a = await self._analyze(event_id)
        return a['weights'], a['copy_notes']

    def _canonical_embed(self, event_id, *, subs, actionable, single, looters,
                         guild, weights, copy_notes):
        embed = discord.Embed(
            title=f"🧮  Log canônico — CTA #{event_id}",
            color=EMBED_INFO,
        )
        embed.add_field(name="🧑‍💻 Loggers", value=f"`{len(subs)}`", inline=True)
        embed.add_field(
            name="📦 Coletas únicas",
            value=f"`{len(actionable)}`" + ("" if guild else " *(todas)*"),
            inline=True,
        )
        embed.add_field(name="👥 Membros", value=f"`{len(looters)}`", inline=True)

        # Peso dos loggers (prévia da fatia do logger_percent)
        nick_of = {s['submitter_id']: s.get('submitter_nick') for s in subs}
        total_w = sum(weights.values())
        if total_w > 0:
            lines = []
            for sid, w in sorted(weights.items(), key=lambda x: -x[1])[:12]:
                tag = f" `{nick_of.get(sid)}`" if nick_of.get(sid) else ""
                lines.append(f"<@{sid}>{tag} — `{w}` ({round(100 * w / total_w)}%)")
            extra = len(weights) - 12
            if extra > 0:
                lines.append(f"… e mais {extra}")
            embed.add_field(name="🏅 Peso dos loggers",
                            value="\n".join(lines), inline=False)
        else:
            empty = ("*Sem coletas na janela do CTA.*" if len(subs) == 1 else
                     "*Sem coletas corroboradas ainda (precisa de 2+ loggers "
                     "vendo a mesma coleta).*")
            embed.add_field(name="🏅 Peso dos loggers", value=empty, inline=False)

        if copy_notes:
            clines = [f"<@{drop}> ≈ cópia de <@{keep}> ({motivo})"
                      for drop, keep, motivo in copy_notes[:6]]
            if len(copy_notes) > 6:
                clines.append(f"… e mais {len(copy_notes) - 6}")
            embed.add_field(name="🚫 Cópias detectadas (peso zerado)",
                            value="\n".join(clines), inline=False)

        if single:
            embed.add_field(
                name="⚠️ Fonte única (revisar)",
                value=f"`{len(single)}` coleta(s) vista(s) por **1 logger só** "
                      f"(sem corroboração de outro logger).",
                inline=False,
            )
        if not guild:
            embed.add_field(
                name="❗ Guilda não definida",
                value="Use `/setup` → Guilda (jogo) pra filtrar só os itens da sua guilda.",
                inline=False,
            )
        if not actionable:
            embed.description = "*Ainda sem coletas reconciliadas — aguardando envios.*"
        embed.set_footer(text="Atualiza a cada /enviarlog · peso = coletas vistas por 2+ loggers.")
        return embed

    async def _ensure_private_thread(self, event: dict):
        """
        Garante a thread PRIVADA (só-logística) no canal de logger: cria no 1º envio
        e marca a role de logística (que dá acesso). Retorna a thread (ou None).
        """
        tid = event.get('logreview_thread_id')
        if tid:
            ch = self.bot.get_channel(tid)
            if ch is None:
                try:
                    ch = await self.bot.fetch_channel(tid)
                except discord.HTTPException:
                    ch = None
            if ch is not None:
                return ch

        cfg = await load_economy_config()
        parent_id = cfg.get('channel_logger')
        parent = self.bot.get_channel(parent_id) if parent_id else None
        if parent is None and parent_id:
            try:
                parent = await self.bot.fetch_channel(parent_id)
            except discord.HTTPException:
                parent = None
        if not isinstance(parent, discord.TextChannel):
            return None
        try:
            thread = await parent.create_thread(
                name=f"🔒 Análise CTA #{event['id']}",
                type=discord.ChannelType.private_thread,
                invitable=False,
            )
        except Exception as e:
            print(f"✗ Erro criando thread privada (CTA #{event['id']}): {e}")
            return None
        await update_event_meta(event['id'], logreview_thread_id=thread.id)
        # Acesso à análise: adiciona explicitamente os membros de logistic + council
        # + lead à thread privada (mais confiável que confiar na menção da role).
        guild = getattr(parent, 'guild', None)
        if guild:
            added_ids = set()
            for key in ('role_logistic', 'role_council', 'role_lead'):
                rid = cfg.get(key)
                role = guild.get_role(rid) if rid else None
                if not role:
                    continue
                for m in role.members:
                    if m.id in added_ids:
                        continue
                    added_ids.add(m.id)
                    try:
                        await thread.add_user(m)
                    except discord.HTTPException:
                        pass
        try:
            await thread.send(
                f"🔒 Análise dos logs do **CTA #{event['id']}** — só logistic/council/lead "
                f"veem aqui. O reconciliado aparece e se atualiza na mensagem abaixo.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as e:
            print(f"✗ Erro postando intro na thread privada: {e}")
        return thread

    async def refresh_canonical(self, event_id: int):
        """
        Recalcula o canônico e edita (ou cria) a ÚNICA mensagem na thread PRIVADA
        (só-logística), criando-a no 1º envio. Os logs crus ficam com o bot; a
        logística só vê o reconciliado. Retorna a thread (ou None).
        """
        event = await get_event_by_id(event_id)
        if not event:
            return None
        target = await self._ensure_private_thread(event)
        if target is None:
            return None

        a = await self._analyze(event_id)
        subs, canonical = a['subs'], a['canonical']
        copy_notes, weights = a['copy_notes'], a['weights']

        cfg = await load_economy_config()
        guild = (cfg.get('guild_ingame_name') or '').strip()
        # Pode haver VÁRIAS guildas separadas por ; — qualquer uma conta.
        guild_set = {g.strip().lower() for g in guild.split(';') if g.strip()}
        if guild_set:
            actionable = [c for c in canonical
                          if (c['looted_by_guild'] or '').strip().lower() in guild_set]
        else:
            actionable = canonical
        single  = [c for c in actionable if c['witnesses'] == 1]
        looters = {(c['looted_by'] or '').lower() for c in actionable if c['looted_by']}

        embed = self._canonical_embed(
            event_id, subs=subs, actionable=actionable, single=single,
            looters=looters, guild=guild, weights=weights, copy_notes=copy_notes,
        )
        csv_file = (await asyncio.to_thread(_build_canonical_csv, actionable, event_id)
                    if actionable else None)

        msg = None
        msg_id = event.get('logreview_msg_id')
        if msg_id:
            try:
                msg = await target.fetch_message(msg_id)
            except discord.HTTPException:
                msg = None
        try:
            if msg:
                await msg.edit(embed=embed, attachments=([csv_file] if csv_file else []))
            else:
                msg = (await target.send(embed=embed, file=csv_file) if csv_file
                       else await target.send(embed=embed))
                await update_event_meta(event_id, logreview_msg_id=msg.id)
        except discord.HTTPException as e:
            print(f"✗ Erro publicando canônico do CTA #{event_id}: {e}")
            return None
        return target

    # ==================================================================
    # /reconciliarlog  (staff) — força a atualização do canônico
    # ==================================================================
    @commands.hybrid_command(
        name="reconciliarlog",
        description="Força a atualização do log canônico de um CTA — council/logistic",
    )
    @app_commands.guild_only()
    @app_commands.describe(cta="ID do CTA (ex: 12)")
    async def reconciliarlog(self, ctx: commands.Context, cta: int):
        if ctx.author.id != OWNER_ID and not await has_configured_role(
                ctx.author, 'role_council', 'role_logistic'):
            await send_err(ctx, "Apenas council ou logistic podem reconciliar logs.")
            return
        await ctx.defer(ephemeral=True)

        event = await get_event_by_id(cta)
        if not event:
            await send_err(ctx, f"CTA #{cta} não existe.")
            return
        if not await get_log_events(cta):
            await send_err(ctx, f"Nenhuma coleta registrada pro CTA #{cta} ainda.")
            return

        target = await self.refresh_canonical(cta)
        if target is not None:
            await send_ok(ctx, f"Log canônico do CTA #{cta} atualizado em {target.mention}.")
        else:
            await send_warn(ctx, "Não consegui publicar — confira se o **canal de logger** "
                                 "está configurado no `/setup`.")


async def setup(bot: commands.Bot):
    await bot.add_cog(LootLogCog(bot))
