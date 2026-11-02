// Npcap: the default packet capture method.
//
// Npcap is not bundled. Its free license does not allow redistribution, and the OEM
// license is not an option for this project, so each user installs it from npcap.com
// and the app has to open and explain itself without it. That is why wpcap.dll is
// loaded at runtime instead of linked.
//
// The DLL is only ever loaded by absolute path from System32\Npcap. This process runs
// elevated, so a wpcap.dll planted next to the executable or on PATH must not be picked
// up. LOAD_WITH_ALTERED_SEARCH_PATH makes its Packet.dll resolve from that same folder
// without changing the DLL search order of the whole process (SetDllDirectory would).
//
// Npcap captures per adapter, at the NDIS layer. So every adapter with an IPv4 address
// is opened, and a rescan picks up the ones that appear later (VPN turned on, DHCP
// finishing after boot). Copies of a packet seen on two bridged adapters are dropped by
// the sniffer (PKT_DEDUP_WINDOW). Route optimizers can hide Albion's traffic from this
// layer, which is why WinDivert stays available as the other method.
//
// Capture is not promiscuous and the filter only lets Albion's ports through.

use std::collections::HashSet;
use std::ffi::{c_char, c_int, c_uint, c_void, CStr, CString, OsString};
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use crate::sniffer::CaptureMsg;

const ERRBUF_SIZE: usize = 256;
const SNAPLEN: c_int = 65535;
const NOT_PROMISCUOUS: c_int = 0;
/// How long a read waits for a packet, and so how long an adapter thread takes to
/// notice that the capture was stopped.
const READ_TIMEOUT_MS: c_int = 250;
const NETMASK_UNKNOWN: u32 = 0xFFFF_FFFF;
const IF_LOOPBACK: u32 = 0x1;
const AF_INET: u16 = 2;
/// Same ports as the WinDivert filter, both directions.
const FILTER: &str = "udp and (port 5056 or port 5055 or port 4535)";

/// While packets arrive, new adapters are rare. Going silent is usually a route change
/// (VPN turned on, traffic moved to a new adapter), so look again sooner.
const RESCAN_WHILE_ONLINE: Duration = Duration::from_secs(30);
const RESCAN_WHILE_SILENT: Duration = Duration::from_secs(5);
const ONLINE_WINDOW: Duration = Duration::from_secs(5);

#[repr(C)]
struct PcapIf {
    next: *mut PcapIf,
    name: *mut c_char,
    description: *mut c_char,
    addresses: *mut PcapAddr,
    flags: u32,
}

#[repr(C)]
struct PcapAddr {
    next: *mut PcapAddr,
    addr: *mut SockAddrFamily,
    netmask: *mut SockAddrFamily,
    broadaddr: *mut SockAddrFamily,
    dstaddr: *mut SockAddrFamily,
}

/// Only the first field of `sockaddr` is read.
#[repr(C)]
struct SockAddrFamily {
    family: u16,
}

/// `struct timeval` uses `long` on Windows, which is 32 bits even on x64.
#[repr(C)]
struct PacketHeader {
    ts_sec: i32,
    ts_usec: i32,
    caplen: u32,
    len: u32,
}

#[repr(C)]
struct BpfProgram {
    len: c_uint,
    instructions: *mut c_void,
}

#[derive(Clone, Copy)]
struct Funcs {
    findalldevs: unsafe extern "C" fn(*mut *mut PcapIf, *mut c_char) -> c_int,
    freealldevs: unsafe extern "C" fn(*mut PcapIf),
    open_live: unsafe extern "C" fn(*const c_char, c_int, c_int, c_int, *mut c_char) -> *mut c_void,
    datalink: unsafe extern "C" fn(*mut c_void) -> c_int,
    compile: unsafe extern "C" fn(*mut c_void, *mut BpfProgram, *const c_char, c_int, u32) -> c_int,
    setfilter: unsafe extern "C" fn(*mut c_void, *mut BpfProgram) -> c_int,
    freecode: unsafe extern "C" fn(*mut BpfProgram),
    next_ex: unsafe extern "C" fn(*mut c_void, *mut *mut PacketHeader, *mut *const u8) -> c_int,
    geterr: unsafe extern "C" fn(*mut c_void) -> *const c_char,
    close: unsafe extern "C" fn(*mut c_void),
    lib_version: unsafe extern "C" fn() -> *const c_char,
}

