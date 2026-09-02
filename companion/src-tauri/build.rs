// build.rs — compiles the Tauri manifest and Common Controls v6 manifest.
//
// Npcap SDK linking was removed: packet capture now uses WinDivert (bundled
// as DLL+sys in resources/), no external SDK or driver installation needed.

fn which_rc() -> Option<String> {
    // Look for rc.exe in installed Windows Kits (10.0.22621, etc.)
    let kits = [
        "C:\\Program Files (x86)\\Windows Kits\\10\\bin",
        "C:\\Program Files\\Windows Kits\\10\\bin",
    ];
    for kit in &kits {
        if let Ok(entries) = std::fs::read_dir(kit) {
            for entry in entries.flatten() {
                let rc = entry.path().join("x64").join("rc.exe");
                if rc.exists() {
                    return Some(rc.to_string_lossy().into_owned());
                }
            }
        }
    }
    None
}

fn main() {
    // Generates the ACL permissions (capabilities/*.json → gen/schemas/) and the rest
    // of the codegen that `tauri::generate_context!()` expects to find. WITHOUT this
    // the ACL stays FROZEN at the last build that ran: any new permission
    // in capabilities/default.json (e.g. allow-start-dragging,
    // allow-maximize) never reaches the binary — the command is denied at runtime,
    // silently, even with decorations:false removing the native title bar
    // (no native chrome AND no JS drag = window impossible to move).
    // Real incident on 20-21/07/2026: build.rs was rewritten for auto-detect
    // of the Npcap SDK and this call disappeared along with it. (Npcap SDK
    // linking has since been removed entirely — capture uses WinDivert.)
    //
    // new_without_app_manifest(): the default tauri_build embeds ONE Windows
    // manifest of its own (windows-app-manifest.xml). This file ALREADY embeds OUR
    // manifest via resource.res (compiled with rc.exe, see below —
    // needs Common Controls v6 for TaskDialogIndirect). Both manifests together
    // = "CVT1100: duplicate resource type:MANIFEST" on link — the
    // linker refuses two RT_MANIFEST in the same binary.
    let attrs = tauri_build::Attributes::new()
        .windows_attributes(tauri_build::WindowsAttributes::new_without_app_manifest());
    if let Err(e) = tauri_build::try_build(attrs) {
        panic!("tauri_build::try_build failed: {e:#}");
    }

    if std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default() == "windows" {
        // Common Controls v6 — TaskDialogIndirect precisa de comctl32.lib
        println!("cargo:rustc-link-lib=dylib=comctl32");

        // Tauri already compiles resource.rc into resource.lib and links it in the binary
        // (VERSIONINFO + icon). But the Common Controls v6 manifest is
        // not there — new_without_app_manifest() turned off Tauri's automatic manifest
        // to avoid duplication with our Companion.exe.manifest.
        // Without manifest, comctl32 v5 is loaded and TaskDialogIndirect is missing.
        // Solution: compile ONLY the manifest as RT_MANIFEST in a separate .res
        // and link it, without touching Tauri's resource.lib.
        let out_dir = std::env::var("OUT_DIR").unwrap();
        let our_manifest = std::path::Path::new("Companion.exe.manifest");
        if our_manifest.exists() {
            let rc_candidates = [
                std::env::var("RC").ok(),
                std::env::var("WindowsSdkVerBinPath")
                    .ok()
                    .map(|p| format!("{}\\x64\\rc.exe", p)),
                which_rc(),
            ];
            if let Some(rc) = rc_candidates.into_iter().flatten().next() {
                let manifest_copy = std::path::Path::new(&out_dir).join("app.manifest");
                std::fs::copy(our_manifest, &manifest_copy).ok();
                let manifest_rc = std::path::Path::new(&out_dir).join("manifest.rc");
                std::fs::write(
                    &manifest_rc,
                    "#pragma code_page(65001)\n1 24 \"app.manifest\"\n",
                )
                .ok();

                let res = std::path::Path::new(&out_dir).join("manifest.res");
                let status = std::process::Command::new(&rc)
                    .args(["/nologo", "/r", "/fo"])
                    .arg(&res)
                    .arg(&manifest_rc)
                    .current_dir(&out_dir)
                    .status();
                if let Ok(s) = status {
                    if s.success() && res.exists() {
                        println!("cargo:rustc-link-arg=/MANIFEST:NO");
                        println!("cargo:rustc-link-arg-bins={}", res.display());
                    }
                }
            }
        }

    }
}
