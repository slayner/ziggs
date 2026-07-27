// companion_lib: ponto de entrada da biblioteca companion.
// O binário main.rs chama companion_lib::run().

pub mod aodp;
pub mod api;
pub mod albion_detect;
pub mod albion_ips;
pub mod config;
pub mod dns;
pub mod lootlog;
pub mod maps;
pub mod photon_parser;
pub mod persist;
pub mod scanner;
pub mod sniffer;
pub mod transfer;
pub mod tunnel;
pub mod zone_detect;

pub use config::CompanionConfig;
pub use lootlog::LootlogStatus;
pub use scanner::{ScanStats, Scanner};
pub use sniffer::{SniffStats, Sniffer, DebugLine};
pub use transfer::TransferQueue;
pub use tunnel::{Tunnel, TunnelStatus};

use std::sync::Arc;
use tokio::sync::Mutex;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager, Emitter,
};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tauri_plugin_opener::OpenerExt;

/// Empurra uma linha pro buffer de debug do sniffer (mostrado no terminal da UI).
/// Cap em 500 linhas (mesmo teto usado nos outros pushers de debug).
fn push_debug(debug: &Arc<Mutex<Vec<DebugLine>>>, level: &str, msg: &str) {
    let line = DebugLine {
        ts: photon_parser::now_iso_utc(),
        level: level.into(),
        msg: msg.into(),
    };
    let mut d = debug.blocking_lock();
    d.push(line);
    if d.len() > 500 { let ex = d.len() - 500; d.drain(..ex); }
}

/// É hora segura pra queimar CPU/rede do usuário?
///
/// Seguro = jogo FECHADO **ou** jogador fora de zona PvP. O "jogo fechado" sai
/// de graça do `stats.online` do sniffer (sem pacote do Albion há 5s), então
/// não custa varredura de processo.
///
/// A trava existe porque a única coisa que o usuário NÃO perdoa é engasgo no
/// meio de um CTA. Fora de PvP, trabalho pesado é invisível; dentro, custa
/// morte. Qualquer tarefa cara nova deve passar por aqui.
async fn heavy_work_ok(
    sniffer: &Sniffer,
    zone: &Arc<Mutex<transfer::ZoneType>>,
    pvp_pause: &Arc<Mutex<bool>>,
) -> bool {
    if !sniffer.stats.lock().await.online {
        return true; // jogo fechado: pode usar a máquina à vontade
    }
    let paused = *pvp_pause.lock().await;
    !(paused && matches!(*zone.lock().await, transfer::ZoneType::PvP))
}

pub struct AppState {
    pub config: Arc<Mutex<CompanionConfig>>,
    pub scanner: Scanner,
    pub scanner_running: Arc<Mutex<bool>>,
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
async fn set_config(
    key: String,
    value: serde_json::Value,
    state: tauri::State<'_, AppState>,
    app: tauri::AppHandle,
) -> Result<(), String> {
    let mut cfg = state.config.lock().await;
    let changed_autostart = key == "autostart";
    match (key.as_str(), value.clone()) {
        ("autostart", serde_json::Value::Bool(b)) => cfg.autostart = b,
        ("minimize_to_tray", serde_json::Value::Bool(b)) => cfg.minimize_to_tray = b,
        ("collect_damage_meter", serde_json::Value::Bool(b)) => {
            cfg.collect_damage_meter = b;
            state.sniffer.capture_damage.store(b, std::sync::atomic::Ordering::Relaxed);
        }
        ("collect_auto_lootlog", serde_json::Value::Bool(b)) => {
            cfg.collect_auto_lootlog = b;
            state.sniffer.capture_loot.store(b, std::sync::atomic::Ordering::Relaxed);
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
            state.sniffer.feed_aodp.store(b, std::sync::atomic::Ordering::Relaxed);
        }
        ("auto_lootlog_submit", serde_json::Value::Bool(b)) => cfg.auto_lootlog_submit = b,
        // Calibração do damage meter — ver spell_index_offset no config.
        ("spell_index_offset", serde_json::Value::Number(n)) => {
            cfg.spell_index_offset = n.as_i64().unwrap_or(0) as i32;
        }
        _ => return Err(format!("campo desconhecido: {}", key)),
    }
    if let Err(e) = config::save(&cfg) {
        return Err(format!("falha ao salvar config: {e}"));
    }
    if changed_autostart {
        #[cfg(target_os = "windows")]
        {
            // Sem Npcap, o companion nunca faz nada útil sozinho no boot — não
            // registra a tarefa mesmo que o usuário tenha ligado o toggle
            // (ver `npcap_installed` e a nota grande em `set_autostart`).
            let want = cfg.autostart && sniffer::npcap_installed();
            let _ = set_autostart(want);
        }
        #[cfg(not(target_os = "windows"))]
        {
            let autostart = app.autolaunch();
            let _ = if cfg.autostart { autostart.enable() } else { autostart.disable() };
        }
    }
    Ok(())
}

#[tauri::command]
async fn get_scan_stats(state: tauri::State<'_, AppState>) -> Result<ScanStats, String> {
    let mut s = state.scanner.stats.lock().await.clone();
    s.throttle_ms = state.scanner.throttle_ms.load(std::sync::atomic::Ordering::Relaxed);
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
    // Battle scanning é sempre on (razão de ser do companion).
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
    let profile = dns::dns_profiles().into_iter().find(|p| p.name == profile_name)
        .ok_or_else(|| format!("perfil '{}' não encontrado", profile_name))?;
    dns::apply_dns(&profile).map_err(|e| format!("{:#}", e))
}

#[tauri::command]
async fn get_dns_targets(_state: tauri::State<'_, AppState>) -> Result<Vec<api::DnsTarget>, String> {
    let api = api::ApiClient::new(config::API_BASE_URL);
    match api.dns_targets().await {
        Ok(out) => Ok(out.servers),
        Err(e) => Err(format!("{:#}", e)),
    }
}

// ─── Zone commands (PvP pause / Blue flush) ──────────────────────────────────

#[tauri::command]
async fn set_zone(
    zone: String,
    state: tauri::State<'_, AppState>,
) -> Result<(), String> {
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
    }.to_string()
}

