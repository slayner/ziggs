// build.rs — compiles the Tauri manifest and Common Controls v6 manifest.
//
// Npcap SDK linking was removed: packet capture now uses WinDivert (bundled
// as DLL+sys in resources/), no external SDK or driver installation needed.

fn main() {
    // Generates the ACL permissions (capabilities/*.json → gen/schemas/) and the rest
    // of the codegen that `tauri::generate_context!()` expects to find. WITHOUT this
    // the ACL stays FROZEN at the last build that ran: any new permission
    // in capabilities/default.json (e.g. allow-start-dragging,
    // allow-set-webview-zoom) never reaches the binary — the command is denied at runtime,
    // silently, even with decorations:false removing the native title bar
    // (no native chrome AND no JS drag = window impossible to move).
    // Real incident on 20-21/07/2026: build.rs was rewritten for auto-detect
    // of the Npcap SDK and this call disappeared along with it. (Npcap SDK
    // linking has since been removed entirely — capture uses WinDivert.)
    //
    // Tauri builds the primary Windows resource itself. Give it our manifest
    // directly so the executable gets exactly one RT_MANIFEST containing both
    // Common Controls v6 and the required elevation declaration.
    let attrs = tauri_build::Attributes::new().windows_attributes(
        tauri_build::WindowsAttributes::new().app_manifest(include_str!("Companion.exe.manifest")),
    );
    if let Err(e) = tauri_build::try_build(attrs) {
        panic!("tauri_build::try_build failed: {e:#}");
    }

    if std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default() == "windows" {
        // Common Controls v6 — TaskDialogIndirect precisa de comctl32.lib
        println!("cargo:rustc-link-lib=dylib=comctl32");
    }
}