/// Why a capture could not start. `Missing` is what the install guide is for.
#[derive(Debug)]
pub enum StartError {
    Missing(String),
    Unavailable(String),
}

fn wpcap_path() -> Result<PathBuf, String> {
    use windows_sys::Win32::System::SystemInformation::GetSystemDirectoryW;

    let mut buffer = [0u16; 260];
    let len = unsafe { GetSystemDirectoryW(buffer.as_mut_ptr(), buffer.len() as u32) } as usize;
    if len == 0 || len >= buffer.len() {
        return Err("could not locate the system directory".to_string());
    }
    Ok(PathBuf::from(OsString::from_wide(&buffer[..len]))
        .join("Npcap")
        .join("wpcap.dll"))
}

unsafe fn load_dll() -> Result<Funcs, String> {
    use windows_sys::Win32::Foundation::GetLastError;
    use windows_sys::Win32::System::LibraryLoader::{
        GetProcAddress, LoadLibraryExW, LOAD_WITH_ALTERED_SEARCH_PATH,
    };

    let path = wpcap_path()?;
    if !path.exists() {
        return Err(format!("{} does not exist", path.display()));
    }
    let wide: Vec<u16> = path
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    let module = LoadLibraryExW(wide.as_ptr(), 0, LOAD_WITH_ALTERED_SEARCH_PATH);
    if module == 0 {
        return Err(format!(
            "could not load {}: GetLastError={}",
            path.display(),
            GetLastError()
        ));
    }

    unsafe fn export<T: Copy>(module: isize, name: &[u8]) -> Result<T, String> {
        match GetProcAddress(module, name.as_ptr()) {
            Some(function) => Ok(std::mem::transmute_copy(&function)),
            None => Err(format!(
                "wpcap.dll does not export {}",
                String::from_utf8_lossy(&name[..name.len() - 1])
            )),
        }
    }

    Ok(Funcs {
        findalldevs: export(module, b"pcap_findalldevs\0")?,
        freealldevs: export(module, b"pcap_freealldevs\0")?,
        open_live: export(module, b"pcap_open_live\0")?,
        datalink: export(module, b"pcap_datalink\0")?,
        compile: export(module, b"pcap_compile\0")?,
        setfilter: export(module, b"pcap_setfilter\0")?,
        freecode: export(module, b"pcap_freecode\0")?,
        next_ex: export(module, b"pcap_next_ex\0")?,
        geterr: export(module, b"pcap_geterr\0")?,
        close: export(module, b"pcap_close\0")?,
        lib_version: export(module, b"pcap_lib_version\0")?,
    })
}

/// A failed load is not remembered: the user can install Npcap while the app is open.
fn load() -> Result<Funcs, String> {
    static LOADED: Mutex<Option<Funcs>> = Mutex::new(None);
    let mut loaded = LOADED
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if let Some(funcs) = *loaded {
        return Ok(funcs);
    }
    let funcs = unsafe { load_dll() }?;
    *loaded = Some(funcs);
    Ok(funcs)
}

fn text_from(pointer: *const c_char) -> String {
    if pointer.is_null() {
        return String::new();
    }
    unsafe { CStr::from_ptr(pointer) }
        .to_string_lossy()
        .into_owned()
}

/// "Npcap version 1.79, based on libpcap version 1.10.4" → "1.79".
fn version_from(text: &str) -> Option<String> {
    let rest = text.strip_prefix("Npcap version ")?;
    let version = rest.split(',').next()?.trim();
    (!version.is_empty()).then(|| version.to_string())
}

/// The installed version, or why Npcap can't be used. `Ok(None)` means it loaded but
/// reported a version string in an unknown format.
pub fn version() -> Result<Option<String>, String> {
    if !wpcap_path()?.exists() {
        return Err("Npcap is not installed".to_string());
    }
    let funcs = load()?;
    Ok(version_from(&text_from(unsafe { (funcs.lib_version)() })))
}

/// Bytes before the IP header on each link type. Ethernet=14, NULL/LOOP=4, and the rest
/// (DLT_RAW and similar, on VPN and TUN adapters) is raw IP. `photon_offset` validates
/// the result and tries the others, so a wrong hint only costs a retry.
fn l2_len_for(datalink: c_int) -> usize {
    match datalink {
        1 => 14,
        0 | 108 => 4,
        _ => 0,
    }
}

