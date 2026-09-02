// companion_lib: library entry point. main.rs calls companion_lib::run().

pub mod albion_detect;
pub mod albion_ips;
pub mod aodp;
pub mod api;
pub mod config;
pub mod crash_report;
pub mod dns;
pub mod firewall;
pub mod lootlog;
pub mod maps;
pub mod persist;
pub mod photon_parser;
pub mod scanner;
pub mod sniffer;
pub mod transfer;
pub mod tunnel;
pub mod tunnel_presets;
#[cfg(target_os = "windows")]
pub mod windivert;
pub mod winutil;
pub mod zone_detect;

pub use config::CompanionConfig;
pub use lootlog::LootlogStatus;
pub use scanner::{KillScanner, ScanStats, Scanner};
pub use sniffer::{DebugLine, SniffStats, Sniffer};
pub use transfer::TransferQueue;
pub use tunnel::{Tunnel, TunnelStatus};

use std::sync::Arc;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Emitter, Manager,
};
use tauri_plugin_autostart::MacosLauncher;
#[cfg(not(target_os = "windows"))]
use tauri_plugin_autostart::ManagerExt;
use tauri_plugin_opener::OpenerExt;
use tokio::sync::Mutex;

/// Pushes a line to the sniffer debug buffer (shown in the UI terminal).
/// Cap at 500 lines.
async fn push_debug(debug: &Arc<Mutex<Vec<DebugLine>>>, level: &str, msg: &str) {
    let line = DebugLine {
        ts: photon_parser::now_iso_utc(),
        level: level.into(),
        msg: msg.into(),
    };
    let mut d = debug.lock().await;
    d.push(line);
    if d.len() > 500 {
        let ex = d.len() - 500;
        d.drain(..ex);
    }
}

/// Safe to spend CPU/network? True when game is closed or player is outside PvP zone.
/// The sniffer's stats.online flag is free (no process scan needed).
async fn heavy_work_ok(
    sniffer: &Sniffer,
    zone: &Arc<Mutex<transfer::ZoneType>>,
    pvp_pause: &Arc<Mutex<bool>>,
) -> bool {
    if !sniffer.stats.lock().await.online {
        return true;
    }
    let paused = *pvp_pause.lock().await;
    !(paused && matches!(*zone.lock().await, transfer::ZoneType::PvP))
}

#[derive(Clone, Debug, serde::Serialize)]
pub struct PlatformCapabilities {
    pub platform: &'static str,
    pub packet_capture: bool,
    pub tunnel: bool,
    pub dns_apply: bool,
    pub market_capture: bool,
    pub self_update: bool,
}

impl PlatformCapabilities {
    fn current() -> Self {
        Self {
            platform: std::env::consts::OS,
            packet_capture: cfg!(target_os = "windows"),
            tunnel: cfg!(target_os = "windows"),
            dns_apply: cfg!(target_os = "windows"),
            market_capture: cfg!(target_os = "windows"),
            self_update: cfg!(target_os = "windows"),
        }
    }
}

pub struct AppState {
    pub config: Arc<Mutex<CompanionConfig>>,
    pub scanner: Scanner,
    pub scanner_running: Arc<Mutex<bool>>,
    pub kill_scanner: KillScanner,
    pub kill_scanner_running: Arc<Mutex<bool>>,
    pub tunnel: Tunnel,
    pub tunnel_running: Arc<Mutex<bool>>,
    pub transfer_queue: Arc<TransferQueue>,
    pub sniffer: Sniffer,
    pub sniffer_running: Arc<Mutex<bool>>,
    pub lootlog: Arc<Mutex<lootlog::LootlogStatus>>,
}

#[tauri::command]
async fn get_config(state: tauri::State<'_, AppState>) -> Result<CompanionConfig, String> {
    Ok(state.config.lock().await.clone())
}

#[tauri::command]
fn get_platform_capabilities() -> PlatformCapabilities {
    PlatformCapabilities::current()
}

#[tauri::command]
async fn set_config(
    key: String,
    value: serde_json::Value,
    state: tauri::State<'_, AppState>,
    #[cfg_attr(target_os = "windows", allow(unused_variables))] app: tauri::AppHandle,
) -> Result<(), String> {
    let mut cfg = state.config.lock().await;
    let changed_autostart = key == "autostart";
    match (key.as_str(), value.clone()) {
        ("autostart", serde_json::Value::Bool(b)) => cfg.autostart = b,
        ("minimize_to_tray", serde_json::Value::Bool(b)) => cfg.minimize_to_tray = b,
        ("collect_damage_meter", serde_json::Value::Bool(b)) => {
            cfg.collect_damage_meter = b;
            state
                .sniffer
                .capture_damage
                .store(b, std::sync::atomic::Ordering::Relaxed);
        }
        ("collect_auto_lootlog", serde_json::Value::Bool(b)) => {
            cfg.collect_auto_lootlog = b;
            state
                .sniffer
                .capture_loot
                .store(b, std::sync::atomic::Ordering::Relaxed);
        }
        ("tunnel_enabled", serde_json::Value::Bool(b)) => cfg.tunnel_enabled = b,
        ("tunnel_endpoint", serde_json::Value::String(s)) => cfg.tunnel_endpoint = s,
        ("tunnel_server_pubkey", serde_json::Value::String(s)) => cfg.tunnel_server_pubkey = s,
        ("tunnel_client_privkey", serde_json::Value::String(s)) => cfg.tunnel_client_privkey = s,
        ("pvp_pause_transfer", serde_json::Value::Bool(b)) => {
            cfg.pvp_pause_transfer = b;
            *state.scanner.pvp_pause.lock().await = b;
        }
        ("feed_aodp", serde_json::Value::Bool(b)) => {
            cfg.feed_aodp = b;
            state
                .sniffer
                .feed_aodp
                .store(b, std::sync::atomic::Ordering::Relaxed);
        }
        // Damage meter spell index calibration — see spell_index_offset in config.
        ("spell_index_offset", serde_json::Value::Number(n)) => {
            cfg.spell_index_offset = n.as_i64().unwrap_or(0) as i32;
        }
        _ => return Err(format!("unknown field: {}", key)),
    }
    if let Err(e) = config::save(&cfg) {
        return Err(format!("failed to save config: {e}"));
    }
    if changed_autostart {
        #[cfg(target_os = "windows")]
        {
            let want = cfg.autostart;
            let _ = set_autostart(want);
        }
        #[cfg(not(target_os = "windows"))]
        {
            let autostart = app.autolaunch();
            let _ = if cfg.autostart {
                autostart.enable()
            } else {
                autostart.disable()
            };
        }
    }
    Ok(())
}

#[tauri::command]
async fn get_scan_stats(state: tauri::State<'_, AppState>) -> Result<ScanStats, String> {
    let mut s = state.scanner.stats.lock().await.clone();
    s.throttle_ms = state
        .scanner
        .throttle_ms
        .load(std::sync::atomic::Ordering::Relaxed);
    Ok(s)
}

#[tauri::command]
async fn start_scanner(state: tauri::State<'_, AppState>) -> Result<(), String> {
    let mut running = state.scanner_running.lock().await;
    if *running {
        return Ok(());
    }
    *running = true;
    state.scanner.prepare_start();
    // Battle scanning is always on (primary purpose of the companion).
    let api = api::ApiClient::new(config::API_BASE_URL);
    let scanner = state.scanner.clone_for_spawn();
    let running_flag = Arc::clone(&state.scanner_running);
    tokio::spawn(async move {
        scanner.run(api, true).await;
        *running_flag.lock().await = false;
    });
    Ok(())
}

#[tauri::command]
async fn stop_scanner(state: tauri::State<'_, AppState>) -> Result<(), String> {
    state.scanner.stop().await;
    *state.scanner_running.lock().await = false;
    Ok(())
}

#[tauri::command]
async fn test_dns(_server_hostname: String) -> Result<Vec<dns::DnsResult>, String> {
    Ok(dns::test_all(&_server_hostname).await)
}

#[tauri::command]
async fn apply_dns(profile_name: String) -> Result<(), String> {
    let profile = dns::dns_profiles()
        .into_iter()
        .find(|p| p.name == profile_name)
        .ok_or_else(|| format!("profile '{}' not found", profile_name))?;
    dns::apply_dns(&profile).map_err(|e| format!("{:#}", e))
}

#[tauri::command]
async fn get_dns_targets(
    _state: tauri::State<'_, AppState>,
) -> Result<Vec<api::DnsTarget>, String> {
    let api = api::ApiClient::new(config::API_BASE_URL);
    match api.dns_targets().await {
        Ok(out) => Ok(out.servers),
        Err(e) => Err(format!("{:#}", e)),
    }
}

// ─── Zone commands (PvP pause / Blue flush) ──────────────────────────────────

#[tauri::command]
async fn set_zone(zone: String, state: tauri::State<'_, AppState>) -> Result<(), String> {
    let z = match zone.as_str() {
        "blue" => transfer::ZoneType::Blue,
        "pvp" => transfer::ZoneType::PvP,
        _ => transfer::ZoneType::Unknown,
    };
    let api = api::ApiClient::new(config::API_BASE_URL);
    state.scanner.set_zone(z, &api).await;
    Ok(())
}

