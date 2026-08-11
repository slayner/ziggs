// Local persisted configuration as JSON.
// Path: <config_dir>/ziggs-companion/config.json
// (Linux: ~/.config/ziggs-companion, macOS: ~/Library/Application Support/ziggs-companion, Windows: %APPDATA%\ziggs-companion)

use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::OnceLock;

const CONFIG_FILENAME: &str = "config.json";

fn default_true() -> bool {
    true
}

/// Ziggs backend base URL — hardcoded in the binary, not editable from the UI.
/// Dev: http://localhost:8000. Prod: public HTTPS URL.
/// Rebuild the companion when this changes.
#[cfg(debug_assertions)]
pub const API_BASE_URL: &str = "http://localhost:8000";
#[cfg(not(debug_assertions))]
pub const API_BASE_URL: &str = "https://ziggs.xyz";

fn default_api_base() -> String {
    API_BASE_URL.to_string()
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CompanionConfig {
    /// Copy of `API_BASE_URL` so the UI can build image URLs.
    ///
    /// `skip_deserializing`: always the binary's value, never the JSON value.
    /// Prevents an old config from silently pointing a new companion build at the wrong backend.
    #[serde(skip_deserializing, default = "default_api_base")]
    pub api_base_url: String,
    /// Data collection toggles — damage meter and auto lootlog default ON.
    /// Saved false values in JSON are respected.
    #[serde(default = "default_true")]
    pub collect_damage_meter: bool,
    #[serde(default = "default_true")]
    pub collect_auto_lootlog: bool,
    /// Launch with the system.
    pub autostart: bool,
    /// Minimize to tray when the window closes.
    pub minimize_to_tray: bool,
    /// WireGuard tunnel — ExitLag-style routing.
    #[serde(default)]
    pub tunnel_enabled: bool,
    #[serde(default)]
    pub tunnel_endpoint: String,
    #[serde(default)]
    pub tunnel_server_pubkey: String,
    #[serde(default)]
    pub tunnel_client_privkey: String,
    /// Pause outbound data transfers while in a PvP zone — only send while in blue zones.
    #[serde(default = "default_true")]
    pub pvp_pause_transfer: bool,
    /// Forward captured market orders to the Albion Online Data Project.
    /// Enabled by default.
    #[serde(default = "default_true")]
    pub feed_aodp: bool,
    /// Discord bearer token — set after optional OAuth login.
    /// None = not logged in. Used for /companion/lootlog/* and /companion/auth/*.
    pub discord_token: Option<String>,
    /// Discord user id as string to preserve 64-bit snowflake precision.
    pub discord_user_id: Option<String>,
    /// Discord username shown in the UI.
    pub discord_username: Option<String>,
    /// Auto-submit lootlog when a user event enters REVIEW.
    /// Guild is resolved server-side from the user's event signups.
    pub auto_lootlog_submit: bool,
    /// Stable identity of THIS installation (one PC = one id). Generated on first
    /// use and persisted. Sent in the X-Ziggs-Install header so the backend
    /// treats app restarts and concurrent rebuild processes as the same companion.
    #[serde(default)]
    pub install_id: String,
    /// Offset applied to the spell index before looking up names. Used to correct
    /// index→name mapping without rebuilding the companion. 0 = no offset.
    #[serde(default)]
    pub spell_index_offset: i32,
    /// Albion region for tunnel server selection. "americas" | "asia" | "europe".
    /// Auto-detected from AODP server when the game is open; defaults to
    /// "americas" so the tunnel works without the game running.
    #[serde(default = "default_region")]
    pub region: String,
    /// Per-region VPS assignment. Maps Albion region -> VPS region.
    /// NOT persisted — cleared on every app launch so the user always starts
    /// with all servers on "Direct". The matrix is a runtime control, not
    /// a saved preference.
    #[serde(skip)]
    pub tunnel_routing: std::collections::HashMap<String, String>,
}

fn default_region() -> String {
    "americas".into()
}

impl Default for CompanionConfig {
    fn default() -> Self {
        Self {
            api_base_url: default_api_base(),
            collect_damage_meter: true, // default ON; see field comment
            collect_auto_lootlog: true, // default ON; see field comment
            autostart: true,            // default launch with system
            minimize_to_tray: true,
            tunnel_enabled: false,
            tunnel_endpoint: String::new(),
            tunnel_server_pubkey: String::new(),
            tunnel_client_privkey: String::new(),
            pvp_pause_transfer: true,
            feed_aodp: true,
            discord_token: None,
            discord_user_id: None,
            discord_username: None,
            auto_lootlog_submit: false,
            install_id: String::new(), // generated on demand by install_id()
            spell_index_offset: 0,
            region: default_region(),
            tunnel_routing: std::collections::HashMap::new(),
        }
    }
}

/// Id of this installation — read from config, generated+persisted on first call.
/// Cached in memory so all ApiClient instances in the process share the same id.
pub fn install_id() -> String {
    static ID: OnceLock<String> = OnceLock::new();
    ID.get_or_init(|| {
        let mut cfg = load();
        if cfg.install_id.is_empty() {
            // 128-bit random hex id. Collisions across installs are irrelevant; no hardware fingerprint.
            let bytes: [u8; 16] = rand::random();
            cfg.install_id = bytes.iter().map(|b| format!("{:02x}", b)).collect();
            let _ = save(&cfg);
        }
        cfg.install_id
    })
    .clone()
}

fn config_path() -> PathBuf {
    let dir = dirs::config_dir().unwrap_or_else(|| PathBuf::from("."));
    let dir = dir.join("ziggs-companion");
    let _ = std::fs::create_dir_all(&dir);
    dir.join(CONFIG_FILENAME)
}

pub fn load() -> CompanionConfig {
    match std::fs::read(config_path()) {
        Ok(bytes) => serde_json::from_slice(&bytes).unwrap_or_default(),
        Err(_) => CompanionConfig::default(),
    }
}

pub fn save(cfg: &CompanionConfig) -> anyhow::Result<()> {
    let bytes = serde_json::to_vec_pretty(cfg)?;
    atomic_write(&config_path(), &bytes)?;
    Ok(())
}

pub(crate) fn atomic_write(path: &std::path::Path, bytes: &[u8]) -> anyhow::Result<()> {
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, bytes)?;

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::ffi::OsStrExt;
        use windows_sys::Win32::Storage::FileSystem::{
            MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
        };
        let from: Vec<u16> = tmp
            .as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();
        let to: Vec<u16> = path
            .as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();
        if unsafe {
            MoveFileExW(
                from.as_ptr(),
                to.as_ptr(),
                MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
            )
        } == 0
        {
            return Err(std::io::Error::last_os_error().into());
        }
    }
    #[cfg(not(target_os = "windows"))]
    std::fs::rename(tmp, path)?;

    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;

    /// `api_base_url` is only for the UI to build image URLs and must always
    /// come from the binary. Removing `skip_deserializing` would let an old
    /// config.json silently point a new companion build at the wrong backend.
    #[test]
    fn api_base_url_ignores_json_value() {
        let json = r#"{
            "api_base_url": "http://backend-that-no-longer-exists:9999",
            "collect_damage_meter": true,
            "collect_auto_lootlog": false,
            "autostart": true,
            "minimize_to_tray": true,
            "discord_token": null,
            "discord_user_id": null,
            "discord_username": null,
            "auto_lootlog_submit": false
        }"#;
        let cfg: CompanionConfig = serde_json::from_str(json).expect("valid config");
        assert_eq!(cfg.api_base_url, API_BASE_URL);
        assert!(
            cfg.collect_damage_meter,
            "other fields still come from JSON"
        );
    }

    /// It must still be serialized for get_config, otherwise the UI has no base URL.
    #[test]
    fn api_base_url_is_serialized_for_ui() {
        let s = serde_json::to_string(&CompanionConfig::default()).unwrap();
        assert!(s.contains(API_BASE_URL), "UI reads config.api_base_url");
    }

    #[test]
    fn atomic_write_replaces_file() {
        let path =
            std::env::temp_dir().join(format!("ziggs-config-test-{}.json", std::process::id()));
        std::fs::write(&path, b"old").unwrap();
        atomic_write(&path, b"new").unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"new");
        assert!(!path.with_extension("json.tmp").exists());
        let _ = std::fs::remove_file(path);
    }
}
