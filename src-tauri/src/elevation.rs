// Administrator rights follow the capture method. Npcap doesn't need them, so the app
// opens without asking. WinDivert loads a driver, so the app reopens itself elevated
// when that is the method in use, and that is when Windows asks for permission.
//
// The process that reopens passes its pid and the new one waits for it to finish, for
// two reasons: the single-instance plugin would otherwise see the old process and close
// the new one, and WebView2 refuses to share its data folder between an elevated and a
// non-elevated browser process, so the old WebView2 has to be gone as well.
//
// The plugin also can't see across that line. From a non-elevated process, its mutex
// held by an elevated one fails with "access denied" instead of "already exists", and
// a second instance would start. `elevated_instance_is_running` covers that case.

use std::os::windows::ffi::OsStrExt;
use std::time::{Duration, Instant};

use windows_sys::Win32::Foundation::{
    CloseHandle, GetLastError, ERROR_ACCESS_DENIED, INVALID_HANDLE_VALUE,
};
use windows_sys::Win32::Security::{
    GetTokenInformation, TokenElevation, TOKEN_ELEVATION, TOKEN_QUERY,
};
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS,
};
use windows_sys::Win32::System::Threading::{
    CreateMutexW, GetCurrentProcess, OpenMutexW, OpenProcessToken,
};
use windows_sys::Win32::UI::Shell::ShellExecuteW;
use windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;

const REPLACES_ARG: &str = "--replaces-pid";
const MINIMIZED_ARG: &str = "--minimized";
const ELEVATED_MARKER: &str = "xyz.ziggs.companion-elevated";
const SYNCHRONIZE: u32 = 0x0010_0000;
/// Long enough for WebView2 to close, short enough that a stuck process can't keep the
/// app from opening. If this runs out, opening is still better than not opening.
const WAIT_FOR_REPLACED: Duration = Duration::from_secs(15);

fn wide(text: &str) -> Vec<u16> {
    std::ffi::OsStr::new(text)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect()
}

pub fn is_elevated() -> bool {
    unsafe {
        let mut token = 0;
        if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) == 0 {
            return false;
        }
        let mut elevation = TOKEN_ELEVATION { TokenIsElevated: 0 };
        let mut size = 0u32;
        let ok = GetTokenInformation(
            token,
            TokenElevation,
            &mut elevation as *mut TOKEN_ELEVATION as *mut _,
            std::mem::size_of::<TOKEN_ELEVATION>() as u32,
            &mut size,
        );
        CloseHandle(token);
        ok != 0 && elevation.TokenIsElevated != 0
    }
}

/// Held until the process ends, when Windows releases it.
pub fn mark_elevated_instance() {
    unsafe {
        CreateMutexW(std::ptr::null(), 0, wide(ELEVATED_MARKER).as_ptr());
    }
}

