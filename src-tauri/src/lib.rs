pub mod albion_detect;
pub mod api;
#[cfg(target_os = "windows")]
pub mod autostart;
#[cfg(test)]
mod capability_config_tests;
pub mod catalog;
pub mod config;
pub mod crash_report;
#[cfg(target_os = "windows")]
pub mod elevation;
pub mod lootlog;
pub mod maps;
#[cfg(target_os = "windows")]
pub mod npcap;
pub mod persist;
pub mod photon_parser;
pub mod sniffer;
#[cfg(target_os = "windows")]
pub mod windivert;
#[cfg(test)]
mod window_config_tests;
pub mod winutil;

use sniffer::{DebugLine, SniffStats, Sniffer};
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

pub struct AppState {
    config: Arc<Mutex<config::CompanionConfig>>,
    sniffer: Sniffer,
    lootlog: Arc<Mutex<lootlog::LootlogStatus>>,
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::Ordering;

    use super::{restore_capture_toggle, second_launch_shows_the_window};
    use crate::{config::CompanionConfig, sniffer::Sniffer};

    #[test]
    fn a_click_shows_the_window_but_the_start_with_windows_does_not() {
        let args = |list: &[&str]| list.iter().map(|arg| arg.to_string()).collect::<Vec<_>>();
        assert!(second_launch_shows_the_window(&args(&["app.exe"])));
        assert!(!second_launch_shows_the_window(&args(&[
            "app.exe",
            "--minimized"
        ])));
    }

    #[test]
    fn failed_damage_setting_save_restores_the_capture_gate() {
        let sniffer = Sniffer::new();
        let previous = CompanionConfig {
            collect_damage_meter: false,
            ..CompanionConfig::default()
        };
        sniffer.capture_damage.store(true, Ordering::Relaxed);

        restore_capture_toggle(&sniffer, "collect_damage_meter", &previous);

        assert!(!sniffer.capture_damage.load(Ordering::Relaxed));
    }

    #[test]
    fn failed_loot_setting_save_restores_the_capture_gate() {
        let sniffer = Sniffer::new();
        let previous = CompanionConfig {
            collect_auto_lootlog: false,
            ..CompanionConfig::default()
        };
        sniffer.capture_loot.store(true, Ordering::Relaxed);

        restore_capture_toggle(&sniffer, "collect_auto_lootlog", &previous);

        assert!(!sniffer.capture_loot.load(Ordering::Relaxed));
    }
}

#[tauri::command]
async fn get_config(state: tauri::State<'_, AppState>) -> Result<config::CompanionConfig, String> {
    Ok(state.config.lock().await.clone())
}
#[tauri::command]
async fn set_config(
    key: String,
    value: serde_json::Value,
    state: tauri::State<'_, AppState>,
    app: tauri::AppHandle,
) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    if key == "capture_method"
        && value == serde_json::json!("windivert")
        && !elevation::is_elevated()
    {
        return reopen_with_windivert(&state, &app).await;
    }

    let mut cfg = state.config.lock().await;
    let changed_autostart = key == "autostart";
    let previous = cfg.clone();

    match (key.as_str(), value) {
        ("autostart", serde_json::Value::Bool(v)) => cfg.autostart = v,
        ("minimize_to_tray", serde_json::Value::Bool(v)) => cfg.minimize_to_tray = v,
        ("collect_damage_meter", serde_json::Value::Bool(v)) => {
            cfg.collect_damage_meter = v;
            state
                .sniffer
                .capture_damage
                .store(v, std::sync::atomic::Ordering::Relaxed);
        }
        ("collect_auto_lootlog", serde_json::Value::Bool(v)) => {
            cfg.collect_auto_lootlog = v;
            state
                .sniffer
                .capture_loot
                .store(v, std::sync::atomic::Ordering::Relaxed);
        }
        ("spell_index_offset", serde_json::Value::Number(v)) => {
            cfg.spell_index_offset = v.as_i64().unwrap_or_default() as i32
        }
        ("capture_method", value) => {
            cfg.capture_method =
                serde_json::from_value(value).map_err(|_| "configuração inválida".to_string())?;
        }
        _ => return Err("configuração inválida".into()),
    };

    if changed_autostart {
        if let Err(error) = apply_autostart(&app, cfg.autostart, cfg.capture_method) {
            *cfg = previous;
            return Err(error);
        }
    }

    if let Err(error) = config::save(&cfg) {
        if changed_autostart {
            let _ = apply_autostart(&app, previous.autostart, previous.capture_method);
        }
        restore_capture_toggle(&state.sniffer, &key, &previous);
        *cfg = previous;
        return Err(error.to_string());
    }

    if cfg.capture_method != previous.capture_method {
        state.sniffer.stop().await;
        start_sniffer(&state.sniffer, cfg.capture_method);
        // Leaving WinDivert: the scheduled task gives way to the Run key.
        if let Err(error) = apply_autostart(&app, cfg.autostart, cfg.capture_method) {
            tracing::warn!("não foi possível ajustar a inicialização automática: {error}");
        }
    }

    Ok(())
}

