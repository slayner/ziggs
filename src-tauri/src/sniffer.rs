#![cfg_attr(not(target_os = "windows"), allow(dead_code, unused_imports))]

// Packet sniffer: captures Albion UDP packets and feeds them to the parser.
//
// The user picks the capture method (see `CaptureMethod`): Npcap (npcap.rs) or
// WinDivert (windivert.rs). Everything after the packet arrives is the same.
//
// Each packet is passed to the PhotonParser. Loot events (opcode 256) go to
// the loot buffer. Debug logs go to the debug buffer (warnings are shown on the
// Lootlog). Online/offline detection: no packets for 5s = offline.

use serde::{Deserialize, Serialize};
#[cfg(any(target_os = "windows", test))]
use std::collections::hash_map::DefaultHasher;
use std::collections::HashMap;
#[cfg(any(target_os = "windows", test))]
use std::collections::HashSet;
#[cfg(any(target_os = "windows", test))]
use std::hash::{Hash, Hasher};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
#[cfg(target_os = "windows")]
use std::sync::mpsc;
use std::sync::Arc;
use std::time::Instant;
use tokio::sync::Mutex;

use crate::config::CaptureMethod;
#[cfg(target_os = "windows")]
use crate::photon_parser::{
    extract_attach_container, extract_character_stats, extract_detach_container, extract_health,
    extract_inventory_move, extract_loot, extract_new_character, extract_new_loot_item,
    extract_new_loot_owner, extract_player_state, self_loot_event, PartyTracker, PhotonParser,
};
use crate::photon_parser::{DamageAcc, LootEvent, PhotonValue};

/// A capture problem the user can fix. Goes to the UI as a code, so the UI can show it
/// translated and with the steps to fix it.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CaptureIssue {
    /// Npcap is not installed: the UI shows the install guide.
    NpcapMissing,
    /// Npcap is installed but no adapter could be opened.
    NpcapUnavailable,
    /// Npcap runs and Albion is open, but nothing arrives. Usually a route optimizer.
    NpcapNoPackets,
    /// WinDivert is the chosen method, but the user declined the administrator prompt.
    WindivertNeedsAdmin,
}

/// How long Albion can stay open without a packet before the UI suggests that Npcap
/// isn't seeing its traffic. The login screen is quiet, so this can't be short.
const NO_PACKETS_HINT_AFTER: std::time::Duration = std::time::Duration::from_secs(60);

/// `albion_is_open` lists every process on the PC, so it only runs when the rest applies.
fn no_packets_issue(
    method: CaptureMethod,
    silent_for: std::time::Duration,
    albion_is_open: impl FnOnce() -> bool,
) -> Option<CaptureIssue> {
    let applies =
        method == CaptureMethod::Npcap && silent_for >= NO_PACKETS_HINT_AFTER && albion_is_open();
    applies.then_some(CaptureIssue::NpcapNoPackets)
}

/// Packet dedup window. Npcap listens on ALL interfaces on purpose
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

fn add_loot_and_persist(
    loot: &mut Vec<LootEvent>,
    event: LootEvent,
    save: impl FnOnce(&[LootEvent]) -> anyhow::Result<()>,
) -> anyhow::Result<bool> {
    if is_duplicate_loot(loot, &event) {
        return Ok(false);
    }

    loot.push(event);
    if let Err(error) = save(loot) {
        loot.pop();
        return Err(error);
    }
    Ok(true)
}

fn clear_loot_and_persist(
    loot: &mut Vec<LootEvent>,
    save: impl FnOnce(&[LootEvent]) -> anyhow::Result<()>,
) -> anyhow::Result<()> {
    let previous = std::mem::take(loot);
    if let Err(error) = save(loot) {
        *loot = previous;
        return Err(error);
    }
    Ok(())
}

fn fill_missing(target: &mut String, value: &str) -> bool {
    if target.is_empty() && !value.is_empty() {
        target.push_str(value);
        true
    } else {
        false
    }
}

fn update_metadata_field(target: &mut String, update: &str) {
    if !update.is_empty() && target != update {
        target.clear();
        target.push_str(update);
    }
}

