// Battle scanner worker: pega ranges distribuídos pelo backend, sonda a API
// pública do Albion e reporta resultados. N clients = N IPs = rate limit
// distribuído — o ponto central do plano.
//
// Zone-aware: se o jogador estiver em zona PvP (e pvp_pause_transfer=true),
// o report vai pra fila local em vez do backend. Flush quando voltar pra zona azul.

use std::time::Duration;
use anyhow::Result;
use serde::{Deserialize, Serialize};
use tokio::sync::Mutex;
use std::sync::Arc;

use crate::api::{ApiClient, ScanClaim, ScanReportIn};
use crate::transfer::TransferQueue;

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
        }
    }
}

pub struct Scanner {
    pub stats: Arc<Mutex<ScanStats>>,
    shutdown: Arc<Mutex<bool>>,
    /// Zona atual — lida por commands da UI (set_zone). Default: blue (envia direto).
    pub zone: Arc<Mutex<crate::transfer::ZoneType>>,
    /// Fila de transferência — compartilhada com AppState.
    pub queue: Option<Arc<TransferQueue>>,
    /// Se true, pausa transferência em zona PvP. Mutável via set_config.
    pub pvp_pause: Arc<Mutex<bool>>,
    /// Nick do jogador — vai no report pro backend creditar batalhas novas
    /// (agradecimento na página pública). Mutável via set_config.
    pub character_name: Arc<Mutex<Option<String>>>,
}

impl Scanner {
    pub fn new() -> Self {
        Self {
            stats: Arc::new(Mutex::new(ScanStats::default())),
            shutdown: Arc::new(Mutex::new(false)),
            zone: Arc::new(Mutex::new(crate::transfer::ZoneType::Blue)),
            queue: None,
            pvp_pause: Arc::new(Mutex::new(true)),
            character_name: Arc::new(Mutex::new(None)),
        }
    }

    pub fn with_queue(mut self, queue: Arc<TransferQueue>) -> Self {
        self.queue = Some(queue);
        self
    }

    /// Clona os Arcs internos pra criar um scanner que roda numa task separada
    /// mas compartilha stats/zone/queue/pvp_pause com o original.
    pub fn clone_for_spawn(&self) -> Scanner {
        Scanner {
            stats: Arc::clone(&self.stats),
            shutdown: Arc::new(Mutex::new(false)),
            zone: Arc::clone(&self.zone),
            queue: self.queue.as_ref().map(Arc::clone),
            pvp_pause: Arc::clone(&self.pvp_pause),
            character_name: Arc::clone(&self.character_name),
        }
    }

    pub async fn stop(&self) {
        *self.shutdown.lock().await = true;
    }

    /// Define a zona atual. Se mudar de PvP→Blue, dispara flush da fila.
    pub async fn set_zone(&self, zone: crate::transfer::ZoneType, api: &ApiClient) {
        let was_pvp = matches!(self.zone.lock().await.clone(), crate::transfer::ZoneType::PvP);
        *self.zone.lock().await = zone.clone();
        let zone_str = match zone {
            crate::transfer::ZoneType::Blue => "blue",
            crate::transfer::ZoneType::PvP => "pvp",
            crate::transfer::ZoneType::Unknown => "unknown",
        };
        self.stats.lock().await.zone = zone_str.to_string();

        // Flush quando volta pra zona azul e estava em PvP
        if was_pvp && matches!(zone, crate::transfer::ZoneType::Blue) {
            if let Some(q) = &self.queue {
                let (sent, failed) = q.flush_all(api).await;
                if sent > 0 || failed > 0 {
                    tracing::info!("scanner: flush zona azul — {} enviados, {} falharam", sent, failed);
                }
                let pending = q.pending_count().await;
                self.stats.lock().await.queued_reports = pending;
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
        *self.shutdown.lock().await = false;
        {
            let mut s = self.stats.lock().await;
            s.status = "running".into();
            s.last_error = None;
        }

        loop {
            if *self.shutdown.lock().await {
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

        let mut found: Vec<serde_json::Value> = Vec::new();
        let mut missing: Vec<i64> = Vec::new();
        let mut errors: Vec<i64> = Vec::new();

        for id in claim.battle_id_start..=claim.battle_id_end {
            let url = format!("https://{}/api/gameinfo/battles/{}", host, id);
            match client.get(&url).send().await {
                Ok(resp) if resp.status().as_u16() == 200 => {
                    match resp.json::<serde_json::Value>().await {
                        Ok(v) if v.get("id").is_some() => found.push(v),
                        _ => missing.push(id),
                    }
                }
                Ok(resp) if resp.status().as_u16() == 404 => missing.push(id),
                Ok(resp) if resp.status().as_u16() == 429 => {
                    // rate limit: espera Retry-After ou 5s e segue
                    let wait = resp
                        .headers()
                        .get("Retry-After")
                        .and_then(|v| v.to_str().ok())
                        .and_then(|s| s.parse::<f64>().ok())
                        .unwrap_or(5.0);
                    tokio::time::sleep(Duration::from_secs_f64(wait)).await;
                    errors.push(id);
                }
                _ => errors.push(id),
            }
            // throttle mínimo entre sondagens (cortesia à API pública)
            tokio::time::sleep(Duration::from_millis(150)).await;
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

        // Zona azul: flush fila primeiro, depois envia este report direto
        if let Some(q) = &self.queue {
            let pending = q.pending_count().await;
            if pending > 0 {
                let _ = q.flush_all(api).await;
                self.stats.lock().await.queued_reports = q.pending_count().await;
            }
        }

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