// ─── Sniffer commands (Fase 2 — packet capture) ──────────────────────────────

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
    // Somado na LEITURA (poll de 5s da UI), não no hot loop — ver o comentário
    // do campo em SniffStats. Locks em sequência, nunca aninhados.
    let damage_map = state.sniffer.damage.lock().await;
    s.damage_total = damage_map.values().map(|a| a.damage).sum::<f64>() as u64;
    // Badge da aba Damage: dano do PRÓPRIO jogador (s.player_name), não o total
    // da party. O DamageAcc é indexado por causer_id (entityId), e o nome do
    // próprio jogador pode ter vários entityIds numa sessão (re-enter no
    // alcance de visão) — resolve por nome via mapa entities, igual ao
    // get_damage_meter. Locks em sequência, nunca aninhados.
    if !s.player_name.is_empty() {
        let ents = state.sniffer.entities.lock().await;
        let my_ids: std::collections::HashSet<i64> = ents.iter()
            .filter(|(_, name)| *name == &s.player_name)
            .map(|(id, _)| *id)
            .collect();
        drop(ents);
        s.my_damage = damage_map.iter()
            .filter(|(id, _)| my_ids.contains(id))
            .map(|(_, a)| a.damage).sum::<f64>() as u64;
    }
    drop(damage_map);
    Ok(s)
}

#[tauri::command]
async fn get_sniffer_debug(state: tauri::State<'_, AppState>) -> Result<Vec<sniffer::DebugLine>, String> {
    Ok(state.sniffer.debug.lock().await.clone())
}

// ─── Albion process detection (status no header) ────────────────────────────

#[tauri::command]
async fn get_albion_pid() -> Option<u32> {
    // ponytail: async pra não bloquear o main thread do webview.
    // A varredura de processos roda numa thread separada.
    tokio::task::spawn_blocking(|| albion_detect::find_albion_pid())
        .await
        .unwrap_or(None)
}

// ─── Lootlog (capturado via packet sniffing) ──────────────────────────────────

/// Devolve os eventos de loot capturados durante a sessão atual.
#[tauri::command]
async fn get_captured_loot(state: tauri::State<'_, AppState>) -> Result<Vec<lootlog::LootRow>, String> {
    let buf = state.sniffer.loot.lock().await;
    Ok(buf.iter().map(|l| {
        // A UI mostra nome, não índice cru — e escolhe o idioma, então vão os
        // três. `item_id` continua indo pro caso de alguém conferir.
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
    }).collect())
}

/// Limpa o buffer de loot capturado.
#[tauri::command]
async fn clear_captured_loot(state: tauri::State<'_, AppState>) -> Result<(), String> {
    let mut buf = state.sniffer.loot.lock().await;
    buf.clear();

    let mut s = state.sniffer.stats.lock().await;
    s.loot_count = 0;
    Ok(())
}

// ─── Nomes de feitiço (damage meter) ─────────────────────────────────────────
// Tabela índice→nome baixada do backend uma vez e cacheada em disco. Vazia =
// UI cai no fallback "Habilidade {id}". Nunca bloqueia o meter: se o download
// falhar, o painel funciona igual, só sem nome.

static SPELL_TABLE: std::sync::OnceLock<Mutex<Vec<api::SpellName>>> = std::sync::OnceLock::new();

fn spell_table() -> &'static Mutex<Vec<api::SpellName>> {
    SPELL_TABLE.get_or_init(|| Mutex::new(Vec::new()))
}

fn spell_cache_path() -> std::path::PathBuf {
    dirs::config_dir()
        .unwrap_or_else(|| ".".into())
        .join("ziggs-companion")
        // Bump a cada mudança de CONTEÚDO da tabela: o cache antigo
        // desserializa sem erro (campos novos são Option), então sem trocar o
        // nome quem já tem cache nunca veria a melhoria.
        // v2 = pt/es. v3 = sub-feitiço herda nome e ícone do pai.
        // v4 = família da arma (`fam`), pra inferir a arma no ranking.
        // v5 = channelingspell entrou na contagem — TODOS os índices mudaram.
        .join("spell_names_v5.json")
}

/// Carrega do cache em disco e, se vazio/velho, baixa do backend em background.
///
/// Repete até conseguir: com autostart o companion sobe junto com o Windows,
/// antes da rede estar de pé, e uma tentativa única falhava calada — o damage
/// meter ficava a sessão inteira mostrando "Habilidade 2972" em vez do nome.
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
            // Backend sem o dump seedado: tentar de novo não resolve.
            Ok(_) => {
                tracing::info!("tabela de feitiços vazia no backend (não seedada)");
                return;
            }
            Err(e) => tracing::warn!("nomes de feitiço falharam, tentando de novo em 60s: {e:#}"),
        }
        tokio::time::sleep(std::time::Duration::from_secs(60)).await;
    }
}

// ─── Damage meter ─────────────────────────────────────────────────────────────

#[derive(serde::Serialize)]
struct SkillRow {
    id: i32,
    /// Nome resolvido pela tabela do dump. None = fora da tabela ou tabela
    /// ausente → a UI mostra "Habilidade {id}". Vem sempre acompanhado do `id`
    /// cru na interface, justamente pra dar pra conferir na calibração.
    name: Option<String>,
    /// Traduções. O idioma vive no localStorage do webview, não no config do
    /// Rust, então mandamos as três e a UI escolhe. None = cai no `name`.
    name_pt: Option<String>,
    name_es: Option<String>,
    /// uniquename do dump (ex. "AIR_RAID") — o que se confere contra o jogo.
    unique_name: Option<String>,
    /// Chave do ícone em render.albiononline.com/v1/spell/{id}.png. Difere do
    /// `unique_name` em sub-feitiço interno, que tem arte genérica de passiva
    /// em vez do ícone da habilidade — aí vem o id do pai.
    icon: Option<String>,
    /// Golpes (eventos de dano), não casts — ver SpellAcc.
    hits: u64,
    total: i64,
    avg: i64,
    max_hit: i64,
    /// Fatia do dano DESTE jogador que veio desta skill.
    pct: f64,
    /// Família da arma dona desta skill — base pra inferir a arma da linha.
    fam: Option<String>,
}

