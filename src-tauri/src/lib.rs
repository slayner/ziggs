pub mod albion_detect;
pub mod api;
pub mod config;
pub mod crash_report;
pub mod lootlog;
pub mod maps;
pub mod persist;
pub mod photon_parser;
pub mod sniffer;
#[cfg(target_os = "windows")]
pub mod windivert;
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
    sniffer_running: Arc<Mutex<bool>>,
    lootlog: Arc<Mutex<lootlog::LootlogStatus>>,
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::Ordering;

    use super::restore_capture_toggle;
    use crate::{config::CompanionConfig, sniffer::Sniffer};

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
        _ => return Err("configuração inválida".into()),
    };

    if changed_autostart {
        if let Err(error) = apply_autostart(&app, cfg.autostart) {
            *cfg = previous;
            return Err(error);
        }
    }

    if let Err(error) = config::save(&cfg) {
        if changed_autostart {
            let _ = apply_autostart(&app, previous.autostart);
        }
        restore_capture_toggle(&state.sniffer, &key, &previous);
        *cfg = previous;
        return Err(error.to_string());
    }

    Ok(())
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
fn apply_autostart(_app: &tauri::AppHandle, enable: bool) -> Result<(), String> {
    set_autostart(enable)
}

#[cfg(not(target_os = "windows"))]
fn apply_autostart(app: &tauri::AppHandle, enable: bool) -> Result<(), String> {
    let autostart = app.autolaunch();
    if enable {
        autostart.enable().map_err(|error| error.to_string())
    } else {
        autostart.disable().map_err(|error| error.to_string())
    }
}

#[cfg(target_os = "windows")]
fn set_autostart(enable: bool) -> Result<(), String> {
    let executable = std::env::current_exe()
        .map_err(|error| format!("não foi possível localizar o executável: {error}"))?;
    const TASK_NAME: &str = "ZiggsCompanion";

    let output = if enable {
        // WinDivert needs administrator rights. The scheduled task must preserve
        // that level and keep the launch unobtrusive after the user signs in.
        let task_command = format!("\"{}\" --minimized", executable.display());
        crate::winutil::no_window(std::process::Command::new("schtasks"))
            .args([
                "/Create",
                "/TN",
                TASK_NAME,
                "/SC",
                "ONLOGON",
                "/TR",
                &task_command,
                "/RL",
                "HIGHEST",
                "/F",
            ])
            .output()
    } else {
        crate::winutil::no_window(std::process::Command::new("schtasks"))
            .args(["/Delete", "/TN", TASK_NAME, "/F"])
            .output()
    }
    .map_err(|error| {
        let action = if enable { "configurar" } else { "remover" };
        format!("não foi possível {action} a inicialização automática: {error}")
    })?;

    if output.status.success() {
        return Ok(());
    }

    let stderr = String::from_utf8_lossy(&output.stderr);
    let stdout = String::from_utf8_lossy(&output.stdout);
    if !enable
        && [stderr.as_ref(), stdout.as_ref()].iter().any(|detail| {
            detail.contains("cannot find") || detail.contains("não foi possível localizar")
        })
    {
        return Ok(());
    }

    let detail = if stderr.trim().is_empty() {
        stdout.trim()
    } else {
        stderr.trim()
    };
    let action = if enable { "criação" } else { "remoção" };
    Err(format!(
        "o Windows não aceitou a {action} da tarefa de inicialização automática: {detail}"
    ))
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
                server_region: entry.server_region.clone(),
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

async fn load_spell_names() {
    let api = api::ApiClient::new(config::API_BASE_URL);
    if let Ok(spells) = api.spell_names().await {
        *spell_table().lock().await = spells;
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
    crash_report::install_hook();
    crash_report::init_logging();
    let mut cfg = config::load();
    cfg.install_id = config::install_id();
    let autostart = cfg.autostart;
    let sniffer = Sniffer::new();
    let state = AppState {
        config: Arc::new(Mutex::new(cfg.clone())),
        sniffer,
        sniffer_running: Arc::new(Mutex::new(false)),
        lootlog: Arc::new(Mutex::new(lootlog::LootlogStatus::default())),
    };
    let start_minimized = std::env::args().any(|arg| arg == "--minimized");
    tauri::Builder::default()
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
            let sniffer = state.sniffer.clone_shared();
            let running = Arc::clone(&state.sniffer_running);
            *running.blocking_lock() = true;
            let generation = sniffer.prepare_start();
            tauri::async_runtime::spawn(async move {
                sniffer.run_generation(generation).await;
                *running.lock().await = false;
            });
            tauri::async_runtime::spawn(load_spell_names());
            tauri::async_runtime::spawn(lootlog::load_item_names());
            build_tray(app.handle())?;
            if !start_minimized {
                if let Some(window) = app.get_webview_window("main") {
                    present_window(&window);
                }
            }
            if autostart {
                if let Err(error) = apply_autostart(app.handle(), true) {
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
            get_captured_loot,
            clear_captured_loot,
            get_damage_meter,
            clear_damage_meter,
            save_lootlog_csv,
            check_and_apply_update,
            report_frontend_crash
        ])
        .run(tauri::generate_context!())
        .expect("falha ao iniciar o companion");
}
