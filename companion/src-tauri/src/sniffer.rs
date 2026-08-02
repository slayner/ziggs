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
use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Instant;
use std::sync::mpsc;
use tokio::sync::Mutex;
use serde::{Deserialize, Serialize};

use crate::photon_parser::{
    PhotonParser, PhotonValue, extract_player_state, extract_party, extract_loot,
    extract_new_character, extract_health, extract_market, extract_gold,
    extract_history_request, extract_history_response, LootEvent, DamageAcc, HistoryReq,
    extract_new_loot_owner, extract_new_loot_item, extract_attach_container,
    extract_detach_container, extract_inventory_move, self_loot_event,
};
use crate::aodp::{self, AodpBatch, AodpServer};

/// Cidades com marketplace real — só reportamos preço quando o jogador está
/// numa delas (o nome do mapa vira a cidade do report).
/// Inclui os 3 Rests (Arthur's/Merlyn's/Morgana's) que têm estações de craft
/// próprias e shareiam o Smuggler's Network. Smuggler's Den em si é mercado
/// mas não é local de craft.
const MARKET_CITIES: [&str; 12] = [
    "Martlock", "Bridgewatch", "Lymhurst", "Fort Sterling",
    "Thetford", "Caerleon", "Brecilien", "Black Market",
    "Arthur's Rest", "Merlyn's Rest", "Morgana's Rest",
    "Smuggler's Den",
];

/// Janela de dedup de pacote. `open_all` escuta TODAS as interfaces de propósito
/// (VPN/ExitLag/adaptador virtual — se ouvíssemos uma só, perderíamos o tráfego
/// quando o user liga VPN). O custo: num adaptador BRIDGEADO (Hyper-V/vEthernet)
/// o MESMO pacote é capturado em 2 interfaces e chega 2× no channel → loot E
/// dano duplicados. As 2 cópias chegam quase juntas, então uma janela curta
/// basta. Pacote Photon distinto NUNCA é byte-idêntico (cada um carrega seu
/// próprio sequence no header), então deduplicar por hash do payload descarta
/// SÓ a cópia, jamais um evento legítimo (dois loots iguais vêm em pacotes com
/// sequence diferente = hash diferente). Não filtramos interface — mantemos a
/// captura ampla e só ignoramos a cópia byte-a-byte.
const PKT_DEDUP_WINDOW: std::time::Duration = std::time::Duration::from_secs(2);

/// Registra o hash do pacote e diz se ele já foi visto dentro da `window` (=
/// cópia byte-idêntica de outra interface). Sempre grava `now` — refresca a
/// marca, então duplicação N-way (3+ interfaces) também é coberta.
fn packet_is_dup(
    recent: &mut HashMap<u64, Instant>,
    h: u64,
    now: Instant,
    window: std::time::Duration,
) -> bool {
    let dup = recent.get(&h).is_some_and(|&t| now.duration_since(t) < window);
    recent.insert(h, now);
    dup
}

/// Segunda rede de segurança contra loot duplicado, mais grosseira e mais
/// confiável que o dedup de bytes crus acima. O dedup de pacote (PKT_DEDUP_
/// WINDOW) parte da premissa de que as duas cópias do MESMO pacote (vindas de
/// 2 interfaces bridgeadas) são byte-idênticas — mas offset/checksum/padding
/// podem divergir sutilmente entre adaptadores, e aí o hash não bate e a
/// linha duplica mesmo assim (foi o caso relatado). Esta checagem ignora
/// bytes e compara a IDENTIDADE do evento (quem lootou, de quem, item,
/// quantidade) contra os últimos já no buffer — se um evento igual acabou de
/// entrar, é a mesma cópia chegando pela outra interface, não um segundo loot
/// genuíno (não dá pra lootar o mesmo corpo/item duas vezes na mesma janela).
/// Preguiçoso de propósito: não tenta consertar o dedup de bytes, só garante
/// que a linha não dobra no terminal/CSV.
const LOOT_DEDUP_LOOKBACK: usize = 8;