#[derive(serde::Serialize)]
struct DamageRow {
    name: String,
    /// Família da arma inferida (bow, dagger, …). None = só auto-attack, ou
    /// nenhuma skill usada veio de arma reconhecida.
    weapon: Option<String>,
    damage: i64,
    dps: i64,
    skills: Vec<SkillRow>,
    /// Dano por segundo dos últimos TIMELINE_SECS, do mais antigo pro mais
    /// recente. Índice = segundos atrás (0 = há 3 min, fim = agora), com os
    /// segundos sem dano preenchidos com 0 — a UI só desenha o array.
    timeline: Vec<i64>,
}

/// Dano por jogador na sessão, resolvido por nome e ordenado por dano desc.
/// Traz breakdown por skill e a timeline pra expandir a linha (estilo Details).
/// ponytail: IDs sem nome conhecido (mobs) são descartados — só jogadores.
#[tauri::command]
/// `vs_players` = só o dano cujo ALVO era jogador (descarta mob/estrutura).
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

    // ── Fase 1: sob os locks do sniffer, SÓ agrega ────────────────────────
    //
    // Junta por NOME: o mesmo jogador tem vários entity ids na sessão (id novo
    // a cada vez que ele reentra no teu alcance de visão) e `entities` nunca
    // esquece nenhum. Sem isso ele virava várias linhas com o dano picado — e,
    // como o React usa o nome como `key`, as chaves colidiam e a lista
    // duplicava a cada troca de fonte de dados.
    //
    // O escopo é apertado de propósito: o loop de pacotes precisa destes
    // mesmos locks a CADA golpe. Antes a formatação inteira (timeline densa de
    // 180 posições por jogador, clone de nome, sort de skills, lookup na
    // tabela de feitiços) acontecia aqui dentro, e a cada 2s a UI travava a
    // captura — justo em ZvZ, que é quando tem mais gente e mais pacote.
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
    }; // locks do sniffer soltos aqui — captura volta a correr livre

    // ── Fase 2: sem lock do sniffer, formata ──────────────────────────────
    let spells = spell_table().lock().await;
    let mut rows: Vec<DamageRow> = merged.iter().map(|(name, acc)| {
        {
            let total_dmg = acc.damage.max(1.0);
            let mut skills: Vec<SkillRow> = acc.spells.iter().map(|(sid, sp)| {
                // Índice negativo = auto attack/desconhecido; nunca indexa.
                let entry = sid.checked_add(offset)
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
                avg: if sp.hits > 0 { (sp.total / sp.hits as f64) as i64 } else { 0 },
                max_hit: sp.max_hit as i64,
                pct: (sp.total / total_dmg) * 100.0,
                fam: entry.and_then(|e| e.fam.clone()),
            }}).collect();
            skills.sort_by(|a, b| b.total.cmp(&a.total));

            // Arma do jogador = família da skill que MAIS deu dano. Não dá pra
            // ler o equipamento (o NewCharacter que lemos só traz id e nome),
            // então inferimos pelo que ele usou. Como `skills` já está ordenado
            // por dano, é o primeiro com família conhecida — assim um passivo
            // compartilhado ou um consumível no meio da lista não decide.
            let weapon = skills.iter().find_map(|s| s.fam.clone());

            // Buckets esparsos → array denso alinhado em `now`, pro gráfico
            // não precisar saber de timestamp nenhum.
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
    }).collect();
    rows.sort_by(|a, b| b.damage.cmp(&a.damage));
    Ok(rows)
}

#[tauri::command]
async fn clear_damage_meter(state: tauri::State<'_, AppState>) -> Result<(), String> {
    // Os dois juntos: zerar só um deixaria o toggle mostrando sessão velha.
    state.sniffer.damage.lock().await.clear();
    state.sniffer.damage_vs_players.lock().await.clear();
    Ok(())
}

/// Gera CSV no formato lootlogger a partir do loot capturado e salva em arquivo.
#[tauri::command]
async fn save_lootlog_csv(
    state: tauri::State<'_, AppState>,
    app: tauri::AppHandle,
) -> Result<String, String> {
    let buf = state.sniffer.loot.lock().await;
    let csv = lootlog::build_csv_from_loot(&buf);
    drop(buf);
    let path = lootlog::save_csv(&csv).map_err(|e| format!("falha ao salvar: {e}"))?;
    {
        let mut s = state.lootlog.lock().await;
        s.last_saved_path = Some(path.clone());
    }
    let _ = app.opener().reveal_item_in_dir(&path);
    Ok(path)
}

/// Abre a página de download do Npcap no browser do usuário.
///
/// A instalação é MANUAL de propósito, não por preguiça: o instalador free do
/// Npcap ABORTA o `/S` ("silent installation is only supported in Npcap OEM")
/// e a licença free também proíbe redistribuí-lo embutido no nosso installer
/// — as duas coisas são exatamente o que a licença OEM (npcap.com/oem) vende.
/// Já existiu um hook NSIS rodando `npcap-installer.exe /S` dos resources;
/// morreu por esses dois motivos. Se um dia comprarmos OEM, é só ressuscitar
/// o hook (git log de nsis-hooks.nsh).
///
/// O fluxo free é bom o bastante: o usuário instala com as opções padrão
/// (nem o modo WinPcap precisa — `ensure_npcap_dll_path` resolve o subdir) e
/// o loop de retry de 15s do `Sniffer::run` pega a instalação sozinho, sem
/// reiniciar o app.
#[tauri::command]
async fn open_npcap_download(app: tauri::AppHandle) -> Result<(), String> {
    app.opener().open_url("https://npcap.com/#download", None::<&str>)
        .map_err(|e| format!("falha ao abrir browser: {e}"))
}

/// Abre qualquer URL no browser padrão. Usado pelos links legais ("Termos",
/// "Privacidade") na tela Sobre do Config — o companion não renderiza HTML
/// legal próprio, só aponta pro site.
#[tauri::command]
async fn open_url(app: tauri::AppHandle, url: String) -> Result<(), String> {
    app.opener().open_url(&url, None::<&str>)
        .map_err(|e| format!("falha ao abrir browser: {e}"))
}

// ─── Discord login (opcional) + lootlog auto-submit ────────────────────────

