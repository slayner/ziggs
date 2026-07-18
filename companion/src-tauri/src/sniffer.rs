// Packet sniffer: captura pacotes UDP do Albion via libpcap/Npcap.
//
// Abre listeners em TODAS as interfaces com IPv4 (VPN/ExitLag cria interfaces
// virtuais — se ouvirmos só uma, perdemos o tráfego quando o user liga VPN).
// Filtro BPF: "udp and (port 5056 or port 5055 or port 4535)" — o jogo usa
// essas 3 portas.
//
// Cada pacote é passado pro PhotonParser. Eventos de loot (opcode 256) vão
// pro buffer de loot. Logs de debug vão pro buffer de debug (mostrados no
// terminal da UI). Detecção online/offline: sem pacotes por 5s = offline.
//
// Npcap é necessário no Windows. Requer admin.

use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Instant;
use std::sync::mpsc;
use tokio::sync::Mutex;
use serde::{Deserialize, Serialize};

use crate::photon_parser::{
    PhotonParser, PhotonValue, extract_player_state, extract_party, extract_loot,
    extract_new_character, extract_health, extract_market, extract_gold,
    extract_history_request, extract_history_response, LootEvent, DamageAcc, HistoryReq,
};
use crate::aodp::{self, AodpBatch, AodpServer};

/// Cidades com marketplace real — só reportamos preço quando o jogador está
/// numa delas (o nome do mapa vira a cidade do report).
const MARKET_CITIES: [&str; 8] = [
    "Martlock", "Bridgewatch", "Lymhurst", "Fort Sterling",
    "Thetford", "Caerleon", "Brecilien", "Black Market",
];

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SniffStats {
    pub running: bool,
    pub online: bool,
    pub packets_captured: u64,
    pub packets_parsed: u64,
    pub operations_extracted: u64,
    pub loot_count: u64,
    pub last_map: String,
    pub last_map_name: String,
    pub last_zone: String,
    pub player_name: String,
    pub guild_name: String,
    pub alliance_name: String,
    pub party_members: Vec<String>,
    pub error: Option<String>,
}

impl Default for SniffStats {
    fn default() -> Self {
        Self {
            running: false,
            online: false,
            packets_captured: 0,
            packets_parsed: 0,
            operations_extracted: 0,
            loot_count: 0,
            last_map: String::new(),
            last_map_name: String::new(),
            last_zone: "unknown".into(),
            player_name: String::new(),
            guild_name: String::new(),
            alliance_name: String::new(),
            party_members: vec![],
            error: None,
        }
    }
}

/// Linha de debug do sniffer — mostrada no terminal da UI.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DebugLine {
    pub ts: String,
    pub level: String,   // "info" | "warn" | "err"
    pub msg: String,
}

pub struct Sniffer {
    pub stats: Arc<Mutex<SniffStats>>,
    pub loot: Arc<Mutex<Vec<LootEvent>>>,
    pub debug: Arc<Mutex<Vec<DebugLine>>>,
    /// Registro entityId → nome (NewCharacter), usado pra resolver o damage meter.
    pub entities: Arc<Mutex<HashMap<i64, String>>>,
    /// Dano/cura acumulado por causer_id na sessão.
    pub damage: Arc<Mutex<HashMap<i64, DamageAcc>>>,
    /// Rows de preço prontos pro POST /companion/prices/submit — drenados
    /// periodicamente pela task de envio no lib.rs.
    pub prices: Arc<Mutex<Vec<serde_json::Value>>>,
    /// Rows de market history prontas pro POST /companion/market-history/submit.
    pub market_history: Arc<Mutex<Vec<serde_json::Value>>>,
    /// Requests de history pendentes (message_id → info), aguardando a response.
    history_pending: Arc<Mutex<HashMap<u64, HistoryReq>>>,
    /// Lotes de ordens de mercado prontos pro upload ao AODP (verbatim).
    pub aodp_out: Arc<Mutex<Vec<AodpBatch>>>,
    /// Última região AODP inferida do IP do servidor do Albion (por pacote).
    pub aodp_server: Arc<Mutex<Option<AodpServer>>>,
    /// Gates de captura — espelham os toggles do config (set_config sincroniza).
    /// O sniffer sempre roda (nome/mapa/party alimentam a UI), mas só acumula
    /// loot/dano/preço com o gate ligado.
    pub capture_loot: Arc<AtomicBool>,
    pub capture_damage: Arc<AtomicBool>,
    pub capture_prices: Arc<AtomicBool>,
    /// Encaminhar ordens de mercado ao AODP (devolver dado à comunidade).
    pub feed_aodp: Arc<AtomicBool>,
    shutdown: Arc<Mutex<bool>>,
}