/// An adapter without IPv4 can't carry Albion traffic yet. It is looked at again on the
/// next rescan, so it gets opened once DHCP or the VPN gives it an address.
fn adapter_is_usable(flags: u32, has_ipv4: bool) -> bool {
    flags & IF_LOOPBACK == 0 && has_ipv4
}

struct Adapter {
    name: CString,
    description: String,
}

fn usable_adapters(funcs: &Funcs) -> Result<Vec<Adapter>, String> {
    let mut first: *mut PcapIf = std::ptr::null_mut();
    let mut errbuf = [0 as c_char; ERRBUF_SIZE];
    if unsafe { (funcs.findalldevs)(&mut first, errbuf.as_mut_ptr()) } != 0 {
        return Err(text_from(errbuf.as_ptr()));
    }

    let mut adapters = Vec::new();
    let mut current = first;
    while !current.is_null() {
        let device = unsafe { &*current };
        let mut has_ipv4 = false;
        let mut address = device.addresses;
        while !address.is_null() {
            let entry = unsafe { &*address };
            if !entry.addr.is_null() && unsafe { (*entry.addr).family } == AF_INET {
                has_ipv4 = true;
            }
            address = entry.next;
        }
        if adapter_is_usable(device.flags, has_ipv4) && !device.name.is_null() {
            let name = unsafe { CStr::from_ptr(device.name) }.to_owned();
            let description = text_from(device.description);
            adapters.push(Adapter {
                description: if description.is_empty() {
                    name.to_string_lossy().into_owned()
                } else {
                    description
                },
                name,
            });
        }
        current = device.next;
    }
    if !first.is_null() {
        unsafe { (funcs.freealldevs)(first) };
    }
    Ok(adapters)
}

/// Opens one adapter with the Albion filter applied. The caller owns the handle.
fn open_adapter(funcs: &Funcs, name: &CStr) -> Result<*mut c_void, String> {
    let filter = CString::new(FILTER).map_err(|error| error.to_string())?;
    let mut errbuf = [0 as c_char; ERRBUF_SIZE];
    unsafe {
        let handle = (funcs.open_live)(
            name.as_ptr(),
            SNAPLEN,
            NOT_PROMISCUOUS,
            READ_TIMEOUT_MS,
            errbuf.as_mut_ptr(),
        );
        if handle.is_null() {
            return Err(text_from(errbuf.as_ptr()));
        }

        let mut program = BpfProgram {
            len: 0,
            instructions: std::ptr::null_mut(),
        };
        if (funcs.compile)(handle, &mut program, filter.as_ptr(), 1, NETMASK_UNKNOWN) != 0 {
            let error = text_from((funcs.geterr)(handle));
            (funcs.close)(handle);
            return Err(error);
        }
        let applied = (funcs.setfilter)(handle, &mut program);
        (funcs.freecode)(&mut program);
        if applied != 0 {
            let error = text_from((funcs.geterr)(handle));
            (funcs.close)(handle);
            return Err(error);
        }
        Ok(handle)
    }
}

/// Reads one adapter on its own thread and owns its handle, so stopping needs no shared
/// state: the thread sees the generation change on its next read timeout and closes.
struct AdapterReader {
    funcs: Funcs,
    handle: *mut c_void,
    name: CString,
    description: String,
    l2: usize,
    tx: mpsc::Sender<CaptureMsg>,
    generation: Arc<AtomicU64>,
    gen_value: u64,
    open: Arc<Mutex<HashSet<CString>>>,
    saw_packet: Arc<AtomicBool>,
}

// The handle is only used by the thread this struct moves into.
unsafe impl Send for AdapterReader {}

impl AdapterReader {
    fn run(self) {
        let mut header: *mut PacketHeader = std::ptr::null_mut();
        let mut data: *const u8 = std::ptr::null();

        while self.generation.load(Ordering::Acquire) == self.gen_value {
            match unsafe { (self.funcs.next_ex)(self.handle, &mut header, &mut data) } {
                1 => {
                    let packet =
                        unsafe { std::slice::from_raw_parts(data, (*header).caplen as usize) }
                            .to_vec();
                    self.saw_packet.store(true, Ordering::Relaxed);
                    if self.tx.send(CaptureMsg::Packet(self.l2, packet)).is_err() {
                        break;
                    }
                }
                0 => {}
                _ => {
                    let error = text_from(unsafe { (self.funcs.geterr)(self.handle) });
                    let _ = self.tx.send(CaptureMsg::Log(format!(
                        "Npcap stopped reading {}: {}",
                        self.description, error
                    )));
                    break;
                }
            }
        }

        unsafe { (self.funcs.close)(self.handle) };
        self.open
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .remove(&self.name);
    }
}