#[tauri::command]
async fn flush_transfer_queue(state: tauri::State<'_, AppState>) -> Result<(usize, usize), String> {
    let api = api::ApiClient::new(config::API_BASE_URL);
    let (sent, failed) = state.transfer_queue.flush_all(&api).await;
    let pending = state.transfer_queue.pending_count().await;
    state.scanner.stats.lock().await.queued_reports = pending;
    Ok((sent, failed))
}

#[tauri::command]
async fn pending_count(state: tauri::State<'_, AppState>) -> Result<usize, String> {
    Ok(state.transfer_queue.pending_count().await)
}

#[tauri::command]
fn classify_zone(cluster_type: String) -> String {
    match zone_detect::classify_zone(&cluster_type) {
        crate::transfer::ZoneType::Blue => "blue",
        crate::transfer::ZoneType::PvP => "pvp",
        crate::transfer::ZoneType::Unknown => "unknown",
    }
    .to_string()
}

// ─── Sniffer commands (packet capture) ──────────────────────────────────────

#[tauri::command]
async fn start_sniffer(state: tauri::State<'_, AppState>) -> Result<(), String> {
    let mut running = state.sniffer_running.lock().await;
    if *running {
        return Ok(());
    }
    *running = true;
    let sniffer = state.sniffer.clone_shared();
    let generation = sniffer.prepare_start();
    let running_flag = Arc::clone(&state.sniffer_running);
    tauri::async_runtime::spawn(async move {
        sniffer.run_generation(generation).await;
        if sniffer.is_current(generation) {
            *running_flag.lock().await = false;
        }
    });
    Ok(())
}

#[tauri::command]
async fn stop_sniffer(state: tauri::State<'_, AppState>) -> Result<(), String> {
    state.sniffer.stop().await;
    *state.sniffer_running.lock().await = false;
    Ok(())
}

#[tauri::command]
async fn get_sniff_stats(state: tauri::State<'_, AppState>) -> Result<SniffStats, String> {
    let mut s = state.sniffer.stats.lock().await.clone();
    // Summed at read time (UI poll every 5s), not in the hot loop.
    // Sequential locks, never nested.
    let damage_map = state.sniffer.damage.lock().await;
    s.damage_total = damage_map.values().map(|a| a.damage).sum::<f64>() as u64;
    // Badge for Damage tab: own player's damage (s.player_name), not party total.
    // A player can have multiple entity IDs per session (re-entering vis range) —
    // resolve by name via the entities map. Sequential locks, never nested.
    if !s.player_name.is_empty() {
        let ents = state.sniffer.entities.lock().await;
        let my_ids: std::collections::HashSet<i64> = ents
            .iter()
            .filter(|(_, name)| *name == &s.player_name)
            .map(|(id, _)| *id)
            .collect();
        drop(ents);
        s.my_damage = damage_map
            .iter()
            .filter(|(id, _)| my_ids.contains(id))
            .map(|(_, a)| a.damage)
            .sum::<f64>() as u64;
    }
    drop(damage_map);
    Ok(s)
}

#[tauri::command]
async fn get_sniffer_debug(
    state: tauri::State<'_, AppState>,
) -> Result<Vec<sniffer::DebugLine>, String> {
    Ok(state.sniffer.debug.lock().await.clone())
}

// ─── Albion process detection (status no header) ────────────────────────────

#[tauri::command]
async fn get_albion_pid() -> Option<u32> {
    // spawn_blocking to avoid blocking the webview main thread.
    // Process enumeration runs on a separate thread.
    tokio::task::spawn_blocking(|| albion_detect::find_albion_pid())
        .await
        .unwrap_or(None)
}

// ─── Lootlog (captured via packet sniffing) ──────────────────────────────────

/// Returns captured loot events for the current session.
#[tauri::command]
async fn get_captured_loot(
    state: tauri::State<'_, AppState>,
) -> Result<Vec<lootlog::LootRow>, String> {
    let buf = state.sniffer.loot.lock().await;
    Ok(buf
        .iter()
        .map(|l| {
            // UI shows name in the user's language; item_id kept for reference.
            let (item_id, en, pt, es) = lootlog::resolve(l.item_index);
            lootlog::LootRow {
                ts: Some(l.ts.clone()),
                item_id,
                item_name: en,
                item_name_pt: pt,
                item_name_es: es,
                quantity: l.quantity as i64,
                looted_by: l.looted_by.clone(),
                looted_by_guild: String::new(),
                looted_from: l.looted_from.clone(),
            }
        })
        .collect())
}

/// Clears the captured loot buffer.
#[tauri::command]
async fn clear_captured_loot(state: tauri::State<'_, AppState>) -> Result<(), String> {
    let mut buf = state.sniffer.loot.lock().await;
    buf.clear();
    // Also delete the persisted session file so cleared loot doesn't
    // reappear on restart.
    let _ = crate::lootlog::save_session(&[]);

    let mut s = state.sniffer.stats.lock().await;
    s.loot_count = 0;
    Ok(())
}

// ─── Spell names (damage meter) ────────────────────────────────────────────
// Index→name table downloaded from backend once and cached to disk.
// Empty table = UI falls back to "Spell {id}".

static SPELL_TABLE: std::sync::OnceLock<Mutex<Vec<api::SpellName>>> = std::sync::OnceLock::new();

fn spell_table() -> &'static Mutex<Vec<api::SpellName>> {
    SPELL_TABLE.get_or_init(|| Mutex::new(Vec::new()))
}

static ITEM_NAMES_MAP: std::sync::OnceLock<Mutex<std::collections::HashMap<String, String>>> =
    std::sync::OnceLock::new();

fn item_names_map() -> &'static Mutex<std::collections::HashMap<String, String>> {
    ITEM_NAMES_MAP.get_or_init(|| Mutex::new(std::collections::HashMap::new()))
}

fn item_names_cache_path() -> std::path::PathBuf {
    dirs::config_dir()
        .unwrap_or_else(|| ".".into())
        .join("ziggs-companion")
        .join("item_names_map_v1.json")
}

/// Loads UniqueName → game_name from disk cache or downloads from backend.
/// Retries until success (autostart may run before network is up).
async fn load_item_names_map() {
    if let Ok(bytes) = std::fs::read(item_names_cache_path()) {
        if let Ok(v) = serde_json::from_slice::<std::collections::HashMap<String, String>>(&bytes) {
            if !v.is_empty() {
                *item_names_map().lock().await = v;
                return;
            }
        }
    }
    let api = api::ApiClient::new(config::API_BASE_URL);
    loop {
        match api.items_map().await {
            Ok(v) if !v.is_empty() => {
                if let Ok(bytes) = serde_json::to_vec(&v) {
                    let _ = std::fs::write(item_names_cache_path(), bytes);
                }
                *item_names_map().lock().await = v;
                return;
            }
            Ok(_) => {
                tracing::info!("items-map empty from backend (not seeded)");
                return;
            }
            Err(e) => {
                tracing::warn!("items-map download failed: {e:#}, retry in 60s");
                tokio::time::sleep(std::time::Duration::from_secs(60)).await;
            }
        }
    }
}

/// Converts UniqueName → game_name using the loaded map. Returns the raw
/// UniqueName as fallback when the key is missing.
pub async fn to_game_name(unique_name: &str) -> String {
    let map = item_names_map().lock().await;
    map.get(unique_name)
        .cloned()
        .unwrap_or_else(|| unique_name.to_string())
}

fn spell_cache_path() -> std::path::PathBuf {
    dirs::config_dir()
        .unwrap_or_else(|| ".".into())
        .join("ziggs-companion")
        // Bump filename when table CONTENT changes: old cache deserializes
        // without error (new fields are Option), so a rename is needed for
        // users to pick up improvements.
        .join("spell_names_v6.json")
}

/// Loads from disk cache or downloads from backend. Retries until success:
/// with autostart the companion starts before the network is ready, and a
/// single attempt would silently fail.
async fn load_spell_names() {
    if let Ok(bytes) = std::fs::read(spell_cache_path()) {
        if let Ok(v) = serde_json::from_slice::<Vec<api::SpellName>>(&bytes) {
            if !v.is_empty() {
                *spell_table().lock().await = v;
                return;
            }
        }
    }
    let api = api::ApiClient::new(config::API_BASE_URL);
    loop {
        match api.spell_names().await {
            Ok(v) if !v.is_empty() => {
                if let Ok(bytes) = serde_json::to_vec(&v) {
                    let _ = std::fs::write(spell_cache_path(), bytes);
                }
                *spell_table().lock().await = v;
                return;
            }
            // Backend without seeded dump: retrying won't help.
            Ok(_) => {
                tracing::info!("spell table empty from backend (not seeded)");
                return;
            }
            Err(e) => tracing::warn!("spell names failed, retrying in 60s: {e:#}"),
        }
        tokio::time::sleep(std::time::Duration::from_secs(60)).await;
    }
}

// ─── Damage meter ─────────────────────────────────────────────────────────────