impl Sniffer {
    pub fn new() -> Self {
        Self {
            stats: Arc::new(Mutex::new(SniffStats::default())),
            loot: Arc::new(Mutex::new(Vec::new())),
            debug: Arc::new(Mutex::new(Vec::new())),
            entities: Arc::new(Mutex::new(HashMap::new())),
            damage: Arc::new(Mutex::new(HashMap::new())),
            prices: Arc::new(Mutex::new(Vec::new())),
            market_history: Arc::new(Mutex::new(Vec::new())),
            history_pending: Arc::new(Mutex::new(HashMap::new())),
            aodp_out: Arc::new(Mutex::new(Vec::new())),
            aodp_server: Arc::new(Mutex::new(None)),
            capture_loot: Arc::new(AtomicBool::new(false)),
            capture_damage: Arc::new(AtomicBool::new(false)),
            capture_prices: Arc::new(AtomicBool::new(false)),
            feed_aodp: Arc::new(AtomicBool::new(false)),
            shutdown: Arc::new(Mutex::new(false)),
        }
    }

    /// Clona compartilhando TODOS os Arcs (inclusive shutdown — stop() no
    /// original para a task spawnada, o que o antigo with_stats não fazia).
    pub fn clone_shared(&self) -> Self {
        Self {
            stats: Arc::clone(&self.stats),
            loot: Arc::clone(&self.loot),
            debug: Arc::clone(&self.debug),
            entities: Arc::clone(&self.entities),
            damage: Arc::clone(&self.damage),
            prices: Arc::clone(&self.prices),
            market_history: Arc::clone(&self.market_history),
            history_pending: Arc::clone(&self.history_pending),
            aodp_out: Arc::clone(&self.aodp_out),
            aodp_server: Arc::clone(&self.aodp_server),
            capture_loot: Arc::clone(&self.capture_loot),
            capture_damage: Arc::clone(&self.capture_damage),
            capture_prices: Arc::clone(&self.capture_prices),
            feed_aodp: Arc::clone(&self.feed_aodp),
            shutdown: Arc::clone(&self.shutdown),
        }
    }

    pub async fn stop(&self) {
        *self.shutdown.lock().await = true;
    }

