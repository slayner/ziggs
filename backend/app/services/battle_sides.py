"""Detecção de lados de uma batalha a partir do grafo de kills entre facções.

Funciona em cima de QUEM MATOU QUEM (BattleKillEvent), não da tag de aliança
do jogo. Isso é o que pega handholding: duas alianças que combinam lutar
juntas (sem alliance in-game) quase não trocam kills entre si e concentram
hostilidade no inimigo comum — o grafo revela isso mesmo que o jogo não saiba.

Módulo puro (sem ORM/rede) para ser fácil de testar.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ponytail: thresholds fixos, calibrar com dados reais depois de rodar em produção
RAT_MIN_ENGAGEMENT = 5       # kills+deaths abaixo disso = pouco engajado -> rato
RAT_AMBIGUITY_RATIO = 0.4    # lutou quase igual pros 2 lados -> ambíguo -> rato


@dataclass
class Faction:
    key: str               # alliance_id, ou "g:<guild_id>" se não tiver aliança
    player_count: int = 0
    kills: int = 0
    deaths: int = 0


@dataclass
class SideAnalysis:
    side_of: dict[str, str] = field(default_factory=dict)   # faction_key -> "A"|"B"|"rats"
    player_count: dict[str, int] = field(default_factory=dict)  # label -> nº jogadores
    score: dict[str, int] = field(default_factory=dict)         # label -> kills reais contra o outro lado


def faction_key(guild_id: str | None, alliance_id: str | None, player_id: str | None) -> str:
    """Facção de um participante: aliança > guilda > o PRÓPRIO JOGADOR como
    facção de 1 pessoa só, se não tiver nem guilda nem aliança.

    Sem o fallback por jogador, todo guildless caía no MESMO bucket
    "g:sem_guilda" — numa luta com guildless dos dois lados, isso misturava
    inimigos na mesma facção e quebrava a detecção de lado (is_zvz, contagem
    de jogador por lado, score, tudo saía errado). Cada guildless agora é
    sua própria facção de 1 jogador; detect_sides já lida bem com muitas
    facções pequenas (é como já trata várias guildas pequenas), então cada
    um cai no lado certo pelo grafo de hostilidade real."""
    if alliance_id:
        return f"a:{alliance_id}"
    if guild_id:
        return f"g:{guild_id}"
    return f"p:{player_id}" if player_id else "g:sem_guilda"


def build_factions(participants: dict[str, dict]) -> tuple[dict[str, Faction], dict[str, str]]:
    """A partir de um dict {player_id: {guild_id, alliance_id, kills, deaths, ...}},
    agrupa em facções. Usado tanto pro processamento de 1 batalha quanto pro merge
    de várias batalhas combinadas numa KB só (ver battle_groups.py)."""
    factions: dict[str, Faction] = {}
    player_faction: dict[str, str] = {}
    for pid, row in participants.items():
        fk = faction_key(row.get("guild_id"), row.get("alliance_id"), pid)
        player_faction[pid] = fk
        f = factions.setdefault(fk, Faction(key=fk))
        f.player_count += 1
        f.kills += row.get("kills", 0)
        f.deaths += row.get("deaths", 0)
    return factions, player_faction


def detect_sides(
    factions: dict[str, Faction],
    kills_between: dict[tuple[str, str], int],
) -> dict[str, str]:
    """Recebe as facções da batalha e quantas vezes cada uma matou a outra
    (kills_between[(killer_key, victim_key)] = contagem) e devolve a qual
    lado cada facção pertence: "A", "B" ou "rats" (fora dos 2 lados principais)."""
    keys = list(factions.keys())
    if not keys:
        return {}
    if len(keys) == 1:
        return {keys[0]: "rats"}

    def hostility(a: str, b: str) -> int:
        return kills_between.get((a, b), 0) + kills_between.get((b, a), 0)

    # Semente: o par com maior hostilidade mútua vira os 2 polos opostos.
    best_pair, best_score = None, -1
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            h = hostility(a, b)
            if h > best_score:
                best_score, best_pair = h, (a, b)

    if best_score <= 0:
        # Nenhuma kill cruzada registrada entre ninguém — não há luta pra separar.
        return {k: "rats" for k in keys}

    side_a, side_b = {best_pair[0]}, {best_pair[1]}
    assigned = {best_pair[0]: "A", best_pair[1]: "B"}

    remaining = sorted(
        (k for k in keys if k not in assigned),
        key=lambda k: -factions[k].player_count,
    )

    for k in remaining:
        h_a = sum(hostility(k, m) for m in side_a)
        h_b = sum(hostility(k, m) for m in side_b)
        total_engagement = factions[k].kills + factions[k].deaths
        lo, hi = sorted((h_a, h_b))
        ambiguous = hi > 0 and (lo / hi) > RAT_AMBIGUITY_RATIO

        if total_engagement < RAT_MIN_ENGAGEMENT or (h_a == 0 and h_b == 0) or ambiguous:
            assigned[k] = "rats"
            continue

        if h_a > h_b:
            side_b.add(k)
            assigned[k] = "B"
        elif h_b > h_a:
            side_a.add(k)
            assigned[k] = "A"
        else:
            if len(side_a) <= len(side_b):
                side_a.add(k)
                assigned[k] = "A"
            else:
                side_b.add(k)
                assigned[k] = "B"

    return assigned


def analyze(
    factions: dict[str, Faction],
    kills_between: dict[tuple[str, str], int],
) -> SideAnalysis:
    side_of = detect_sides(factions, kills_between)

    player_count: dict[str, int] = {}
    for key, label in side_of.items():
        player_count[label] = player_count.get(label, 0) + factions[key].player_count

    score = {"A": 0, "B": 0}
    main_sides = {"A", "B"}
    for (killer, victim), n in kills_between.items():
        ks, vs = side_of.get(killer), side_of.get(victim)
        if ks in main_sides and vs in main_sides and ks != vs:
            score[ks] += n

    return SideAnalysis(side_of=side_of, player_count=player_count, score=score)


def _demo() -> None:
    # 1) handholding: OXE + FOUP combinam contra FURE sem se atacarem.
    factions = {
        "a:OXE": Faction("a:OXE", player_count=70, kills=29, deaths=50),
        "a:FOUP": Faction("a:FOUP", player_count=60, kills=20, deaths=40),
        "a:FURE": Faction("a:FURE", player_count=80, kills=90, deaths=40),
    }
    kills_between = {
        ("a:OXE", "a:FURE"): 15,
        ("a:FOUP", "a:FURE"): 14,
        ("a:FURE", "a:OXE"): 50,
        ("a:FURE", "a:FOUP"): 40,
        # OXE e FOUP não se atacam -> hostilidade 0 entre eles.
    }
    result = analyze(factions, kills_between)
    assert result.side_of["a:OXE"] == result.side_of["a:FOUP"], "OXE e FOUP deveriam ficar no mesmo lado"
    assert result.side_of["a:FURE"] != result.side_of["a:OXE"], "FURE deveria ficar no lado oposto"
    assert result.player_count[result.side_of["a:OXE"]] == 130
    assert result.player_count[result.side_of["a:FURE"]] == 80
    assert result.score[result.side_of["a:OXE"]] == 29  # 15+14
    assert result.score[result.side_of["a:FURE"]] == 90  # 50+40

    # 2) rato: zerg pequena que cruzou o caminho, lutou um pouco com os 2 lados.
    factions2 = dict(factions)
    factions2["g:zerg_de_passagem"] = Faction("g:zerg_de_passagem", player_count=8, kills=2, deaths=1)
    kills_between2 = dict(kills_between)
    kills_between2[("g:zerg_de_passagem", "a:OXE")] = 1
    kills_between2[("g:zerg_de_passagem", "a:FURE")] = 1
    result2 = analyze(factions2, kills_between2)
    assert result2.side_of["g:zerg_de_passagem"] == "rats", "engajamento baixo deveria virar rato"

    # 3) sem cruzamento de kills nenhum -> não dá pra separar lados, tudo rato.
    factions3 = {"a:X": Faction("a:X", player_count=10), "a:Y": Faction("a:Y", player_count=10)}
    result3 = analyze(factions3, {})
    assert result3.side_of["a:X"] == "rats" and result3.side_of["a:Y"] == "rats"

    # 4) guildless dos DOIS lados de uma mesma luta — faction_key tem que dar
    # uma facção por JOGADOR (não um "g:sem_guilda" compartilhado), senão
    # build_factions mistura os dois bandos guildless numa facção só e
    # detect_sides joga os dois pro MESMO lado, mesmo lutando um contra o outro.
    participants4 = {
        "p1": {"guild_id": "G1", "alliance_id": None, "kills": 8, "deaths": 2},
        "p2": {"guild_id": None, "alliance_id": None, "kills": 4, "deaths": 1},  # guildless, lado G1
        "p3": {"guild_id": "G2", "alliance_id": None, "kills": 2, "deaths": 8},
        "p4": {"guild_id": None, "alliance_id": None, "kills": 1, "deaths": 4},  # guildless, lado G2
    }
    factions4, player_faction4 = build_factions(participants4)
    assert player_faction4["p2"] != player_faction4["p4"], "guildless de lados opostos não podem cair na mesma facção"
    kills_between4 = {
        (player_faction4["p1"], player_faction4["p3"]): 6,
        (player_faction4["p3"], player_faction4["p1"]): 1,
        (player_faction4["p2"], player_faction4["p3"]): 4,  # p2 (guildless) ataca G2
        (player_faction4["p3"], player_faction4["p2"]): 1,
        (player_faction4["p4"], player_faction4["p1"]): 1,  # p4 (guildless) ataca G1
        (player_faction4["p1"], player_faction4["p4"]): 4,
    }
    result4 = analyze(factions4, kills_between4)
    assert result4.side_of[player_faction4["p1"]] == result4.side_of[player_faction4["p2"]], "p1 e p2 deveriam ficar do mesmo lado"
    assert result4.side_of[player_faction4["p3"]] == result4.side_of[player_faction4["p4"]], "p3 e p4 deveriam ficar do mesmo lado"
    assert result4.side_of[player_faction4["p1"]] != result4.side_of[player_faction4["p3"]], "os 2 bandos deveriam ficar em lados opostos"

    print("battle_sides: 4 cenários OK")


if __name__ == "__main__":
    _demo()
