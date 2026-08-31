import asyncio, sys
sys.path.insert(0, ".")
from sqlalchemy import text
from app.db import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(
            "SELECT kind, region, completed_head_source_id, captured_head_source_id, "
            "scan_anchor_source_id, scan_head_source_id, scan_active, scan_blocked, "
            "scan_resolution, next_offset, scan_started_at, scan_last_progress_at, "
            "blocked_reason FROM native_feed_streams ORDER BY kind, region"
        ))).all()
        for r in rows:
            print(dict(r._mapping))

asyncio.run(main())