    /// Loop principal: abre listeners em todas as interfaces, captura pacotes.
    ///
    /// A captura pcap é bloqueante (pcap_next_ex trava a thread). Como estamos
    /// num runtime tokio, movemos a captura pra threads dedicadas (spawn_blocking)
    /// que mandam pacotes por um channel std::mpsc. O task async processa os
    /// pacotes recebidos sem bloquear o executor.
    pub async fn run(&self) {
        *self.shutdown.lock().await = false;
        {
            let mut s = self.stats.lock().await;
            s.running = true;
            s.error = None;
        }
        // Zera o arquivo de log a cada sessão pra ficar fácil de ler.
        if let Some(p) = debug_log_path() {
            if let Some(parent) = p.parent() { let _ = std::fs::create_dir_all(parent); }
            let _ = std::fs::write(&p, format!("=== sessão {} ===\n", crate::photon_parser::now_iso_utc()));
        }
        self.debug_log("info", "Sniffer iniciando — procurando interfaces de rede…").await;

        let devices = match pcap::Device::list() {
            Ok(d) => d,
            Err(e) => {
                let msg = format!("pcap Device::list falhou: {}. Npcap instalado?", e);
                self.debug_log("err", &msg).await;
                let mut s = self.stats.lock().await;
                s.error = Some(msg);
                s.running = false;
                return;
            }
        };

        // Abre cada interface e cria um canal pra enviar pacotes.
        let (tx, rx) = mpsc::channel::<(usize, Vec<u8>)>();
        let mut opened_count = 0;
        for dev in &devices {
            if dev.name.contains("lo") || dev.name.contains("Loopback") {
                continue;
            }
            let has_ipv4 = dev.addresses.iter().any(|a| matches!(a.addr, std::net::IpAddr::V4(_)));
            if !has_ipv4 {
                continue;
            }
            let desc = dev.name.clone();
            match open_device_capture(dev) {
                Ok(cap) => {
                    opened_count += 1;
                    let l2 = l2_len_for(cap.get_datalink());
                    self.debug_log("info", &format!(
                        "Ouvindo interface: {} (L2={}b — {})",
                        desc, l2, if l2 == 0 { "IP puro / VPN" } else { "ethernet" }
                    )).await;
                    // Spawna uma thread dedicada pra cada capture (bloqueante).
                    let tx_clone = tx.clone();
                    std::thread::spawn(move || {
                        let mut cap = cap;
                        loop {
                            match cap.next_packet() {
                                Ok(packet) => {
                                    let _ = tx_clone.send((l2, packet.data.to_vec()));
                                }
                                Err(pcap::Error::TimeoutExpired) => { /* sem pacote */ }
                                Err(e) => {
                                    let _ = tx_clone.send((0, Vec::new())); // sinaliza erro
                                    eprintln!("pcap erro em {}: {}", desc, e);
                                    break;
                                }
                            }
                        }
                    });
                }
                Err(e) => {
                    self.debug_log("warn", &format!("Não foi possível abrir {}: {}", desc, e)).await;
                }
            }
        }
        drop(tx); // fecha o sender original — rx fecha quando todas as threads terminam

        if opened_count == 0 {
            let msg = "Nenhuma interface de rede pôde ser aberta. Precisa admin/Npcap?".to_string();
            self.debug_log("err", &msg).await;
            let mut s = self.stats.lock().await;
            s.error = Some(msg);
            s.running = false;
            return;
        }
        self.debug_log("info", &format!("{} interface(s) ativa(s). Aguardando pacotes do Albion…", opened_count)).await;

        let mut parser = PhotonParser::new();
        let mut last_packet_time = Instant::now();
        let mut was_online = false;
        let mut logged_health = false;
        let mut seen_codes: HashSet<i16> = HashSet::new();
        let mut last_heartbeat = Instant::now();
        let mut raw_pkts: u64 = 0;

        loop {
            if *self.shutdown.lock().await {
                let mut s = self.stats.lock().await;
                s.running = false;
                self.debug_log("info", "Sniffer parado.").await;
                break;
            }

            // Recebe pacotes do canal (non-blocking — se não tem pacote, continua).
            match rx.recv_timeout(std::time::Duration::from_millis(50)) {
                Ok((l2_hint, data)) => {
                    if !data.is_empty() { raw_pkts += 1; }
                    // Offset do payload Photon: L2 (ethernet/raw) + IP (IHL real) + UDP.
                    // Hardcodar 42 quebra sob VPN/ExitLag (adaptadores IP-puro = 0 de L2).
                    let off = match photon_offset(&data, l2_hint) {
                        Some(o) => o,
                        None => continue,
                    };
                    last_packet_time = Instant::now();
                    // Região AODP: infere do IP do servidor do Albion no header IP.
                    if let Some(srv) = albion_server_from_frame(&data, l2_hint) {
                        *self.aodp_server.lock().await = Some(srv);
                    }
                    let photon_data = &data[off..];

                    {
                        let mut s = self.stats.lock().await;
                        s.packets_captured += 1;
                    }

                    if !was_online {
                        was_online = true;
                        let mut s = self.stats.lock().await;
                        s.online = true;
                        self.debug_log("info", "Albion detectado — pacotes recebidos. Capturando loot…").await;
                    }

                    // Rede de segurança: um pacote malformado nunca pode derrubar a
                    // captura. Se o parser entrar em pânico, logamos, resetamos o estado
                    // de fragmentos e seguimos.
                    let ops = match std::panic::catch_unwind(
                        std::panic::AssertUnwindSafe(|| parser.parse(photon_data))
                    ) {
                        Ok(ops) => ops,
                        Err(_) => {
                            self.debug_log("err", "parser entrou em pânico num pacote — pulando e resetando").await;
                            parser = PhotonParser::new();
                            continue;
                        }
                    };

                    if !ops.is_empty() {
                        let mut s = self.stats.lock().await;
                        s.packets_parsed += 1;
                        s.operations_extracted += ops.len() as u64;
                    }

                    for op in &ops {
                        // Rastreio de mapa: loga TODA ocorrência dos opcodes de zona
                        // (41=ChangeCluster resp, 17=JoinCluster, 294=ChangeCluster req)
                        // pra ver o que dispara em CADA troca de mapa.
                        if matches!(op.albion_code, 41 | 17 | 294) {
                            self.debug_log("info", &format!(
                                "ZONE op={} type={} :: {}", op.albion_code, op.message_type, dump_params(op)
                            )).await;
                        }

                        // Descoberta de protocolo: loga 1x cada albion_code visto com seus
                        // params. Os opcodes 2/41 podem estar errados nesta versão do jogo —
                        // isto revela qual opcode carrega nome/mapa/guild de verdade.
                        if op.albion_code >= 0 && seen_codes.insert(op.albion_code) && seen_codes.len() <= 90 {
                            self.debug_log("info", &format!(
                                "op code={} type={} :: {}", op.albion_code, op.message_type, dump_params(op)
                            )).await;
                        }

                        // op 103 = info de guild/aliança do jogador local (param 15/16).
                        // ponytail: inferido do stream — aparece 1x por sessão com valores
                        // constantes (o guild/aliança do próprio jogador). Confirmar com o user.
                        if op.albion_code == 103 {
                            let mut s = self.stats.lock().await;
                            if let Some(PhotonValue::String(g)) = op.parameters.get(&15) {
                                if !g.is_empty() { s.guild_name = g.clone(); }
                            }
                            if let Some(PhotonValue::String(a)) = op.parameters.get(&16) {
                                if !a.is_empty() { s.alliance_name = a.clone(); }
                            }
                        }

                        if let Some(state) = extract_player_state(op) {
                            let mut s = self.stats.lock().await;
                            let name_changed = !state.player_name.is_empty() && s.player_name != state.player_name;
                            let map_changed = !state.map_index.is_empty() && s.last_map != state.map_index;
                            if !state.player_name.is_empty() { s.player_name = state.player_name.clone(); }
                            if !state.guild_name.is_empty() { s.guild_name = state.guild_name.clone(); }
                            if !state.alliance_name.is_empty() { s.alliance_name = state.alliance_name.clone(); }
                            // Registra o jogador local (id→nome) pro damage meter — ele
                            // não vem nos eventos NewCharacter, só na resposta de Join.
                            if let (Some(id), false) = (state.local_object_id, state.player_name.is_empty()) {
                                self.entities.lock().await.insert(id, state.player_name.clone());
                            }
                            if !state.map_index.is_empty() {
                                s.last_map = state.map_index.clone();
                                s.last_map_name = crate::maps::resolve(&state.map_index);
                            }
                            if name_changed {
                                self.debug_log("info", &format!("Personagem detectado: {}", s.player_name)).await;
                            }
                            if map_changed {
                                self.debug_log("info", &format!("Mudança de mapa: {} ({})", s.last_map_name, s.last_map)).await;
                            }
                        }

                        if op.albion_code == 231 {
                            if let Some(names) = extract_party(op) {
                                let mut s = self.stats.lock().await;
                                s.party_members = names;
                            }
                        }

                        if self.capture_loot.load(Ordering::Relaxed) {
                            if let Some(loot) = extract_loot(op) {
                                let mut buf = self.loot.lock().await;
                                buf.push(loot.clone());
                                let mut s = self.stats.lock().await;
                                s.loot_count = buf.len() as u64;
                            }
                        }

                        // Damage meter: registro id→nome + acúmulo de dano/cura.
                        // Registro de entidades roda sempre (barato, e o meter
                        // precisa dos nomes vistos ANTES de ligar o toggle).
                        if let Some((id, name)) = extract_new_character(op) {
                            self.entities.lock().await.insert(id, name);
                        }
                        if self.capture_damage.load(Ordering::Relaxed) {
                            if let Some(h) = extract_health(op) {
                                if !logged_health {
                                    logged_health = true;
                                    let mut keys: Vec<u8> = op.parameters.keys().copied().collect();
                                    keys.sort();
                                    self.debug_log("info", &format!(
                                        "HealthUpdate detectado (calibração) — params: {:?}", keys
                                    )).await;
                                }
                                // Só dano (change < 0). Cura é descartada de
                                // propósito — o painel é de dano, só.
                                if h.change < 0.0 {
                                    let now = std::time::SystemTime::now()
                                        .duration_since(std::time::UNIX_EPOCH)
                                        .map(|d| d.as_secs())
                                        .unwrap_or(0);
                                    let mut dmg = self.damage.lock().await;
                                    dmg.entry(h.causer_id).or_default()
                                        .record(h.spell_id, -h.change, now);
                                }
                            }
                        }

                        // Mercado: respostas do marketplace enquanto o jogador navega.
                        // Alimenta o NOSSO banco (por cidade) e devolve ao AODP (verbatim).
                        if self.capture_prices.load(Ordering::Relaxed) || self.feed_aodp.load(Ordering::Relaxed) {
                            let cap = extract_market(op);
                            if !cap.raw_orders.is_empty() {
                                let (city, raw_map, map_name) = {
                                    let s = self.stats.lock().await;
                                    let city = MARKET_CITIES.iter()
                                        .find(|c| s.last_map_name.contains(*c))
                                        .map(|c| c.to_string());
                                    (city, s.last_map.clone(), s.last_map_name.clone())
                                };

                                // Nosso banco: só em cidade de market conhecida.
                                if self.capture_prices.load(Ordering::Relaxed) {
                                    if let Some(city) = &city {
                                        let ts = crate::photon_parser::now_iso_utc();
                                        let mut buf = self.prices.lock().await;
                                        for o in &cap.offers {
                                            buf.push(serde_json::json!({
                                                "item_id": o.item_id,
                                                "city": city,
                                                "quality": o.quality,
                                                "sell_price_min": o.unit_price_silver,
                                                "price_date": ts,
                                            }));
                                        }
                                        let len = buf.len();
                                        if len > 5000 { buf.drain(..len - 5000); }
                                    }
                                }

                                // AODP: encaminha as ordens cruas. LocationId numérico
                                // (o cluster atual) preenchido quando a ordem não traz —
                                // exatamente como o client oficial faz.
                                if self.feed_aodp.load(Ordering::Relaxed) {
                                    let server = self.aodp_server.lock().await.clone();
                                    let numeric_loc = raw_map.trim_start_matches('0');
                                    // Só envia se sabemos a região E o local é um cluster numérico.
                                    if let Some(server) = server {
                                        if !numeric_loc.is_empty() && numeric_loc.chars().all(|c| c.is_ascii_digit()) {
                                            let orders: Vec<serde_json::Value> = cap.raw_orders.iter().map(|o| {
                                                let mut o = o.clone();
                                                let empty = o.get("LocationId")
                                                    .and_then(|l| l.as_str())
                                                    .map_or(true, |l| l.is_empty());
                                                if empty {
                                                    o["LocationId"] = serde_json::Value::String(numeric_loc.to_string());
                                                }
                                                o
                                            }).collect();
                                            let natsmsg = serde_json::json!({ "Orders": orders }).to_string();
                                            let mut buf = self.aodp_out.lock().await;
                                            buf.push(AodpBatch {
                                                server_id: server.id,
                                                base_url: server.base_url,
                                                topic: "marketorders.ingest".into(),
                                                natsmsg,
                                            });
                                            // Cap: no máximo 50 lotes pendentes.
                                            let len = buf.len();
                                            if len > 50 { buf.drain(..len - 50); }
                                        } else {
                                            self.debug_log("warn", &format!(
                                                "AODP: local não numérico ({}), pulando envio", map_name
                                            )).await;
                                        }
                                    }
                                }
                            }
                        }

                        // Market history: gráfico agregado do próprio jogo. O
                        // request traz item/qualidade/escala; a response traz os
                        // buckets — correlacionados pelo message-id. Guardado no
                        // NOSSO banco (independência do AODP).
                        if self.capture_prices.load(Ordering::Relaxed) {
                            if let Some((mid, info)) = extract_history_request(op) {
                                let mut pend = self.history_pending.lock().await;
                                pend.insert(mid, info);
                                // Cap: descarta requests órfãos antigos (response nunca veio).
                                if pend.len() > 256 {
                                    let drop: Vec<u64> = pend.keys().take(pend.len() - 256).copied().collect();
                                    for k in drop { pend.remove(&k); }
                                }
                            }
                            if let Some((mid, buckets)) = extract_history_response(op) {
                                let info = self.history_pending.lock().await.remove(&mid);
                                if let Some(info) = info {
                                    let location = {
                                        let s = self.stats.lock().await;
                                        s.last_map.trim_start_matches('0').to_string()
                                    };
                                    // Região do servidor do Albion (detectada pelo IP dos
                                    // pacotes) — mercados são separados por servidor.
                                    let region = self.aodp_server.lock().await
                                        .as_ref().map(|s| s.region()).unwrap_or("west");
                                    let mut buf = self.market_history.lock().await;
                                    for b in buckets {
                                        buf.push(serde_json::json!({
                                            "albion_id": info.albion_id,
                                            "region": region,
                                            "quality": info.quality,
                                            "location": location,
                                            "timescale": info.timescale,
                                            "bucket_ts": b.bucket_ts,
                                            "item_count": b.item_count,
                                            "silver_amount": b.silver_amount,
                                        }));
                                    }
                                    let len = buf.len();
                                    if len > 10000 { buf.drain(..len - 10000); }
                                }
                            }
                        }

                        // Gold: preço do mercado de ouro (global, sem localização).
                        // Só precisa da região do servidor — encaminha ao AODP.
                        if self.feed_aodp.load(Ordering::Relaxed) {
                            if let Some(g) = extract_gold(op) {
                                if let Some(server) = self.aodp_server.lock().await.clone() {
                                    let natsmsg = serde_json::json!({
                                        "Prices": g.prices,
                                        "Timestamps": g.timestamps,
                                    }).to_string();
                                    let mut buf = self.aodp_out.lock().await;
                                    buf.push(AodpBatch {
                                        server_id: server.id,
                                        base_url: server.base_url,
                                        topic: "goldprices.ingest".into(),
                                        natsmsg,
                                    });
                                    let len = buf.len();
                                    if len > 50 { buf.drain(..len - 50); }
                                }
                            }
                        }
                    }
                }
                Err(mpsc::RecvTimeoutError::Timeout) => { /* sem pacotes — continua o loop */ }
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    self.debug_log("err", "Todas as interfaces de captura fecharam. Reinicie o companion.").await;
                    let mut s = self.stats.lock().await;
                    s.running = false;
                    break;
                }
            }

            // Heartbeat diagnóstico: a cada 10s mostra se pacotes/parse estão fluindo.
            // pkts>0 & ops=0 → parser quebrado; pkts=0 → captura não vê o Albion.
            if last_heartbeat.elapsed().as_secs() >= 10 {
                last_heartbeat = Instant::now();
                let s = self.stats.lock().await;
                self.debug_log("info", &format!(
                    "stats: raw={} pkts={} parsed={} ops={} loot={} online={} codes={}",
                    raw_pkts, s.packets_captured, s.packets_parsed, s.operations_extracted,
                    s.loot_count, s.online, seen_codes.len()
                )).await;
            }

            // Detecção offline: 5s sem pacotes.
            if was_online && last_packet_time.elapsed().as_secs() >= 5 {
                was_online = false;
                let mut s = self.stats.lock().await;
                s.online = false;
                self.debug_log("warn", "Sem pacotes do Albion há 5s — jogo fechado ou VPN mudou rota?").await;
            }
        }
    }

    async fn debug_log(&self, level: &str, msg: &str) {
        let ts = crate::photon_parser::now_iso_utc();
        // Também grava em arquivo (Documents/ziggs-companion/companion-debug.log)
        // pra diagnóstico — o terminal da UI trunca, o arquivo não.
        if let Some(p) = debug_log_path() {
            use std::io::Write;
            if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&p) {
                let _ = writeln!(f, "{} [{}] {}", ts, level.to_uppercase(), msg);
            }
        }
        let line = DebugLine {
            ts,
            level: level.to_string(),
            msg: msg.to_string(),
        };
        let mut buf = self.debug.lock().await;
        buf.push(line);
        if buf.len() > 500 {
            let excess = buf.len() - 500;
            buf.drain(..excess);
        }
    }
}