#[tauri::command]
async fn companion_login(
    _state: tauri::State<'_, AppState>,
    app: tauri::AppHandle,
) -> Result<String, String> {
    let api = api::ApiClient::new(config::API_BASE_URL);
    let nonce: String = (0..16).map(|_| {
        let c = rand::Rng::gen_range(&mut rand::thread_rng(), 0..=255u8);
        format!("{:02x}", c)
    }).collect();
    let url = api.auth_start_url(&nonce);
    app.opener().open_url(&url, None::<&str>)
        .map_err(|e| format!("falha ao abrir browser: {e}"))?;
    Ok(nonce)
}

#[tauri::command]
async fn companion_poll_auth(
    nonce: String,
    state: tauri::State<'_, AppState>,
) -> Result<api::AuthPollResult, String> {
    let api = api::ApiClient::new(config::API_BASE_URL);
    match api.auth_poll(&nonce).await {
        Ok(result) => {
            let mut cfg = state.config.lock().await;
            cfg.discord_token = Some(result.token.clone());
            cfg.discord_user_id = Some(result.user_id.clone());
            cfg.discord_username = Some(result.username.clone());
            if let Err(e) = config::save(&cfg) {
                return Err(format!("falha ao salvar config: {e}"));
            }
            Ok(result)
        }
        Err(e) => Err(format!("{:#}", e)),
    }
}

#[tauri::command]
async fn companion_logout(state: tauri::State<'_, AppState>) -> Result<(), String> {
    let mut cfg = state.config.lock().await;
    cfg.discord_token = None;
    cfg.discord_user_id = None;
    cfg.discord_username = None;
    cfg.auto_lootlog_submit = false;
    config::save(&cfg).map_err(|e| format!("falha ao salvar config: {e}"))?;
    Ok(())
}

#[tauri::command]
async fn get_active_events(
    state: tauri::State<'_, AppState>,
) -> Result<Vec<api::ActiveEvent>, String> {
    let cfg = state.config.lock().await.clone();
    let api = api::ApiClient::new(config::API_BASE_URL).with_token(cfg.discord_token.clone());
    api.active_events().await.map_err(|e| format!("{:#}", e))
}

/// Envia o loot capturado (CSV) pra um evento.
#[tauri::command]
async fn submit_captured_loot(
    event_id: i64,
    state: tauri::State<'_, AppState>,
) -> Result<api::LootlogIngestOut, String> {
    let cfg = state.config.lock().await.clone();
    let buf = state.sniffer.loot.lock().await;
    let csv = lootlog::build_csv_from_loot(&buf);
    drop(buf);
    let api = api::ApiClient::new(config::API_BASE_URL).with_token(cfg.discord_token.clone());
    api.submit_lootlog(event_id, &csv)
        .await
        .map_err(|e| format!("{:#}", e))
}

/// Worker do auto-submit: manda o lootlog sozinho quando um evento em que o
/// usuário está inscrito entra em REVISÃO.
///
/// Por que revisão e não o fim da captura: é quando a guilda fecha o CTA e
/// começa a conferir os logs — mandar antes significaria enviar um log pela
/// metade, e mandar depois é tarde.
///
/// Reenviar o mesmo evento é inofensivo (o backend faz upsert por
/// guild+event+submitter), então o controle de "já mandei" é só em memória:
/// reiniciar o app no máximo reenvia uma vez, sobrescrevendo com o mesmo dado.
/// Mantém perfis quentes no site enquanto o jogo está aberto (`online`). Loop de
/// 5min. Só NOMEAÇÃO — o backend busca o dado na Albion (não confia no cliente):
/// - **Fase 2, todo ciclo:** players VISTOS em jogo (`entities`) → `/warm/seen`
///   (refresh-only no backend; cobre briga sub-limiar/roaming que o tracker pula).
/// - **Fase 1, a cada ~20min:** o PRÓPRIO personagem → `/warm` (faz bootstrap se
///   for desconhecido; ajuda gatherer/solo que nunca cai em ZvZ rastreada).
/// Região vem do servidor AODP detectado (west/east/europe → americas/asia/europe).
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
            continue; // jogo fechado: nada a aquecer
        }
        let region = match aodp_server.lock().await.as_ref().map(|s| s.region()) {
            Some("east") => "asia",
            Some("europe") => "europe",
            Some("west") => "americas",
            _ => continue, // região desconhecida: não chuta
        };
        let api = api::ApiClient::new(config::API_BASE_URL);

        // Fase 2: players vistos (dedup, sem o próprio, teto de 100 — o backend
        // corta em 100 de qualquer jeito). entities só cresce na sessão; re-enviar
        // é inofensivo (backend é refresh-only e idempotente por refresh_requested_at).
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
                tracing::debug!("warm/seen falhou: {e:#}");
            }
        }

        // Fase 1: próprio char a cada ~20min (1º ciclo e depois de 4 em 4).
        if !name.is_empty() && tick % 4 == 1 {
            match api.warm_profile(&name, region).await {
                Ok(out) => {
                    let cur = (name.clone(), region.to_string());
                    if last_logged.as_ref() != Some(&cur) {
                        tracing::info!("warm: nomeando {} ({}) — {}", name, region, out.status);
                        last_logged = Some(cur);
                    }
                }
                Err(e) => tracing::debug!("warm falhou: {e:#}"),
            }
        }
    }
}

/// Worker de fundo que estima o valor em prata dos loots capturados nesta
/// sessão — só badge ILUSTRATIVO da aba Lootlog. Agrega o loot buffer por
/// item_id (pra não mandar duplicados) e chama a rota
/// /companion/lootlog/silver-estimate do backend. Poll de 30s: o valor
/// não precisa ser em tempo real, e mantém a cota de HTTP baixa.
async fn loot_silver_worker(
    loot: Arc<Mutex<Vec<photon_parser::LootEvent>>>,
    stats: Arc<Mutex<sniffer::SniffStats>>,
) {
    let api = api::ApiClient::new(config::API_BASE_URL);
    loop {
        tokio::time::sleep(std::time::Duration::from_secs(30)).await;
        // Agrega por item_id (dedup) — o backend precifica por item, não por
        // linha, então mandar a mesma T4_BAG 50× só bateria o mesmo cache.
        let mut agg: std::collections::HashMap<String, i64> = std::collections::HashMap::new();
        {
            let buf = loot.lock().await;
            for ev in buf.iter() {
                let (item_id, _, _, _) = lootlog::resolve(ev.item_index);
                if item_id.is_empty() { continue; }
                *agg.entry(item_id).or_insert(0) += ev.quantity as i64;
            }
        }
        if agg.is_empty() {
            // Sem loot — zera o badge em vez de deixar um valor velho.
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
            Err(e) => tracing::debug!("silver-estimate falhou: {e:#}"),
        }
    }
}

