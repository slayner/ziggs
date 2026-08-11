/// Suppress console windows when spawning subprocesses from a GUI app.
/// CREATE_NO_WINDOW = 0x08000000 prevents a black terminal window per subprocess.
#[cfg(target_os = "windows")]
pub fn no_window(mut cmd: std::process::Command) -> std::process::Command {
    use std::os::windows::process::CommandExt;
    cmd.creation_flags(0x08000000);
    cmd
}

#[cfg(not(target_os = "windows"))]
pub fn no_window(cmd: std::process::Command) -> std::process::Command {
    cmd
}
