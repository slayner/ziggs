"""Companion — tarefas distribuídas de escaneamento de batalhas.

Cada linha é um range de IDs de batalha que um companion pode reivindicar,
sondar na API pública do Albion e reportar. N clients = N IPs = rate limit
distribuído — o ponto central do companion.

Sem auth. O dado reportado é validado contra a API pública do Albion no
upsert (upsert_battle_light), não confiamos cegamente no client.

Fluxo:
  1. Backend gera ranges a partir dos buracos da sequência por região (igual
     ao battle_sweeper, mas com claim/release em vez de sondagem síncrona).
  2. Companion chama POST /companion/scan/claim → pega uma tarefa pendente.
  3. Companion sonda cada ID em gameinfo.albiononline.com.
  4. Companion chama POST /companion/scan/report com found/missing/errors.
  5. Backend aplica upsert_battle_light nas batalhas encontradas (igual ao
     sweeper faz internamente), grava BattleIdProbe pros 404s e marca a
     tarefa como done.

Tarefas claim que expiram (claim_expires_at no passado) voltam a 'pending'
no próximo claim — tolerante a companions que caem no meio do trabalho.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigInt, pk


# Tarefas claim voltam a pending depois disso (companion caiu/fechou).
CLAIM_TTL = timedelta(minutes=15)
# Tamanho de cada range enviado a um companion por claim.
RANGE_SIZE = 50
# Regiões suportadas (precisa bater com battle_tracker.HOSTS).
COMPANION_REGIONS = ("americas", "europe", "asia")


class CompanionScanTask(Base):
    """Um range de IDs de batalha para escanear, por região.

    status:
      'pending'   — aguardando um companion pegar
      'claimed'   — um companion pegou (claim_expires_at no futuro)
      'done'      — reportado (found/missing/errors gravados)
      'failed'    — claim expirou sem report (volta a 'pending' na próxima gerada)
    """
    __tablename__ = "companion_scan_tasks"

    id: Mapped[int] = pk()
    region: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # Range inclusivo [start, end] — companion sonda endereço /battles/{id} de
    # cada ID nesse intervalo. RANGE_SIZE define quantos IDs por tarefa.
    battle_id_start: Mapped[int] = mapped_column(BigInt(), nullable=False)
    battle_id_end: Mapped[int] = mapped_column(BigInt(), nullable=False)
    # Prioridade: 2=alta (batalhas recentes), 1=média, 0=baixa (antigas).
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    # Instalação que pegou a tarefa (header X-Ziggs-Install). NÃO é auth — só
    # identidade: garante 1 range por PC em vez de 1 por processo (3 cópias
    # abertas durante um rebuild pegavam 3 ranges e pareciam 3 companions).
    # Legado/companion antigo sem o header = None, comportamento antigo.
    claimed_by: Mapped[str | None] = mapped_column(String(64), index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Contagem reportada pelo companion (validação no report).
    found_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    missing_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)