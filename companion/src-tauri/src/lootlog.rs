// Lootlog: captures OtherGrabbedLoot events via packet sniffing and accumulates
// them in a session buffer. Converts the buffer to ao-loot-logger CSV and saves
// to a file on user request.

use std::collections::HashMap;
use std::sync::{OnceLock, RwLock};

use serde::{Deserialize, Serialize};

use crate::photon_parser::LootEvent;

fn session_path() -> std::path::PathBuf {
    dirs::config_dir()
        .unwrap_or_else(|| ".".into())
        .join("ziggs-companion")
        .join("loot_session.json")
}

// Session persists across restarts until the user clears it.
pub fn load_session() -> anyhow::Result<Vec<LootEvent>> {
    match std::fs::read(session_path()) {
        Ok(bytes) => Ok(serde_json::from_slice(&bytes)?),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Vec::new()),
        Err(e) => Err(e.into()),
    }
}

pub fn save_session(events: &[LootEvent]) -> anyhow::Result<()> {
    let bytes = serde_json::to_vec(events)?;
    crate::persist::atomic_write(&session_path(), &bytes)?;
    Ok(())
}

#[derive(Clone, Debug, Serialize, Deserialize, Default)]
pub struct LootlogStatus {
    pub parsed_count: u64,
    pub last_parsed_at: Option<String>,
    pub last_rows: u64,
    pub last_saved_path: Option<String>,
}

// Loot row matching the backend's expected ao-loot-logger schema.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LootRow {
    pub ts: Option<String>,
    pub item_id: String,
    // English name goes into the CSV; UI picks localized name separately.
    pub item_name: String,
    #[serde(default)]
    pub item_name_pt: String,
    #[serde(default)]
    pub item_name_es: String,
    pub quantity: i64,
    pub looted_by: String,
    pub looted_by_guild: String,
    pub looted_from: String,
}

// Item table: packet index → id + localized names. Index matches the ao-bin-dump
// packet catalog; downloaded once from the backend and cached locally.

static ITEMS: OnceLock<RwLock<HashMap<i32, crate::api::ItemName>>> = OnceLock::new();

fn items() -> &'static RwLock<HashMap<i32, crate::api::ItemName>> {
    ITEMS.get_or_init(|| RwLock::new(HashMap::new()))
}

fn item_cache_path() -> std::path::PathBuf {
    dirs::config_dir()
        .unwrap_or_else(|| ".".into())
        .join("ziggs-companion")
        // v2: switched to the real packet index. Bumping the filename invalidates
        // the old cache and forces one refetch.
        .join("item_names_v2.json")
}

fn store(list: Vec<crate::api::ItemName>) {
    let map = list.into_iter().map(|it| (it.i, it)).collect();
    if let Ok(mut w) = items().write() {
        *w = map;
    }
}

// Resolve index to (item_id, en, pt, es). Unknown indexes fall back to `IDX_{n}`,
// matching the backend convention so nothing is dropped on ingest.
pub fn resolve(index: i32) -> (String, String, String, String) {
    if let Ok(r) = items().read() {
        if let Some(it) = r.get(&index) {
            let en = it.en.clone();
            let pt = it.pt.clone().unwrap_or_else(|| en.clone());
            let es = it.es.clone().unwrap_or_else(|| en.clone());
            return (it.id.clone(), en, pt, es);
        }
    }
    let fallback = format!("IDX_{}", index);
    (fallback.clone(), fallback.clone(), fallback.clone(), fallback)
}

// Load from disk cache, or download from the backend on a 60s retry loop. A single
// failure at boot would leave every entry as `IDX_n`.
pub async fn load_item_names() {
    if let Ok(bytes) = std::fs::read(item_cache_path()) {
        if let Ok(v) = serde_json::from_slice::<Vec<crate::api::ItemName>>(&bytes) {
            if !v.is_empty() {
                store(v);
                return;
            }
        }
    }
    let api = crate::api::ApiClient::new(crate::config::API_BASE_URL);
    loop {
        match api.items().await {
            Ok(v) if !v.is_empty() => {
                if let Ok(bytes) = serde_json::to_vec(&v) {
                    let _ = std::fs::write(item_cache_path(), bytes);
                }
                store(v);
                return;
            }
            Ok(_) => {
                tracing::info!("catálogo de itens vazio no backend");
                return;
            }
            Err(e) => tracing::warn!("catálogo de itens falhou, de novo em 60s: {e:#}"),
        }
        tokio::time::sleep(std::time::Duration::from_secs(60)).await;
    }
}

