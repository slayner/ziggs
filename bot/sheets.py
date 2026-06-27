"""
Ponte com a Google Sheet via Apps Script web app (Ponto 4).

O webhook (URL /exec do Apps Script) e o secret são POR SERVIDOR — ficam no
economy_config de cada guild (configurados via /setsheets). Cada chamada faz um
POST JSON pro webhook do SERVIDOR ATUAL e espera uma resposta JSON no formato:

    { "ok": true,  ...dados... }
    { "ok": false, "error": "mensagem" }

Ações suportadas (ver sheets_appscript.gs):
  · list_comps      -> { ok, comps: [str, ...] }
  · create_cta_page -> { ok, page_name: str }
  · list_roles      -> { ok, roles: [str, ...] }
  · submit_functions-> { ok }

Tudo é best-effort: em falha de rede/parse, retornamos None / [] e logamos.
O código do bot NUNCA deve quebrar por causa da planilha.
"""
import json
import asyncio

import aiohttp

from database import load_economy_config, get_current_guild

# Sheets é POR SERVIDOR: a URL /exec do Apps Script e o secret ficam no
# economy_config de cada guild (isolado). Cada servidor publica o próprio Web App
# (cópia da planilha-modelo) e configura com /setsheets. As funções abaixo leem a
# config do SERVIDOR ATUAL (resolvido pelo ContextVar do database).
_TIMEOUT = aiohttp.ClientTimeout(total=60)


async def _get_cfg() -> tuple:
    """(webhook_url, secret) do servidor atual. ('', '') se não configurado/sem contexto."""
    if get_current_guild() is None:
        # Sem servidor no contexto: não dá pra saber QUAL planilha usar. Não deveria
        # ocorrer dentro de comando/interação (o contexto é setado no ponto de entrada).
        print("✗ Sheets: chamada sem servidor no contexto — não dá pra resolver a planilha.")
        return '', ''
    try:
        cfg = await load_economy_config()
    except Exception as e:
        print(f"✗ Sheets: falha ao ler a config do servidor ({type(e).__name__}: {e})")
        return '', ''
    return ((cfg.get('sheets_webhook_url') or '').strip(),
            (cfg.get('sheets_secret') or '').strip())


async def is_configured() -> bool:
    """True se o SERVIDOR atual tem um webhook de Sheets configurado."""
    url, _ = await _get_cfg()
    return bool(url)


async def _call(action: str, payload: dict) -> dict | None:
    """
    Faz um POST {action, secret, **payload} e devolve o dict JSON da resposta,
    ou None em caso de falha. Apps Script costuma redirecionar (302) para
    googleusercontent — o aiohttp segue redirects por padrão.
    """
    url, secret = await _get_cfg()
    if not url:
        return None

    body = {"action": action}
    if secret:
        body["secret"] = secret
    body.update(payload)

    # 1 tentativa + 1 retry: web app do Apps Script costuma estar "frio" (cold-start)
    # e estourar o timeout na 1ª chamada; na 2ª já está quente e responde rápido.
    text = None
    status = None
    for attempt in range(2):
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.post(url, json=body) as resp:
                    status = resp.status
                    text = await resp.text()
            break  # requisição concluída
        except asyncio.TimeoutError:
            if attempt == 0:
                continue  # re-tenta uma vez (cold-start)
            print(f"✗ Sheets[{action}] TIMEOUT (>{int(_TIMEOUT.total)}s, 2 tentativas) — "
                  f"o Apps Script não respondeu. Causas comuns: web app lento/cold-start, "
                  f"URL /exec errada no .env, ou o script não foi (re)publicado como 'qualquer um'.")
            return None
        except Exception as e:
            # Mostra o TIPO da exceção (antes o log saía vazio em timeouts).
            print(f"✗ Sheets[{action}] exceção: {type(e).__name__}: {e}")
            return None

    if text is None:
        return None
    if status and status >= 400:
        print(f"✗ Sheets[{action}] HTTP {status}: {text[:200]}")
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        print(f"✗ Sheets[{action}] resposta não-JSON: {text[:200]}")
        return None
    if not isinstance(data, dict):
        print(f"✗ Sheets[{action}] resposta inesperada: {text[:200]}")
        return None
    if not data.get("ok", False):
        err = data.get("error")
        # Resposta {"status":"ok"} (sem "ok"/"error") = Apps Script ANTIGO
        # (append do mass-info) ainda deployado. Precisa republicar o .gs novo.
        if err is None and "ok" not in data:
            print(f"✗ Sheets[{action}] o Apps Script deployado parece ser o "
                  f"ANTIGO (resposta {text[:120]}). Republique sheets_appscript.gs "
                  f"e use a URL /exec atualizada no .env.")
        else:
            print(f"✗ Sheets[{action}] erro: {err}")
    return data


async def list_comps() -> list[str]:
    """Nomes das comps (coluna A da página COMPS). [] em falha."""
    url, _ = await _get_cfg()
    if not url:
        print("⚠️ Sheets[list_comps] webhook não configurado neste servidor — "
              "use /setup → Planilha. Autocomplete da comp fica vazio.")
        return []
    data = await _call("list_comps", {})
    if not data or not data.get("ok"):
        # _call já logou o motivo (HTTP/JSON/erro). Reforço pro caso da comp.
        print(f"⚠️ Sheets[list_comps] sem resposta ok — autocomplete da comp vazio.")
        return []
    comps = data.get("comps") or []
    out = [str(c).strip() for c in comps if str(c).strip()]
    if not out:
        print(f"⚠️ Sheets[list_comps] respondeu ok mas SEM comps (confira a coluna A "
              f"da página COMPS e COMPS_HEADER_ROWS no .gs). Resposta: {str(data)[:200]}")
    return out


