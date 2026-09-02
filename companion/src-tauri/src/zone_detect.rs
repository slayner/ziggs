// Classify Albion cluster types into blue/PvP zones.
// Source: ao-bin-dumps/cluster/world.json `@type` field.
// Phase 1 uses manual set_zone; phase 2.5 will read the live map.

use crate::transfer::ZoneType;

/// Classify a cluster type string into a blue/PvP/unknown zone.
pub fn classify_zone(cluster_type: &str) -> ZoneType {
    let t = cluster_type.trim();
    if t.is_empty() {
        return ZoneType::Unknown;
    }

    // DUNGEON_HELL: non-lethal = blue, lethal = PvP. Check before the PvP prefix
    // because "DUNGEON_HELL_" matches there.
    if t.starts_with("DUNGEON_HELL") {
        if t.contains("NON_LETHAL") {
            return ZoneType::Blue;
        }
        return ZoneType::PvP;
    }

    // ARENA_CRYSTAL: NONLETHAL = blue, otherwise PvP
    if t.starts_with("ARENA_CRYSTAL") {
        if t.contains("NONLETHAL") || t.contains("NON_LETHAL") {
            return ZoneType::Blue;
        }
        return ZoneType::PvP;
    }

    // DRAGON_AREA: ORANGE = blue (non-lethal), BLACK = PvP (full-loot)
    if t.starts_with("DRAGON_AREA") || t.starts_with("DRAGON-ISLANDS") {
        if t.contains("BLACK") {
            return ZoneType::PvP;
        }
        return ZoneType::Blue;
    }

    // PvP (lethal) — full-loot or otherwise dangerous
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
    ];
    for prefix in &pvp_prefixes {
        if t.starts_with(prefix) {
            return ZoneType::PvP;
        }
    }

    // Blue (safe) — known non-PvP types
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
    // All expedition types are safe.
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_safe_zones() {
        assert!(matches!(
            classify_zone("PLAYERCITY_SAFEAREA_01"),
            ZoneType::Blue
        ));
        assert!(matches!(classify_zone("STARTINGCITY"), ZoneType::Blue));
        assert!(matches!(classify_zone("SAFEAREA"), ZoneType::Blue));
        assert!(matches!(classify_zone("GUILDISLAND"), ZoneType::Blue));
        assert!(matches!(classify_zone("DUNGEON_YELLOW"), ZoneType::Blue));
        assert!(matches!(classify_zone("OPENPVP_YELLOW"), ZoneType::Blue));
        assert!(matches!(classify_zone("ARENA_STANDARD"), ZoneType::Blue));
        assert!(matches!(
            classify_zone("ARENA_CRYSTAL_NONLETHAL"),
            ZoneType::Blue
        ));
        assert!(matches!(
            classify_zone("DUNGEON_HELL_2V2_NON_LETHAL"),
            ZoneType::Blue
        ));
        assert!(matches!(classify_zone("TUNNEL_ROYAL"), ZoneType::Blue));
        assert!(matches!(
            classify_zone("T4_EXPEDITION_STANDARD"),
            ZoneType::Blue
        ));
        assert!(matches!(
            classify_zone("CORRUPTED_DUNGEON_INTERMEDIATE"),
            ZoneType::Blue
        ));
        assert!(matches!(
            classify_zone("DRAGON_AREA_1_TO_1_ORANGE"),
            ZoneType::Blue
        ));
        assert!(matches!(
            classify_zone("DRAGON-ISLANDS-001"),
            ZoneType::Blue
        ));
    }

    #[test]
    fn test_pvp_zones() {
        assert!(matches!(classify_zone("OPENPVP_BLACK_1"), ZoneType::PvP));
        assert!(matches!(classify_zone("OPENPVP_RED"), ZoneType::PvP));
        assert!(matches!(classify_zone("DUNGEON_BLACK_1"), ZoneType::PvP));
        assert!(matches!(classify_zone("DUNGEON_RED"), ZoneType::PvP));
        assert!(matches!(
            classify_zone("DUNGEON_HELL_2V2_LETHAL"),
            ZoneType::PvP
        ));
        assert!(matches!(classify_zone("PASSAGE_BLACK"), ZoneType::PvP));
        assert!(matches!(
            classify_zone("PLAYERCITY_BLACK_ROYAL"),
            ZoneType::PvP
        ));
        assert!(matches!(classify_zone("PLAYERCITY_HELLDEN"), ZoneType::PvP));
        assert!(matches!(classify_zone("TUNNEL_ROYAL_RED"), ZoneType::PvP));
        assert!(matches!(classify_zone("TUNNEL_DEEP"), ZoneType::PvP));
        assert!(matches!(classify_zone("TUNNEL_DEEP_RAID"), ZoneType::PvP));
        assert!(matches!(classify_zone("HIDEOUT"), ZoneType::PvP));
        assert!(matches!(classify_zone("ARENA_CRYSTAL"), ZoneType::PvP));
        assert!(matches!(
            classify_zone("ARENA_CRYSTAL_20VS20"),
            ZoneType::PvP
        ));
        assert!(matches!(
            classify_zone("DRAGON_AREA_1_TO_1_BLACK"),
            ZoneType::PvP
        ));
        assert!(matches!(
            classify_zone("DRAGON_AREA_15_TO_20_BLACK"),
            ZoneType::PvP
        ));
    }

    #[test]
    fn test_unknown() {
        assert!(matches!(classify_zone(""), ZoneType::Unknown));
        assert!(matches!(classify_zone("DEBUG_BLACK"), ZoneType::Unknown));
    }
}
