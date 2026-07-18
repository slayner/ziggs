// Resolve o índice de cluster que o Albion envia (ex: "0000") pro nome legível
// ("Thetford"). Tabela gerada de ao-bin-dumps/cluster/world.json (@id → @displayname).

use std::collections::HashMap;
use std::sync::OnceLock;

static MAP_NAMES: OnceLock<HashMap<String, String>> = OnceLock::new();

fn table() -> &'static HashMap<String, String> {
    MAP_NAMES.get_or_init(|| {
        serde_json::from_str(include_str!("map_names.json")).unwrap_or_default()
    })
}

/// Nome legível do mapa a partir do índice cru. O jogo às vezes manda sufixo
/// "@..." (hideouts/instâncias) — cortamos antes de buscar. Sem match, devolve
/// o índice cru pra não sumir com a info.
pub fn resolve(raw_index: &str) -> String {
    let key = raw_index.split('@').next().unwrap_or(raw_index);
    table().get(key).cloned().unwrap_or_else(|| raw_index.to_string())
}

#[cfg(test)]
mod tests {
    use super::resolve;

    #[test]
    fn known_cities() {
        assert_eq!(resolve("0000"), "Thetford");
        assert_eq!(resolve("3004"), "Martlock");
        assert_eq!(resolve("0000@abc"), "Thetford"); // sufixo cortado
    }

    #[test]
    fn unknown_falls_back_to_raw() {
        assert_eq!(resolve("ZZZ-nope"), "ZZZ-nope");
    }
}
