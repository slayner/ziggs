// Packet sniffer: captures Albion UDP packets via libpcap/Npcap.
//
// Listens on ALL IPv4 interfaces (VPN/ExitLag creates virtual interfaces —
// listening on only one means losing traffic when the user enables VPN).
// BPF filter: "udp and (port 5056 or port 5055 or port 4535)" — the 3 ports
// the game uses.
//
// Each packet is passed to the PhotonParser. Loot events (opcode 256) go to
// the loot buffer. Debug logs go to the debug buffer (shown in the UI
// terminal). Online/offline detection: no packets for 5s = offline.
//
// Requires Npcap on Windows. Needs admin.

use serde::{Deserialize, Serialize};
use std::collections::hash_map::DefaultHasher;
use std::collections::{HashMap, HashSet};
use std::hash::{Hash, Hasher};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc;
use std::sync::Arc;
use std::time::Instant;
use tokio::sync::Mutex;

use crate::aodp::{self, AodpBatch, AodpServer};
use crate::photon_parser::{
    extract_attach_container, extract_detach_container, extract_gold, extract_health,
    extract_history_request, extract_history_response, extract_inventory_move, extract_loot,
    extract_market, extract_new_character, extract_new_loot_item, extract_new_loot_owner,
    extract_party, extract_player_state, self_loot_event, DamageAcc, HistoryReq, LootEvent,
    PhotonParser, PhotonValue,
};

/// Cities with real marketplaces — we only report prices when the player is
/// in one of these. Includes the 3 Rests (Arthur's/Merlyn's/Morgana's) which
/// have their own crafting stations and share the Smuggler's Network.
const MARKET_CITIES: [&str; 12] = [
    "Martlock",
    "Bridgewatch",
    "Lymhurst",
    "Fort Sterling",
    "Thetford",
    "Caerleon",
    "Brecilien",
    "Black Market",
    "Arthur's Rest",
    "Merlyn's Rest",
    "Morgana's Rest",
    "Smuggler's Den",
];

/// Packet dedup window. `open_all` listens on ALL interfaces on purpose
/// (VPN/ExitLag/virtual adapters — listening on only one would lose traffic
/// when the user enables VPN). The cost: on a BRIDGED adapter (Hyper-V/vEthernet)
/// the SAME packet is captured on 2 interfaces and arrives 2× on the channel
/// → duplicated loot/damage. The 2 copies arrive nearly simultaneously, so a
/// short window suffices. Distinct Photon packets are never byte-identical
/// (each carries its own sequence number in the header), so dedup by payload
/// hash only discards the copy, never a legitimate event (two identical loots
/// come in packets with different sequences = different hashes).
const PKT_DEDUP_WINDOW: std::time::Duration = std::time::Duration::from_secs(2);

/// Records the packet hash and returns whether it was seen within `window`.
/// Always writes `now` — refreshes the timestamp so N-way duplication (3+
/// interfaces) is also covered.
fn packet_is_dup(
    recent: &mut HashMap<u64, Instant>,
    h: u64,
    now: Instant,
    window: std::time::Duration,
) -> bool {
    let dup = recent
        .get(&h)
        .is_some_and(|&t| now.duration_since(t) < window);
    recent.insert(h, now);
    dup
}

/// Second safety net against duplicate loot. The byte-level dedup
/// (PKT_DEDUP_WINDOW) assumes copies from bridged adapters are byte-identical,
/// but offset/checksum/padding can diverge between adapters, making the hash
/// miss and the line still duplicate. This check compares event identity
/// (who looted, from whom, item, quantity) against the last entries in the
/// buffer — if an identical event just arrived, it's the same copy from
/// another interface, not a genuine second loot (you can't loot the same
/// body/item twice in the same window). Lazy on purpose: doesn't try to fix the
/// byte dedup, just ensures the line doesn't double in the terminal/CSV.
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
    /// Total session damage (sum of the `damage` map). Not maintained here:
    /// the hot loop does not write this field — `get_sniff_stats` (lib.rs)
    /// sums on read and fills the copy sent to the UI (Damage tab badge).
    #[serde(default)]
    pub damage_total: u64,
    /// Total damage dealt by the local player (player_name). Summed on read
    /// by get_sniff_stats — feeds the Damage tab badge with the user's own
    /// number, not the whole party's.
    #[serde(default)]
    pub my_damage: u64,
    /// Illustrative estimate of silver value of loots captured this session.
    /// Computed by a background worker in lib.rs (polling
    /// /companion/lootlog/silver-estimate), NOT in the hot loop. Lootlog tab
    /// badge only — not load-bearing for payout/reconcile.
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

/// Debug line from sniffer — shown in the UI terminal.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DebugLine {
    pub ts: String,
    pub level: String, // "info" | "warn" | "err"
    pub msg: String,
}

pub struct Sniffer {
    pub stats: Arc<Mutex<SniffStats>>,
    pub loot: Arc<Mutex<Vec<LootEvent>>>,
    pub debug: Arc<Mutex<Vec<DebugLine>>>,
    /// Entity ID → name mapping (NewCharacter), used to resolve the damage meter.
    pub entities: Arc<Mutex<HashMap<i64, String>>>,
    /// Damage/healing accumulated by causer_id this session.
    pub damage: Arc<Mutex<HashMap<i64, DamageAcc>>>,
    /// Same accumulation, but only for hits whose TARGET is a known player.
    /// Separate accumulator instead of read-time filter because the target
    /// doesn't survive `record`: DamageAcc is indexed by causer and discards
    /// target_id. Filtering after would require per-target breakdown — much
    /// more memory than keeping two totals.
    pub damage_vs_players: Arc<Mutex<HashMap<i64, DamageAcc>>>,
    /// Price rows ready for POST /companion/prices/submit — drained
    /// periodically by the upload task in lib.rs.
    pub prices: Arc<Mutex<Vec<serde_json::Value>>>,
    /// Market history rows ready for POST /companion/market-history/submit.
    pub market_history: Arc<Mutex<Vec<serde_json::Value>>>,
    /// Pending history requests (message_id → info), awaiting response.
    history_pending: Arc<Mutex<HashMap<u64, HistoryReq>>>,
    /// Market order batches ready for AODP upload (verbatim).
    pub aodp_out: Arc<Mutex<Vec<AodpBatch>>>,
    /// Last AODP region inferred from the Albion server IP (per packet).
    pub aodp_server: Arc<Mutex<Option<AodpServer>>>,
    /// Capture gates — mirror the config toggles (set_config syncs them).
    /// The sniffer always runs (name/map/party feed the UI), but only
    /// accumulates loot/damage/prices when the gate is on.
    pub capture_loot: Arc<AtomicBool>,
    pub capture_damage: Arc<AtomicBool>,
    pub capture_prices: Arc<AtomicBool>,
    /// Forward market orders to AODP (return data to the community).
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