#[derive(serde::Serialize)]
struct SkillRow {
    id: i32,
    /// Resolved name from the dump table. None = not in table → UI shows "Spell {id}".
    name: Option<String>,
    /// Translations. Language lives in the webview's localStorage, not Rust config,
    /// so we send all three and let the UI pick. None falls back to `name`.
    name_pt: Option<String>,
    name_es: Option<String>,
    /// uniquename from the dump (e.g. "AIR_RAID") — for cross-referencing.
    unique_name: Option<String>,
    /// Icon key for render.albiononline.com/v1/spell/{id}.png.
    /// Differs from unique_name for internal sub-spells (generic passive art);
    /// in that case, carries the parent's id.
    icon: Option<String>,
    /// Hit events (damage instances), not casts — see SpellAcc.
    hits: u64,
    total: i64,
    avg: i64,
    max_hit: i64,
    /// Share of THIS player's total damage dealt by this skill.
    pct: f64,
    /// Weapon family that owns this skill — used to infer the player's weapon.
    fam: Option<String>,
}

#[derive(serde::Serialize)]
struct DamageRow {
    name: String,
    /// Inferred weapon family (bow, dagger, …). None = only auto-attack or
    /// no recognized weapon family among used skills.
    weapon: Option<String>,
    damage: i64,
    dps: i64,
    skills: Vec<SkillRow>,
    /// DPS over the last TIMELINE_SECS seconds, oldest first.
    /// Index = seconds ago (0 = 3 min ago, end = now), with zeros for idle seconds.
    /// The UI just draws the array without knowing about timestamps.
    timeline: Vec<i64>,
}

/// Per-player damage for the session, resolved by name and sorted by damage desc.
/// Includes per-skill breakdown and timeline for row expansion.
/// Unknown IDs (mobs) are discarded — only named players are shown.
#[tauri::command]
/// `vs_players` = only damage where the TARGET was a player (excludes mobs/structures).
async fn get_damage_meter(
    state: tauri::State<'_, AppState>,
    vs_players: bool,
) -> Result<Vec<DamageRow>, String> {
    let window = crate::photon_parser::TIMELINE_SECS;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let offset = state.config.lock().await.spell_index_offset;

    // ── Phase 1: under sniffer locks, aggregate only ──────────────────────
    //
    // Group by NAME: the same player has multiple entity IDs per session (new ID
    // each time they re-enter vis range), and `entities` never forgets.
    // Without this merge, the same player appears as multiple rows with split
    // damage — and since React uses name as key, keys collide and the list
    // duplicates on every data source switch.
    //
    // Lock scope is intentionally narrow: the packet loop needs these same
    // locks on EVERY hit. Previously the entire formatting (dense timeline
    // of 180 points per player, name clone, skill sort, spell table lookup)
    // happened under the locks, freezing packet capture every 2s — right
    // during ZvZ, when there's the most traffic.
    let merged: Vec<(String, crate::photon_parser::DamageAcc)> = {
        let names = state.sniffer.entities.lock().await;
        let dmg = if vs_players {
            state.sniffer.damage_vs_players.lock().await
        } else {
            state.sniffer.damage.lock().await
        };
        let mut by_name: std::collections::HashMap<String, crate::photon_parser::DamageAcc> =
            std::collections::HashMap::new();
        for (id, acc) in dmg.iter() {
            if let Some(name) = names.get(id) {
                by_name.entry(name.clone()).or_default().merge(acc);
            }
        }
        by_name.into_iter().collect()
    }; // sniffer locks released here — packet capture resumes freely

    // ── Phase 2: formatting, no sniffer locks ───────────────────────────
    let spells = spell_table().lock().await;
    let mut rows: Vec<DamageRow> = merged
        .iter()
        .map(|(name, acc)| {
            {
                let total_dmg = acc.damage.max(1.0);
                let mut skills: Vec<SkillRow> = acc
                    .spells
                    .iter()
                    .map(|(sid, sp)| {
                        // Negative index = auto-attack/unknown; never indexes the table.
                        let entry = sid
                            .checked_add(offset)
                            .filter(|i| *i >= 0)
                            .and_then(|i| spells.get(i as usize));
                        SkillRow {
                            id: *sid,
                            name: entry.map(|e| e.name.clone()),
                            name_pt: entry.and_then(|e| e.pt.clone()),
                            name_es: entry.and_then(|e| e.es.clone()),
                            unique_name: entry.map(|e| e.id.clone()),
                            icon: entry.map(|e| e.icon.clone().unwrap_or_else(|| e.id.clone())),
                            hits: sp.hits,
                            total: sp.total as i64,
                            avg: if sp.hits > 0 {
                                (sp.total / sp.hits as f64) as i64
                            } else {
                                0
                            },
                            max_hit: sp.max_hit as i64,
                            pct: (sp.total / total_dmg) * 100.0,
                            fam: entry.and_then(|e| e.fam.clone()),
                        }
                    })
                    .collect();
                skills.sort_by(|a, b| b.total.cmp(&a.total));

                // Player's weapon = family of the highest-damage skill.
                // We can't read equipment (NewCharacter only has id + name), so we
                // infer from usage. skills is already sorted by damage, so the
                // first with a known family wins — shared passives won't decide.
                let weapon = skills.iter().find_map(|s| s.fam.clone());

                // Sparse buckets → dense array aligned to `now` for the chart.
                let mut timeline = vec![0i64; window as usize];
                let oldest = now.saturating_sub(window - 1);
                for (sec, d) in &acc.timeline {
                    if *sec >= oldest && *sec <= now {
                        timeline[(*sec - oldest) as usize] = *d as i64;
                    }
                }
                DamageRow {
                    name: (*name).clone(),
                    weapon,
                    damage: acc.damage as i64,
                    dps: acc.dps() as i64,
                    skills,
                    timeline,
                }
            }
        })
        .collect();
    rows.sort_by(|a, b| b.damage.cmp(&a.damage));
    Ok(rows)
}

#[tauri::command]
async fn clear_damage_meter(state: tauri::State<'_, AppState>) -> Result<(), String> {
    // Clear both maps: clearing only one would leave the toggle showing stale data.
    state.sniffer.damage.lock().await.clear();
    state.sniffer.damage_vs_players.lock().await.clear();
    Ok(())
}

/// Generates lootlogger-format CSV from captured loot and saves to file.
#[tauri::command]
async fn save_lootlog_csv(
    state: tauri::State<'_, AppState>,
    app: tauri::AppHandle,
) -> Result<String, String> {
    let buf = state.sniffer.loot.lock().await;
    let csv = lootlog::build_csv_from_loot(&buf);
    drop(buf);
    let path = lootlog::save_csv(&csv).map_err(|e| format!("save failed: {e}"))?;
    {
        let mut s = state.lootlog.lock().await;
        s.last_saved_path = Some(path.clone());
    }
    let _ = app.opener().reveal_item_in_dir(&path);
    Ok(path)
}

/// Opens any URL in the default browser. Used for legal links ("Terms",
/// "Privacy") in the Config About screen.
#[tauri::command]
async fn open_url(app: tauri::AppHandle, url: String) -> Result<(), String> {
    app.opener()
        .open_url(&url, None::<&str>)
        .map_err(|e| format!("failed to open browser: {e}"))
}

/// Mantém perfis aquecidos no site enquanto o jogo está aberto. Ciclo de 5 min.
/// Only NAMING — the backend fetches data from Albion (never trusts the client):
/// - Every cycle: SEEN players (`entities`) → `/warm/seen` (refresh-only; covers
///   sub-threshold fights the tracker misses).
/// - Every ~20min: own character → `/warm` (bootstraps unknown gatherers/solos).
/// Region comes from the detected AODP server (west/east/europe → americas/asia/europe).
async fn warm_self_worker(
    entities: Arc<Mutex<std::collections::HashMap<i64, String>>>,
    stats: Arc<Mutex<sniffer::SniffStats>>,
    aodp_server: Arc<Mutex<Option<aodp::AodpServer>>>,
) {
    let mut last_logged: Option<(String, String)> = None;
    let mut tick: u32 = 0;
    loop {
        tokio::time::sleep(std::time::Duration::from_secs(5 * 60)).await;
        tick += 1;

        let (name, online) = {
            let s = stats.lock().await;
            (s.player_name.clone(), s.online)
        };
        if !online {
            continue; // game closed: nothing to warm
        }
        let region = match aodp_server.lock().await.as_ref().map(|s| s.region()) {
            Some("east") => "asia",
            Some("europe") => "europe",
            Some("west") => "americas",
            _ => continue, // unknown region: skip
        };
        let api = api::ApiClient::new(config::API_BASE_URL);

        // Phase 2: seen players (dedup, exclude self, cap 100 — backend truncates anyway).
        // entities only grows during the session; re-sending is harmless (idempotent).
        let seen: Vec<String> = {
            let e = entities.lock().await;
            let uniq: std::collections::HashSet<String> = e
                .values()
                .filter(|n| !n.is_empty() && **n != name)
                .cloned()
                .collect();
            uniq.into_iter().take(100).collect()
        };
        if !seen.is_empty() {
            if let Err(e) = api.warm_seen(&seen, region).await {
                tracing::debug!("warm/seen failed: {e:#}");
            }
        }

        // Phase 1: own character every ~20min (1st cycle, then every 4th).
        if !name.is_empty() && tick % 4 == 1 {
            match api.warm_profile(&name, region).await {
                Ok(out) => {
                    let cur = (name.clone(), region.to_string());
                    if last_logged.as_ref() != Some(&cur) {
                        tracing::info!("warm: naming {} ({}) — {}", name, region, out.status);
                        last_logged = Some(cur);
                    }
                }
                Err(e) => tracing::debug!("warm failed: {e:#}"),
            }
        }
    }
}

