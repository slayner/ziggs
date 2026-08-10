// Battle scanner: claims ID ranges from backend, probes Albion public API,
// reports results. N clients = N source IPs = distributed rate limiting.
//
// Zone-aware: in PvP, reports queue locally; flushed when back in blue zone.
//
// AIMD throttle: 429 → backoff ×2 (cap 5s); sustained 200 → recover -50ms
// per probe (floor 150ms). Same philosophy as backend albion_gate, but on
// the client since we talk to Albion directly.

use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::Mutex;

use crate::api::{ApiClient, KillScanClaim, KillScanReportIn, ScanClaim, ScanReportIn};
use crate::sniffer::DebugLine;
use crate::transfer::TransferQueue;

/// Push a line to the debug buffer (UI terminal), capped at 500 lines.
async fn emit_debug(debug: &Option<Arc<Mutex<Vec<DebugLine>>>>, level: &str, msg: String) {
    if let Some(d) = debug {
        let line = DebugLine {
            ts: crate::photon_parser::now_iso_utc(),
            level: level.into(),
            msg,
        };
        let mut g = d.lock().await;
        g.push(line);
        if g.len() > 500 {
            let ex = g.len() - 500;
            g.drain(..ex);
        }
    }
}

/// Inter-probe delay — adaptive. Starts at 150ms. 429 doubles (cap 5s).
/// Sustained 200 recovers -50ms per probe (floor 150ms). Atomic to avoid
/// locking in the scan hot loop.
const THROTTLE_MIN_MS: u64 = 150;
const THROTTLE_MAX_MS: u64 = 5_000;
const THROTTLE_START_MS: u64 = 150;
const THROTTLE_RECOVER_MS: u64 = 50; // -50ms per sustained 200

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ScanStats {
    pub status: String, // "idle" | "running" | "error" | "disabled"
    pub last_cycle_at: Option<String>,
    pub battles_found: u64,
    pub battles_missing: u64,
    pub battles_errors: u64,
    pub cycles: u64,
    pub last_error: Option<String>,
    pub queued_reports: usize,
    pub zone: String,     // "blue" | "pvp" | "unknown"
    pub throttle_ms: u64, // adaptive inter-probe delay (transparency)
    // Kill scan — runs in parallel with battle scan, shared throttle/zone.
    pub kill_cycles: u64,
    pub kill_events_found: u64,
    pub kill_events_missing: u64,
    pub kill_events_errors: u64,
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
            kill_cycles: 0,
            kill_events_found: 0,
            kill_events_missing: 0,
            kill_events_errors: 0,
        }
    }
}

