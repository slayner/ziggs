"""Migra dados do SQLite legado para PostgreSQL.

Uso:
    cd backend
    scripts\python.exe scripts\migrate_sqlite_to_postgres.py

Copia tabela por tabela em chunks, desabilitando foreign-key checks na sessão
Postgres (`session_replication_role = replica`). Reseta sequences depois.
"""
from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

# Adiciona app/ ao path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.base import Base

SQLITE_URL = "sqlite:///./ziggs.db"
POSTGRES_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://ziggs:azqwsx123@localhost:5432/ziggs")

CHUNK_SIZE = 5000


def _chunk(rows: list[dict], size: int) -> Iterator[list[dict]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def main() -> None:
    src_engine = create_engine(SQLITE_URL, future=True)
    dst_engine = create_engine(POSTGRES_URL, future=True, pool_size=5, max_overflow=5)

    src_session = sessionmaker(bind=src_engine, autoflush=False)
    dst_session = sessionmaker(bind=dst_engine, autoflush=False)

    tables = list(Base.metadata.sorted_tables)
    total_tables = len(tables)

    with dst_session() as dst:
        # Desabilita FK checks e triggers nesta sessão.
        dst.execute(text("SET session_replication_role = 'replica'"))
        dst.commit()

        for idx, table in enumerate(tables, 1):
            t0 = time.monotonic()
            name = table.name
            with src_session() as src:
                count = src.execute(select(text("count(*)")).select_from(table)).scalar()
                if not count:
                    print(f"[{idx}/{total_tables}] {name}: vazio — pulando")
                    continue
                print(f"[{idx}/{total_tables}] {name}: migrando {count:,} linhas...", end=" ", flush=True)

                inserted = 0
                for chunk in _fetch_chunks(src, table, CHUNK_SIZE):
                    dst.execute(table.insert(), chunk)
                    inserted += len(chunk)
                    if inserted % 50000 == 0 or inserted == count:
                        print(f"{inserted:,}", end=" ", flush=True)
                dst.commit()

            elapsed = time.monotonic() - t0
            print(f"OK ({elapsed:.1f}s)")

        # Reseta todas as sequences de serial PKs.
        for seq_name in _serial_sequences(dst):
            table_name = seq_name.replace("_id_seq", "")
            dst.execute(
                text(f"SELECT setval('{seq_name}', COALESCE((SELECT MAX(id) FROM {table_name}), 1))")
            )
        dst.commit()
        print("Sequences resetadas.")


def _fetch_chunks(src: Session, table, size: int) -> Iterator[list[dict]]:
    """Lê a tabela em chunks usando server-side cursor via yield_per."""
    stmt = select(table).execution_options(yield_per=size)
    rows: list[dict] = []
    for row in src.execute(stmt):
        rows.append(row._asdict())
        if len(rows) >= size:
            yield rows
            rows = []
    if rows:
        yield rows


def _serial_sequences(dst: Session) -> list[str]:
    """Lista sequences de PKs autoincrement (serial/bigserial) no schema public."""
    rows = dst.execute(text("""
        SELECT sequencename
        FROM pg_sequences
        WHERE schemaname = 'public' AND sequencename LIKE '%_id_seq'
    """)).all()
    return [r[0] for r in rows]


if __name__ == "__main__":
    main()
