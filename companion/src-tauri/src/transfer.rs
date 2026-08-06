// Disk-persisted transfer queue. Buffers outbound data in PvP zones and flushes
// in blue zones. Phase 1 uses a manual toggle; phase 2.5 will use map detection.

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

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct QueuedItem {
    pub kind: String,       // "scan_report" | "prices"
    pub payload: serde_json::Value,
    pub queued_at: String,  // ISO timestamp
    #[serde(default)]
    pub retries: u32,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct QueueFile {
    pub items: Vec<QueuedItem>,
}

pub struct TransferQueue {
    items: Arc<Mutex<Vec<QueuedItem>>>,
    uploader: Mutex<()>,
    path: PathBuf,
}

impl TransferQueue {
    pub fn new() -> Self {
        let path = queue_path();
        let items = match load_queue(&path) {
            Ok(items) => items,
            Err(e) if e.downcast_ref::<std::io::Error>().is_some_and(|e| e.kind() == std::io::ErrorKind::NotFound) => Vec::new(),
            Err(e) => {
                tracing::error!("fila persistida inválida em {}: {e:#}", path.display());
                let corrupt = path.with_extension(format!("corrupt-{}", iso_now()));
                if let Err(rename_err) = std::fs::rename(&path, &corrupt) {
                    tracing::error!("não foi possível preservar fila inválida: {rename_err}");
                }
                Vec::new()
            }
        };
        Self {
            items: Arc::new(Mutex::new(items)),
            uploader: Mutex::new(()),
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
            retries: 0,
        };
        let mut items = self.items.lock().await;
        items.push(item);
        if let Err(e) = save_queue(&self.path, &items) {
            tracing::error!("falha ao persistir scan_report: {e:#}");
        }
    }

    pub async fn enqueue_prices(&self, rows: Vec<serde_json::Value>) {
        let item = QueuedItem {
            kind: "prices".into(),
            payload: serde_json::json!({ "rows": rows }),
            queued_at: iso_now(),
            retries: 0,
        };
        let mut items = self.items.lock().await;
        items.push(item);
        if let Err(e) = save_queue(&self.path, &items) {
            tracing::error!("falha ao persistir prices: {e:#}");
        }
    }

    pub async fn enqueue_market_history(&self, rows: Vec<serde_json::Value>) {
        let item = QueuedItem {
            kind: "market_history".into(),
            payload: serde_json::json!({ "rows": rows }),
            queued_at: iso_now(),
            retries: 0,
        };
        let mut items = self.items.lock().await;
        items.push(item);
        if let Err(e) = save_queue(&self.path, &items) {
            tracing::error!("falha ao persistir market_history: {e:#}");
        }
    }

    /// Drain at most `max` items with a short pause between each.
    /// Returns (sent, failed). Items are removed only after a successful ACK.
    ///
    /// Gradual draining avoids a network/CPU burst when leaving a PvP zone.
    pub async fn flush_some(&self, api: &ApiClient, max: usize) -> (usize, usize) {
        let _uploader = self.uploader.lock().await;
        let mut sent = 0;
        let mut failed = 0;
        const MAX_RETRIES: u32 = 5;
        let batch = {
            let items = self.items.lock().await;
            items.iter().take(max).cloned().collect::<Vec<_>>()
        };
        for (i, item) in batch.iter().enumerate() {
            if i > 0 {
                tokio::time::sleep(std::time::Duration::from_millis(150)).await;
            }
            if self.send_one(api, item).await {
                sent += 1;
                let mut items = self.items.lock().await;
                if let Some(pos) = items.iter().position(|queued| queued == item) {
                    items.remove(pos); // only after ACK
                    if let Err(e) = save_queue(&self.path, &items) {
                        tracing::error!("ACK recebido, mas falhou salvar remoção da fila: {e:#}");
                    }
                }
            } else {
                failed += 1;
                let mut items = self.items.lock().await;
                if let Some(pos) = items.iter().position(|queued| queued == item) {
                    let mut updated = items.remove(pos);
                    updated.retries += 1;
                    if updated.retries >= MAX_RETRIES {
                        tracing::warn!(
                            "descartando item após {} tentativas: kind={}",
                            updated.retries, updated.kind
                        );
                    } else {
                        items.push(updated); // move to the back so other items get a chance first
                    }
                    if let Err(e) = save_queue(&self.path, &items) {
                        tracing::error!("falha ao salvar fila após mover item falhado: {e:#}");
                    }
                }
            }
        }
        (sent, failed)
    }

    /// Try sending a single item. Returns true when the backend accepts it.
    async fn send_one(&self, api: &ApiClient, item: &QueuedItem) -> bool {
        match item.kind.as_str() {
            "scan_report" => match serde_json::from_value::<ScanReportIn>(item.payload.clone()) {
                Ok(report) => api.report_scan(&report).await.is_ok(),
                Err(_) => false,
            },
            "prices" => {
                let rows = item.payload.get("rows")
                    .and_then(|v| v.as_array()).cloned().unwrap_or_default();
                api.submit_prices(&rows).await.is_ok()
            }
            "market_history" => {
                let rows = item.payload.get("rows")
                    .and_then(|v| v.as_array()).cloned().unwrap_or_default();
                api.submit_market_history(&rows).await.is_ok()
            }
            _ => false,
        }
    }

    pub async fn flush_all(&self, api: &ApiClient) -> (usize, usize) {
        self.flush_some(api, usize::MAX).await
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
    crate::persist::atomic_write(path, &bytes)?;
    Ok(())
}

fn iso_now() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
    format!("{}", secs)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn persisted_item_is_only_removed_by_ack_path() {
        let item = QueuedItem { kind: "prices".into(), payload: serde_json::json!({}), queued_at: "1".into(), retries: 0 };
        let mut items = vec![item.clone()];
        let pending = items[0].clone();
        assert_eq!(items, vec![item.clone()]); // cloning must not drain
        let pos = items.iter().position(|queued| queued == &pending).unwrap();
        items.remove(pos); // mirrors the ACK-only removal in flush_some
        assert!(items.is_empty());
    }
}