/// Background worker that estimates the silver value of captured loot —
/// purely illustrative badge for the Lootlog tab. Aggregates by item_id
/// (dedup) and calls /companion/lootlog/silver-estimate. 30s poll.
async fn loot_silver_worker(
    loot: Arc<Mutex<Vec<photon_parser::LootEvent>>>,
    stats: Arc<Mutex<sniffer::SniffStats>>,
) {
    let api = api::ApiClient::new(config::API_BASE_URL);
    loop {
        tokio::time::sleep(std::time::Duration::from_secs(30)).await;
        // Aggregate by item_id (dedup) — backend prices by item, not by line.
        let mut agg: std::collections::HashMap<String, i64> = std::collections::HashMap::new();
        {
            let buf = loot.lock().await;
            for ev in buf.iter() {
                let (item_id, _, _, _) = lootlog::resolve(ev.item_index);
                if item_id.is_empty() {
                    continue;
                }
                *agg.entry(item_id).or_insert(0) += ev.quantity as i64;
            }
        }
        if agg.is_empty() {
            // No loot — reset badge instead of showing stale value.
            let mut s = stats.lock().await;
            s.loot_silver_total = 0;
            continue;
        }
        let items: Vec<(String, i64)> = agg.into_iter().collect();
        match api.loot_silver_estimate(&items).await {
            Ok(total) => {
                let mut s = stats.lock().await;
                s.loot_silver_total = total as u64;
            }
            Err(e) => tracing::debug!("silver-estimate failed: {e:#}"),
        }
    }
}


// ─── Autostart (Task Scheduler on Windows — admin without UAC prompt on boot) ──

/// Autostart via Task Scheduler (Windows) — runs as admin on boot without UAC
/// prompt (RunLevel HighestAvailable). Starts as a normal window: ads need to
/// show on boot to cover tunnel VPS cost. Closing the window still goes to
/// tray if minimize_to_tray is on.
#[cfg(target_os = "windows")]
fn set_autostart(enable: bool) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        let exe = std::env::current_exe()
            .map_err(|e| format!("current_exe: {e}"))?
            .to_string_lossy()
            .to_string();
        if enable {
            let exe_ = exe.replace('\"', "\\\"");
            let xml = format!(
                r#"<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <Enabled>true</Enabled>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
  </Settings>
  <Actions>
    <Exec>
      <Command>"{exe_}"</Command>
    </Exec>
  </Actions>
</Task>"#
            );
            let tmp = std::env::temp_dir().join("ziggs-companion-task.xml");
            std::fs::write(&tmp, &xml).map_err(|e| format!("write xml: {e}"))?;
            let out = crate::winutil::no_window(std::process::Command::new("schtasks"))
                .args([
                    "/Create",
                    "/TN",
                    "ZiggsCompanion",
                    "/XML",
                    tmp.to_str().unwrap_or(""),
                    "/F",
                ])
                .output()
                .map_err(|e| format!("schtasks: {e}"))?;
            let _ = std::fs::remove_file(&tmp);
            if !out.status.success() {
                return Err(format!(
                    "schtasks: {}",
                    String::from_utf8_lossy(&out.stderr)
                ));
            }
        } else {
            let _ = crate::winutil::no_window(std::process::Command::new("schtasks"))
                .args(["/Delete", "/TN", "ZiggsCompanion", "/F"])
                .output();
        }
        Ok(())
    }
    #[cfg(not(target_os = "windows"))]
    {
        // macOS/Linux use tauri_plugin_autostart (LaunchAgent / .desktop) — no admin needed.
        let _ = enable;
        Err("autostart on macOS/Linux must use the plugin via set_config".into())
    }
}

// ─── Tunnel commands ─────────────────────────────────────────────────────────

#[tauri::command]
fn tunnel_generate_keypair() -> serde_json::Value {
    let (priv_b64, pub_b64) = tunnel::generate_keypair();
    serde_json::json!({
        "private_key": priv_b64,
        "public_key": pub_b64,
    })
}

#[tauri::command]
async fn tunnel_start(
    state: tauri::State<'_, AppState>,
    app: tauri::AppHandle,
) -> Result<(), String> {
    let mut running = state.tunnel_running.lock().await;
    if *running {
        return Ok(());
    }
    *running = true;
    // Resolve which VPS to use. Priority:
    // 1. tunnel_routing for the current Albion region (if game detected)
    // 2. Any assigned route (first one — the user wants a VPS connected)
    // 3. Preset for current region
    let albion_region = current_region(&state).await;
    let cfg = state.config.lock().await.clone();
    let vps_id = cfg
        .tunnel_routing
        .get(&albion_region)
        .cloned()
        .or_else(|| cfg.tunnel_routing.values().next().cloned())
        .unwrap_or_default();
    let preset = tunnel_presets::for_id(&vps_id).await;
    let mut cfg = cfg;
    if let Some(p) = preset {
        cfg.tunnel_endpoint = p.endpoint;
        cfg.tunnel_server_pubkey = p.server_pubkey;
    }
    if cfg.tunnel_endpoint.is_empty() {
        *running = false;
        return Err(format!(
            "No VPS assigned for region '{}'. Assign one in the server selection table.",
            albion_region
        ));
    }
    if cfg.tunnel_client_privkey.is_empty() {
        let (priv_b64, _pub_b64) = tunnel::generate_keypair();
        cfg.tunnel_client_privkey = priv_b64;
    }
    let _ = config::save(&cfg);
    let _ = app.emit("config-changed", ());
    let tunnel_cfg = tunnel::TunnelConfig {
        endpoint: cfg.tunnel_endpoint,
        server_pubkey: cfg.tunnel_server_pubkey,
        client_privkey: cfg.tunnel_client_privkey,
        enabled: cfg.tunnel_enabled,
        albion_region: albion_region.clone(),
    };
    let tunnel = state.tunnel.clone();
    tunnel.prepare_start();
    let running_flag = Arc::clone(&state.tunnel_running);
    tokio::spawn(async move {
        tunnel.run(tunnel_cfg).await;
        *running_flag.lock().await = false;
    });
    Ok(())
}

/// Region for tunnel server selection. Prefers the AODP-detected region
/// (from the running game); falls back to the persisted config region so
/// the tunnel works without Albion open.
async fn current_region(state: &tauri::State<'_, AppState>) -> String {
    match state
        .sniffer
        .aodp_server
        .lock()
        .await
        .as_ref()
        .map(|s| s.region())
    {
        Some("east") => "asia".into(),
        Some("europe") => "europe".into(),
        Some("west") => "americas".into(),
        _ => state.config.lock().await.region.clone(),
    }
}

