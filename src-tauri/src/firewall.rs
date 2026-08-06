// Windows Firewall manipulation via netsh — zero PowerShell, zero terminals.
// netsh is a single native process; CREATE_NO_WINDOW hides it reliably.
// (PowerShell spawns child processes that flash even with CREATE_NO_WINDOW.)

use std::process::Command;

/// Add a firewall rule that blocks all outbound traffic to the given IP ranges.
/// `name` is the rule display name. `ranges` is a comma-separated list of
/// "start-end" or "single" IP addresses.
pub fn add_block_rule(name: &str, ranges: &str) -> Result<(), String> {
    let output = crate::winutil::no_window(Command::new("netsh"))
        .args([
            "advfirewall", "firewall", "add", "rule",
            &format!("name={name}"),
            "dir=out", "action=block",
            &format!("remoteip={ranges}"),
        ])
        .output()
        .map_err(|e| format!("netsh: {e}"))?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).into_owned());
    }
    Ok(())
}

/// Remove a firewall rule by name.
pub fn remove_rule(name: &str) -> Result<(), String> {
    let _ = crate::winutil::no_window(Command::new("netsh"))
        .args(["advfirewall", "firewall", "delete", "rule", &format!("name={name}")])
        .output();
    Ok(())
}