fn merge_character_metadata(
    target: &mut crate::photon_parser::CharacterMetadata,
    update: &crate::photon_parser::CharacterMetadata,
) {
    update_metadata_field(&mut target.guild_name, &update.guild_name);
    update_metadata_field(&mut target.alliance_name, &update.alliance_name);
}

fn apply_character_metadata(
    loot: &mut LootEvent,
    name: &str,
    metadata: &crate::photon_parser::CharacterMetadata,
) -> bool {
    let mut changed = false;
    if loot.looted_by == name {
        changed |= fill_missing(&mut loot.looted_by_guild, &metadata.guild_name);
        changed |= fill_missing(&mut loot.looted_by_alliance, &metadata.alliance_name);
    }
    if loot.looted_from == name {
        changed |= fill_missing(&mut loot.looted_from_guild, &metadata.guild_name);
        changed |= fill_missing(&mut loot.looted_from_alliance, &metadata.alliance_name);
    }
    changed
}

fn loot_needs_metadata(
    loot: &LootEvent,
    name: &str,
    metadata: &crate::photon_parser::CharacterMetadata,
) -> bool {
    (loot.looted_by == name
        && ((loot.looted_by_guild.is_empty() && !metadata.guild_name.is_empty())
            || (loot.looted_by_alliance.is_empty() && !metadata.alliance_name.is_empty())))
        || (loot.looted_from == name
            && ((loot.looted_from_guild.is_empty() && !metadata.guild_name.is_empty())
                || (loot.looted_from_alliance.is_empty() && !metadata.alliance_name.is_empty())))
}

fn backfill_loot_and_persist(
    loot: &mut Vec<LootEvent>,
    name: &str,
    metadata: &crate::photon_parser::CharacterMetadata,
    save: impl FnOnce(&[LootEvent]) -> anyhow::Result<()>,
) -> anyhow::Result<bool> {
    if !loot
        .iter()
        .any(|event| loot_needs_metadata(event, name, metadata))
    {
        return Ok(false);
    }

    let previous = loot.clone();
    for event in loot.iter_mut() {
        apply_character_metadata(event, name, metadata);
    }
    if let Err(error) = save(loot) {
        *loot = previous;
        return Err(error);
    }
    Ok(true)
}

fn backfill_pending_metadata_and_persist(
    loot: &mut Vec<LootEvent>,
    metadata_by_name: &HashMap<String, crate::photon_parser::CharacterMetadata>,
    save: impl FnOnce(&[LootEvent]) -> anyhow::Result<()>,
) -> anyhow::Result<bool> {
    let previous = loot.clone();
    let mut changed = false;

    for event in loot.iter_mut() {
        let looted_by = event.looted_by.clone();
        let looted_from = event.looted_from.clone();
        if let Some(metadata) = metadata_by_name.get(&looted_by) {
            changed |= apply_character_metadata(event, &looted_by, metadata);
        }
        if looted_from != looted_by {
            if let Some(metadata) = metadata_by_name.get(&looted_from) {
                changed |= apply_character_metadata(event, &looted_from, metadata);
            }
        }
    }

    if !changed {
        return Ok(false);
    }
    if let Err(error) = save(loot) {
        *loot = previous;
        return Err(error);
    }
    Ok(true)
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
    #[serde(default)]
    pub capture_issue: Option<CaptureIssue>,
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
            capture_issue: None,
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
    pub character_metadata: Arc<Mutex<HashMap<String, crate::photon_parser::CharacterMetadata>>>,
    party: Arc<Mutex<PartyTracker>>,
    /// Damage/healing accumulated by causer_id this session.
    pub damage: Arc<Mutex<HashMap<i64, DamageAcc>>>,
    /// Same accumulation, but only for hits whose TARGET is a known player.
    /// Separate accumulator instead of read-time filter because the target
    /// doesn't survive `record`: DamageAcc is indexed by causer and discards
    /// target_id. Filtering after would require per-target breakdown — much
    /// more memory than keeping two totals.
    pub damage_vs_players: Arc<Mutex<HashMap<i64, DamageAcc>>>,
    /// Capture gates — mirror the configuration toggles.
    pub capture_loot: Arc<AtomicBool>,
    pub capture_damage: Arc<AtomicBool>,
    generation: Arc<AtomicU64>,
    /// WinDivert capture handle (0 = not started), shut down on stop. Npcap needs no
    /// such thing: its threads watch `generation`. See `windivert.rs`.
    windivert_handle: Arc<std::sync::Mutex<isize>>,
}

