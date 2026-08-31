#!/usr/bin/env python3
"""Verifica o estado dos cursors/pointers de scan para as 3 regiões."""
import asyncio
from sqlalchemy import select, text
from app.db import AsyncSessionLocal
from app.models.scan_worker import (
    ScanStreamState, ScanWorkTask, ScanLap, ScanWorker
)
from app.models.battles import BattleSyncCursor
from app.models.players import KillSyncCursor
from datetime import datetime, timezone

async def check():
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        
        print("=" * 60)
        print("SCAN STREAM STATES (circuit breakers, paused, recent_pages)")
        print("=" * 60)
        states = (await db.scalars(
            select(ScanStreamState).order_by(ScanStreamState.region, ScanStreamState.feed_type)
        )).all()
        
        if not states:
            print("Nenhum ScanStreamState encontrado")
        else:
            for s in states:
                opened = f" (abre em {s.opened_until.isoformat()})" if s.opened_until else ""
                print(f"{s.region:10s} / {s.feed_type:12s} | circuit={s.circuit_state:10s} | paused={str(s.paused):5s} | "
                      f"errors={s.consecutive_errors:2d} | recent_pages={s.recent_pages:2d} | "
                      f"last_claimed={s.last_claimed_at.isoformat() if s.last_claimed_at else 'never'}{opened}")
        
        print("\n" + "=" * 60)
        print("BATTLE SYNC CURSORS (next_offset, done)")
        print("=" * 60)
        battle_cursors = (await db.scalars(select(BattleSyncCursor).order_by(BattleSyncCursor.region))).all()
        for c in battle_cursors:
            print(f"{c.region:10s} | next_offset={c.next_offset:6d} | done={c.done}")
        
        print("\n" + "=" * 60)
        print("KILL SYNC CURSORS (next_offset, done)")
        print("=" * 60)
        kill_cursors = (await db.scalars(select(KillSyncCursor).order_by(KillSyncCursor.region))).all()
        for c in kill_cursors:
            print(f"{c.region:10s} | next_offset={c.next_offset:6d} | done={c.done}")
        
        print("\n" + "=" * 60)
        print("SCAN LAPS (backfill histórico ativo)")
        print("=" * 60)
        laps = (await db.scalars(
            select(ScanLap).where(ScanLap.status == "active").order_by(ScanLap.region, ScanLap.feed_type)
        )).all()
        if not laps:
            print("Nenhum lap ativo")
        else:
            for l in laps:
                pct = (l.completed_pages / l.expected_pages * 100) if l.expected_pages else 0
                print(f"{l.region:10s} / {l.feed_type:12s} | status={l.status} | "
                      f"pages={l.completed_pages}/{l.expected_pages} ({pct:.1f}%) | "
                      f"stride={l.page_stride} | started={l.started_at.isoformat()}")
        
        print("\n" + "=" * 60)
        print("TASKS PENDENTES/CLAIMED POR REGIÃO")
        print("=" * 60)
        result = await db.execute(text("""
            SELECT region, feed_type, status, COUNT(*), 
                   MIN(page_offset) as min_offset, MAX(page_offset) as max_offset
            FROM scan_work_tasks 
            WHERE status IN ('pending', 'claimed')
            GROUP BY region, feed_type, status 
            ORDER BY region, feed_type, status
        """))
        for row in result.fetchall():
            print(f"{row[0]:10s} / {row[1]:12s} | {row[2]:10s} | count={row[3]:4d} | "
                  f"offset_range=[{row[4]}, {row[5]}]")
        
        print("\n" + "=" * 60)
        print("WORKERS ATIVOS")
        print("=" * 60)
        workers = (await db.scalars(
            select(ScanWorker).where(ScanWorker.status == "active").order_by(ScanWorker.last_heartbeat.desc())
        )).all()
        if not workers:
            print("Nenhum worker ativo")
        else:
            for w in workers:
                age = (now - w.last_heartbeat).total_seconds() if w.last_heartbeat else 999999
                print(f"{w.name:20s} ({w.worker_id[:16]}...) | "
                      f"heartbeat={age:.0f}s ago | tasks_done={w.total_tasks_done} | "
                      f"battles={w.total_battles_found} | kills={w.total_kills_found} | "
                      f"errors={w.total_errors}")

asyncio.run(check())
