// Detecção automática de zona (azul vs PvP) baseada no mapa atual do jogador.
//
// Fonte dos dados: ao-bin-dumps/cluster/world.json — cada cluster tem um @type
// que classifica o mapa. A classificação azul/PvP é derivada do tipo:
//
//   AZUL (safe, non-lethal):
//     PLAYERCITY_SAFEAREA*, STARTINGCITY, STARTAREA, SAFEAREA*, TUTORIAL,
//     GUILDISLAND, PLAYERISLAND, SHOWROOMISLAND, DUNGEON_SAFEAREA,
//     PASSAGE_SAFEAREA, DUNGEON_YELLOW, OPENPVP_YELLOW, PASSAGE_YELLOW,
//     ARENA_* (non-lethal), DUNGEON_HELL_*_NON_LETHAL, ARENA_CRYSTAL_NONLETHAL,
//     CORRUPTED_DUNGEON_*, *_EXPEDITION_*, HARDCORE_EXPEDITION_*,
//     TUNNEL_ROYAL, TUNNEL_LOW/MEDIUM/HIGH, TUNNEL_HIDEOUT
//
//   PVP (lethal, full loot):
//     OPENPVP_BLACK_*, OPENPVP_RED, DUNGEON_BLACK_*, DUNGEON_RED,
//     DUNGEON_HELL_*_LETHAL, PASSAGE_BLACK, PASSAGE_RED,
//     PLAYERCITY_BLACK*, PLAYERCITY_HELLDEN, TUNNEL_ROYAL_RED,
//     TUNNEL_DEEP*, TUNNEL_HIDEOUT_DEEP, TUNNEL_DEEP_RAID,
//     HIDEOUT, ARENA_CRYSTAL (lethal), DRAGONAREA
//
// Sem memory read (Fase 1): não dá pra ler o mapa atual do processo do jogo.
// Esta module só tem a classificação — a detecção do mapa atual vem na Fase 2.5
// (memory read). Por enquanto a zona é setada manualmente via set_zone command.

use crate::transfer::ZoneType;

/// Classifica um cluster type string em zona azul ou PvP.
pub fn classify_zone(cluster_type: &str) -> ZoneType {
    let t = cluster_type.trim();
    if t.is_empty() {
        return ZoneType::Unknown;
    }

    // DUNGEON_HELL: non-lethal = azul, lethal = PvP. Checar antes dos prefixos
    // porque "DUNGEON_HELL_" está na lista PvP mas NON_LETHAL é safe.
    if t.starts_with("DUNGEON_HELL") {
        if t.contains("NON_LETHAL") {
            return ZoneType::Blue;
        }
        return ZoneType::PvP;
    }

    // ARENA_CRYSTAL: NONLETHAL = azul, resto = PvP
    if t.starts_with("ARENA_CRYSTAL") {
        if t.contains("NONLETHAL") || t.contains("NON_LETHAL") {
            return ZoneType::Blue;
        }
        return ZoneType::PvP;
    }

    // PvP (lethal) — tipos que permitem full-loot PvP ou são perigosos
    let pvp_prefixes = [
        "OPENPVP_BLACK",
        "OPENPVP_RED",
        "DUNGEON_BLACK",
        "DUNGEON_RED",
        "PASSAGE_BLACK",
        "PASSAGE_RED",
        "PLAYERCITY_BLACK",
        "PLAYERCITY_HELLDEN",
        "TUNNEL_ROYAL_RED",
        "TUNNEL_DEEP",
        "TUNNEL_HIDEOUT_DEEP",
        "HIDEOUT",
        "DRAGONAREA",
    ];
    for prefix in &pvp_prefixes {
        if t.starts_with(prefix) {
            return ZoneType::PvP;
        }
    }

    // Azul (safe) — tudo o resto que é um tipo conhecido e não-PvP
    let safe_prefixes = [
        "PLAYERCITY_SAFEAREA",
        "STARTINGCITY",
        "STARTAREA",
        "SAFEAREA",
        "TUTORIAL",
        "GUILDISLAND",
        "PLAYERISLAND",
        "SHOWROOMISLAND",
        "DUNGEON_SAFEAREA",
        "DUNGEON_YELLOW",
        "PASSAGE_SAFEAREA",
        "PASSAGE_YELLOW",
        "OPENPVP_YELLOW",
        "ARENA_",
        "CORRUPTED_DUNGEON",
        "TUNNEL_ROYAL",
        "TUNNEL_LOW",
        "TUNNEL_MEDIUM",
        "TUNNEL_HIGH",
        "TUNNEL_HIDEOUT",
        "HARDCORE_EXPEDITION",
    ];
    // Expeditions (T*_EXPEDITION_*)
    if t.contains("EXPEDITION") {
        return ZoneType::Blue;
    }
    for prefix in &safe_prefixes {
        if t.starts_with(prefix) {
            return ZoneType::Blue;
        }
    }

    ZoneType::Unknown
}