pub enum CaptureMsg {
    Packet(usize, Vec<u8>),
    /// The capture ended by itself. The sniffer starts it over, or reports why it can't.
    Dead(String),
    /// A line for the log file, from a capture thread.
    Log(String),
}

/// Why a capture could not start, split by who needs to know what.
#[cfg(target_os = "windows")]
#[derive(Clone, PartialEq)]
struct CaptureProblem {
    issue: Option<CaptureIssue>,
    /// Technical text shown on screen. None when the issue code says it all.
    shown: Option<String>,
    logged: String,
}

impl Sniffer {
    pub fn new() -> Self {
        let loot = match crate::lootlog::load_session() {
            Ok(events) => events,
            Err(e) => {
                tracing::warn!("loot session could not be loaded: {e:#}");
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
            character_metadata: Arc::new(Mutex::new(HashMap::new())),
            party: Arc::new(Mutex::new(PartyTracker::default())),
            damage: Arc::new(Mutex::new(HashMap::new())),
            damage_vs_players: Arc::new(Mutex::new(HashMap::new())),
            capture_loot: Arc::new(AtomicBool::new(false)),
            capture_damage: Arc::new(AtomicBool::new(false)),
            generation: Arc::new(AtomicU64::new(0)),
            windivert_handle: Arc::new(std::sync::Mutex::new(0)),
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
            character_metadata: Arc::clone(&self.character_metadata),
            party: Arc::clone(&self.party),
            damage: Arc::clone(&self.damage),
            damage_vs_players: Arc::clone(&self.damage_vs_players),
            capture_loot: Arc::clone(&self.capture_loot),
            capture_damage: Arc::clone(&self.capture_damage),
            generation: Arc::clone(&self.generation),
            windivert_handle: Arc::clone(&self.windivert_handle),
        }
    }

    pub async fn stop(&self) {
        self.generation.fetch_add(1, Ordering::SeqCst);
        {
            let mut stats = self.stats.lock().await;
            stats.running = false;
            stats.online = false;
            stats.error = None;
            stats.capture_issue = None;
        }
        #[cfg(target_os = "windows")]
        crate::windivert::shutdown(&self.windivert_handle);
    }

    pub fn prepare_start(&self) -> u64 {
        self.generation.fetch_add(1, Ordering::SeqCst) + 1
    }

    pub fn is_current(&self, generation: u64) -> bool {
        self.generation.load(Ordering::Acquire) == generation
    }

    #[cfg(target_os = "windows")]
    fn start_backend(
        &self,
        method: CaptureMethod,
        tx: &mpsc::Sender<CaptureMsg>,
        generation: u64,
    ) -> Result<(), CaptureProblem> {
        match method {
            CaptureMethod::Npcap => {
                crate::npcap::start_capture(tx.clone(), Arc::clone(&self.generation), generation)
                    .map_err(|error| match error {
                        crate::npcap::StartError::Missing(detail) => CaptureProblem {
                            issue: Some(CaptureIssue::NpcapMissing),
                            shown: None,
                            logged: format!("Npcap not found: {detail}"),
                        },
                        crate::npcap::StartError::Unavailable(detail) => CaptureProblem {
                            issue: Some(CaptureIssue::NpcapUnavailable),
                            logged: format!("Npcap could not start: {detail}"),
                            shown: Some(detail),
                        },
                    })
            }
            CaptureMethod::Windivert if !crate::elevation::is_elevated() => Err(CaptureProblem {
                issue: Some(CaptureIssue::WindivertNeedsAdmin),
                shown: None,
                logged: "WinDivert needs administrator rights".to_string(),
            }),
            CaptureMethod::Windivert => crate::windivert::start_capture(
                tx.clone(),
                Arc::clone(&self.generation),
                generation,
                Arc::clone(&self.windivert_handle),
            )
            .map_err(|detail| CaptureProblem {
                issue: None,
                logged: format!("WinDivert could not start: {detail}"),
                shown: Some(format!("WinDivert: {detail}")),
            }),
        }
    }

    /// Tries until the capture starts or this generation is replaced. Retrying is what
    /// makes it start by itself when the user finishes installing Npcap, and when the
    /// app autostarts before Windows has the driver or the network ready. Returns
    /// whether it started.
    #[cfg(target_os = "windows")]
    async fn open_capture(
        &self,
        method: CaptureMethod,
        tx: &mpsc::Sender<CaptureMsg>,
        generation: u64,
    ) -> bool {
        let mut last_problem: Option<CaptureProblem> = None;
        loop {
            if !self.is_current(generation) {
                return false;
            }
            match self.start_backend(method, tx, generation) {
                Ok(()) => {
                    let mut stats = self.stats.lock().await;
                    if self.is_current(generation) {
                        stats.error = None;
                        stats.capture_issue = None;
                    }
                    drop(stats);
                    self.debug_log_file("info", &format!("Capture active: {method:?}"))
                        .await;
                    return true;
                }
                Err(problem) => {
                    // Logged only when it changes: this repeats until the user acts.
                    if last_problem.as_ref() != Some(&problem) {
                        self.debug_log_file("err", &problem.logged).await;
                        let mut stats = self.stats.lock().await;
                        if self.is_current(generation) {
                            stats.error = problem.shown.clone();
                            stats.capture_issue = problem.issue;
                        }
                        drop(stats);
                        last_problem = Some(problem);
                    }
                    tokio::time::sleep(std::time::Duration::from_secs(5)).await;
                }
            }
        }
    }

    /// Main loop: starts the chosen capture method and processes packets.
    #[cfg(target_os = "windows")]
    pub async fn run_generation(&self, generation: u64, method: CaptureMethod) {
        {
            let mut s = self.stats.lock().await;
            s.running = true;
            s.online = false;
            s.error = None;
            s.capture_issue = None;
        }
        // Warnings from the previous method don't apply to this one.
        self.debug.lock().await.clear();
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
        self.debug_log("info", &format!("Sniffer starting: {method:?}"))
            .await;

        // The channel lives for the entire session.
        let (tx, rx) = mpsc::channel::<CaptureMsg>();

        if !self.open_capture(method, &tx, generation).await {
            return;
        }

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
                Ok(CaptureMsg::Dead(reason)) => {
                    self.debug_log_file("err", &format!("Capture stopped: {reason}"))
                        .await;
                    was_online = false;
                    self.stats.lock().await.online = false;
                    if !self.open_capture(method, &tx, generation).await {
                        break;
                    }
                }
                Ok(CaptureMsg::Log(line)) => {
                    self.debug_log_file("info", &line).await;
                }
                Ok(CaptureMsg::Packet(l2_hint, data)) => {
                    raw_pkts += 1;
                    // Photon payload offset: L2 (ethernet/raw) + IP (real IHL) + UDP.
                    // WinDivert delivers raw IP (L2=0), Npcap depends on the adapter.
                    let off = match photon_offset(&data, l2_hint) {
                        Some(o) => o,
                        None => continue,
                    };
                    last_packet_time = Instant::now();

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
                        s.capture_issue = None;
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

                        let party_changed = {
                            let mut party = self.party.lock().await;
                            if party.apply(op) {
                                let mut s = self.stats.lock().await;
                                // A party without the local player means they are the one who left.
                                if !s.player_name.is_empty()
                                    && !party.names().contains(&s.player_name)
                                {
                                    party.clear();
                                }
                                s.party_members = party.names();
                                Some(s.party_members.join(", "))
                            } else {
                                None
                            }
                        };
                        if let Some(names) = party_changed {
                            self.debug_log_file(
                                "info",
                                &format!("Party (event {}): {names}", op.albion_code),
                            )
                            .await;
                        }

                        if let Some((name, metadata)) = extract_character_stats(op) {
                            self.record_character_metadata(name, metadata).await;
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
                        if let Some((id, name, metadata)) = extract_new_character(op) {
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
                            self.entities.lock().await.insert(id, name.clone());
                            // madvac lê guild/alliance do próprio NewCharacter (29):
                            // em ZvZ todo player visível gera um, então é a fonte
                            // que mais preenche looted_by/from_guild.
                            if !metadata.guild_name.is_empty() || !metadata.alliance_name.is_empty()
                            {
                                self.record_character_metadata(name, metadata).await;
                            }
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
                let mut s = self.stats.lock().await;
                if s.error.is_none() {
                    s.capture_issue = no_packets_issue(method, last_packet_time.elapsed(), || {
                        crate::albion_detect::find_albion_pid().is_some()
                    });
                }
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
        }
    }

    #[cfg(not(target_os = "windows"))]
    pub async fn run_generation(&self, _generation: u64, _method: CaptureMethod) {
        let mut stats = self.stats.lock().await;
        stats.running = false;
        stats.error = Some("Captura de pacotes indisponível nesta plataforma.".into());
    }

    async fn record_character_metadata(
        &self,
        name: String,
        metadata: crate::photon_parser::CharacterMetadata,
    ) {
        let metadata = {
            let mut players = self.character_metadata.lock().await;
            if players.len() >= 5000 && !players.contains_key(&name) {
                if let Some(evicted) = players.keys().next().cloned() {
                    players.remove(&evicted);
                }
            }
            let cached = players.entry(name.clone()).or_default();
            merge_character_metadata(cached, &metadata);
            cached.clone()
        };

        let result = {
            let mut loot = self.loot.lock().await;
            let result = backfill_loot_and_persist(
                &mut loot,
                &name,
                &metadata,
                crate::lootlog::save_session,
            );
            if matches!(result, Ok(true)) {
                self.stats.lock().await.loot_count = loot.len() as u64;
            }
            result
        };
        if let Err(error) = result {
            self.debug_log(
                "err",
                &format!("não foi possível persistir os metadados do lootlog: {error}"),
            )
            .await;
        }
    }

    pub async fn clear_captured_loot(&self) -> Result<(), String> {
        let result = {
            let mut loot = self.loot.lock().await;
            let result = clear_loot_and_persist(&mut loot, crate::lootlog::save_session);
            if result.is_ok() {
                self.stats.lock().await.loot_count = loot.len() as u64;
            }
            result
        };
        result.map_err(|error| format!("não foi possível limpar o lootlog salvo: {error}"))
    }

    async fn push_loot(&self, mut loot: LootEvent) {
        let looted_by = loot.looted_by.clone();
        let looted_from = loot.looted_from.clone();
        {
            let metadata = self.character_metadata.lock().await;
            if let Some(player) = metadata.get(&looted_by) {
                apply_character_metadata(&mut loot, &looted_by, player);
            }
            if let Some(player) = metadata.get(&looted_from) {
                apply_character_metadata(&mut loot, &looted_from, player);
            }
        }

        {
            let stats = self.stats.lock().await;
            if loot.looted_by == stats.player_name {
                fill_missing(&mut loot.looted_by_guild, &stats.guild_name);
                fill_missing(&mut loot.looted_by_alliance, &stats.alliance_name);
            }
            if loot.looted_from == stats.player_name {
                fill_missing(&mut loot.looted_from_guild, &stats.guild_name);
                fill_missing(&mut loot.looted_from_alliance, &stats.alliance_name);
            }
        }

        let metadata_by_name = self.character_metadata.lock().await.clone();
        let result = {
            let mut loot_buffer = self.loot.lock().await;
            let result = (|| {
                backfill_pending_metadata_and_persist(
                    &mut loot_buffer,
                    &metadata_by_name,
                    crate::lootlog::save_session,
                )?;
                add_loot_and_persist(&mut loot_buffer, loot, crate::lootlog::save_session)
            })();
            if matches!(result, Ok(true)) {
                self.stats.lock().await.loot_count = loot_buffer.len() as u64;
            }
            result
        };
        if let Err(error) = result {
            self.debug_log(
                "err",
                &format!("não foi possível persistir a sessão de loot: {error}"),
            )
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
            let ellipsis = if a.len() > 16 { ", ..." } else { "" };
            format!("[{}{}]", head.join(", "), ellipsis)
        }
        other => brief(other),
    }
}

fn debug_log_path() -> Option<std::path::PathBuf> {
    dirs::document_dir().map(|d| d.join("ziggs-companion").join("companion-debug.log"))
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

/// Photon payload offset within the captured frame.
/// Tries the L2 suggested by the datalink, then falls back to 0/14/4 and
/// validates the IPv4+UDP structure — works on ethernet, raw IP (VPN) and
/// loopback without hardcoding.
pub(crate) fn photon_offset(data: &[u8], l2_hint: usize) -> Option<usize> {
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

#[cfg(test)]
mod tests {
    use super::photon_offset;
    use super::{
        add_loot_and_persist, backfill_loot_and_persist, backfill_pending_metadata_and_persist,
        clear_loot_and_persist, is_duplicate_loot, merge_character_metadata, no_packets_issue,
        CaptureIssue, LootEvent, LOOT_DEDUP_LOOKBACK, NO_PACKETS_HINT_AFTER,
    };
    use crate::config::CaptureMethod;
    use crate::photon_parser::CharacterMetadata;
    use std::time::Duration;

    #[test]
    fn hints_that_npcap_sees_nothing_only_with_albion_open_and_silent() {
        let long = NO_PACKETS_HINT_AFTER;
        assert_eq!(
            no_packets_issue(CaptureMethod::Npcap, long, || true),
            Some(CaptureIssue::NpcapNoPackets)
        );
        assert_eq!(no_packets_issue(CaptureMethod::Npcap, long, || false), None);
        assert_eq!(
            no_packets_issue(CaptureMethod::Npcap, long - Duration::from_secs(1), || true),
            None
        );
    }

    #[test]
    fn never_suggests_changing_method_while_on_windivert() {
        assert_eq!(
            no_packets_issue(CaptureMethod::Windivert, NO_PACKETS_HINT_AFTER, || true),
            None
        );
    }

    #[test]
    fn does_not_list_processes_until_the_silence_is_long_enough() {
        no_packets_issue(CaptureMethod::Npcap, Duration::ZERO, || {
            panic!("process list should not be read")
        });
    }

    #[test]
    fn the_ui_receives_the_issue_codes_it_expects() {
        for (issue, code) in [
            (CaptureIssue::NpcapMissing, "npcap_missing"),
            (CaptureIssue::NpcapUnavailable, "npcap_unavailable"),
            (CaptureIssue::NpcapNoPackets, "npcap_no_packets"),
            (CaptureIssue::WindivertNeedsAdmin, "windivert_needs_admin"),
        ] {
            assert_eq!(serde_json::to_value(issue).unwrap(), code);
        }
    }

    /// Real switch between methods without a game. WinDivert can't start here (no rights
    /// without elevation, no DLL next to the test binary with it), Npcap can. Switching
    /// must clear the failure and packets must be counted. Run with
    /// `cargo test --lib live_sniffer -- --ignored`.
    #[cfg(target_os = "windows")]
    #[tokio::test(flavor = "multi_thread")]
    #[ignore = "needs Npcap installed and internet access"]
    async fn live_sniffer_switches_method_and_counts_packets() {
        let sniffer = super::Sniffer::new();

        let generation = sniffer.prepare_start();
        let running = sniffer.clone_shared();
        tokio::spawn(async move {
            running
                .run_generation(generation, CaptureMethod::Windivert)
                .await
        });
        tokio::time::sleep(Duration::from_millis(500)).await;
        let failed = sniffer.stats.lock().await.clone();
        assert!(
            failed.capture_issue == Some(CaptureIssue::WindivertNeedsAdmin)
                || failed.error.is_some_and(|e| e.starts_with("WinDivert:"))
        );

        sniffer.stop().await;
        let stopped = sniffer.stats.lock().await.clone();
        assert_eq!(stopped.error, None);
        assert_eq!(stopped.capture_issue, None);

        let generation = sniffer.prepare_start();
        let running = sniffer.clone_shared();
        tokio::spawn(async move {
            running
                .run_generation(generation, CaptureMethod::Npcap)
                .await
        });
        tokio::time::sleep(Duration::from_millis(800)).await;
        assert_eq!(sniffer.stats.lock().await.error, None);

        let socket = std::net::UdpSocket::bind("0.0.0.0:5055").expect("port 5055 free");
        socket
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let query = [
            0x43, 0x21, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 7, b'e', b'x', b'a', b'm', b'p', b'l', b'e',
            3, b'c', b'o', b'm', 0, 0, 1, 0, 1,
        ];
        socket.send_to(&query, "1.1.1.1:53").expect("query sent");
        let mut reply = [0u8; 512];
        socket.recv_from(&mut reply).expect("DNS reply");
        tokio::time::sleep(Duration::from_millis(800)).await;

        let stats = sniffer.stats.lock().await.clone();
        assert!(stats.packets_captured >= 2, "{}", stats.packets_captured);
        assert!(stats.online);
        assert_eq!(stats.capture_issue, None);
        sniffer.stop().await;
    }

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
            ..Default::default()
        }
    }

    #[test]
    fn metadata_backfill_preserves_known_values_and_persists_the_new_ones() {
        let mut loot = vec![loot_ev("Alice", "Bob", 1, 1)];
        loot[0].looted_by_guild = "Guild already known".into();
        let mut saved = Vec::new();

        let changed = backfill_loot_and_persist(
            &mut loot,
            "Alice",
            &CharacterMetadata {
                guild_name: "Ziggs".into(),
                alliance_name: "Alliance".into(),
            },
            |events| {
                saved = events.to_vec();
                Ok(())
            },
        )
        .expect("o backfill deve persistir");

        assert!(changed);
        assert_eq!(loot[0].looted_by_guild, "Guild already known");
        assert_eq!(loot[0].looted_by_alliance, "Alliance");
        assert_eq!(saved[0].looted_by_guild, "Guild already known");
        assert_eq!(saved[0].looted_by_alliance, "Alliance");
    }

    #[test]
    fn failed_metadata_backfill_restores_existing_metadata() {
        let mut loot = vec![loot_ev("Alice", "Bob", 1, 1)];
        loot[0].looted_by_guild = "Guild already known".into();

        let error = backfill_loot_and_persist(
            &mut loot,
            "Alice",
            &CharacterMetadata {
                guild_name: "Ziggs".into(),
                alliance_name: "Alliance".into(),
            },
            |_| Err(anyhow::anyhow!("disco indisponível")),
        )
        .expect_err("a falha de persistência precisa ser devolvida");

        assert!(error.to_string().contains("disco indisponível"));
        assert_eq!(loot[0].looted_by_guild, "Guild already known");
        assert!(loot[0].looted_by_alliance.is_empty());
    }

    #[test]
    fn pending_metadata_is_persisted_on_a_later_retry() {
        let mut loot = vec![loot_ev("Alice", "Bob", 1, 1)];
        let pending = std::collections::HashMap::from([(
            "Alice".to_string(),
            CharacterMetadata {
                guild_name: "Ziggs".into(),
                alliance_name: "Alliance".into(),
            },
        )]);
        let mut saved = Vec::new();

        let changed = backfill_pending_metadata_and_persist(&mut loot, &pending, |events| {
            saved = events.to_vec();
            Ok(())
        })
        .expect("o retry deve persistir os metadados pendentes");

        assert!(changed);
        assert_eq!(loot[0].looted_by_guild, "Ziggs");
        assert_eq!(loot[0].looted_by_alliance, "Alliance");
        assert_eq!(saved[0].looted_by_guild, "Ziggs");
        assert_eq!(saved[0].looted_by_alliance, "Alliance");
    }

    #[test]
    fn partial_metadata_does_not_erase_cached_fields() {
        let mut cached = CharacterMetadata {
            guild_name: "Ziggs".into(),
            alliance_name: "Alliance".into(),
        };

        merge_character_metadata(
            &mut cached,
            &CharacterMetadata {
                guild_name: String::new(),
                alliance_name: String::new(),
            },
        );

        assert_eq!(cached.guild_name, "Ziggs");
        assert_eq!(cached.alliance_name, "Alliance");
    }

    #[test]
    fn newer_nonempty_metadata_replaces_cached_fields() {
        let mut cached = CharacterMetadata {
            guild_name: "Old guild".into(),
            alliance_name: "Old alliance".into(),
        };

        merge_character_metadata(
            &mut cached,
            &CharacterMetadata {
                guild_name: "New guild".into(),
                alliance_name: "New alliance".into(),
            },
        );

        assert_eq!(cached.guild_name, "New guild");
        assert_eq!(cached.alliance_name, "New alliance");
    }

    #[test]
    fn cached_metadata_updates_only_missing_loot_fields() {
        let mut loot = vec![loot_ev("Alice", "Bob", 1, 1)];
        loot[0].looted_by_guild = "Original guild".into();
        let pending = std::collections::HashMap::from([(
            "Alice".to_string(),
            CharacterMetadata {
                guild_name: "Current guild".into(),
                alliance_name: "Current alliance".into(),
            },
        )]);

        backfill_pending_metadata_and_persist(&mut loot, &pending, |_| Ok(()))
            .expect("o backfill deve persistir");

        assert_eq!(loot[0].looted_by_guild, "Original guild");
        assert_eq!(loot[0].looted_by_alliance, "Current alliance");
    }

    #[test]
    fn failed_pending_backfill_restores_the_buffer() {
        let mut loot = vec![loot_ev("Alice", "Bob", 1, 1)];
        let pending = std::collections::HashMap::from([(
            "Alice".to_string(),
            CharacterMetadata {
                guild_name: "Ziggs".into(),
                alliance_name: "Alliance".into(),
            },
        )]);

        let error = backfill_pending_metadata_and_persist(&mut loot, &pending, |_| {
            Err(anyhow::anyhow!("disco indisponível"))
        })
        .expect_err("a falha de persistência precisa ser devolvida");

        assert!(error.to_string().contains("disco indisponível"));
        assert!(loot[0].looted_by_guild.is_empty());
        assert!(loot[0].looted_by_alliance.is_empty());
    }

    #[test]
    fn failed_clear_persistence_restores_the_buffer() {
        let mut loot = vec![loot_ev("Alice", "Bob", 1, 1)];

        let error =
            clear_loot_and_persist(&mut loot, |_| Err(anyhow::anyhow!("disco indisponível")))
                .expect_err("a falha de persistência precisa ser devolvida");

        assert!(error.to_string().contains("disco indisponível"));
        assert_eq!(loot.len(), 1, "limpar não pode apagar evento não salvo");
        assert_eq!(loot[0].looted_by, "Alice");
    }

    #[test]
    fn failed_loot_persistence_discards_the_new_event() {
        let mut loot = vec![loot_ev("Alice", "Bob", 1, 1)];

        let error = add_loot_and_persist(&mut loot, loot_ev("Carol", "Dave", 2, 1), |_| {
            Err(anyhow::anyhow!("disco indisponível"))
        })
        .expect_err("a falha de persistência precisa ser devolvida");

        assert!(error.to_string().contains("disco indisponível"));
        assert_eq!(
            loot.len(),
            1,
            "evento não persistido não pode ficar na memória"
        );
        assert_eq!(loot[0].looted_by, "Alice");
    }

    #[test]
    fn loot_dedup_catches_identical_event_from_two_interfaces() {
        let mut buf = vec![loot_ev("Alice", "Bob", 2958, 3)];
        // Same identity (same copy arriving from the other interface) → dup.
        assert!(is_duplicate_loot(&buf, &loot_ev("Alice", "Bob", 2958, 3)));
        buf.push(loot_ev("Alice", "Bob", 2958, 3));

        // Different item from same body, same second → not dup.
        assert!(!is_duplicate_loot(&buf, &loot_ev("Alice", "Bob", 1001, 3)));
        // Different quantity → not dup.
        assert!(!is_duplicate_loot(&buf, &loot_ev("Alice", "Bob", 2958, 5)));
        // Different looter → not dup.
        assert!(!is_duplicate_loot(&buf, &loot_ev("Carol", "Bob", 2958, 3)));
    }

    #[test]
    fn loot_dedup_does_not_see_beyond_lookback() {
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