/// Answer to the page when the user says no to the Windows permission prompt.
#[cfg(target_os = "windows")]
const ELEVATION_DECLINED: &str = "elevation_declined";

/// Runs the Windows prompt off the async threads, since it blocks until the user answers.
#[cfg(target_os = "windows")]
async fn relaunch_elevated_for_the_user() -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(|| elevation::relaunch_elevated(false))
        .await
        .map_err(|error| error.to_string())?
}

/// This process can't use WinDivert, so the choice takes effect in a new, elevated one.
/// It's saved first so the new process reads it, and undone if the user says no, which
/// leaves this process and its capture exactly as they were.
#[cfg(target_os = "windows")]
async fn reopen_with_windivert(
    state: &tauri::State<'_, AppState>,
    app: &tauri::AppHandle,
) -> Result<(), String> {
    let previous = {
        let mut cfg = state.config.lock().await;
        let previous = cfg.clone();
        cfg.capture_method = config::CaptureMethod::Windivert;
        if let Err(error) = config::save(&cfg) {
            *cfg = previous;
            return Err(error.to_string());
        }
        previous
    };

    if let Err(error) = relaunch_elevated_for_the_user().await {
        tracing::info!("não foi possível reabrir como administrador: {error}");
        let mut cfg = state.config.lock().await;
        *cfg = previous;
        if let Err(error) = config::save(&cfg) {
            return Err(error.to_string());
        }
        return Err(ELEVATION_DECLINED.into());
    }
    app.exit(0);
    Ok(())
}

/// For when the user said no at startup and changed their mind.
#[tauri::command]
async fn relaunch_as_admin(app: tauri::AppHandle) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        if let Err(error) = relaunch_elevated_for_the_user().await {
            tracing::info!("não foi possível reabrir como administrador: {error}");
            return Err(ELEVATION_DECLINED.into());
        }
        app.exit(0);
        Ok(())
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = app;
        Err("indisponível nesta plataforma".into())
    }
}

/// Whether this process goes on to open the app. See `elevation::startup_step`. If the
/// user says no to the prompt, the app still opens and explains, unless an instance is
/// already capturing: a second one would only fight it for the saved loot session.
#[cfg(target_os = "windows")]
fn settle_privileges(method: config::CaptureMethod, minimized: bool) -> bool {
    let elevated = elevation::is_elevated();
    if elevated {
        elevation::mark_elevated_instance();
    }
    let other_is_elevated = !elevated && elevation::elevated_instance_is_running();
    let step = elevation::startup_step(
        method == config::CaptureMethod::Windivert,
        elevated,
        other_is_elevated,
        minimized,
    );
    tracing::info!(
        ?method,
        elevated,
        other_is_elevated,
        minimized,
        ?step,
        "starting"
    );
    match step {
        elevation::StartupStep::Continue => true,
        elevation::StartupStep::Quit => false,
        elevation::StartupStep::Reopen => match elevation::relaunch_elevated(true) {
            Ok(()) => false,
            Err(error) => {
                tracing::info!("não foi possível reabrir como administrador: {error}");
                !other_is_elevated
            }
        },
    }
}

/// An entry that starts with Windows opens the app hidden, so finding it already running
/// is no reason to show it.
fn second_launch_shows_the_window(args: &[String]) -> bool {
    !args.iter().any(|arg| arg == "--minimized")
}