pub fn elevated_instance_is_running() -> bool {
    unsafe {
        let handle = OpenMutexW(SYNCHRONIZE, 0, wide(ELEVATED_MARKER).as_ptr());
        if handle != 0 {
            CloseHandle(handle);
            return true;
        }
        GetLastError() == ERROR_ACCESS_DENIED
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum StartupStep {
    /// This process opens the app.
    Continue,
    /// Reopen elevated, which is when Windows asks for permission.
    Reopen,
    /// Nothing to do here. Ends without asking anything.
    Quit,
}

/// What a process does as it starts. With an elevated instance running, a click still has
/// to reopen elevated, since only an elevated process can reach that instance to bring it
/// to the front. An entry that starts with Windows wants no window, so it just ends: a
/// permission prompt at logon for that would be pointless.
pub fn startup_step(
    method_needs_admin: bool,
    elevated: bool,
    other_is_elevated: bool,
    minimized: bool,
) -> StartupStep {
    match (elevated, other_is_elevated, method_needs_admin, minimized) {
        (true, _, _, _) => StartupStep::Continue,
        (false, true, _, true) => StartupStep::Quit,
        (false, true, _, false) => StartupStep::Reopen,
        (false, false, true, _) => StartupStep::Reopen,
        (false, false, false, _) => StartupStep::Continue,
    }
}

fn relaunch_parameters(keep_minimized: bool, pid: u32) -> String {
    if keep_minimized {
        format!("{MINIMIZED_ARG} {REPLACES_ARG} {pid}")
    } else {
        format!("{REPLACES_ARG} {pid}")
    }
}

/// Opens this executable elevated, which is when Windows asks for permission. Blocks
/// until the user answers. An error is usually the user saying no. The new process
/// only proceeds after this one ends, so the caller should end right after an `Ok`.
pub fn relaunch_elevated(keep_minimized: bool) -> Result<(), String> {
    let executable = std::env::current_exe().map_err(|error| format!("current_exe: {error}"))?;
    let directory = executable
        .parent()
        .ok_or_else(|| "executable has no folder".to_string())?;
    let parameters = relaunch_parameters(
        keep_minimized && std::env::args().any(|arg| arg == MINIMIZED_ARG),
        std::process::id(),
    );

    let result = unsafe {
        ShellExecuteW(
            0,
            wide("runas").as_ptr(),
            wide(&executable.to_string_lossy()).as_ptr(),
            wide(&parameters).as_ptr(),
            wide(&directory.to_string_lossy()).as_ptr(),
            SW_SHOWNORMAL,
        )
    };
    // Per the documentation, anything above 32 is success.
    if result > 32 {
        Ok(())
    } else {
        Err(format!("ShellExecuteW runas returned {result}"))
    }
}

fn replaced_pid(args: impl Iterator<Item = String>) -> Option<u32> {
    let mut args = args;
    args.find(|arg| arg == REPLACES_ARG)?;
    args.next()?.parse().ok()
}

/// The process itself, or the WebView2 it started, is still alive.
fn instance_is_alive(pid: u32) -> bool {
    unsafe {
        let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snapshot == INVALID_HANDLE_VALUE {
            return false;
        }
        let mut alive = false;
        let mut entry: PROCESSENTRY32W = std::mem::zeroed();
        entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
        if Process32FirstW(snapshot, &mut entry) != 0 {
            loop {
                let name_len = entry.szExeFile.iter().position(|&c| c == 0).unwrap_or(0);
                let name = String::from_utf16_lossy(&entry.szExeFile[..name_len]);
                if entry.th32ProcessID == pid
                    || (entry.th32ParentProcessID == pid
                        && name.eq_ignore_ascii_case("msedgewebview2.exe"))
                {
                    alive = true;
                    break;
                }
                if Process32NextW(snapshot, &mut entry) == 0 {
                    break;
                }
            }
        }
        CloseHandle(snapshot);
        alive
    }
}

/// Call before anything else at startup. Does nothing unless this process was opened by
/// `relaunch_elevated`.
pub fn wait_for_replaced_instance() {
    let Some(pid) = replaced_pid(std::env::args()) else {
        return;
    };
    let deadline = Instant::now() + WAIT_FOR_REPLACED;
    while Instant::now() < deadline && instance_is_alive(pid) {
        std::thread::sleep(Duration::from_millis(100));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(list: &[&str]) -> impl Iterator<Item = String> {
        list.iter()
            .map(|arg| arg.to_string())
            .collect::<Vec<_>>()
            .into_iter()
    }

    #[test]
    fn windivert_always_ends_up_elevated_and_npcap_never_asks() {
        // WinDivert selected, nothing running: both a click and the start with Windows.
        for minimized in [false, true] {
            assert_eq!(
                startup_step(true, false, false, minimized),
                StartupStep::Reopen
            );
            assert_eq!(
                startup_step(true, true, false, minimized),
                StartupStep::Continue,
                "already elevated, as the scheduled task opens it"
            );
            assert_eq!(
                startup_step(false, false, false, minimized),
                StartupStep::Continue
            );
        }
    }

    #[test]
    fn an_elevated_instance_running_is_never_duplicated() {
        for method_needs_admin in [false, true] {
            assert_eq!(
                startup_step(method_needs_admin, false, true, false),
                StartupStep::Reopen,
                "a click reopens to reach it and bring it to the front"
            );
            assert_eq!(
                startup_step(method_needs_admin, false, true, true),
                StartupStep::Quit,
                "the start with Windows just ends, with no prompt at logon"
            );
        }
    }

    #[test]
    fn reads_the_pid_of_the_process_being_replaced() {
        assert_eq!(
            replaced_pid(args(&["app.exe", "--replaces-pid", "4321"])),
            Some(4321)
        );
        assert_eq!(
            replaced_pid(args(&["app.exe", "--minimized", "--replaces-pid", "7"])),
            Some(7)
        );
    }

    #[test]
    fn a_normal_launch_replaces_nothing() {
        assert_eq!(replaced_pid(args(&["app.exe"])), None);
        assert_eq!(replaced_pid(args(&["app.exe", "--minimized"])), None);
        assert_eq!(replaced_pid(args(&["app.exe", "--replaces-pid"])), None);
        assert_eq!(
            replaced_pid(args(&["app.exe", "--replaces-pid", "x"])),
            None
        );
    }

    #[test]
    fn the_parameters_it_builds_are_the_ones_it_reads() {
        for keep_minimized in [true, false] {
            let parameters = relaunch_parameters(keep_minimized, 99);
            let parts = parameters.split(' ').map(str::to_string);
            assert_eq!(replaced_pid(parts), Some(99));
            assert_eq!(parameters.contains("--minimized"), keep_minimized);
        }
    }

    #[test]
    fn sees_a_running_process_and_a_finished_one() {
        assert!(instance_is_alive(std::process::id()));

        let mut child = std::process::Command::new("cmd")
            .args(["/C", "exit"])
            .spawn()
            .expect("cmd");
        let pid = child.id();
        child.wait().expect("cmd ended");
        assert!(!instance_is_alive(pid));
    }

    #[test]
    fn replacing_nobody_does_not_wait() {
        let started = Instant::now();
        wait_for_replaced_instance();
        assert!(started.elapsed() < Duration::from_secs(1));
    }

    #[test]
    fn the_manifest_does_not_ask_for_administrator_on_open() {
        let manifest = include_str!("../Companion.exe.manifest");
        assert!(manifest.contains(r#"level="asInvoker""#));
        assert!(!manifest.contains("requireAdministrator"));
    }
}