/// Caminho do arquivo de log de diagnóstico.
fn debug_log_path() -> Option<std::path::PathBuf> {
    dirs::document_dir().map(|d| d.join("ziggs-companion").join("companion-debug.log"))
}

/// Abre uma capture numa device específica com filtro BPF das 3 portas do Albion.
fn open_device_capture(dev: &pcap::Device) -> Result<pcap::Capture<pcap::Active>, String> {
    let builder = pcap::Capture::from_device(dev.clone())
        .map_err(|e| format!("from_device: {}", e))?;
    let builder = builder.promisc(true).immediate_mode(true);
    let mut opened = builder.open()
        .map_err(|e| format!("open: {}. Precisa admin/Npcap?", e))?;
    opened.filter("udp and (port 5056 or port 5055 or port 4535)", true)
        .map_err(|e| format!("BPF filter: {}", e))?;
    Ok(opened)
}

/// Repr compacto dos params de uma operação (idx=valor) — pra calibrar índices.
fn dump_params(op: &crate::photon_parser::ParsedOperation) -> String {
    use crate::photon_parser::PhotonValue;
    let mut keys: Vec<u8> = op.parameters.keys().copied().collect();
    keys.sort();
    keys.iter().map(|k| {
        let r = match &op.parameters[k] {
            PhotonValue::String(s) => format!("\"{}\"", s),
            PhotonValue::Byte(n) => n.to_string(),
            PhotonValue::Short(n) => n.to_string(),
            PhotonValue::Int(n) => n.to_string(),
            PhotonValue::Long(n) => n.to_string(),
            PhotonValue::Float(n) => n.to_string(),
            PhotonValue::Double(n) => n.to_string(),
            PhotonValue::Bool(b) => b.to_string(),
            PhotonValue::Array(a) => format!("arr[{}]", a.len()),
            PhotonValue::Bytes(b) => format!("bytes[{}]", b.len()),
            PhotonValue::Dictionary(_) => "dict".into(),
            PhotonValue::Null => "null".into(),
        };
        format!("{}={}", k, r)
    }).collect::<Vec<_>>().join(" ")
}