fn start_sniffer(sniffer: &Sniffer, method: config::CaptureMethod) {
    let generation = sniffer.prepare_start();
    let sniffer = sniffer.clone_shared();
    tauri::async_runtime::spawn(async move {
        sniffer.run_generation(generation, method).await;
    });
}

#[derive(serde::Serialize)]
struct NpcapStatus {
    installed: bool,
    version: Option<String>,
}

#[tauri::command]
fn get_npcap_status() -> NpcapStatus {
    #[cfg(target_os = "windows")]
    let version = npcap::version();
    #[cfg(not(target_os = "windows"))]
    let version: Result<Option<String>, String> = Err("Npcap is Windows only".to_string());

    match version {
        Ok(version) => NpcapStatus {
            installed: true,
            version,
        },
        Err(_) => NpcapStatus {
            installed: false,
            version: None,
        },
    }
}

/// The address is fixed here so the page can't open anything else through this command.
#[tauri::command]
fn open_npcap_download(app: tauri::AppHandle) -> Result<(), String> {
    app.opener()
        .open_url("https://npcap.com/#download", None::<&str>)
        .map_err(|error| error.to_string())
}

fn restore_capture_toggle(sniffer: &Sniffer, key: &str, previous: &config::CompanionConfig) {
    match key {
        "collect_damage_meter" => sniffer.capture_damage.store(
            previous.collect_damage_meter,
            std::sync::atomic::Ordering::Relaxed,
        ),
        "collect_auto_lootlog" => sniffer.capture_loot.store(
            previous.collect_auto_lootlog,
            std::sync::atomic::Ordering::Relaxed,
        ),
        _ => {}
    }
}

#[cfg(target_os = "windows")]
fn apply_autostart(
    _app: &tauri::AppHandle,
    enable: bool,
    method: config::CaptureMethod,
) -> Result<(), String> {
    autostart::apply(enable, method, autostart::Moment::Changed)
}

#[cfg(not(target_os = "windows"))]
fn apply_autostart(
    app: &tauri::AppHandle,
    enable: bool,
    _method: config::CaptureMethod,
) -> Result<(), String> {
    let autostart = app.autolaunch();
    if enable {
        autostart.enable().map_err(|error| error.to_string())
    } else {
        autostart.disable().map_err(|error| error.to_string())
    }
}

#[tauri::command]
async fn get_sniff_stats(state: tauri::State<'_, AppState>) -> Result<SniffStats, String> {
    let mut stats = state.sniffer.stats.lock().await.clone();
    let damage = state.sniffer.damage.lock().await;
    stats.damage_total = damage.values().map(|value| value.damage).sum::<f64>() as u64;
    Ok(stats)
}
#[tauri::command]
async fn get_sniffer_debug(state: tauri::State<'_, AppState>) -> Result<Vec<DebugLine>, String> {
    Ok(state.sniffer.debug.lock().await.clone())
}
#[tauri::command]
fn get_catalog_status() -> catalog::CatalogStatuses {
    catalog::statuses()
}
#[tauri::command]
async fn get_captured_loot(
    state: tauri::State<'_, AppState>,
) -> Result<Vec<lootlog::LootRow>, String> {
    Ok(state
        .sniffer
        .loot
        .lock()
        .await
        .iter()
        .map(|entry| {
            let (item_id, item_name, item_name_pt, item_name_es) =
                lootlog::resolve(entry.item_index);
            lootlog::LootRow {
                ts: Some(entry.ts.clone()),
                item_id,
                item_name,
                item_name_pt,
                item_name_es,
                quantity: entry.quantity as i64,
                looted_by: entry.looted_by.clone(),
                looted_by_guild: entry.looted_by_guild.clone(),
                looted_by_alliance: entry.looted_by_alliance.clone(),
                looted_from: entry.looted_from.clone(),
                looted_from_guild: entry.looted_from_guild.clone(),
                looted_from_alliance: entry.looted_from_alliance.clone(),
            }
        })
        .collect())
}
#[tauri::command]
async fn clear_captured_loot(state: tauri::State<'_, AppState>) -> Result<(), String> {
    state.sniffer.clear_captured_loot().await
}
static SPELL_TABLE: std::sync::OnceLock<Mutex<Vec<api::SpellName>>> = std::sync::OnceLock::new();

fn spell_table() -> &'static Mutex<Vec<api::SpellName>> {
    SPELL_TABLE.get_or_init(|| Mutex::new(Vec::new()))
}

