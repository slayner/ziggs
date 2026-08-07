#!/usr/bin/env python3
"""Ziggs VPS Scanner — distributed battle scanner worker.

Runs on tunnel VPS machines. Claims ranges of battle IDs from the backend,
probes each against the Albion public API, and reports found/missing/errors.

No external dependencies beyond Python 3.12 stdlib.
"""

from __future__ import annotations

import json
import logging
import os
import random
import signal
import socket
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event, Thread

# ---------------------------------------------------------------------------
# Configuration (env vars with defaults)
# ---------------------------------------------------------------------------

BACKEND_URL = os.environ.get("SCAN_BACKEND_URL", "http://localhost:8000")
SECRET = os.environ.get("SCAN_SECRET", "ziggs-scan-dev-v1")
WORKER_NAME = os.environ.get("SCAN_WORKER_NAME", socket.gethostname())
REGION_PREF = os.environ.get("SCAN_REGION_PREF", "") or None
CONCURRENCY = int(os.environ.get("SCAN_CONCURRENCY", "10"))
HEARTBEAT_INTERVAL = int(os.environ.get("SCAN_HEARTBEAT_INTERVAL", "30"))
PROBE_TIMEOUT = int(os.environ.get("SCAN_PROBE_TIMEOUT", "10"))
DELAY_BETWEEN_IDS = float(os.environ.get("SCAN_DELAY_BETWEEN_IDS", "0.1"))
LOG_LEVEL = os.environ.get("SCAN_LOG_LEVEL", "INFO").upper()

# Auto-generate worker_id from hostname + random hex if not set
_WORKER_ID_ENV = os.environ.get("SCAN_WORKER_ID", "")
if _WORKER_ID_ENV:
    WORKER_ID = _WORKER_ID_ENV
else:
    _rand = random.SystemRandom()
    WORKER_ID = f"{socket.gethostname()}-{_rand.getrandbits(32):08x}"

