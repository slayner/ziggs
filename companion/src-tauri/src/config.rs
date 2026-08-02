// Configuração local persistida em arquivo JSON.
// Caminho: <config_dir>/ziggs-companion/config.json
// (Linux: ~/.config/ziggs-companion, macOS: ~/Library/Application Support/ziggs-companion, Windows: %APPDATA%\ziggs-companion)

use std::path::PathBuf;
use std::sync::OnceLock;
use serde::{Deserialize, Serialize};

const CONFIG_FILENAME: &str = "config.json";

fn default_true() -> bool { true }

/// URL base do backend Ziggs — hardcoded no binário (não editável pela UI).
/// Em dev: http://localhost:8000. Em prod: URL HTTPS pública.
/// Mudou? rebuilda o companion.
#[cfg(debug_assertions)]
pub const API_BASE_URL: &str = "http://localhost:8000";
#[cfg(not(debug_assertions))]
pub const API_BASE_URL: &str = "http://localhost:8000";

fn default_api_base() -> String { API_BASE_URL.to_string() }

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CompanionConfig {
    /// Cópia do `API_BASE_URL` só pra UI montar URL de imagem (o resto vai por
    /// command Tauri e não precisa disso).
    ///
    /// `skip_deserializing`: é SEMPRE o valor do binário, nunca o que está no
    /// JSON. Sem isso, um config salvo por uma build antiga apontaria o
    /// companion novo pro backend velho — e ainda por cima em silêncio.
    #[serde(skip_deserializing, default = "default_api_base")]
    pub api_base_url: String,
    /// Toggles de coleta — damage_meter e auto_lootlog ON por padrão
    /// (decisão de 19/07/2026: o companion é um monitor, monitor nasce
    /// monitorando; quem desligar tem a escolha respeitada no JSON salvo).
    /// battles e prices são SEMPRE true (a própria razão do companion existir).
    #[serde(default = "default_true")]
    pub collect_damage_meter: bool,
    #[serde(default = "default_true")]
    pub collect_auto_lootlog: bool,
    /// Iniciar com o sistema.
    pub autostart: bool,
    /// Minimizar para tray ao fechar a janela.
    pub minimize_to_tray: bool,
    /// WireGuard tunnel — rota tipo ExitLag. Configurada por mim direto no
    /// código quando a VPS estiver pronta; UI hoje é só placeholder.
    #[serde(default)]
    pub tunnel_enabled: bool,
    #[serde(default)]
    pub tunnel_endpoint: String,
    #[serde(default)]
    pub tunnel_server_pubkey: String,
    #[serde(default)]
    pub tunnel_client_privkey: String,
    /// Pausar transferência de dados em zona PvP — só envia ao backend em zona azul.
    #[serde(default = "default_true")]
    pub pvp_pause_transfer: bool,
    /// Encaminhar ordens de mercado capturadas ao Albion Online Data Project
    /// (devolver dado à comunidade, já que consumimos deles). Ligado por padrão.
    #[serde(default = "default_true")]
    pub feed_aodp: bool,
    /// Token de portador (bearer) Discord — preenchido após login OAuth opcional.
    /// None = não logado. Usado pra /companion/lootlog/* e /companion/auth/*.
    pub discord_token: Option<String>,
    /// Discord user id (string pra preservar precisão do snowflake 64-bit).
    pub discord_user_id: Option<String>,
    /// Discord username (mostrado na UI).
    pub discord_username: Option<String>,
    /// Enviar lootlog automaticamente quando um evento do usuário entrar em
    /// REVISÃO. Não precisa escolher guilda: o backend descobre pelos eventos
    /// em que o usuário logado está inscrito (ver /companion/lootlog/active-events).
    pub auto_lootlog_submit: bool,
    /// Identidade estável DESTA instalação (um PC = um id), gerada no primeiro
    /// uso e persistida. Vai no header X-Ziggs-Install de toda request pro
    /// backend saber que fechar/reabrir o app (ou rodar 2 cópias durante um
    /// rebuild) continua sendo o MESMO companion — sem isso cada processo
    /// contava como um PC novo e pegava seu próprio range de scan.
    /// Vazio = ainda não gerado; `install_id()` cuida disso.
    #[serde(default)]
    pub install_id: String,
    /// Deslocamento aplicado ao índice de feitiço antes de olhar a tabela de
    /// nomes. Existe porque o mapeamento índice→nome é uma HIPÓTESE (posição no
    /// spells.xml) que ninguém validou contra tráfego real: se na calibração o
    /// nome sair consistentemente N posições fora, ajusta aqui em vez de
    /// rebuildar. 0 = sem ajuste.
    #[serde(default)]
    pub spell_index_offset: i32,
}

