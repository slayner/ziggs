// Fila de transferência com persistência em disco.
//
// Quando o jogador está em zona PvP, NÃO envia dados ao backend — acumula local.
// Quando entra em zona azul, faz flush de tudo (mesmo que velho — dados antigos
// ainda útil pra histórico). Se fechar o programa em PvP, a fila persiste em
// JSON e volta no próximo startup.
//
// Sem memory read (Fase 1): zona é informada pelo usuário via toggle.
// Fase 2.5 (memory read): substituir toggle por detecção automática do mapa.

use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::Mutex;
use serde::{Deserialize, Serialize};
use anyhow::Result;

use crate::api::{ApiClient, ScanReportIn};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum ZoneType {
    Blue,
    PvP,
    Unknown,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct QueuedItem {
    pub kind: String,       // "scan_report" | "prices"
    pub payload: serde_json::Value,
    pub queued_at: String,  // ISO timestamp
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct QueueFile {
    pub items: Vec<QueuedItem>,
}

pub struct TransferQueue {
    items: Arc<Mutex<Vec<QueuedItem>>>,
    path: PathBuf,
}

impl TransferQueue {
    pub fn new() -> Self {
        let path = queue_path();
        let items = load_queue(&path).unwrap_or_default();
        Self {
            items: Arc::new(Mutex::new(items)),
            path,
        }
    }

    pub async fn pending_count(&self) -> usize {
        self.items.lock().await.len()
    }

    pub async fn enqueue_scan_report(&self, report: ScanReportIn) {
        let item = QueuedItem {
            kind: "scan_report".into(),
            payload: serde_json::to_value(&report).unwrap_or(serde_json::Value::Null),
            queued_at: iso_now(),
        };
        let mut items = self.items.lock().await;
        items.push(item);
        let _ = save_queue(&self.path, &items);
    }

    pub async fn enqueue_prices(&self, rows: Vec<serde_json::Value>) {
        let item = QueuedItem {
            kind: "prices".into(),
            payload: serde_json::json!({ "rows": rows }),
            queued_at: iso_now(),
        };
        let mut items = self.items.lock().await;
        items.push(item);
        let _ = save_queue(&self.path, &items);
    }

    pub async fn enqueue_market_history(&self, rows: Vec<serde_json::Value>) {
        let item = QueuedItem {
            kind: "market_history".into(),
            payload: serde_json::json!({ "rows": rows }),
            queued_at: iso_now(),
        };
        let mut items = self.items.lock().await;
        items.push(item);
        let _ = save_queue(&self.path, &items);
    }

    /// Envia todos os items pendentes. Remove cada um só após sucesso.
    /// Retorna (sent, failed).
    pub async fn flush_all(&self, api: &ApiClient) -> (usize, usize) {
        let mut items = self.items.lock().await;
        if items.is_empty() {
            return (0, 0);
        }

        let mut sent = 0;
        let mut failed = 0;
        let mut remaining = Vec::new();

        for item in items.drain(..) {
            let ok = match item.kind.as_str() {
                "scan_report" => {
                    match serde_json::from_value::<ScanReportIn>(item.payload.clone()) {
                        Ok(report) => api.report_scan(&report).await.is_ok(),
                        Err(_) => false,
                    }
                }
                "prices" => {
                    let rows = item.payload.get("rows")
                        .and_then(|v| v.as_array())
                        .cloned()
                        .unwrap_or_default();
                    api.submit_prices(&rows).await.is_ok()
                }
                "market_history" => {
                    let rows = item.payload.get("rows")
                        .and_then(|v| v.as_array())
                        .cloned()
                        .unwrap_or_default();
                    api.submit_market_history(&rows).await.is_ok()
                }
                _ => false,
            };
            if ok {
                sent += 1;
            } else {
                failed += 1;
                remaining.push(item);
            }
        }

        *items = remaining;
        let _ = save_queue(&self.path, &items);
        (sent, failed)
    }
}

fn queue_path() -> PathBuf {
    let dir = dirs::config_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("ziggs-companion");
    let _ = std::fs::create_dir_all(&dir);
    dir.join("transfer_queue.json")
}

fn load_queue(path: &PathBuf) -> Result<Vec<QueuedItem>> {
    let bytes = std::fs::read(path)?;
    let file: QueueFile = serde_json::from_slice(&bytes)?;
    Ok(file.items)
}

fn save_queue(path: &PathBuf, items: &[QueuedItem]) -> Result<()> {
    let file = QueueFile { items: items.to_vec() };
    let bytes = serde_json::to_vec_pretty(&file)?;
    std::fs::write(path, bytes)?;
    Ok(())
}

fn iso_now() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
    format!("{}", secs)
}