# Albion API hosts per region
HOSTS = {
    "americas": "gameinfo.albiononline.com",
    "europe":   "gameinfo-ams.albiononline.com",
    "asia":     "gameinfo-sgp.albiononline.com",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("vps_scanner")

# ---------------------------------------------------------------------------
# Shutdown signal
# ---------------------------------------------------------------------------

_shutdown = Event()

def _on_signal(signum, frame):
    name = signal.Signals(signum).name
    log.info("Received %s, shutting down gracefully...", name)
    _shutdown.set()

signal.signal(signal.SIGINT, _on_signal)
signal.signal(signal.SIGTERM, _on_signal)

# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only — urllib)
# ---------------------------------------------------------------------------

def _request(method: str, path: str, body: dict | None = None) -> tuple[int, dict | None]:
    """Make an HTTP request to the backend. Returns (status_code, json_body_or_None)."""
    url = f"{BACKEND_URL.rstrip('/')}{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Scan-Secret": SECRET,
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        # 204 is "no content" — not an error for us
        if exc.code == 204:
            return 204, None
        # Try to read error body
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        log.warning("HTTP %d %s: %s", exc.code, path, err_body[:200])
        return exc.code, None
    except (urllib.error.URLError, OSError) as exc:
        log.warning("HTTP error %s: %s", path, exc)
        return 0, None


def _probe_battle(host: str, battle_id: int) -> tuple[int, str]:
    """Probe a single battle ID against the Albion API.

    Returns (battle_id, "found" | "missing" | "error").
    """
    url = f"https://{host}/api/gameinfo/battles/{battle_id}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as resp:
            if resp.status == 200:
                raw = resp.read()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    return battle_id, "error"
                # Validate it has the expected "id" field matching
                if isinstance(data, dict) and data.get("id") == battle_id:
                    return battle_id, "found"
                return battle_id, "error"
            return battle_id, "error"
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return battle_id, "missing"
        if exc.code == 429:
            return battle_id, "rate_limited"
        return battle_id, "error"
    except (urllib.error.URLError, OSError, TimeoutError):
        return battle_id, "error"


# ---------------------------------------------------------------------------
# Heartbeat thread
# ---------------------------------------------------------------------------

def _heartbeat_loop():
    """Daemon thread: sends heartbeat every HEARTBEAT_INTERVAL seconds."""
    while not _shutdown.is_set():
        _shutdown.wait(HEARTBEAT_INTERVAL)
        if _shutdown.is_set():
            break
        try:
            status, _ = _request("POST", "/scan/heartbeat", {"worker_id": WORKER_ID})
            if status == 204:
                log.debug("Heartbeat ok")
            else:
                log.warning("Heartbeat failed (HTTP %d)", status)
        except Exception:
            log.warning("Heartbeat error", exc_info=True)


# ---------------------------------------------------------------------------
# Register (retry until success)
# ---------------------------------------------------------------------------

def _register():
    """Register with backend. Retries every 10s until success."""
    while not _shutdown.is_set():
        body = {"worker_id": WORKER_ID, "name": WORKER_NAME}
        if REGION_PREF:
            body["region_pref"] = REGION_PREF
        status, data = _request("POST", "/scan/register", body)
        if status == 200 and data:
            log.info("Registered as %r (status=%s)", WORKER_ID, data.get("status", "?"))
            return
        log.warning("Register failed (HTTP %d), retrying in 10s...", status)
        _shutdown.wait(10)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print("Ziggs VPS Scanner")
        print()
        print("Usage: python3 vps_scanner.py")
        print()
        print("Environment variables:")
        print("  SCAN_BACKEND_URL        Backend API base URL (default: http://localhost:8000)")
        print("  SCAN_SECRET             Shared secret (default: ziggs-scan-dev-v1)")
        print("  SCAN_WORKER_NAME        Human-friendly name (default: hostname)")
        print("  SCAN_WORKER_ID          Unique worker ID (default: hostname-random)")
        print("  SCAN_REGION_PREF        Preferred region: americas/europe/asia (default: any)")
        print("  SCAN_CONCURRENCY        Parallel probes (default: 10)")
        print("  SCAN_HEARTBEAT_INTERVAL Seconds between heartbeats (default: 30)")
        print("  SCAN_PROBE_TIMEOUT      Seconds per HTTP probe (default: 10)")
        print("  SCAN_DELAY_BETWEEN_IDS  Seconds between dispatching probes (default: 0.1)")
        print("  SCAN_LOG_LEVEL          Log level: DEBUG/INFO/WARNING (default: INFO)")
        return

    log.info("Ziggs VPS Scanner starting")
    log.info("Worker ID: %s  Name: %s  Region pref: %s", WORKER_ID, WORKER_NAME, REGION_PREF or "any")
    log.info("Backend: %s  Concurrency: %d  Probe timeout: %ds",
             BACKEND_URL, CONCURRENCY, PROBE_TIMEOUT)

    # Register (blocks until success or shutdown)
    _register()
    if _shutdown.is_set():
        log.info("Shutdown before registration completed")
        return

    # Start heartbeat daemon thread
    hb = Thread(target=_heartbeat_loop, daemon=True, name="heartbeat")
    hb.start()

    # Adaptive throttle state
    consecutive_429 = 0
    MAX_CONSECUTIVE_429 = 5
    RATE_LIMIT_PAUSE = 30

    while not _shutdown.is_set():
        # Claim work
        claim_body = {"worker_id": WORKER_ID}
        if REGION_PREF:
            claim_body["region"] = REGION_PREF
        status, task = _request("POST", "/scan/claim", claim_body)

        if status == 204:
            log.debug("No work available, sleeping 5s")
            _shutdown.wait(5)
            continue

        if status != 200 or not task:
            log.warning("Claim failed (HTTP %d), retrying in 10s...", status)
            _shutdown.wait(10)
            continue

        task_id = task["task_id"]
        region = task["region"]
        start_id = task["battle_id_start"]
        end_id = task["battle_id_end"]
        host = HOSTS.get(region)
        if not host:
            log.error("Unknown region %r for task %d, reporting all as errors", region, task_id)
            _request("POST", "/scan/report", {
                "worker_id": WORKER_ID,
                "task_id": task_id,
                "found": [],
                "missing": [],
                "errors": list(range(start_id, end_id + 1)),
            })
            continue

        log.info("Task %d: region=%s range=%d-%d (%d IDs)",
                 task_id, region, start_id, end_id, end_id - start_id + 1)

        # Probe all IDs concurrently
        found: list[int] = []
        missing: list[int] = []
        errors: list[int] = []
        t_start = time.monotonic()

        ids = list(range(start_id, end_id + 1))
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
            futures = {}
            for bid in ids:
                if _shutdown.is_set():
                    break
                # Check rate-limit pause
                if consecutive_429 >= MAX_CONSECUTIVE_429:
                    log.warning("Rate limit threshold reached (%d consecutive 429s), pausing %ds",
                                consecutive_429, RATE_LIMIT_PAUSE)
                    _shutdown.wait(RATE_LIMIT_PAUSE)
                    consecutive_429 = 0
                futures[executor.submit(_probe_battle, host, bid)] = bid
                time.sleep(DELAY_BETWEEN_IDS)

            for future in as_completed(futures):
                if _shutdown.is_set():
                    # Cancel remaining futures
                    for f in futures:
                        f.cancel()
                    break
                try:
                    bid, result = future.result()
                except Exception:
                    bid = futures[future]
                    result = "error"
                    log.debug("Probe exception for battle %d", bid, exc_info=True)

                if result == "found":
                    found.append(bid)
                elif result == "missing":
                    missing.append(bid)
                elif result == "rate_limited":
                    consecutive_429 += 1
                    # Retry once after 2s
                    log.debug("429 on battle %d, retrying after 2s", bid)
                    time.sleep(2)
                    _, retry_result = _probe_battle(host, bid)
                    if retry_result == "found":
                        found.append(bid)
                        consecutive_429 = 0
                    elif retry_result == "missing":
                        missing.append(bid)
                        consecutive_429 = 0
                    else:
                        errors.append(bid)
                else:
                    errors.append(bid)
                    consecutive_429 = 0

        elapsed = time.monotonic() - t_start
        log.info("Task %d done: found=%d missing=%d errors=%d in %.1fs",
                 task_id, len(found), len(missing), len(errors), elapsed)

        # Report results
        if not _shutdown.is_set():
            report_body = {
                "worker_id": WORKER_ID,
                "task_id": task_id,
                "found": found,
                "missing": missing,
                "errors": errors,
            }
            status, result = _request("POST", "/scan/report", report_body)
            if status == 200 and result:
                log.info("Report accepted=%d rejected=%d", result.get("accepted", 0), result.get("rejected", 0))
            else:
                log.warning("Report failed (HTTP %d)", status)

    log.info("Scanner shut down")


if __name__ == "__main__":
    main()
