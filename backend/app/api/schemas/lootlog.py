"""Schemas do lootlog anônimo (área só-admin do site)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LootLogSubmissionOut(BaseModel):
    """Submissão exibida na área admin: só quem enviou + o texto cru do arquivo
    (idêntico ao .csv, copiável). Sem parse/valor — o carve do logger_pool vive
    em compute_logger_weights (chamado no finalize), não exposto aqui."""
    id: int
    event_id: int
    submitter_user_id: int | None
    submitter_name: str | None
    file_name: str
    raw_text: str | None
    created_at: datetime


class LootLogListOut(BaseModel):
    submissions: list[LootLogSubmissionOut]


class LoggerPayoutRowOut(BaseModel):
    submitter_user_id: int | None
    submitter_name: str | None
    weight: int          # contagem de coletas corroboradas (tratamento bot-v1)
    amount: int
    percent: int = 0     # round(100*w/total_w) — fatia do logger_pool deste logger


class LootLogPreviewOut(BaseModel):
    """Preview da fatia do logger p/ um CTA (área admin)."""
    event_id: int
    tab_value: int
    logger_percent: int
    logger_pool: int
    total_weight: int
    rows: list[LoggerPayoutRowOut]


class LootLogSettingsOut(BaseModel):
    logger_percent: int = 5
    enabled: bool = True


class LootLogSettingsIn(BaseModel):
    logger_percent: int | None = Field(default=None, ge=0, le=100)
    enabled: bool | None = None


class LootLogIngestOut(BaseModel):
    id: int
    row_count: int
    silver_total: int
    is_update: bool


class LoggerStandingOut(BaseModel):
    """Linha do standings pós-ingest (pro bot reagir no thread)."""
    user_id: int | None
    percent: int


class BotLootLogIngestOut(BaseModel):
    """Resposta do /bot/guilds/{g}/lootlog/ingest — inclui o standings atual
    (cada logger + sua % do logger_pool) computado com os pesos bot-v1."""
    id: int
    row_count: int
    silver_total: int
    is_update: bool
    standings: list[LoggerStandingOut]