fn spell_cache_path() -> std::path::PathBuf {
    dirs::config_dir()
        .unwrap_or_else(|| ".".into())
        .join("ziggs-companion")
        .join("spell_names_v1.json")
}

async fn store_spell_names(spells: Vec<api::SpellName>) {
    *spell_table().lock().await = spells;
}

async fn load_cached_spell_names() {
    match std::fs::read(spell_cache_path()) {
        Ok(bytes) => match serde_json::from_slice::<Vec<api::SpellName>>(&bytes) {
            Ok(spells) if !spells.is_empty() => {
                let count = spells.len();
                store_spell_names(spells).await;
                catalog::mark_spells_ready(catalog::CatalogSource::Cache, count);
                tracing::info!(count, "spell catalog loaded from cache");
            }
            Ok(_) => catalog::mark_spells_degraded("cache de habilidades vazio"),
            Err(error) => {
                catalog::mark_spells_degraded(format!("cache de habilidades inválido: {error}"))
            }
        },
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => catalog::mark_spells_degraded(format!(
            "não foi possível ler o cache de habilidades: {error}"
        )),
    }
}

async fn load_spell_names() {
    load_cached_spell_names().await;
    let api = api::ApiClient::new(config::API_BASE_URL);
    loop {
        match api.spell_names().await {
            Ok(spells) if !spells.is_empty() => {
                let count = spells.len();
                if let Ok(bytes) = serde_json::to_vec(&spells) {
                    if let Err(error) = persist::atomic_write(&spell_cache_path(), &bytes) {
                        tracing::warn!("spell catalog cache write failed: {error:#}");
                    }
                }
                store_spell_names(spells).await;
                catalog::mark_spells_ready(catalog::CatalogSource::Backend, count);
                tracing::info!(count, "spell catalog refreshed from backend");
                return;
            }
            Ok(_) => {
                catalog::mark_spells_degraded("backend retornou um catálogo de habilidades vazio");
                tracing::warn!("spell catalog empty on backend, retrying in 60s");
            }
            Err(error) => {
                catalog::mark_spells_degraded(format!("falha ao buscar habilidades: {error:#}"));
                tracing::warn!("spell catalog fetch failed, retrying in 60s: {error:#}");
            }
        }
        tokio::time::sleep(std::time::Duration::from_secs(60)).await;
    }
}

#[derive(serde::Serialize)]
struct SkillRow {
    id: i32,
    name: Option<String>,
    name_pt: Option<String>,
    name_es: Option<String>,
    unique_name: Option<String>,
    icon: Option<String>,
    hits: u64,
    total: i64,
    avg: i64,
    max_hit: i64,
    pct: f64,
    fam: Option<String>,
}

#[derive(serde::Serialize)]
struct DamageRow {
    name: String,
    weapon: Option<String>,
    damage: i64,
    dps: i64,
    skills: Vec<SkillRow>,
    timeline: Vec<i64>,
}

