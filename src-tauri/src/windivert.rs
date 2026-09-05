// WinDivert: sole packet capture mechanism for the companion.
//
// Captures Albion UDP packets at the WFP (Windows Filtering Platform) network
// layer, which sits ABOVE routing and VPN encapsulation. This means:
//
//   - Works with ExitLag, Cloudflare WARP, and any VPN/route optimizer
//   - No external driver installation (DLL + .sys bundled as Tauri resources)
//   - SNIFF mode: copies packets without intercepting, game is unaffected
//
// WinDivert.dll and WinDivert64.sys are bundled in resources/ (LGPLv3).
// Requires admin (already required by the companion for wintun).

use std::sync::atomic::{AtomicI64, AtomicU64, Ordering};
use std::sync::mpsc;
use std::sync::Arc;

use crate::sniffer::CaptureMsg;

const WINDIVERT_LAYER_NETWORK: i32 = 0;
const WINDIVERT_FLAG_SNIFF: u64 = 0x0001;
const WINDIVERT_PRIORITY_HIGHEST: i16 = 30000;
const WINDIVERT_SHUTDOWN_RECV: u32 = 1;

/// Albion uses UDP ports 5056, 5055, 4535. Filter captures both directions.
const FILTER: &[u8] = b"udp and (udp.DstPort == 5056 or udp.DstPort == 5055 \
or udp.DstPort == 4535 or udp.SrcPort == 5056 or udp.SrcPort == 5055 or \
udp.SrcPort == 4535)\0";

/// WINDIVERT_ADDRESS struct size: i64(8) + u32(4) + u32(4) + [u8;64] = 80 bytes.
const ADDR_SIZE: usize = 80;

type FnOpen = unsafe extern "C" fn(*const u8, i32, i16, u64) -> isize;
type FnRecv = unsafe extern "C" fn(isize, *mut u8, u32, *mut u32, *mut u8) -> i32;
type FnShutdown = unsafe extern "C" fn(isize, u32) -> i32;
type FnClose = unsafe extern "C" fn(isize) -> i32;

struct Funcs {
    open: FnOpen,
    recv: FnRecv,
    shutdown: FnShutdown,
    close: FnClose,
}

/// Loads WinDivert.dll from the exe directory or resources subdirectory.
/// Returns `Err(msg)` with the GetLastError code if the DLL exists but
/// LoadLibrary fails, or `Ok(funcs)` on success.
unsafe fn load_dll() -> Result<Funcs, String> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::System::LibraryLoader::LoadLibraryW;

    let exe_dir = std::env::current_exe()
        .map_err(|e| format!("current_exe: {}", e))?
        .parent()
        .ok_or_else(|| "cannot get exe dir".to_string())?
        .to_path_buf();

    let candidates = [
        exe_dir.join("WinDivert.dll"),
        exe_dir.join("resources").join("WinDivert.dll"),
    ];

    let mut module = 0isize;
    let mut last_err = String::new();
    for path in &candidates {
        if path.exists() {
            let wide: Vec<u16> = path
                .as_os_str()
                .encode_wide()
                .chain(std::iter::once(0))
                .collect();
            module = LoadLibraryW(wide.as_ptr());
            if module != 0 {
                break;
            }
            let err = unsafe { windows_sys::Win32::Foundation::GetLastError() };
            last_err = format!("LoadLibraryW({:?}) failed: GetLastError={}", path, err);
        }
    }

    if module == 0 {
        return Err(if last_err.is_empty() {
            format!("WinDivert.dll not found. Tried: {:?}", candidates)
        } else {
            last_err
        });
    }

    unsafe fn get_func<T: Copy>(module: isize, name: &[u8]) -> Option<T> {
        use windows_sys::Win32::System::LibraryLoader::GetProcAddress;
        match GetProcAddress(module, name.as_ptr()) {
            Some(f) => Some(std::mem::transmute_copy(&f)),
            None => None,
        }
    }

    Ok(Funcs {
        open: get_func(module, b"WinDivertOpen\0").ok_or("WinDivertOpen export missing")?,
        recv: get_func(module, b"WinDivertRecv\0").ok_or("WinDivertRecv export missing")?,
        shutdown: get_func(module, b"WinDivertShutdown\0").ok_or("WinDivertShutdown export missing")?,
        close: get_func(module, b"WinDivertClose\0").ok_or("WinDivertClose export missing")?,
    })
}

