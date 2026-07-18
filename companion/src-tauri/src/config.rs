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
pub const API_BASE_URL: &str = "http://localhost:8000";

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CompanionConfig {
    /// Toggles de coleta — só damage_meter e auto_lootlog (off por padrão).
    /// battles e prices são SEMPRE true (a própria razão do companion existir).
    pub collect_damage_meter: bool,
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
    /// Guild ID (Discord snowflake) pra onde enviar lootlog auto.
    /// O companion não descobre isso sozinho — o user escolhe no dropdown.
    pub lootlog_guild_id: Option<String>,
    /// Enviar lootlog automaticamente pro evento ativo ao capturar.
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
            collect_damage_meter: false, // toggle na sidebar, off por padrão
            collect_auto_lootlog: false, // toggle na sidebar, off por padrão
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
            lootlog_guild_id: None,
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
    std::fs::write(config_path(), bytes)?;
    Ok(())
}