/// Lista de (cluster_id, display_name, cluster_type) extraída do world.json.
/// Pode ser usada pra lookup de nome → tipo quando a Fase 2.5 ler o mapa do jogo.
/// ponytail: gerado de ao-bin-dumps/cluster/world.json — 1421 clusters.
/// Não embedamos a lista completa aqui (seria ~50KB de constantes);
/// a classificação por tipo é suficiente. Quando o memory read chegar,
/// o companion vai ter o cluster_id e pode bater num hashmap local.

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_safe_zones() {
        assert!(matches!(classify_zone("PLAYERCITY_SAFEAREA_01"), ZoneType::Blue));
        assert!(matches!(classify_zone("STARTINGCITY"), ZoneType::Blue));
        assert!(matches!(classify_zone("SAFEAREA"), ZoneType::Blue));
        assert!(matches!(classify_zone("GUILDISLAND"), ZoneType::Blue));
        assert!(matches!(classify_zone("DUNGEON_YELLOW"), ZoneType::Blue));
        assert!(matches!(classify_zone("OPENPVP_YELLOW"), ZoneType::Blue));
        assert!(matches!(classify_zone("ARENA_STANDARD"), ZoneType::Blue));
        assert!(matches!(classify_zone("ARENA_CRYSTAL_NONLETHAL"), ZoneType::Blue));
        assert!(matches!(classify_zone("DUNGEON_HELL_2V2_NON_LETHAL"), ZoneType::Blue));
        assert!(matches!(classify_zone("TUNNEL_ROYAL"), ZoneType::Blue));
        assert!(matches!(classify_zone("T4_EXPEDITION_STANDARD"), ZoneType::Blue));
        assert!(matches!(classify_zone("CORRUPTED_DUNGEON_INTERMEDIATE"), ZoneType::Blue));
    }

    #[test]
    fn test_pvp_zones() {
        assert!(matches!(classify_zone("OPENPVP_BLACK_1"), ZoneType::PvP));
        assert!(matches!(classify_zone("OPENPVP_RED"), ZoneType::PvP));
        assert!(matches!(classify_zone("DUNGEON_BLACK_1"), ZoneType::PvP));
        assert!(matches!(classify_zone("DUNGEON_RED"), ZoneType::PvP));
        assert!(matches!(classify_zone("DUNGEON_HELL_2V2_LETHAL"), ZoneType::PvP));
        assert!(matches!(classify_zone("PASSAGE_BLACK"), ZoneType::PvP));
        assert!(matches!(classify_zone("PLAYERCITY_BLACK_ROYAL"), ZoneType::PvP));
        assert!(matches!(classify_zone("PLAYERCITY_HELLDEN"), ZoneType::PvP));
        assert!(matches!(classify_zone("TUNNEL_ROYAL_RED"), ZoneType::PvP));
        assert!(matches!(classify_zone("TUNNEL_DEEP"), ZoneType::PvP));
        assert!(matches!(classify_zone("TUNNEL_DEEP_RAID"), ZoneType::PvP));
        assert!(matches!(classify_zone("HIDEOUT"), ZoneType::PvP));
        assert!(matches!(classify_zone("ARENA_CRYSTAL"), ZoneType::PvP));
        assert!(matches!(classify_zone("ARENA_CRYSTAL_20VS20"), ZoneType::PvP));
    }

    #[test]
    fn test_unknown() {
        assert!(matches!(classify_zone(""), ZoneType::Unknown));
        assert!(matches!(classify_zone("DEBUG_BLACK"), ZoneType::Unknown));
    }
}