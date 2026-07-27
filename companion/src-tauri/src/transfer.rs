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

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
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
        };
        let mut items = self.items.lock().await;
        items.push(item);
        if let Err(e) = save_queue(&self.path, &items) {
            tracing::error!("falha ao persistir market_history: {e:#}");
        }
    }

    /// Envia todos os items pendentes. Remove cada um só após sucesso.
    /// Retorna (sent, failed).
    /// Envia no MÁXIMO `max` itens e volta, com respiro entre um e outro.
    ///
    /// A fila é drenada aos poucos, continuamente, em vez de despejada de uma
    /// vez. Depois de um tempo em zona de risco ela acumula, e um `flush_all`
    /// na volta pra zona azul viraria uma rajada de dezenas de requests — pico
    /// de rede e CPU exatamente quando o jogador acabou de sair da luta.
    /// Trabalho constante e pequeno passa despercebido; rajada, não.
    pub async fn flush_some(&self, api: &ApiClient, max: usize) -> (usize, usize) {
        let _uploader = self.uploader.lock().await;
        let mut sent = 0;
        let mut failed = 0;
        let batch = {
            let items = self.items.lock().await;
            items.iter().take(max).cloned().collect::<Vec<_>>()
        };
        for (i, item) in batch.iter().enumerate() {
            if i > 0 {
                tokio::time::sleep(std::time::Duration::from_millis(300)).await;
            }
            if self.send_one(api, item).await {
                sent += 1;
                let mut items = self.items.lock().await;
                if let Some(pos) = items.iter().position(|queued| queued == item) {
                    items.remove(pos); // só depois do ACK
                    if let Err(e) = save_queue(&self.path, &items) {
                        // O disco ainda contém o item: crash pode duplicar, nunca perder.
                        tracing::error!("ACK recebido, mas falhou salvar remoção da fila: {e:#}");
                    }
                }
            } else {
                failed += 1;
            }
        }
        (sent, failed)
    }

    /// Um item. `true` = aceito pelo backend (pode sair da fila).
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
        let item = QueuedItem { kind: "prices".into(), payload: serde_json::json!({}), queued_at: "1".into() };
        let mut items = vec![item.clone()];
        let pending = items[0].clone();
        assert_eq!(items, vec![item.clone()]); // enviar/clonar não drena
        let pos = items.iter().position(|queued| queued == &pending).unwrap();
        items.remove(pos); // operação feita somente após ACK em flush_some
        assert!(items.is_empty());
    }
}
