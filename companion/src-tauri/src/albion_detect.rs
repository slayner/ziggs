// Detect the Albion Online process. Windows: single Toolhelp32 snapshot.
// Linux/macOS: /proc scan as a compile-compatible stub.

use serde::Serialize;

#[derive(Clone, Debug, Serialize)]
pub struct AlbionProcess {
    pub pid: u32,
    pub name: String,
    pub detected: bool,
}

pub fn find_albion_pid() -> Option<u32> {
    #[cfg(target_os = "windows")]
    {
        find_albion_pid_windows()
    }
    #[cfg(not(target_os = "windows"))]
    {
        find_albion_pid_unix()
    }
}

#[cfg(target_os = "windows")]
fn find_albion_pid_windows() -> Option<u32> {
    use windows_sys::Win32::Foundation::{CloseHandle, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW,
        PROCESSENTRY32W, TH32CS_SNAPPROCESS,
    };
    unsafe {
        let snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snap == INVALID_HANDLE_VALUE { return None; }
        let mut entry: PROCESSENTRY32W = std::mem::zeroed();
        entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
        if Process32FirstW(snap, &mut entry) != 0 {
            loop {
                let name_len = entry.szExeFile.iter().position(|&c| c == 0).unwrap_or(0);
                let name = String::from_utf16_lossy(&entry.szExeFile[..name_len]);
                let n = name.to_ascii_lowercase();
                if n.contains("albiononline") || n == "albion-online.exe" || n == "albiononline.exe" {
                    CloseHandle(snap);
                    return Some(entry.th32ProcessID);
                }
                if Process32NextW(snap, &mut entry) == 0 { break; }
            }
        }
        CloseHandle(snap);
    }
    None
}

#[cfg(not(target_os = "windows"))]
fn find_albion_pid_unix() -> Option<u32> {
    use std::fs;
    for entry in fs::read_dir("/proc").ok()?.flatten() {
        let name = entry.file_name();
        let pid: u32 = name.to_string_lossy().parse().ok()?;
        let cmd = fs::read_to_string(format!("/proc/{}/cmdline", pid)).ok()?;
        if cmd.to_ascii_lowercase().contains("albiononline") {
            return Some(pid);
        }
    }
    None
}