#[tauri::command]
async fn tunnel_stop(state: tauri::State<'_, AppState>) -> Result<(), String> {
    state.tunnel.stop().await;
    // Don't allow a new start while the old task still holds Wintun, socket
    // and routes. Prevents two concurrent tunnels after a fast stop/start.
    for _ in 0..100 {
        if !*state.tunnel_running.lock().await {
            return Ok(());
        }
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
    return Err("timeout stopping tunnel".into());
}

#[tauri::command]
async fn tunnel_status(state: tauri::State<'_, AppState>) -> Result<TunnelStatus, String> {
    Ok(state.tunnel.status.lock().await.clone())
}

#[derive(serde::Serialize)]
struct RegionInfo {
    region: String,
    label: String,
    country: String,
    available: bool,
    endpoint: String,
    latency_ms: Option<f64>,
    online: bool,
    /// Ping to each Albion server through this VPS (PC→VPS, same for all columns).
    cell_pings: std::collections::HashMap<String, Option<f64>>,
}

#[derive(serde::Serialize)]
struct AlbionServerInfo {
    region: String,
}

#[derive(serde::Serialize)]
struct RoutingMatrix {
    /// VPS servers (rows) — first entry is always "direct" (no VPS)
    vps: Vec<RegionInfo>,
    /// Albion server regions (columns)
    albion: Vec<AlbionServerInfo>,
    /// Current routing: albion_region -> vps_region (from config, empty = direct)
    routing: std::collections::HashMap<String, String>,
}

#[tauri::command]
async fn tunnel_regions(state: tauri::State<'_, AppState>) -> Result<RoutingMatrix, String> {
    let albion_regions: Vec<String> = tunnel_presets::ALBION_GAME_IPS
        .iter()
        .map(|(r, _)| r.to_string())
        .collect();

    // "Direct" row: ICMP ping to each Albion game server IP.
    // Americas (5.188.125.x) blocks ICMP — will show null.
    // Asia and Europe respond to ICMP with real game server RTT.
    let mut direct_pings = std::collections::HashMap::new();
    for (region, ip) in tunnel_presets::ALBION_GAME_IPS {
        direct_pings.insert(region.to_string(), ping_host(ip).await);
    }
    let mut vps_rows = vec![RegionInfo {
        region: "direct".to_string(),
        label: String::new(),
        country: String::new(),
        available: true,
        endpoint: String::new(),
        latency_ms: None,
        online: true,
        cell_pings: direct_pings,
    }];

    // VPS rows: fetch manifest from the site, ping each VPS, fetch VPS→Albion
    // pings from each VPS's own ping server. Only VPS that respond to ICMP
    // appear — offline ones are hidden automatically.
    let manifest = tunnel_presets::fetch_manifest().await;
    for vps in &manifest {
        let host = vps.endpoint.split(':').next().unwrap_or("");
        let pc_to_vps = match ping_host(host).await {
            Some(ms) => ms,
            None => continue, // VPS offline — don't show it
        };

        // Fetch VPS→Albion pings from this VPS's ping server.
        let vps_to_albion: std::collections::HashMap<String, Option<f64>> =
            match reqwest::get(&vps.ping_url).await {
                Ok(resp) => match resp
                    .json::<std::collections::HashMap<String, Option<f64>>>()
                    .await
                {
                    Ok(map) => map,
                    Err(_) => std::collections::HashMap::new(),
                },
                Err(_) => std::collections::HashMap::new(),
            };

        let mut cell_pings = std::collections::HashMap::new();
        for r in &albion_regions {
            // ponytail: Americas game server blocks ICMP from VPS too; estimate ~5ms (same DC).
            let vps_to_srv = vps_to_albion.get(r).copied().flatten().or_else(|| {
                if r == "americas" {
                    Some(5.0)
                } else {
                    None
                }
            });
            let total = vps_to_srv.map(|v| pc_to_vps + v);
            cell_pings.insert(r.clone(), total);
        }
        vps_rows.push(RegionInfo {
            region: vps.id.clone(),
            label: vps.label.clone(),
            country: vps.country.clone(),
            available: true,
            endpoint: vps.endpoint.clone(),
            latency_ms: Some(pc_to_vps),
            online: true,
            cell_pings,
        });
    }

    let albion: Vec<AlbionServerInfo> = albion_regions
        .iter()
        .map(|r| AlbionServerInfo { region: r.clone() })
        .collect();
    let routing = state.config.lock().await.tunnel_routing.clone();

    Ok(RoutingMatrix {
        vps: vps_rows,
        albion,
        routing,
    })
}

/// Set which VPS to use for an Albion region. Empty vps_region = none (direct).
#[tauri::command]
async fn set_tunnel_route(
    state: tauri::State<'_, AppState>,
    app: tauri::AppHandle,
    albion_region: String,
    vps_region: String,
) -> Result<(), String> {
    let mut cfg = state.config.lock().await;
    if vps_region.is_empty() {
        cfg.tunnel_routing.remove(&albion_region);
    } else {
        cfg.tunnel_routing.insert(albion_region, vps_region);
    }
    let _ = config::save(&cfg);
    drop(cfg);
    let _ = app.emit("config-changed", ());
    // If the tunnel is running, restart it so the new route takes effect
    // (the kick in add_albion_routes forces the game to reconnect with
    // sockets that follow the new tunnel routes).
    let running = *state.tunnel_running.lock().await;
    if running {
        let tunnel = state.tunnel.clone();
        tunnel.stop().await;
        // Wait for the old tunnel task to finish.
        for _ in 0..100 {
            if !*state.tunnel_running.lock().await {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        }
        // Re-resolve VPS endpoint from routing config.
        let albion_region = current_region(&state).await;
        let cfg = state.config.lock().await.clone();
        let vps_id = cfg
            .tunnel_routing
            .get(&albion_region)
            .cloned()
            .or_else(|| cfg.tunnel_routing.values().next().cloned())
            .unwrap_or_default();
        let preset = tunnel_presets::for_id(&vps_id).await;
        let mut cfg = cfg;
        if let Some(p) = preset {
            cfg.tunnel_endpoint = p.endpoint;
            cfg.tunnel_server_pubkey = p.server_pubkey;
        }
        if cfg.tunnel_endpoint.is_empty() {
            return Ok(()); // No VPS assigned — direct routing.
        }
        if cfg.tunnel_client_privkey.is_empty() {
            let (priv_b64, _) = tunnel::generate_keypair();
            cfg.tunnel_client_privkey = priv_b64;
        }
        let _ = config::save(&cfg);
        let tunnel_cfg = tunnel::TunnelConfig {
            endpoint: cfg.tunnel_endpoint,
            server_pubkey: cfg.tunnel_server_pubkey,
            client_privkey: cfg.tunnel_client_privkey,
            enabled: cfg.tunnel_enabled,
            albion_region: albion_region.clone(),
        };
        tunnel.prepare_start();
        let running_flag = Arc::clone(&state.tunnel_running);
        tokio::spawn(async move {
            tunnel.run(tunnel_cfg).await;
            *running_flag.lock().await = false;
        });
    }
    Ok(())
}

/// Ping a host via the OS `ping` command (works on all platforms, no
/// admin needed, reliable ICMP). Returns average RTT in ms from 3 probes.
/// Parses the last line of ping output which contains min/avg/max stats.
/// Locale-independent: finds the pattern "= XXXms" in the stats line.
pub async fn ping_host(host: &str) -> Option<f64> {
    let host = host.to_string();
    tokio::task::spawn_blocking(move || {
        #[cfg(target_os = "windows")]
        let output = crate::winutil::no_window(std::process::Command::new("ping"))
            .args(["-n", "3", "-w", "2000", &host])
            .output();
        #[cfg(not(target_os = "windows"))]
        let output = std::process::Command::new("ping")
            .args(["-c", "3", "-W", "2", &host])
            .output();
        let output = output.ok()?;
        let text = String::from_utf8_lossy(&output.stdout);

        // Extract all numeric values followed by "ms" from the stats line.
        // Works across locales (PT-BR "Mdia", EN "Average", ES "Promedio").
        // The stats line is the last non-empty line containing "=".
        #[cfg(target_os = "windows")]
        {
            // Windows: "Minimum = 127ms, Maximum = 128ms, M�dia = 127ms"
            // Find the stats line (last line with "=" and "ms")
            let stats_line = text
                .lines()
                .rev()
                .find(|l| l.contains('=') && l.contains("ms"))?;
            // Parse the SECOND number (avg) from the three values
            let nums: Vec<f64> = stats_line
                .split(',')
                .filter_map(|part| {
                    let part = part.trim();
                    // Find "= " followed by digits then "ms"
                    if let Some(eq_pos) = part.find('=') {
                        let rest = part[eq_pos + 1..].trim();
                        let end = rest.find("ms")?;
                        rest[..end].trim().parse::<f64>().ok()
                    } else {
                        None
                    }
                })
                .collect();
            // Windows gives [min, max, avg] — take the last one (avg)
            if nums.is_empty() {
                return None;
            }
            return Some(nums[nums.len() - 1]);
        }
        #[cfg(not(target_os = "windows"))]
        {
            // Linux: "rtt min/avg/max/mdev = 128.123/129.456/130.789/1.234 ms"
            let stats_line = text
                .lines()
                .rev()
                .find(|l| l.contains("rtt") && l.contains('='))?;
            let parts: Vec<&str> = stats_line.split('=').nth(1)?.trim().split('/').collect();
            if parts.len() >= 2 {
                parts[1].trim().parse::<f64>().ok()
            } else {
                None
            }
        }
    })
    .await
    .ok()
    .flatten()
}

/// TCP "ping": resolve once, then measure pure connect RTT 3 times.
/// Used for TCP services (not WireGuard VPS).
pub async fn ping_port(host: &str, port: u16) -> Option<f64> {
    use tokio::net::TcpStream;
    use tokio::time::timeout;

    // Resolve once (not measured)
    let addrs = match tokio::net::lookup_host((host, port)).await {
        Ok(a) => a.collect::<Vec<_>>(),
        Err(_) => return None,
    };
    if addrs.is_empty() {
        return None;
    }

    let mut total = 0.0_f64;
    let mut count = 0_u32;
    for addr in &addrs {
        if count >= 3 {
            break;
        }
        let start = std::time::Instant::now();
        let conn = timeout(std::time::Duration::from_secs(2), TcpStream::connect(addr)).await;
        if let Ok(Ok(_)) = conn {
            total += start.elapsed().as_secs_f64() * 1000.0;
            count += 1;
        }
    }
    if count == 0 {
        return None;
    }
    Some(total / count as f64)
}

#[tauri::command]
async fn tunnel_is_admin() -> bool {
    // Delegates to the SAME check the startup uses. Previously this had its own
    // copy with PROCESS_QUERY_INFORMATION — a bug already fixed in
    // is_windows_admin. Two copies of the same check is how the bug survived
    // the first fix.
    #[cfg(target_os = "windows")]
    {
        is_windows_admin()
    }
    #[cfg(not(target_os = "windows"))]
    {
        unsafe { libc::geteuid() == 0 }
    }
}

#[cfg(target_os = "windows")]
fn is_windows_admin() -> bool {
    use windows_sys::Win32::Foundation::CloseHandle;
    use windows_sys::Win32::Foundation::HANDLE;
    use windows_sys::Win32::Security::{
        GetTokenInformation, TokenElevation, TOKEN_ELEVATION, TOKEN_INFORMATION_CLASS,
    };
    use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};

    // TOKEN_QUERY (0x0008) is the minimum access for GetTokenInformation;
    // we previously used PROCESS_QUERY_INFORMATION (0x0400), which some
    // security configs deny even for elevated processes. When that happened,
    // is_windows_admin() returned false and the companion re-launched via
    // ShellExecuteW("runas") in a loop.
    const TOKEN_QUERY: u32 = 0x0008;
    unsafe {
        let mut token: HANDLE = 0;
        if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) != 0 {
            let mut elevation: TOKEN_ELEVATION = std::mem::zeroed();
            let mut ret_len = 0u32;
            let ok = GetTokenInformation(
                token,
                TokenElevation as TOKEN_INFORMATION_CLASS,
                &mut elevation as *mut _ as *mut std::ffi::c_void,
                std::mem::size_of::<TOKEN_ELEVATION>() as u32,
                &mut ret_len,
            );
            CloseHandle(token);
            ok != 0 && elevation.TokenIsElevated != 0
        } else {
            false
        }
    }
}