async def create_cta_page(comp: str, page_name: str, event_id: int) -> dict | None:
    """
    Copia a página-modelo da comp (coluna B do COMPS) e renomeia para `page_name`
    (o Apps Script garante unicidade). Retorna um dict
    `{"page_name": str, "sheet_url": str|None}` ou None em caso de falha.

    O `sheet_url` é um deep link direto para a aba criada (#gid=...); o `.gs`
    antigo (sem esse campo) devolve sheet_url=None — o bot continua funcionando.
    """
    data = await _call("create_cta_page", {
        "comp": comp,
        "page_name": page_name,
        "event_id": event_id,
    })
    if not data or not data.get("ok"):
        return None
    name = data.get("page_name")
    if not name:
        return None
    url = data.get("sheet_url")
    return {
        "page_name": str(name),
        "sheet_url": str(url) if url else None,
    }


async def list_roles(comp: str) -> list[str]:
    """Roles da comp (coluna A da página de roles, indicada na coluna C do COMPS)."""
    data = await _call("list_roles", {"comp": comp})
    if not data or not data.get("ok"):
        return []
    roles = data.get("roles") or []
    return [str(r).strip() for r in roles if str(r).strip()]


async def submit_functions(page_name: str, discord_name: str, roles: list[str],
                           event_id: int, caller_name: str = "",
                           prev_name: str = "") -> int | None:
    """
    Registra (upsert) o jogador na lista persistente T:W da página do CTA: grava
    nome + até 3 roles no registro-mestre, reativa (tira o 'cancelado') e tira da
    escalação (re-registrar volta a esperar). `prev_name` (nick antigo) é removido
    do mestre se mudou. Retorna a linha (compat; hoje o .gs devolve 0) ou None.
    """
    padded = (list(roles) + ["", "", ""])[:3]
    data = await _call("submit_functions", {
        "page_name":   page_name,
        "discord_name": discord_name,
        "role1": padded[0],
        "role2": padded[1],
        "role3": padded[2],
        "event_id": event_id,
        "caller_name": caller_name,
        "prev_name": prev_name or "",
    })
    if not (data and data.get("ok")):
        return None
    row = data.get("row")
    try:
        return int(row)
    except (TypeError, ValueError):
        return 0


async def cancel_functions(page_name: str, discord_name: str) -> bool:
    """
    Cancela o ping de um jogador: marca CANCELADO no registro-mestre (sticky),
    tira o nome da escalação e renderiza — o nome fica CINZA no rodapé de T:W e o
    autofill deixa de escalá-lo. Best-effort: True se a planilha confirmou.
    """
    page_name = (page_name or "").strip()
    name = (discord_name or "").strip()
    if not page_name or not name:
        return False
    data = await _call("cancel_functions", {
        "page_name": page_name,
        "discord_name": name,
    })
    return bool(data and data.get("ok"))


async def get_assignments(page_name: str) -> list[dict]:
    """
    Lê a escalação (A3:S) de uma página de CTA. Retorna TODOS os slots com role
    definida — inclusive os VAGOS (name='') — pra o bot conhecer as vagas abertas
    da comp. [] em falha/sem dados. Quem só quer os escalados deve filtrar por
    name não-vazio (um .gs antigo deployado não devolve os slots vagos).
    Cada item: {row, party, leader, name, role, weapon, offhand, helmet, armor,
                boots, cape, food, abilities, style, obs}. Cada peça de equipamento
                vem como "tier nome" (ex.: "8.4 Bruxa").
    """
    page_name = (page_name or "").strip()
    if not page_name:
        return []
    data = await _call("get_assignments", {"page_name": page_name})
    if not data or not data.get("ok"):
        return []
    return data.get("assignments") or []


async def delete_cta_page(page_name: str) -> bool:
    """
    Apaga a página (aba) de um CTA na planilha. Best-effort e idempotente:
    o Apps Script devolve ok=True mesmo se a aba já não existir. Retorna True
    se a planilha confirmou (ok), False em falha de rede/config.
    """
    page_name = (page_name or "").strip()
    if not page_name:
        return False
    data = await _call("delete_cta_page", {"page_name": page_name})
    return bool(data and data.get("ok"))


async def remove_functions(page_name: str, discord_name: str,
                           sheet_row: int = 0) -> bool:
    """
    Remove o registro de um jogador da página do CTA antes de re-registrar.
    O Apps Script procura o NOME EXATO na página inteira (porque o nome às vezes
    é movido manualmente) e limpa essas células; além disso limpa as roles em
    M/N/O na `sheet_row` original (as roles não se movem com o nome).
    Best-effort: retorna True se a planilha confirmou.
    """
    data = await _call("remove_functions", {
        "page_name": page_name,
        "discord_name": discord_name,
        "sheet_row": int(sheet_row or 0),
    })
    return bool(data and data.get("ok"))