// ao-loot-logger CSV header. The backend matches by column name and tolerates
// extra columns and any order.
const CSV_HEADER: &str = "timestamp_utc;looted_by__alliance;looted_by__guild;looted_by__name;\
item_id;item_name;quantity;looted_from__alliance;looted_from__guild;looted_from__name";

// Convert captured LootEvents into ao-loot-logger CSV. Item names stay in English
// so the file stays interoperable across clients in different languages.
pub fn build_csv_from_loot(events: &[LootEvent]) -> String {
    let mut lines = Vec::with_capacity(events.len() + 1);
    lines.push(CSV_HEADER.to_string());
    for e in events {
        // Guild/alliance are not present in the loot packet; backend reconciles by looter name.
        let (item_id, item_name, _, _) = resolve(e.item_index);
        lines.push(format!(
            "{};{};{};{};{};{};{};{};{};{}",
            e.ts, "", "", e.looted_by, item_id, item_name, e.quantity, "", "", e.looted_from
        ));
    }
    lines.join("\n")
}

// Save CSV text to the user's Downloads folder.
pub fn save_csv(csv_text: &str) -> std::io::Result<String> {
    // Use a dated file directly in Downloads for easy discovery.
    let dir = dirs::download_dir()
        .or_else(dirs::document_dir)
        .unwrap_or_else(|| std::path::PathBuf::from("."));
    std::fs::create_dir_all(&dir)?;
    let ts = {
        use std::time::{SystemTime, UNIX_EPOCH};
        SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs()
    };
    let path = dir.join(format!("lootlog-{}.csv", ts));
    std::fs::write(&path, csv_text)?;
    Ok(path.to_string_lossy().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ev(item_index: i32) -> LootEvent {
        LootEvent {
            ts: "2026-07-18T12:00:00Z".into(),
            looted_by: "Zezinho".into(),
            looted_from: "Fulano".into(),
            item_index,
            quantity: 2,
            is_silver: false,
        }
    }

    // Single test because the global item table would flake under parallel access.
    #[test]
    fn csv_traz_id_e_nome_e_bate_com_o_parser_do_backend() {
        store(vec![crate::api::ItemName {
            i: 2958,
            id: "T7_HEAD_PLATE_SET3@1".into(),
            en: "Grandmaster's Guardian Helmet".into(),
            pt: Some("Elmo de Guardião do Grão-mestre".into()),
            es: None,
        }]);

        let csv = build_csv_from_loot(&[ev(2958), ev(999999)]);
        let lines: Vec<&str> = csv.lines().collect();

        // These columns are required by the backend's column-name parser.
        let cols: Vec<&str> = lines[0].split(';').collect();
        for req in ["timestamp_utc", "looted_by__guild", "looted_by__name",
                    "item_id", "quantity", "looted_from__name"] {
            assert!(cols.contains(&req), "coluna {req} sumiu do header");
        }
        // Each row must align with the header column count.
        for l in &lines[1..] {
            assert_eq!(l.split(';').count(), cols.len(), "linha desalinhada: {l}");
        }

        let f: Vec<&str> = lines[1].split(';').collect();
        assert_eq!(f[cols.iter().position(|c| *c == "item_id").unwrap()], "T7_HEAD_PLATE_SET3@1");
        assert_eq!(
            f[cols.iter().position(|c| *c == "item_name").unwrap()],
            "Grandmaster's Guardian Helmet",
            "CSV must stay in English"
        );

        // Unknown index must still emit an item_id so the backend keeps the row.
        assert!(lines[2].contains("IDX_999999"));
    }

    #[test]
    fn loot_session_roundtrip() {
        let path = std::env::temp_dir().join(format!("ziggs-loot-session-{}.json", std::process::id()));
        let events = vec![ev(2958)];
        let bytes = serde_json::to_vec(&events).unwrap();
        crate::persist::atomic_write(&path, &bytes).unwrap();
        let loaded: Vec<LootEvent> = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        assert_eq!(loaded.len(), 1);
        assert_eq!(loaded[0].item_index, 2958);
        let _ = std::fs::remove_file(path);
    }
}