/// Shows the window AND forces WebView2 surface presentation.
///
/// WebView2 bug: the renderer has the pixels (screenshot via CDP is perfect)
/// but the window stays white until a resize forces recomposition. Classic
/// "white until resize" with visible:false + late show(). Workaround: ±1px
/// nudges after show. Two nudges with delays because the surface may not
/// exist yet right after show.
fn present_window(w: &tauri::WebviewWindow) {
    let _ = w.show();
    let _ = w.unminimize();
    let _ = w.set_focus();
    let w = w.clone();
    tauri::async_runtime::spawn(async move {
        for delay_ms in [150u64, 900] {
            tokio::time::sleep(std::time::Duration::from_millis(delay_ms)).await;
            if let Ok(size) = w.outer_size() {
                let _ = w.set_size(tauri::PhysicalSize::new(size.width + 1, size.height));
                tokio::time::sleep(std::time::Duration::from_millis(60)).await;
                let _ = w.set_size(tauri::PhysicalSize::new(size.width, size.height));
            }
        }
    });
}

fn build_tray(app: &tauri::AppHandle) -> tauri::Result<()> {
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let show = MenuItem::with_id(app, "show", "Open", true, None::<&str>)?;
    let pause = MenuItem::with_id(app, "pause", "Pause scanner", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &pause, &quit])?;
    TrayIconBuilder::with_id("main-tray")
        .icon(app.default_window_icon().unwrap().clone())
        .menu(&menu)
        .tooltip("Ziggs Companion")
        .on_menu_event(|app, event| match event.id.as_ref() {
            "quit" => {
                let tunnel = app.state::<AppState>().tunnel.clone();
                let handle = app.clone();
                tauri::async_runtime::spawn(async move {
                    stop_tunnel_and_wait(&tunnel).await;
                    // Also scrub stale routes in case the tunnel task already exited.
                    let _ = tunnel::scrub_stale_routes_now();
                    handle.exit(0);
                });
            }
            "show" => {
                if let Some(w) = app.get_webview_window("main") {
                    present_window(&w);
                }
            }
            "pause" => {
                let _ = app.emit("scanner-pause", ());
            }
            _ => {}
        })
        .build(app)?;
    Ok(())
}