/// Starts a WinDivert SNIFF capture thread that sends raw IP packets (L2=0,
/// no ethernet header — WinDivert delivers raw IP at the NETWORK layer) to
/// the sniffer's channel. Returns a shared handle store — call `shutdown`
/// on it to unblock the thread and close the handle.
///
/// Returns `Ok(handle_store)` if WinDivert was started, `Err(msg)` with a
/// human-readable error string on failure.
pub fn start_capture(
    tx: mpsc::Sender<CaptureMsg>,
    generation: Arc<AtomicU64>,
    gen_value: u64,
) -> Result<Arc<AtomicI64>, String> {
    let funcs = unsafe { load_dll() }?;

    let handle = unsafe {
        (funcs.open)(
            FILTER.as_ptr(),
            WINDIVERT_LAYER_NETWORK,
            WINDIVERT_PRIORITY_HIGHEST,
            WINDIVERT_FLAG_SNIFF,
        )
    };

    if handle == 0 || handle == -1 {
        #[cfg(target_os = "windows")]
        {
            let err = unsafe { windows_sys::Win32::Foundation::GetLastError() };
            let hint = match err {
                5 => "ACCESS_DENIED — not running as admin",
                31 => "DEVICE_NOT_WORKING — driver not loaded",
                127 => "PROC_NOT_FOUND — DLL exports missing (wrong WinDivert version?)",
                577 => "INVALID_PARAMETER — Windows blocking driver signature (Secure Boot?)",
                1058 => "SERVICE_DISABLED — WinDivert service disabled",
                1060 => "SERVICE_DOES_NOT_EXIST — driver not installed",
                1275 => "DLL_NOT_FOUND — WinDivert.dll missing dependencies (VC++ runtime?)",
                _ => "see WinDivert docs",
            };
            return Err(format!("WinDivertOpen failed: GetLastError={} ({})", err, hint));
        }
        #[cfg(not(target_os = "windows"))]
        {
            return Err("WinDivertOpen failed (non-Windows)".to_string());
        }
    }

    let handle_store = Arc::new(AtomicI64::new(handle as i64));
    let handle_store_clone = Arc::clone(&handle_store);

    std::thread::spawn(move || {
        let mut buf = vec![0u8; 65535];
        let mut addr = [0u8; ADDR_SIZE];

        loop {
            if generation.load(Ordering::Acquire) != gen_value {
                break;
            }

            let mut recv_len: u32 = 0;
            let ok = unsafe {
                (funcs.recv)(
                    handle,
                    buf.as_mut_ptr(),
                    buf.len() as u32,
                    &mut recv_len,
                    addr.as_mut_ptr(),
                )
            };

            if ok == 0 {
                break;
            }

            if recv_len > 0 {
                let packet = buf[..recv_len as usize].to_vec();
                let _ = tx.send(CaptureMsg::Packet(0, packet));
            }
        }

        unsafe {
            (funcs.close)(handle);
        }
        handle_store_clone.store(0, Ordering::SeqCst);
    });

    Ok(handle_store)
}

/// Shuts down a WinDivert capture handle (unblocks the recv call) and closes
/// it. No-op if the handle is 0 (not started or already closed).
pub fn shutdown(handle_store: &Arc<AtomicI64>) {
    let handle = handle_store.swap(0, Ordering::SeqCst);
    if handle == 0 {
        return;
    }
    if let Ok(funcs) = unsafe { load_dll() } {
        unsafe {
            (funcs.shutdown)(handle as isize, WINDIVERT_SHUTDOWN_RECV);
            (funcs.close)(handle as isize);
        }
    }
}