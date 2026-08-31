"""Verifica o estado do scan dispatcher para diagnosticar problemas de região."""
import asyncio
import sys
from sqlalchemy import select
from app.db import AsyncSessionLocal
from app.models.scan_worker import ScanStreamState, ScanWorkTask, ScanWorker
from datetime import datetime, timezone

async def check():
    async with AsyncSessionLocal() as db:
        print("=== ScanStreamState (circuits/paused) ===")
        states = (await db.scalars(select(ScanStreamState).order_by(ScanStreamState.region, ScanStreamState.feed_type))).all()
        for s in states:
            print(f"{s.region}/{s.feed_type}: circuit={s.circuit_state} paused={s.paused} errors={s.consecutive_errors} recent_pages={s.recent_pages}")
        
        print("\n=== Tasks por região/status ===")
        result = await db.execute("""
            SELECT region, feed_type, status, COUNT(*) 
            FROM scan_work_tasks 
            GROUP BY region, feed_type, status 
            ORDER BY region, feed_type, status
        """)
        for row in result:
            print(f"{row[0]}/{row[1]} {row[2]}: {row[3]}")
        
        print("\n=== Workers ativos ===")
        now = datetime.now(timezone.utc)
        workers = (await db.scalars(select(ScanWorker).order_by(ScanWorker.last_heartbeat.desc()))).all()
        for w in workers:
            age = (now - w.last_heartbeat).total_seconds() if w.last_heartbeat else 999999
            print(f"{w.name} ({w.worker_id[:16]}...): status={w.status} heartbeat={age:.0f}s ago")

asyncio.run(check())
