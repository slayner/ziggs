// Battle scanner worker: pega ranges distribuídos pelo backend, sonda a API
// pública do Albion e reporta resultados. N clients = N IPs = rate limit
// distribuído — o ponto central do plano.
//
// Zone-aware: se o jogador estiver em zona PvP (e pvp_pause_transfer=true),
// o report vai pra fila local em vez do backend. Flush quando voltar pra zona azul.
//
// Throttle adaptativo: o delay entre sondagens sobe/desce conforme a API do
// Albion responde. 429 → recua multiplicativo (×2, teto 5s). 200 sustentado →
// recupera aditivo (-50ms por sondagem, piso 150ms). Mesma filosofia AIMD do
// albion_gate no backend, mas no client — porque o companion fala direto com
// a API pública, não passa pelo rate limiter do servidor.

use std::time::Duration;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use anyhow::Result;
use serde::{Deserialize, Serialize};
use tokio::sync::Mutex;
use std::sync::Arc;

use crate::api::{ApiClient, ScanClaim, ScanReportIn};
use crate::transfer::TransferQueue;

/// Delay entre sondagens — adaptativo. Começa em 150ms (cortesia mínima).
/// 429 dobra (teto 5s). 200 sustentado recupera -50ms por sondagem (piso 150ms).
/// Atômico pra não precisar de lock no hot loop do scan.
const THROTTLE_MIN_MS: u64 = 150;
const THROTTLE_MAX_MS: u64 = 5_000;
const THROTTLE_START_MS: u64 = 150;
const THROTTLE_RECOVER_MS: u64 = 50;  // -50ms por 200 sustentado

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ScanStats {
    pub status: String,             // "idle" | "running" | "error" | "disabled"
    pub last_cycle_at: Option<String>,
    pub battles_found: u64,
    pub battles_missing: u64,
    pub battles_errors: u64,
    pub cycles: u64,
    pub last_error: Option<String>,
    pub queued_reports: usize,
    pub zone: String,               // "blue" | "pvp" | "unknown"
    pub throttle_ms: u64,           // delay adaptativo entre sondagens (transparência)
}

impl Default for ScanStats {
    fn default() -> Self {
        Self {
            status: "idle".into(),
            last_cycle_at: None,
            battles_found: 0,
            battles_missing: 0,
            battles_errors: 0,
            cycles: 0,
            last_error: None,
            queued_reports: 0,
            zone: "blue".into(),
            throttle_ms: THROTTLE_START_MS,
        }
    }
}

pub struct Scanner {
    pub stats: Arc<Mutex<ScanStats>>,
    shutdown: Arc<AtomicBool>,
    /// Zona atual — lida por commands da UI (set_zone). Default: blue (envia direto).
    pub zone: Arc<Mutex<crate::transfer::ZoneType>>,
    /// Fila de transferência — compartilhada com AppState.
    pub queue: Option<Arc<TransferQueue>>,
    /// Se true, pausa transferência em zona PvP. Mutável via set_config.
    pub pvp_pause: Arc<Mutex<bool>>,
    /// Nick do jogador — vai no report pro backend creditar batalhas novas
    /// (agradecimento na página pública). Mutável via set_config.
    pub character_name: Arc<Mutex<Option<String>>>,
    /// Delay entre sondagens (ms), adaptativo. 429 dobra, 200 sustentado recupera.
    /// Atômico: lido/escrito no hot loop do cycle sem lock.
    pub throttle_ms: Arc<AtomicU64>,
}

impl Scanner {
    pub fn new() -> Self {
        Self {
            stats: Arc::new(Mutex::new(ScanStats::default())),
            shutdown: Arc::new(AtomicBool::new(false)),
            zone: Arc::new(Mutex::new(crate::transfer::ZoneType::Blue)),
            queue: None,
            pvp_pause: Arc::new(Mutex::new(true)),
            character_name: Arc::new(Mutex::new(None)),
            throttle_ms: Arc::new(AtomicU64::new(THROTTLE_START_MS)),
        }
    }

    pub fn with_queue(mut self, queue: Arc<TransferQueue>) -> Self {
        self.queue = Some(queue);
        self
    }

    /// Clona os Arcs internos pra criar um scanner que roda numa task separada
    /// mas compartilha stats/zone/queue/pvp_pause/throttle com o original.
    pub fn clone_for_spawn(&self) -> Scanner {
        Scanner {
            stats: Arc::clone(&self.stats),
            shutdown: Arc::clone(&self.shutdown),
            zone: Arc::clone(&self.zone),
            queue: self.queue.as_ref().map(Arc::clone),
            pvp_pause: Arc::clone(&self.pvp_pause),
            character_name: Arc::clone(&self.character_name),
            throttle_ms: Arc::clone(&self.throttle_ms),
        }
    }