async fn stop_tunnel_and_wait(tunnel: &Tunnel) {
    tunnel.stop_quick().await;
    for _ in 0..100 {
        if !tunnel.status.lock().await.running {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
}

// ─── Auto-updater ────────────────────────────────────────────────────────────

/// Checks for update on startup. If an update is available, emits
/// `update-status: "available"` so the UI shows an update button — the user
/// clicks it to download and install. This avoids forcing a restart while the
/// user is mid-CTA. `apply_update` does the download+install+relaunch.
#[cfg(target_os = "windows")]
async fn auto_update(app: &tauri::AppHandle) -> Result<(), anyhow::Error> {
    use tauri_plugin_updater::UpdaterExt;
    let update = match app.updater()?.check().await {
        Ok(Some(u)) => u,
        Ok(None) => return Ok(()),
        Err(e) => return Err(e.into()),
    };
    tracing::info!(
        "auto-update: {} -> {} (waiting for user)",
        update.current_version,
        update.version
    );
    let _ = app.emit("update-status", "available");
    Ok(())
}

/// Download, install, and relaunch. Called when the user clicks the update button.
#[cfg(target_os = "windows")]
async fn apply_update(app: &tauri::AppHandle) -> Result<(), anyhow::Error> {
    use tauri_plugin_updater::UpdaterExt;
    let update = match app.updater()?.check().await {
        Ok(Some(u)) => u,
        Ok(None) => return Ok(()),
        Err(e) => return Err(e.into()),
    };
    let _ = app.emit("update-status", "downloading");
    update
        .download_and_install(
            |chunk, total| {
                tracing::debug!("auto-update: {chunk} / {total:?} bytes");
            },
            || {
                tracing::info!("auto-update: download complete, installing");
            },
        )
        .await?;
    let _ = app.emit("update-status", "installed");
    stop_tunnel_and_wait(&app.state::<AppState>().tunnel).await;
    // app.restart() relaunches WITHOUT admin — the companion needs admin for
    // WinDivert/wintun. Re-launch elevated via ShellExecuteW("runas") and exit,
    // same as the boot-time elevation path. The new process hits the
    // is_windows_admin() check, passes, and continues normally.
    if let Ok(exe) = std::env::current_exe() {
        if let Ok(exe_path) = exe.into_os_string().into_string() {
            use windows_sys::Win32::Foundation::HWND;
            use windows_sys::Win32::UI::Shell::ShellExecuteW;
            use windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;
            let verb: Vec<u16> = "runas\0".encode_utf16().collect();
            let file: Vec<u16> = exe_path.encode_utf16().chain(std::iter::once(0)).collect();
            unsafe {
                ShellExecuteW(
                    0 as HWND,
                    verb.as_ptr(),
                    file.as_ptr(),
                    std::ptr::null(),
                    std::ptr::null(),
                    SW_SHOWNORMAL,
                );
            }
        }
    }
    std::process::exit(0);
}

#[tauri::command]
#[cfg(target_os = "windows")]
async fn check_and_apply_update(app: tauri::AppHandle) -> Result<(), String> {
    apply_update(&app).await.map_err(|e| e.to_string())
}

#[cfg(not(target_os = "windows"))]
#[tauri::command]
async fn check_and_apply_update() -> Result<(), String> {
    Err("auto-update is not available on this platform".into())
}

#[tauri::command]
async fn report_frontend_crash(message: String, stack: String) -> Result<(), String> {
    crash_report::save_frontend(message, stack).map_err(|e| e.to_string())?;
    crash_report::send_pending_once()
        .await
        .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn run() {
    crash_report::install_hook();
    crash_report::init_logging();

    let install_id = config::install_id();
    let mut cfg = config::load();
    cfg.install_id = install_id;

    // Admin is required for WinDivert (packet capture) and wintun (tunnel).
    // In autostart, the Task Scheduler opens with HighestAvailable = no prompt.
    //
    // Anti-loop guard: the elevated child inherits this environment variable.
    // If it still doesn't appear as admin (user denied UAC,
    // or rare bug in is_windows_admin), do NOT re-launch again — show a fatal
    // MessageBox and exit.
    #[cfg(target_os = "windows")]
    {
        let already_tried = std::env::var_os("ZIGGS_ELEV_TRIED").is_some();
        if !is_windows_admin() {
            if already_tried {
                use windows_sys::Win32::UI::WindowsAndMessaging::{
                    MessageBoxW, MB_ICONERROR, MB_OK,
                };
                let title: Vec<u16> = "Ziggs Companion\0".encode_utf16().collect();
                let msg: Vec<u16> =
                    "Ziggs Companion requires administrator privileges to capture packets \
                     (WinDivert) and manage the tunnel (wintun).\n\n\
                     If you declined the UAC prompt, try again and accept it. \
                     If the problem persists, run the companion directly as administrator \
                     (right-click → Run as administrator).\0"
                        .encode_utf16()
                        .collect();
                unsafe {
                    MessageBoxW(
                        0 as windows_sys::Win32::Foundation::HWND,
                        msg.as_ptr(),
                        title.as_ptr(),
                        MB_OK | MB_ICONERROR,
                    );
                }
                std::process::exit(1);
            }
            let exe = std::env::current_exe().unwrap_or_default();
            if let Ok(exe_path) = exe.into_os_string().into_string() {
                use windows_sys::Win32::Foundation::HWND;
                use windows_sys::Win32::UI::Shell::ShellExecuteW;
                use windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;
                // NSIS preserves process arguments after an update, so drop
                // the old marker before the elevated relaunch.
                let args = std::env::args()
                    .skip(1)
                    .filter(|arg| arg != "--ziggs-elev")
                    .collect::<Vec<_>>()
                    .join(" ");
                std::env::set_var("ZIGGS_ELEV_TRIED", "1");
                let verb: Vec<u16> = "runas\0".encode_utf16().collect();
                let file: Vec<u16> = exe_path.encode_utf16().chain(std::iter::once(0)).collect();
                let params: Vec<u16> = args.encode_utf16().chain(std::iter::once(0)).collect();
                unsafe {
                    ShellExecuteW(
                        0 as HWND,
                        verb.as_ptr(),
                        file.as_ptr(),
                        params.as_ptr(),
                        std::ptr::null(),
                        SW_SHOWNORMAL,
                    );
                }
                return;
            }
        }
    }

    let autostart_on = cfg.autostart;
    let start_minimized = std::env::args().any(|a| a == "--minimized");
    let transfer_queue = Arc::new(TransferQueue::new());
    let sniffer = Sniffer::new();
    let scanner = Scanner::new()
        .with_queue(Arc::clone(&transfer_queue))
        .with_debug(Arc::clone(&sniffer.debug));
    let kill_scanner = KillScanner::from_scanner(&scanner);
    let state = AppState {
        config: Arc::new(Mutex::new(cfg)),
        scanner,
        scanner_running: Arc::new(Mutex::new(false)),
        kill_scanner,
        kill_scanner_running: Arc::new(Mutex::new(false)),
        tunnel: Tunnel::new(),
        tunnel_running: Arc::new(Mutex::new(false)),
        transfer_queue,
        sniffer,
        sniffer_running: Arc::new(Mutex::new(false)),
        lootlog: Arc::new(Mutex::new(lootlog::LootlogStatus::default())),
    };

    let mut builder = tauri::Builder::default();

    // Single-instance: if already running, focus existing window and close this one.
    // Must be the first plugin registered to intercept before any other initializes.
    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                present_window(&w);
            }
        }));
    }

    builder
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            None,
        ))
        .manage(state)
        .setup(move |app| {
            crash_report::set_version(app.package_info().version.to_string());
            tauri::async_runtime::spawn(async {
                loop {
                    if let Err(e) = crash_report::send_pending_once().await {
                        tracing::debug!("crash report pending: {e:#}");
                    }
                    tokio::time::sleep(std::time::Duration::from_secs(60)).await;
                }
            });
            // Auto-update: silent check on startup — downloads and installs without
            // confirmation. Passive install (small progress bar), then relaunch.
            #[cfg(target_os = "windows")]
            {
                let _ = app
                    .handle()
                    .plugin(tauri_plugin_updater::Builder::new().build());
                let handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    if let Err(e) = auto_update(&handle).await {
                        tracing::warn!("auto-update: {e:#}");
                    }
                });
            }
            // Spell name table for damage meter: disk cache, otherwise download.
            // In background — the meter works without it (falls back to spell id).
            tauri::async_runtime::spawn(load_spell_names());
            tauri::async_runtime::spawn(load_item_names_map());
            tauri::async_runtime::spawn(lootlog::load_item_names());

            {
                let st = app.state::<AppState>();
                // Illustrative silver badge for the Lootlog tab. Not load-bearing.
                tauri::async_runtime::spawn(loot_silver_worker(
                    Arc::clone(&st.sniffer.loot),
                    Arc::clone(&st.sniffer.stats),
                ));
                // Keeps profiles warm on the site: own character + seen players.
                tauri::async_runtime::spawn(warm_self_worker(
                    Arc::clone(&st.sniffer.entities),
                    Arc::clone(&st.sniffer.stats),
                    Arc::clone(&st.sniffer.aodp_server),
                ));
            }

            build_tray(app.handle())?;
            // Window is hidden in tauri.conf; only show if NOT a minimized boot
            // (--minimized from Task Scheduler = straight to tray).
            if !start_minimized {
                if let Some(w) = app.get_webview_window("main") {
                    present_window(&w);
                }
            }
            if autostart_on {
                #[cfg(target_os = "windows")]
                {
                    let _ = set_autostart(true);
                }
                #[cfg(not(target_os = "windows"))]
                {
                    let autostart_mgr = app.autolaunch();
                    let _ = autostart_mgr.enable();
                }
            }
            // Auto-start scanner and tunnel if their toggles are on.
            let state: tauri::State<AppState> = app.state();
            if let Err(e) = tunnel::scrub_stale_routes_now() {
                tracing::warn!("tunnel startup cleanup: {e:#}");
            }
            // Generate the client WireGuard keypair on first launch so the user
            // never has to. The server endpoint+pubkey come from tunnel_presets
            // (region-detected); the client private key is always our own.
            {
                let mut cfg = state.config.blocking_lock();
                if cfg.tunnel_client_privkey.is_empty() {
                    let (priv_b64, _pub_b64) = tunnel::generate_keypair();
                    cfg.tunnel_client_privkey = priv_b64;
                    let _ = config::save(&cfg);
                }
            }
            let cfg = state.config.blocking_lock().clone();
            // Sync pvp_pause from config into scanner.
            *state.scanner.pvp_pause.blocking_lock() = cfg.pvp_pause_transfer;
            // Sync capture gates with persisted config.
            // capture_prices is always true (core feature); the gate exists only
            // for emergency manual pause.
            {
                use std::sync::atomic::Ordering;
                state
                    .sniffer
                    .capture_loot
                    .store(cfg.collect_auto_lootlog, Ordering::Relaxed);
                state
                    .sniffer
                    .capture_damage
                    .store(cfg.collect_damage_meter, Ordering::Relaxed);
                state.sniffer.capture_prices.store(true, Ordering::Relaxed);
                state
                    .sniffer
                    .feed_aodp
                    .store(cfg.feed_aodp, Ordering::Relaxed);
            }
            // Pre-loads local exits and their handshakes before the user opens
            // the game. If the tunnel toggle is on, connects afterward using
            // the same result; otherwise just warms the list.
            if !cfg.tunnel_endpoint.is_empty()
                && !cfg.tunnel_server_pubkey.is_empty()
                && !cfg.tunnel_client_privkey.is_empty()
            {
                let tunnel = state.tunnel.clone();
                let running_flag = Arc::clone(&state.tunnel_running);
                let tunnel_cfg = tunnel::TunnelConfig {
                    endpoint: cfg.tunnel_endpoint.clone(),
                    server_pubkey: cfg.tunnel_server_pubkey.clone(),
                    client_privkey: cfg.tunnel_client_privkey.clone(),
                    enabled: cfg.tunnel_enabled,
                    albion_region: cfg.region.clone(),
                };
                if cfg.tunnel_enabled {
                    *state.tunnel_running.blocking_lock() = true;
                    tunnel.prepare_start();
                }
                tauri::async_runtime::spawn(async move {
                    if let Err(e) = tunnel.preload(tunnel_cfg.clone()).await {
                        tracing::warn!("tunnel preload: {e:#}");
                    }
                    if tunnel_cfg.enabled {
                        tunnel.run(tunnel_cfg).await;
                        *running_flag.lock().await = false;
                    }
                });
            }
            // Battle scanning is ALWAYS on (primary purpose of the companion).
            {
                let mut running = state.scanner_running.blocking_lock();
                if !*running {
                    *running = true;
                    state.scanner.prepare_start();
                    let api = api::ApiClient::new(config::API_BASE_URL);
                    let scanner = state.scanner.clone_for_spawn();
                    let running_flag = Arc::clone(&state.scanner_running);
                    tauri::async_runtime::spawn(async move {
                        scanner.run(api, true).await;
                        *running_flag.lock().await = false;
                    });
                }
            }
            // Kill scan runs in parallel — shares throttle/zone with battle scan.
            {
                let mut running = state.kill_scanner_running.blocking_lock();
                if !*running {
                    *running = true;
                    let api = api::ApiClient::new(config::API_BASE_URL);
                    let ks = state.kill_scanner.clone_for_spawn();
                    let running_flag = Arc::clone(&state.kill_scanner_running);
                    tauri::async_runtime::spawn(async move {
                        ks.run(api, true).await;
                        *running_flag.lock().await = false;
                    });
                }
            }
            // ── Single upload queue ───────────────────────────────────────
            //
            // Previously each producer called flush_all directly, and returning to
            // blue zone dumped the entire queue in a burst — network/CPU spike
            // right after a fight, the worst possible moment.
            //
            // Now one place controls pacing: few items per tick, only when
            // heavy_work_ok. Nothing is lost — in risky zones the queue just
            // grows and drains slowly afterward. New producers should only ENQUEUE.
            {
                const TICK_SECS: u64 = 3;
                const CHUNK: usize = 20;
                let q = Arc::clone(&state.transfer_queue);
                let up_sniffer = state.sniffer.clone_shared();
                let up_zone = Arc::clone(&state.scanner.zone);
                let up_pause = Arc::clone(&state.scanner.pvp_pause);
                let up_stats = Arc::clone(&state.scanner.stats);
                tauri::async_runtime::spawn(async move {
                    let api = api::ApiClient::new(config::API_BASE_URL);
                    loop {
                        tokio::time::sleep(std::time::Duration::from_secs(TICK_SECS)).await;
                        if q.pending_count().await == 0 {
                            continue;
                        }
                        if !heavy_work_ok(&up_sniffer, &up_zone, &up_pause).await {
                            continue; // risky zone: wait, don't lose data
                        }
                        let (sent, failed) = q.flush_some(&api, CHUNK).await;
                        if sent > 0 || failed > 0 {
                            up_stats.lock().await.queued_reports = q.pending_count().await;
                        }
                    }
                });
            }
            // Auto-start sniffer (packet capture) — always on, no toggle.
            // Feeds name/map/party to the UI; loot/damage/prices only accumulate
            // when their respective gates are enabled.
            {
                let mut running = state.sniffer_running.blocking_lock();
                if !*running {
                    *running = true;
                    let sniffer = state.sniffer.clone_shared();
                    let generation = sniffer.prepare_start();
                    let running_flag = Arc::clone(&state.sniffer_running);
                    tauri::async_runtime::spawn(async move {
                        sniffer.run_generation(generation).await;
                        if sniffer.is_current(generation) {
                            *running_flag.lock().await = false;
                        }
                    });
                }
            }
            // Periodic price upload: drains the sniffer buffer every 60s,
            // aggregates lowest sell_price_min per (item, quality, city, region) and
            // enqueues for transfer (respects PvP pause; persists on failure).
            {
                let prices_buf = Arc::clone(&state.sniffer.prices);
                let q = Arc::clone(&state.transfer_queue);
                let debug = Arc::clone(&state.sniffer.debug);
                tauri::async_runtime::spawn(async move {
                    loop {
                        tokio::time::sleep(std::time::Duration::from_secs(60)).await;
                        let raw: Vec<serde_json::Value> = {
                            let mut b = prices_buf.lock().await;
                            if b.is_empty() {
                                continue;
                            }
                            b.drain(..).collect()
                        };
                        // Lowest sell_price_min per (item_id, quality, city, region).
                        let mut best: std::collections::HashMap<String, serde_json::Value> =
                            std::collections::HashMap::new();
                        for row in raw {
                            let key = format!(
                                "{}|{}|{}|{}",
                                row.get("item_id").and_then(|v| v.as_str()).unwrap_or(""),
                                row.get("quality").and_then(|v| v.as_i64()).unwrap_or(1),
                                row.get("city").and_then(|v| v.as_str()).unwrap_or(""),
                                row.get("region").and_then(|v| v.as_str()).unwrap_or("west"),
                            );
                            let price = row
                                .get("sell_price_min")
                                .and_then(|v| v.as_i64())
                                .unwrap_or(i64::MAX);
                            let cur = best
                                .get(&key)
                                .and_then(|r| r.get("sell_price_min"))
                                .and_then(|v| v.as_i64());
                            if cur.map_or(true, |c| price < c) {
                                best.insert(key, row);
                            }
                        }
                        let n_rows = best.len();
                        // Chunk at 1900 (backend limits 2000 per request) — large
                        // payloads in a single POST cause 30s timeouts and get
                        // stuck in the queue forever.
                        let aggregated: Vec<serde_json::Value> = best.into_values().collect();
                        for chunk in aggregated.chunks(1900) {
                            q.enqueue_prices(chunk.to_vec()).await;
                        }
                        push_debug(
                            &debug,
                            "info",
                            &format!(
                                "prices: enqueued {n_rows} rows ({} chunks)",
                                (n_rows + 1899) / 1900
                            ),
                        )
                        .await;
                        // Enqueue only — the single uploader handles pacing.
                    }
                });
            }
            // Market history upload: drains buffer every 60s and enqueues for
            // transfer (same persistence and PvP pause as prices).
            {
                let hist_buf = Arc::clone(&state.sniffer.market_history);
                let q = Arc::clone(&state.transfer_queue);
                let debug = Arc::clone(&state.sniffer.debug);
                tauri::async_runtime::spawn(async move {
                    loop {
                        tokio::time::sleep(std::time::Duration::from_secs(60)).await;
                        let rows: Vec<serde_json::Value> = {
                            let mut b = hist_buf.lock().await;
                            if b.is_empty() {
                                continue;
                            }
                            b.drain(..).collect()
                        };
                        let n_rows = rows.len();
                        // Backend limits 2000 rows per request — chunk at 1900.
                        for chunk in rows.chunks(1900) {
                            q.enqueue_market_history(chunk.to_vec()).await;
                        }
                        push_debug(
                            &debug,
                            "info",
                            &format!("market_history: enqueued {n_rows} buckets"),
                        )
                        .await;
                        // Enqueue only — see single uploader comment above.
                    }
                });
            }
            // AODP upload: drains market order batches and uploads (with PoW).
            // One at a time with spacing — PoW is CPU-bound.
            //
            // The most expensive task in the companion and the only one that
            // genuinely spends CPU. Gated behind heavy_work_ok: in PvP with the
            // game open, batches wait in queue (not lost) and upload when the
            // player returns to blue zone or closes the game.
            {
                let aodp_out = Arc::clone(&state.sniffer.aodp_out);
                let debug = Arc::clone(&state.sniffer.debug);
                let aodp_sniffer = state.sniffer.clone_shared();
                let aodp_zone = Arc::clone(&state.scanner.zone);
                let aodp_pause = Arc::clone(&state.scanner.pvp_pause);
                tauri::async_runtime::spawn(async move {
                    let client = reqwest::Client::builder()
                        .user_agent("ziggs-companion/0.1")
                        .timeout(std::time::Duration::from_secs(30))
                        .build()
                        .unwrap_or_default();
                    loop {
                        tokio::time::sleep(std::time::Duration::from_secs(5)).await;
                        // Peek at queue before checking zone: no batch = no work.
                        if aodp_out.lock().await.is_empty() {
                            continue;
                        }
                        if !heavy_work_ok(&aodp_sniffer, &aodp_zone, &aodp_pause).await {
                            continue; // in PvP: batch waits, not lost
                        }
                        let batch = { aodp_out.lock().await.pop() };
                        let Some(batch) = batch else { continue };
                        if let Err(e) = aodp::upload(&client, &batch).await {
                            let line = sniffer::DebugLine {
                                ts: photon_parser::now_iso_utc(),
                                level: "warn".into(),
                                msg: format!("AODP upload failed: {e:#}"),
                            };
                            let mut d = debug.lock().await;
                            d.push(line);
                            if d.len() > 500 {
                                let ex = d.len() - 500;
                                d.drain(..ex);
                            }
                        }
                    }
                });
            }
            // Tunnel does NOT auto-start on boot on purpose: user must enable
            // manually each time. Forcing the click drives ad impressions on
            // the Route tab, covering VPS cost. The tunnel_enabled toggle only
            // controls whether the button appears "on" in the UI.
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let app = window.app_handle();
                let state: tauri::State<AppState> = app.state();
                let cfg = state.config.blocking_lock();
                if cfg.minimize_to_tray {
                    api.prevent_close();
                    let _ = window.hide();
                } else if state.tunnel.status.blocking_lock().running {
                    api.prevent_close();
                    let tunnel = state.tunnel.clone();
                    let window = window.clone();
                    tauri::async_runtime::spawn(async move {
                        stop_tunnel_and_wait(&tunnel).await;
                        let _ = window.destroy();
                    });
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            get_config,
            get_platform_capabilities,
            set_config,
            get_scan_stats,
            start_scanner,
            stop_scanner,
            test_dns,
            apply_dns,
            get_dns_targets,
            save_lootlog_csv,
            get_captured_loot,
            clear_captured_loot,
            get_damage_meter,
            clear_damage_meter,
            tunnel_generate_keypair,
            tunnel_start,
            tunnel_stop,
            tunnel_status,
            tunnel_is_admin,
            tunnel_regions,
            set_tunnel_route,
            check_and_apply_update,
            set_zone,
            flush_transfer_queue,
            pending_count,
            classify_zone,
            get_albion_pid,
            start_sniffer,
            stop_sniffer,
            get_sniff_stats,
            get_sniffer_debug,
            open_url,
            report_frontend_crash,
        ])
        .run(tauri::generate_context!())
        .expect("failed to start companion");
}
#[cfg(test)]
mod policy_tests {
    use super::*;

