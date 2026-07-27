// Lootlog: o companion captura eventos de loot via packet sniffing (opcode
// 256 = OtherGrabbedLoot) e os acumula num buffer na sessão. Este módulo
// converte o buffer em CSV no formato lootlogger (compatível com o backend)
// e salva em arquivo quando o usuário pede o download.

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

/// A sessão continua após restart até o usuário usar "limpar". Isso preserva
/// o loot enquanto o CTA demora para entrar em review, sem inventar escopo novo.
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

/// Linha de loot no formato que o backend espera (mesmo schema do lootlogger).
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct LootRow {
    pub ts: Option<String>,
    pub item_id: String,
    /// Nome em inglês (o que vai pro CSV). pt/es são só pra UI escolher.
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

// ─── Tabela de itens (índice do pacote → id + nome) ──────────────────────────
// O índice vem do campo `Index` do ao-bin-dump — AUTORITATIVO, é o mesmo que o
// jogo usa no pacote (ao contrário dos feitiços, que dependem de hipótese de
// ordem). Baixada uma vez do backend e cacheada em disco.

static ITEMS: OnceLock<RwLock<HashMap<i32, crate::api::ItemName>>> = OnceLock::new();

fn items() -> &'static RwLock<HashMap<i32, crate::api::ItemName>> {
    ITEMS.get_or_init(|| RwLock::new(HashMap::new()))
}

fn item_cache_path() -> std::path::PathBuf {
    dirs::config_dir()
        .unwrap_or_else(|| ".".into())
        .join("ziggs-companion")
        // v2: o índice do catálogo mudou de items.json `Index` p/ items.txt
        // (índice de pacote real). Cache v1 mapeava o índice errado — o nome do
        // arquivo é bumpado pra abandonar o cache velho e forçar 1 refetch.
        .join("item_names_v2.json")
}

fn store(list: Vec<crate::api::ItemName>) {
    let map = list.into_iter().map(|it| (it.i, it)).collect();
    if let Ok(mut w) = items().write() {
        *w = map;
    }
}

/// Item resolvido: `(item_id, nome_en, nome_pt, nome_es)`. Índice desconhecido
/// (item novo que o dump ainda não tem) devolve o fallback `IDX_{n}` — mesma
/// convenção do backend, então nada se perde no ingest.
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

/// Carrega do cache em disco e, se vazio, baixa do backend — repetindo até
/// conseguir (mesmo motivo do `load_spell_names`: no autostart a rede não está
/// pronta, e uma tentativa única deixava o lootlog inteiro em `IDX_2958`).
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

/// Cabeçalho do CSV no formato ao-loot-logger (compatível com o backend Ziggs,
/// que casa por NOME de coluna e tolera extras/ordem — ver `parse_loot_rows`).
const CSV_HEADER: &str = "timestamp_utc;looted_by__alliance;looted_by__guild;looted_by__name;\
item_id;item_name;quantity;looted_from__alliance;looted_from__guild;looted_from__name";

/// Converte o buffer de LootEvents capturados em CSV no formato lootlogger.
///
/// `item_name` sai em INGLÊS de propósito: o CSV é interoperável (vai pro site
/// da guilda, é comparado com log de outra pessoa que pode estar em outro
/// idioma). A tradução é coisa de UI, não de arquivo.
pub fn build_csv_from_loot(events: &[LootEvent]) -> String {
    let mut lines = Vec::with_capacity(events.len() + 1);
    lines.push(CSV_HEADER.to_string());
    for e in events {
        // Guild/alliance não vêm no pacote de loot — ficam vazios e o backend
        // reconcilia pelo nome do looter.
        let (item_id, item_name, _, _) = resolve(e.item_index);
        lines.push(format!(
            "{};{};{};{};{};{};{};{};{};{}",
            e.ts, "", "", e.looted_by, item_id, item_name, e.quantity, "", "", e.looted_from
        ));
    }
    lines.join("\n")
}

/// Salva o texto CSV na pasta Downloads do usuário.
pub fn save_csv(csv_text: &str) -> std::io::Result<String> {
    // Downloads é onde o usuário procura um arquivo que ele mandou baixar.
    // Sem subpasta: um arquivo avulso com nome datado não bagunça nada, e
    // enterrar em ziggs-companion/ só fazia o usuário caçar.
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

    /// Um teste só: a tabela é global, dois testes mexendo nela em paralelo
    /// dariam flake.
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

        // O backend casa por NOME de coluna (parse_loot_rows); estas são as
        // que ele exige. Renomear qualquer uma quebra o ingest em silêncio.
        let cols: Vec<&str> = lines[0].split(';').collect();
        for req in ["timestamp_utc", "looted_by__guild", "looted_by__name",
                    "item_id", "quantity", "looted_from__name"] {
            assert!(cols.contains(&req), "coluna {req} sumiu do header");
        }
        // Toda linha tem que ter exatamente as colunas do header, senão o
        // parser desalinha os campos.
        for l in &lines[1..] {
            assert_eq!(l.split(';').count(), cols.len(), "linha desalinhada: {l}");
        }

        let f: Vec<&str> = lines[1].split(';').collect();
        assert_eq!(f[cols.iter().position(|c| *c == "item_id").unwrap()], "T7_HEAD_PLATE_SET3@1");
        assert_eq!(
            f[cols.iter().position(|c| *c == "item_name").unwrap()],
            "Grandmaster's Guardian Helmet",
            "CSV vai em inglês, não traduzido"
        );

        // Índice fora do dump não pode virar linha vazia — o backend precisa
        // de item_id preenchido pra não descartar a coleta.
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