pub struct Scanner {
    pub stats: Arc<Mutex<ScanStats>>,
    shutdown: Arc<AtomicBool>,
    /// Current zone — read by UI commands (set_zone). Default: blue (sends directly).
    pub zone: Arc<Mutex<crate::transfer::ZoneType>>,
    /// Transfer queue — shared with AppState.
    pub queue: Option<Arc<TransferQueue>>,
    /// If true, pauses transfer in PvP zone. Mutable via set_config.
    pub pvp_pause: Arc<Mutex<bool>>,
    /// Player nickname — included in reports for battle attribution on the
    /// public page. Mutable via set_config.
    pub character_name: Arc<Mutex<Option<String>>>,
    /// Inter-probe delay (ms), adaptive. 429 doubles, sustained 200 recovers.
    /// Atomic: read/written in the cycle hot loop without locking.
    pub throttle_ms: Arc<AtomicU64>,
    /// Debug buffer (UI terminal). Absent when scanner runs without UI.
    pub debug: Option<Arc<Mutex<Vec<DebugLine>>>>,
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
            debug: None,
        }
    }

    pub fn with_queue(mut self, queue: Arc<TransferQueue>) -> Self {
        self.queue = Some(queue);
        self
    }

    pub fn with_debug(mut self, debug: Arc<Mutex<Vec<DebugLine>>>) -> Self {
        self.debug = Some(debug);
        self
    }

    /// Clone internal Arcs to spawn in a separate task, sharing state with
    /// the original scanner.
    pub fn clone_for_spawn(&self) -> Scanner {
        Scanner {
            stats: Arc::clone(&self.stats),
            shutdown: Arc::clone(&self.shutdown),
            zone: Arc::clone(&self.zone),
            queue: self.queue.as_ref().map(Arc::clone),
            pvp_pause: Arc::clone(&self.pvp_pause),
            character_name: Arc::clone(&self.character_name),
            throttle_ms: Arc::clone(&self.throttle_ms),
            debug: self.debug.as_ref().map(Arc::clone),
        }
    }

    pub async fn stop(&self) {
        self.shutdown.store(true, Ordering::Relaxed);
    }

    pub fn prepare_start(&self) {
        self.shutdown.store(false, Ordering::Relaxed);
    }

    /// Set current zone. Flushes queue on PvP→Blue transition.
    pub async fn set_zone(&self, zone: crate::transfer::ZoneType, _api: &ApiClient) {
        let was_pvp = matches!(
            self.zone.lock().await.clone(),
            crate::transfer::ZoneType::PvP
        );
        *self.zone.lock().await = zone.clone();
        let zone_str = match zone {
            crate::transfer::ZoneType::Blue => "blue",
            crate::transfer::ZoneType::PvP => "pvp",
            crate::transfer::ZoneType::Unknown => "unknown",
        };
        self.stats.lock().await.zone = zone_str.to_string();

        // Back in blue zone: just update counter. The single uploader sends
        // gradually — flushing the whole queue here would spike network right
        // after a fight, the worst possible time.
        if was_pvp && matches!(zone, crate::transfer::ZoneType::Blue) {
            if let Some(q) = &self.queue {
                self.stats.lock().await.queued_reports = q.pending_count().await;
            }
        }
    }

    /// Main loop: claim → scan → report → sleep → repeat.
    /// Stops on `stop()` or persistent connection error.
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
            // In PvP with pause on: don't claim new tasks, just accumulate locally.
            // Sleep 30s (saves CPU/network during combat). Resumes on blue zone.
            let in_pvp = *self.pvp_pause.lock().await
                && matches!(
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

    /// One cycle: claim a range, probe each ID, report (or queue if PvP).
    /// Returns Ok(true) if a range was processed, Ok(false) if no work.
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
                    // Sustained 200 → recover throttle (-50ms, floor 150ms).
                    let cur = self.throttle_ms.load(Ordering::Relaxed);
                    if cur > THROTTLE_MIN_MS {
                        self.throttle_ms
                            .store(cur.saturating_sub(THROTTLE_RECOVER_MS), Ordering::Relaxed);
                    }
                }
                Ok(resp) if resp.status().as_u16() == 404 => missing.push(id),
                Ok(resp) if resp.status().as_u16() == 429 => {
                    // rate limit: wait Retry-After or 5s and continue.
                    let wait = resp
                        .headers()
                        .get("Retry-After")
                        .and_then(|v| v.to_str().ok())
                        .and_then(|s| s.parse::<f64>().ok())
                        .unwrap_or(5.0);
                    tokio::time::sleep(Duration::from_secs_f64(wait)).await;
                    errors.push(id);
                    // 429 → multiplicative backoff (×2, cap 5s).
                    let cur = self.throttle_ms.load(Ordering::Relaxed);
                    let next = (cur * 2).min(THROTTLE_MAX_MS);
                    self.throttle_ms.store(next, Ordering::Relaxed);
                }
                _ => errors.push(id),
            }
            // Adaptive throttle between probes (courtesy to public API).
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

        // Zone-aware: blue → send directly (+ flush queue). PvP → enqueue.
        let in_pvp = *self.pvp_pause.lock().await
            && matches!(
                self.zone.lock().await.clone(),
                crate::transfer::ZoneType::PvP
            );

        if in_pvp {
            let n_found = report.found.len();
            if let Some(q) = &self.queue {
                q.enqueue_scan_report(report).await;
                let pending = q.pending_count().await;
                self.stats.lock().await.queued_reports = pending;
            }
            // Claims expire server-side; another scanner will pick them up.
            let mut s = self.stats.lock().await;
            s.cycles += 1;
            s.last_cycle_at = Some(chrono_now());
            emit_debug(
                &self.debug,
                "info",
                format!(
                    "scan: range {}-{} {} em PvP → {} encontradas enfileiradas",
                    claim.battle_id_start, claim.battle_id_end, claim.server, n_found,
                ),
            )
            .await;
            return Ok(true);
        }

        // Blue zone: send this report directly. Queued reports stay with the
        // single uploader — mixing both here would recreate the burst.
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
        emit_debug(
            &self.debug,
            "info",
            format!(
                "scan: range {}-{} {} → {} encontradas, {} 404, {} erros",
                claim.battle_id_start,
                claim.battle_id_end,
                claim.server,
                out.accepted,
                report.missing.len(),
                report.errors.len(),
            ),
        )
        .await;
        Ok(true)
    }
}