struct Capture {
    funcs: Funcs,
    tx: mpsc::Sender<CaptureMsg>,
    generation: Arc<AtomicU64>,
    gen_value: u64,
    open: Arc<Mutex<HashSet<CString>>>,
    saw_packet: Arc<AtomicBool>,
    /// Adapters whose failure to open was already logged, since this retries forever.
    failures_logged: HashSet<CString>,
}

impl Capture {
    fn open_count(&self) -> usize {
        self.open
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .len()
    }

    fn is_current(&self) -> bool {
        self.generation.load(Ordering::Acquire) == self.gen_value
    }

    /// Opens every usable adapter that isn't open yet. A failed adapter is tried again
    /// on the next rescan: it may just not be ready. Returns the first failure.
    fn open_new_adapters(&mut self) -> Result<Option<String>, String> {
        let mut first_failure = None;
        for adapter in usable_adapters(&self.funcs)? {
            let already_open = self
                .open
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .contains(&adapter.name);
            if already_open {
                continue;
            }
            match open_adapter(&self.funcs, &adapter.name) {
                Ok(handle) => {
                    self.open
                        .lock()
                        .unwrap_or_else(|poisoned| poisoned.into_inner())
                        .insert(adapter.name.clone());
                    self.failures_logged.remove(&adapter.name);
                    let l2 = l2_len_for(unsafe { (self.funcs.datalink)(handle) });
                    let _ = self.tx.send(CaptureMsg::Log(format!(
                        "Npcap listening on {} (L2={l2})",
                        adapter.description
                    )));
                    let reader = AdapterReader {
                        funcs: self.funcs,
                        handle,
                        name: adapter.name,
                        description: adapter.description,
                        l2,
                        tx: self.tx.clone(),
                        generation: Arc::clone(&self.generation),
                        gen_value: self.gen_value,
                        open: Arc::clone(&self.open),
                        saw_packet: Arc::clone(&self.saw_packet),
                    };
                    std::thread::spawn(move || reader.run());
                }
                Err(error) => {
                    if self.failures_logged.insert(adapter.name) {
                        let _ = self.tx.send(CaptureMsg::Log(format!(
                            "Npcap could not open {}: {}",
                            adapter.description, error
                        )));
                    }
                    first_failure.get_or_insert(error);
                }
            }
        }
        Ok(first_failure)
    }

    /// Keeps the adapter list current until the capture stops. Losing every adapter is
    /// reported, so the sniffer starts over and tells the user what is wrong.
    fn watch(mut self) {
        let mut last_scan = Instant::now();
        let mut last_packet: Option<Instant> = None;

        while self.is_current() {
            std::thread::sleep(Duration::from_secs(1));
            if self.saw_packet.swap(false, Ordering::Relaxed) {
                last_packet = Some(Instant::now());
            }
            let online = last_packet.is_some_and(|at| at.elapsed() < ONLINE_WINDOW);
            let interval = if online {
                RESCAN_WHILE_ONLINE
            } else {
                RESCAN_WHILE_SILENT
            };
            if last_scan.elapsed() < interval {
                continue;
            }
            last_scan = Instant::now();

            let scan = self.open_new_adapters();
            if self.open_count() == 0 && self.is_current() {
                let reason = match scan {
                    Ok(Some(failure)) | Err(failure) => failure,
                    Ok(None) => "no adapter left to capture from".to_string(),
                };
                let _ = self.tx.send(CaptureMsg::Dead(reason));
                return;
            }
        }
    }
}