fn is_duplicate_loot(buf: &[LootEvent], ev: &LootEvent) -> bool {
    buf.iter().rev().take(LOOT_DEDUP_LOOKBACK).any(|p| {
        p.looted_by == ev.looted_by
            && p.looted_from == ev.looted_from
            && p.item_index == ev.item_index
            && p.quantity == ev.quantity
            && p.is_silver == ev.is_silver
    })
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SniffStats {
    pub running: bool,
    pub online: bool,
    pub packets_captured: u64,
    pub packets_parsed: u64,
    pub operations_extracted: u64,
    pub loot_count: u64,
    /// Dano total da sessão (soma do mapa `damage`). NÃO é mantido aqui:
    /// o hot loop não escreve este campo — `get_sniff_stats` (lib.rs) soma
    /// na leitura e preenche a cópia que vai pra UI (badge da aba Damage).
    #[serde(default)]
    pub damage_total: u64,
    /// Dano total causado PELO PRÓPRIO jogador (player_name). Somado na
    /// leitura por get_sniff_stats — alimenta o badge da aba Damage com o
    /// número que interessa ao usuário (o dele), não o da party toda.
    #[serde(default)]
    pub my_damage: u64,
    /// Estimativa ILUSTRATIVA do valor em prata dos loots capturados nesta
    /// sessão. Calculada por um worker de fundo no lib.rs (poll da rota
    /// /companion/lootlog/silver-estimate), NÃO no hot loop. Só badge da
    /// aba Lootlog — não é load-bearing em payout/reconcile nenhum.
    #[serde(default)]
    pub loot_silver_total: u64,
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
            damage_total: 0,
            my_damage: 0,
            loot_silver_total: 0,
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
    /// Mesmo acúmulo, mas só dos golpes cujo ALVO é jogador conhecido.
    ///
    /// Acumulador separado em vez de filtro na leitura porque o alvo não
    /// sobrevive ao `record`: o `DamageAcc` é indexado por causer e joga o
    /// target fora. Pra poder filtrar depois teria que guardar breakdown por
    /// alvo — muito mais memória do que manter dois totais.
    pub damage_vs_players: Arc<Mutex<HashMap<i64, DamageAcc>>>,
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
    generation: Arc<AtomicU64>,
}

enum CaptureMsg {
    Packet(usize, Vec<u8>),
    Dead(String),
}

impl Sniffer {
    pub fn new() -> Self {
        let loot = match crate::lootlog::load_session() {
            Ok(events) => events,
            Err(e) => {
                tracing::warn!("loot session não pôde ser carregada: {e:#}");
                Vec::new()
            }
        };
        let mut stats = SniffStats::default();
        stats.loot_count = loot.len() as u64;
        Self {
            stats: Arc::new(Mutex::new(stats)),
            loot: Arc::new(Mutex::new(loot)),
            debug: Arc::new(Mutex::new(Vec::new())),
            entities: Arc::new(Mutex::new(HashMap::new())),
            damage: Arc::new(Mutex::new(HashMap::new())),
            damage_vs_players: Arc::new(Mutex::new(HashMap::new())),
            prices: Arc::new(Mutex::new(Vec::new())),
            market_history: Arc::new(Mutex::new(Vec::new())),
            history_pending: Arc::new(Mutex::new(HashMap::new())),
            aodp_out: Arc::new(Mutex::new(Vec::new())),
            aodp_server: Arc::new(Mutex::new(None)),
            capture_loot: Arc::new(AtomicBool::new(false)),
            capture_damage: Arc::new(AtomicBool::new(false)),
            capture_prices: Arc::new(AtomicBool::new(false)),
            feed_aodp: Arc::new(AtomicBool::new(false)),
            generation: Arc::new(AtomicU64::new(0)),
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
            damage_vs_players: Arc::clone(&self.damage_vs_players),
            prices: Arc::clone(&self.prices),
            market_history: Arc::clone(&self.market_history),
            history_pending: Arc::clone(&self.history_pending),
            aodp_out: Arc::clone(&self.aodp_out),
            aodp_server: Arc::clone(&self.aodp_server),
            capture_loot: Arc::clone(&self.capture_loot),
            capture_damage: Arc::clone(&self.capture_damage),
            capture_prices: Arc::clone(&self.capture_prices),
            feed_aodp: Arc::clone(&self.feed_aodp),
            generation: Arc::clone(&self.generation),
        }
    }

    pub async fn stop(&self) {
        self.generation.fetch_add(1, Ordering::SeqCst);
        self.stats.lock().await.running = false;
    }

    pub fn prepare_start(&self) -> u64 {
        self.generation.fetch_add(1, Ordering::SeqCst) + 1
    }

    pub fn is_current(&self, generation: u64) -> bool {
        self.generation.load(Ordering::Acquire) == generation
    }

    /// Abre uma thread de captura por interface com IPv4 que ainda não está
    /// em `opened` (nome da interface), mandando os pacotes pro `tx` dado.
    /// Devolve quantas interfaces NOVAS foram abertas nesta chamada.
    ///
    /// Recebe `tx`/`opened` de fora (em vez de criar um channel próprio) pra
    /// poder ser chamada de novo mais tarde, na mesma sessão, quando uma
    /// interface aparece DEPOIS do boot — WiFi ainda associando com o AP,
    /// adaptador de VPN ligado depois, Hyper-V/Docker/VirtualBox trazendo uma
    /// interface virtual com IPv4 antes da rede real (autostart no Windows
    /// corre contra o DHCP). Sem isso, se QUALQUER interface abrisse primeiro
    /// — mesmo uma virtual que nunca vê tráfego do Albion — o sniffer nunca
    /// mais olhava a lista de novo, e ficava pra sempre "sem pacotes".
    async fn open_all(
        &self,
        devices: &[pcap::Device],
        tx: &mpsc::Sender<CaptureMsg>,
        opened: &mut HashSet<String>,
        generation: u64,
    ) -> usize {
        let mut opened_count = 0;
        for dev in devices {
            if dev.name.contains("lo") || dev.name.contains("Loopback") {
                continue;
            }
            if opened.contains(&dev.name) {
                continue;
            }
            if !dev.addresses.iter().any(|a| matches!(a.addr, std::net::IpAddr::V4(_))) {
                continue;
            }
            let desc = dev.name.clone();
            match open_device_capture(dev) {
                Ok(cap) => {
                    opened_count += 1;
                    opened.insert(dev.name.clone());
                    let l2 = l2_len_for(cap.get_datalink());
                    self.debug_log("info", &format!(
                        "Ouvindo interface: {} (L2={}b — {})",
                        desc, l2, if l2 == 0 { "IP puro / VPN" } else { "ethernet" }
                    )).await;
                    // Spawna uma thread dedicada pra cada capture (bloqueante).
                    let tx_clone = tx.clone();
                    let liveness = Arc::clone(&self.generation);
                    std::thread::spawn(move || {
                        let mut cap = cap;
                        while liveness.load(Ordering::Acquire) == generation {
                            match cap.next_packet() {
                                Ok(packet) => {
                                    let _ = tx_clone.send(CaptureMsg::Packet(l2, packet.data.to_vec()));
                                }
                                Err(pcap::Error::TimeoutExpired) => { /* sem pacote */ }
                                Err(e) => {
                                    let _ = tx_clone.send(CaptureMsg::Dead(desc.clone()));
                                    tracing::warn!("pcap erro em {}: {}", desc, e);
                                    break;
                                }
                            }
                        }
                    });
                }
                Err(e) => {
                    // NÃO marca como `opened` — a falha pode ser transitória
                    // (driver ainda subindo, interface ainda sem link) e é
                    // exatamente esse caso que o re-scan existe pra cobrir.
                    self.debug_log("warn", &format!("Não foi possível abrir {}: {}", desc, e)).await;
                }
            }
        }
        if opened_count > 0 {
            self.debug_log("info", &format!(
                "{} interface(s) nova(s) ativa(s).", opened_count
            )).await;
        }
        opened_count
    }

    /// Loop principal: abre listeners em todas as interfaces, captura pacotes.
    ///
    /// A captura pcap é bloqueante (pcap_next_ex trava a thread). Como estamos
    /// num runtime tokio, movemos a captura pra threads dedicadas (spawn_blocking)
    /// que mandam pacotes por um channel std::mpsc. O task async processa os
    /// pacotes recebidos sem bloquear o executor.
    pub async fn run(&self) {
        let generation = self.prepare_start();
        self.run_generation(generation).await;
    }

    pub async fn run_generation(&self, generation: u64) {
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

        // O channel vive pela sessão inteira: `open_all` é chamado de novo
        // periodicamente (ver `last_iface_scan` mais abaixo) pra pegar
        // interfaces que aparecem DEPOIS do boot, reusando o mesmo `tx`.
        let (tx, rx) = mpsc::channel::<CaptureMsg>();
        let mut opened_ifaces: HashSet<String> = HashSet::new();

        // Enumerar/abrir interface pode falhar quando o companion sobe junto com
        // o Windows (autostart): o serviço do Npcap e os adaptadores de rede
        // ainda não estão prontos. Antes disso derrubava o sniffer de vez e o
        // Albion nunca era detectado na sessão inteira — agora tenta de novo.
        loop {
            if self.generation.load(Ordering::Acquire) != generation {
                return;
            }
            let devices = match pcap::Device::list() {
                Ok(d) => d,
                Err(e) => {
                    let msg = format!("pcap Device::list falhou: {}. Npcap instalado?", e);
                    self.debug_log("err", &msg).await;
                    self.stats.lock().await.error = Some(msg);
                    tokio::time::sleep(std::time::Duration::from_secs(15)).await;
                    continue;
                }
            };
            let n = self.open_all(&devices, &tx, &mut opened_ifaces, generation).await;
            if n > 0 {
                break;
            }
            let msg = "Nenhuma interface de rede pôde ser aberta. Precisa admin/Npcap?".to_string();
            self.debug_log("err", &format!("{} Tentando de novo em 15s…", msg)).await;
            self.stats.lock().await.error = Some(msg);
            tokio::time::sleep(std::time::Duration::from_secs(15)).await;
        }
        self.stats.lock().await.error = None;

        let mut parser = PhotonParser::new();
        let mut last_packet_time = Instant::now();
        let mut was_online = false;
        let mut logged_health: u32 = 0;
        let mut logged_char: u32 = 0;
        let mut logged_loot: u32 = 0;
        let mut local_player_name = String::new();
        let mut seen_codes: HashSet<i16> = HashSet::new();
        // Estado do self-loot (ver photon_parser.rs) — igual a `entities`, só
        // cresce durante a sessão; limpo pontualmente no detach/no consumo do
        // slot lootado, resto é resíduo aceitável (ver doutrina de `entities`).
        let mut loot_container_owner: HashMap<i64, String> = HashMap::new();
        let mut loot_container_uuid: HashMap<[u8; 16], i64> = HashMap::new();
        let mut loot_container_slots: HashMap<(i64, i32), i64> = HashMap::new();
        let mut loot_objects: HashMap<i64, (i32, i32)> = HashMap::new();
        let mut last_heartbeat = Instant::now();
        let mut last_iface_scan = Instant::now();
        let mut raw_pkts: u64 = 0;
        // Dedup de pacote (ver PKT_DEDUP_WINDOW): hash do payload Photon → última
        // vez visto. Poda amortizada a cada 500 pacotes pra não crescer.
        let mut recent_pkts: HashMap<u64, Instant> = HashMap::new();
        let mut pkt_since_prune: u32 = 0;

        loop {
            if self.generation.load(Ordering::Acquire) != generation {
                self.debug_log("info", "Sniffer parado.").await;
                break;
            }

            // Recebe pacotes do canal (non-blocking — se não tem pacote, continua).
            match rx.recv_timeout(std::time::Duration::from_millis(50)) {
                Ok(CaptureMsg::Dead(name)) => {
                    opened_ifaces.remove(&name);
                    self.debug_log("warn", &format!("Captura fechou em {name}; interface será reaberta.")).await;
                    last_iface_scan = Instant::now() - std::time::Duration::from_secs(60);
                }
                Ok(CaptureMsg::Packet(l2_hint, data)) => {
                    raw_pkts += 1;
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

                    // Dedup: cópia byte-idêntica de outra interface (adaptador
                    // bridgeado — ver PKT_DEDUP_WINDOW) → pula, senão loot/dano
                    // duplica. Pula ANTES do parse (também economiza o parse).
                    {
                        let mut hasher = DefaultHasher::new();
                        photon_data.hash(&mut hasher);
                        let h = hasher.finish();
                        let now2 = Instant::now();
                        if packet_is_dup(&mut recent_pkts, h, now2, PKT_DEDUP_WINDOW) {
                            continue;
                        }
                        pkt_since_prune += 1;
                        if pkt_since_prune >= 500 {
                            pkt_since_prune = 0;
                            recent_pkts.retain(|_, t| now2.duration_since(*t) < PKT_DEDUP_WINDOW);
                        }
                    }

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
                        // isto revela qual opcode carrega nome/mapa/guild de verdade, e também
                        // qualquer opcode de self-loot que o extract_loot não cobre (só
                        // OtherGrabbedLoot é parsado hoje).
                        if op.albion_code >= 0 && seen_codes.insert(op.albion_code) && seen_codes.len() <= 200 {
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
                            if !state.player_name.is_empty() {
                                s.player_name = state.player_name.clone();
                                local_player_name = state.player_name.clone();
                            }
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
                            // Dump de diagnóstico de loot — os 20 primeiros
                            // eventos com ESTRUTURA *quase* loot-like: ao menos
                            // 2 strings (@1, @2) e 1 int (@4 ou @5), mesmo que
                            // `extract_loot` os aceite OU descarte. Pega também
                            // self-loot se vier com params em ordem diferente.
                            //
                            // Mesma motivação do [CALIB] do damage meter: sem
                            // evidência real do pacote, qualquer fix é chute.
                            let l_from = op.parameters.get(&1).and_then(|v| v.as_string()).unwrap_or("").to_string();
                            let l_by = op.parameters.get(&2).and_then(|v| v.as_string()).unwrap_or("").to_string();
                            let i4 = op.parameters.get(&4).and_then(|v| v.as_i64()).is_some();
                            let i5 = op.parameters.get(&5).and_then(|v| v.as_i64()).is_some();
                            // GVG_SEASON_xx crest events (op 388: SCHEMA_xx/GUILDSYMBOL_xx)
                            // têm a MESMA estrutura (2 strings + int) e disparam
                            // dezenas de vezes por minuto — sem excluir, eles sozinhos
                            // enchem o teto de 20 antes de qualquer loot real aparecer.
                            let is_gvg_noise = l_from.starts_with("SCHEMA_") || l_by.starts_with("GUILDSYMBOL_");
                            let looks_like_loot = op.message_type == 4 && !l_from.is_empty() && !l_by.is_empty()
                                && (i4 || i5) && !is_gvg_noise;
                            if looks_like_loot && logged_loot < 20 {
                                logged_loot += 1;
                                let params = dump_params(op);
                                let item_idx = op.parameters.get(&4).and_then(|v| v.as_i64()).unwrap_or(0);
                                let qty = op.parameters.get(&5).and_then(|v| v.as_i64()).unwrap_or(0);
                                self.debug_log("info", &format!(
                                    "[LOOT {:02}] op={} from={:?} by={:?} item={} qty={} | {}",
                                    logged_loot, op.albion_code, l_from, l_by, item_idx, qty, params
                                )).await;
                            }
                            if let Some(loot) = extract_loot(op) {
                                self.push_loot(loot).await;
                            }
                            // Self-loot: `extract_loot`/OtherGrabbedLoot só cobre loot
                            // ALHEIO — o servidor não ecoa o broadcast de volta pra
                            // quem lootou. Confirmado contra o ao-loot-logger
                            // (madvac/ao-loot-logger): a única forma de detectar o
                            // PRÓPRIO loot é acompanhar o request que o cliente manda
                            // ao mover o item do loot bag pra mochila, cruzado com o
                            // conteúdo daquele loot bag (visto em eventos anteriores).
                            // Ver doc grande em extract_new_loot_owner (photon_parser.rs).
                            if let Some((id, owner)) = extract_new_loot_owner(op) {
                                loot_container_owner.insert(id, owner);
                            }
                            if let Some((object_id, item_index, quantity)) = extract_new_loot_item(op) {
                                loot_objects.insert(object_id, (item_index, quantity));
                            }
                            if let Some((id, uuid, slots)) = extract_attach_container(op) {
                                loot_container_uuid.insert(uuid, id);
                                for (slot, object_id) in slots.iter().enumerate() {
                                    if *object_id != 0 {
                                        loot_container_slots.insert((id, slot as i32), *object_id);
                                    }
                                }
                            }
                            if let Some(uuid) = extract_detach_container(op) {
                                loot_container_uuid.remove(&uuid);
                            }
                            if let Some(mv) = extract_inventory_move(op) {
                                if !local_player_name.is_empty() {
                                    if let Some(&container_id) = loot_container_uuid.get(&mv.from_uuid) {
                                        if let Some(object_id) = loot_container_slots.remove(&(container_id, mv.from_slot)) {
                                            if let Some((item_index, quantity)) = loot_objects.remove(&object_id) {
                                                if let Some(owner) = loot_container_owner.get(&container_id) {
                                                    let loot = self_loot_event(
                                                        local_player_name.clone(), owner.clone(), item_index, quantity,
                                                    );
                                                    self.push_loot(loot).await;
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // Damage meter: registro id→nome + acúmulo de dano/cura.
                        // Registro de entidades roda sempre (barato, e o meter
                        // precisa dos nomes vistos ANTES de ligar o toggle).
                        if let Some((id, name)) = extract_new_character(op) {
                            // Dump dos 3 primeiros NewCharacter com TODOS os
                            // params e o CONTEÚDO dos arrays.
                            //
                            // Hoje só lemos id (0) e nome (1). O equipamento do
                            // jogador vem neste mesmo evento — é o que falta pra
                            // mostrar o render da arma com tier/encantamento no
                            // ranking. Procuramos um array de inteiros que
                            // pareça índices de item (os mesmos do lootlog).
                            if logged_char < 3 {
                                logged_char += 1;
                                let mut ps: Vec<(u8, String)> = op.parameters
                                    .iter().map(|(k, v)| (*k, deep(v))).collect();
                                ps.sort_by_key(|(k, _)| *k);
                                let params = ps.iter()
                                    .map(|(k, v)| format!("{k}={v}"))
                                    .collect::<Vec<_>>().join("  ");
                                self.debug_log("info", &format!(
                                    "[CHAR {}] {} | {}", logged_char, name, params
                                )).await;
                            }
                            self.entities.lock().await.insert(id, name);
                        }
                        if self.capture_damage.load(Ordering::Relaxed) {
                            if let Some(h) = extract_health(op) {
                                // Dump de calibração: os primeiros eventos de
                                // dano com TODOS os params e seus valores.
                                //
                                // Antes logava só as CHAVES, uma vez — o que
                                // não ajuda quando a suspeita é justamente que
                                // o param do feitiço mudou de lugar no patch.
                                // Com os valores dá pra ver qual param varia
                                // por skill e em que faixa, e comparar com o
                                // que a tabela diz.
                                if h.change < 0.0 && logged_health < 15 {
                                    logged_health += 1;
                                    let mut ps: Vec<(u8, String)> = op.parameters
                                        .iter().map(|(k, v)| (*k, brief(v))).collect();
                                    ps.sort_by_key(|(k, _)| *k);
                                    let params = ps.iter()
                                        .map(|(k, v)| format!("{k}={v}"))
                                        .collect::<Vec<_>>().join("  ");
                                    let who = self.entities.lock().await
                                        .get(&h.causer_id).cloned()
                                        .unwrap_or_else(|| format!("id{}", h.causer_id));
                                    self.debug_log("info", &format!(
                                        "[CALIB {:02}] {} dano={:.0} spell={} | {}",
                                        logged_health, who, -h.change, h.spell_id, params
                                    )).await;
                                }
                                // Só dano (change < 0). Cura é descartada de
                                // propósito — o painel é de dano, só.
                                //
                                // spell 0 fica de fora: é o dano sem feitiço
                                // atribuído (o que aparece quando alguém dá
                                // /die), não dano causado por ninguém. Ficava
                                // creditado ao índice 0 da tabela e aparecia
                                // como "Trudge". Descartado no ACÚMULO, não só
                                // na exibição — se entrasse no total, a % das
                                // outras skills sairia errada.
                                if h.change < 0.0 && h.spell_id != 0 {
                                    let now = std::time::SystemTime::now()
                                        .duration_since(std::time::UNIX_EPOCH)
                                        .map(|d| d.as_secs())
                                        .unwrap_or(0);
                                    // Alvo conhecido em `entities` = jogador.
                                    // É o mesmo critério que o meter já usa
                                    // pra decidir quais LINHAS são jogador
                                    // (NewCharacter só vem de player); mob não
                                    // entra no mapa e por isso não conta aqui.
                                    // Guard solto antes de travar `damage` —
                                    // mesma ordem do get_damage_meter.
                                    let target_is_player =
                                        self.entities.lock().await.contains_key(&h.target_id);
                                    let mut dmg = self.damage.lock().await;
                                    dmg.entry(h.causer_id).or_default()
                                        .record(h.spell_id, -h.change, now);
                                    drop(dmg);
                                    if target_is_player {
                                        self.damage_vs_players.lock().await
                                            .entry(h.causer_id).or_default()
                                            .record(h.spell_id, -h.change, now);
                                    }
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
                                            // Converte UniqueName (ItemTypeId do jogo)
                                            // → game_name (nome em inglês do jogo) que é
                                            // o ID canônico do nosso banco de preços.
                                            let game_name = crate::to_game_name(&o.item_id).await;
                                            buf.push(serde_json::json!({
                                                "item_id": game_name,
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
                                    // Aceita clusters numéricos (cidades reais,
                                    // Rests) E BLACKBANK-* (Smuggler's Den) — o
                                    // AODP client oficial aceita ambos.
                                    let is_valid_loc = !numeric_loc.is_empty() && (
                                        numeric_loc.chars().all(|c| c.is_ascii_digit()) ||
                                        numeric_loc.starts_with("BLACKBANK-")
                                    );
                                    if let Some(server) = server {
                                        if is_valid_loc {
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
                                                "AODP: local inválido ({}), pulando envio", map_name
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
            // Só pro arquivo de diagnóstico — não aparece no terminal da UI
            // (polui a tela do usuário sem informar nada acionável).
            if last_heartbeat.elapsed().as_secs() >= 10 {
                last_heartbeat = Instant::now();
                let s = self.stats.lock().await;
                self.debug_log_file("info", &format!(
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

            // Re-varredura de interfaces: pega adaptador que subiu DEPOIS do
            // boot (WiFi ainda associando, VPN ligada mais tarde) — sem isso,
            // uma interface virtual que abriu primeiro (Hyper-V, Docker,
            // VirtualBox) travava o sniffer nela pra sempre, mesmo com a placa
            // de rede real disponível alguns segundos depois.
            //
            // Cadência ADAPTATIVA: 30s enquanto ONLINE (só vigia o raro
            // adaptador novo), mas 5s enquanto OFFLINE. Ficar offline no meio
            // da sessão quase sempre é MUDANÇA DE ROTA — o usuário ligou uma
            // VPN (Cloudflare WARP, etc.) e o tráfego do Albion migrou pra uma
            // interface WinTun nova que ainda não estamos ouvindo. Rescan
            // rápido reengancha assim que esse adaptador ganha IPv4, em vez de
            // deixar o companion cego por até 30s (o WinTun às vezes demora
            // alguns segundos pra receber o IP, então UM rescan não basta —
            // tem que insistir). Offline = jogo fechado ou idle, então varrer
            // mais é de graça (ver doutrina de custo no CLAUDE.md).
            let rescan_secs = if was_online { 30 } else { 5 };
            if last_iface_scan.elapsed().as_secs() >= rescan_secs {
                last_iface_scan = Instant::now();
                if let Ok(devices) = pcap::Device::list() {
                    self.open_all(&devices, &tx, &mut opened_ifaces, generation).await;
                }
            }
        }
    }

    async fn push_loot(&self, loot: LootEvent) {
        let (len, save_error) = {
            let mut buf = self.loot.lock().await;
            if is_duplicate_loot(&buf, &loot) {
                return;
            }
            buf.push(loot);
            (buf.len(), crate::lootlog::save_session(&buf).err())
        };
        self.stats.lock().await.loot_count = len as u64;
        if let Some(e) = save_error {
            self.debug_log("err", &format!("Falha ao persistir loot da sessão: {e}")).await;
        }
    }

    async fn debug_log(&self, level: &str, msg: &str) {
        self.debug_log_inner(level, msg, true).await;
    }

    /// Só pro arquivo de diagnóstico — não aparece no terminal da UI. Pra
    /// heartbeats periódicos (stats: raw=… pkts=…) que poluem a tela do
    /// usuário mas ainda valem pra debug em disco.
    async fn debug_log_file(&self, level: &str, msg: &str) {
        self.debug_log_inner(level, msg, false).await;
    }

    async fn debug_log_inner(&self, level: &str, msg: &str, to_ui: bool) {
        let ts = crate::photon_parser::now_iso_utc();
        if let Some(p) = debug_log_path() {
            use std::io::Write;
            if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&p) {
                let _ = writeln!(f, "{} [{}] {}", ts, level.to_uppercase(), msg);
            }
        }
        if to_ui {
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
}

/// Caminho do arquivo de log de diagnóstico.
/// Valor de parâmetro Photon em uma linha, pro dump de calibração.
/// Coleção sai só com o tamanho: o que interessa aqui são os escalares.
fn brief(v: &PhotonValue) -> String {
    match v {
        PhotonValue::Bool(b) => b.to_string(),
        PhotonValue::Byte(n) => n.to_string(),
        PhotonValue::Short(n) => n.to_string(),
        PhotonValue::Int(n) => n.to_string(),
        PhotonValue::Long(n) => n.to_string(),
        PhotonValue::Float(n) => format!("{n:.1}"),
        PhotonValue::Double(n) => format!("{n:.1}"),
        PhotonValue::String(s) => format!("{s:?}"),
        PhotonValue::Bytes(b) => format!("bytes[{}]", b.len()),
        PhotonValue::Array(a) => format!("arr[{}]", a.len()),
        PhotonValue::Dictionary(d) => format!("dict[{}]", d.len()),
        PhotonValue::Null => "null".into(),
    }
}

/// Como `brief`, mas ABRE os arrays (até 16 itens).
///
/// O equipamento vem como array de índices de item, e `arr[13]` não diz nada —
/// os números é que revelam quais são as peças.
fn deep(v: &PhotonValue) -> String {
    match v {
        PhotonValue::Array(a) => {
            let head: Vec<String> = a.iter().take(16).map(brief).collect();
            let reticencias = if a.len() > 16 { ", …" } else { "" };
            format!("[{}{}]", head.join(", "), reticencias)
        }
        other => brief(other),
    }
}

fn debug_log_path() -> Option<std::path::PathBuf> {
    dirs::document_dir().map(|d| d.join("ziggs-companion").join("companion-debug.log"))
}

/// Abre uma capture numa device específica com filtro BPF das 3 portas do Albion.
fn open_device_capture(dev: &pcap::Device) -> Result<pcap::Capture<pcap::Active>, String> {
    let builder = pcap::Capture::from_device(dev.clone())
        .map_err(|e| format!("from_device: {}", e))?;
    let builder = builder.promisc(true).immediate_mode(true).timeout(500);
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

// ── Npcap DLL path fix ─────────────────────────────────────────────────────
// Npcap moderno (sem "WinPcap API-compatible Mode") instala wpcap.dll/Packet.dll
// em C:\Windows\System32\Npcap\ e depende do PATH do processo pra achar. O crate
// `pcap` chama LoadLibrary("wpcap.dll") — se o PATH não inclui o subdir, falha
// com "wpcap.dll not found" mesmo o Npcap estando instalado. Isto roda UMA vez
// no startup: lê o InstallDir do registry, adiciona ao PATH do processo e chama
// SetDllDirectoryW pro mesmo dir. Se o Npcap NÃO está instalado de jeito nenhum,
// não faz nada aqui — instalação é MANUAL de propósito (ver nota grande no
// CLAUDE.md sobre Npcap): o instalador free ABORTA com `/S` ("silent
// installation is only supported in Npcap OEM"), e essa função já rodou
// tentando isso síncrono no thread principal, ANTES da janela abrir — quando
// o installer travava esperando o usuário clicar um dialog que não tinha
// janela nenhuma atrás pra explicar o quê, o app parecia simplesmente
// travado no boot pra quem não tinha Npcap ainda (todo usuário novo). O
// sniffer detecta a ausência sozinho (`Device::list` falha) e o banner
// `.ck-npcap` na UI manda pro download manual. No-op em não-Windows.
#[cfg(target_os = "windows")]
pub fn ensure_npcap_dll_path() {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::System::LibraryLoader::SetDllDirectoryW;
    use windows_sys::Win32::System::Registry::{
        RegCloseKey, RegOpenKeyExW, RegQueryValueExW, HKEY_LOCAL_MACHINE, KEY_READ,
    };

    // Npcap grava InstallDir + Version em HKLM\SOFTWARE\WOW6432Node\Npcap
    // (64-bit) ou HKLM\SOFTWARE\Npcap (32-bit). Tentamos os dois.
    let mut dir: Option<std::path::PathBuf> = None;
    let mut version: Option<String> = None;
    for subkey in ["SOFTWARE\\WOW6432Node\\Npcap", "SOFTWARE\\Npcap"] {
        let mut hkey = 0isize;
        let subkey_w: Vec<u16> = subkey.encode_utf16().chain(std::iter::once(0)).collect();
        if unsafe { RegOpenKeyExW(HKEY_LOCAL_MACHINE, subkey_w.as_ptr(), 0, KEY_READ, &mut hkey) } != 0 {
            continue;
        }
        // InstallDir
        let mut len = 512u32;
        let mut buf = vec![0u16; (len as usize / 2) + 1];
        let valname: Vec<u16> = "InstallDir\0".encode_utf16().collect();
        let mut ty = 0u32;
        if unsafe { RegQueryValueExW(hkey, valname.as_ptr(), std::ptr::null_mut(), &mut ty, buf.as_mut_ptr() as *mut u8, &mut len) } == 0 && ty == 1 {
            let nul = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
            let s = String::from_utf16_lossy(&buf[..nul]);
            if !s.is_empty() { dir = Some(std::path::PathBuf::from(s)); }
        }
        // Version (opcional — só informativo por enquanto)
        let mut len = 64u32;
        let mut buf = vec![0u16; (len as usize / 2) + 1];
        let valname: Vec<u16> = "Version\0".encode_utf16().collect();
        let mut ty = 0u32;
        if unsafe { RegQueryValueExW(hkey, valname.as_ptr(), std::ptr::null_mut(), &mut ty, buf.as_mut_ptr() as *mut u8, &mut len) } == 0 && ty == 1 {
            let nul = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
            let s = String::from_utf16_lossy(&buf[..nul]);
            if !s.is_empty() { version = Some(s); }
        }
        unsafe { RegCloseKey(hkey); }
        if dir.is_some() { break; }
    }
    let _ = version; // reservado pra futuro (alertar versão antiga, etc.)

    // Sem Npcap de jeito nenhum: nada a fazer aqui, o sniffer vai reportar o
    // erro e a UI mostra o banner de download manual (ver comentário acima).
    let dir = match dir {
        Some(d) => d,
        None => return,
    };
    // Já em System32? LoadLibrary acha sozinho, no-op.
    let sys32 = std::env::var_os("SystemRoot")
        .map(|r| std::path::PathBuf::from(r).join("System32"))
        .unwrap_or_default();
    if dir == sys32 { return; }

    // Adiciona ao PATH do processo (vale pra LoadLibrary search order).
    if let Some(cur) = std::env::var_os("PATH") {
        let mut parts: Vec<std::path::PathBuf> = std::env::split_paths(&cur).collect();
        if !parts.contains(&dir) {
            parts.push(dir.clone());
            if let Ok(joined) = std::env::join_paths(parts) {
                std::env::set_var("PATH", joined);
            }
        }
    }
    // SetDllDirectoryW: adicional ao PATH pro loader achar wpcap.dll mesmo
    // sem estar no PATH do sistema. Inofensivo se já está em System32.
    let wide: Vec<u16> = dir.as_os_str().encode_wide().chain(std::iter::once(0)).collect();
    unsafe { SetDllDirectoryW(wide.as_ptr()); }
}

#[cfg(not(target_os = "windows"))]
pub fn ensure_npcap_dll_path() {}

/// Só checa se o Npcap está instalado (a chave do registry existe), sem
/// mexer em PATH/SetDllDirectory — usado pra decidir se vale registrar
/// autostart (ver `set_autostart` em lib.rs). Baixo custo: só abre e fecha
/// a chave, não lê valores.
#[cfg(target_os = "windows")]
pub fn npcap_installed() -> bool {
    use windows_sys::Win32::System::Registry::{
        RegCloseKey, RegOpenKeyExW, HKEY_LOCAL_MACHINE, KEY_READ,
    };
    for subkey in ["SOFTWARE\\WOW6432Node\\Npcap", "SOFTWARE\\Npcap"] {
        let mut hkey = 0isize;
        let subkey_w: Vec<u16> = subkey.encode_utf16().chain(std::iter::once(0)).collect();
        if unsafe { RegOpenKeyExW(HKEY_LOCAL_MACHINE, subkey_w.as_ptr(), 0, KEY_READ, &mut hkey) } == 0 {
            unsafe { RegCloseKey(hkey); }
            return true;
        }
    }
    false
}

#[cfg(not(target_os = "windows"))]
pub fn npcap_installed() -> bool { true }

#[cfg(test)]
mod tests {
    use super::photon_offset;
    use super::{is_duplicate_loot, LootEvent, LOOT_DEDUP_LOOKBACK};

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

    #[test]
    fn packet_dedup_window() {
        use super::packet_is_dup;
        use std::collections::HashMap;
        use std::time::{Duration, Instant};
        let mut recent = HashMap::new();
        let win = Duration::from_secs(2);
        let t0 = Instant::now();
        // 1ª vez: não é dup.
        assert!(!packet_is_dup(&mut recent, 42, t0, win));
        // Mesma hash logo depois (cópia de outra interface): É dup.
        assert!(packet_is_dup(&mut recent, 42, t0 + Duration::from_millis(5), win));
        // Hash diferente (dois loots iguais vêm com sequence diferente): não é dup.
        assert!(!packet_is_dup(&mut recent, 99, t0 + Duration::from_millis(6), win));
        // Mesma hash fora da janela: não é cópia (pacote legítimo reusa hash só
        // após reconexão/reset de sequence, muito depois) → não é dup.
        assert!(!packet_is_dup(&mut recent, 42, t0 + Duration::from_secs(3), win));
    }

    fn loot_ev(by: &str, from: &str, idx: i32, qty: i32) -> LootEvent {
        LootEvent {
            ts: "2026-07-23T12:00:00Z".into(),
            looted_by: by.into(), looted_from: from.into(),
            item_index: idx, quantity: qty, is_silver: false,
        }
    }

    #[test]
    fn loot_dedup_pega_evento_identico_vindo_de_2_interfaces() {
        let mut buf = vec![loot_ev("Zezinho", "Fulano", 2958, 3)];
        // Mesma identidade (mesma cópia chegando pela outra interface) → dup.
        assert!(is_duplicate_loot(&buf, &loot_ev("Zezinho", "Fulano", 2958, 3)));
        buf.push(loot_ev("Zezinho", "Fulano", 2958, 3));

        // Item diferente do mesmo corpo, mesmo segundo → não é dup.
        assert!(!is_duplicate_loot(&buf, &loot_ev("Zezinho", "Fulano", 1001, 3)));
        // Quantidade diferente → não é dup.
        assert!(!is_duplicate_loot(&buf, &loot_ev("Zezinho", "Fulano", 2958, 5)));
        // Looter diferente → não é dup.
        assert!(!is_duplicate_loot(&buf, &loot_ev("Outro", "Fulano", 2958, 3)));
    }

    #[test]
    fn loot_dedup_nao_enxerga_alem_do_lookback() {
        // Evento fora da janela de lookback (mais de LOOT_DEDUP_LOOKBACK
        // eventos distintos no meio) não é considerado dup — evita falso
        // positivo quando o mesmo drop acontece de novo bem mais tarde.
        let mut buf = vec![loot_ev("A", "B", 1, 1)];
        for i in 0..LOOT_DEDUP_LOOKBACK {
            buf.push(loot_ev("X", "Y", 100 + i as i32, 1));
        }
        assert!(!is_duplicate_loot(&buf, &loot_ev("A", "B", 1, 1)));
    }
}