    /// Clones sharing ALL Arcs (including shutdown — stop() on the original
    /// stops the spawned task).
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

    /// Opens a capture thread per IPv4 interface not yet in `opened`, sending
    /// packets to the given `tx`. Returns the count of NEW interfaces opened.
    ///
    /// Takes `tx`/`opened` from outside (instead of creating its own channel)
    /// so it can be called again later in the same session when an interface
    /// appears AFTER boot — WiFi still associating, VPN adapter enabled later,
    /// Hyper-V/Docker/VirtualBox bringing up a virtual interface with IPv4
    /// before the real network (autostart on Windows races DHCP). Without this,
    /// if ANY interface opened first — even a virtual one that never sees
    /// Albion traffic — the sniffer would never scan the list again and stay
    /// "no packets" forever.
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
            if !dev
                .addresses
                .iter()
                .any(|a| matches!(a.addr, std::net::IpAddr::V4(_)))
            {
                continue;
            }
            let desc = dev.name.clone();
            match open_device_capture(dev) {
                Ok(cap) => {
                    opened_count += 1;
                    opened.insert(dev.name.clone());
                    let l2 = l2_len_for(cap.get_datalink());
                    self.debug_log(
                        "info",
                        &format!(
                            "Listening on interface: {} (L2={}b — {})",
                            desc,
                            l2,
                            if l2 == 0 { "raw IP / VPN" } else { "ethernet" }
                        ),
                    )
                    .await;
                    // Spawns a dedicated thread per capture (blocking).
                    let tx_clone = tx.clone();
                    let liveness = Arc::clone(&self.generation);
                    std::thread::spawn(move || {
                        let mut cap = cap;
                        while liveness.load(Ordering::Acquire) == generation {
                            match cap.next_packet() {
                                Ok(packet) => {
                                    let _ =
                                        tx_clone.send(CaptureMsg::Packet(l2, packet.data.to_vec()));
                                }
                                Err(pcap::Error::TimeoutExpired) => { /* no packet */ }
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
                    // Don't mark as `opened` — the failure may be transient
                    // (driver still starting, interface not yet up) and
                    // that's exactly what the re-scan exists to cover.
                    self.debug_log("warn", &format!("Could not open {}: {}", desc, e))
                        .await;
                }
            }
        }
        if opened_count > 0 {
            self.debug_log(
                "info",
                &format!("{} new interface(s) active.", opened_count),
            )
            .await;
        }
        opened_count
    }

    /// Main loop: opens listeners on all interfaces, captures packets.
    ///
    /// pcap capture is blocking (pcap_next_ex blocks the thread). Since we're
    /// in a tokio runtime, capture runs on dedicated threads (spawn) that send
    /// packets via std::mpsc. The async task processes received packets without
    /// blocking the executor.
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
        // Zero the log file each session for readability.
        if let Some(p) = debug_log_path() {
            if let Some(parent) = p.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            let _ = std::fs::write(
                &p,
                format!("=== session {} ===\n", crate::photon_parser::now_iso_utc()),
            );
        }
        self.debug_log(
            "info",
            "Sniffer starting — scanning for network interfaces…",
        )
        .await;

        // The channel lives for the entire session: `open_all` is called
        // periodically (see `last_iface_scan` below) to pick up interfaces
        // that appear AFTER boot, reusing the same `tx`.
        let (tx, rx) = mpsc::channel::<CaptureMsg>();
        let mut opened_ifaces: HashSet<String> = HashSet::new();

        // Enumerating/opening interfaces can fail when the companion starts
        // alongside Windows (autostart): Npcap service and network adapters
        // aren't ready yet. Retries instead of killing the sniffer.
        loop {
            if self.generation.load(Ordering::Acquire) != generation {
                return;
            }
            let devices = match pcap::Device::list() {
                Ok(d) => d,
                Err(e) => {
                    let msg = format!("pcap Device::list failed: {}. Npcap installed?", e);
                    self.debug_log("err", &msg).await;
                    self.stats.lock().await.error = Some(msg);
                    tokio::time::sleep(std::time::Duration::from_secs(15)).await;
                    continue;
                }
            };
            let n = self
                .open_all(&devices, &tx, &mut opened_ifaces, generation)
                .await;
            if n > 0 {
                break;
            }
            let msg = "No network interface could be opened. Needs admin/Npcap?".to_string();
            self.debug_log("err", &format!("{} Retrying in 15s…", msg))
                .await;
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
        // Self-loot state (see photon_parser.rs) — same as `entities`, only
        // grows during the session; cleaned up on detach/consumption of the
        // looted slot, leftover is acceptable residue.
        let mut loot_container_owner: HashMap<i64, String> = HashMap::new();
        let mut loot_container_uuid: HashMap<[u8; 16], i64> = HashMap::new();
        let mut loot_container_slots: HashMap<(i64, i32), i64> = HashMap::new();
        let mut loot_objects: HashMap<i64, (i32, i32)> = HashMap::new();
        let mut last_heartbeat = Instant::now();
        let mut last_iface_scan = Instant::now();
        let mut raw_pkts: u64 = 0;
        // Packet dedup (see PKT_DEDUP_WINDOW): Photon payload hash → last seen.
        // Amortized prune every 500 packets to avoid unbounded growth.
        let mut recent_pkts: HashMap<u64, Instant> = HashMap::new();
        let mut pkt_since_prune: u32 = 0;

        loop {
            if self.generation.load(Ordering::Acquire) != generation {
                self.debug_log("info", "Sniffer stopped.").await;
                break;
            }

            // Receive packets from channel (non-blocking — if no packet, continue).
            match rx.recv_timeout(std::time::Duration::from_millis(50)) {
                Ok(CaptureMsg::Dead(name)) => {
                    opened_ifaces.remove(&name);
                    self.debug_log(
                        "warn",
                        &format!("Capture closed on {name}; interface will be reopened."),
                    )
                    .await;
                    last_iface_scan = Instant::now() - std::time::Duration::from_secs(60);
                }
                Ok(CaptureMsg::Packet(l2_hint, data)) => {
                    raw_pkts += 1;
                    // Photon payload offset: L2 (ethernet/raw) + IP (real IHL) + UDP.
                    // Hardcoding 42 breaks under VPN/ExitLag (raw IP adapters = 0 L2).
                    let off = match photon_offset(&data, l2_hint) {
                        Some(o) => o,
                        None => continue,
                    };
                    last_packet_time = Instant::now();
                    // AODP region: inferred from the Albion server IP in the IP header.
                    if let Some(srv) = albion_server_from_frame(&data, l2_hint) {
                        *self.aodp_server.lock().await = Some(srv);
                    }
                    let photon_data = &data[off..];

                    // Dedup: byte-identical copy from another interface (bridged
                    // adapter — see PKT_DEDUP_WINDOW) → skip, else loot/damage
                    // duplicates. Skips BEFORE parse (also saves the parse cost).
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
                        self.debug_log(
                            "info",
                            "Albion detected — packets received. Capturing loot…",
                        )
                        .await;
                    }

                    // Safety net: a malformed packet must never crash capture.
                    // If the parser panics, log, reset fragment state, continue.
                    let ops = match crate::crash_report::catch_unwind_silent(|| {
                        parser.parse(photon_data)
                    }) {
                        Ok(ops) => ops,
                        Err(_) => {
                            self.debug_log(
                                "err",
                                "parser panicked on a packet — skipping and resetting",
                            )
                            .await;
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
                        // Zone tracking: log ALL occurrences of zone opcodes
                        // (41=ChangeCluster resp, 17=JoinCluster, 294=ChangeCluster req)
                        // to see what fires on EACH map change.
                        if matches!(op.albion_code, 41 | 17 | 294) {
                            self.debug_log(
                                "info",
                                &format!(
                                    "ZONE op={} type={} :: {}",
                                    op.albion_code,
                                    op.message_type,
                                    dump_params(op)
                                ),
                            )
                            .await;
                        }

                        // Protocol discovery: log each new albion_code once with its
                        // params. Opcodes 2/41 may be wrong in this game version —
                        // this reveals which opcode carries name/map/guild, and also
                        // any self-loot opcode that extract_loot doesn't cover.
                        if op.albion_code >= 0
                            && seen_codes.insert(op.albion_code)
                            && seen_codes.len() <= 200
                        {
                            self.debug_log(
                                "info",
                                &format!(
                                    "op code={} type={} :: {}",
                                    op.albion_code,
                                    op.message_type,
                                    dump_params(op)
                                ),
                            )
                            .await;
                        }

                        // op 103 = guild/alliance info for local player (param 15/16).
                        // Inferred from the stream — appears 1x per session with
                        // constant values (the player's own guild/alliance).
                        if op.albion_code == 103 {
                            let mut s = self.stats.lock().await;
                            if let Some(PhotonValue::String(g)) = op.parameters.get(&15) {
                                if !g.is_empty() {
                                    s.guild_name = g.clone();
                                }
                            }
                            if let Some(PhotonValue::String(a)) = op.parameters.get(&16) {
                                if !a.is_empty() {
                                    s.alliance_name = a.clone();
                                }
                            }
                        }

                        if let Some(state) = extract_player_state(op) {
                            let mut s = self.stats.lock().await;
                            let name_changed =
                                !state.player_name.is_empty() && s.player_name != state.player_name;
                            let map_changed =
                                !state.map_index.is_empty() && s.last_map != state.map_index;
                            if !state.player_name.is_empty() {
                                s.player_name = state.player_name.clone();
                                local_player_name = state.player_name.clone();
                            }
                            if !state.guild_name.is_empty() {
                                s.guild_name = state.guild_name.clone();
                            }
                            if !state.alliance_name.is_empty() {
                                s.alliance_name = state.alliance_name.clone();
                            }
                            // Register the local player (id→name) for the damage meter —
                            // they don't come in NewCharacter events, only in Join response.
                            if let (Some(id), false) =
                                (state.local_object_id, state.player_name.is_empty())
                            {
                                self.entities
                                    .lock()
                                    .await
                                    .insert(id, state.player_name.clone());
                            }
                            if !state.map_index.is_empty() {
                                s.last_map = state.map_index.clone();
                                s.last_map_name = crate::maps::resolve(&state.map_index);
                            }
                            if name_changed {
                                self.debug_log(
                                    "info",
                                    &format!("Character detected: {}", s.player_name),
                                )
                                .await;
                            }
                            if map_changed {
                                self.debug_log(
                                    "info",
                                    &format!("Map change: {} ({})", s.last_map_name, s.last_map),
                                )
                                .await;
                            }
                        }

                        if op.albion_code == 231 {
                            if let Some(names) = extract_party(op) {
                                let mut s = self.stats.lock().await;
                                s.party_members = names;
                            }
                        }

                        if self.capture_loot.load(Ordering::Relaxed) {
                            // Loot diagnostic dump: first 20 events with loot-like
                            // structure (2 strings + 1 int), even if extract_loot
                            // accepts or rejects them. Also catches self-loot if it
                            // arrives with different param ordering.
                            // Same motivation as the [CALIB] dump for the damage meter:
                            // without real packet evidence, any fix is a guess.
                            let l_from = op
                                .parameters
                                .get(&1)
                                .and_then(|v| v.as_string())
                                .unwrap_or("")
                                .to_string();
                            let l_by = op
                                .parameters
                                .get(&2)
                                .and_then(|v| v.as_string())
                                .unwrap_or("")
                                .to_string();
                            let i4 = op.parameters.get(&4).and_then(|v| v.as_i64()).is_some();
                            let i5 = op.parameters.get(&5).and_then(|v| v.as_i64()).is_some();
                            // GVG_SEASON_xx crest events (op 388) have the same
                            // structure (2 strings + int) and fire dozens of times
                            // per minute — without excluding them they alone fill the
                            // 20-event cap before any real loot appears.
                            let is_gvg_noise =
                                l_from.starts_with("SCHEMA_") || l_by.starts_with("GUILDSYMBOL_");
                            let looks_like_loot = op.message_type == 4
                                && !l_from.is_empty()
                                && !l_by.is_empty()
                                && (i4 || i5)
                                && !is_gvg_noise;
                            if looks_like_loot && logged_loot < 20 {
                                logged_loot += 1;
                                let params = dump_params(op);
                                let item_idx =
                                    op.parameters.get(&4).and_then(|v| v.as_i64()).unwrap_or(0);
                                let qty =
                                    op.parameters.get(&5).and_then(|v| v.as_i64()).unwrap_or(0);
                                self.debug_log(
                                    "info",
                                    &format!(
                                        "[LOOT {:02}] op={} from={:?} by={:?} item={} qty={} | {}",
                                        logged_loot,
                                        op.albion_code,
                                        l_from,
                                        l_by,
                                        item_idx,
                                        qty,
                                        params
                                    ),
                                )
                                .await;
                            }
                            if let Some(loot) = extract_loot(op) {
                                self.push_loot(loot).await;
                            }
                            // Self-loot: extract_loot/OtherGrabbedLoot only covers OTHER
                            // players' loot — the server doesn't echo the broadcast back
                            // to the looter. Confirmed against ao-loot-logger: the only way
                            // to detect your OWN loot is to track the client's inventory
                            // move request, cross-referenced with the loot bag contents
                            // (seen in prior events). See extract_new_loot_owner docs in
                            // photon_parser.rs.
                            if let Some((id, owner)) = extract_new_loot_owner(op) {
                                loot_container_owner.insert(id, owner);
                            }
                            if let Some((object_id, item_index, quantity)) =
                                extract_new_loot_item(op)
                            {
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
                                    if let Some(&container_id) =
                                        loot_container_uuid.get(&mv.from_uuid)
                                    {
                                        if let Some(object_id) = loot_container_slots
                                            .remove(&(container_id, mv.from_slot))
                                        {
                                            if let Some((item_index, quantity)) =
                                                loot_objects.remove(&object_id)
                                            {
                                                if let Some(owner) =
                                                    loot_container_owner.get(&container_id)
                                                {
                                                    let loot = self_loot_event(
                                                        local_player_name.clone(),
                                                        owner.clone(),
                                                        item_index,
                                                        quantity,
                                                    );
                                                    self.push_loot(loot).await;
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // Damage meter: id→name registration + damage/heal accumulation.
                        // Entity registration always runs (cheap, and the meter needs
                        // names seen BEFORE the toggle is turned on).
                        if let Some((id, name)) = extract_new_character(op) {
                            // Dump first 3 NewCharacter with ALL params and
                            // array contents. Currently only id (0) and name (1)
                            // are read. Equipment comes in this same event —
                            // that's what's needed for weapon tier/enchant render
                            // in the ranking.
                            if logged_char < 3 {
                                logged_char += 1;
                                let mut ps: Vec<(u8, String)> =
                                    op.parameters.iter().map(|(k, v)| (*k, deep(v))).collect();
                                ps.sort_by_key(|(k, _)| *k);
                                let params = ps
                                    .iter()
                                    .map(|(k, v)| format!("{k}={v}"))
                                    .collect::<Vec<_>>()
                                    .join("  ");
                                self.debug_log(
                                    "info",
                                    &format!("[CHAR {}] {} | {}", logged_char, name, params),
                                )
                                .await;
                            }
                            self.entities.lock().await.insert(id, name);
                        }
                        if self.capture_damage.load(Ordering::Relaxed) {
                            if let Some(h) = extract_health(op) {
                                // Calibration dump: first damage events with ALL
                                // params and their values. Previously only logged
                                // keys, once — unhelpful when the suspicion is that
                                // the spell param moved in a patch.
                                if h.change < 0.0 && logged_health < 15 {
                                    logged_health += 1;
                                    let mut ps: Vec<(u8, String)> =
                                        op.parameters.iter().map(|(k, v)| (*k, brief(v))).collect();
                                    ps.sort_by_key(|(k, _)| *k);
                                    let params = ps
                                        .iter()
                                        .map(|(k, v)| format!("{k}={v}"))
                                        .collect::<Vec<_>>()
                                        .join("  ");
                                    let who = self
                                        .entities
                                        .lock()
                                        .await
                                        .get(&h.causer_id)
                                        .cloned()
                                        .unwrap_or_else(|| format!("id{}", h.causer_id));
                                    self.debug_log(
                                        "info",
                                        &format!(
                                            "[CALIB {:02}] {} dano={:.0} spell={} | {}",
                                            logged_health, who, -h.change, h.spell_id, params
                                        ),
                                    )
                                    .await;
                                }
                                // Only damage (change < 0). Healing is discarded on
                                // purpose — this is the damage panel.
                                //
                                // spell 0 is excluded: unattributed damage (what shows
                                // when someone /die's). It was credited to index 0 of the
                                // table and appeared as "Trudge". Discarded in
                                // ACCUMULATION, not just display — if it entered the
                                // total, other skills' percentages would be wrong.
                                if h.change < 0.0 && h.spell_id != 0 {
                                    let now = std::time::SystemTime::now()
                                        .duration_since(std::time::UNIX_EPOCH)
                                        .map(|d| d.as_secs())
                                        .unwrap_or(0);
                                    // Target known in `entities` = player.
                                    // Same criterion the meter uses to decide which
                                    // ROWS are players (NewCharacter only comes from
                                    // players); mobs don't enter the map.
                                    // Lock `damage_vs_players` after releasing
                                    // `damage` — same order as get_damage_meter.
                                    let target_is_player =
                                        self.entities.lock().await.contains_key(&h.target_id);
                                    let mut dmg = self.damage.lock().await;
                                    dmg.entry(h.causer_id)
                                        .or_default()
                                        .record(h.spell_id, -h.change, now);
                                    drop(dmg);
                                    if target_is_player {
                                        self.damage_vs_players
                                            .lock()
                                            .await
                                            .entry(h.causer_id)
                                            .or_default()
                                            .record(h.spell_id, -h.change, now);
                                    }
                                }
                            }
                        }

                        // Market: marketplace responses while the player browses.
                        // Feeds OUR database (by city) and forwards to AODP (verbatim).
                        if self.capture_prices.load(Ordering::Relaxed)
                            || self.feed_aodp.load(Ordering::Relaxed)
                        {
                            let cap = extract_market(op);
                            if !cap.raw_orders.is_empty() {
                                let (city, raw_map, map_name) = {
                                    let s = self.stats.lock().await;
                                    let city = MARKET_CITIES
                                        .iter()
                                        .find(|c| s.last_map_name.contains(*c))
                                        .map(|c| c.to_string());
                                    (city, s.last_map.clone(), s.last_map_name.clone())
                                };

                                // Our database: only in known market cities.
                                if self.capture_prices.load(Ordering::Relaxed) {
                                    if let Some(city) = &city {
                                        let ts = crate::photon_parser::now_iso_utc();
                                        let mut buf = self.prices.lock().await;
                                        for o in &cap.offers {
                                            // Convert UniqueName (game's ItemTypeId)
                                            // → game_name (English in-game name), which is
                                            // the canonical ID in our price database.
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
                                        if len > 5000 {
                                            buf.drain(..len - 5000);
                                        }
                                    }
                                }

                                // AODP: forward raw orders. Numeric LocationId (current
                                // cluster) filled when the order doesn't have one — same
                                // as the official client does.
                                if self.feed_aodp.load(Ordering::Relaxed) {
                                    let server = self.aodp_server.lock().await.clone();
                                    let numeric_loc = raw_map.trim_start_matches('0');
                                    // Accept numeric clusters (real cities, Rests)
                                    // AND BLACKBANK-* (Smuggler's Den) — the
                                    // official AODP client accepts both.
                                    let is_valid_loc = !numeric_loc.is_empty()
                                        && (numeric_loc.chars().all(|c| c.is_ascii_digit())
                                            || numeric_loc.starts_with("BLACKBANK-"));
                                    if let Some(server) = server {
                                        if is_valid_loc {
                                            let orders: Vec<serde_json::Value> = cap
                                                .raw_orders
                                                .iter()
                                                .map(|o| {
                                                    let mut o = o.clone();
                                                    let empty = o
                                                        .get("LocationId")
                                                        .and_then(|l| l.as_str())
                                                        .map_or(true, |l| l.is_empty());
                                                    if empty {
                                                        o["LocationId"] = serde_json::Value::String(
                                                            numeric_loc.to_string(),
                                                        );
                                                    }
                                                    o
                                                })
                                                .collect();
                                            let natsmsg =
                                                serde_json::json!({ "Orders": orders }).to_string();
                                            let mut buf = self.aodp_out.lock().await;
                                            buf.push(AodpBatch {
                                                server_id: server.id,
                                                base_url: server.base_url,
                                                topic: "marketorders.ingest".into(),
                                                natsmsg,
                                            });
                                            // Cap: max 50 pending batches.
                                            let len = buf.len();
                                            if len > 50 {
                                                buf.drain(..len - 50);
                                            }
                                        } else {
                                            self.debug_log(
                                                "warn",
                                                &format!(
                                                    "AODP: invalid location ({}), skipping send",
                                                    map_name
                                                ),
                                            )
                                            .await;
                                        }
                                    }
                                }
                            }
                        }

                        // Market history: aggregate chart from the game itself.
                        // Request carries item/quality/scale; response carries buckets —
                        // correlated by message-id. Stored in OUR database
                        // (independent of AODP).
                        if self.capture_prices.load(Ordering::Relaxed) {
                            if let Some((mid, info)) = extract_history_request(op) {
                                let mut pend = self.history_pending.lock().await;
                                pend.insert(mid, info);
                                // Cap: discard old orphan requests (response never came).
                                if pend.len() > 256 {
                                    let drop: Vec<u64> =
                                        pend.keys().take(pend.len() - 256).copied().collect();
                                    for k in drop {
                                        pend.remove(&k);
                                    }
                                }
                            }
                            if let Some((mid, buckets)) = extract_history_response(op) {
                                let info = self.history_pending.lock().await.remove(&mid);
                                if let Some(info) = info {
                                    let location = {
                                        let s = self.stats.lock().await;
                                        s.last_map.trim_start_matches('0').to_string()
                                    };
                                    // Albion server region (detected from packet IPs) —
                                    // markets are separated by server.
                                    let region = self
                                        .aodp_server
                                        .lock()
                                        .await
                                        .as_ref()
                                        .map(|s| s.region())
                                        .unwrap_or("west");
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
                                    if len > 10000 {
                                        buf.drain(..len - 10000);
                                    }
                                }
                            }
                        }

                        // Gold: gold market price (global, no location).
                        // Only needs the server region — forwards to AODP.
                        if self.feed_aodp.load(Ordering::Relaxed) {
                            if let Some(g) = extract_gold(op) {
                                if let Some(server) = self.aodp_server.lock().await.clone() {
                                    let natsmsg = serde_json::json!({
                                        "Prices": g.prices,
                                        "Timestamps": g.timestamps,
                                    })
                                    .to_string();
                                    let mut buf = self.aodp_out.lock().await;
                                    buf.push(AodpBatch {
                                        server_id: server.id,
                                        base_url: server.base_url,
                                        topic: "goldprices.ingest".into(),
                                        natsmsg,
                                    });
                                    let len = buf.len();
                                    if len > 50 {
                                        buf.drain(..len - 50);
                                    }
                                }
                            }
                        }
                    }
                }
                Err(mpsc::RecvTimeoutError::Timeout) => { /* no packets — continue loop */ }
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    self.debug_log(
                        "err",
                        "All capture interfaces closed. Restart the companion.",
                    )
                    .await;
                    let mut s = self.stats.lock().await;
                    s.running = false;
                    break;
                }
            }

            // Diagnostic heartbeat: every 10s shows if packets/parse are flowing.
            // pkts>0 & ops=0 → parser broken; pkts=0 → capture not seeing Albion.
            // File only — not shown in the UI terminal (would pollute without
            // actionable info).
            if last_heartbeat.elapsed().as_secs() >= 10 {
                last_heartbeat = Instant::now();
                let s = self.stats.lock().await;
                self.debug_log_file(
                    "info",
                    &format!(
                        "stats: raw={} pkts={} parsed={} ops={} loot={} online={} codes={}",
                        raw_pkts,
                        s.packets_captured,
                        s.packets_parsed,
                        s.operations_extracted,
                        s.loot_count,
                        s.online,
                        seen_codes.len()
                    ),
                )
                .await;
            }

            // Offline detection: 5s without packets.
            if was_online && last_packet_time.elapsed().as_secs() >= 5 {
                was_online = false;
                let mut s = self.stats.lock().await;
                s.online = false;
                self.debug_log(
                    "warn",
                    "No Albion packets for 5s — game closed or VPN changed route?",
                )
                .await;
            }

            // Re-scan interfaces: picks up adapters that came up AFTER boot
            // (WiFi still associating, VPN enabled later). Without this, a
            // virtual interface that opened first (Hyper-V, Docker, VirtualBox)
            // would trap the sniffer on it forever, even when the real NIC is
            // available seconds later.
            //
            // Adaptive cadence: 30s while ONLINE (only watching for rare new
            // adapters), but 5s while OFFLINE. Going offline mid-session is
            // almost always a ROUTE CHANGE — the user enabled a VPN (Cloudflare
            // WARP, etc.) and Albion traffic migrated to a new WinTun interface
            // we're not yet listening on. Fast rescan rehooks as soon as that
            // adapter gets IPv4. Offline = game closed or idle, so scanning
            // more is free.
            let rescan_secs = if was_online { 30 } else { 5 };
            if last_iface_scan.elapsed().as_secs() >= rescan_secs {
                last_iface_scan = Instant::now();
                if let Ok(devices) = pcap::Device::list() {
                    self.open_all(&devices, &tx, &mut opened_ifaces, generation)
                        .await;
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
            self.debug_log("err", &format!("Failed to persist session loot: {e}"))
                .await;
        }
    }

    async fn debug_log(&self, level: &str, msg: &str) {
        self.debug_log_inner(level, msg, true).await;
    }

    /// File only — not shown in the UI terminal. For periodic heartbeats
    /// (stats: raw=… pkts=…) that pollute the screen but are still useful
    /// for on-disk debugging.
    async fn debug_log_file(&self, level: &str, msg: &str) {
        self.debug_log_inner(level, msg, false).await;
    }

    async fn debug_log_inner(&self, level: &str, msg: &str, to_ui: bool) {
        let ts = crate::photon_parser::now_iso_utc();
        if let Some(p) = debug_log_path() {
            use std::io::Write;
            if let Ok(mut f) = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&p)
            {
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

/// Diagnostic log file path.
/// Photon parameter value for calibration dumps.
/// Collections only show their size; scalars are what matter here.
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

/// Like `brief`, but EXPANDS arrays (up to 16 items).
/// Equipment comes as an item-index array — `arr[13]` says nothing, the
/// numbers reveal which pieces they are.
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

/// Opens a capture on a specific device with BPF filter for Albion's 3 ports.
fn open_device_capture(dev: &pcap::Device) -> Result<pcap::Capture<pcap::Active>, String> {
    let builder =
        pcap::Capture::from_device(dev.clone()).map_err(|e| format!("from_device: {}", e))?;
    let builder = builder.promisc(true).immediate_mode(true).timeout(500);
    let mut opened = builder
        .open()
        .map_err(|e| format!("open: {}. Needs admin/Npcap?", e))?;
    opened
        .filter("udp and (port 5056 or port 5055 or port 4535)", true)
        .map_err(|e| format!("BPF filter: {}", e))?;
    Ok(opened)
}

/// Compact repr of an operation's params (idx=value) — for calibrating indices.
fn dump_params(op: &crate::photon_parser::ParsedOperation) -> String {
    use crate::photon_parser::PhotonValue;
    let mut keys: Vec<u8> = op.parameters.keys().copied().collect();
    keys.sort();
    keys.iter()
        .map(|k| {
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
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// L2 header size from the interface's datalink type.
/// Ethernet=14; NULL (BSD loopback / some VPN)=4; rest (raw IP)=0.
fn l2_len_for(dl: pcap::Linktype) -> usize {
    match dl.0 {
        1 => 14, // DLT_EN10MB
        0 => 4,  // DLT_NULL
        _ => 0,  // DLT_RAW and similar: raw IP packet (VPN/ExitLag/TUN)
    }
}

/// Photon payload offset within the captured frame.
/// Tries the L2 suggested by the datalink, then falls back to 0/14/4 and
/// validates the IPv4+UDP structure — works on ethernet, raw IP (VPN) and
/// loopback without hardcoding.
fn photon_offset(data: &[u8], l2_hint: usize) -> Option<usize> {
    for &l2 in &[l2_hint, 0, 14, 4] {
        if data.len() < l2 + 28 {
            continue;
        } // 20 min IP + 8 UDP
        let vihl = data[l2];
        if vihl >> 4 != 4 {
            continue;
        } // IPv4 only (Albion is IPv4)
        let ihl = (vihl & 0x0F) as usize * 4;
        if ihl < 20 {
            continue;
        }
        // Confirm UDP protocol (byte 9 of IPv4 header = 17).
        if data[l2 + 9] != 17 {
            continue;
        }
        let off = l2 + ihl + 8;
        if data.len() >= off {
            return Some(off);
        }
    }
    None
}

/// Infers the AODP region from the IPs (src/dst) in the IPv4 header.
/// Src IP (bytes 12-15) and dst IP (16-19) — one of them is the Albion server.
fn albion_server_from_frame(data: &[u8], l2_hint: usize) -> Option<AodpServer> {
    for &l2 in &[l2_hint, 0, 14, 4] {
        if data.len() < l2 + 20 {
            continue;
        }
        let vihl = data[l2];
        if vihl >> 4 != 4 {
            continue;
        }
        if data[l2 + 9] != 17 {
            continue;
        } // UDP
        let src = [data[l2 + 12], data[l2 + 13], data[l2 + 14], data[l2 + 15]];
        let dst = [data[l2 + 16], data[l2 + 17], data[l2 + 18], data[l2 + 19]];
        if let Some(s) = aodp::server_for_ip(src).or_else(|| aodp::server_for_ip(dst)) {
            return Some(s);
        }
        return None; // Valid IP header but neither side is a known Albion server
    }
    None
}

// ── Npcap DLL path fix ─────────────────────────────────────────────────────
// Modern Npcap (without "WinPcap API-compatible Mode") installs wpcap.dll
// and Packet.dll in C:\Windows\System32\Npcap\ and relies on the process
// PATH to find them. The `pcap` crate calls LoadLibrary("wpcap.dll") — if
// PATH doesn't include the subdir, it fails with "wpcap.dll not found" even
// though Npcap is installed. This runs ONCE at startup: reads InstallDir
// from the registry, adds it to the process PATH, and calls
// SetDllDirectoryW for the same dir. If Npcap isn't installed at all, this
// is a no-op — installation is MANUAL on purpose (the free installer aborts
// `/S`). The sniffer detects absence on its own (Device::list fails) and the
// UI banner directs the user to manual download. No-op on non-Windows.
#[cfg(target_os = "windows")]
pub fn ensure_npcap_dll_path() {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::System::LibraryLoader::SetDllDirectoryW;
    use windows_sys::Win32::System::Registry::{
        RegCloseKey, RegOpenKeyExW, RegQueryValueExW, HKEY_LOCAL_MACHINE, KEY_READ,
    };

    // Npcap stores InstallDir + Version in HKLM\SOFTWARE\WOW6432Node\Npcap
    // (64-bit) or HKLM\SOFTWARE\Npcap (32-bit). Try both.
    let mut dir: Option<std::path::PathBuf> = None;
    let mut version: Option<String> = None;
    for subkey in ["SOFTWARE\\WOW6432Node\\Npcap", "SOFTWARE\\Npcap"] {
        let mut hkey = 0isize;
        let subkey_w: Vec<u16> = subkey.encode_utf16().chain(std::iter::once(0)).collect();
        if unsafe {
            RegOpenKeyExW(
                HKEY_LOCAL_MACHINE,
                subkey_w.as_ptr(),
                0,
                KEY_READ,
                &mut hkey,
            )
        } != 0
        {
            continue;
        }
        // InstallDir
        let mut len = 512u32;
        let mut buf = vec![0u16; (len as usize / 2) + 1];
        let valname: Vec<u16> = "InstallDir\0".encode_utf16().collect();
        let mut ty = 0u32;
        if unsafe {
            RegQueryValueExW(
                hkey,
                valname.as_ptr(),
                std::ptr::null_mut(),
                &mut ty,
                buf.as_mut_ptr() as *mut u8,
                &mut len,
            )
        } == 0
            && ty == 1
        {
            let nul = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
            let s = String::from_utf16_lossy(&buf[..nul]);
            if !s.is_empty() {
                dir = Some(std::path::PathBuf::from(s));
            }
        }
        // Version (optional — informational only for now)
        let mut len = 64u32;
        let mut buf = vec![0u16; (len as usize / 2) + 1];
        let valname: Vec<u16> = "Version\0".encode_utf16().collect();
        let mut ty = 0u32;
        if unsafe {
            RegQueryValueExW(
                hkey,
                valname.as_ptr(),
                std::ptr::null_mut(),
                &mut ty,
                buf.as_mut_ptr() as *mut u8,
                &mut len,
            )
        } == 0
            && ty == 1
        {
            let nul = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
            let s = String::from_utf16_lossy(&buf[..nul]);
            if !s.is_empty() {
                version = Some(s);
            }
        }
        unsafe {
            RegCloseKey(hkey);
        }
        if dir.is_some() {
            break;
        }
    }
    let _ = version; // reserved for future use (alert on old version, etc.)

    // No Npcap at all: nothing to do here, the sniffer will report the
    // error and the UI shows the download banner (see comment above).
    let dir = match dir {
        Some(d) => d,
        None => return,
    };
    // Already in System32? LoadLibrary finds it on its own, no-op.
    let sys32 = std::env::var_os("SystemRoot")
        .map(|r| std::path::PathBuf::from(r).join("System32"))
        .unwrap_or_default();
    if dir == sys32 {
        return;
    }

    // Add to process PATH (affects LoadLibrary search order).
    if let Some(cur) = std::env::var_os("PATH") {
        let mut parts: Vec<std::path::PathBuf> = std::env::split_paths(&cur).collect();
        if !parts.contains(&dir) {
            parts.push(dir.clone());
            if let Ok(joined) = std::env::join_paths(parts) {
                std::env::set_var("PATH", joined);
            }
        }
    }
    // SetDllDirectoryW: supplemental to PATH for the loader to find
    // wpcap.dll even without being in the system PATH. Harmless if already
    // in System32.
    let wide: Vec<u16> = dir
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    unsafe {
        SetDllDirectoryW(wide.as_ptr());
    }
}

#[cfg(not(target_os = "windows"))]
pub fn ensure_npcap_dll_path() {}

/// Checks whether Npcap is installed (registry key exists), without
/// modifying PATH/SetDllDirectory — used to decide whether to register
/// autostart (see `set_autostart` in lib.rs). Low cost: only opens and
/// closes the key, doesn't read values.
#[cfg(target_os = "windows")]
pub fn npcap_installed() -> bool {
    use windows_sys::Win32::System::Registry::{
        RegCloseKey, RegOpenKeyExW, HKEY_LOCAL_MACHINE, KEY_READ,
    };
    for subkey in ["SOFTWARE\\WOW6432Node\\Npcap", "SOFTWARE\\Npcap"] {
        let mut hkey = 0isize;
        let subkey_w: Vec<u16> = subkey.encode_utf16().chain(std::iter::once(0)).collect();
        if unsafe {
            RegOpenKeyExW(
                HKEY_LOCAL_MACHINE,
                subkey_w.as_ptr(),
                0,
                KEY_READ,
                &mut hkey,
            )
        } == 0
        {
            unsafe {
                RegCloseKey(hkey);
            }
            return true;
        }
    }
    false
}

#[cfg(not(target_os = "windows"))]
pub fn npcap_installed() -> bool {
    true
}

#[cfg(test)]
mod tests {
    use super::photon_offset;
    use super::{is_duplicate_loot, LootEvent, LOOT_DEDUP_LOOKBACK};

    // Minimum UDP/IPv4: [ip header 20][udp 8][payload]. version=4, ihl=5, proto=17.
    fn ipv4_udp(payload: &[u8]) -> Vec<u8> {
        let mut p = vec![0u8; 28];
        p[0] = 0x45; // version 4, IHL 5
        p[9] = 17; // protocol UDP
        p.extend_from_slice(payload);
        p
    }

    #[test]
    fn ethernet_offset() {
        // 14 bytes ethernet header
        let mut frame = vec![0u8; 14];
        frame.extend_from_slice(&ipv4_udp(b"PHOTON"));
        assert_eq!(photon_offset(&frame, 14), Some(14 + 28));
        assert_eq!(&frame[42..], b"PHOTON");
    }

    #[test]
    fn raw_ip_offset_vpn() {
        // No ethernet (VPN/ExitLag). Wrong hint (14) but fallback finds at 0.
        let frame = ipv4_udp(b"PHOTON");
        assert_eq!(photon_offset(&frame, 14), Some(28));
        assert_eq!(&frame[28..], b"PHOTON");
    }

    #[test]
    fn ip_options_offset() {
        // IHL=6 (24 bytes IP header, 4 bytes options)
        let mut p = vec![0u8; 32];
        p[0] = 0x46; // version 4, IHL 6
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
        // First time: not a dup.
        assert!(!packet_is_dup(&mut recent, 42, t0, win));
        // Same hash shortly after (copy from another interface): is dup.
        assert!(packet_is_dup(
            &mut recent,
            42,
            t0 + Duration::from_millis(5),
            win
        ));
        // Different hash (two identical loots come with different sequences): not dup.
        assert!(!packet_is_dup(
            &mut recent,
            99,
            t0 + Duration::from_millis(6),
            win
        ));
        // Same hash outside window: not a copy (legitimate packet reuses hash
        // only after reconnect/sequence reset, much later) → not dup.
        assert!(!packet_is_dup(
            &mut recent,
            42,
            t0 + Duration::from_secs(3),
            win
        ));
    }

    fn loot_ev(by: &str, from: &str, idx: i32, qty: i32) -> LootEvent {
        LootEvent {
            ts: "2026-07-23T12:00:00Z".into(),
            looted_by: by.into(),
            looted_from: from.into(),
            item_index: idx,
            quantity: qty,
            is_silver: false,
        }
    }

    #[test]
    fn loot_dedup_pega_evento_identico_vindo_de_2_interfaces() {
        let mut buf = vec![loot_ev("Zezinho", "Fulano", 2958, 3)];
        // Same identity (same copy arriving from the other interface) → dup.
        assert!(is_duplicate_loot(
            &buf,
            &loot_ev("Zezinho", "Fulano", 2958, 3)
        ));
        buf.push(loot_ev("Zezinho", "Fulano", 2958, 3));

        // Different item from same body, same second → not dup.
        assert!(!is_duplicate_loot(
            &buf,
            &loot_ev("Zezinho", "Fulano", 1001, 3)
        ));
        // Different quantity → not dup.
        assert!(!is_duplicate_loot(
            &buf,
            &loot_ev("Zezinho", "Fulano", 2958, 5)
        ));
        // Different looter → not dup.
        assert!(!is_duplicate_loot(
            &buf,
            &loot_ev("Outro", "Fulano", 2958, 3)
        ));
    }

    #[test]
    fn loot_dedup_nao_enxerga_alem_do_lookback() {
        // Event beyond the lookback window (more than LOOT_DEDUP_LOOKBACK
        // distinct events in between) is not considered a dup — avoids false
        // positive when the same drop happens again much later.
        let mut buf = vec![loot_ev("A", "B", 1, 1)];
        for i in 0..LOOT_DEDUP_LOOKBACK {
            buf.push(loot_ev("X", "Y", 100 + i as i32, 1));
        }
        assert!(!is_duplicate_loot(&buf, &loot_ev("A", "B", 1, 1)));
    }
}