/// Starts capturing on every usable adapter. Packets go to the sniffer's channel as raw
/// frames with their L2 size. Stops by itself when `generation` changes.
pub fn start_capture(
    tx: mpsc::Sender<CaptureMsg>,
    generation: Arc<AtomicU64>,
    gen_value: u64,
) -> Result<(), StartError> {
    let funcs = load().map_err(StartError::Missing)?;
    let mut capture = Capture {
        funcs,
        tx,
        generation,
        gen_value,
        open: Arc::new(Mutex::new(HashSet::new())),
        saw_packet: Arc::new(AtomicBool::new(false)),
        failures_logged: HashSet::new(),
    };

    let failure = capture
        .open_new_adapters()
        .map_err(StartError::Unavailable)?;
    if capture.open_count() == 0 {
        return Err(StartError::Unavailable(failure.unwrap_or_else(|| {
            "no network adapter with an IPv4 address".to_string()
        })));
    }

    std::thread::spawn(move || capture.watch());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_dll_is_only_loaded_from_the_npcap_system_folder() {
        let path = wpcap_path().expect("system directory");
        assert!(path.is_absolute(), "search order must not decide this");
        let lower = path.to_string_lossy().to_lowercase();
        assert!(lower.ends_with("\\system32\\npcap\\wpcap.dll"), "{path:?}");
    }

    #[test]
    fn struct_layouts_match_libpcap_on_windows_x64() {
        assert_eq!(std::mem::size_of::<PacketHeader>(), 16);
        assert_eq!(std::mem::offset_of!(PacketHeader, caplen), 8);
        assert_eq!(std::mem::size_of::<BpfProgram>(), 16);
        assert_eq!(std::mem::offset_of!(PcapIf, flags), 32);
        assert_eq!(std::mem::offset_of!(PcapAddr, addr), 8);
    }

    #[test]
    fn link_types_give_the_header_size() {
        assert_eq!(l2_len_for(1), 14, "ethernet");
        assert_eq!(l2_len_for(0), 4, "null");
        assert_eq!(l2_len_for(108), 4, "loop");
        assert_eq!(l2_len_for(12), 0, "raw ip");
        assert_eq!(l2_len_for(101), 0, "raw ip");
    }

    #[test]
    fn only_non_loopback_adapters_with_ipv4_are_opened() {
        assert!(adapter_is_usable(0x6, true));
        assert!(!adapter_is_usable(0x6, false), "no address yet");
        assert!(!adapter_is_usable(IF_LOOPBACK | 0x6, true), "loopback");
    }

    #[test]
    fn reads_the_version_out_of_the_library_string() {
        assert_eq!(
            version_from("Npcap version 1.79, based on libpcap version 1.10.4").as_deref(),
            Some("1.79")
        );
        assert_eq!(version_from("WinPcap version 4.1.3"), None);
    }

    /// Real capture on this PC, no game needed: a DNS query sent from Albion's port goes
    /// out and comes back through the filter, proving both directions reach the channel
    /// without promiscuous mode. Run with `cargo test --lib live_npcap -- --ignored`.
    #[test]
    #[ignore = "needs Npcap installed and internet access"]
    fn live_npcap_sees_both_directions_on_an_albion_port() {
        let generation = Arc::new(AtomicU64::new(1));
        let (tx, rx) = mpsc::channel();
        start_capture(tx, Arc::clone(&generation), 1).expect("Npcap should start");
        std::thread::sleep(Duration::from_millis(500));

        let socket = std::net::UdpSocket::bind("0.0.0.0:5056").expect("port 5056 free");
        socket
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let query = [
            0x12, 0x34, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 7, b'e', b'x', b'a', b'm', b'p', b'l', b'e',
            3, b'c', b'o', b'm', 0, 0, 1, 0, 1,
        ];
        socket.send_to(&query, "1.1.1.1:53").expect("query sent");
        let mut reply = [0u8; 512];
        let (reply_len, _) = socket.recv_from(&mut reply).expect("DNS reply");

        let mut saw_query = false;
        let mut saw_reply = false;
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline && !(saw_query && saw_reply) {
            if let Ok(CaptureMsg::Packet(l2, frame)) = rx.recv_timeout(Duration::from_millis(200)) {
                let offset = crate::sniffer::photon_offset(&frame, l2).expect("IPv4 + UDP");
                saw_query |= frame[offset..] == query;
                saw_reply |= frame[offset..] == reply[..reply_len];
            }
        }
        generation.store(2, Ordering::SeqCst);

        assert!(saw_query, "outgoing packet not captured");
        assert!(saw_reply, "incoming packet not captured");
    }

    #[test]
    #[ignore = "needs Npcap installed"]
    fn live_npcap_reports_its_version() {
        let version = version().expect("Npcap should load").expect("known format");
        assert!(version.chars().next().is_some_and(|c| c.is_ascii_digit()));
    }
}
