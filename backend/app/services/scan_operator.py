"""Single leader process for distributed scan coordination and ingestion."""
from __future__ import annotations

import asyncio
import logging
import os

import httpx
from sqlalchemy import text

from app.db import async_engine
from app.services import scan_dispatcher

log = logging.getLogger(__name__)

LOCK_ID = 0x5A494747
BACKEND_URL = os.getenv("SCAN_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
SCAN_SECRET = os.environ["SCAN_SECRET"]


async def run() -> int:
    async with async_engine.connect() as lock_connection:
        # ponytail: essa conexao fica idle-in-transaction POR DESIGN (a lideranca
        # dura a vida do processo) — exenta do timeout do banco, senao o
        # idle_in_transaction_session_timeout derruba ela e a lideranca some em silencio.
        await lock_connection.execute(text("SET idle_in_transaction_session_timeout = 0"))
        acquired = await lock_connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": LOCK_ID}
        )
        if not acquired:
            log.error("scan_operator: another operator owns the advisory lock")
            return 1

        async with httpx.AsyncClient(
            base_url=BACKEND_URL,
            headers={"X-Scan-Secret": SCAN_SECRET},
            timeout=2.0,
        ) as client:
            async def web_is_idle() -> bool:
                try:
                    response = await client.get("/scan/pressure")
                    response.raise_for_status()
                    return bool(response.json().get("idle"))
                except Exception as exc:
                    log.warning("scan_operator: pressure unavailable: %s", exc)
                    return False

            tasks = [
                asyncio.create_task(scan_dispatcher.run_forever()),
                asyncio.create_task(scan_dispatcher.run_ingest_forever(web_is_idle)),
                asyncio.create_task(scan_dispatcher.run_idle_worker_forever(web_is_idle)),
            ]
            log.info("scan_operator: leader started")
            try:
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await lock_connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": LOCK_ID}
                )
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    raise SystemExit(asyncio.run(run()))
