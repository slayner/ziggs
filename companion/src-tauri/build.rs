// build.rs — encontra o Npcap SDK (wpcap.lib) automaticamente.
//
// Procura em:
//   1. NPCAP_SDK_DIR env var
//   2. C:\npcap-sdk\Lib\x64
//   3. C:\Program Files\Npcap SDK\Lib\x64
//
// Se encontrar, adiciona ao LIB path pra o linker achar wpcap.lib.

fn which_rc() -> Option<String> {
    // Procura rc.exe nos Windows Kits instalados (10.0.22621, etc.)
    let kits = ["C:\\Program Files (x86)\\Windows Kits\\10\\bin",
                "C:\\Program Files\\Windows Kits\\10\\bin"];
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
    // Gera o ACL de permissões (capabilities/*.json → gen/schemas/) e o resto
    // do codegen que `tauri::generate_context!()` espera encontrar. SEM isso
    // o ACL fica CONGELADO no último build em que rodou: qualquer permissão
    // nova em capabilities/default.json (ex: allow-start-dragging,
    // allow-maximize) nunca chega ao binário — o comando é negado em runtime,
    // calado, mesmo com decorations:false tirando a barra de título nativa
    // (sem chrome nativo E sem drag por JS = janela impossível de mover).
    // Mordida real em 20-21/07/2026: build.rs foi reescrito pro auto-detect
    // do Npcap SDK e essa chamada sumiu junto.
    //
    // new_without_app_manifest(): o tauri_build padrão embute UM manifest
    // Windows próprio (windows-app-manifest.xml). Este arquivo JÁ embute o
    // NOSSO manifest via resource.res (compilado com rc.exe, ver abaixo —
    // precisa do Common Controls v6 pro TaskDialogIndirect). Os dois manifests
    // juntos = "CVT1100: duplicate resource type:MANIFEST" no link — o
    // linker recusa dois RT_MANIFEST no mesmo binário.
    let attrs = tauri_build::Attributes::new()
        .windows_attributes(tauri_build::WindowsAttributes::new_without_app_manifest());
    if let Err(e) = tauri_build::try_build(attrs) {
        panic!("tauri_build::try_build falhou: {e:#}");
    }

    if std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default() == "windows" {
        // Common Controls v6 — TaskDialogIndirect precisa de comctl32.lib
        println!("cargo:rustc-link-lib=dylib=comctl32");

        // O Tauri já compila resource.rc em resource.lib e linka no binário
        // (VERSIONINFO + ícone). Mas o manifest do Common Controls v6 não
        // está lá — new_without_app_manifest() desligou o manifest automático
        // do Tauri pra evitar duplicação com o nosso Companion.exe.manifest.
        // Sem manifest, comctl32 v5 é carregada e TaskDialogIndirect falta.
        // Solução: compilar SÓ o manifest como RT_MANIFEST num .res separado
        // e linkar, sem tocar no resource.lib do Tauri.
        let out_dir = std::env::var("OUT_DIR").unwrap();
        let our_manifest = std::path::Path::new("Companion.exe.manifest");
        if our_manifest.exists() {
            let rc_candidates = [
                std::env::var("RC").ok(),
                std::env::var("WindowsSdkVerBinPath").ok().map(|p| format!("{}\\x64\\rc.exe", p)),
                which_rc(),
            ];
            if let Some(rc) = rc_candidates.into_iter().flatten().next() {
                let manifest_copy = std::path::Path::new(&out_dir).join("app.manifest");
                std::fs::copy(our_manifest, &manifest_copy).ok();
                let manifest_rc = std::path::Path::new(&out_dir).join("manifest.rc");
                std::fs::write(&manifest_rc, "#pragma code_page(65001)\n1 24 \"app.manifest\"\n").ok();

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

        let candidates = [
            std::env::var("NPCAP_SDK_DIR").ok().map(|d| std::path::Path::new(&d).join("Lib").join("x64").to_string_lossy().into_owned()),
            Some("C:\\npcap-sdk\\Lib\\x64".into()),
            Some("C:\\Program Files\\Npcap SDK\\Lib\\x64".into()),
        ];

        for candidate in candidates.iter().flatten() {
            let lib_path = std::path::Path::new(candidate);
            if lib_path.join("wpcap.lib").exists() {
                println!("cargo:rustc-link-search=native={}", candidate);
                println!("cargo:rerun-if-env-changed=NPCAP_SDK_DIR");
                return;
            }
        }
        println!("cargo:warning=Npcap SDK nao encontrado. Baixe de https://npcap.com/dist/ e extraia em C:\\npcap-sdk");
        println!("cargo:warning=Ou set NPCAP_SDK_DIR=<caminho> apontando pra a pasta do SDK");
    }
}