async fn auto_lootlog_worker(
    config: Arc<Mutex<CompanionConfig>>,
    loot: Arc<Mutex<Vec<photon_parser::LootEvent>>>,
) {
    let mut submitted: std::collections::HashSet<i64> = std::collections::HashSet::new();
    loop {
        tokio::time::sleep(std::time::Duration::from_secs(60)).await;

        let cfg = config.lock().await.clone();
        if !cfg.auto_lootlog_submit || cfg.discord_token.is_none() {
            continue;
        }
        let api = api::ApiClient::new(config::API_BASE_URL).with_token(cfg.discord_token.clone());
        let events = match api.active_events().await {
            Ok(e) => e,
            Err(e) => {
                tracing::debug!("auto-lootlog: não deu pra listar eventos: {e:#}");
                continue;
            }
        };
        let Some(ev) = single_review_event(&events) else { continue };
        if !submitted.contains(&ev.event_id) {
            let csv = {
                let buf = loot.lock().await;
                lootlog::build_csv_from_loot(&buf)
            };
            // Sem loot capturado não há o que mandar — e mandar vazio
            // sobrescreveria uma submissão manual boa com nada.
            if csv.lines().count() <= 1 {
                continue;
            }
            match api.submit_lootlog(ev.event_id, &csv).await {
                Ok(out) => {
                    submitted.insert(ev.event_id);
                    tracing::info!(
                        "auto-lootlog: evento {} enviado ({} linhas)", ev.event_id, out.row_count
                    );
                }
                Err(e) => tracing::warn!("auto-lootlog: evento {} falhou: {e:#}", ev.event_id),
            }
        }
    }
}

fn single_review_event(events: &[api::ActiveEvent]) -> Option<&api::ActiveEvent> {
    let mut review = events.iter().filter(|e| e.state == "review");
    let event = review.next()?;
    review.next().is_none().then_some(event)
}

// ─── Autostart (Task Scheduler no Windows = admin sem UAC no boot) ────────────