fn host_for(region: &str) -> &'static str {
    // Keep in sync with backend player_tracker.HOSTS.
    match region {
        "americas" => "gameinfo.albiononline.com",
        "europe" => "gameinfo-ams.albiononline.com",
        "asia" => "gameinfo-sgp.albiononline.com",
        _ => "gameinfo.albiononline.com",
    }
}

fn chrono_now() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs();
    format!("{}", secs)
}

fn valid_battle_payload(value: &serde_json::Value) -> bool {
    value.get("id").is_some()
}

fn valid_kill_payload(value: &serde_json::Value) -> bool {
    value.get("EventId").is_some()
}

// ─── Kill Scanner ────────────────────────────────────────────────────────────
// Same pattern as the battle Scanner, but probes /api/gameinfo/events/{id}
// instead of /battles/{id}. Runs in parallel, shares throttle and zone-awareness.

pub struct KillScanner {
    pub stats: Arc<Mutex<ScanStats>>,
    shutdown: Arc<AtomicBool>,
    pub zone: Arc<Mutex<crate::transfer::ZoneType>>,
    pub pvp_pause: Arc<Mutex<bool>>,
    pub throttle_ms: Arc<AtomicU64>,
    pub debug: Option<Arc<Mutex<Vec<DebugLine>>>>,
}

impl KillScanner {
    pub fn new() -> Self {
        Self {
            stats: Arc::new(Mutex::new(ScanStats::default())),
            shutdown: Arc::new(AtomicBool::new(false)),
            zone: Arc::new(Mutex::new(crate::transfer::ZoneType::Blue)),
            pvp_pause: Arc::new(Mutex::new(true)),
            throttle_ms: Arc::new(AtomicU64::new(THROTTLE_START_MS)),
            debug: None,
        }
    }

    /// Clone Arcs from a battle Scanner to share stats/throttle/zone.
    pub fn from_scanner(scanner: &Scanner) -> Self {
        Self {
            stats: Arc::clone(&scanner.stats),
            shutdown: Arc::new(AtomicBool::new(false)),
            zone: Arc::clone(&scanner.zone),
            pvp_pause: Arc::clone(&scanner.pvp_pause),
            throttle_ms: Arc::clone(&scanner.throttle_ms),
            debug: scanner.debug.as_ref().map(Arc::clone),
        }
    }

    pub async fn stop(&self) {
        self.shutdown.store(true, Ordering::Relaxed);
    }

    /// Clone Arcs to spawn in a separate task, sharing stats with the battle Scanner.
    pub fn clone_for_spawn(&self) -> KillScanner {
        KillScanner {
            stats: Arc::clone(&self.stats),
            shutdown: Arc::new(AtomicBool::new(false)),
            zone: Arc::clone(&self.zone),
            pvp_pause: Arc::clone(&self.pvp_pause),
            throttle_ms: Arc::clone(&self.throttle_ms),
            debug: self.debug.as_ref().map(Arc::clone),
        }
    }