impl Default for CompanionConfig {
    fn default() -> Self {
        Self {
            api_base_url: default_api_base(),
            collect_damage_meter: true, // on por padrão — ver comentário do campo
            collect_auto_lootlog: true, // on por padrão — ver comentário do campo
            autostart: true,              // inicia com o sistema por padrão (Task Scheduler)
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
            install_id: String::new(), // gerado sob demanda por install_id()
            spell_index_offset: 0,
        }
    }
}

/// Id desta instalação — lê do config, gera+persiste na primeira chamada.
/// Cacheado em memória: todos os ApiClient do processo usam o mesmo valor.
pub fn install_id() -> String {
    static ID: OnceLock<String> = OnceLock::new();
    ID.get_or_init(|| {
        let mut cfg = load();
        if cfg.install_id.is_empty() {
            // 128 bits em hex — colisão entre instalações é irrelevante e não
            // carrega nada do hardware (nada de fingerprint do PC).
            let bytes: [u8; 16] = rand::random();
            cfg.install_id = bytes.iter().map(|b| format!("{:02x}", b)).collect();
            let _ = save(&cfg);
        }
        cfg.install_id
    })
    .clone()
}

fn config_path() -> PathBuf {
    let dir = dirs::config_dir()
        .unwrap_or_else(|| PathBuf::from("."));
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

fn atomic_write(path: &std::path::Path, bytes: &[u8]) -> anyhow::Result<()> {
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, bytes)?;

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::ffi::OsStrExt;
        use windows_sys::Win32::Storage::FileSystem::{MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH};
        let from: Vec<u16> = tmp.as_os_str().encode_wide().chain(std::iter::once(0)).collect();
        let to: Vec<u16> = path.as_os_str().encode_wide().chain(std::iter::once(0)).collect();
        if unsafe { MoveFileExW(from.as_ptr(), to.as_ptr(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) } == 0 {
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

    /// `api_base_url` existe só pra UI montar URL de imagem, e tem que vir
    /// SEMPRE do binário. Se um dia alguém tirar o `skip_deserializing`, um
    /// config.json salvo por build antiga passaria a apontar o companion novo
    /// pro backend velho — e em silêncio, que é o pior tipo de quebra.
    #[test]
    fn api_base_url_ignora_o_que_esta_no_json() {
        let json = r#"{
            "api_base_url": "http://backend-que-nao-existe-mais:9999",
            "collect_damage_meter": true,
            "collect_auto_lootlog": false,
            "autostart": true,
            "minimize_to_tray": true,
            "discord_token": null,
            "discord_user_id": null,
            "discord_username": null,
            "auto_lootlog_submit": false
        }"#;
        let cfg: CompanionConfig = serde_json::from_str(json).expect("config válido");
        assert_eq!(cfg.api_base_url, API_BASE_URL);
        assert!(cfg.collect_damage_meter, "os outros campos continuam vindo do JSON");
    }

    /// E precisa ir pra UI no get_config, senão o ícone fica sem base.
    #[test]
    fn api_base_url_e_serializado_pra_ui() {
        let s = serde_json::to_string(&CompanionConfig::default()).unwrap();
        assert!(s.contains(API_BASE_URL), "UI lê config.api_base_url");
    }

    #[test]
    fn escrita_atomica_substitui_arquivo() {
        let path = std::env::temp_dir().join(format!("ziggs-config-test-{}.json", std::process::id()));
        std::fs::write(&path, b"old").unwrap();
        atomic_write(&path, b"new").unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"new");
        assert!(!path.with_extension("json.tmp").exists());
        let _ = std::fs::remove_file(path);
    }
}