/// Autostart via Task Scheduler (Windows) — roda como admin no boot sem prompt
/// UAC (RunLevel HighestAvailable, sempre — o app exige admin pros trackers).
/// Inicia como janela normal (sem `--minimized`): os anuncios precisam aparecer
/// no boot pra cobrir o custo da VPS do túnel. Fechar a janela ainda vai pra
/// bandeja se `minimize_to_tray` estiver on — só o arranque é sempre visível.
/// macOS/Linux: manter via tauri_plugin_autostart (LaunchAgent / .desktop).
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
            let out = std::process::Command::new("schtasks")
                .args([
                    "/Create", "/TN", "ZiggsCompanion",
                    "/XML", tmp.to_str().unwrap_or(""),
                    "/F",
                ])
                .output()
                .map_err(|e| format!("schtasks: {e}"))?;
            let _ = std::fs::remove_file(&tmp);
            if !out.status.success() {
                return Err(format!("schtasks: {}", String::from_utf8_lossy(&out.stderr)));
            }
        } else {
            let _ = std::process::Command::new("schtasks")
                .args(["/Delete", "/TN", "ZiggsCompanion", "/F"])
                .output();
        }
        Ok(())
    }
    #[cfg(not(target_os = "windows"))]
    {
        // ponytail: macOS/Linux usam tauri_plugin_autostart (LaunchAgent / .desktop) — sem admin.
        let _ = enable;
        Err("autostart em macOS/Linux deve usar o plugin via set_config".into())
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
async fn tunnel_start(state: tauri::State<'_, AppState>) -> Result<(), String> {
    let mut running = state.tunnel_running.lock().await;
    if *running {
        return Ok(());
    }
    *running = true;
    let cfg = state.config.lock().await.clone();
    let tunnel_cfg = tunnel::TunnelConfig {
        endpoint: cfg.tunnel_endpoint,
        server_pubkey: cfg.tunnel_server_pubkey,
        client_privkey: cfg.tunnel_client_privkey,
        enabled: cfg.tunnel_enabled,
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

#[tauri::command]
async fn tunnel_stop(state: tauri::State<'_, AppState>) -> Result<(), String> {
    state.tunnel.stop().await;
    // Não libera um novo start enquanto a task antiga ainda possui Wintun,
    // socket e rotas. Isso evita dois túneis concorrentes após stop/start rápido.
    for _ in 0..100 {
        if !*state.tunnel_running.lock().await { return Ok(()); }
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
    return Err("timeout ao encerrar túnel".into());
}

#[tauri::command]
async fn tunnel_status(state: tauri::State<'_, AppState>) -> Result<TunnelStatus, String> {
    Ok(state.tunnel.status.lock().await.clone())
}

#[tauri::command]
async fn tunnel_is_admin() -> bool {
    // Delega pro MESMO check que o startup usa. Este comando tinha uma cópia
    // própria com PROCESS_QUERY_INFORMATION — o bug já corrigido em
    // is_windows_admin (ver comentário lá). Resultado: aberto como admin, o
    // startup passava mas a aba Túnel continuava dizendo "precisa de admin".
    // Duas cópias do mesmo check é como o bug sobreviveu à primeira correção.
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
    use windows_sys::Win32::Foundation::HANDLE;
    use windows_sys::Win32::Security::{TOKEN_ELEVATION, GetTokenInformation, TokenElevation, TOKEN_INFORMATION_CLASS};
    use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};
    use windows_sys::Win32::Foundation::CloseHandle;

    // TOKEN_QUERY (0x0008) é o acesso mínimo pro GetTokenInformation; antes
    // usávamos PROCESS_QUERY_INFORMATION (0x0400), que pode ser negado pelo
    // token mesmo em processo elevado em algumas configs de segurança —
    // quando isso acontecia, is_windows_admin() retornava false e o companion
    // relançava via ShellExecuteW("runas") em loop (cada relaunch voltava
    // elevado mas a checagem falhava de novo).
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

/// Mostra a janela E força a apresentação da superfície do WebView2.
///
/// O bug (encurralado por sonda CDP em 19/07/2026): o renderer TINHA os
/// pixels — screenshot interno perfeito — mas a janela ficava branca até um
/// resize forçar a recomposição. É o "white window until resize" do WebView2
/// com janela `visible: false` + show() tardio. O contorno é o do ecossistema:
/// nudge de ±1px depois do show. Dois nudges com atraso porque logo-após-show
/// a superfície pode nem existir ainda; 1px por 60ms é imperceptível.
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
    let quit = MenuItem::with_id(app, "quit", "Sair", true, None::<&str>)?;
    let show = MenuItem::with_id(app, "show", "Abrir", true, None::<&str>)?;
    let pause = MenuItem::with_id(app, "pause", "Pausar scanner", true, None::<&str>)?;
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
    tunnel.stop().await;
    for _ in 0..100 {
        if !tunnel.status.lock().await.running { break; }
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
}

// ─── Auto-updater ────────────────────────────────────────────────────────────

/// Verifica update no startup. Se há, baixa+instala+relaunch automaticamente.
/// Sutil: emite eventos `update-status` pro frontend (downloading/installed/erro)
/// mas nunca pede confirmação. installMode=passive no Windows já mostra uma
/// barra mínima do installer. Sem botão "ficar na versão antiga".
#[cfg(desktop)]
async fn auto_update(app: &tauri::AppHandle) -> Result<(), anyhow::Error> {
    use tauri_plugin_updater::UpdaterExt;
    let update = match app.updater()?.check().await {
        Ok(Some(u)) => u,
        Ok(None) => return Ok(()), // já na última versão
        Err(e) => return Err(e.into()),
    };
    tracing::info!("auto-update: {} -> {}", update.current_version, update.version);
    let _ = app.emit("update-status", "downloading");
    update.download_and_install(
        |chunk, total| {
            tracing::debug!("auto-update: {chunk} / {total:?} bytes");
        },
        || {
            tracing::info!("auto-update: download concluído, instalando");
        },
    ).await?;
    let _ = app.emit("update-status", "installed");
    stop_tunnel_and_wait(&app.state::<AppState>().tunnel).await;
    // Windows: o installer passive já fecha o app. Outros SOs: relaunch explícito.
    app.restart();
}

pub fn run() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info,tauri=info".into()),
        )
        .init();

    // Npcap moderno sem modo WinPcap põe wpcap.dll num subdir que o loader do
    // Windows não acha sem PATH/SetDllDirectory. Tem que rodar ANTES de qualquer
    // pcap::Device::list (no setup do Tauri) — depois da primeira chamada falha
    // a DLL já ficou cacheada como "não encontrada" e não adianta mais.
    sniffer::ensure_npcap_dll_path();

    let install_id = config::install_id();
    let mut cfg = config::load();
    cfg.install_id = install_id;

    // O companion SEMPRE roda como admin — sniffer (Npcap), túnel (wintun) e
    // DNS precisam. Sem admin: relança com UAC (runas) e sai. No startup
    // automático o Task Scheduler abre com HighestAvailable = sem prompt.
    //
    // Rede anti-loop: passamos --ziggs-elev no relaunch. Se o processo foi
    // relançado por nós e ainda assim não aparece como admin (ex.: usuário
    // negou o UAC, ou bug raro em is_windows_admin), NÃO relança de novo —
    // mostra MessageBox fatal e sai. Nunca segue sem admin: o sniffer não
    // consegue abrir a capture sem Npcap+admin, e o usuário precisa saber
    // disso pra poder consertar (aceitar UAC, instalar Npcap, etc).
    #[cfg(target_os = "windows")]
    {
        let already_tried = std::env::args().any(|a| a == "--ziggs-elev");
        if !is_windows_admin() {
            if already_tried {
                use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_OK, MB_ICONERROR};
                let title: Vec<u16> = "Ziggs Companion\0".encode_utf16().collect();
                let msg: Vec<u16> =
                    "O companion precisa de privilégios de administrador para capturar pacotes \
                     (Npcap) e gerenciar o túnel (wintun).\n\n\
                     Se você negou o prompt UAC, tente novamente aceitando. \
                     Se o problema persiste, execute o companion diretamente como administrador \
                     (botão direito → Executar como administrador).\0"
                        .encode_utf16().collect();
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
                // Preserva os args (--minimized) no relaunch elevado e marca
                // que já tentamos elevar (--ziggs-elev lido pelo processo filho).
                let mut args = std::env::args().skip(1).collect::<Vec<_>>().join(" ");
                if !args.is_empty() { args.insert_str(0, " "); }
                args = format!("--ziggs-elev{args}");
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
    let scanner = Scanner::new().with_queue(Arc::clone(&transfer_queue));
    let state = AppState {
        config: Arc::new(Mutex::new(cfg)),
        scanner,
        scanner_running: Arc::new(Mutex::new(false)),
        tunnel: Tunnel::new(),
        tunnel_running: Arc::new(Mutex::new(false)),
        transfer_queue,
        sniffer: Sniffer::new(),
        sniffer_running: Arc::new(Mutex::new(false)),
        lootlog: Arc::new(Mutex::new(lootlog::LootlogStatus::default())),
    };

    let mut builder = tauri::Builder::default();

    // Single-instance: se já está rodando, foca a janela existente e fecha esta.
    // ponytail: tem que ser o primeiro plugin registrado pra interceptar antes
    // de qualquer outro inicializar. Desktop only (mobile não faz sentido).
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
            // Auto-update: init updater plugin (desktop only).
            // ponytail: check silencioso no startup — se há update, baixa e instala
            // sem pedir confirmação. Instala passive (barra de progresso pequena),
            // depois relança. Usuário não escolhe ficar na versão antiga.
            #[cfg(desktop)]
            {
                let _ = app.handle().plugin(tauri_plugin_updater::Builder::new().build());
                let handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    if let Err(e) = auto_update(&handle).await {
                        tracing::warn!("auto-update: {e:#}");
                    }
                });
            }
            // Tabela de nomes de feitiço do damage meter: cache em disco, senão
            // baixa. Em background — o meter funciona sem ela (fallback pro id).
            tauri::async_runtime::spawn(load_spell_names());
            tauri::async_runtime::spawn(lootlog::load_item_names());

            // Auto-submit do lootlog: fica de olho nos eventos do usuário e
            // envia sozinho quando algum entra em revisão. O worker checa o
            // toggle a cada volta, então ligar/desligar vale na hora.
            {
                let st = app.state::<AppState>();
                tauri::async_runtime::spawn(auto_lootlog_worker(
                    Arc::clone(&st.config),
                    Arc::clone(&st.sniffer.loot),
                ));
                // Badge ILUSTRATIVO de prata da aba Lootlog — só pra dar uma
                // noção de quanto de loot passou pela sessão. Não load-bearing.
                tauri::async_runtime::spawn(loot_silver_worker(
                    Arc::clone(&st.sniffer.loot),
                    Arc::clone(&st.sniffer.stats),
                ));
                // Mantém perfis quentes no site: o próprio char + players vistos.
                tauri::async_runtime::spawn(warm_self_worker(
                    Arc::clone(&st.sniffer.entities),
                    Arc::clone(&st.sniffer.stats),
                    Arc::clone(&st.sniffer.aodp_server),
                ));
            }

            build_tray(app.handle())?;
            // Janela fica invisível no tauri.conf; só mostra se NÃO for boot
            // minimizado (--minimized do Task Scheduler = direto pra bandeja).
            if !start_minimized {
                if let Some(w) = app.get_webview_window("main") {
                    present_window(&w);
                }
            }
            if autostart_on {
                #[cfg(target_os = "windows")]
                {
                    // Sem Npcap o companion não faz nada útil sozinho no
                    // boot — não registra a tarefa. Reavaliado a cada
                    // startup: se o usuário instalar o Npcap depois, o
                    // próximo launch já registra sozinho, sem precisar
                    // mexer no toggle.
                    if sniffer::npcap_installed() {
                        let _ = set_autostart(true);
                    }
                }
                #[cfg(not(target_os = "windows"))]
                {
                    let autostart_mgr = app.autolaunch();
                    let _ = autostart_mgr.enable();
                }
            }
            // Auto-inicia scanner e túnel se os toggles estiverem ligados.
            // ponytail: roda fora do command handler — replica o mínimo de
            // start_scanner/tunnel_start inline (mesmo padrão de spawn).
            let state: tauri::State<AppState> = app.state();
            if let Err(e) = tunnel::scrub_stale_routes_now() {
                tracing::warn!("tunnel startup cleanup: {e:#}");
            }
            let cfg = state.config.blocking_lock().clone();
            // Sincroniza pvp_pause do config no scanner.
            *state.scanner.pvp_pause.blocking_lock() = cfg.pvp_pause_transfer;
            // Sincroniza os gates de captura do sniffer com o config persistido.
            // capture_prices é SEMPRE true (razão de ser do companion); o gate
            // existe no sniffer só pra emergência (pause manual via código).
            {
                use std::sync::atomic::Ordering;
                state.sniffer.capture_loot.store(cfg.collect_auto_lootlog, Ordering::Relaxed);
                state.sniffer.capture_damage.store(cfg.collect_damage_meter, Ordering::Relaxed);
                state.sniffer.capture_prices.store(true, Ordering::Relaxed);
                state.sniffer.feed_aodp.store(cfg.feed_aodp, Ordering::Relaxed);
            }
            // Pré-carrega as saídas locais e seus handshakes antes do usuário
            // abrir o jogo. Se o toggle persistido estiver ligado, conecta logo
            // depois usando o mesmo resultado; caso contrário só aquece a lista.
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
            // Battle scanning é SEMPRE on (razão de ser do companion).
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
            // ── Uploader ÚNICO da fila de transferência ───────────────────
            //
            // Antes cada produtor dava seu próprio `flush_all`, e a volta pra
            // zona azul despejava a fila inteira numa rajada — pico de rede e
            // CPU logo depois da luta, que é o pior momento possível.
            //
            // Agora existe UM lugar decidindo ritmo e política: poucos itens
            // por tick, com respiro entre eles, e só quando `heavy_work_ok`.
            // Nada some — em zona de risco a fila só engorda e é drenada
            // devagar depois. Produtor novo deve apenas ENFILEIRAR.
            {
                const TICK_SECS: u64 = 5;
                const CHUNK: usize = 3;
                let q = Arc::clone(&state.transfer_queue);
                let up_sniffer = state.sniffer.clone_shared();
                let up_zone = Arc::clone(&state.scanner.zone);
                let up_pause = Arc::clone(&state.scanner.pvp_pause);
                let up_stats = Arc::clone(&state.scanner.stats);
                tauri::async_runtime::spawn(async move {
                    let api = api::ApiClient::new(config::API_BASE_URL);
                    loop {
                        tokio::time::sleep(std::time::Duration::from_secs(TICK_SECS)).await;
                        if q.pending_count().await == 0 { continue; }
                        if !heavy_work_ok(&up_sniffer, &up_zone, &up_pause).await {
                            continue; // zona de risco: espera, não perde
                        }
                        let (sent, failed) = q.flush_some(&api, CHUNK).await;
                        if sent > 0 || failed > 0 {
                            up_stats.lock().await.queued_reports = q.pending_count().await;
                        }
                    }
                });
            }
            // Auto-inicia o sniffer (packet capture) — sempre ligado, sem toggle.
            // Ele alimenta nome/mapa/party da UI; loot/dano/preço só acumulam
            // com os gates ligados.
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
            // Envio periódico de preços: drena o buffer do sniffer a cada 60s,
            // agrega o menor preço por (item, quality, city) e envia via fila
            // de transferência (respeita pausa em PvP; persiste se falhar).
            {
                let prices_buf = Arc::clone(&state.sniffer.prices);
                let q = Arc::clone(&state.transfer_queue);
                let debug = Arc::clone(&state.sniffer.debug);
                tauri::async_runtime::spawn(async move {
                    loop {
                        tokio::time::sleep(std::time::Duration::from_secs(60)).await;
                        let raw: Vec<serde_json::Value> = {
                            let mut b = prices_buf.lock().await;
                            if b.is_empty() { continue; }
                            b.drain(..).collect()
                        };
                        // Menor sell_price_min por (item_id, quality, city).
                        let mut best: std::collections::HashMap<String, serde_json::Value> = std::collections::HashMap::new();
                        for row in raw {
                            let key = format!(
                                "{}|{}|{}",
                                row.get("item_id").and_then(|v| v.as_str()).unwrap_or(""),
                                row.get("quality").and_then(|v| v.as_i64()).unwrap_or(1),
                                row.get("city").and_then(|v| v.as_str()).unwrap_or(""),
                            );
                            let price = row.get("sell_price_min").and_then(|v| v.as_i64()).unwrap_or(i64::MAX);
                            let cur = best.get(&key).and_then(|r| r.get("sell_price_min")).and_then(|v| v.as_i64());
                            if cur.map_or(true, |c| price < c) { best.insert(key, row); }
                        }
                        let n_rows = best.len();
                        q.enqueue_prices(best.into_values().collect()).await;
                        push_debug(&debug, "info",
                            &format!("prices: enfileiradas {n_rows} rows (agregadas p/ backend)"));
                        // Só enfileira. Quem envia é o uploader único, no
                        // ritmo dele — produtor não decide política de rede.
                    }
                });
            }
            // Envio de market history: drena o buffer a cada 60s e envia via
            // fila de transferência (persistência + pausa em PvP como os preços).
            {
                let hist_buf = Arc::clone(&state.sniffer.market_history);
                let q = Arc::clone(&state.transfer_queue);
                let debug = Arc::clone(&state.sniffer.debug);
                tauri::async_runtime::spawn(async move {
                    loop {
                        tokio::time::sleep(std::time::Duration::from_secs(60)).await;
                        let rows: Vec<serde_json::Value> = {
                            let mut b = hist_buf.lock().await;
                            if b.is_empty() { continue; }
                            b.drain(..).collect()
                        };
                        let n_rows = rows.len();
                        q.enqueue_market_history(rows).await;
                        push_debug(&debug, "info",
                            &format!("market_history: enfileirados {n_rows} buckets"));
                        // Só enfileira — ver comentário do uploader único.
                    }
                });
            }
            // Upload ao AODP: drena os lotes de ordens de mercado e envia (PoW).
            // Um por vez, com respiro entre eles — o PoW é CPU-bound.
            //
            // É a tarefa mais CARA do companion e a única que gasta CPU de
            // verdade. Fica atrás do `heavy_work_ok`: em zona PvP com o jogo
            // aberto, os lotes só esperam na fila (não se perdem) e sobem
            // quando o jogador volta pra zona azul ou fecha o jogo.
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
                        // Espia a fila antes de decidir: sem lote, nem precisa
                        // consultar zona.
                        if aodp_out.lock().await.is_empty() { continue; }
                        if !heavy_work_ok(&aodp_sniffer, &aodp_zone, &aodp_pause).await {
                            continue; // em PvP: o lote espera, não se perde
                        }
                        let batch = { aodp_out.lock().await.pop() };
                        let Some(batch) = batch else { continue };
                        if let Err(e) = aodp::upload(&client, &batch).await {
                            let line = sniffer::DebugLine {
                                ts: photon_parser::now_iso_utc(),
                                level: "warn".into(),
                                msg: format!("AODP upload falhou: {e:#}"),
                            };
                            let mut d = debug.lock().await;
                            d.push(line);
                            if d.len() > 500 { let ex = d.len() - 500; d.drain(..ex); }
                        }
                    }
                });
            }
            // Túnel NÃO auto-inicia no boot de propósito: o usuário precisa
            // ligar manualmente toda vez. Forçar o clique expõe mais os anúncios
            // da aba Rota, que cobrem o custo da VPS. O toggle `tunnel_enabled`
            // só decide se o botão aparece "ligado" na UI — não liga sozinho.
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
            set_zone,
            flush_transfer_queue,
            pending_count,
            classify_zone,
            get_albion_pid,
            start_sniffer,
            stop_sniffer,
            get_sniff_stats,
            get_sniffer_debug,
            open_npcap_download,
            open_url,
            companion_login,
            companion_poll_auth,
            companion_logout,
            get_active_events,
            submit_captured_loot,
        ])
        .run(tauri::generate_context!())
        .expect("erro ao iniciar companion");
}
#[cfg(test)]
mod policy_tests {
    use super::*;

