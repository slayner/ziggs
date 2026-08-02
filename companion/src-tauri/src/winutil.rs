/// Helper pra não abrir console ao spawnar subprocessos de um app GUI.
/// CREATE_NO_WINDOW = 0x08000000 — sem isso o Windows abre uma janela de
/// terminal preta pra cada `schtasks`/`powershell`/`netsh`/`route`.
#[cfg(target_os = "windows")]
pub fn no_window(mut cmd: std::process::Command) -> std::process::Command {
    use std::os::windows::process::CommandExt;
    cmd.creation_flags(0x08000000);
    cmd
}

#[cfg(not(target_os = "windows"))]
pub fn no_window(cmd: std::process::Command) -> std::process::Command { cmd }