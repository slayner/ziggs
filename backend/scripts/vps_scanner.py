#!/usr/bin/env python3
"""Ziggs VPS Scanner — feed polling distribuído.

Roda em VPS tunnel. Pede tasks ao backend (POST /scan/claim), busca páginas
do feed de batalhas ou kill events da API pública do Albion, e reporta os
dados crus (POST /scan/report). O backend faz upsert.

Stdlib only — sem dependências externas (urllib, json, threading).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event

HOSTS = {
    "americas": "gameinfo.albiononline.com",
    "europe":   "gameinfo-ams.albiononline.com",
    "asia":     "gameinfo-sgp.albiononline.com",
}

BACKEND_URL = os.getenv("SCAN_BACKEND_URL", "http://localhost:8000").rstrip("/")
SECRET = os.getenv("SCAN_SECRET", "ziggs-scan-dev-v1")
WORKER_NAME = os.getenv("SCAN_WORKER_NAME", socket.gethostname())
WORKER_ID = os.getenv("SCAN_WORKER_ID") or f"{socket.gethostname()}-{os.urandom(4).hex()}"
REGION_PREF = os.getenv("SCAN_REGION_PREF", "")
PROBE_TIMEOUT = int(os.getenv("SCAN_PROBE_TIMEOUT", "15"))
HEARTBEAT_INTERVAL = int(os.getenv("SCAN_HEARTBEAT_INTERVAL", "30"))
LOG_LEVEL = os.getenv("SCAN_LOG_LEVEL", "INFO")

# Tunnel metadata — quando preenchido, a VPS aparece no /vps-manifest.json
# (companion + site) automaticamente. VPS sem tunnel deixa vazio.
VPS_LABEL = os.getenv("VPS_LABEL", "")
VPS_COUNTRY = os.getenv("VPS_COUNTRY", "")
VPS_ENDPOINT = os.getenv("VPS_ENDPOINT", "")
VPS_SERVER_PUBKEY = os.getenv("VPS_SERVER_PUBKEY", "")
VPS_PING_URL = os.getenv("VPS_PING_URL", "")

# Credencial individual persistida em disco — sobrevive restart.
# Sem isso, cada restart re-registra e gera token novo; o token antigo
# fica inválido e o worker não consegue claim/heartbeat/report.
_CRED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".scan-credential")
_REPORT_SPOOL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".scan-report-spool.json")
_API_TOKEN = ""

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("vps_scanner")

_shutdown = Event()

# Mantém uma margem confortável abaixo do limite wire do backend. Chunks são
# codificados em base64, portanto o corpo é maior que o JSON bruto.
REPORT_BODY_MAX_BYTES = 3_500_000
# 2 MiB vira ~2,8 MiB em base64, mantendo folga para os limites HTTP e proxy.
_REPORT_CHUNK_BYTES = 2 * 1024 * 1024
# O backend reconstitui o JSON inteiro antes de colocá-lo na fila durável.
# Limitar aqui impede que uma batalha excepcional esgote a VPS, o proxy ou o DB.
REPORT_PAYLOAD_MAX_BYTES = 20 * 1024 * 1024
_MAX_REPORT_CHUNKS = REPORT_PAYLOAD_MAX_BYTES // _REPORT_CHUNK_BYTES


def _json_bytes(body) -> bytes:
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def _build_report_bodies(base: dict, data: list[dict] | None) -> list[dict]:
    """Cria reports idempotentes sem ultrapassar o limite HTTP.

    Páginas normais seguem como um report simples. Deep-processes grandes são
    fatiados sobre a representação JSON completa, de forma que cada parte pode
    ser reenviada sem baixar a batalha de novo após uma falha de transporte.
    """
    simple = {**base, "data": data}
    if len(_json_bytes(simple)) <= REPORT_BODY_MAX_BYTES:
        return [simple]
    if data is None:
        return [simple]

    encoded = _json_bytes(data)
    if len(encoded) > REPORT_PAYLOAD_MAX_BYTES:
        raise ValueError("payload excede o limite total de report")
    digest = hashlib.sha256(encoded).hexdigest()
    chunks = [encoded[i:i + _REPORT_CHUNK_BYTES] for i in range(0, len(encoded), _REPORT_CHUNK_BYTES)]
    if len(chunks) > _MAX_REPORT_CHUNKS:
        raise ValueError("payload excede o limite total de report")
    reports = []
    for index, chunk in enumerate(chunks):
        report = {
            **base,
            "data": None,
            "payload_chunk": base64.b64encode(chunk).decode("ascii"),
            "payload_sha256": digest,
            "chunk_index": index,
            "chunk_count": len(chunks),
        }
        if len(_json_bytes(report)) > REPORT_BODY_MAX_BYTES:
            raise ValueError("chunk de report excede o limite seguro")
        reports.append(report)
    return reports


def _load_report_spool():
    """Recupera um report já coletado para não repetir download após erro HTTP."""
    try:
        with open(_REPORT_SPOOL_FILE, "r", encoding="utf-8") as f:
            spool = json.load(f)
        if isinstance(spool, dict) and isinstance(spool.get("reports"), list):
            return spool
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return None


def _save_report_spool(task_id: int, reports: list[dict]) -> None:
    tmp = f"{_REPORT_SPOOL_FILE}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"task_id": task_id, "reports": reports}, f, separators=(",", ":"))
        os.replace(tmp, _REPORT_SPOOL_FILE)
    except OSError as exc:
        log.warning("Não foi possível persistir report pendente: %s", exc)
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _clear_report_spool():
    try:
        os.unlink(_REPORT_SPOOL_FILE)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("Não foi possível apagar report enviado: %s", exc)


def _rechunk_report_spool(spool: dict, chunk_bytes: int | None = None) -> dict | None:
    """Reduz o tamanho das partes preservando a coleta já persistida.

    Um 413 pode vir de um proxy com teto menor que o contrato do backend. Em
    vez de baixar a batalha de novo, reconstrói o JSON no spool e o refaz em
    partes menores. Retorna ``None`` quando já não há como reduzir.
    """
    reports = spool.get("reports") or []
    if not reports:
        return None
    first = reports[0]
    try:
        if first.get("payload_chunk") is None:
            if first.get("data") is None:
                return None
            encoded = _json_bytes(first["data"])
        else:
            ordered = sorted(reports, key=lambda row: row["chunk_index"])
            encoded = b"".join(
                base64.b64decode(row["payload_chunk"], validate=True)
                for row in ordered
            )
    except (KeyError, ValueError, TypeError):
        return None
    if not encoded or len(encoded) > REPORT_PAYLOAD_MAX_BYTES:
        return None
    current = max(
        (len(base64.b64decode(row["payload_chunk"], validate=True))
         for row in reports if row.get("payload_chunk") is not None),
        default=len(encoded),
    )
    target = chunk_bytes if chunk_bytes is not None else current // 2
    if target < 64 * 1024 or target >= current:
        return None
    base = {
        key: value for key, value in first.items()
        if key not in {"data", "payload_chunk", "payload_sha256", "chunk_index", "chunk_count"}
    }
    digest = hashlib.sha256(encoded).hexdigest()
    chunks = [encoded[i:i + target] for i in range(0, len(encoded), target)]
    if len(chunks) > _MAX_REPORT_CHUNKS:
        return None
    reports = []
    for index, chunk in enumerate(chunks):
        report = {
            **base,
            "data": None,
            "payload_chunk": base64.b64encode(chunk).decode("ascii"),
            "payload_sha256": digest,
            "chunk_index": index,
            "chunk_count": len(chunks),
        }
        if len(_json_bytes(report)) > REPORT_BODY_MAX_BYTES:
            return None
        reports.append(report)
    return {"task_id": first.get("task_id"), "reports": reports}


def _send_report_spool(spool: dict) -> tuple[bool, int]:
    reports = spool.get("reports") or []
    for index, report in enumerate(reports):
        # Uploads chunked podem durar mais que a lease original. Renova antes
        # de cada parte para não descartar uma coleta válida no último chunk.
        renew_status, _ = _request("POST", "/scan/renew", {
            "worker_id": WORKER_ID,
            "task_id": report["task_id"],
            "lease_token": report["lease_token"],
        })
        if renew_status not in (200, 202):
            log.warning("Lease do report %d/%d não foi renovada (HTTP %d)", index + 1, len(reports), renew_status)
            return False, renew_status
        status, result_data = _request("POST", "/scan/report", report, timeout=120)
        if status not in (200, 202):
            log.warning("Report %d/%d falhou (HTTP %d)", index + 1, len(reports), status)
            return False, status
        if result_data:
            log.info(
                "Report %d/%d aceito=%d rejeitado=%d",
                index + 1, len(reports), result_data.get("accepted", 0), result_data.get("rejected", 0),
            )
    return True, 202


def _on_signal(signum, frame):
    name = signal.Signals(signum).name
    log.info("Received %s, shutting down...", name)
    _shutdown.set()


signal.signal(signal.SIGINT, _on_signal)
signal.signal(signal.SIGTERM, _on_signal)


def _request(method, path, body=None, timeout=30):
    url = f"{BACKEND_URL}{path}"
    headers = {
        "X-Scan-Secret": _API_TOKEN or SECRET,
        "X-Scan-Worker": WORKER_ID,
    }
    if body is not None:
        raw = json.dumps(body).encode("utf-8")
        # gzip em payloads grandes (>32KB) — o backend aceita Content-Encoding: gzip
        # nos reports de scan (limite wire 1MB sem gzip, 4MB com gzip).
        if len(raw) > 32_768:
            import gzip
            raw = gzip.compress(raw)
            headers["Content-Type"] = "application/json"
            headers["Content-Encoding"] = "gzip"
        else:
            headers["Content-Type"] = "application/json"
        data = raw
    else:
        data = None
    # Usa a credencial individual quando disponível (claim/heartbeat/report);
    # o bootstrap secret só serve pra registrar.
    req = urllib.request.Request(
        url, data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.status == 204 or not raw:
                return resp.status, None
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        if exc.code == 204:
            return 204, None
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        log.warning("HTTP %d %s: %s", exc.code, path, err_body[:200])
        return exc.code, None
    except (urllib.error.URLError, OSError) as exc:
        log.warning("HTTP error %s: %s", path, exc)
        return 0, None


def _fetch_feed_page(host, feed_type, offset):
    """Busca uma página do feed ou deep-process de uma batalha.
    Para deep_process, offset é o battle.id (não o offset do feed).
    Retorna (status, data_list)."""
    if feed_type == "deep_process":
        return _fetch_deep_process(host, offset)
    if feed_type == "battles":
        url = f"https://{host}/api/gameinfo/battles?sort=recent&limit=51&offset={offset}"
    else:
        url = f"https://{host}/api/gameinfo/events?limit=51&offset={offset}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "ZiggsCompanion/0.1 (https://ziggs.xyz)",
    })
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as resp:
            if resp.status == 200:
                raw = resp.read()
                data = json.loads(raw)
                if isinstance(data, list):
                    return "ok", data
                return "error", None
            return "error", None
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            return "rate_limited", None
        return "error", None
    except (urllib.error.URLError, OSError, TimeoutError):
        return "error", None


def _fetch_deep_process(host, battle_id):
    """Busca detail + eventos paginados de uma batalha para deep-process.
    Retorna (status, data_list) onde data_list tem um único item com
    _deep_process=True, battle_id, raw e events. Stdlib only (urllib)."""
    headers = {"User-Agent": "ZiggsCompanion/0.1 (https://ziggs.xyz)"}

    def _get(url):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as resp:
                if resp.status == 200:
                    return "ok", json.loads(resp.read())
                return "error", None
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                return "rate_limited", None
            return "error", None
        except (urllib.error.URLError, OSError, TimeoutError):
            return "error", None

    # detail
    st, raw = _get(f"https://{host}/api/gameinfo/battles/{battle_id}")
    if st != "ok" or not isinstance(raw, dict) or not raw.get("id"):
        return st if st == "rate_limited" else "error", None

    # eventos paginados
    events = []
    albion_id = raw["id"]
    for page in range(40):
        st, batch = _get(f"https://{host}/api/gameinfo/events/battle/{albion_id}?offset={page * 51}&limit=51")
        if st == "rate_limited":
            return "rate_limited", None
        if st != "ok" or not isinstance(batch, list) or not batch:
            break
        events.extend(batch)
        if len(batch) < 51:
            break

    return "ok", [{"_deep_process": True, "battle_id": battle_id, "raw": raw, "events": events}]


def _heartbeat_loop():
    while not _shutdown.is_set():
        _shutdown.wait(HEARTBEAT_INTERVAL)
        if _shutdown.is_set():
            break
        status, _ = _request("POST", "/scan/heartbeat", {"worker_id": WORKER_ID})
        if status == 204:
            log.debug("Heartbeat ok")
        else:
            log.warning("Heartbeat failed (HTTP %d)", status)


def _load_credential():
    """Carrega credencial salva em disco (se existir e for do mesmo WORKER_ID)."""
    global _API_TOKEN
    try:
        with open(_CRED_FILE, "r") as f:
            data = json.load(f)
            if data.get("worker_id") == WORKER_ID and data.get("token"):
                _API_TOKEN = data["token"]
                log.info("Loaded saved credential for %s", WORKER_ID)
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass


def _save_credential(token):
    """Persiste credencial em disco pra sobreviver restart."""
    global _API_TOKEN
    _API_TOKEN = token
    try:
        with open(_CRED_FILE, "w") as f:
            json.dump({"worker_id": WORKER_ID, "token": token}, f)
    except OSError as e:
        log.warning("Failed to save credential: %s", e)


def _register():
    # Se já temos credencial salva, tenta heartbeat pra ver se ainda é válida.
    # Se funcionar, não precisa re-registrar (evita gerar token novo a cada restart).
    if _API_TOKEN:
        status, _ = _request("POST", "/scan/heartbeat", {"worker_id": WORKER_ID})
        if status == 204:
            log.info("Reusing existing credential for %s", WORKER_ID)
            return
        # 401 = credencial expirada/revogada — re-registra
        log.info("Saved credential invalid, re-registering...")

    while not _shutdown.is_set():
        # Registro usa o bootstrap SECRET, não a credencial individual.
        # SCAN_REGION_PREF é ignorado — workers claimam de todas as regiões.
        body = {"worker_id": WORKER_ID, "name": WORKER_NAME}
        # Tunnel metadata — optional, faz a VPS aparecer no manifest do companion.
        if VPS_LABEL:
            body["vps_label"] = VPS_LABEL
            body["vps_country"] = VPS_COUNTRY or None
            body["vps_endpoint"] = VPS_ENDPOINT or None
            body["vps_server_pubkey"] = VPS_SERVER_PUBKEY or None
            body["vps_ping_url"] = VPS_PING_URL or None
        # Para registro, usa o bootstrap secret
        url = f"{BACKEND_URL}/scan/register"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json", "X-Scan-Secret": SECRET},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                if resp.status == 200 and raw:
                    result = json.loads(raw)
                    token = result.get("credential", "")
                    if token:
                        _save_credential(token)
                    log.info("Registered as %s (status=%s)", WORKER_ID, result.get("status", "?"))
                    return
        except urllib.error.HTTPError as exc:
            log.warning("Register failed (HTTP %d), retrying in 10s...", exc.code)
        except (urllib.error.URLError, OSError):
            log.warning("Register failed (network error), retrying in 10s...")
        _shutdown.wait(10)


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print("Ziggs VPS Scanner — feed polling")
        print("Usage: python3 vps_scanner.py")
        print("Env: SCAN_BACKEND_URL, SCAN_SECRET, SCAN_WORKER_NAME, SCAN_WORKER_ID,")
        print("     SCAN_PROBE_TIMEOUT, SCAN_HEARTBEAT_INTERVAL, SCAN_LOG_LEVEL")
        print("")
        print("Workers claim tasks from all regions (americas, europe, asia).")
        print("Rate limit is per host, so 1 worker can work on all 3 regions simultaneously.")
        return

    log.info("Starting — worker=%s name=%s regions=all backend=%s tunnel=%s",
             WORKER_ID, WORKER_NAME, BACKEND_URL, VPS_LABEL or "none")

    _load_credential()
    _register()
    if _shutdown.is_set():
        log.info("Shutdown before registration completed")
        return

    hb = threading.Thread(target=_heartbeat_loop, daemon=True, name="heartbeat")
    hb.start()

    while not _shutdown.is_set():
        # Primeiro conclui qualquer coleta já persistida. Assim timeout, restart
        # ou 413 nunca faz a VPS baixar a mesma batalha em loop.
        spool = _load_report_spool()
        if spool is not None:
            sent, status = _send_report_spool(spool)
            if sent:
                _clear_report_spool()
                continue
            if status == 413:
                reduced = _rechunk_report_spool(spool)
                if reduced is not None:
                    log.warning("Report pendente recebeu 413; reduzindo chunks sem nova coleta")
                    _save_report_spool(reduced["task_id"], reduced["reports"])
                    continue
                log.error("Report pendente permanece grande demais após reduzir chunks; liberando tarefa")
            if status in (404, 409, 413):
                log.warning("Report pendente não pode mais usar a lease (HTTP %d); descartando-o", status)
                _clear_report_spool()
                continue
            _shutdown.wait(10)
            continue

        # Workers claimam globalmente — o backend distribui tasks de todas as 3
        # regiões (americas, europe, asia) igualmente. Rate limit é por host, então
        # 1 worker pode trabalhar nas 3 regiões sem penalidade.
        status, task = _request("POST", "/scan/claim", {"worker_id": WORKER_ID})
        if status == 204:
            _shutdown.wait(5)
            continue
        if status != 200 or not task:
            log.warning("Claim failed (HTTP %d), retrying in 10s...", status)
            _shutdown.wait(10)
            continue

        task_id = task["task_id"]
        lease_token = task.get("lease_token", "")
        region = task["region"]
        feed_type = task["feed_type"]
        offset = task["page_offset"]
        host = HOSTS.get(region)
        if not host:
            log.error("Unknown region %r", region)
            reports = _build_report_bodies({
                "worker_id": WORKER_ID, "task_id": task_id, "lease_token": lease_token,
                "found_count": 0, "error_count": 1,
            }, None)
            _save_report_spool(task_id, reports)
            continue

        log.info("Task %d: %s/%s offset=%d", task_id, region, feed_type, offset)
        t0 = time.monotonic()
        result, data = _fetch_feed_page(host, feed_type, offset)
        elapsed = time.monotonic() - t0
        if result == "ok" and data is not None:
            found_count, error_count = len(data), 0
            log.info("Task %d done: %d items in %.1fs", task_id, found_count, elapsed)
        elif result == "rate_limited":
            found_count, error_count = 0, 1
            log.warning("Task %d rate limited (429), pausing 10s", task_id)
            _shutdown.wait(10)
        else:
            found_count, error_count = 0, 1
            log.warning("Task %d failed (%s) in %.1fs", task_id, result, elapsed)

        if not _shutdown.is_set():
            try:
                reports = _build_report_bodies({
                    "worker_id": WORKER_ID, "task_id": task_id, "lease_token": lease_token,
                    "found_count": found_count, "error_count": error_count,
                    "latency_ms": round(elapsed * 1000),
                }, data if result == "ok" else None)
            except ValueError as exc:
                log.error("Task %d não cabe em report seguro: %s", task_id, exc)
                reports = _build_report_bodies({
                    "worker_id": WORKER_ID, "task_id": task_id, "lease_token": lease_token,
                    "found_count": 0, "error_count": 1, "latency_ms": round(elapsed * 1000),
                }, None)
            _save_report_spool(task_id, reports)

    log.info("Shut down")


if __name__ == "__main__":
    main()