    pub async fn run(&self, api: ApiClient, enabled: bool) {
        if !enabled {
            return;
        }
        loop {
            if self.shutdown.load(Ordering::Relaxed) {
                break;
            }
            let in_pvp = *self.pvp_pause.lock().await
                && matches!(
                    self.zone.lock().await.clone(),
                    crate::transfer::ZoneType::PvP
                );
            if in_pvp {
                tokio::time::sleep(Duration::from_secs(30)).await;
                continue;
            }
            match self.cycle(&api).await {
                Ok(true) => {}
                Ok(false) => {
                    tokio::time::sleep(Duration::from_secs(15)).await;
                }
                Err(e) => {
                    let msg = format!("{:#}", e);
                    {
                        let mut s = self.stats.lock().await;
                        s.last_error = Some(msg);
                    }
                    tokio::time::sleep(Duration::from_secs(30)).await;
                }
            }
        }
    }

    async fn cycle(&self, api: &ApiClient) -> Result<bool> {
        let claim: KillScanClaim = match api.claim_kill_scan().await {
            Ok(c) => c,
            Err(e) if e.to_string().contains("sem trabalho") => return Ok(false),
            Err(e) => return Err(e),
        };

        let host = host_for(&claim.region);
        let client = reqwest::Client::builder()
            .user_agent("ziggs-companion/0.1")
            .timeout(Duration::from_secs(15))
            .build()?;

        let mut found: Vec<i64> = Vec::new();
        let mut missing: Vec<i64> = Vec::new();
        let mut errors: Vec<i64> = Vec::new();

        for id in claim.event_id_start..=claim.event_id_end {
            let url = format!("https://{}/api/gameinfo/events/{}", host, id);
            match client.get(&url).send().await {
                Ok(resp) if resp.status().as_u16() == 200 => {
                    match resp.json::<serde_json::Value>().await {
                        Ok(v) if valid_kill_payload(&v) => found.push(id),
                        _ => errors.push(id),
                    }
                    let cur = self.throttle_ms.load(Ordering::Relaxed);
                    if cur > THROTTLE_MIN_MS {
                        self.throttle_ms
                            .store(cur.saturating_sub(THROTTLE_RECOVER_MS), Ordering::Relaxed);
                    }
                }
                Ok(resp) if resp.status().as_u16() == 404 => missing.push(id),
                Ok(resp) if resp.status().as_u16() == 429 => {
                    let wait = resp
                        .headers()
                        .get("Retry-After")
                        .and_then(|v| v.to_str().ok())
                        .and_then(|s| s.parse::<f64>().ok())
                        .unwrap_or(5.0);
                    tokio::time::sleep(Duration::from_secs_f64(wait)).await;
                    errors.push(id);
                    let cur = self.throttle_ms.load(Ordering::Relaxed);
                    let next = (cur * 2).min(THROTTLE_MAX_MS);
                    self.throttle_ms.store(next, Ordering::Relaxed);
                }
                _ => errors.push(id),
            }
            let ms = self.throttle_ms.load(Ordering::Relaxed);
            tokio::time::sleep(Duration::from_millis(ms)).await;
        }

        let report = KillScanReportIn {
            region: claim.region.clone(),
            event_id_start: claim.event_id_start,
            event_id_end: claim.event_id_end,
            found,
            missing: missing.clone(),
            errors: errors.clone(),
        };

        // Kill scan is lightweight — discards in PvP instead of queuing.
        // Backend re-validates everything anyway.
        match api.report_kill_scan(&report).await {
            Ok(out) => {
                let mut s = self.stats.lock().await;
                s.kill_cycles += 1;
                s.kill_events_found += out.accepted as u64;
                s.kill_events_missing += report.missing.len() as u64;
                s.kill_events_errors += report.errors.len() as u64;
            }
            Err(e) => {
                tracing::warn!("kill-scan report falhou: {:#}", e);
            }
        }
        emit_debug(
            &self.debug,
            "info",
            format!(
                "kill-scan: {}-{} {} → {} encontrados, {} 404, {} erros",
                claim.event_id_start,
                claim.event_id_end,
                claim.region,
                report.found.len(),
                report.missing.len(),
                report.errors.len(),
            ),
        )
        .await;
        Ok(true)
    }
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