    fn zona(z: transfer::ZoneType) -> Arc<Mutex<transfer::ZoneType>> {
        Arc::new(Mutex::new(z))
    }

    /// The full "when is it safe to spend resources" policy lives here.
    /// Getting it wrong either way is bad: allowing work in PvP causes lag
    /// deaths; being too strict means data never uploads.
    #[tokio::test]
    async fn heavy_work_ok_all_cases() {
        let sniffer = Sniffer::new();
        let pause_on = Arc::new(Mutex::new(true));
        let pause_off = Arc::new(Mutex::new(false));

        // Game closed (online=false, the default): allows work in any zone.
        assert!(
            heavy_work_ok(&sniffer, &zona(transfer::ZoneType::PvP), &pause_on).await,
            "game closed is the best time to work"
        );

        sniffer.stats.lock().await.online = true;

        assert!(
            !heavy_work_ok(&sniffer, &zona(transfer::ZoneType::PvP), &pause_on).await,
            "in PvP zone: don't touch the CPU"
        );
        assert!(
            heavy_work_ok(&sniffer, &zona(transfer::ZoneType::Blue), &pause_on).await,
            "blue zone: safe to upload"
        );
        assert!(
            heavy_work_ok(&sniffer, &zona(transfer::ZoneType::Unknown), &pause_on).await,
            "unknown zone does not block — only confirmed PvP blocks"
        );
        assert!(
            heavy_work_ok(&sniffer, &zona(transfer::ZoneType::PvP), &pause_off).await,
            "user disabled pause: respect their choice"
        );
    }

}
