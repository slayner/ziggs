#!/usr/bin/env python3
"""Ziggs VPS Scanner — feed polling distribuído.

Roda em VPS tunnel. Pede tasks ao backend (POST /scan/claim), busca páginas
do feed de batalhas ou kill events da API pública do Albion, e reporta os
dados crus (POST /scan/report). O backend faz upsert.

Stdlib only — sem dependências externas (urllib, json, threading).
"""
from __future__ import annotations

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

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("vps_scanner")

_shutdown = Event()


def _on_signal(signum, frame):
    name = signal.Signals(signum).name
    log.info("Received %s, shutting down...", name)
    _shutdown.set()


signal.signal(signal.SIGINT, _on_signal)
signal.signal(signal.SIGTERM, _on_signal)


def _request(method, path, body=None, timeout=30):
    url = f"{BACKEND_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "X-Scan-Secret": SECRET},
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
    """Busca uma página do feed. Retorna (status, data_list)."""
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


def _register():
    while not _shutdown.is_set():
        body = {"worker_id": WORKER_ID, "name": WORKER_NAME}
        if REGION_PREF:
            body["region_pref"] = REGION_PREF
        status, data = _request("POST", "/scan/register", body)
        if status == 200 and data:
            log.info("Registered as %s (status=%s)", WORKER_ID, data.get("status", "?"))
            return
        log.warning("Register failed (HTTP %d), retrying in 10s...", status)
        _shutdown.wait(10)


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print("Ziggs VPS Scanner — feed polling")
        print("Usage: python3 vps_scanner.py")
        print("Env: SCAN_BACKEND_URL, SCAN_SECRET, SCAN_WORKER_NAME, SCAN_WORKER_ID,")
        print("     SCAN_REGION_PREF, SCAN_PROBE_TIMEOUT, SCAN_HEARTBEAT_INTERVAL,")
        print("     SCAN_LOG_LEVEL")
        return

    log.info("Starting — worker=%s name=%s region=%s backend=%s",
             WORKER_ID, WORKER_NAME, REGION_PREF or "any", BACKEND_URL)

    _register()
    if _shutdown.is_set():
        log.info("Shutdown before registration completed")
        return

    hb = threading.Thread(target=_heartbeat_loop, daemon=True, name="heartbeat")
    hb.start()

    consecutive_429 = 0

    while not _shutdown.is_set():
        claim_body = {"worker_id": WORKER_ID}
        if REGION_PREF:
            claim_body["region"] = REGION_PREF
        status, task = _request("POST", "/scan/claim", claim_body)

        if status == 204:
            _shutdown.wait(5)
            continue
        if status != 200 or not task:
            log.warning("Claim failed (HTTP %d), retrying in 10s...", status)
            _shutdown.wait(10)
            continue

        task_id = task["task_id"]
        region = task["region"]
        feed_type = task["feed_type"]
        offset = task["page_offset"]
        host = HOSTS.get(region)
        if not host:
            log.error("Unknown region %r", region)
            _request("POST", "/scan/report", {
                "worker_id": WORKER_ID, "task_id": task_id,
                "found_count": 0, "error_count": 1, "data": None,
            })
            continue

        log.info("Task %d: %s/%s offset=%d", task_id, region, feed_type, offset)

        t0 = time.monotonic()
        result, data = _fetch_feed_page(host, feed_type, offset)
        elapsed = time.monotonic() - t0

        if result == "ok" and data is not None:
            found_count = len(data)
            error_count = 0
            log.info("Task %d done: %d items in %.1fs", task_id, found_count, elapsed)
        elif result == "rate_limited":
            consecutive_429 += 1
            found_count = 0
            error_count = 1
            log.warning("Task %d rate limited (429), pausing 10s", task_id)
            _shutdown.wait(10)
        else:
            found_count = 0
            error_count = 1
            log.warning("Task %d failed (%s) in %.1fs", task_id, result, elapsed)

        if not _shutdown.is_set():
            report_body = {
                "worker_id": WORKER_ID,
                "task_id": task_id,
                "found_count": found_count,
                "error_count": error_count,
                "data": data if result == "ok" else None,
            }
            status, result_data = _request("POST", "/scan/report", report_body, timeout=120)
            if status == 200 and result_data:
                log.info("Report accepted=%d rejected=%d",
                         result_data.get("accepted", 0), result_data.get("rejected", 0))
            else:
                log.warning("Report failed (HTTP %d)", status)

    log.info("Shut down")


if __name__ == "__main__":
    main()