/// Tamanho do header de camada 2 a partir do datalink da interface.
/// Ethernet=14; NULL (BSD loopback / algumas VPN)=4; resto (RAW/IP puro)=0.
fn l2_len_for(dl: pcap::Linktype) -> usize {
    match dl.0 {
        1 => 14, // DLT_EN10MB
        0 => 4,  // DLT_NULL
        _ => 0,  // DLT_RAW e afins: pacote IP puro (VPN/ExitLag/TUN)
    }
}

/// Offset do payload Photon dentro do frame capturado.
/// Tenta o L2 sugerido pelo datalink, depois cai pra 0/14/4 e valida a estrutura
/// IPv4+UDP — assim funciona em ethernet, IP-puro (VPN) e loopback sem hardcodar.
fn photon_offset(data: &[u8], l2_hint: usize) -> Option<usize> {
    for &l2 in &[l2_hint, 0, 14, 4] {
        if data.len() < l2 + 28 { continue; } // 20 IP mín + 8 UDP
        let vihl = data[l2];
        if vihl >> 4 != 4 { continue; }        // só IPv4 (Albion é IPv4)
        let ihl = (vihl & 0x0F) as usize * 4;
        if ihl < 20 { continue; }
        // Confirma protocolo UDP (byte 9 do header IPv4 = 17).
        if data[l2 + 9] != 17 { continue; }
        let off = l2 + ihl + 8;
        if data.len() >= off { return Some(off); }
    }
    None
}

