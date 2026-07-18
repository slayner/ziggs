// Lootlog: o companion captura eventos de loot via packet sniffing (opcode
// 256 = OtherGrabbedLoot) e os acumula num buffer na sessão. Este módulo
// converte o buffer em CSV no formato lootlogger (compatível com o backend)
// e salva em arquivo quando o usuário pede o download.

use serde::{Deserialize, Serialize};

use crate::photon_parser::LootEvent;

#[derive(Clone, Debug, Serialize, Deserialize, Default)]
pub struct LootlogStatus {
    pub parsed_count: u64,
    pub last_parsed_at: Option<String>,
    pub last_rows: u64,
    pub last_saved_path: Option<String>,
}

/// Linha de loot no formato que o backend espera (mesmo schema do lootlogger).
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LootRow {
    pub ts: Option<String>,
    pub item_id: String,
    pub item_name: String,
    pub quantity: i64,
    pub looted_by: String,
    pub looted_by_guild: String,
    pub looted_from: String,
}

/// Cabeçalho do CSV no formato lootlogger (compatível com o backend Ziggs).
const CSV_HEADER: &str = "timestamp_utc;looted_by__guild;looted_by__name;item_id;item_name;quantity;looted_from__name";

/// Converte o buffer de LootEvents capturados em CSV no formato lootlogger.
// ponytail: item_id do Albion é numérico (itemIndex); o backend aceita tanto
// o ID numérico quanto o string formatado (T4_BAG etc). O lootlogger envia o
// número como item_id, então fazemos o mesmo.
pub fn build_csv_from_loot(events: &[LootEvent]) -> String {
    let mut lines = Vec::with_capacity(events.len() + 1);
    lines.push(CSV_HEADER.to_string());
    for e in events {
        // ponytail: guild não vem no pacote de loot — fica vazio, o backend
        // reconcilia por nome do looter. item_name também não vem no pacote.
        lines.push(format!(
            "{};{};{};{};{};{};{}",
            e.ts, "", e.looted_by, e.item_index, "", e.quantity, e.looted_from
        ));
    }
    lines.join("\n")
}

/// Salva o texto CSV num arquivo na pasta Documents do usuário.
// ponytail: Documents é o lugar óbvio pra um export — sem config extra.
pub fn save_csv(csv_text: &str) -> std::io::Result<String> {
    let dir = dirs::document_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join("ziggs-companion");
    std::fs::create_dir_all(&dir)?;
    let ts = {
        use std::time::{SystemTime, UNIX_EPOCH};
        SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs()
    };
    let path = dir.join(format!("lootlog-{}.csv", ts));
    std::fs::write(&path, csv_text)?;
    Ok(path.to_string_lossy().to_string())
}