    pub async fn stop(&self) {
        self.shutdown.store(true, Ordering::Relaxed);
    }

    pub fn prepare_start(&self) {
        self.shutdown.store(false, Ordering::Relaxed);
    }

    /// Define a zona atual. Se mudar de PvP→Blue, dispara flush da fila.
    pub async fn set_zone(&self, zone: crate::transfer::ZoneType, _api: &ApiClient) {
        let was_pvp = matches!(self.zone.lock().await.clone(), crate::transfer::ZoneType::PvP);
        *self.zone.lock().await = zone.clone();
        let zone_str = match zone {
            crate::transfer::ZoneType::Blue => "blue",
            crate::transfer::ZoneType::PvP => "pvp",
            crate::transfer::ZoneType::Unknown => "unknown",
        };
        self.stats.lock().await.zone = zone_str.to_string();

        // Voltou pra zona azul: só atualiza o contador. O envio é do uploader
        // único, aos poucos — despejar a fila inteira aqui era uma rajada de
        // rede logo depois da luta, o pior momento possível.
        if was_pvp && matches!(zone, crate::transfer::ZoneType::Blue) {
            if let Some(q) = &self.queue {
                self.stats.lock().await.queued_reports = q.pending_count().await;
            }
        }
    }

    /// Loop principal: claim → scan → report → sleep → repete.
    /// Para quando `stop()` ou erro persistente de conexão.
    pub async fn run(&self, api: ApiClient, enabled: bool) {
        if !enabled {
            self.stats.lock().await.status = "disabled".into();
            return;
        }
        {
            let mut s = self.stats.lock().await;
            s.status = "running".into();
            s.last_error = None;
        }

        loop {
            if self.shutdown.load(Ordering::Relaxed) {
                self.stats.lock().await.status = "idle".into();
                break;
            }
            // Em zona PvP com pause ligado: não claima novas tarefas — só
            // acumula local. Dorme 30s (economia de CPU/rede pro jogador
            // que está em combate). Retoma claim quando volta pra zona azul.
            let in_pvp = *self.pvp_pause.lock().await && matches!(
                self.zone.lock().await.clone(),
                crate::transfer::ZoneType::PvP
            );
            if in_pvp {
                tokio::time::sleep(Duration::from_secs(30)).await;
                continue;
            }
            match self.cycle(&api).await {
                Ok(true) => {
                    let mut s = self.stats.lock().await;
                    s.cycles += 1;
                    s.last_cycle_at = Some(chrono_now());
                }
                Ok(false) => {
                    tokio::time::sleep(Duration::from_secs(10)).await;
                }
                Err(e) => {
                    let msg = format!("{:#}", e);
                    {
                        let mut s = self.stats.lock().await;
                        s.last_error = Some(msg.clone());
                    }
                    tokio::time::sleep(Duration::from_secs(15)).await;
                }
            }
        }
    }