    fn zona(z: transfer::ZoneType) -> Arc<Mutex<transfer::ZoneType>> {
        Arc::new(Mutex::new(z))
    }

    /// A política inteira do "quando pode gastar máquina" mora aqui. Errar em
    /// qualquer direção é ruim: liberar em PvP faz o jogador morrer, travar
    /// demais faz o dado nunca subir.
    #[tokio::test]
    async fn quando_pode_gastar_maquina() {
        let sniffer = Sniffer::new();
        let pausa_on = Arc::new(Mutex::new(true));
        let pausa_off = Arc::new(Mutex::new(false));

        // Jogo fechado (online=false, o default): libera em qualquer zona.
        assert!(heavy_work_ok(&sniffer, &zona(transfer::ZoneType::PvP), &pausa_on).await,
                "jogo fechado é a melhor hora pra trabalhar");

        sniffer.stats.lock().await.online = true;

        assert!(!heavy_work_ok(&sniffer, &zona(transfer::ZoneType::PvP), &pausa_on).await,
                "jogando em zona de risco: não encosta na CPU dele");
        assert!(heavy_work_ok(&sniffer, &zona(transfer::ZoneType::Blue), &pausa_on).await,
                "zona azul: pode enviar normalmente");
        assert!(heavy_work_ok(&sniffer, &zona(transfer::ZoneType::Unknown), &pausa_on).await,
                "zona desconhecida não bloqueia — só PvP confirmado bloqueia");
        assert!(heavy_work_ok(&sniffer, &zona(transfer::ZoneType::PvP), &pausa_off).await,
                "usuário desligou a pausa: respeita a escolha dele");
    }


    fn event(id: i64, state: &str) -> api::ActiveEvent {
        api::ActiveEvent {
            event_id: id,
            guild_id: 1,
            guild_name: None,
            title: None,
            scheduled_at: None,
            state: state.into(),
        }
    }

    #[test]
    fn auto_lootlog_exige_um_unico_evento_em_review() {
        assert_eq!(single_review_event(&[event(1, "review")]).map(|e| e.event_id), Some(1));
        assert!(single_review_event(&[event(1, "review"), event(2, "review")]).is_none());
        assert!(single_review_event(&[event(1, "in_progress")]).is_none());
    }
}