#[tauri::command]
async fn get_damage_meter(
    state: tauri::State<'_, AppState>,
    vs_players: bool,
) -> Result<Vec<DamageRow>, String> {
    let window = crate::photon_parser::TIMELINE_SECS;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0);
    let offset = state.config.lock().await.spell_index_offset;

    let merged: Vec<(String, crate::photon_parser::DamageAcc)> = {
        let names = state.sniffer.entities.lock().await;
        let damage = if vs_players {
            state.sniffer.damage_vs_players.lock().await
        } else {
            state.sniffer.damage.lock().await
        };
        let mut by_name: std::collections::HashMap<String, crate::photon_parser::DamageAcc> =
            std::collections::HashMap::new();

        for (id, accumulator) in damage.iter() {
            if let Some(name) = names.get(id) {
                by_name.entry(name.clone()).or_default().merge(accumulator);
            }
        }

        by_name.into_iter().collect()
    };

    let spells = spell_table().lock().await;
    let mut rows: Vec<DamageRow> = merged
        .iter()
        .map(|(name, accumulator)| {
            let total_damage = accumulator.damage.max(1.0);
            let mut skills: Vec<SkillRow> = accumulator
                .spells
                .iter()
                .map(|(spell_id, spell)| {
                    let entry = spell_id
                        .checked_add(offset)
                        .filter(|index| *index >= 0)
                        .and_then(|index| spells.get(index as usize));

                    SkillRow {
                        id: *spell_id,
                        name: entry.map(|entry| entry.name.clone()),
                        name_pt: entry.and_then(|entry| entry.pt.clone()),
                        name_es: entry.and_then(|entry| entry.es.clone()),
                        unique_name: entry.map(|entry| entry.id.clone()),
                        icon: entry
                            .map(|entry| entry.icon.clone().unwrap_or_else(|| entry.id.clone())),
                        hits: spell.hits,
                        total: spell.total as i64,
                        avg: if spell.hits > 0 {
                            (spell.total / spell.hits as f64) as i64
                        } else {
                            0
                        },
                        max_hit: spell.max_hit as i64,
                        pct: (spell.total / total_damage) * 100.0,
                        fam: entry.and_then(|entry| entry.fam.clone()),
                    }
                })
                .collect();
            skills.sort_by(|left, right| right.total.cmp(&left.total));

            let weapon = skills.iter().find_map(|skill| skill.fam.clone());
            let mut timeline = vec![0; window as usize];
            let oldest = now.saturating_sub(window - 1);
            for (second, damage) in &accumulator.timeline {
                if *second >= oldest && *second <= now {
                    timeline[(*second - oldest) as usize] = *damage as i64;
                }
            }

            DamageRow {
                name: name.clone(),
                weapon,
                damage: accumulator.damage as i64,
                dps: accumulator.dps() as i64,
                skills,
                timeline,
            }
        })
        .collect();
    rows.sort_by(|left, right| right.damage.cmp(&left.damage));

    Ok(rows)
}
#[tauri::command]
async fn clear_damage_meter(state: tauri::State<'_, AppState>) -> Result<(), String> {
    state.sniffer.damage.lock().await.clear();
    state.sniffer.damage_vs_players.lock().await.clear();
    Ok(())
}
#[tauri::command]
async fn save_lootlog_csv(
    state: tauri::State<'_, AppState>,
    app: tauri::AppHandle,
) -> Result<String, String> {
    let csv = lootlog::build_csv_from_loot(&state.sniffer.loot.lock().await);
    let path = lootlog::save_csv(&csv).map_err(|e| e.to_string())?;
    state.lootlog.lock().await.last_saved_path = Some(path.clone());
    let _ = app.opener().reveal_item_in_dir(&path);
    Ok(path)
}
#[tauri::command]
async fn report_frontend_crash(message: String, stack: String) -> Result<(), String> {
    crash_report::save_frontend(message, stack).map_err(|e| e.to_string())?;
    crash_report::send_pending_once()
        .await
        .map_err(|e| e.to_string())?;
    Ok(())
}
fn present_window(window: &tauri::WebviewWindow) {
    let _ = window.show();
    let _ = window.unminimize();
    let _ = window.set_focus();
}
fn build_tray(app: &tauri::AppHandle) -> tauri::Result<()> {
    let quit = MenuItem::with_id(app, "quit", "Sair", true, None::<&str>)?;
    let show = MenuItem::with_id(app, "show", "Abrir", true, None::<&str>)?;
    TrayIconBuilder::with_id("main-tray")
        .icon(app.default_window_icon().unwrap().clone())
        .menu(&Menu::with_items(app, &[&show, &quit])?)
        .tooltip("Ziggs Companion")
        .on_menu_event(|app, event| match event.id.as_ref() {
            "quit" => app.exit(0),
            "show" => {
                if let Some(window) = app.get_webview_window("main") {
                    present_window(&window);
                }
            }
            _ => {}
        })
        .build(app)?;
    Ok(())
}
#[cfg(target_os = "windows")]
async fn auto_update(app: &tauri::AppHandle) -> Result<(), anyhow::Error> {
    use tauri_plugin_updater::UpdaterExt;
    if app.updater()?.check().await?.is_some() {
        let _ = app.emit("update-status", "available");
    }
    Ok(())
}
#[cfg(target_os = "windows")]
#[tauri::command]
async fn check_and_apply_update(app: tauri::AppHandle) -> Result<(), String> {
    use tauri_plugin_updater::UpdaterExt;
    let Some(update) = app
        .updater()
        .map_err(|e| e.to_string())?
        .check()
        .await
        .map_err(|e| e.to_string())?
    else {
        return Ok(());
    };
    let _ = app.emit("update-status", "downloading");
    update
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|e| e.to_string())?;
    app.restart();
}
#[cfg(not(target_os = "windows"))]
#[tauri::command]
async fn check_and_apply_update() -> Result<(), String> {
    Err("Atualização automática indisponível nesta plataforma".into())
}
pub fn run() {
    #[cfg(target_os = "windows")]
    elevation::wait_for_replaced_instance();
    crash_report::install_hook();
    crash_report::init_logging();
    let mut cfg = config::load();
    let start_minimized = std::env::args().any(|arg| arg == "--minimized");
    #[cfg(target_os = "windows")]
    {
        // Opened by an entry the user has turned off, which only an elevated process could
        // remove. Finish the job and stay closed, as they asked.
        if start_minimized && !cfg.autostart {
            if let Err(error) =
                autostart::apply(false, cfg.capture_method, autostart::Moment::Launch)
            {
                tracing::warn!("não foi possível remover a inicialização automática: {error}");
            }
            return;
        }
        if !settle_privileges(cfg.capture_method, start_minimized) {
            return;
        }
        // Before the single-instance check, which may end this process: that is how a task
        // left behind would otherwise survive every logon.
        if let Err(error) =
            autostart::apply(cfg.autostart, cfg.capture_method, autostart::Moment::Launch)
        {
            tracing::warn!("não foi possível configurar a inicialização automática: {error}");
        }
    }
    cfg.install_id = config::install_id();
    let sniffer = Sniffer::new();
    let state = AppState {
        config: Arc::new(Mutex::new(cfg.clone())),
        sniffer,
        lootlog: Arc::new(Mutex::new(lootlog::LootlogStatus::default())),
    };
    tauri::Builder::default()
        // First on purpose: a second launch has to end before anything else starts.
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            if !second_launch_shows_the_window(&args) {
                return;
            }
            if let Some(window) = app.get_webview_window("main") {
                present_window(&window);
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            None,
        ))
        .manage(state)
        .setup(move |app| {
            crash_report::set_version(app.package_info().version.to_string());
            tauri::async_runtime::spawn(async {
                if let Err(error) = crash_report::send_pending_once().await {
                    tracing::debug!(
                        "não foi possível reenviar o relatório de falha pendente: {error:#}"
                    );
                }
            });
            #[cfg(target_os = "windows")]
            {
                let _ = app
                    .handle()
                    .plugin(tauri_plugin_updater::Builder::new().build());
                let handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    let _ = auto_update(&handle).await;
                });
            }
            let state: tauri::State<AppState> = app.state();
            state.sniffer.capture_damage.store(
                cfg.collect_damage_meter,
                std::sync::atomic::Ordering::Relaxed,
            );
            state.sniffer.capture_loot.store(
                cfg.collect_auto_lootlog,
                std::sync::atomic::Ordering::Relaxed,
            );
            start_sniffer(&state.sniffer, cfg.capture_method);
            tauri::async_runtime::spawn(load_spell_names());
            tauri::async_runtime::spawn(lootlog::load_item_names());
            build_tray(app.handle())?;
            if !start_minimized {
                if let Some(window) = app.get_webview_window("main") {
                    present_window(&window);
                }
            }
            // On Windows this already happened, before the window: see the start of `run`.
            #[cfg(not(target_os = "windows"))]
            if cfg.autostart {
                if let Err(error) = apply_autostart(app.handle(), cfg.autostart, cfg.capture_method)
                {
                    tracing::warn!(
                        "não foi possível configurar a inicialização automática: {error}"
                    );
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window
                    .app_handle()
                    .state::<AppState>()
                    .config
                    .blocking_lock()
                    .minimize_to_tray
                {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            get_config,
            set_config,
            get_sniff_stats,
            get_sniffer_debug,
            get_catalog_status,
            get_captured_loot,
            clear_captured_loot,
            get_damage_meter,
            clear_damage_meter,
            save_lootlog_csv,
            check_and_apply_update,
            report_frontend_crash,
            get_npcap_status,
            open_npcap_download,
            relaunch_as_admin
        ])
        .run(tauri::generate_context!())
        .expect("falha ao iniciar o companion");
}