/// Infere a região AODP a partir dos IPs (origem/destino) do header IPv4.
/// Src IP (bytes 12-15) e dst IP (16-19) — um deles é o servidor do Albion.
fn albion_server_from_frame(data: &[u8], l2_hint: usize) -> Option<AodpServer> {
    for &l2 in &[l2_hint, 0, 14, 4] {
        if data.len() < l2 + 20 { continue; }
        let vihl = data[l2];
        if vihl >> 4 != 4 { continue; }
        if data[l2 + 9] != 17 { continue; } // UDP
        let src = [data[l2 + 12], data[l2 + 13], data[l2 + 14], data[l2 + 15]];
        let dst = [data[l2 + 16], data[l2 + 17], data[l2 + 18], data[l2 + 19]];
        if let Some(s) = aodp::server_for_ip(src).or_else(|| aodp::server_for_ip(dst)) {
            return Some(s);
        }
        return None; // IP header válido mas nenhum lado é servidor Albion conhecido
    }
    None
}

#[cfg(test)]
mod tests {
    use super::photon_offset;

    // UDP/IPv4 mínimo: [ip header 20][udp 8][payload]. version=4, ihl=5, proto=17.
    fn ipv4_udp(payload: &[u8]) -> Vec<u8> {
        let mut p = vec![0u8; 28];
        p[0] = 0x45;   // version 4, IHL 5
        p[9] = 17;     // protocolo UDP
        p.extend_from_slice(payload);
        p
    }

    #[test]
    fn ethernet_offset() {
        // 14 bytes de ethernet na frente
        let mut frame = vec![0u8; 14];
        frame.extend_from_slice(&ipv4_udp(b"PHOTON"));
        assert_eq!(photon_offset(&frame, 14), Some(14 + 28));
        assert_eq!(&frame[42..], b"PHOTON");
    }

    #[test]
    fn raw_ip_offset_vpn() {
        // Sem ethernet (VPN/ExitLag). Hint errado (14) mas o fallback acha em 0.
        let frame = ipv4_udp(b"PHOTON");
        assert_eq!(photon_offset(&frame, 14), Some(28));
        assert_eq!(&frame[28..], b"PHOTON");
    }

    #[test]
    fn ip_options_offset() {
        // IHL=6 (24 bytes de header IP, com 4 de options)
        let mut p = vec![0u8; 32];
        p[0] = 0x46;   // version 4, IHL 6
        p[9] = 17;
        p.extend_from_slice(b"PHOTON");
        assert_eq!(photon_offset(&p, 0), Some(24 + 8));
    }
}