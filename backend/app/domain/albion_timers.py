"""Timers de prime time do Albion Online por região/servidor.

Cada servidor tem janelas de PvP (prime time) que abrem e fecham em horários
fixos UTC. Este módulo mapeia batalhas (start_time) para os timers ativos no
momento, permitindo construir um heatmap de atividade por timer.

Fonte: horários confirmados pelo usuário em ago/2026.
- Americas: 18, 20, 22, 00, 02, 04 (6 timers, janelas de 2h)
- Europe:   12, 14, 16, 18, 20, 22 (6 timers, janelas de 2h)
- Asia:     06, 08, 12, 14, 16, 18 (6 timers, janelas de 2h — NÃO tem 10)

A ordem de exibição começa pelo primeiro timer da "noite" de cada servidor
(18 no Americas, 12 no Europe, 06 no Asia), seguindo a sequência real dos
timers mesmo que atravesse meia-noite. O label é apenas o número (ex: "18"),
sem sufixo "Timer".
"""
from __future__ import annotations

from datetime import datetime, timezone

# ── Definição dos timers por região ──────────────────────────────────────────
# Cada timer: (label, hora_inicio_utc, hora_fim_utc)
# A ORDEM da lista define a ordem de exibição no heatmap (primeiro = primeiro
# timer do "dia" do servidor). As janelas têm 2h cada.

_TIMERS: dict[str, list[tuple[str, int, int]]] = {
    "americas": [
        ("18", 18, 20),
        ("20", 20, 22),
        ("22", 22, 24),
        ("00", 0, 2),
        ("02", 2, 4),
        ("04", 4, 6),
    ],
    "europe": [
        ("12", 12, 14),
        ("14", 14, 16),
        ("16", 16, 18),
        ("18", 18, 20),
        ("20", 20, 22),
        ("22", 22, 24),
    ],
    "asia": [
        ("06", 6, 8),
        ("08", 8, 10),
        ("12", 12, 14),
        ("14", 14, 16),
        ("16", 16, 18),
        ("18", 18, 20),
    ],
}


def timers_for_region(region: str) -> list[tuple[str, int, int]]:
    """Retorna a lista de (label, hora_inicio_utc, hora_fim_utc) da região,
    na ordem de exibição (primeiro timer do dia primeiro)."""
    return _TIMERS.get(region, _TIMERS["americas"])


def timer_for_battle(region: str, start_time: datetime) -> str | None:
    """Determina qual timer de prime time estava ativo quando a batalha
    começou. Retorna None se a batalha caiu fora de qualquer janela."""
    if start_time is None:
        return None
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    hour = start_time.hour
    for name, start, end in timers_for_region(region):
        if start <= hour < end:
            return name
    return None


def timer_heatmap(region: str, battle_times: list[tuple[datetime, int]]) -> list[dict]:
    """Constrói dados de heatmap de timers para uma guilda.

    Args:
        region: região da guilda (americas/europe/asia).
        battle_times: lista de (start_time, weight) onde weight é tipicamente
            o kill_fame ou 1 (contagem de batalhas).

    Returns:
        Lista de {"timer": str, "battles": int, "weight": int} ordenada pela
        ordem de exibição dos timers da região.
    """
    timers = timers_for_region(region)
    counts: dict[str, dict] = {
        name: {"timer": name, "battles": 0, "weight": 0}
        for name, _, _ in timers
    }
    for start_time, weight in battle_times:
        timer_name = timer_for_battle(region, start_time)
        if timer_name is not None and timer_name in counts:
            counts[timer_name]["battles"] += 1
            counts[timer_name]["weight"] += weight
    return [counts[name] for name, _, _ in timers]