    /// Um ciclo: claim um range, sonda cada ID, reporta (ou enfileira se PvP).
    /// Retorna Ok(true) se processou um range, Ok(false) se não havia trabalho.
    async fn cycle(&self, api: &ApiClient) -> Result<bool> {
        let claim: ScanClaim = match api.claim_scan().await {
            Ok(c) => c,
            Err(e) if e.to_string().contains("sem trabalho") => return Ok(false),
            Err(e) => return Err(e),
        };

        let host = host_for(&claim.server);
        let client = reqwest::Client::builder()
            .user_agent("ziggs-companion/0.1")
            .timeout(Duration::from_secs(15))
            .build()?;

        let mut found: Vec<i64> = Vec::new();
        let mut missing: Vec<i64> = Vec::new();
        let mut errors: Vec<i64> = Vec::new();

        for id in claim.battle_id_start..=claim.battle_id_end {
            let url = format!("https://{}/api/gameinfo/battles/{}", host, id);
            match client.get(&url).send().await {
                Ok(resp) if resp.status().as_u16() == 200 => {
                    match resp.json::<serde_json::Value>().await {
                        Ok(v) if valid_battle_payload(&v) => found.push(id),
                        _ => errors.push(id),
                    }
                    // 200 sustentado → recupera throttle (-50ms, piso 150ms).
                    let cur = self.throttle_ms.load(Ordering::Relaxed);
                    if cur > THROTTLE_MIN_MS {
                        self.throttle_ms.store(
                            cur.saturating_sub(THROTTLE_RECOVER_MS),
                            Ordering::Relaxed,
                        );
                    }
                }
                Ok(resp) if resp.status().as_u16() == 404 => missing.push(id),
                Ok(resp) if resp.status().as_u16() == 429 => {
                    // rate limit: espera Retry-After ou 5s e segue.
                    let wait = resp
                        .headers()
                        .get("Retry-After")
                        .and_then(|v| v.to_str().ok())
                        .and_then(|s| s.parse::<f64>().ok())
                        .unwrap_or(5.0);
                    tokio::time::sleep(Duration::from_secs_f64(wait)).await;
                    errors.push(id);
                    // 429 → recua multiplicativo (×2, teto 5s). Ganância detectada.
                    let cur = self.throttle_ms.load(Ordering::Relaxed);
                    let next = (cur * 2).min(THROTTLE_MAX_MS);
                    self.throttle_ms.store(next, Ordering::Relaxed);
                }
                _ => errors.push(id),
            }
            // throttle adaptativo entre sondagens (cortesia à API pública).
            let ms = self.throttle_ms.load(Ordering::Relaxed);
            tokio::time::sleep(Duration::from_millis(ms)).await;
        }

        let report = ScanReportIn {
            task_id: claim.task_id,
            region: claim.server.clone(),
            found,
            missing,
            errors,
            character_name: self.character_name.lock().await.clone(),
        };

        // Zone-aware: zona azul → envia direto (+ flush fila). PvP → enfileira.
        let in_pvp = *self.pvp_pause.lock().await && matches!(
            self.zone.lock().await.clone(),
            crate::transfer::ZoneType::PvP
        );

        if in_pvp {
            if let Some(q) = &self.queue {
                q.enqueue_scan_report(report).await;
                let pending = q.pending_count().await;
                self.stats.lock().await.queued_reports = pending;
            }
            // ponytail: sem api.report_scan — dado fica local até zona azul.
            // Conta como ciclo processado mas sem confirmar pro backend.
            // O claim vai expirar em 15min e voltar a pending se não reportar,
            // mas isso é ok — outro companion ou re-claim pega depois.
            // Upgrade path: estender CLAIM_TTL no backend pra companions em PvP.
            let mut s = self.stats.lock().await;
            s.cycles += 1;
            s.last_cycle_at = Some(chrono_now());
            return Ok(true);
        }

        // Zona azul: manda ESTE report direto. A fila acumulada fica com o
        // uploader único — misturar as duas coisas aqui recriava a rajada.
        let out = api.report_scan(&report).await?;
        {
            let mut s = self.stats.lock().await;
            s.battles_found += out.accepted as u64;
            s.battles_missing += report.missing.len() as u64;
            s.battles_errors += report.errors.len() as u64;
            if let Some(q) = &self.queue {
                s.queued_reports = q.pending_count().await;
            }
        }
        Ok(true)
    }
}

fn host_for(region: &str) -> &'static str {
    // Bater com backend player_tracker.HOSTS
    match region {
        "americas" => "gameinfo.albiononline.com",
        "europe" => "gameinfo-ams.albiononline.com",
        "asia" => "gameinfo-sgp.albiononline.com",
        _ => "gameinfo.albiononline.com",
    }
}

fn chrono_now() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
    format!("{}", secs)
}

fn valid_battle_payload(value: &serde_json::Value) -> bool {
    value.get("id").is_some()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn clone_compartilha_shutdown() {
        let scanner = Scanner::new();
        let clone = scanner.clone_for_spawn();
        clone.stop().await;
        assert!(scanner.shutdown.load(Ordering::Relaxed));
    }


    #[test]
    fn resposta_200_malformada_nao_e_batalha() {
        assert!(!valid_battle_payload(&serde_json::json!({"players": []})));
        assert!(valid_battle_payload(&serde_json::json!({"id": 42})));
    }

    #[test]
    fn report_envia_somente_ids_de_batalha() {
        let report = ScanReportIn {
            task_id: 1,
            region: "americas".into(),
            found: vec![42],
            missing: vec![43],
            errors: vec![],
            character_name: None,
        };
        let json = serde_json::to_value(report).unwrap();
        assert_eq!(json["found"], serde_json::json!([42]));
    }
}
